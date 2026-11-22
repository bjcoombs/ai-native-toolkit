"""Tests for lib/check_command.py: the check and lint command classifiers (issues #514, #698)."""
from __future__ import annotations

import pytest

from lib.check_command import (
    is_check_command, is_check_name, is_lint_command, is_lint_run, may_lint)
from lib.ci_scripts import script_path


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


@pytest.mark.parametrize(("text", "expected"), [
    ("ruff check .", True), ("uv run ruff check .", True), ("make lint", True),
    ("npm run lint", True), ("pnpm lint", True), ("yarn eslint .", True), ("npx eslint src", True),
    ("task lint", True), ("go vet ./...", True), ("cargo clippy", True),
    ("ruff --version", False), ("mypy -V", False), ("eslint --help", False),
    ("npm test", False), ("npx jest", False), ("yarn test", False), ("npm install eslint", False),
])
def test_is_lint_run(text: str, expected: bool) -> None:
    # Issue #782: a lint call that runs the lint, and not a package-manager test alias.
    assert is_lint_run(text) is expected


def test_is_lint_command_keeps_its_meaning() -> None:
    # test_or_lint_configured and Layer 5 still read is_lint_command unchanged (#782 requirement 3).
    assert is_lint_command("ruff --version") and is_lint_command("npm test")


@pytest.mark.parametrize(("text", "expected"), [
    ("make check", True), ("npm test", True), ("npm run ci", True), ("tox -e lint", True),
    ("nox", True), ("./scripts/test.sh", True), ("task check", True),
    ("pre-commit run --all-files", True), ("uv run pre-commit run", True),
    ("pytest", False), ("uv run pytest -q", False), ("python -m pytest", False),
    ("go test ./...", False), ("cargo test", False), ("bun test", False), ("npx jest", False), ("npx vitest run", False),
    ("npx playwright test", False), ("make build", False), ("pre-commit install", False),
])
def test_may_lint(text: str, expected: bool) -> None:
    assert may_lint(text) is expected


@pytest.mark.parametrize(("text", "lint", "maybe"), [
    ("hatch fmt --check", True, False),
    ("hatch fmt", False, False),            # rewrites the files: no failing check
    ("hatch run ruff check .", True, False),
    ("hatch run lint", False, True),        # a script: like tox -e, its recipe is not read
    ("hatch run lint:all", False, True),
    ("hatch run test:cov", False, True),
    ("hatch -e lint run check", False, True),
    ("hatch run pytest -q", False, False),  # a direct test run
    ("hatch build", False, False),
])
def test_hatch_commands(text: str, lint: bool, maybe: bool) -> None:
    # Issue #782: a Hatch repository that lints in CI must not read a definite level 0.
    assert (is_lint_run(text), may_lint(text)) == (lint, maybe)


@pytest.mark.parametrize("text", [
    "pyright", "uv run pyright src", "basedpyright", "tsc --noEmit", "npx tsc --noEmit",
    "npx vue-tsc --noEmit", "npm run typecheck", "npm run type-check", "pnpm typecheck",
    "yarn type-check", "make typecheck", "make type-check", "just typecheck", "task typecheck",
    "hatch run pyright",
])
def test_a_type_checker_is_a_lint_run(text: str) -> None:
    # Issue #782: the Layer 3 condition is "CI runs a linter or type checker".
    assert is_lint_run(text) is True


def test_type_checkers_leave_is_lint_command_unchanged() -> None:
    # Layer 5 and test_or_lint_configured keep is_lint_command's vocabulary.
    assert not is_lint_command("pyright") and not is_lint_command("tsc --noEmit")
    assert not is_lint_run("tsc --version") and not is_lint_run("npm run build:types")


@pytest.mark.parametrize(("text", "lint", "maybe"), [
    ("pnpm exec eslint .", True, False),
    ("pnpm dlx eslint .", True, False),
    ("yarn dlx eslint .", True, False),
    ("yarn exec tsc --noEmit", True, False),
    ("npm exec -- eslint .", True, False),
    ("bunx eslint .", True, False),
    ("bun x tsc", True, False),
    ("pnpm exec vitest run", False, False),       # a test runner runs tests only
    ("npx playwright test", False, False),
    ("npx prettier --check .", False, True),      # unrecognised: unknown, never false
    ("pnpm dlx some-tool", False, True),
    ("yarn exec custom-check", False, True),
    ("pnpm exec", False, False),
])
def test_package_manager_runner_forms(text: str, lint: bool, maybe: bool) -> None:
    # Issue #782: the program a runner form runs decides the reading, as the npx branch does.
    assert (is_lint_run(text), may_lint(text)) == (lint, maybe)


@pytest.mark.parametrize(("text", "expected"), [
    ("./ci.sh", "ci.sh"),
    ("./scripts/test.sh -q", "scripts/test.sh"),
    ("bash scripts/test.sh", "scripts/test.sh"),
    ("sh ./ci/check.sh", "ci/check.sh"),
    ("bin/test", "bin/test"),
    ("scripts/test.sh", "scripts/test.sh"),
    ("bash -c 'pytest'", None),
    ("bash -ec 'pytest'", None),
    ("bash scripts/test.sh --ci", "scripts/test.sh"),
    ("bash scripts/test.sh --coverage -c x", "scripts/test.sh"),
    ("bash -e scripts/test.sh", "scripts/test.sh"),
    ("bash -o pipefail ./ci.sh", "ci.sh"),
    ("bash +o errexit scripts/test.sh", "scripts/test.sh"),
    ("bash -euo pipefail ./ci.sh", "ci.sh"),
    ("bash --noprofile --norc -eo pipefail ./ci.sh", "ci.sh"),
    ("bash -euo pipefail -c 'pytest'", None),
    ("./bin/golangci-lint run", None),
    ("bin/phpunit", None),
    ("./tools/ruff check .", None),
    ("bash", None),
    ("./gradlew test", None),
    ("./mvnw verify", None),
    ("/opt/ci/test.sh", None),
    ("$HOME/test.sh", None),
    ("~/test.sh", None),
    ("./${DIR}/test.sh", None),
    ("../shared/test.sh", None),
    ("pytest", None),
    ("make test", None),
    ("", None),
])
def test_script_path_names_the_committed_file_a_command_runs(text: str,
                                                             expected: str | None) -> None:
    """#937: the repository file a command runs as a script, or None when it runs none
    (or a path the tree cannot settle)."""
    assert script_path(text) == expected
