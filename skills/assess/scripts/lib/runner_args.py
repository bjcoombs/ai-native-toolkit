"""How Gradle and the Python script runners read their own arguments (issue #1046).

One parser, shared by the CI-line reader (``lib.check_command``, ``lib.ci_lines``,
``lib.lint_vocabulary``) and the coverage gate (``lib.testing.coverage_ci``, #954). Every
rule was checked against Gradle 9.4, pdm 2 and poethepoet.

Gradle:

- ``gradle_tasks(args)``: ``(tasks run, tasks excluded)``. A run task is named by its last
  path part (``:app:check`` is ``check``); an option's value is no task (``-p sub``,
  ``-P key``). A word after ``-x`` / ``--exclude-task`` (or its ``=`` form) is excluded: a
  bare or root name (``test``, ``:test``) excludes the task in every project and is kept
  bare, a project path (``:app:test``) only in that project and is kept whole.
- ``gradle_dry_run(args)``: ``-m`` (alone or in a short cluster such as ``-qm``),
  ``--dry-run`` or ``--task-graph``, which execute no task. A cluster is read up to the
  first option that takes a value, so ``-Pm`` and ``-Dmode=x`` are no dry run.
- ``gradle_run(words)``: a Gradle command with its exclusions applied, so ``check -x check``
  and ``test -x test`` name no task (Gradle exits 0 and runs nothing) and ``-x test check``
  names ``check``. Other commands come back unchanged.
- ``gradle_skips_tests(words)``: a Gradle run whose ``test`` task is excluded with no other
  test task named (``check -x test`` still runs ``check``'s other tasks, never the tests):
  ``lib.ci_lines`` reads it as ``command_flags.maven_skips_tests`` (#799), a target it
  cannot settle. ``runs_no_task(words)``: a Gradle dry run or ``make_runs_no_recipe``.

Script runners (``script_run(words)``): what ``hatch run``, ``pdm [run]`` and ``poe`` run,
as ``(words, is_script)``, or None for another command (``pdm install``):

- ``hatch run X``: ``X`` and the rest; its scripts are not read, so ``X`` is read by name.
- ``pdm run X``: the script ``X`` when ``[tool.pdm.scripts]`` defines one (a script shadows
  a program of the same name), else the program ``X``. A bare ``pdm X`` that is not a pdm
  command runs only a script (pdm exits 2 with "Command not found" otherwise), so with the
  scripts read and no ``X`` among them it runs nothing (``[]``).
- ``poe X``: always the task ``X`` (poe runs no program by name).

The scripts come from the root ``pyproject.toml`` under ``reads_pdm_scripts`` (a decorator on
a scan whose first argument is the repository root: ``ci_checks.scan_ci_checks``,
``coverage_gate.detect_coverage_gate``). Outside one (an instruction file) they are not read,
and ``pdm X`` is read as a script by name. Inward-only imports: stdlib and
``lib.command_flags``; never ``lib.check_command`` or an orchestrator.
Tests: ``tests/test_runner_args.py``.
"""
from __future__ import annotations

import functools
import tomllib
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path, PurePosixPath
from typing import ParamSpec, TypeVar

from lib.command_flags import make_runs_no_recipe

GRADLE = frozenset({"gradle", "gradlew"})
_GRADLE_DRY_RUN = frozenset({"--dry-run", "--task-graph"})
# Gradle short options that take a value: the rest of a cluster after one is that value.
_GRADLE_VALUE_SHORTS = frozenset("bcDgIPpx")
_GRADLE_EXCLUDE = ("-x", "--exclude-task")
# Gradle options that take the next word as their value (``-p sub``, ``-P key``).
_GRADLE_VALUED = frozenset({
    "-p", "--project-dir", "-b", "--build-file", "-c", "--settings-file", "-I",
    "--init-script", "-g", "--gradle-user-home", "--include-build", "-D", "-P",
})


def is_gradle(words: Sequence[str]) -> bool:
    """``gradle`` or a wrapper (``./gradlew``), by basename."""
    return bool(words) and PurePosixPath(words[0]).name in GRADLE


def gradle_tasks(args: Sequence[str]) -> tuple[list[str], set[str]]:
    """``(tasks run, tasks excluded)`` of a Gradle command's arguments (module docstring)."""
    tasks: list[str] = []
    excluded: set[str] = set()
    skip: str | None = None
    for word in args:
        if skip is not None:
            if skip == "x":
                excluded.add(_excluded_name(word))
            skip = None
        elif word in _GRADLE_EXCLUDE:
            skip = "x"
        elif word.startswith("--exclude-task="):
            excluded.add(_excluded_name(word.partition("=")[2]))
        elif word.startswith("-"):
            skip = "v" if word in _GRADLE_VALUED else None
        elif not word.startswith("$"):
            tasks.append(word.rsplit(":", 1)[-1])
    return tasks, excluded


def _excluded_name(task: str) -> str:
    """``test`` and ``:test`` are ``test``; ``:app:test`` stays ``:app:test``."""
    bare = task.removeprefix(":")
    return bare if ":" not in bare else task


def partly_excluded(excluded: set[str], task: str) -> bool:
    """``task`` excluded in one project only (``-x :app:test``): another may still run it."""
    return any(e.endswith(":" + task) for e in excluded)


def gradle_dry_run(args: Sequence[str]) -> bool:
    """``-m`` (in a short cluster too: ``-qm``), ``--dry-run`` or ``--task-graph``."""
    return any(w in _GRADLE_DRY_RUN or _short_cluster_has_m(w) for w in args)


def _short_cluster_has_m(word: str) -> bool:
    """``-m`` inside a short-option cluster (``-qm``), up to an option that takes a value
    (``-Dmode=x`` is a property, not a dry run)."""
    if not word.startswith("-") or word.startswith("--"):
        return False
    for char in word[1:]:
        if char == "m":
            return True
        if char in _GRADLE_VALUE_SHORTS or not char.isalpha():
            return False
    return False


def runs_no_task(words: Sequence[str]) -> bool:
    """A command that runs no recipe or task: a Gradle dry run, or ``make_runs_no_recipe``."""
    return (is_gradle(words) and gradle_dry_run(words[1:])) or make_runs_no_recipe(list(words))


def gradle_run(words: list[str]) -> list[str]:
    """``words`` with Gradle's exclusions applied: each ``-x X`` pair goes, and so does a
    named task that an exclusion covers (``check -x check`` -> no task). Not Gradle: as is."""
    if not is_gradle(words):
        return words
    _, excluded = gradle_tasks(words[1:])
    out = [words[0]]
    skip: str | None = None
    for word in words[1:]:
        if skip is not None:
            out += [] if skip == "x" else [word]
            skip = None
        elif word in _GRADLE_EXCLUDE:
            skip = "x"
        elif word.startswith("--exclude-task="):
            continue
        elif word.startswith("-"):
            skip = "v" if word in _GRADLE_VALUED else None
            out.append(word)
        elif word.rsplit(":", 1)[-1] not in excluded and _excluded_name(word) not in excluded:
            out.append(word)
    return out


def gradle_skips_tests(words: Sequence[str]) -> bool:
    """``./gradlew check -x test``: a Gradle run with ``test`` excluded (in any project) and
    no other test task named, so a check reading would rest on tests that do not run."""
    if not is_gradle(words):
        return False
    tasks, excluded = gradle_tasks(words[1:])
    return (any(e.rsplit(":", 1)[-1] == "test" for e in excluded)
            and not any("test" in t.lower() for t in tasks if t not in excluded))


# --- Python script runners ----------------------------------------------------------
# pdm's own commands; any other first word is a script (``pdm cov`` runs ``pdm run cov``).
PDM_COMMANDS = frozenset({
    "add", "build", "cache", "completion", "config", "export", "fix", "import", "info",
    "init", "install", "list", "lock", "new", "outdated", "publish", "py", "python",
    "remove", "search", "self", "show", "sync", "update", "use", "venv"})
# The script word a check reads for a script named like a program (``pdm run pytest`` with a
# ``pytest`` script, ``poe ruff``): its recipe is unread, so neither the program nor the name
# credits it. ``<`` never starts a shell word.
SCRIPT = "<script>"
# Runner options that take the next word (``pdm run -p sub lint``, ``poe -C sub lint``).
_RUNNER_VALUED = frozenset({"-p", "--project", "-C", "--directory", "--root", "-e",
                            "--executor", "--venv"})
_PDM_SCRIPTS: ContextVar[frozenset[str] | None] = ContextVar("pdm_scripts", default=None)


def _after_flags(args: Sequence[str]) -> list[str]:
    """``args`` from the first word that is not an option or an option's value."""
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in _RUNNER_VALUED else 1
    return list(args[i:])


def script_run(words: Sequence[str]) -> tuple[list[str], bool] | None:
    """What ``hatch run``, ``pdm [run]`` or ``poe`` runs, as ``(words, is_script)`` (module
    docstring); None for another command."""
    head = PurePosixPath(words[0]).name if words else ""
    rest = _after_flags(words[1:])
    if head == "poe":
        return rest, True
    if head == "hatch":
        return (_after_flags(rest[1:]), False) if rest[:1] == ["run"] else None
    if head != "pdm" or (rest[:1] and rest[0] in PDM_COMMANDS):
        return None
    scripts = _PDM_SCRIPTS.get()
    if rest[:1] == ["run"]:
        rest = _after_flags(rest[1:])
        return rest, bool(rest) and scripts is not None and rest[0] in scripts
    if scripts is not None and rest[:1] and rest[0] not in scripts:
        return [], True   # pdm pytest with no pytest script: "Command not found", exit 2
    return rest, True


def pdm_scripts(root: Path) -> frozenset[str] | None:
    """The ``[tool.pdm.scripts]`` names of ``root/pyproject.toml`` (``_``, the shared
    options, is none); empty with no such file, None when it cannot be parsed."""
    path = root / "pyproject.toml"
    try:
        if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
            return frozenset()
        data = tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    scripts = data.get("tool", {}).get("pdm", {}).get("scripts", {})
    return frozenset(k for k in scripts if k != "_") if isinstance(scripts, dict) else frozenset()


@contextmanager
def pdm_scripts_of(root: Path) -> Iterator[None]:
    """Read ``root``'s pdm scripts for ``script_run`` inside the block."""
    token = _PDM_SCRIPTS.set(pdm_scripts(root))
    try:
        yield
    finally:
        _PDM_SCRIPTS.reset(token)


P = ParamSpec("P")
R = TypeVar("R")


def reads_pdm_scripts(scan: Callable[P, R]) -> Callable[P, R]:
    """Run ``scan`` (first argument: the repository root) with its pdm scripts read."""
    @functools.wraps(scan)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        root = args[0] if args else kwargs.get("repo_root")
        if not isinstance(root, (Path, str)):
            return scan(*args, **kwargs)
        with pdm_scripts_of(Path(root)):
            return scan(*args, **kwargs)
    return wrapper
