"""Does the tree hold the script a CI line runs? (issue #937)

``script_path(text)`` (defined in ``lib.ci.ci_step_paths`` since #845 and re-exported here, as
the shared CI-step follower's path half) names the repository file a command runs as a committed script:
``./ci.sh`` is ``ci.sh``, ``bash scripts/test.sh`` (``sh``, ``dash`` and ``zsh`` too, #1061) and ``. scripts/test.sh`` (``source``, #1033)
are ``scripts/test.sh``, ``bin/test``
is itself. It returns None for no script (``bash -c '...'``, ``pytest``), a modelled
runner (``./gradlew``), a known tool installed into the tree (``_KNOWN_TOOLS``: the lint,
type, test and analyser programs of ``lib.ci.check_command``, so ``./bin/golangci-lint run``
after ``install.sh -b ./bin`` and ``bin/phpunit`` are tools, not scripts) and a path the
tree cannot settle (absolute, ``~``, ``$VAR``, ``../``). Only the shell's own leading
flags are read, so ``bash scripts/test.sh --ci`` runs ``scripts/test.sh``, and a short
cluster ending in ``o`` / ``O`` takes the next word (``bash -o pipefail ./ci.sh``,
``bash -euo pipefail ./ci.sh``, GitHub's ``--noprofile --norc -eo pipefail``).

``present_in(root, tracked)`` is the predicate ``lib.ci.ci_checks`` reads a script against.
A script is present at its path, or as the suffix of a tracked path under a directory
(``cd svc && ./ci.sh``, ``working-directory:``). The suffix match is lenient on purpose:
any tracked ``*/ci.sh``, even under ``testdata/``, credits a root ``./ci.sh``, because the
CI line's working directory is not resolved. A path outside what git tracks by design is
not missing, as in ``lib.instructions.command_resolver``: an excluded tree (``./vendor/bin/phpunit``,
``build/``) or a gitignored path that a step installs or builds. Answers are cached per
path.

``committed(text, present)`` is false only when ``text`` runs a script that ``present``
says the tree does not hold; with no predicate every script reads present. A CI check or
lint step that runs such a script reads ``enforced_basis`` / ``lint_basis``
``script_not_found`` (null) in ``lib.ci.ci_checks``, never true. ``runs_missing(test)`` turns a
``lib.ci.ci_checks`` line test into the one that flags such a line: ``test`` holds with every
script read as present and fails with ``present``. ``lib.ci.ci_checks`` builds its missing-check
test under ``named_check.instruction_words``, so a missing ``./ci.sh`` stays
``script_not_found`` after #951. Tests:
``tests/test_check_command.py`` (``script_path``) and ``tests/test_ci_checks.py``.

``ScriptTree(root, tracked)`` is that predicate plus a body reader (#1033):
``script_runs(text, present, test)`` is true when ``present`` is a ``ScriptTree`` and the script
``text`` runs, read at its exact tracked path (never the lenient suffix; at most 64 KiB),
provably fails on a failing ``test`` command (``lib.script_body``); the body is read through
``lib.ci.ci_step_paths.read_script`` (#845), so a tracked symlink that resolves outside the
checkout is never read (#799), so a script is followed one level and only
inside the repository. The form decides the
start state: ``. x`` / ``source x`` is sourced, ``bash -e x`` takes the shell's flags, ``./x``
its shebang, and only when git records the file as executable (mode 100755), since a
``./x`` of a 100644 file exits 126 and gates nothing. ``lib.ci.ci_checks`` adds it to its check and lint line tests, so a read body only
upgrades a name reading: ``./ci.sh`` that ends in ``pytest`` reads true; one it cannot show
keeps the name's reading (``check_target``, or true for ``./test.sh``).

Inward-only imports: ``lib.ci.ci_step_paths``, ``lib.instructions.command_index``,
``lib.instructions.command_resolver``, ``lib.docs.doc_graph`` and ``lib.script_body``; never an
orchestrator.
"""
from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from lib.ci.ci_step_paths import SHELL_HEADS, read_script, script_path, takes_value
from lib.instructions.command_index import is_gitignored
from lib.instructions.command_resolver import command_words
from lib.docs.doc_graph import is_excluded_path
from lib.script_body import body_runs, start_state

__all__ = ["Present", "ScriptTree", "committed", "present_in", "runs_missing", "script_path",
           "script_runs"]

Present = Callable[[str], bool]


def runs_missing(test: Callable[..., bool]) -> Callable[..., bool]:
    """The line test that holds where ``test`` runs a check or linter only through a script
    the tree does not hold (#937): true with no ``present`` predicate, false with it."""
    def missing(line: str, *, script_item: bool = True, present: Present | None = None) -> bool:
        return (test(line, script_item=script_item)
                and not test(line, script_item=script_item, present=present))
    return missing


class ScriptTree:
    """A ``present`` predicate that can also read a committed script's body (#1033)."""

    def __init__(self, root: Path, tracked: frozenset[str]) -> None:
        self._root, self._tracked, self._present = root, tracked, present_in(root, tracked)
        self._modes: dict[str, str] | None = None

    def executable(self, path: str) -> bool:
        """Whether git records ``path`` as executable (mode 100755): ``./x`` of a 100644
        file exits 126, so the step fails whatever the check does and the check gates nothing."""
        if self._modes is None:
            self._modes = _index_modes(self._root)
        return self._modes.get(path) == "100755"

    def __call__(self, path: str) -> bool:
        return self._present(path)

    def runs(self, text: str, test: Callable[[str], bool]) -> bool:
        """Whether the script ``text`` runs, at its exact tracked path, provably fails on a
        failing ``test`` command (``lib.script_body``); false when its body is not read."""
        path = script_path(text)
        if path is None or path not in self._tracked or any(
                rel.endswith("/" + path) for rel in self._tracked):
            return False   # cd svc && ./ci.sh may run svc/ci.sh: only an unambiguous path is read
        body = read_script(self._root, path)   # never a symlink out of the checkout (#799)
        if body is None:
            return False
        words = command_words(text)
        form = "sourced" if words[0] == "source" else "shell" if words[0] in SHELL_HEADS else "executed"
        if form == "executed" and not self.executable(path):
            return False
        state = start_state(form, body, _flags(words) if form == "shell" else [])
        return state is not None and body_runs(body, errexit=state[0], pipefail=state[1], test=test,
                                               sourced=form == "sourced")


def script_runs(text: str, present: Present | None, test: Callable[[str], bool]) -> bool:
    """``ScriptTree.runs`` when ``present`` is one, else false (the line was read without a tree)."""
    return isinstance(present, ScriptTree) and present.runs(text, test)


def _index_modes(root: Path) -> dict[str, str]:
    """``{path: mode}`` from ``git ls-files -s -z``; empty outside a git checkout."""
    try:
        done = subprocess.run(["git", "-C", str(root), "ls-files", "-s", "-z"], capture_output=True,
                              encoding="utf-8", errors="surrogateescape", check=False, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return {}
    if done.returncode:
        return {}
    modes: dict[str, str] = {}
    for entry in done.stdout.split("\0"):
        meta, _, path = entry.partition("\t")
        if path and meta:
            modes[path] = meta.split(" ", 1)[0]
    return modes


def _flags(words: list[str]) -> list[str]:
    """A shell's own leading flags with their values (``bash -eo pipefail x`` -> ``-eo pipefail``)."""
    out: list[str] = []
    i = 1
    while i < len(words) and words[i][:1] in ("-", "+"):
        out += words[i:i + 2] if takes_value(words[i]) else words[i:i + 1]
        i += 2 if takes_value(words[i]) else 1
    return out


def committed(text: str, present: Present | None) -> bool:
    """False only when ``text`` runs a script that ``present`` says the tree does not hold."""
    path = script_path(text) if present is not None else None
    return path is None or present is None or present(path)


def present_in(root: Path, tracked: frozenset[str]) -> Present:
    """The cached does-the-tree-hold-it predicate for ``root`` (module docstring)."""
    seen: dict[str, bool] = {}

    def present(path: str) -> bool:
        if path not in seen:
            seen[path] = (path in tracked or any(rel.endswith("/" + path) for rel in tracked)
                          or is_excluded_path(Path(path)) or is_gitignored(root, path))
        return seen[path]
    return present
