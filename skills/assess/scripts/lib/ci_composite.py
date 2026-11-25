"""Read the steps of a local composite action a workflow step uses (issues #923, #1194).

``lib.ci.ci_checks`` reads a workflow line by line, so a step ``uses: ./.github/actions/check``
whose ``action.yml`` runs ``ruff check .`` and ``pytest`` used to read as no command.
``inline_composites(text, read_local)`` returns the workflow text with each such action's
``run:`` steps and known lint action steps copied in after the calling step, as sibling
steps of the same job:

- **Repo-local, never executed.** Only a ``uses:`` value starting ``./`` that
  names a directory (``lib.ci_action_inputs``'s ``action.yml`` / ``action.yaml`` lookup),
  read through ``read_local`` (tracked files inside the checkout, never a symlink out of it).
  The action must declare ``runs.using: composite``; a node or docker action is not read.
  A step with a ``run:`` key is copied. A copied ``run: ./ci.sh`` is read by ``lib.ci_scripts``
  as any CI line is, so a committed script's body can still upgrade it.
- **Known lint actions (#1194).** A step that ``uses:`` an action the line tests read as a lint
  step (``lib.ci.ci_lines.lint_step_action``: ``LINT_ACTIONS``, the reviewdog actions,
  ``MAY_LINT_ACTIONS``) is copied whole, ``with:`` block included, its ``${{ inputs.<name> }}``
  values filled as below, so ``lib.ci.ci_lint_inputs`` reads it as the same step written in the
  workflow: ``args: --version`` is install only, an unfilled input is unsure (null). Any other
  remote action is dropped: its code is not read, so it is never credited, as at the top level.
- **Nested local actions (#1194).** A step that ``uses:`` another local action is followed the
  same way, its copies placed at the calling step's indent, up to ``_MAX_DEPTH`` (8) levels
  below the action the workflow calls and ``_MAX_READS`` (64) nested reads per calling step,
  so a diamond of actions each calling the next many times cannot blow up the text. Its
  ``with:`` values (filled from the outer action's inputs first) are the inner action's
  inputs, and its own ``if:`` / ``continue-on-error`` join the caller's on every inner copy.
  One that cannot be read (missing, not a composite, an action already on the path, or past
  the depth or read limit) is copied with its ``uses:`` line in ``unknown``.
- **Masking and triggers.** Each copy is the whole step item, re-indented, so its own
  ``shell:`` (pipefail, errexit) and ``continue-on-error`` apply. The calling step's
  ``continue-on-error`` and ``if:`` lines are added to every copy at the copy's own key
  column (whatever each file's dash-to-key gap), so ``continue-on-error:
  true`` or ``if: false`` on the caller masks them all. The copies sit in the calling job and
  workflow, so the caller's triggers and job keys apply.
- **Inputs.** ``${{ inputs.<name> }}`` in a copied ``run:`` value is replaced by the
  caller's literal ``with:`` scalar for that name, else, only when the caller's ``with:``
  omits the name, the action's literal ``inputs.<name>.default``. One left in command position (no value, a block or
  ``${{ }}`` value) makes that line unknown. A whole ``run: ${{ inputs.run }}`` (or
  ``script`` / ``command``) the workflow's calling step passes is emptied (``run: ''``):
  ``lib.ci_action_inputs`` already reads the caller's ``with:`` value for it (#799), so it is
  neither counted twice nor read as an unknown command; one the caller omits takes the
  default like any other input. Below the top level the nested step is not kept, so its
  value is filled in like any other input (#1194). The step's other lines (an ``env:`` value such as ``CMD:
  ${{ inputs.cmd }}``) take the literal values too, never marked unknown, so
  ``lib.ci.ci_expressions`` can read ``run: $CMD`` (#1164).
- **Unknown, never 0.** ``inline_composites`` returns ``(text, unknown)``
  (``lib.ci.ci_matrix.Expanded``); ``lib.ci.ci_checks`` reads the unknown lines as ``check_target``
  (null) unless masked, as for a matrix command.

The structure comes from ``lib.ci_masking``'s indentation model; there is no YAML parser.
Only GitHub workflow text is read. Inward-only imports: ``lib.ci_action_inputs``,
``lib.ci.ci_continuations``, ``lib.ci.ci_lines``, ``lib.ci_masking``, ``lib.ci.ci_matrix``. Tests:
``tests/test_ci_composite_matrix.py``, ``tests/test_ci_composite_lint.py``.
"""
from __future__ import annotations

import re
from typing import Literal, NamedTuple

from lib.ci_action_inputs import INPUT_KEYS, ReadLocal, local_action_paths
from lib.ci.ci_continuations import join_continued
from lib.ci.ci_lines import lint_step_action
from lib.ci_masking import IndentTree, parse_lines
from lib.ci.ci_matrix import CMD_EXPR, Expanded, scalar

_CALLER_KEYS = frozenset({"continue-on-error", "if"})
_MAX_DEPTH = 8   # nested levels followed below the action a workflow calls
_MAX_READS = 64  # nested actions read for one calling step (bounds a diamond's fan-out)


class _Budget:
    """The nested reads one calling step has left."""

    def __init__(self) -> None:
        self.reads = _MAX_READS
_INPUT_REF = re.compile(r"\$\{\{\s*inputs\.([\w-]+)\s*\}\}")
_INPUT_ONLY = re.compile(r"^\s*inputs\.([\w-]+)\s*$")
_FORWARD = re.compile(r"""^["']?\$\{\{\s*inputs\.([\w-]+)\s*\}\}["']?$""")


_Kind = Literal["run", "lint", "local"]   # a run: step, a known lint action, a nested local action


class _Step(NamedTuple):
    dash: int                  # the step's dash indent in the action
    gap: int                   # its key column less its dash indent (``-   run:`` is 4)
    lines: list[str]
    body: frozenset[int]       # run-value lines, relative to the step
    kind: _Kind


def _shift(line: str, delta: int) -> str:
    if delta >= 0 or not line.strip():
        return " " * delta + line if line.strip() else line
    lead = len(line) - len(line.lstrip(" "))
    return line[min(-delta, lead):]


def _child(tree: IndentTree, line: int, name: str) -> int | None:
    return next((k for k in tree.children(line) if tree.lines[k].key == name), None)


def _defaults(tree: IndentTree) -> dict[str, str]:
    """The literal ``inputs.<name>.default`` values of an action."""
    top = next((i for i, line in enumerate(tree.lines)
                if line.structural and line.level == 0 and line.key == "inputs"), None)
    out: dict[str, str] = {}
    for name in tree.children(top) if top is not None else []:
        default = _child(tree, name, "default")
        value = scalar(tree.lines[default].value) if default is not None else None
        if value is not None:
            out[tree.lines[name].key] = value
    return out


def _run_steps(action: str) -> tuple[list[_Step], dict[str, str]] | None:
    """The ``run:``, known lint action and nested local ``uses:`` steps of a composite action,
    and its input defaults; None for any other action."""
    lines = join_continued(action).splitlines()
    tree = IndentTree(parse_lines(lines))
    runs = next((i for i, line in enumerate(tree.lines)
                 if line.structural and line.level == 0 and line.key == "runs"), None)
    if runs is None:
        return None
    using = _child(tree, runs, "using")
    steps = _child(tree, runs, "steps")
    if using is None or tree.lines[using].value.strip("\"'").lower() != "composite" or steps is None:
        return None
    out: list[_Step] = []
    for item in tree.items(steps):
        run = next((k for k in item if tree.lines[k].key == "run"), None)
        uses = next((k for k in item if tree.lines[k].key == "uses"), None)
        local = uses is not None and tree.lines[uses].value.strip("\"'").startswith("./")
        kind: _Kind | None = ("local" if local else "run" if run is not None
                else "lint" if uses is not None and lint_step_action(lines[uses]) else None)
        if kind is None:
            continue   # any other remote action: not read, as at the top level
        start, end = item[0], max(tree.subtree(k).stop for k in item)
        body = frozenset(j - start for j in tree.subtree(run)) if run is not None else frozenset()
        dash = tree.lines[start].indent
        out.append(_Step(dash, tree.lines[start].level - dash, lines[start:end], body, kind))
    return out, _defaults(tree)


def _read_action(uses: str, read_local: ReadLocal) -> tuple[str, str] | None:
    """``(path, text)`` of the local action ``uses`` names, or None."""
    if not uses.startswith("./") or uses.endswith((".yml", ".yaml")):
        return None
    for rel in local_action_paths(uses):
        text = read_local(rel)
        if text is not None:
            return rel, text
    return None


def _value(line: str) -> str:
    """A run-value line's text: a ``run:`` key's value, or the line itself."""
    stripped = line.strip().removeprefix("- ")
    return stripped.split(":", 1)[1].strip() if stripped.startswith("run:") else stripped


def _fill(line: str, given: dict[str, str]) -> str:
    """``line`` with each ``${{ inputs.<name> }}`` that ``given`` holds replaced by its value."""
    return _INPUT_REF.sub(lambda m: given.get(m.group(1), m.group(0)), line)


def _settle(line: str, given: dict[str, str], supplied: frozenset[str]) -> tuple[str, bool]:
    """``(line, unknown)``: ``${{ inputs.<name> }}`` replaced by its literal caller or default
    value; unknown when one left in command position cannot be. A whole ``run:`` of a
    ``run`` / ``script`` / ``command`` input the caller ``supplied`` is left to
    ``lib.ci_action_inputs``, which reads the caller's ``with:`` line."""
    forward = _FORWARD.match(_value(line))
    if forward and forward.group(1) in INPUT_KEYS and forward.group(1) in supplied:
        return _INPUT_REF.sub("''", line), False   # read from the caller's with: line instead
    line = _fill(line, given)
    left = (m.group(1) for m in CMD_EXPR.finditer(_value(line)))
    return line, any(_INPUT_ONLY.match(inner) or "inputs" in inner for inner in left)


def _with_values(tree: IndentTree, item: list[int]) -> dict[str, str | None]:
    """The caller step's ``with:`` keys: a literal scalar, or None for any other value."""
    return {tree.lines[c].key: scalar(tree.lines[c].value)
            for k in item if tree.lines[k].key == "with" for c in tree.children(k)}


def _caller_keys(tree: IndentTree, lines: list[str], item: list[int]) -> list[str]:
    """A calling step's ``continue-on-error`` and ``if:`` lines, to add to every copy."""
    return [lines[k].strip().removeprefix("- ") for k in item if tree.lines[k].key in _CALLER_KEYS]


def _nested(step: list[str], read_local: ReadLocal, indent: int, caller: list[str],
            path: tuple[str, ...], budget: _Budget) -> list[tuple[str, bool]] | None:
    """The copies of a nested local action ``step`` (its lines already shifted and filled),
    or None when it cannot be read: unresolvable, not composite, a cycle, too deep or past
    the read budget. ``path`` holds the action the workflow calls and each one below it."""
    tree = IndentTree(parse_lines(step))
    uses = next((i for i, line in enumerate(tree.lines)
                 if line.structural and line.key == "uses" and i in tree.mapping_of), None)
    if uses is None or len(path) > _MAX_DEPTH or budget.reads <= 0:
        return None
    found = _read_action(tree.lines[uses].value.strip("\"'"), read_local)
    if found is None or found[0] in path:
        return None
    budget.reads -= 1
    item = tree.siblings[tree.mapping_of[uses]]
    return _copies(found[1], _with_values(tree, item), read_local, indent,
                   caller + _caller_keys(tree, step, item), (*path, found[0]), budget)


def _copies(action: str, passed: dict[str, str | None], read_local: ReadLocal, indent: int,
            caller: list[str], path: tuple[str, ...], budget: _Budget) -> list[tuple[str, bool]] | None:
    """``(line, unknown)`` copies of a composite action's steps at dash ``indent``, or None
    when ``action`` is not a composite."""
    read = _run_steps(action)
    if read is None:
        return None
    steps, defaults = read
    # A caller's value replaces the default whatever it is: a non-literal one stays unknown.
    given = {k: v for k, v in (defaults | passed).items() if v is not None}
    # Only the workflow's own calling step stays in the text for lib.ci_action_inputs to read
    # a forwarded run: from; a nested step is replaced by its copies, so its value is filled in.
    supplied = frozenset(passed) if len(path) == 1 else frozenset()
    copied: list[tuple[str, bool]] = []
    for step in steps:
        shifted = [_shift(raw, indent - step.dash) for raw in step.lines]
        if step.kind == "local":
            inner = _nested([_fill(line, given) for line in shifted], read_local, indent, caller, path, budget)
            if inner is not None:
                copied += inner   # each inner copy carries the caller keys already
                continue
            copied += [(line, "uses:" in raw) for raw, line in zip(step.lines, shifted, strict=True)]
        elif step.kind == "lint":   # read by lib.ci.ci_lint_inputs as a top-level step is
            copied += [(_fill(line, given), False) for line in shifted]
        else:
            for j, line in enumerate(shifted):
                if j in step.body:
                    copied.append(_settle(line, given, supplied))
                else:   # an env: value (CMD: ${{ inputs.cmd }}) takes its literal too (#1164)
                    copied.append((_fill(line, given), False))
        copied += [(" " * (indent + step.gap) + key, False) for key in caller]
    return copied


def inline_composites(text: str, read_local: ReadLocal) -> Expanded:
    """Copy each local composite action's steps after its calling step (module docstring)."""
    lines = text.splitlines()
    tree = IndentTree(parse_lines(lines))
    after: dict[int, list[tuple[str, bool]]] = {}   # last line of a calling step -> (line, unknown)
    for i, line in enumerate(tree.lines):
        if not (line.structural and line.key == "uses" and i in tree.mapping_of):
            continue
        parent = tree.parent[tree.mapping_of[i]]
        if parent is None or tree.lines[parent].key != "steps":
            continue
        found = _read_action(line.value.strip("\"'"), read_local)
        if found is None:
            continue
        item = tree.siblings[tree.mapping_of[i]]
        copied = _copies(found[1], _with_values(tree, item), read_local, tree.lines[item[0]].indent,
                         _caller_keys(tree, lines, item), (found[0],), _Budget())
        if copied:
            after.setdefault(max(tree.subtree(k).stop for k in item) - 1, []).extend(copied)
    out: list[str] = []
    unknown: set[int] = set()
    for i, raw in enumerate(lines):
        out.append(raw)
        for copy, unsure in after.get(i, []):
            if unsure:
                unknown.add(len(out))
            out.append(copy)
    body = "\n".join(out) + ("\n" if text.endswith("\n") and out else "")
    return Expanded(body if after else text, frozenset(unknown))
