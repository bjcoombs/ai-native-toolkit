"""Tests for lib/ci_checks.py: does CI run a test or lint command? (issue #698)."""
from __future__ import annotations

from pathlib import Path

import pytest

from lib.ci_checks import check_commands, scan_ci_checks
from lib.shared_tooling import pulls_jobs_from_elsewhere


@pytest.mark.parametrize("text", [
    "run: cd x && uv run pytest",
    "run: FOO=1 make test",
    'run: "pytest"',
    "script:\n  - npm test",
    "run: |\n  uv sync\n  uv run ruff check .",
    "- run: ./scripts/test.sh",
    "run: npm ci && npm run lint",
    "- make lint",
    "run: ruff check .",
    "run: npx eslint src",
    "run: cargo clippy -- -D warnings",
    "run: go vet ./...",
    "run: uv run --frozen mypy src",
])
def test_a_ci_line_that_runs_a_check_counts(text: str) -> None:
    assert check_commands(text) >= 1


@pytest.mark.parametrize("text", [
    "# run pytest",
    "name: Run pytest",
    "test:",
    "needs: test",
    "echo pytest",
    "- npm ci",
    "run: make deploy",
    "- uses: actions/checkout@v4",
    "lint:",
    "name: Lint with ruff",
    "echo lint",
    "run: make build",
])
def test_a_ci_line_that_runs_no_check_does_not_count(text: str) -> None:
    assert check_commands(text) == 0


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def test_no_ci_files_is_false_with_basis_no_ci(tmp_path: Path) -> None:
    block = scan_ci_checks(_write(tmp_path, {"README.md": "x\n"}))
    assert block == {"available": True, "ci_files": 0, "check_commands": 0, "included_files": 0,
                     "test_or_lint_runs": False, "basis": "no_ci"}


def test_a_workflow_running_a_test_is_true(tmp_path: Path) -> None:
    block = scan_ci_checks(_write(tmp_path, {
        ".github/workflows/ci.yml": "jobs:\n  t:\n    steps:\n      - run: pytest -q\n",
        ".github/workflows/deploy.yaml": "jobs:\n  d:\n    steps:\n      - run: make deploy\n",
    }))
    assert (block["ci_files"], block["check_commands"]) == (2, 1)
    assert (block["test_or_lint_runs"], block["basis"]) == (True, "check_command")


def test_ci_with_no_check_is_false(tmp_path: Path) -> None:
    block = scan_ci_checks(_write(tmp_path, {".gitlab-ci.yml": "build:\n  script:\n    - make build\n"}))
    assert (block["test_or_lint_runs"], block["basis"]) == (False, "no_check_command")


@pytest.mark.parametrize("files", [
    {".gitlab-ci.yml": "include:\n  - project: acme/ci\n    file: python.yml\n"},
    {".gitlab-ci.yml": "include: 'ci/jobs.yml'\nstages: [test]\n"},
    {".github/workflows/ci.yml": "jobs:\n  test:\n    uses: acme/ci/.github/workflows/py.yml@v1\n"},
])
def test_jobs_only_from_includes_is_unknown(tmp_path: Path, files: dict[str, str]) -> None:
    block = scan_ci_checks(_write(tmp_path, files))
    assert (block["test_or_lint_runs"], block["basis"]) == (None, "included_jobs")
    assert block["included_files"] == 1


def test_a_local_check_beats_an_include(tmp_path: Path) -> None:
    block = scan_ci_checks(_write(tmp_path, {
        ".gitlab-ci.yml": "include:\n  - component: gitlab.com/acme/c@1\nlint:\n  script:\n    - make lint\n",
    }))
    assert (block["test_or_lint_runs"], block["included_files"]) == (True, 1)


def test_a_local_reusable_workflow_is_not_an_include() -> None:
    text = "jobs:\n  test:\n    uses: ./.github/workflows/tests.yml\n"
    assert pulls_jobs_from_elsewhere(text, gitlab=False) is False
    assert pulls_jobs_from_elsewhere("jobs:\n  t:\n    steps:\n      - uses: actions/checkout@v4\n",
                                     gitlab=False) is False
    assert pulls_jobs_from_elsewhere("test:\n  trigger:\n    include: child.yml\n", gitlab=True) is False
