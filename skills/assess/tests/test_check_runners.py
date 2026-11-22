"""Tests for issue #920: common test runners and wrappers in CI (``lib.check_command``).

Each listed runner reads true at Layer 5 level 1; an unrecognised target of a runner whose
recipe is not read (``make foo``, ``task foo``) reads null (``check_target``), never false.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lib.check_command import check_verdict, is_check_command, is_lint_command, is_lint_run, may_lint
from lib.ci_checks import scan_ci_checks

_ISSUE_RUNNERS = [
    "dotnet test", "swift test", "mix test", "bundle exec rspec", "deno test",
    "vendor/bin/phpunit", "./vendor/bin/phpunit", "ctest --output-on-failure",
    "bazel test //...", "turbo run test", "npx nx affected -t test", "task test",
    "hatch test", "hatch run test", "pnpm exec vitest run", "docker compose run app pytest",
]


@pytest.mark.parametrize("text", _ISSUE_RUNNERS + [
    "docker compose run --rm -e CI=1 app pytest -q", "docker-compose -f ci.yml run app make test",
    "docker compose exec app pytest", "bundle exec rake spec", "nx run app:test",
    "npx nx run-many --target=lint,test", "turbo test", "deno task test", "uv run make test",
    "make -C sub VAR=1 test", "hatch run pytest", "vitest run", "rspec",
])
def test_a_test_runner_or_wrapped_test_is_a_check(text: str) -> None:
    assert check_verdict(text) is True
    assert is_check_command(text)


@pytest.mark.parametrize("text", [
    "make foo", "task foo", "make", "task", "make build", "make deploy", "just foo", "turbo run build",
    "npx nx affected -t build", "hatch run foo", "bundle exec rake", "uv run make foo",
    "docker compose run app", "deno task foo",
])
def test_an_unrecognised_target_reads_null(text: str) -> None:
    """The recipe is not read, so the target may run a check: unknown, never false."""
    assert check_verdict(text) is None
    assert not is_check_command(text)


@pytest.mark.parametrize("text", [
    "dotnet build", "swift build", "mix compile", "bazel build //...", "make install",
    "task deps", "make clean install", "bundle install", "npm ci", "uv sync --extra test",
    "npm run build", "go build ./...", "docker compose build", "docker compose up -d", "npx",
    "pnpm exec", "hatch run",
])
def test_setup_and_build_commands_are_not_checks(text: str) -> None:
    assert check_verdict(text) is False


@pytest.mark.parametrize(("text", "lint"), [
    ("docker compose run app ruff check .", True), ("bundle exec rubocop", True),
    ("hatch run ruff check .", True), ("pnpm exec eslint .", True), ("bundle exec rspec", False),
])
def test_a_wrapper_is_read_by_the_program_it_runs_for_lint(text: str, lint: bool) -> None:
    assert is_lint_command(text) is lint
    assert is_lint_run(text) is lint


@pytest.mark.parametrize(("text", "maybe"), [
    # Direct test programs run tests only: Layer 3 reads no linter in them.
    ("dotnet test", False), ("swift test", False), ("mix test", False), ("deno test", False),
    ("hatch test", False), ("ctest", False), ("vendor/bin/phpunit", False),
    ("bundle exec rspec", False), ("docker compose run app pytest", False),
    ("pnpm exec vitest run", False),
    # A target whose recipe is not read may lint: Layer 3 reads null, as Layer 5 does.
    ("bazel test //...", True), ("turbo run test", True), ("task test", True),
    ("hatch run test", True), ("hatch run foo", True), ("docker compose run app", True),
])
def test_layer_three_reads_the_new_runners(text: str, maybe: bool) -> None:
    assert may_lint(text) is maybe


@pytest.mark.parametrize("text", ["turbo run lint", "npx nx affected -t lint", "task lint",
                                  "nx run app:lint"])
def test_a_monorepo_lint_target_is_a_lint_run(text: str) -> None:
    """Layer 3 and Layer 5 agree: a lint-named target of turbo, nx or task runs the linter."""
    assert is_lint_run(text) is True
    assert is_lint_command(text) is True


def _gha(*runs: str, on: str = "pull_request") -> dict[str, str]:
    steps = "".join(f"      - run: {r}\n" for r in runs)
    return {".github/workflows/ci.yml":
            f"on: {on}\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n{steps}"}


def _scan(tmp_path: Path, files: dict[str, str]) -> tuple[bool | None, str]:
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    block = scan_ci_checks(tmp_path)
    return block["enforced_check_runs"], block["enforced_basis"]


@pytest.mark.parametrize("run", _ISSUE_RUNNERS)
def test_each_listed_runner_reads_true_in_ci(tmp_path: Path, run: str) -> None:
    assert _scan(tmp_path, _gha(run)) == (True, "enforced_check")


@pytest.mark.parametrize("run", ["make foo", "task foo", "make", "hatch run foo",
                                 "docker compose run app"])
def test_an_unrecognised_target_in_ci_reads_check_target(tmp_path: Path, run: str) -> None:
    assert _scan(tmp_path, _gha(run)) == (None, "check_target")


@pytest.mark.parametrize(("files", "expected"), [
    (_gha("make install"), (False, "no_check_command")),
    (_gha("make foo || true"), (False, "no_check_command")),
    (_gha("make foo", on="workflow_dispatch"), (False, "no_check_command")),
    (_gha("pytest || true", "make foo"), (None, "check_target")),
    (_gha("make foo", "dotnet test"), (True, "enforced_check")),
    ({".gitlab-ci.yml": "stages:\n  - make\nbuild:\n  needs:\n    - make\n  script:\n    - echo hi\n"},
     (False, "no_check_command")),
    ({".gitlab-ci.yml": "test:\n  script:\n    - make\n"}, (None, "check_target")),
])
def test_check_target_is_masked_and_triggered_like_any_check(
        tmp_path: Path, files: dict[str, str], expected: tuple[bool | None, str]) -> None:
    """A masked or off-trigger ``make foo`` still reads false; a bare ``- make`` value too."""
    assert _scan(tmp_path, files) == expected
