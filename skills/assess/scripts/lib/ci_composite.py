"""Read the ``run:`` steps of a local composite action a workflow step uses (issue #923).

``lib.ci_checks`` reads a workflow line by line, so a step ``uses: ./.github/actions/check``
whose ``action.yml`` runs ``ruff check .`` and ``pytest`` used to read as no command.
``inline_composites(text, read_local)`` returns the workflow text with each such action's
``run:`` steps copied in after the calling step, as sibling steps of the same job:

- **Repo-local, one level, never executed.** Only a ``uses:`` value starting ``./`` that
  names a directory (``lib.ci_action_inputs``'s ``action.yml`` / ``action.yaml`` lookup),
  read through ``read_local`` (tracked files inside the checkout, never a symlink out of it).
  The action must declare ``runs.using: composite``; a node or docker action is not read. Only
  its steps with a ``run:`` key are copied: a step that ``uses:`` another action, local or
  not, is not followed. A copied ``run: ./ci.sh`` is read by ``lib.ci_scripts`` as any CI
  line is, so a committed script's body can still upgrade it.
- **Masking and triggers.** Each copy is the whole step item, re-indented, so its own
  ``shell:`` (pipefail, errexit) and ``continue-on-error`` apply. The calling step's
  ``continue-on-error`` and ``if:`` lines are added to every copy, so ``continue-on-error:
  true`` or ``if: false`` on the caller masks them all. The copies sit in the calling job and
  workflow, so the caller's triggers and job keys apply.
- **Inputs.** ``${{ inputs.<name> }}`` is not substituted: ``lib.ci_action_inputs`` reads
  the caller's ``with:`` value instead (#799), and the calling step is left as it was.

The structure comes from ``lib.ci_masking``'s indentation model; there is no YAML parser.
Only GitHub workflow text is read. Inward-only imports: ``lib.ci_action_inputs``,
``lib.ci_continuations``, ``lib.ci_masking``. Tests: ``tests/test_ci_composite_matrix.py``.
"""
from __future__ import annotations

from lib.ci_action_inputs import ReadLocal, _local_paths
from lib.ci_continuations import join_continued
from lib.ci_masking import IndentTree, parse_lines

_CALLER_KEYS = frozenset({"continue-on-error", "if"})


def _shift(line: str, delta: int) -> str:
    if delta >= 0 or not line.strip():
        return " " * delta + line if line.strip() else line
    lead = len(line) - len(line.lstrip(" "))
    return line[min(-delta, lead):]


def _child(tree: IndentTree, line: int, name: str) -> int | None:
    return next((k for k in tree.children(line) if tree.lines[k].key == name), None)


def _run_steps(action: str) -> list[tuple[int, list[str]]]:
    """``(dash indent, lines)`` of each ``run:`` step of a composite action, or none."""
    lines = join_continued(action).splitlines()
    tree = IndentTree(parse_lines(lines))
    runs = next((i for i, line in enumerate(tree.lines)
                 if line.structural and line.level == 0 and line.key == "runs"), None)
    if runs is None:
        return []
    using = _child(tree, runs, "using")
    steps = _child(tree, runs, "steps")
    if using is None or tree.lines[using].value.strip("\"'").lower() != "composite" or steps is None:
        return []
    out: list[tuple[int, list[str]]] = []
    for item in tree.items(steps):
        if not any(tree.lines[k].key == "run" for k in item):
            continue
        end = max(tree.subtree(k).stop for k in item)
        out.append((tree.lines[item[0]].indent, lines[item[0]:end]))
    return out


def _read_action(uses: str, read_local: ReadLocal) -> str | None:
    if not uses.startswith("./") or uses.endswith((".yml", ".yaml")):
        return None
    for rel in _local_paths(uses):
        text = read_local(rel)
        if text is not None:
            return text
    return None


def inline_composites(text: str, read_local: ReadLocal) -> str:
    """Copy each local composite action's ``run:`` steps after its calling step (module docstring)."""
    lines = text.splitlines()
    tree = IndentTree(parse_lines(lines))
    after: dict[int, list[str]] = {}   # last line of a calling step -> copied lines
    for i, line in enumerate(tree.lines):
        if not (line.structural and line.key == "uses" and i in tree.mapping_of):
            continue
        parent = tree.parent[tree.mapping_of[i]]
        if parent is None or tree.lines[parent].key != "steps":
            continue
        action = _read_action(line.value.strip("\"'"), read_local)
        if action is None:
            continue
        item = tree.siblings[tree.mapping_of[i]]
        indent = tree.lines[item[0]].indent
        caller = [lines[k].strip().removeprefix("- ") for k in item if tree.lines[k].key in _CALLER_KEYS]
        copied: list[str] = []
        for dash, step in _run_steps(action):
            delta = indent - dash
            body = [_shift(s, delta) for s in step]
            level = tree.lines[item[0]].level
            copied += body + [" " * level + key for key in caller]
        if copied:
            after.setdefault(max(tree.subtree(k).stop for k in item) - 1, []).extend(copied)
    if not after:
        return text
    out: list[str] = []
    for i, raw in enumerate(lines):
        out.append(raw)
        out.extend(after.get(i, []))
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")
