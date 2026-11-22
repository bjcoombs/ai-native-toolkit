"""Does the tree hold the script a CI line runs? (issue #937)

``script_path(text)`` names the repository file a command runs as a committed script:
``./ci.sh`` is ``ci.sh``, ``bash scripts/test.sh`` is ``scripts/test.sh``, ``bin/test``
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
``script_not_found`` (null) in ``lib.ci_checks``, never true. Tests:
``tests/test_check_command.py`` (``script_path``) and ``tests/test_ci_checks.py``.

Inward-only imports: ``lib.check_command``, ``lib.command_index``,
``lib.command_resolver``, ``lib.doc_graph`` and ``lib.lint_vocabulary``; never an
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
    if head in ("bash", "sh", "zsh"):
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
