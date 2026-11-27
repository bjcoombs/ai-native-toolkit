"""CI practices that undermine the ``ci_runs_tests`` gate: a skip label and ignored test failures (issue #828).

Two hazards of the default maturity model read this block. Each is a harm, never a practice: a
hazard condition is never counted, and while it is met it holds the ``ci_runs_tests`` Step gate
(``blocks``, ``lib/scoring/level_tally.py``), so CI that can be told not to fail never earns
the Step that trusts it. Nothing is executed; the committed files are read as text, and a CI step
is followed into the repository only through the shared CI-step follower
(``lib.ci.ci_step_follower``: repo-local, never outside the checkout).

- **``skip_labels``**: the number of pull request labels that skip every job of every
  GitHub Actions workflow that runs a check on pull requests. A workflow counts when its ``on:``
  names ``pull_request`` or ``pull_request_target`` and one of its lines runs a test or lint
  command (``lib.ci.ci_checks.check_commands``, after the follower inlines local actions). A job
  is skipped by label ``L`` when its own ``if:`` holds
  ``!contains(github.event.pull_request.labels.*.name, 'L')`` and no ``||`` (which could let it
  run anyway), or when a job it ``needs:`` is skipped by ``L`` and its own ``if:`` holds neither
  ``always()`` nor ``!cancelled()`` (either runs it after a skipped need). An ``if:`` with ``||``,
  ``==`` or ``!=`` is not read as a skip, and labels compare without case, as GitHub's ``contains``
  does. A label
  counts when it skips every check job (a job whose own lines run a test or lint command,
  ``check_jobs``; every job when none is found) of every such workflow; one unskipped check job or
  workflow clears it, so precision comes first. Null with ``skip_labels_reason`` ``no_pr_checks`` when no workflow runs a check on pull
  requests: there is nothing a label could skip that this reader can see.
- **``test_failures_ignored``**: the number of committed files that tell Maven to ignore test
  failures: ``maven.test.failure.ignore`` set to true (``-Dmaven.test.failure.ignore=true``, a
  bare ``-Dmaven.test.failure.ignore``, or a ``<maven.test.failure.ignore>true`` property) or a
  Surefire or Failsafe ``<testFailureIgnore>true``, outside an XML comment, a pom's
  ``<profiles>`` (a profile runs only when activated) and a ``#`` or ``//`` line comment. A file that also fails the run from the
  test reports (a Jenkins ``junit`` step, which marks the build UNSTABLE, or a report action with
  ``fail_on_failure: true``) is not counted: the failures still turn the run red. The files read: the GitHub Actions workflows
  and ``.gitlab-ci.yml``, each Jenkinsfile (a file named ``Jenkinsfile``, ``Jenkinsfile.<x>`` or
  ``<x>.jenkinsfile``), ``.mvn/maven.config``, the poms of the root reactor (``pom.xml`` and its
  ``<modules>`` closure; a pom no module names, such as a test fixture, is never read), and every
  file a CI line leads to through the follower (a committed script, a make recipe, a local
  action or reusable workflow, the poms a Maven command reads). Null with
  ``test_failures_ignored_reason`` ``no_ci_files`` when none of those CI files or a root pom is
  tracked.

``ci_hazard_files`` lists the files behind both counts, sorted, for evidence. Every value is a
count, a reason code or a repo-relative path, so the block survives counts-only output with its
paths. ``scan_ci_hazards(repo_root)`` builds the block (``CiHazardsBlock``). Tests:
``tests/test_ci_hazards.py``.
"""
from __future__ import annotations

import posixpath
import re
from pathlib import Path
from typing import TypedDict

from lib.ci.ci_build_follower import follow_maven
from lib.ci.ci_checks import check_commands
from lib.ci.ci_masking import IndentTree, parse_lines
from lib.ci.ci_matrix import jobs
from lib.ci.ci_step_follower import StepFollower, follow_workflow
from lib.ci.ci_step_paths import local_reader
from lib.ci.ci_triggers import workflow_events
from lib.instructions.command_index import CI_CONFIG_FILES, ci_config_files, read_config, repo_files

NO_PR_CHECKS = "no_pr_checks"
NO_CI_FILES = "no_ci_files"
PR_EVENTS = frozenset({"pull_request", "pull_request_target"})
MAVEN_CONFIG = ".mvn/maven.config"

_SKIP = re.compile(r"""!\s*contains\(\s*github\.event\.pull_request\.labels\.\*\.name\s*,\s*['"]([^'"]+)['"]\s*\)""")
_IGNORE = re.compile(
    r"maven\.test\.failure\.ignore\s*(?:=|>)\s*[\"']?true\b"
    r"|-Dmaven\.test\.failure\.ignore(?![\w.=])"
    r"|<testFailureIgnore>\s*true\s*</testFailureIgnore>", re.IGNORECASE)
_NEEDS_ITEM = re.compile(r"[\w-]+")
# A later step that reads the test reports and fails the run again: a Jenkins junit step (the
# build goes UNSTABLE, a failing status) or a report action with fail_on_failure: true.
_REFAIL = re.compile(r"\bjunit\s*[('\"\[]|\bjunit\s+testResults\b|\bfail_on_failure\s*:\s*['\"]?true\b", re.IGNORECASE)
# A status check that runs a job after a skipped need: always() or !cancelled(). failure() and
# cancelled() alone are false when a need was only skipped, so the job is skipped with it.
_STATUS = re.compile(r"\balways\s*\(|!\s*cancelled\s*\(")
_XML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
# A pom's profiles run only when activated, which a CI command may never do, so they are not read.
_PROFILES = re.compile(r"<profiles>.*?</profiles>", re.DOTALL)
_LINE_COMMENT = re.compile(r"(?:^|\s)(?:#|//).*$", re.MULTILINE)


class CiHazardsBlock(TypedDict):
    available: bool
    skip_labels: int | None  # labels that skip every check job on pull requests
    skip_labels_reason: str | None  # NO_PR_CHECKS when null
    test_failures_ignored: int | None  # files that set Maven to ignore test failures
    test_failures_ignored_reason: str | None  # NO_CI_FILES when null
    ci_hazard_files: list[str]  # the files behind both counts, sorted


def jenkinsfiles(tracked: frozenset[str]) -> list[str]:
    """The tracked Jenkinsfiles: ``Jenkinsfile``, ``Jenkinsfile.<x>`` or ``<x>.jenkinsfile``."""
    return sorted(f for f in tracked if (name := posixpath.basename(f)) == "Jenkinsfile"
                  or name.startswith("Jenkinsfile.") or name.lower().endswith(".jenkinsfile"))


def ignores_test_failures(text: str) -> bool:
    """True when ``text`` sets Maven to ignore test failures (module docstring). An XML comment,
    a pom's ``<profiles>`` and a ``#`` or ``//`` comment to the end of a line are dropped first, so
    a setting that is commented out, or set only in a profile CI may never activate, is not read."""
    return _IGNORE.search(_LINE_COMMENT.sub("", _PROFILES.sub("", _XML_COMMENT.sub("", text)))) is not None


def refails_from_reports(text: str) -> bool:
    """True when ``text`` also fails the run from the test reports (``_REFAIL``), after comments
    are dropped as in ``ignores_test_failures``."""
    return _REFAIL.search(_LINE_COMMENT.sub("", _XML_COMMENT.sub("", text))) is not None


def _if_value(tree: IndentTree, lines: list[str], key: int) -> str:
    return " ".join(lines[j].strip() for j in tree.subtree(key) if j != key) or tree.lines[key].value


def _needs(tree: IndentTree, lines: list[str], key: int) -> list[str]:
    value = tree.lines[key].value
    words = value if value else " ".join(lines[j] for j in tree.subtree(key) if j != key)
    return _NEEDS_ITEM.findall(words.replace("- ", " "))


def job_skip_labels(text: str) -> dict[str, frozenset[str]]:
    """Each job of a GitHub workflow with the pull request labels that skip it (module docstring)."""
    lines = text.splitlines()
    tree = IndentTree(parse_lines(lines))
    own: dict[str, frozenset[str]] = {}
    needs: dict[str, list[str]] = {}
    runs_anyway: set[str] = set()   # its if: uses a status function, so a skipped need does not skip it
    for job in jobs(tree):
        name = tree.lines[job].key
        labels: frozenset[str] = frozenset()
        needs[name] = []
        for k in tree.children(job):
            if tree.lines[k].key == "if":
                value = _if_value(tree, lines, k)
                # || could let it run anyway, and a comparison (== false) can invert the test
                labels = (frozenset() if re.search(r"\|\||==|!=", value)
                          else frozenset(label.lower() for label in _SKIP.findall(value)))
                if _STATUS.search(value):
                    runs_anyway.add(name)
            elif tree.lines[k].key == "needs":
                needs[name] = _needs(tree, lines, k)
        own[name] = labels
    done: dict[str, frozenset[str]] = {}

    def skipped(name: str, path: frozenset[str]) -> frozenset[str]:
        if name not in done:
            inherited = [] if name in runs_anyway else [
                skipped(n, path | {name}) for n in needs.get(name, []) if n in own and n not in path]
            done[name] = own[name].union(*inherited)
        return done[name]

    return {name: skipped(name, frozenset()) for name in own}


def check_jobs(text: str) -> frozenset[str]:
    """The jobs of a GitHub workflow whose own lines run a test or lint command."""
    lines = text.splitlines()
    tree = IndentTree(parse_lines(lines))
    out: set[str] = set()
    for job in jobs(tree):
        span = tree.subtree(job)
        body = "\n".join(["jobs:", *(lines[j] for j in span)])
        if check_commands(body) > 0:
            out.add(tree.lines[job].key)
    return frozenset(out)


def _skip_labels(texts: dict[str, str]) -> tuple[int | None, list[str]]:
    """The labels that skip every check job on pull requests, and the workflows read for it."""
    common: frozenset[str] | None = None
    read: list[str] = []
    for rel, text in sorted(texts.items()):
        events = workflow_events(text) or []
        if not PR_EVENTS & set(events) or check_commands(text) == 0:
            continue
        labels = job_skip_labels(text)
        checking = check_jobs(text)
        if checking:  # a job that runs no check (a paths filter, a notice) cannot clear the label
            labels = {name: found for name, found in labels.items() if name in checking}
        skipping = frozenset.intersection(*labels.values()) if labels else frozenset()
        common = skipping if common is None else common & skipping
        read.append(rel)
    if common is None:
        return None, []
    return len(common), (read if common else [])


def _followed_files(follower: StepFollower, text: str) -> dict[str, str]:
    """Each repository file a CI file's lines lead to, with the text that matters in it."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        for found in follower.step(line):
            if found.kind == "maven":
                for pom in found.paths:
                    out.setdefault(pom, follower.read_local(pom) or "")
            elif found.paths:
                out.setdefault(found.paths[0], "\n".join(found.commands))
    return out


def scan_ci_hazards(repo_root: Path) -> CiHazardsBlock:
    """The ``ci_hazards`` block for the repository at ``repo_root`` (module docstring)."""
    root = repo_root.resolve()
    tracked = repo_files(root)
    reader = local_reader(root, tracked)
    follower = StepFollower(reader, tracked)
    files = ci_config_files(tracked)
    workflows = {rel: follow_workflow(read_config(root, rel), reader).text
                 for rel in files if rel not in CI_CONFIG_FILES}
    texts = {**workflows, **{rel: read_config(root, rel) for rel in files if rel in CI_CONFIG_FILES}}
    texts |= {rel: read_config(root, rel) for rel in jenkinsfiles(tracked)}
    skip, skip_files = _skip_labels(workflows)
    sources = dict(texts)
    if MAVEN_CONFIG in tracked:
        sources[MAVEN_CONFIG] = read_config(root, MAVEN_CONFIG)
    reactor = follow_maven(["mvn"], reader) if "pom.xml" in tracked else None
    for pom in reactor.paths if reactor is not None else ():
        sources.setdefault(pom, reader(pom) or "")
    for text in texts.values():
        for rel, found in _followed_files(follower, text).items():
            sources.setdefault(rel, found)
    ignoring = sorted(rel for rel, text in sources.items()
                      if ignores_test_failures(text) and not refails_from_reports(text))
    measured = bool(texts) or reactor is not None
    return {
        "available": True,
        "skip_labels": skip, "skip_labels_reason": None if skip is not None else NO_PR_CHECKS,
        "test_failures_ignored": len(ignoring) if measured else None,
        "test_failures_ignored_reason": None if measured else NO_CI_FILES,
        "ci_hazard_files": sorted({*skip_files, *ignoring}),
    }
