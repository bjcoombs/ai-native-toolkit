"""Invented run-context blocks that meet every Step 3 gate of the default model (#1641).

The Steps are cumulative foundations, so a fixture that means "Step 2 held" or "Step 3 held"
needs every gate below: the instruction file with a named check command, a kept claim and
the line budget (``layer-0-instructions`` up to level 2), CI running the tests, review
evidence, a linter in CI and a secrets guard. ``step3_signals()`` returns a fresh copy of those
blocks, with no coverage gate, so Step 4 waits on it; a test overrides the block it is about.
"""
from __future__ import annotations

from typing import Any


def step3_signals() -> dict[str, Any]:
    """Every Step 3 gate met; no coverage gate, so Step 4 waits on it."""
    return {
        "layer_grades": {"instruction_file_present": True},
        "agent_environment": {"available": True, "check": {"named_total": 1}},
        "instruction_claims": {"total": 3, "verified": 3, "failed": 0},
        "nested_instructions": {"available": True, "budget": {"claude_code": {"files": ["CLAUDE.md"], "lines": 40}}},
        "ci_checks": {"enforced_check_runs": True, "enforced_basis": "required_checks", "lint_runs": True},
        "review_evidence": {"present": True},
        "secrets_guard": {"configured": True, "configured_reason": None},
        "coverage_gate": {"enforced_meets_minimum": False, "enforced_basis": "no_gate"},
    }


# Committed files that hold the Step 3 gates a headless run reads beside CI (#1641): an AI review
# step on pull requests (``review``), a lint action (``linters``; a ``uses:`` step, so it adds no
# check line of its own to ``ci_runs_tests``) and a pre-commit secret scan (``secrets_guard``).
STEP3_FILES = {
    ".github/workflows/review.yml": "name: review\non: [pull_request]\njobs:\n  review:\n    runs-on: ubuntu-latest\n"
                                    "    steps:\n      - uses: actions/checkout@v4\n"
                                    "      - uses: anthropics/claude-code-action@v1\n",
    ".github/workflows/lint.yml": "name: lint\non: [pull_request]\njobs:\n  lint:\n    runs-on: ubuntu-latest\n"
                                  "    steps:\n      - uses: actions/checkout@v4\n      - uses: astral-sh/ruff-action@v3\n",
    ".pre-commit-config.yaml": "repos:\n  - repo: https://github.com/gitleaks/gitleaks\n    rev: v8.18.0\n"
                               "    hooks:\n      - id: gitleaks\n",
}
