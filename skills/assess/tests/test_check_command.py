"""Tests for lib/check_command.py: the check and lint command classifiers (issues #514, #698)."""
from __future__ import annotations

import pytest

from lib.check_command import is_check_command, is_check_name, is_lint_command


@pytest.mark.parametrize(("text", "expected"), [
    ("make lint", True),
    ("npm run lint:ci", True),
    ("pnpm lint", True),
    ("ruff check .", True),
    ("npx eslint src", True),
    ("uv run --frozen mypy src", True),
    ("python -m flake8", True),
    ("go vet ./...", True),
    ("cargo clippy -- -D warnings", True),
    ("bash scripts/lint.sh", True),
    ("./scripts/lint", True),
    ("lint", False),
    ("vet", False),
    ("npm install eslint", False),
    ("uv sync --group lint", False),
    ("python -m pip install ruff", False),
    ("make build", False),
    ("echo lint", False),
    ("", False),
])
def test_is_lint_command(text: str, expected: bool) -> None:
    assert is_lint_command(text) is expected


def test_lint_is_not_a_check_name_for_instruction_files() -> None:
    """Instruction-file classification is unchanged: lint targets are not named checks."""
    assert is_check_command("make lint") is False
    assert is_check_name("check:all") and not is_check_name("lint")
