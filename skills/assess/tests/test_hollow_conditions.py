"""Hollow cases: the cheapest committed change that meets a maturity-model condition (#784).

Each case is a config edit or a comment that satisfies one level of the shipped
default maturity model without changing what CI enforces. The test asserts that
the level is not met: a hollow case must not be credited. ``unmet`` and
``unknown`` both pass.

A case the model still credits carries ``xfail(strict=True)``. Its reason names
the issue whose fix makes it unmet, or ``hollow: no fixing issue filed``. When a
fix lands, its case turns into an unexpected pass and fails the suite until the
fixing PR removes the mark. Each fixing PR removes the marks on its own cases.
Only an AssertionError counts as the expected failure, so a renamed row or a
scan that raises fails the suite instead of hiding behind the mark.

Conditions read from git history, execution or the platform API are not cheap
to fake with one edit, so they have no case here.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from _pytest.mark.structures import ParameterSet

from lib.level_signals import load_default_levels
from lib.levels import Group, evaluate
from lib.promissory_markers import scan_promissory_markers
from lib.signals import build_document

_ISSUES = "https://github.com/bjcoombs/ai-native-toolkit/issues/"
_NONE_FILED = "hollow: no fixing issue filed"
# Inherited from a hook or a caller's git, these would point git -C at another repository.
_GIT_LOCATION_VARS = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)


def _workflow(run: str) -> str:
    return f"on: pull_request\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n      - run: {run}\n"


_NOOP_CHECK = {
    "CLAUDE.md": "# Guide\n\nRun `make test` before pushing.\n",
    "Makefile": "test:\n\ttrue\n",
}
_ALLOW = {"permissions": {"allow": ["Bash(make test)"]}}
_JACOCO_POM = (
    "<project><build><plugins><plugin>"
    "<groupId>org.jacoco</groupId><artifactId>jacoco-maven-plugin</artifactId>"
    "<executions><execution><id>check</id><goals><goal>check</goal></goals>"
    "<configuration><rules><rule><limits><limit>"
    "<counter>LINE</counter><value>COVEREDRATIO</value><minimum>0.01</minimum>"
    "</limit></limits></rule></rules></configuration>"
    "</execution></executions></plugin></plugins></build></project>\n"
)


def _case(case_id: str, row: str, level: int, files: dict[str, str], fixed_by: str | None) -> ParameterSet:
    """One hollow case; ``fixed_by`` is the xfail reason, None once the model rejects it."""
    marks = [pytest.mark.xfail(strict=True, raises=AssertionError, reason=fixed_by)] if fixed_by else []
    return pytest.param(row, level, files, id=case_id, marks=marks)


CASES = [
    # Declared, not counted under #744; no issue changes the criterion.
    _case("l0-1-stub-instructions", "layer-0-instructions", 1, {"CLAUDE.md": "# x\n"}, _NONE_FILED),
    _case("l0-2-noop-check", "layer-0-instructions", 2, _NOOP_CHECK, _NONE_FILED),
    _case("l1-1-telemetry-dependency", "layer-1-liveness", 1, {
        "pyproject.toml": "[project]\nname = 'x'\nversion = '0'\ndependencies = ['opentelemetry-api']\n",
    }, _NONE_FILED),
    _case("l3-1-one-noqa", "layer-3-linters", 1, {"app.py": "import os  # noqa: F401\n"}, None),
    _case("l3-1-linter-version", "layer-3-linters", 1,
          {".github/workflows/ci.yml": _workflow("ruff --version")}, None),
    _case("l4-1-codeowners-star", "layer-4-architecture", 1, {
        ".github/CODEOWNERS": "* @x\n", "src/a.py": "x = 1\n",
    }, None),
    # The Layer 4 recovery step is as cheap as `* @x`.
    _case("l4-1-architecture-one-path", "layer-4-architecture", 1, {
        "ARCHITECTURE.md": "# Architecture\n\nThe entry point is `src/a.py`.\n", "src/a.py": "x = 1\n",
    }, _NONE_FILED),
    _case("l5-1-pytest-or-true", "layer-5-ci", 1,
          {".github/workflows/ci.yml": _workflow("pytest || true")}, None),
    _case("l5-1-linter-version", "layer-5-ci", 1,
          {".github/workflows/ci.yml": _workflow("ruff --version")}, None),
    # #919: a check that cannot fail its job, never runs, or sits where GitHub does not run it.
    _case("l3-1-exit-zero", "layer-3-linters", 1,
          {".github/workflows/ci.yml": _workflow("ruff check . --exit-zero")}, None),
    _case("l5-1-semicolon-exit-0", "layer-5-ci", 1,
          {".github/workflows/ci.yml": _workflow("pytest; exit 0")}, None),
    _case("l5-1-if-false", "layer-5-ci", 1, {".github/workflows/ci.yml": (
        "on: pull_request\njobs:\n  t:\n    if: false\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: pytest\n")}, None),
    _case("l5-1-subfolder-workflow", "layer-5-ci", 1,
          {".github/workflows/sub/ci.yml": _workflow("pytest")}, None),
    # Declared, not counted under #744.
    _case("l5-2-named-check-and-lockfile", "layer-5-ci", 2, {
        **_NOOP_CHECK, "uv.lock": "version = 1\n", ".github/workflows/ci.yml": _workflow("make test"),
    }, _NONE_FILED),
    _case("l6-1-fail-under-1", "layer-6-coverage", 1, {
        "pyproject.toml": "[project]\nname = 'x'\nversion = '0'\n\n[tool.coverage.report]\nfail_under = 1\n",
    }, None),
    # The threshold is null, so it should read as unknown.
    _case("l6-1-templated-threshold", "layer-6-coverage", 1,
          {".github/workflows/ci.yml": _workflow("pytest --cov-fail-under=${{ env.COV_MIN }}")}, None),
    _case("l6-1-jacoco-ratio", "layer-6-coverage", 1, {"pom.xml": _JACOCO_POM}, None),
    _case("l7-1-review-config-file", "layer-7-review", 1, {".coderabbit.yaml": "reviews: {}\n"}, _NONE_FILED),
    # Layer 8 has no fixing child of #776.
    _case("l8-1-one-allow-entry", "layer-8-agent-ops", 1,
          {".claude/settings.json": json.dumps(_ALLOW)}, _NONE_FILED),
    _case("l8-2-one-hook-one-routine", "layer-8-agent-ops", 2, {
        ".claude/settings.json": json.dumps(
            {**_ALLOW, "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "true"}]}]}}),
        ".claude/routines/x.md": "# x\n",
    }, _NONE_FILED),
]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   encoding="utf-8", errors="replace", env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull})


def _committed(repo: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=a@example.com", "-c", "user.name=A", "commit", "-qm", "init")
    return repo


def _level_met(repo: Path, row_id: str, level: int) -> bool | None:
    model = load_default_levels()
    criteria = next(r for r in model["rows"] if r["id"] == row_id)["levels"][level - 1]["criteria"]
    scan = scan_promissory_markers(repo)
    # Without ripgrep the marker scan is unavailable, the level reads unknown and
    # the case would XPASS as if its fix had landed: skip it instead.
    if not scan.available and any(c["signal"].startswith("promissory_markers.") for c in criteria):
        pytest.skip(scan.reason)
    scans = dict(build_document(repo)["scans"])
    scans["promissory_markers"] = scan.summary()
    group: Group = {"name": "g", "settings": None, "repos": {"r": scans}}
    row = next(r for r in evaluate(model, group)["rows"] if r["id"] == row_id)
    met: bool | None = row["repos"]["r"]["levels"][level - 1]["met"]
    return met


def test_every_case_names_its_fixing_issue_or_none() -> None:
    assert len(CASES) >= 15
    for case in CASES:
        for mark in case.marks:
            reason = mark.kwargs["reason"]
            assert mark.kwargs["strict"] is True
            assert mark.kwargs.get("raises") is AssertionError, case.id
            assert reason == _NONE_FILED or reason.startswith(_ISSUES), case.id


@pytest.mark.parametrize(("row_id", "level", "files"), CASES)
def test_hollow_fixture_does_not_meet_its_condition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, row_id: str, level: int, files: dict[str, str],
) -> None:
    for name in _GIT_LOCATION_VARS:
        monkeypatch.delenv(name, raising=False)
    repo = _committed(tmp_path / "r", files)
    assert _level_met(repo, row_id, level) is not True
