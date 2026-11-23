"""Does the tree hold the script a CI line runs? (issue #937)

``script_path(text)`` names the repository file a command runs as a committed script:
``./ci.sh`` is ``ci.sh``, ``bash scripts/test.sh`` and ``. scripts/test.sh`` (``source``, #1033)
are ``scripts/test.sh``, ``bin/test``
is itself. It returns None for no script (``bash -c '...'``, ``pytest``), a modelled
runner (``./gradlew``), a known tool installed into the tree (``_KNOWN_TOOLS``: the lint,
type, test and analyser programs of ``lib.check_command``, so ``./bin/golangci-lint run``
after ``install.sh -b ./bin`` and ``bin/phpunit`` are tools, not scripts) and a path the
tree cannot settle (absolute, ``~``, ``$VAR``, ``../``). Only the shell's own leading
flags are read, so ``bash scripts/test.sh --ci`` runs ``scripts/test.sh``, and a short
cluster ending in ``o`` / ``O`` takes the next word (``bash -o pipefail ./ci.sh``,
``bash -euo pipefail ./ci.sh``, GitHub's ``--noprofile --norc -eo pipefail``).

``present_in(root, tracked)`` is the predicate ``lib.ci_checks`` reads a script against.
A script is present at its path, or as the suffix of a tracked path under a directory
(``cd svc && ./ci.sh``, ``working-directory:``). The suffix match is lenient on purpose:
any tracked ``*/ci.sh``, even under ``testdata/``, credits a root ``./ci.sh``, because the
CI line's working directory is not resolved. A path outside what git tracks by design is
not missing, as in ``lib.command_resolver``: an excluded tree (``./vendor/bin/phpunit``,
``build/``) or a gitignored path that a step installs or builds. Answers are cached per
path.

``committed(text, present)`` is false only when ``text`` runs a script that ``present``
says the tree does not hold; with no predicate every script reads present. A CI check or
lint step that runs such a script reads ``enforced_basis`` / ``lint_basis``
``script_not_found`` (null) in ``lib.ci_checks``, never true. ``runs_missing(test)`` turns a
``lib.ci_checks`` line test into the one that flags such a line: ``test`` holds with every
script read as present and fails with ``present``. ``lib.ci_checks`` builds its missing-check
test under ``named_check.instruction_words``, so a missing ``./ci.sh`` stays
``script_not_found`` after #951. Tests:
``tests/test_check_command.py`` (``script_path``) and ``tests/test_ci_checks.py``.

``ScriptTree(root, tracked)`` is that predicate plus a body reader (#1033):
``script_runs(text, present, test)`` is true when ``present`` is a ``ScriptTree`` and the script
``text`` runs, read at its exact tracked path (never the lenient suffix; at most 64 KiB),
provably fails on a failing ``test`` command (``lib.script_body``). The form decides the
start state: ``. x`` / ``source x`` is sourced, ``bash -e x`` takes the shell's flags, ``./x``
its shebang. ``lib.ci_checks`` adds it to its check and lint line tests, so a read body only
upgrades a name reading: ``./ci.sh`` that ends in ``pytest`` reads true; one it cannot show
keeps the name's reading (``check_target``, or true for ``./test.sh``).

Inward-only imports: ``lib.check_command``, ``lib.command_index``,
``lib.command_resolver``, ``lib.doc_graph``, ``lib.lint_vocabulary`` and ``lib.script_body``; never an
orchestrator.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path, PurePosixPath

from lib.check_command import BARE_RUN_PROGRAMS, LINT_PROGRAMS, TYPE_PROGRAMS
from lib.command_index import is_gitignored
from lib.command_resolver import RUNNERS, command_words
from lib.doc_graph import is_excluded_path
from lib.lint_vocabulary import ANALYSER_PROGRAMS, program_name
from lib.script_body import body_runs, start_state

Present = Callable[[str], bool]

def _takes_value(flag: str) -> bool:
    """A short-flag cluster ending in ``o`` / ``O`` (``-o``, ``-euo``, ``+O``) takes the next word."""
    return not flag.startswith("--") and len(flag) > 1 and flag[-1] in "oO"
# A tool a CI step installs into the tree (``install.sh -b ./bin``) is not a committed script.
_KNOWN_TOOLS = LINT_PROGRAMS | TYPE_PROGRAMS | BARE_RUN_PROGRAMS | ANALYSER_PROGRAMS


def script_path(text: str) -> str | None:
    """The repository file ``text`` runs as a committed script, or None (module docstring)."""
    words = command_words(text)
    if not words:
        return None
    head = words[0]
    if head in ("bash", "sh", "zsh", "source"):   # . reads as source (command_words)
        i, flags = 1, []
        while i < len(words) and words[i][:1] in ("-", "+"):
            flags.append(words[i])
            i += 2 if _takes_value(words[i]) else 1
        rest = words[i:]
        if not rest or any("c" in w[1:] for w in flags if w.startswith("-") and not w.startswith("--")):
            return None
        path = rest[0]
    elif head in RUNNERS or not (head.startswith(("./", "bin/")) or head.endswith(".sh")):
        return None
    else:
        path = head
    if path.startswith(("/", "~")) or "$" in path or program_name(path) in _KNOWN_TOOLS:
        return None
    norm = str(PurePosixPath(path.removeprefix("./")))
    return None if norm.split("/")[0] == ".." else norm


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

    def __call__(self, path: str) -> bool:
        return self._present(path)

    def runs(self, text: str, test: Callable[[str], bool]) -> bool:
        """Whether the script ``text`` runs, at its exact tracked path, provably fails on a
        failing ``test`` command (``lib.script_body``); false when its body is not read."""
        path = script_path(text)
        if path is None or path not in self._tracked or any(
                rel.endswith("/" + path) for rel in self._tracked):
            return False   # cd svc && ./ci.sh may run svc/ci.sh: only an unambiguous path is read
        try:
            with (self._root / path).open("rb") as handle:
                raw = handle.read(_MAX_BODY + 1)
        except OSError:
            return False
        if len(raw) > _MAX_BODY:
            return False
        body = raw.decode("utf-8", errors="replace")
        words = command_words(text)
        form = "sourced" if words[0] == "source" else "shell" if words[0] in _SHELL_HEADS else "executed"
        state = start_state(form, body, _flags(words) if form == "shell" else [])
        return state is not None and body_runs(body, errexit=state[0], pipefail=state[1], test=test,
                                               sourced=form == "sourced")


def script_runs(text: str, present: Present | None, test: Callable[[str], bool]) -> bool:
    """``ScriptTree.runs`` when ``present`` is one, else false (the line was read without a tree)."""
    return isinstance(present, ScriptTree) and present.runs(text, test)


_MAX_BODY = 64 * 1024
_SHELL_HEADS = frozenset({"bash", "sh", "zsh"})


def _flags(words: list[str]) -> list[str]:
    """A shell's own leading flags with their values (``bash -eo pipefail x`` -> ``-eo pipefail``)."""
    out: list[str] = []
    i = 1
    while i < len(words) and words[i][:1] in ("-", "+"):
        out += words[i:i + 2] if _takes_value(words[i]) else words[i:i + 1]
        i += 2 if _takes_value(words[i]) else 1
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
