"""How Gradle and the Python script runners read their own arguments (issue #1046).

One parser, shared by the CI-line reader (``lib.check_command``, ``lib.ci_lines``,
``lib.lint_vocabulary``) and the coverage gate (``lib.testing.coverage_ci``, #954). Every
rule was checked against Gradle 9.4, pdm 2.29.7 and poethepoet 0.49.0.

Gradle:

- ``gradle_tasks(args)``: ``(tasks run, tasks excluded)``. A run task is named by its last
  path part (``:app:check`` is ``check``); an option's value is no task (``-p sub``,
  ``-P key``, ``--warning-mode all``: every value-taking option in Gradle 9.4's ``--help``,
  plus ``-b``, ``-c`` and the ``--tests`` filter, #1124). A word after ``-x`` / ``--exclude-task`` (or its ``=`` form) is excluded: a
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
- ``gradle_build_verdict(words)`` (#1124): ``build`` depends on ``check``, which runs the
  tests with a JVM or Android plugin. True where ``gradle_tests_in_check`` read such a
  plugin applied in a tracked ``*.gradle[.kts]`` (under ``reads_pdm_scripts``), else None;
  False for a dry run or ``-x check``.
- ``gradle_task_paths(args)`` keeps each run task's project path, and
  ``gradle_runs_in(args, names, build_file, cwd)`` (#1126) matches it to a build file's
  directory under the Gradle root, the nearest tracked ``settings.gradle[.kts]`` at or above
  the start, else the gate (``:app:x`` is ``android/app/build.gradle`` beside
  ``android/settings.gradle``; ``:x`` the root build file): True on a match, None on a
  mismatch, a root-file gate or ``-b`` / ``-c``. The start (#1162, #1163) is the CI line's
  known working directory ``cwd`` (``""`` the root; ``cd`` / ``working-directory:``, read by
  ``lib.testing.ci_workdir``) joined with ``-p`` / ``--project-dir``. An unqualified task
  runs in the start's project and its subprojects under the same root: a gate elsewhere, or
  any gate with an unreadable ``-p`` (``$DIR``, absolute, above the root), reads None; with
  the start unknown it runs in every project (True).

Script runners (``script_run(words)``): what ``hatch run``, ``pdm [run]`` and ``poe`` run,
as ``(words, is_script)``, or None for another command (``pdm install``):

- ``hatch run X``: ``X`` and the rest; its scripts are not read, so ``X`` is read by name.
- ``pdm run X``: the script ``X`` when ``[tool.pdm.scripts]`` defines one (a script shadows
  a program of the same name), else the program ``X``. A bare ``pdm X`` that is not a pdm
  command runs only a script (pdm exits 2 with "Command not found" otherwise), so with the
  scripts read and no ``X`` among them it runs nothing (``[]``).
- ``poe X``: always the task ``X`` (poe runs no program by name).
- With no ``pyproject.toml`` tracked (``NO_PYPROJECT``), ``pdm run X`` and ``poe X`` read as
  ``SCRIPT``: ``pdm run test`` falls back to the shell's ``test`` and ``poe test`` fails.

The scripts come from every tracked ``pyproject.toml`` under ``reads_pdm_scripts`` (a decorator on
a scan whose first argument is the repository root: ``ci_checks.scan_ci_checks``) or
``pdm_scripts_set`` (``coverage_ci`` and ``coverage_root_config`` set ``CiRuns.pdm_scripts``
where they classify the coverage gate's runs). Outside one (an instruction file) they are not read,
and ``pdm X`` is read as a script by name. Inward-only imports: stdlib,
``lib.command_flags`` and ``lib.command_index``; never ``lib.check_command`` or an orchestrator.
Tests: ``tests/test_runner_args.py``, ``tests/test_gradle_tasks.py``,
``tests/test_gradle_workdir.py``.
"""
from __future__ import annotations

import functools
import posixpath
import re
import tomllib
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from pathlib import Path, PurePosixPath
from typing import ParamSpec, TypeVar

from lib.command_flags import make_runs_no_recipe
from lib.command_index import read_config, repo_files

GRADLE = frozenset({"gradle", "gradlew"})
_GRADLE_DRY_RUN = frozenset({"--dry-run", "--task-graph"})
# Gradle short options that take a value: the rest of a cluster after one is that value.
_GRADLE_VALUE_SHORTS = frozenset("bcDFgIMPpx")
_GRADLE_EXCLUDE = ("-x", "--exclude-task")
# Gradle options that take the next word as their value (``-p sub``, ``--warning-mode all``):
# every one in Gradle 9.4's ``--help``, ``-b`` / ``-c`` (gone from 9.x, still in older
# wrappers) and the ``test`` task's ``--tests`` (#1124).
_GRADLE_VALUED = frozenset({
    "-p", "--project-dir", "-b", "--build-file", "-c", "--settings-file", "-I",
    "--init-script", "-g", "--gradle-user-home", "--include-build", "-D", "--system-prop",
    "-P", "--project-prop", "-F", "--dependency-verification", "-M",
    "--write-verification-metadata", "--console", "--console-unicode", "--warning-mode",
    "--max-workers", "--priority", "--project-cache-dir", "--configuration-cache-problems",
    "--update-locks", "--tests",
})
# Options that replace the build or settings file, so a project path is not read against
# the checkout's own layout (``-c other/settings.gradle :app:check``).
_GRADLE_LAYOUT = frozenset({"-b", "-c", "--build-file", "--settings-file"})
_GRADLE_BUILD_FILES = frozenset({"build.gradle", "build.gradle.kts"})
_GRADLE_SETTINGS = frozenset({"settings.gradle", "settings.gradle.kts"})
# The directories of the tracked settings files (each a Gradle root), set by
# ``gradle_roots_set`` (from the coverage gate's ``CiRuns.gradle_roots``); None outside one.
_GRADLE_ROOTS: ContextVar[frozenset[str] | None] = ContextVar("gradle_roots", default=None)


def is_gradle(words: Sequence[str]) -> bool:
    """``gradle`` or a wrapper (``./gradlew``), by basename."""
    return bool(words) and PurePosixPath(words[0]).name in GRADLE


def gradle_tasks(args: Sequence[str]) -> tuple[list[str], set[str]]:
    """``(tasks run, tasks excluded)`` of a Gradle command's arguments (module docstring)."""
    paths, excluded = _gradle_words(args)
    return [word.rsplit(":", 1)[-1] for word in paths], excluded


def gradle_task_paths(args: Sequence[str]) -> list[str]:
    """The tasks a Gradle command runs as written, project path kept (``:app:check``)."""
    return _gradle_words(args)[0]


def _gradle_words(args: Sequence[str]) -> tuple[list[str], set[str]]:
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
            tasks.append(word)
    return tasks, excluded


def gradle_project(task: str) -> str | None:
    """The project directory a run task names in Gradle's default layout: None for an
    unqualified task (every project), ``""`` for ``:task`` (the root project only), ``a/b``
    for ``:a:b:task`` or ``a:b:task`` (relative to the root, Gradle 9.4)."""
    if ":" not in task:
        return None
    return "/".join(part for part in task.split(":")[:-1] if part)


def gradle_runs_in(args: Sequence[str], names: frozenset[str], build_file: str | None,
                   cwd: str | None = None) -> bool | None:
    """Does a run task named in ``names`` run in ``build_file``'s project (#1126, #1162)?
    True for a project path naming ``build_file``'s directory (``_in_project``), or an
    unqualified one that runs there (``_in_start``); None when only a task in another or
    unmatched project names one (a ``settings.gradle`` ``projectDir`` can move a project, a
    root build file or ``allprojects {}`` can configure others, ``-b`` / ``-c`` replace the
    layout); False when none is named. ``cwd`` is the CI line's working directory (``""``
    the repository root, None unknown). ``build_file`` None: any named task."""
    start, replaced, unread = _gradle_start(args, cwd)
    named = False
    for task in gradle_task_paths(args):
        if task.rsplit(":", 1)[-1] not in names:
            continue
        project = gradle_project(task)
        if build_file is None or (project is None and _in_start(
                posixpath.dirname(build_file), start, unread)):
            return True
        if (project is not None and not replaced and not unread
                and posixpath.basename(build_file) in _GRADLE_BUILD_FILES
                and _in_project(project, posixpath.dirname(build_file), start)):
            return True
        named = True
    return None if named else False


def _gradle_start(args: Sequence[str], cwd: str | None = None
                  ) -> tuple[str | None, bool, bool]:
    """``(the directory Gradle starts in, or None when unknown; whether ``-b`` / ``-c``
    replace the layout; whether ``-p`` is unreadable)``. The start is the ``-p`` /
    ``--project-dir`` directory joined onto ``cwd`` (``-p`` alone when ``cwd`` is unknown),
    else ``cwd``, normalised (``""`` the repository root). A ``-p`` value that is a shell
    expansion, absolute, or above the root is unreadable."""
    start = cwd
    replaced = unread = False
    words = iter(args)
    for word in words:
        head, eq, value = word.partition("=") if word.startswith("--") else (word[:2], "", word[2:])
        if head in _GRADLE_LAYOUT:
            replaced = True
        elif head in ("-p", "--project-dir"):
            value = value if eq or value else next(words, "")
            joined = posixpath.normpath(posixpath.join(cwd or "", value)) if value else ""
            unread = (unread or not value or value.startswith(("$", "/", "~"))
                      or joined.startswith(".."))
            start = None if not value else ("" if joined == "." else joined)
    return start, replaced, unread


def _in_start(gate_dir: str, start: str | None, unread: bool) -> bool:
    """An unqualified task runs in the start directory's project and its subprojects
    (#1162): ``gate_dir`` at or under ``start``, and under the same Gradle root when the
    roots are read (``android/`` with its own ``settings.gradle`` is another build). An
    unknown start keeps the reading before #1162: every project."""
    if unread:
        return False
    if start is None:
        return True
    if start and gate_dir != start and not gate_dir.startswith(start + "/"):
        return False
    roots = _GRADLE_ROOTS.get()
    root = None if roots is None else _nearest(start, roots)
    return root is None or _nearest(gate_dir, roots or frozenset()) == root


def _in_project(project: str, gate_dir: str, start: str | None) -> bool:
    """``:a:b`` is ``a/b`` under the Gradle root: the nearest directory holding a tracked
    ``settings.gradle[.kts]`` at or above the start (``-p`` and the CI line's working
    directory, ``_gradle_start``), else, with the start unknown, above the gate's own
    directory (the gate's build is taken as the one run). With no root read, the gate's
    directory is ``project`` or ends with ``/project`` (under the start, when known)."""
    roots = _GRADLE_ROOTS.get()
    base = gate_dir if start is None else start
    root = None if roots is None else _nearest(base, roots)
    if root is None:
        want = "/".join(p for p in (start, project) if p)
        return gate_dir == want or gate_dir.endswith("/" + want)
    return gate_dir == "/".join(p for p in (root, project) if p)


def _nearest(directory: str, roots: frozenset[str]) -> str | None:
    """``directory`` or its nearest parent in ``roots`` (``""`` is the repository root)."""
    while directory not in roots:
        if not directory:
            return None
        directory = posixpath.dirname(directory)
    return directory


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


# A plugin whose ``check`` runs the tests, applied (not ``apply false``) in a Gradle build
# file: ``plugins { id 'java' }``, ``apply plugin: 'java'``, ``kotlin("jvm")``, a bare
# ``java`` / ``application`` line in a Kotlin DSL ``plugins {}`` block, or Android's.
_JVM_PLUGIN_IDS = (r"java|java-library|application|groovy|scala|java-gradle-plugin|kotlin"
                   r"|kotlin-android|org\.jetbrains\.kotlin\.(?:jvm|android)"
                   r"|com\.android\.(?:application|library|dynamic-feature)")
_JVM_PLUGIN_RE = re.compile(
    rf"""^.*?(?:\bapply\s*\(?\s*plugin\s*[:=]\s*|\bid\s*\(?\s*)["'](?:{_JVM_PLUGIN_IDS})["']"""
    r"""(?!.*\bapply\s*\(?\s*false)"""
    r"""|^.*?\bkotlin\s*\(\s*["'](?:jvm|android)["']\s*\)(?!.*\bapply\s*\(?\s*false)"""
    r"""|^\s*(?:java|`java-library`|application|groovy|scala|`java-gradle-plugin`)\s*$""",
    re.M)
_GRADLE_COMMENT_RE = re.compile(r"/\*.*?\*/|(?<![:\"'])//[^\n]*", re.S)
_GRADLE_TESTS_IN_CHECK: ContextVar[bool] = ContextVar("gradle_tests_in_check", default=False)


def gradle_tests_in_check(root: Path) -> bool:
    """A tracked ``*.gradle`` / ``*.gradle.kts`` file applies a plugin whose ``check`` runs
    the tests (``_JVM_PLUGIN_RE``). False is unknown, never "no tests": a version-catalog
    alias or a convention plugin applies one this does not read."""
    return any(_JVM_PLUGIN_RE.search(_GRADLE_COMMENT_RE.sub("", read_config(root, rel)))
               for rel in sorted(repo_files(root)) if rel.endswith((".gradle", ".gradle.kts")))


def gradle_roots(root: Path) -> frozenset[str]:
    """The directories of the tracked ``settings.gradle[.kts]`` files (``""``: the root)."""
    return frozenset(posixpath.dirname(rel) for rel in repo_files(root)
                     if posixpath.basename(rel) in _GRADLE_SETTINGS)


@contextmanager
def gradle_roots_set(roots: frozenset[str] | None) -> Iterator[None]:
    """Give ``gradle_runs_in`` these Gradle roots (None: not read) inside the block."""
    token = _GRADLE_ROOTS.set(roots)
    try:
        yield
    finally:
        _GRADLE_ROOTS.reset(token)


def gradle_build_verdict(words: Sequence[str]) -> bool | None:
    """Does ``./gradlew build`` run the tests (#1124)? ``build`` depends on ``check`` (the
    base plugin, Gradle 9.4), which runs ``test`` with a JVM or Android plugin: True when
    ``gradle_tests_in_check`` read one, None when it did not or ``check`` is excluded in one
    project only, False for another command, a dry run, no ``build`` task or ``-x check``."""
    if not is_gradle(words) or gradle_dry_run(words[1:]):
        return False
    tasks, excluded = gradle_tasks(words[1:])
    if "build" not in tasks or {"build", "check"} & excluded:
        return False
    return None if partly_excluded(excluded, "check") else (_GRADLE_TESTS_IN_CHECK.get() or None)


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
# The script names when no ``pyproject.toml`` is tracked: ``pdm run test`` falls back to the
# shell's ``test`` and ``poe test`` fails, so a run there reads ``SCRIPT`` (null), never credit.
NO_PYPROJECT: frozenset[str] = frozenset({SCRIPT})
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
    scripts = _PDM_SCRIPTS.get()
    unread = scripts is NO_PYPROJECT   # no project: what runs is not settled (null)
    if head == "poe":
        return ([SCRIPT] if unread and rest else rest), True
    if head == "hatch":
        return (_after_flags(rest[1:]), False) if rest[:1] == ["run"] else None
    if head != "pdm" or (rest[:1] and rest[0] in PDM_COMMANDS):
        return None
    if rest[:1] == ["run"]:
        rest = _after_flags(rest[1:])
        if unread and rest:
            return [SCRIPT], True
        return rest, bool(rest) and scripts is not None and rest[0] in scripts
    if scripts is not None and rest[:1] and rest[0] not in scripts:
        return [], True   # pdm pytest with no pytest script: "Command not found", exit 2
    return rest, True


def pdm_scripts(root: Path) -> frozenset[str] | None:
    """The ``[tool.pdm.scripts]`` names of every tracked ``pyproject.toml`` (``_``, the shared
    options, is none), so a project in a subdirectory (``cd backend && pdm cov``) counts too;
    empty with none, ``NO_PYPROJECT`` when no ``pyproject.toml`` is tracked at all, None when
    one cannot be parsed (unknown, never "no such script")."""
    names: set[str] = set()
    projects = [rel for rel in sorted(repo_files(root)) if PurePosixPath(rel).name == "pyproject.toml"]
    if not projects:
        return NO_PYPROJECT
    for rel in projects:
        text = read_config(root, rel)
        if "pdm" not in text:
            continue
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            return None
        scripts = data.get("tool", {}).get("pdm", {}).get("scripts", {})
        if isinstance(scripts, dict):
            names.update(k for k in scripts if k != "_")
    return frozenset(names)


@contextmanager
def pdm_scripts_set(names: frozenset[str] | None) -> Iterator[None]:
    """Give ``script_run`` these pdm script names (None: not read) inside the block."""
    token = _PDM_SCRIPTS.set(names)
    try:
        yield
    finally:
        _PDM_SCRIPTS.reset(token)


def pdm_scripts_of(root: Path) -> AbstractContextManager[None]:
    """Read ``root``'s pdm scripts for ``script_run`` inside the block."""
    return pdm_scripts_set(pdm_scripts(root))


P = ParamSpec("P")
R = TypeVar("R")


def reads_pdm_scripts(scan: Callable[P, R]) -> Callable[P, R]:
    """Run ``scan`` (first argument: the repository root) with its pdm scripts and
    ``gradle_tests_in_check`` read."""
    @functools.wraps(scan)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        root = args[0] if args else kwargs.get("repo_root")
        if not isinstance(root, (Path, str)):
            return scan(*args, **kwargs)
        token = _GRADLE_TESTS_IN_CHECK.set(gradle_tests_in_check(Path(root)))
        try:
            with pdm_scripts_of(Path(root)):
                return scan(*args, **kwargs)
        finally:
            _GRADLE_TESTS_IN_CHECK.reset(token)
    return wrapper
