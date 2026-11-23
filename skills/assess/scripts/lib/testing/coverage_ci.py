"""Does CI run the coverage gate? (issue #921)

``coverage_gate`` reads the thresholds a repository configures. A threshold
gates nothing unless something runs the coverage tool that reads it, so the
Layer 6 level 1 signal (and the Step 4 ``coverage_gate`` gate) reads
``coverage_gate.enforced_meets_minimum``: a threshold of at least 50 percent
*and* an enforced CI line that runs it. Enforced is #775's rule, reused
unchanged: the line survives ``lib.ci_masking.unmasked_lines`` (no ``|| true``,
``continue-on-error``, ``allow_failure`` or ``set +e``) and its workflow or job
runs on ordinary changes (``lib.ci_triggers``). GitHub Actions workflows and
``.gitlab-ci.yml`` are read, as ``ci_checks`` reads them.

How a gate is run, by where it is written:

- A flag on a CI line (``pytest --cov --cov-fail-under=80``, ``coverage report
  --fail-under 80``, ``cargo llvm-cov --fail-under-lines 90``): the line itself
  must be an enforced command. The flag must be its own word of a real command,
  so ``echo 'use --cov-fail-under=90 locally'`` runs nothing; ``sh -c '...'``
  and a quoted ``run: "..."`` value are read through, and a folded block scalar
  (``run: >-``) is read as one command.
- A tool config (the tool must be the command's program, through ``uv run``,
  ``python -m``, ``npx`` and the like, never an argument such as ``pip install
  pytest``; coverage.py ``fail_under``, Jest ``coverageThreshold``, Vitest
  ``thresholds``, nyc/c8 ``check-coverage``, JaCoCo, go-test-coverage): an
  enforced CI line must run the tool that reads it. coverage.py: ``pytest`` with
  a ``--cov`` flag (or a pytest ``addopts`` holding one), or ``coverage report``
  / ``xml`` / ``json`` / ``html`` / ``lcov`` (``coverage run`` alone checks no
  threshold). Jest: ``jest --coverage``, or ``jest`` with ``collectCoverage:
  true`` committed; Vitest: ``vitest --coverage``; nyc/c8: the program; JaCoCo:
  ``mvn verify`` / ``install`` / ``deploy`` / ``jacoco:check``, Gradle's
  ``jacocoTestCoverageVerification`` (by project path, ``:app:...``, only in that
  project's build file, else ``indirect_run``, #1126); go-test-coverage: the program or its
  action. A threshold flag in a pytest config section (``[pytest]``,
  ``[tool:pytest]``, ``[tool.pytest.ini_options]``) is ``addopts``, run by any
  ``pytest`` line with pytest-cov on (``--cov`` there or in ``addopts``); a ``tox.ini`` ``[testenv]`` ``commands`` line is a task command.
- A task-runner file (``Makefile``, ``tox.ini`` commands, ``package.json``
  scripts, ``justfile``, ``noxfile.py``, ...), or a flag outside a pytest section of
  ``pyproject.toml`` or ``setup.cfg`` (a hatch, pdm or poe script, a tox command, #954):
  CI reaches it only through a target or script, whose recipe this scan does not follow
  (``indirect_run`` when CI runs one: ``hatch run``, ``pdm [run]`` and ``poe`` included,
  through ``uv run`` or ``poetry run``).

#954 edge cases. A CI line's gate is credited only to the run whose own flag carries
its threshold (``echo --cov-fail-under=90 && pytest --cov --cov-fail-under=10``
enforces 10), and one gate is read per ``--cov-fail-under`` on a line. ``--no-cov``
turns pytest-cov off wherever it stands, on the line or in ``addopts``
(``--no-cov-on-fail`` does not). A Gradle task named with ``-x`` / ``--exclude-task``
is not run (``check -x jacocoTestCoverageVerification`` reads false, and so does ``-x
test`` with no other test task, which leaves the verification no execution data;
``-x check`` stops a wired ``check`` crediting it; a project path such as ``-x
:app:jacocoTestCoverageVerification`` leaves other projects running and reads null),
and a dry run (``-m``, in a short
cluster such as ``-qm`` too, ``--dry-run``, ``--task-graph``) runs nothing; Gradle's and
pdm's arguments are read by ``lib.runner_args``, shared with the CI-line reader (#1046). A
``[tool.pdm.scripts]`` entry that shadows the program (``pdm run pytest`` with a ``pytest``
script) reads ``indirect_run``, and a bare ``pdm pytest`` with no such script runs nothing. ``pytest -c
FILE`` / ``--config-file FILE`` reads FILE's ``addopts`` as the run's own (null when
FILE was not read), and ``--rootdir`` with no ``--cov`` reads null. Coverage switched on in config (pytest ``addopts``,
Jest ``collectCoverage: true``) is keyed by the config's directory
(``lib.testing.coverage_activation``): it switches on a config gate in that directory or below,
never a sibling package's (an ``addopts`` ``--cov`` in a directory under the gate's, such
as ``tests/pytest.ini``, reads ``indirect_run``: ``pytest tests`` may read it), and a gate on a CI line reads it from the root, with a
package-only ``addopts`` reading ``indirect_run``.

Unknown beats wrong. A gate reads null (not false) when the line or tool run
sits in a workflow or job whose triggers are not settled (``unknown_trigger``),
when an enforced CI line runs a check target or script that may run it
(``indirect_run``: ``make test``, ``npm test``, ``tox``, ``mvn test``; and a
Jest or Vitest run with no ``--coverage``, whose config may enable it), when a
CI file pulls jobs this run cannot read (``included_jobs``), when the tree holds
a CI system this scan does not read (``unread_ci_system``, #787; the paths are
``lib.unread_ci``'s, shared with ``ci_checks``, #928), or when the
gate itself is written in such a file or in a composite action.

``enforced_basis`` tokens: ``ci_run`` (true); ``no_gate`` and
``threshold_below_minimum`` (false, as ``threshold_meets_minimum``);
``threshold_not_read`` (null: ``threshold_meets_minimum`` is null); the null
reasons above; ``status_check_unconfirmed`` (null: a Codecov status target, #882,
which no CI line runs and which gates a merge only when branch protection requires it);
``not_run_in_ci`` (false: CI is read and runs none of the
gates); ``no_ci`` (false: no CI file at all).

Inward-only imports: stdlib and ``lib`` CI readers; never an orchestrator.

Does CI run the coverage gate? (#921, the maintainer decision on the Step 4 gate).
`enforced_meets_minimum(gates, threshold, ci, line_gates, ...)` returns the
`coverage_gate` block's `enforced_meets_minimum` and `enforced_basis`, which Layer 6
level 1 of the default maturity model, and so the Step 4 `coverage_gate` gate, reads.
A false or null `threshold_meets_minimum` stays false or null (`no_gate`,
`threshold_below_minimum`, `threshold_not_read`); a true one needs a counted gate of at
least 50 percent that an enforced CI line runs. `read_ci_runs(repo_root)` reads the
GitHub workflows and `.gitlab-ci.yml` with #775's rules unchanged
(`ci_masking.unmasked_lines`, `ci_triggers.workflow_trigger` / `resolve_workflows` /
`gitlab_line_triggers`), one `Run` per command, a folded `run: >-` paragraph read as one
command (masked as one line, so a `|| true` on a continuation line masks the head, and a
paragraph with a line its step or body masks is dropped) and `sh -c '...'` and a quoted `run:` value read through. A word holding
whitespace (a quoted argument) is dropped, so a flag inside an `echo` string is no flag.
A gate written on a CI line must sit on an enforced command that carries its flag and
whose program can run it (`runs_a_program`: not `echo` / `printf` and the like, not an
install such as `pip install` or `npm install`), so `echo --cov-fail-under=90` runs nothing. A
config gate needs an enforced command whose program is its tool (`program` resolves the
wrappers `check_command._exec_program` and `_info_program` read, `bundle exec` and `docker
compose run` included, and `check_command.is_info_invocation` (#919) makes `pytest
--version` or `pytest --co` no run, so `pip install pytest --cov`
and `npm install jest` run neither; `runs_tool`, `gate_family`):
`pytest --cov` (or a pytest `addopts` holding `--cov`, on one line, in a TOML list or on
INI continuation lines) or `coverage
report|xml|json|html|lcov` for coverage.py `fail_under` (`coverage run` alone checks
nothing), any `pytest` with pytest-cov on (`--cov` on the line or in `addopts`) for a threshold flag inside a pytest config section (`[pytest]` in `pytest.ini` or `tox.ini`, `[tool:pytest]`, `[tool.pytest.ini_options]`; a `tox.ini` `[testenv]` `commands` line is a task command), `jest --coverage` (or `jest` with
`collectCoverage: true` committed), `vitest --coverage`, nyc/c8, `mvn verify|install|deploy`
or `jacoco:check`, Gradle `jacocoTestCoverageVerification` (and `build` / `check` when the
build file wires `check.dependsOn jacocoTestCoverageVerification`; any other Gradle task
reads `indirect_run`, null), the go-test-coverage
program or action. A task-runner gate (`Makefile`, `tox.ini`, `package.json` scripts)
is reached only through a target. pytest-cov is on only with `--cov` or `--cov=<source>` on the line or in `addopts`
(`--cov-report` or `--cov-fail-under` alone turns nothing on), and an inline
`--cov-fail-under` on a `pytest` line needs it too. Jest and Vitest `--coverage` is read
with its value (`--coverage=false` runs no gate; `--coverageDirectory` is not the flag), and
`collectCoverage: true` is read with comments stripped. A coverage action step
(go-test-coverage) is masked like a `run:` line. A CI file read as empty that is not empty
on disk (over the size cap, unreadable) is unread. Null, never false: `unknown_trigger`,
`indirect_run` (an enforced `named_check.may_lint_named` line, #951, such as `make test`, `make ci`, `npm
test`, `tox`, `mvn test`, a target or script named for coverage such as `make coverage`,
`make cov`, `npm run coverage` or `just coverage`, or a Jest / Vitest run with no
`--coverage`),
`included_jobs` (`shared_tooling.pulls_jobs_from_elsewhere`), `unread_ci_system`
(a tracked file at an `unread_ci.py` path, the predicate `ci_checks` shares (#928), or the gate written in one or in a composite
action). Otherwise false: `not_run_in_ci`, or `no_ci` with no CI file. Known limits:
GitHub step and job `if:`, branch filters, matrix values and composite-action
internals are not read, as for `ci_checks`; `mvn test` with a check bound to the test
phase reads null. A run whose own flag overrides the config threshold
(`coverage report --fail-under=10` beside `fail_under = 80`) still credits the config gate.
A root `addopts` or `collectCoverage: true` switches on a package's gate, though a run at
the root has coverage.py read the root config; Gradle's `check` wiring is still read once
per repository; a package `addopts` `--no-cov` does not stop a CI-line gate, and a root
`addopts` `--no-cov` turns off a package gate whose own `addopts` holds `--cov` (pytest
reads the nearest config only); `pytest -p no:cov --cov` (pytest exits 4 on the unknown
flag) still credits. On a scoped run,
any enforced run of the tool anywhere in the repository credits the scope's config gate
(`cd other && pytest --cov` credits `pkg/pyproject.toml`): the working directory a CI line
runs in is not resolved, except for a Gradle run (`Run.cwd`, `lib.testing.ci_workdir`): `-p app`
or `cd app` runs an unqualified task in `app` and its subprojects only, so a gate in `lib`
reads null (#1162), and the Gradle root is the nearest settings file at or above that
directory (#1163, `lib.runner_args.gradle_runs_in`). Imports `lib.ci_checks`, `lib.ci_masking`, `lib.ci_triggers`,
`lib.command_index`, `lib.command_resolver`, `lib.check_command`, `lib.testing.ci_workdir`, `lib.testing.coverage_activation`,
`lib.testing.coverage_codecov` (a Codecov patch or ``auto`` status is skipped, #882) and `lib.shared_tooling`; never `lib.testing.coverage_gate` (which calls it) or an orchestrator.
Tests: `tests/test_coverage_ci.py`, `tests/test_coverage_ci_edges.py` (#954),
`tests/test_gradle_workdir.py` (#1162).
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeVar

from lib.check_command import (
    _DIRECT_TESTS, LINT_PROGRAMS, _exec_program, _info_program, _program, is_info_invocation)
from lib.ci_masking import mask_lines
from lib.testing.coverage_activation import Activation, below, covers
from lib.testing.coverage_codecov import is_patch
from lib.named_check import may_lint_named
from lib.ci_triggers import Trigger, gitlab_line_triggers, resolve_workflows, workflow_trigger
from lib.command_index import CI_CONFIG_FILES, ci_config_files, ci_line_text, read_config, repo_files
from lib.unread_ci import unread_ci_paths
from lib.testing.ci_workdir import exact_cwd
from lib.command_resolver import command_words, extract_commands
from lib.runner_args import (
    GRADLE, SCRIPT, gradle_dry_run, gradle_roots, gradle_roots_set, gradle_runs_in,
    gradle_tasks as gradle_tasks, partly_excluded, pdm_scripts, pdm_scripts_set, script_run)
from lib.shared_tooling import pulls_jobs_from_elsewhere

EnforcedBasis = Literal[
    "ci_run", "no_gate", "threshold_below_minimum", "threshold_not_read", "unknown_trigger",
    "check_may_pass", "indirect_run", "included_jobs", "unread_ci_system", "status_check_unconfirmed",
    "not_run_in_ci", "no_ci"]

# A gate as ``coverage_gate`` reports it; read by key so this module does not import it.
Gate = Mapping[str, object]
G = TypeVar("G", bound=Gate)
# The coverage-flag gates on one command's text, as ``coverage_gate`` reads a CI line.
LineGates = Callable[[str], Sequence[Gate]]

_FOLDED = re.compile(r"^(\s*)(?:-\s+)?(?:(?:run|script)\s*:\s*)?>[-+0-9]*\s*(?:#.*)?$")
_SHELLS = frozenset({"sh", "bash", "zsh", "dash"})
_COVERAGE_REPORTS = frozenset({"report", "xml", "json", "html", "lcov"})
_MAVEN_PHASES = frozenset({"verify", "install", "deploy", "jacoco:check"})
_GO_TC_ACTION = re.compile(r"uses:\s*['\"]?vladopajic/go-test-coverage@", re.IGNORECASE)
# Files whose CLI gates run only through a target or session: a task runner.
_TASK_FILES = frozenset({
    "Makefile", "justfile", "Justfile", "Taskfile.yml", "Taskfile.yaml", "noxfile.py",
    "package.json", "tox.ini", ".pre-commit-config.yaml",
    # #954: outside a pytest section, a flag in these is a hatch / pdm / poe script or a
    # tox / setuptools command, run only through ``hatch run``, ``pdm``, ``poe`` or ``tox``.
    "pyproject.toml", "setup.cfg"})
# The null reasons, most specific first.
_NULL_ORDER: tuple[EnforcedBasis, ...] = (
    "unknown_trigger", "check_may_pass", "indirect_run", "included_jobs", "unread_ci_system",
    "status_check_unconfirmed")


@dataclass(frozen=True)
class Run:
    """One command a CI file runs: its file, physical line span, words and trigger."""

    file: str
    span: range
    words: tuple[str, ...]
    text: str
    trigger: Trigger
    may_pass: bool = False   # a #1008 form: its failure may not fail the job
    cwd: str | None = None   # a Gradle run's working directory, "" the root (#1162)


@dataclass(frozen=True)
class CiRuns:
    """The enforced (unmasked) CI commands of a repository, with what is left unread."""

    runs: tuple[Run, ...]
    ci_files: frozenset[str]
    elsewhere: bool
    unread: bool
    pdm_scripts: frozenset[str] | None = None   # runner_args.pdm_scripts (#1046)
    gradle_roots: frozenset[str] | None = None   # runner_args.gradle_roots (#1126)


# Nothing read: the CI is not read when no threshold of at least 50 percent is set.
NO_RUNS = CiRuns((), frozenset(), False, False)


def _command_groups(lines: list[str], unmasked: list[str], texts: list[str] | None = None,
                    which: int = 0) -> list[tuple[range, str]]:
    """``(span, text)`` per command line; a folded block scalar (``run: >-``) is one text
    per paragraph, and lines joined by a trailing ``\\`` are one text, attributed to
    their first line. ``texts`` and ``which`` read the unsure lines instead
    (``ci_masking.mask_lines``' second value, #1008)."""
    texts = unmasked if texts is None else texts
    out: list[tuple[range, str]] = []
    i = 0
    while i < len(lines):
        folded = _FOLDED.match(lines[i])
        if folded is None:
            # A line ending in ``\\`` continues on the next, as the shell reads it.
            j = i
            while j + 1 < len(lines) and lines[j].rstrip().endswith("\\"):
                j += 1
            parts = [ci_line_text(texts[i])] + [texts[k].strip() for k in range(i + 1, j + 1)]
            text = " ".join(p.removesuffix("\\").strip() for p in parts if p.strip())
            out.append((range(i, j + 1), text))
            i = j + 1
            continue
        indent = len(folded.group(1))
        j = i + 1
        while j < len(lines) and (not lines[j].strip()
                                  or len(lines[j]) - len(lines[j].lstrip()) > indent):
            j += 1
        paragraph: list[int] = []
        for k in [*range(i + 1, j), j]:
            if k < j and lines[k].strip():
                paragraph.append(k)
                continue
            if paragraph:
                out.append((range(paragraph[0], paragraph[-1] + 1),
                            _folded_text(lines, unmasked if which == 0 else texts, paragraph, which)))
                paragraph = []
        i = j
    return out


def _folded_text(lines: list[str], unmasked: list[str], paragraph: list[int], which: int = 0) -> str:
    """One folded paragraph as the shell runs it, masked as one line: a line blanked by
    its step or body (``continue-on-error``, ``set +e``) drops the paragraph, and the
    joined text is masked by #775's same-line rules, so a ``|| true`` on a continuation
    line masks the head it follows. With ``which`` 1, ``unmasked`` is the unsure lines
    (#1008), and a paragraph holding none reads nothing."""
    blank = any(not unmasked[n].strip() for n in paragraph)
    if blank if which == 0 else not any(unmasked[n].strip() for n in paragraph):
        return ""
    joined = " ".join(lines[n].strip() for n in paragraph)
    return ci_line_text(mask_lines(f"run: {joined}")[which][0])


def _word_lists(text: str) -> list[tuple[tuple[str, ...], str]]:
    """The commands of one line as ``(words, text)``. A word holding whitespace (a quoted
    argument) is dropped, so a flag inside an ``echo`` string is no flag."""
    stripped = text.strip()
    if len(stripped) > 1 and stripped[0] == stripped[-1] and stripped[0] in "'\"":
        stripped = stripped[1:-1]
    if not stripped:
        return []
    out: list[tuple[tuple[str, ...], str]] = []
    for command in extract_commands(f"```bash\n{stripped}\n```\n"):
        words = command_words(command.text)
        if (len(words) >= 3 and words[0].rsplit("/", 1)[-1] in _SHELLS
                and words[1].startswith("-") and words[1].endswith("c")):
            out.extend(_word_lists(words[2]))
            continue
        out.append((tuple(w for w in words if not any(c.isspace() for c in w)), command.text))
    return out


def read_ci_runs(repo_root: Path) -> CiRuns:
    """Every unmasked command of the repository's GitHub workflows and ``.gitlab-ci.yml``."""
    root = repo_root.resolve()
    tracked = repo_files(root)
    files = ci_config_files(tracked)
    texts = {rel: read_config(root, rel) for rel in files}
    workflows = resolve_workflows(
        {rel: workflow_trigger(texts[rel]) for rel in files if rel not in CI_CONFIG_FILES}, texts)
    runs: list[Run] = []
    elsewhere = False
    # A CI file read as empty that is not empty on disk (over the size cap, unreadable)
    # was not read: its runs are unknown, never absent.
    unread = bool(unread_ci_paths(tracked)) or any(
        not texts[rel] and _size(root / rel) != 0 for rel in files)
    for rel in files:
        text = texts[rel]
        gitlab = rel in CI_CONFIG_FILES
        elsewhere = elsewhere or pulls_jobs_from_elsewhere(text, gitlab=gitlab)
        lines = text.splitlines()
        triggers: list[Trigger] = (gitlab_line_triggers(text) if gitlab
                                   else [workflows.get(rel, "unknown")] * len(lines))
        unmasked, unsure = mask_lines(text)
        for span, line_text in _command_groups(lines, unmasked):
            # An action step is masked like a run: line (continue-on-error, if: false).
            if _GO_TC_ACTION.search(unmasked[span[0]]):
                runs.append(Run(rel, span, ("go-test-coverage",), line_text, triggers[span[0]]))
                continue
            for words, command in _word_lists(line_text):
                if words:
                    runs.append(Run(rel, span, words, command, triggers[span[0]],
                                    cwd=_gradle_cwd(span, words, lines, gitlab)))
        for span, line_text in _command_groups(lines, unmasked, unsure, 1):
            runs.extend(Run(rel, span, words, command, triggers[span[0]], True,
                            _gradle_cwd(span, words, lines, gitlab))
                        for words, command in _word_lists(line_text) if words)
    return CiRuns(tuple(runs), frozenset(files), elsewhere, unread, pdm_scripts(root),
                  gradle_roots(root))


def _gradle_cwd(span: range, words: tuple[str, ...], lines: list[str], gitlab: bool
                ) -> str | None:
    """A Gradle run's working directory (``lib.testing.ci_workdir``, #1162), read for
    Gradle only; None: unresolved, or another program."""
    if program(words)[0] not in GRADLE:
        return None
    cwd = exact_cwd(span, words, lines, gitlab)
    return None if cwd is None else "" if not cwd.parts else cwd.as_posix()


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return -1


def _line(gate: Gate) -> int:
    line = gate["line"]
    return line if isinstance(line, int) else 0


# Package managers that run a project binary by name (``yarn jest``, ``pnpm vitest``).
_BIN_RUNNERS = frozenset({"yarn", "pnpm", "bun"})
_BIN_TOOLS = frozenset({"jest", "vitest", "nyc", "c8"})


def program(words: tuple[str, ...]) -> tuple[str, list[str]]:
    """``(program name, its arguments)`` for one command, through the wrappers
    ``check_command`` reads (``_exec_program``, then ``_info_program``: ``uv run``,
    ``poetry run``, ``uvx``, ``npx``, ``python -m``, ``bundle exec``, ``docker compose
    run``, ``pnpm exec``, ``yarn dlx``) and ``yarn jest`` / ``pnpm vitest``. The name is the
    basename (``./mvnw`` is ``mvnw``). ``pdm [run]`` and ``poe`` are read through too
    (#954, #1046: ``pdm run pytest --cov`` runs pytest; ``pdm run cov`` names the script
    ``cov``, a program no gate family matches, and a script that shadows ``pytest`` is
    ``runner_args.SCRIPT``). ``pip install pytest`` names ``pip``, and ``echo pytest --cov``
    names ``echo``: a tool named as an argument is not run."""
    rest = _unwrapped(words)
    if not rest or script_run(rest) == ([], True):   # pdm pytest with no such script: exit 2
        return "", []
    return rest[0].rsplit("/", 1)[-1], rest[1:]


def _unwrapped(words: tuple[str, ...]) -> list[str]:
    """``words`` through ``check_command``'s wrappers and ``yarn jest`` / ``pnpm vitest``."""
    if not words:
        return []
    run = _exec_program(list(words))
    rest = _info_program(run if run is not None else list(words))
    if rest and rest[0] in _BIN_RUNNERS and len(rest) > 1 and rest[1] in _BIN_TOOLS:
        rest = rest[1:]
    return rest


# Programs that print, set or install, and so run no gate their arguments name.
_NOT_RUNS = frozenset({"echo", "printf", "cat", "true", ":", "export", "set", "test", "["})
_INSTALLERS = frozenset({"pip", "pip3", "pipx", "npm", "yarn", "pnpm", "bun", "gem", "cargo",
                         "go", "brew", "apt", "apt-get", "uv", "poetry"})
_INSTALL_VERBS = frozenset({"install", "add", "i", "ci"})


def runs_a_program(words: tuple[str, ...]) -> bool:
    """A command whose program can carry a threshold flag: not ``echo`` / ``printf`` and
    the like, and not an install (``pip install``, ``npm install``, ``uv pip install``)."""
    name, args = program(words)
    if not name or name in _NOT_RUNS or is_info_invocation(" ".join(words)):
        return False
    positionals = [a for a in args if not a.startswith("-")]
    if name in _INSTALLERS and (positionals[:1] == ["pip"] or bool(
            set(positionals[:1]) & _INSTALL_VERBS)):
        return False
    return True


def cov_enabled(args: Iterable[str]) -> bool:
    """pytest-cov is on: ``--cov`` or ``--cov=<source>``. ``--cov-report`` or
    ``--cov-fail-under`` alone does not turn it on."""
    return any(w == "--cov" or w.startswith("--cov=") for w in args)


def cov_disabled(args: Iterable[str]) -> bool:
    """``--no-cov`` turns pytest-cov off wherever it stands (``--no-cov-on-fail`` only
    skips the report on a failure)."""
    return "--no-cov" in args


_VERIFY = "jacocoTestCoverageVerification"


def gradle_runs_nothing(words: tuple[str, ...]) -> bool:
    """A Gradle dry run (``-m``, in a short cluster too: ``-qm``; ``--dry-run``,
    ``--task-graph``) executes no task (``lib.runner_args.gradle_dry_run``)."""
    name, args = program(words)
    return name in GRADLE and gradle_dry_run(args)


def gradle_skips_verification(words: tuple[str, ...]) -> bool:
    """A Gradle command that runs no JaCoCo verification: a dry run, or one that
    excludes the verification task with ``-x`` (Gradle skips an excluded task even when
    another task depends on it), or excludes ``test`` with no other test task named (no
    execution data, so the verification's ``onlyIf`` skips it; Gradle 9.4). ``-x check``
    only stops ``check`` crediting it; ``-x jacocoTestReport`` leaves it running. A
    project-path exclusion (``-x :app:jacocoTestCoverageVerification``) leaves the other
    projects' verification running, so it does not count here."""
    name, args = program(words)
    if name not in GRADLE:
        return False
    tasks, excluded = gradle_tasks(args)
    no_tests = "test" in excluded and not any("test" in t.lower() for t in tasks
                                              if t != _VERIFY)
    return gradle_runs_nothing(words) or _VERIFY in excluded or no_tests


def coverage_flag(args: Iterable[str]) -> bool | None:
    """Jest / Vitest ``--coverage`` as given: True for ``--coverage``, ``--coverage=true``
    or ``--coverage.enabled[=true]``, False for ``=false``, None when absent
    (``--coverageDirectory`` and ``--coverage.reporter`` are not the flag)."""
    for word in args:
        head, _, value = word.partition("=")
        if head in ("--coverage", "--coverage.enabled"):
            return value.lower() not in ("false", "0")
    return None


def _config_arg(args: Sequence[str]) -> tuple[str | None, bool]:
    """``(the -c / --config-file path, whether --rootdir is given)`` of a pytest command."""
    path: str | None = None
    rootdir = False
    takes: str | None = None
    for word in args:
        if takes is not None:
            path = word if takes == "c" else path
            takes = None
        elif word in ("-c", "--config-file", "--rootdir"):
            takes = "r" if word == "--rootdir" else "c"
            rootdir = rootdir or word == "--rootdir"
        elif word.startswith("--config-file="):
            path = word.partition("=")[2]
        elif word.startswith("--rootdir="):
            rootdir = True
        elif word.startswith("-c") and not word.startswith("--") and len(word) > 2:
            path = word[2:]
    return path, rootdir


def pytest_on(args: Sequence[str], on: bool, off: bool,
              activation: Activation = Activation()) -> bool | None:
    """Is pytest-cov on for this ``pytest`` command? ``on`` / ``off`` are the directory
    ``addopts`` that apply. ``-c FILE`` / ``--config-file FILE`` replaces them with FILE's
    own ``addopts`` (#954: ``pytest -c ci/pytest.ini`` with ``--cov`` there enforces the
    root ``fail_under``, which coverage.py reads from the run directory); a FILE not read
    is None (unsure), as is ``--rootdir`` with no ``--cov`` (the config pytest finds then is
    not resolved). ``--no-cov`` anywhere is off."""
    if cov_disabled(args):
        return False
    path, rootdir = _config_arg(args)
    if path is not None:
        read, state = activation.config(path.removeprefix("./"))
        if read and state is None:
            return False
        if cov_enabled(args) or state is True:
            return True
        return False if read else None
    if off:
        return False
    if on or cov_enabled(args):
        return True
    return None if rootdir else False


def runs_tool(words: tuple[str, ...], family: str, *, pytest_cov: bool = False,
              jest_collects: bool = False, gradle_check: bool = False,
              pytest_off: bool = False, activation: Activation = Activation(),
              pytest_maybe: bool = False, gate_file: str | None = None,
              cwd: str | None = None) -> bool | None:
    """Does this command run ``family``'s coverage check? The tool must be the program
    the command runs (``program``), never an argument. None: it runs the tool, but
    whether coverage is on is set elsewhere (a Jest or Vitest config), or a Gradle task
    may run the verification (``build`` / ``check`` are met when the build file wires
    ``check`` to ``jacocoTestCoverageVerification``, ``gradle_check``). A Gradle task
    named by project path (``:app:check``) credits only ``gate_file``'s project (#1126); an
    unqualified one, with ``-p`` or a known ``cwd``, only a project at or under it (#1162)."""
    if is_info_invocation(" ".join(words)):   # pytest --version runs no gate (#919)
        return False
    name, args = program(words)
    if family in ("coverage.py", "pytest"):
        if name in ("pytest", "py.test"):
            # Either family gates only with pytest-cov on: ``--cov`` on the line or in
            # ``addopts``. An ``addopts`` threshold with no ``--cov`` anywhere checks nothing,
            # and ``--no-cov`` turns it off (#954).
            state = pytest_on(args, pytest_cov, pytest_off, activation)
            if state is False and pytest_maybe and pytest_on(args, True, pytest_off, activation):
                return None   # an addopts --cov below the gate's directory may apply
            return state
        return (family == "coverage.py" and name == "coverage"
                and bool(args) and args[0] in _COVERAGE_REPORTS)
    if family in ("jest", "vitest"):
        if name != family:
            return False
        flag = coverage_flag(args)
        if flag is not None:
            return flag
        return True if family == "jest" and jest_collects else None
    if family == "nyc":
        return name in ("nyc", "c8")
    if family == "maven-jacoco":
        return name in ("mvn", "mvnw") and bool(_MAVEN_PHASES & set(args))
    if family == "gradle-jacoco":
        if name not in GRADLE or gradle_skips_verification(words):
            return False   # #954: a dry run, or -x jacocoTestCoverageVerification
        _, excluded = gradle_tasks(args)
        # A project-path exclusion may or may not be the gate's project: unsure, never false.
        partly = any(partly_excluded(excluded, t) for t in (_VERIFY, "test", "check"))
        if gradle_runs_in(args, frozenset({_VERIFY}), gate_file, cwd):
            return None if partly else True
        if (gradle_check and gradle_runs_in(args, frozenset({"build", "check"}), gate_file, cwd)
                and "check" not in excluded):
            return None if partly else True
        return None
    if family == "go-test-coverage":
        return name == "go-test-coverage"
    return False


def gate_family(gate: Gate, pytest_lines: frozenset[tuple[str, int]] = frozenset()
                ) -> str | None:
    """The tool run a config gate needs, or None for a gate written as a command line.
    ``pytest_lines`` are the ``(file, line)`` pairs inside a pytest config section
    (``[pytest]``, ``[tool:pytest]``, ``[tool.pytest.ini_options]``): a flag there is
    ``addopts``, which any ``pytest`` run applies, not a ``tox.ini`` ``commands`` line."""
    tool, file = str(gate["tool"]), str(gate["file"]).rsplit("/", 1)[-1]
    if tool == "coverage.py" and str(gate["form"]).endswith("fail_under"):
        return "coverage.py"
    if tool == "pytest-cov" and (str(gate["file"]), _line(gate)) in pytest_lines:
        return "pytest"
    if tool in ("jest", "vitest"):
        return tool
    if tool in ("nyc", "c8") and file != "package.json":
        return "nyc"
    if tool == "nyc/c8":
        return "nyc"
    if tool == "jacoco":
        return "maven-jacoco" if file == "pom.xml" else "gradle-jacoco"
    if tool == "go-test-coverage":
        return "go-test-coverage"
    return None


def _merge(states: Iterable[bool | None | str]) -> bool | str:
    """True if any state is True, else the first null reason, else False."""
    reasons: list[str] = []
    for state in states:
        if state is True:
            return True
        if isinstance(state, str):
            reasons.append(state)
    return min(reasons, key=_NULL_ORDER.index) if reasons else False


def gate_run(gate: Gate, ci: CiRuns, line_gates: LineGates,
             activation: Activation = Activation()) -> bool | str:
    """True when an enforced CI line runs ``gate``; a null reason token when that cannot be
    told; False when CI is read and does not. ``activation`` switches coverage on for a
    config gate only from the gate's directory or one above it (#954)."""
    if gate["tool"] == "codecov":   # #882: a server-side status; whether it is required is not read
        return "status_check_unconfirmed"
    file = str(gate["file"])
    family = gate_family(gate, activation.pytest_lines)
    if file in ci.ci_files:
        found = _line_gate_run(gate, ci, line_gates, activation)
    elif file.rsplit("/", 1)[-1] in _TASK_FILES and family is None:
        found = _indirect(ci)
    elif family is None:
        # A CI system or composite action this module does not read.
        found = "unread_ci_system"
    else:
        pytest_cov = covers(activation.pytest_cov, file)
        jest_collects = covers(activation.jest_collects, file)
        off = covers(activation.pytest_off, file)
        maybe = below(activation.pytest_cov, file)
        found = _merge([_tool_state(run, family, pytest_cov, jest_collects,
                                    activation.gradle_check, off, activation, maybe, file)
                        for run in ci.runs] + [_indirect(ci, family)])
    if found is False and ci.elsewhere:
        return "included_jobs"
    if found is False and ci.unread:
        return "unread_ci_system"
    return found


def _line_gate_run(gate: Gate, ci: CiRuns, line_gates: LineGates,
                   activation: Activation) -> bool | str:
    """A gate written on a CI line: an enforced run there must carry the flag with the
    gate's own threshold (``echo --cov-fail-under=90 && pytest --cov --cov-fail-under=10``
    enforces 10, never 90, #954). A root ``addopts`` ``--cov`` switches a bare ``pytest``
    run on; one only in a package's ``addopts`` reads ``indirect_run``, because the
    directory the CI line runs in is not resolved. A root ``addopts`` ``--no-cov`` turns
    every ``pytest`` run off."""
    cov_dirs = activation.pytest_cov
    off = "" in activation.pytest_off
    on = "" in cov_dirs
    states: list[bool | None | str] = []
    for run in ci.runs:
        if run.file != gate["file"] or _line(gate) - 1 not in run.span:
            continue
        if not runs_a_program(run.words) or not any(
                g["tool"] == gate["tool"] and g["form"] == gate["form"]
                and g["threshold"] == gate["threshold"]
                for g in line_gates(" ".join(run.words))):
            continue
        state = _activates(gate, run.words, on, off, activation)
        if state is False and cov_dirs and _activates(gate, run.words, True, off, activation):
            state = None   # only a package's addopts --cov: the run directory is not resolved
        if state is True:
            states.append(("check_may_pass" if run.may_pass else True) if run.trigger == "ordinary"
                          else "unknown_trigger" if run.trigger == "unknown" else False)
        elif state is None and run.trigger != "not_ordinary":
            states.append("indirect_run" if run.trigger == "ordinary" else "unknown_trigger")
    return _merge(states)


def _activates(gate: Gate, words: tuple[str, ...], on: bool, off: bool,
               activation: Activation) -> bool | None:
    """A ``--cov-fail-under`` on a ``pytest`` line gates only with pytest-cov on
    (``pytest_on``); through a wrapper (``hatch run cov --``) the script may set it, so
    the flag is taken as given. None: unsure."""
    name, args = program(words)
    if name == SCRIPT:   # pdm run pytest with a pytest script: the script runs (#1046)
        return None
    if gate["tool"] != "pytest-cov" or name not in ("pytest", "py.test"):
        return True
    return pytest_on(args, on, off, activation)


def _tool_state(run: Run, family: str, pytest_cov: bool, jest_collects: bool,
                gradle_check: bool = False, pytest_off: bool = False,
                activation: Activation = Activation(), maybe: bool = False,
                gate_file: str | None = None) -> bool | None | str:
    state = runs_tool(run.words, family, pytest_cov=pytest_cov, jest_collects=jest_collects,
                      gradle_check=gradle_check, pytest_off=pytest_off, activation=activation,
                      pytest_maybe=maybe, gate_file=gate_file, cwd=run.cwd)
    if state is False or run.trigger == "not_ordinary":
        return False
    if run.trigger == "unknown":
        return "unknown_trigger"
    return ("check_may_pass" if run.may_pass else True) if state else "indirect_run"


def _indirect(ci: CiRuns, family: str | None = None) -> bool | str:
    """``indirect_run`` when an enforced CI line runs a check target or script whose recipe
    this scan does not follow (``unknown_trigger`` when its triggers are not settled). A
    Gradle dry run runs nothing, and for a JaCoCo Gradle gate neither does a run that
    excludes the verification (#954)."""
    return _merge(("indirect_run" if run.trigger == "ordinary" else "unknown_trigger")
                  for run in ci.runs if run.trigger != "not_ordinary"
                  and (may_lint_named(run.text) or coverage_target(run.words))
                  and not gradle_runs_nothing(run.words)
                  and not (family == "gradle-jacoco" and gradle_skips_verification(run.words)))


_TARGET_RUNNERS = frozenset({"make", "gmake", "just", "task", "rake", "invoke", "nox", "tox"})
_SCRIPT_RUNNERS = frozenset({"npm", "yarn", "pnpm", "bun"})


def coverage_target(words: tuple[str, ...]) -> bool:
    """A runner target or package script named for coverage (``make coverage``, ``make
    cov``, ``npm run coverage``, ``just coverage``), or any ``pdm [run]`` script or ``poe``
    task (#954, as ``may_lint`` reads a ``hatch run`` script): it may run the gate. A
    test or lint program run through ``pdm run`` (``pdm run pytest``) is no script, unless
    a ``[tool.pdm.scripts]`` entry of that name shadows it (#1046)."""
    rest = _program(list(words)) if words else []   # uv run poe cov: the runner is poe
    run = script_run(rest) if rest and rest[0].rsplit("/", 1)[-1] in ("pdm", "poe") else None
    if run is not None:   # pdm [run] <script>, poe <task>: the recipe is not read
        return bool(run[0]) and (run[1] or run[0][0] not in _DIRECT_TESTS | LINT_PROGRAMS)
    rest = _unwrapped(words)
    name = rest[0].rsplit("/", 1)[-1] if rest else ""
    positionals = [w for w in rest[1:] if not w.startswith("-")]
    if name in _SCRIPT_RUNNERS and positionals[:1] == ["run"]:
        positionals = positionals[1:]
    elif name not in _TARGET_RUNNERS | _SCRIPT_RUNNERS:
        return False
    return any("cov" in w.lower() for w in positionals[:1 if name in _SCRIPT_RUNNERS else None])


def enforced_meets_minimum(
        gates: Sequence[G], threshold: bool | None, ci: CiRuns, line_gates: LineGates, *,
        counted: Callable[[G], bool], minimum: float, activation: Activation = Activation()
        ) -> tuple[bool | None, EnforcedBasis]:
    """``(enforced_meets_minimum, enforced_basis)``: ``threshold`` is
    ``threshold_meets_minimum``. A false or null threshold stays false or null; a true one
    is met only when CI runs a counted gate of at least ``minimum`` percent."""
    if threshold is None:
        return None, "threshold_not_read"
    if threshold is False:
        return False, ("threshold_below_minimum" if any(counted(g) for g in gates)
                       else "no_gate")
    reasons: list[str] = []
    for gate in gates:
        percent = gate["threshold_percent"]
        # #882: a Codecov patch or ``auto`` status holds no floor of 50, so it never nulls a known false
        unfloored = is_patch(gate) or (gate["tool"] == "codecov" and percent is None)
        if not counted(gate) or unfloored or (isinstance(percent, (int, float)) and percent < minimum):
            continue
        with pdm_scripts_set(ci.pdm_scripts), gradle_roots_set(ci.gradle_roots):   # #1046, #1126
            state = gate_run(gate, ci, line_gates, activation)
        if state is True and percent is not None:
            return True, "ci_run"
        if isinstance(state, str):
            reasons.append(state)
        elif state is True:
            reasons.append("threshold_not_read")
    if reasons:
        order: tuple[str, ...] = (*_NULL_ORDER, "threshold_not_read")
        return None, _basis(min(reasons, key=order.index))
    return False, "not_run_in_ci" if ci.ci_files or ci.unread else "no_ci"


def _basis(token: str) -> EnforcedBasis:
    for known in (*_NULL_ORDER, "threshold_not_read"):
        if token == known:
            return known
    return "threshold_not_read"
