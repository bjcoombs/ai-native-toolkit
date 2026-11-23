"""Which ``with:`` inputs of a GitHub Actions step are shell commands? (issue #799)

``lib.ci_checks`` reads a workflow line by line, so a ``run:`` or ``script:`` key under a
``uses:`` step's ``with:`` reads as shell whatever the action does with it. An action decides
that, so ``read_inputs(text, read_local)`` sorts each ``run``, ``script`` and ``command``
input of a step (or a job calling a reusable workflow) that has a ``uses:`` key:

- **Shell runner** (read as a run). A local action (``uses: ./.github/actions/common``) whose
  committed ``action.yml`` / ``action.yaml`` (or, for a local ``*.yml`` reusable workflow, that
  file) has a ``run:`` step that is exactly ``${{ inputs.<name> }}``, for that input name; or
  a listed third-party runner (``SHELL_INPUTS``, each input name checked against the action's
  own ``action.yml``: ``nick-fields/retry`` ``command``, ``reactivecircus/android-emulator-runner``
  ``script``, ``coactions/setup-xvfb`` and ``gabrielbb/xvfb-action`` ``run``). A ``command:``
  key is rewritten to ``run:`` so the line readers drop its lead.
- **Not shell** (``SCRIPT_ACTIONS``: ``actions/github-script``, whose ``script:`` is
  JavaScript). Its lines are blanked.
- **Declared** (any other action). The action's code is not read, so the command may or may
  not run and may or may not fail the job. Its lines stay in the text, so
  ``test_or_lint_configured`` counts them, and their indexes come back in ``declared``:
  ``lib.ci_checks`` reads them as ``check_target`` (null), never as an enforced run.

Other ``with:`` inputs (``args:``, ``ref:``) are left as they were. ``read_local(path)`` returns
a committed file's text inside the checkout, or None; ``lib.ci_checks`` passes one that reads
tracked files only and never a path outside the repository. The structure comes from
``lib.ci_masking``'s indentation model; there is no YAML parser. Every line keeps its index.

Inward-only imports: ``lib.ci_masking``. Tests: ``tests/test_ci_action_inputs.py``.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import NamedTuple

from lib.ci_masking import IndentTree, parse_lines

ReadLocal = Callable[[str], str | None]

# Third-party actions that run an input as a shell command, by the input's name.
SHELL_INPUTS: dict[str, str] = {
    "nick-fields/retry": "command",
    "reactivecircus/android-emulator-runner": "script",
    "coactions/setup-xvfb": "run",
    "gabrielbb/xvfb-action": "run",
}
# Actions whose ``script:`` input is not shell.
SCRIPT_ACTIONS = frozenset({"actions/github-script"})
INPUT_KEYS = frozenset({"run", "script", "command"})
_FORWARD = re.compile(r"""^["']?\$\{\{\s*inputs\.([\w-]+)\s*\}\}["']?$""")
_REMOTE = re.compile(r"^([\w.-]+/[\w.-]+)")


class Inputs(NamedTuple):
    """The workflow text with the shell inputs settled, and the declared-only line indexes."""

    text: str
    declared: frozenset[int]


def forwarded_inputs(action_text: str) -> frozenset[str]:
    """The input names a local action or reusable workflow runs as a whole ``run:`` step."""
    lines = action_text.splitlines()
    tree = IndentTree(parse_lines(lines))
    names: set[str] = set()
    for i, line in enumerate(tree.lines):
        if line.structural and line.key == "run":
            value = line.value
        else:
            owner = tree.owner[i]
            if owner is None or tree.lines[owner].key != "run":
                continue
            value = lines[i].strip()
        match = _FORWARD.match(value)
        if match:
            names.add(match.group(1))
    return frozenset(names)


def _local_paths(uses: str) -> list[str]:
    path = uses.removeprefix("./").rstrip("/")
    if not path or path.split("/")[0] == ".." or "${{" in path:
        return []
    if path.endswith((".yml", ".yaml")):
        return [path]
    return [f"{path}/action.yml", f"{path}/action.yaml"]


def _shell_input(uses: str, key: str, read_local: ReadLocal) -> bool | None:
    """True when the action runs input ``key`` as shell, False when it is known not to, else None."""
    if uses.startswith("./"):
        for rel in _local_paths(uses):
            text = read_local(rel)
            if text is not None:
                return True if key in forwarded_inputs(text) else None
        return None
    match = _REMOTE.match(uses)
    name = match.group(1).lower() if match else ""
    if SHELL_INPUTS.get(name) == key:
        return True
    return False if name in SCRIPT_ACTIONS else None


def read_inputs(text: str, read_local: ReadLocal) -> Inputs:
    """Settle the ``with:`` shell inputs of one workflow file (module docstring)."""
    lines = text.splitlines()
    tree = IndentTree(parse_lines(lines))
    out = list(lines)
    declared: set[int] = set()
    for i, line in enumerate(tree.lines):
        if not (line.structural and line.key == "with" and i in tree.mapping_of):
            continue
        step = tree.siblings[tree.mapping_of[i]]
        uses = next((tree.lines[k].value.strip("\"'") for k in step if tree.lines[k].key == "uses"), "")
        if not uses:
            continue
        for k in tree.children(i):
            key = tree.lines[k].key
            if key not in INPUT_KEYS:
                continue
            span = tree.subtree(k)
            verdict = _shell_input(uses, key, read_local)
            if verdict is True:
                if key == "command":
                    out[k] = re.sub(r"\bcommand:", "run:", out[k], count=1)
            elif verdict is False:
                for j in span:
                    out[j] = ""
            else:
                declared.update(span)
    return Inputs("\n".join(out) + ("\n" if text.endswith("\n") else ""), frozenset(declared))
