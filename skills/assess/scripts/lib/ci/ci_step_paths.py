"""Which repository file a CI step names, and the one safe way to read it (issue #845).

The path half of the shared CI-step follower (``lib.ci.ci_step_follower``). Every reader that
follows a CI step into the repository resolves the step here and reads through here, so the
safety rules are written once: **repo-local only, nothing executed**, and a tracked path whose
resolution leaves the checkout (a symlink to ``/etc/passwd``, ``../other``) is never read (#799).

- ``script_path(text)`` names the repository file a command runs as a committed script (moved
  from ``lib.ci.ci_scripts``, #937): ``./ci.sh`` is ``ci.sh``, ``bash scripts/test.sh``
  (``sh``, ``dash`` and ``zsh`` too, #1061) and ``. scripts/test.sh`` (``source``, #1033) are
  ``scripts/test.sh``, ``bin/test`` is itself. It returns None for no script (``bash -c '...'``,
  ``pytest``), a modelled runner (``./gradlew``), a known tool installed into the tree
  (``KNOWN_TOOLS``: the lint, type, test and analyser programs of ``lib.ci.check_command``, so
  ``./bin/golangci-lint run`` and ``bin/phpunit`` are tools, not scripts) and a path the tree
  cannot settle (absolute, ``~``, ``$VAR``, ``../``). Only the shell's own leading flags are
  read, and a short cluster ending in ``o`` / ``O`` takes the next word (``bash -euo pipefail
  ./ci.sh``, GitHub's ``--noprofile --norc -eo pipefail``).
- ``inside(root, rel)`` is the symlink rule; ``local_reader(root, tracked)`` the ``ReadLocal``
  every follower is given (a tracked file inside the checkout, at most
  ``lib.instructions.command_index.MAX_CONFIG_BYTES``, else None); ``read_script(root, rel)``
  a script body for ``lib.ci.ci_scripts.ScriptTree`` (at most 64 KiB, else None).
- ``local_workflow(uses)`` is the ``.github/workflows/<file>`` path a ``uses:`` value calls as
  a local reusable workflow, and ``local_calls(text)`` the file names a workflow calls that way
  (``lib.ci.ci_triggers``). ``read_action(uses, read_local)`` is ``(path, text)`` of the local
  action a ``uses: ./dir`` names (its ``action.yml`` / ``action.yaml``, ``lib.ci.ci_composite``
  and ``lib.agents.agent_ops``).

Inward-only imports: ``lib.ci.check_command``, ``lib.ci.ci_action_inputs``,
``lib.instructions.command_index``, ``lib.instructions.command_resolver`` and
``lib.ci.lint_vocabulary``; never an orchestrator. Tests: ``tests/test_ci_step_follower.py``,
``tests/test_check_command.py`` (``script_path``).
"""
from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from lib.ci.check_command import BARE_RUN_PROGRAMS, LINT_PROGRAMS, TYPE_PROGRAMS
from lib.ci.ci_action_inputs import ReadLocal, local_action_paths
from lib.ci.lint_vocabulary import ANALYSER_PROGRAMS, program_name
from lib.instructions.command_index import read_config
from lib.instructions.command_resolver import RUNNERS, command_words

# A tool a CI step installs into the tree (``install.sh -b ./bin``) is not a committed script.
KNOWN_TOOLS = LINT_PROGRAMS | TYPE_PROGRAMS | BARE_RUN_PROGRAMS | ANALYSER_PROGRAMS
SHELL_HEADS = frozenset({"bash", "sh", "dash", "zsh"})
MAX_SCRIPT = 64 * 1024
LOCAL_WORKFLOW = re.compile(r"""^["']?\./(\.github/workflows/[\w.-]+\.ya?ml)""")
_LOCAL_CALL = re.compile(r"uses:\s*['\"]?\./\.github/workflows/([\w.-]+\.ya?ml)")


def takes_value(flag: str) -> bool:
    """A short-flag cluster ending in ``o`` / ``O`` (``-o``, ``-euo``, ``+O``) takes the next word."""
    return flag in ("--rcfile", "--init-file") or (
        not flag.startswith("--") and len(flag) > 1 and flag[-1] in "oO")   # bash --rcfile f x.sh (#955)


def script_path(text: str) -> str | None:
    """The repository file ``text`` runs as a committed script, or None (module docstring)."""
    words = command_words(text)
    if not words:
        return None
    head = words[0]
    if head in ("bash", "sh", "dash", "zsh", "source"):   # . reads as source (command_words)
        i, flags = 1, []
        while i < len(words) and words[i][:1] in ("-", "+"):
            flags.append(words[i])
            i += 2 if takes_value(words[i]) else 1
        rest = words[i:]
        if not rest or any("c" in w[1:] for w in flags if w.startswith("-") and not w.startswith("--")):
            return None
        path = rest[0]
    elif head in RUNNERS or not (head.startswith(("./", "bin/")) or head.endswith(".sh")):
        return None
    else:
        path = head
    if path.startswith(("/", "~")) or "$" in path or program_name(path) in KNOWN_TOOLS:
        return None
    norm = str(PurePosixPath(path.removeprefix("./")))
    return None if norm.split("/")[0] == ".." else norm


def inside(root: Path, rel: str) -> bool:
    """Whether ``rel`` resolves inside the checkout ``root`` (a symlink out of it does not, #799)."""
    base = root.resolve()
    return (base / rel).resolve().is_relative_to(base)


def local_reader(root: Path, tracked: frozenset[str]) -> ReadLocal:
    """Read a tracked file inside ``root``, never one a symlink takes outside the checkout."""
    def read(rel: str) -> str | None:
        if rel not in tracked or not inside(root, rel):
            return None
        return read_config(root, rel)
    return read


def read_script(root: Path, rel: str) -> str | None:
    """A committed script's body (at most ``MAX_SCRIPT`` bytes), or None when it is larger,
    unreadable or resolves outside the checkout."""
    if not inside(root, rel):
        return None   # a tracked symlink out of the checkout is never read (#799)
    try:
        with (root / rel).open("rb") as handle:
            raw = handle.read(MAX_SCRIPT + 1)
    except OSError:
        return None
    return None if len(raw) > MAX_SCRIPT else raw.decode("utf-8", errors="replace")


def local_workflow(uses: str) -> str | None:
    """The ``.github/workflows/<file>`` path a ``uses:`` value calls as a local reusable workflow."""
    match = LOCAL_WORKFLOW.match(uses)
    return match.group(1) if match else None


def local_calls(text: str) -> set[str]:
    """Workflow file names (``tests.yml``) a workflow's text calls as local reusable workflows."""
    return set(_LOCAL_CALL.findall(text))


def read_action(uses: str, read_local: ReadLocal) -> tuple[str, str] | None:
    """``(path, text)`` of the local action a ``uses: ./dir`` value names, or None."""
    if not uses.startswith("./"):
        return None
    for rel in local_action_paths(uses):
        text = read_local(rel)
        if text is not None:
            return rel, text
    return None
