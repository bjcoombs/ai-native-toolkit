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
- A tool config (coverage.py ``fail_under``, Jest ``coverageThreshold``, Vitest
  ``thresholds``, nyc/c8 ``check-coverage``, JaCoCo, go-test-coverage): an
  enforced CI line must run the tool that reads it. coverage.py: ``pytest`` with
  a ``--cov`` flag (or a pytest ``addopts`` holding one), or ``coverage report``
  / ``xml`` / ``json`` / ``html`` / ``lcov`` (``coverage run`` alone checks no
  threshold). Jest: ``jest --coverage``, or ``jest`` with ``collectCoverage:
  true`` committed; Vitest: ``vitest --coverage``; nyc/c8: the program; JaCoCo:
  ``mvn verify`` / ``install`` / ``deploy`` / ``jacoco:check``, Gradle's
  ``jacocoTestCoverageVerification``; go-test-coverage: the program or its
  action. A threshold flag in a pytest config section (``[pytest]``,
  ``[tool:pytest]``, ``[tool.pytest.ini_options]``) is ``addopts``, run by any
  ``pytest`` line; a ``tox.ini`` ``[testenv]`` ``commands`` line is a task command.
- A task-runner file (``Makefile``, ``tox.ini`` commands, ``package.json``
  scripts, ``justfile``, ``noxfile.py``, ...): CI reaches it only through a
  target, whose recipe this scan does not follow.

Unknown beats wrong. A gate reads null (not false) when the line or tool run
sits in a workflow or job whose triggers are not settled (``unknown_trigger``),
when an enforced CI line runs a check target or script that may run it
(``indirect_run``: ``make test``, ``npm test``, ``tox``, ``mvn test``; and a
Jest or Vitest run with no ``--coverage``, whose config may enable it), when a
CI file pulls jobs this run cannot read (``included_jobs``), when the tree holds
a CI system this scan does not read (``unread_ci_system``, #787), or when the
gate itself is written in such a file or in a composite action.

``enforced_basis`` tokens: ``ci_run`` (true); ``no_gate`` and
``threshold_below_minimum`` (false, as ``threshold_meets_minimum``);
``threshold_not_read`` (null: ``threshold_meets_minimum`` is null); the null
reasons above; ``not_run_in_ci`` (false: CI is read and runs none of the
gates); ``no_ci`` (false: no CI file at all).

Inward-only imports: stdlib and ``lib`` CI readers; never an orchestrator.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeVar

from lib.check_command import may_lint
from lib.ci_checks import UNREAD_CI_FILES
from lib.ci_masking import unmasked_lines
from lib.ci_triggers import Trigger, gitlab_line_triggers, resolve_workflows, workflow_trigger
from lib.command_index import CI_CONFIG_FILES, ci_config_files, ci_line_text, read_config, repo_files
from lib.command_resolver import command_words, extract_commands
from lib.shared_tooling import pulls_jobs_from_elsewhere

EnforcedBasis = Literal[
    "ci_run", "no_gate", "threshold_below_minimum", "threshold_not_read", "unknown_trigger",
    "indirect_run", "included_jobs", "unread_ci_system", "not_run_in_ci", "no_ci"]

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
    "package.json", "tox.ini", ".pre-commit-config.yaml"})
# The null reasons, most specific first.
_NULL_ORDER: tuple[EnforcedBasis, ...] = (
    "unknown_trigger", "indirect_run", "included_jobs", "unread_ci_system")


@dataclass(frozen=True)
class Run:
    """One command a CI file runs: its file, physical line span, words and trigger."""

    file: str
    span: range
    words: tuple[str, ...]
    text: str
    trigger: Trigger


@dataclass(frozen=True)
class CiRuns:
    """The enforced (unmasked) CI commands of a repository, with what is left unread."""

    runs: tuple[Run, ...]
    ci_files: frozenset[str]
    elsewhere: bool
    unread: bool


# Nothing read: the CI is not read when no threshold of at least 50 percent is set.
NO_RUNS = CiRuns((), frozenset(), False, False)


def _command_groups(lines: list[str], unmasked: list[str]) -> list[tuple[range, str]]:
    """``(span, text)`` per command line; a folded block scalar (``run: >-``) is one text
    per paragraph, attributed to its first line."""
    out: list[tuple[range, str]] = []
    i = 0
    while i < len(lines):
        folded = _FOLDED.match(lines[i])
        if folded is None:
            out.append((range(i, i + 1), ci_line_text(unmasked[i])))
            i += 1
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
                text = " ".join(unmasked[n].strip() for n in paragraph if unmasked[n].strip())
                out.append((range(paragraph[0], paragraph[-1] + 1), text))
                paragraph = []
        i = j
    return out


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
    for rel in files:
        text = texts[rel]
        gitlab = rel in CI_CONFIG_FILES
        elsewhere = elsewhere or pulls_jobs_from_elsewhere(text, gitlab=gitlab)
        lines = text.splitlines()
        triggers: list[Trigger] = (gitlab_line_triggers(text) if gitlab
                                   else [workflows.get(rel, "unknown")] * len(lines))
        for span, line_text in _command_groups(lines, unmasked_lines(text)):
            if _GO_TC_ACTION.search(lines[span[0]]):
                runs.append(Run(rel, span, ("go-test-coverage",), line_text, triggers[span[0]]))
                continue
            for words, command in _word_lists(line_text):
                if words:
                    runs.append(Run(rel, span, words, command, triggers[span[0]]))
    return CiRuns(tuple(runs), frozenset(files), elsewhere, bool(UNREAD_CI_FILES & tracked))


def _line(gate: Gate) -> int:
    line = gate["line"]
    return line if isinstance(line, int) else 0


def _names(words: Iterable[str]) -> list[str]:
    return [w.rsplit("/", 1)[-1] for w in words]


def runs_tool(words: tuple[str, ...], family: str, *, pytest_cov: bool = False,
              jest_collects: bool = False) -> bool | None:
    """Does this command run ``family``'s coverage check? None: it runs the tool, but
    whether coverage is on is set elsewhere (a Jest or Vitest config)."""
    names = _names(words)
    flags = set(words)
    if family in ("coverage.py", "pytest"):
        if "pytest" in names or "py.test" in names:
            return (family == "pytest" or pytest_cov
                    or any(w == "--cov" or w.startswith(("--cov=", "--cov-")) for w in words))
        return family == "coverage.py" and any(
            n == "coverage" and i + 1 < len(names) and names[i + 1] in _COVERAGE_REPORTS
            for i, n in enumerate(names))
    if family in ("jest", "vitest"):
        if family not in names:
            return False
        if any(w == "--coverage" or w.startswith("--coverage") for w in words) or jest_collects:
            return True
        return None
    if family == "nyc":
        return "nyc" in names or "c8" in names
    if family == "maven-jacoco":
        return bool({"mvn", "mvnw"} & set(names)) and bool(_MAVEN_PHASES & flags)
    if family == "gradle-jacoco":
        return bool({"gradle", "gradlew"} & set(names)) and any(
            "jacocoTestCoverageVerification" in w for w in words)
    if family == "go-test-coverage":
        return "go-test-coverage" in names
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


def gate_run(gate: Gate, ci: CiRuns, line_gates: LineGates, *, pytest_cov: bool = False,
             jest_collects: bool = False,
             pytest_lines: frozenset[tuple[str, int]] = frozenset()) -> bool | str:
    """True when an enforced CI line runs ``gate``; a null reason token when that cannot be
    told; False when CI is read and does not."""
    file = str(gate["file"])
    family = gate_family(gate, pytest_lines)
    if file in ci.ci_files:
        states: list[bool | None | str] = []
        for run in ci.runs:
            if run.file != file or _line(gate) - 1 not in run.span:
                continue
            carried = any(g["tool"] == gate["tool"] and g["form"] == gate["form"]
                          for g in line_gates(" ".join(run.words)))
            if carried:
                states.append(True if run.trigger == "ordinary"
                              else "unknown_trigger" if run.trigger == "unknown" else False)
        found = _merge(states)
    elif file.rsplit("/", 1)[-1] in _TASK_FILES and family is None:
        found = _indirect(ci)
    elif family is None:
        # A CI system or composite action this module does not read.
        found = "unread_ci_system"
    else:
        found = _merge([_tool_state(run, family, pytest_cov, jest_collects)
                        for run in ci.runs] + [_indirect(ci)])
    if found is False and ci.elsewhere:
        return "included_jobs"
    if found is False and ci.unread:
        return "unread_ci_system"
    return found


def _tool_state(run: Run, family: str, pytest_cov: bool, jest_collects: bool) -> bool | None | str:
    state = runs_tool(run.words, family, pytest_cov=pytest_cov, jest_collects=jest_collects)
    if state is False or run.trigger == "not_ordinary":
        return False
    if run.trigger == "unknown":
        return "unknown_trigger"
    return True if state else "indirect_run"


def _indirect(ci: CiRuns) -> bool | str:
    """``indirect_run`` when an enforced CI line runs a check target or script whose recipe
    this scan does not follow (``unknown_trigger`` when its triggers are not settled)."""
    return _merge(("indirect_run" if run.trigger == "ordinary" else "unknown_trigger")
                  for run in ci.runs if run.trigger != "not_ordinary" and may_lint(run.text))


def enforced_meets_minimum(
        gates: Sequence[G], threshold: bool | None, ci: CiRuns, line_gates: LineGates, *,
        counted: Callable[[G], bool], minimum: float, pytest_cov: bool = False,
        jest_collects: bool = False,
        pytest_lines: frozenset[tuple[str, int]] = frozenset()
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
        if not counted(gate) or (isinstance(percent, (int, float)) and percent < minimum):
            continue
        state = gate_run(gate, ci, line_gates, pytest_cov=pytest_cov, pytest_lines=pytest_lines,
                         jest_collects=jest_collects)
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
