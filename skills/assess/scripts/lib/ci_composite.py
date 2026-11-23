"""Read the ``run:`` steps of a local composite action a workflow step uses (issue #923).

``lib.ci_checks`` reads a workflow line by line, so a step ``uses: ./.github/actions/check``
whose ``action.yml`` runs ``ruff check .`` and ``pytest`` used to read as no command.
``inline_composites(text, read_local)`` returns the workflow text with each such action's
``run:`` steps copied in after the calling step, as sibling steps of the same job:

- **Repo-local, one level, never executed.** Only a ``uses:`` value starting ``./`` that
  names a directory (``lib.ci_action_inputs``'s ``action.yml`` / ``action.yaml`` lookup),
  read through ``read_local`` (tracked files inside the checkout, never a symlink out of it).
  The action must declare ``runs.using: composite``; a node or docker action is not read. Only
  its steps with a ``run:`` key are copied: a step that ``uses:`` a remote action is
  dropped, and one that ``uses:`` another local action is not followed (below). A copied ``run: ./ci.sh`` is read by ``lib.ci_scripts`` as any CI
  line is, so a committed script's body can still upgrade it.
- **Masking and triggers.** Each copy is the whole step item, re-indented, so its own
  ``shell:`` (pipefail, errexit) and ``continue-on-error`` apply. The calling step's
  ``continue-on-error`` and ``if:`` lines are added to every copy, so ``continue-on-error:
  true`` or ``if: false`` on the caller masks them all. The copies sit in the calling job and
  workflow, so the caller's triggers and job keys apply.
- **Inputs.** ``${{ inputs.<name> }}`` in a copied ``run:`` value is replaced by the
  caller's literal ``with:`` scalar for that name, else, only when the caller's ``with:``
  omits the name, the action's literal ``inputs.<name>.default``. One left in command position (no value, a block or
  ``${{ }}`` value) makes that line unknown. A whole ``run: ${{ inputs.run }}`` (or
  ``script`` / ``command``) is left as it is: ``lib.ci_action_inputs`` already reads the
  caller's ``with:`` value for it (#799), so it is not counted twice.
- **Unknown, never 0.** A step of the action that ``uses:`` another local action is not
  followed: it is copied with its ``uses:`` line in ``unknown``. ``inline_composites``
  returns ``(text, unknown)`` (``lib.ci_matrix.Expanded``); ``lib.ci_checks`` reads the
  unknown lines as ``check_target`` (null) unless masked, as for a matrix command.

The structure comes from ``lib.ci_masking``'s indentation model; there is no YAML parser.
Only GitHub workflow text is read. Inward-only imports: ``lib.ci_action_inputs``,
``lib.ci_continuations``, ``lib.ci_masking``, ``lib.ci_matrix``. Tests: ``tests/test_ci_composite_matrix.py``.
"""
from __future__ import annotations

import re
from typing import NamedTuple

from lib.ci_action_inputs import INPUT_KEYS, ReadLocal, _local_paths
from lib.ci_continuations import join_continued
from lib.ci_masking import IndentTree, parse_lines
from lib.ci_matrix import CMD_EXPR, Expanded, scalar

_CALLER_KEYS = frozenset({"continue-on-error", "if"})
_INPUT_REF = re.compile(r"\$\{\{\s*inputs\.([\w-]+)\s*\}\}")
_INPUT_ONLY = re.compile(r"^\s*inputs\.([\w-]+)\s*$")
_FORWARD = re.compile(r"""^["']?\$\{\{\s*inputs\.([\w-]+)\s*\}\}["']?$""")


class _Step(NamedTuple):
    dash: int                  # the step's dash indent in the action
    lines: list[str]
    body: frozenset[int]       # run-value lines, relative to the step
    nested: bool               # a step that uses another local action: not followed


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


def _run_steps(action: str) -> tuple[list[_Step], dict[str, str]]:
    """The ``run:`` steps and nested local ``uses:`` steps of a composite action, and its
    input defaults; nothing for any other action."""
    lines = join_continued(action).splitlines()
    tree = IndentTree(parse_lines(lines))
    runs = next((i for i, line in enumerate(tree.lines)
                 if line.structural and line.level == 0 and line.key == "runs"), None)
    if runs is None:
        return [], {}
    using = _child(tree, runs, "using")
    steps = _child(tree, runs, "steps")
    if using is None or tree.lines[using].value.strip("\"'").lower() != "composite" or steps is None:
        return [], {}
    out: list[_Step] = []
    for item in tree.items(steps):
        run = next((k for k in item if tree.lines[k].key == "run"), None)
        uses = next((k for k in item if tree.lines[k].key == "uses"), None)
        nested = uses is not None and tree.lines[uses].value.strip("\"'").startswith("./")
        if run is None and not nested:
            continue
        start, end = item[0], max(tree.subtree(k).stop for k in item)
        body = frozenset(j - start for j in tree.subtree(run)) if run is not None else frozenset()
        out.append(_Step(tree.lines[start].indent, lines[start:end], body, nested))
    return out, _defaults(tree)


def _read_action(uses: str, read_local: ReadLocal) -> str | None:
    if not uses.startswith("./") or uses.endswith((".yml", ".yaml")):
        return None
    for rel in _local_paths(uses):
        text = read_local(rel)
        if text is not None:
            return text
    return None


def _value(line: str) -> str:
    """A run-value line's text: a ``run:`` key's value, or the line itself."""
    stripped = line.strip().removeprefix("- ")
    return stripped.split(":", 1)[1].strip() if stripped.startswith("run:") else stripped


def _settle(line: str, given: dict[str, str]) -> tuple[str, bool]:
    """``(line, unknown)``: ``${{ inputs.<name> }}`` replaced by its literal caller or default
    value; unknown when one left in command position cannot be. A whole ``run:`` of a
    ``run`` / ``script`` / ``command`` input is left to ``lib.ci_action_inputs``."""
    forward = _FORWARD.match(_value(line))
    if forward and forward.group(1) in INPUT_KEYS:
        return line, False
    line = _INPUT_REF.sub(lambda m: given.get(m.group(1), m.group(0)), line)
    left = (m.group(1) for m in CMD_EXPR.finditer(_value(line)))
    return line, any(_INPUT_ONLY.match(inner) or "inputs" in inner for inner in left)


def _with_values(tree: IndentTree, item: list[int]) -> dict[str, str | None]:
    """The caller step's ``with:`` keys: a literal scalar, or None for any other value."""
    return {tree.lines[c].key: scalar(tree.lines[c].value)
            for k in item if tree.lines[k].key == "with" for c in tree.children(k)}


def inline_composites(text: str, read_local: ReadLocal) -> Expanded:
    """Copy each local composite action's ``run:`` steps after its calling step (module docstring)."""
    lines = text.splitlines()
    tree = IndentTree(parse_lines(lines))
    after: dict[int, list[tuple[str, bool]]] = {}   # last line of a calling step -> (line, unknown)
    for i, line in enumerate(tree.lines):
        if not (line.structural and line.key == "uses" and i in tree.mapping_of):
            continue
        parent = tree.parent[tree.mapping_of[i]]
        if parent is None or tree.lines[parent].key != "steps":
            continue
        action = _read_action(line.value.strip("\"'"), read_local)
        if action is None:
            continue
        steps, defaults = _run_steps(action)
        item = tree.siblings[tree.mapping_of[i]]
        # A caller's value replaces the default whatever it is: a non-literal one stays unknown.
        given = {k: v for k, v in (defaults | _with_values(tree, item)).items() if v is not None}
        indent, level = tree.lines[item[0]].indent, tree.lines[item[0]].level
        caller = [lines[k].strip().removeprefix("- ") for k in item if tree.lines[k].key in _CALLER_KEYS]
        copied: list[tuple[str, bool]] = []
        for step in steps:
            for j, raw in enumerate(step.lines):
                shifted = _shift(raw, indent - step.dash)
                if step.nested:
                    copied.append((shifted, "uses:" in raw))
                elif j in step.body:
                    copied.append(_settle(shifted, given))
                else:
                    copied.append((shifted, False))
            copied += [(" " * level + key, False) for key in caller]
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
