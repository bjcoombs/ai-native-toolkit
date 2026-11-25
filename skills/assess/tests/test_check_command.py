"""Tests for lib/ci/check_command.py: the check and lint command classifiers (issues #514, #698)."""
from __future__ import annotations

import pytest

from lib.ci.check_command import (
    check_verdict, is_check_command, is_check_name, is_lint_command, is_lint_run, may_lint)
from lib.ci.ci_scripts import script_path
from lib.command_flags import make_args, shell_args, shell_string
from lib.instructions.command_resolver import command_words


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
    ("make check", True), ("npm test", True), ("npm run check", True), ("tox -e lint", True),
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
    ("hatch run lint", True, True),         # #955: the name trust of make lint
    ("hatch run lint:all", True, True),     # the lint environment's all script
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


# --- #955: shell, make and hatch parsing --------------------------------------------------


@pytest.mark.parametrize(("text", "expected"), [
    # the issue's table
    ("sh -e ./ci/run.sh", None), ("bash -e scripts/test.sh", True),   # -e takes no value
    ('bash -c "make test"', True),                                      # the -c string is read
    ("bash --norc ./ci.sh", None), ("bash --norc ./test.sh", True),  # --norc is not -c
    ("make -j 4 build", False), ("make -j 4 test", True),            # -j takes the number
    # the rest of each rule
    ("bash -o pipefail scripts/test.sh", True), ("bash -eo pipefail scripts/test.sh", True),
    ("bash --rcfile x ./test.sh", True), ("bash -lc 'pytest -q'", True), ("sh -c 'make build'", False),
    ('bash -c "echo hi"', False), ("bash -c 'foo'", None),   # an unknown program, as uv run foo ('bash -c "make deps && make test"', None),
    ("bash -c 'pytest || true'", None), ('bash -c "pytest > out.log"', None),
    ('bash -c "$(cat cmd)"', None), ("bash -c 'pytest `which x`'", None),
    ('bash -c "unterminated', None), ("bash -c ''", None),
    ("make -j build", False), ("make -l 2.5 test", True), ("make -o foo build", False),
    ("make -W foo test", True), ("make -j 4", None),
])
def test_shell_and_make_options_are_read_per_program(text: str, expected: bool | None) -> None:
    assert check_verdict(text) is expected


@pytest.mark.parametrize(("text", "path"), [
    ("bash --rcfile x ./test.sh", "test.sh"), ("bash --init-file x scripts/test.sh", "scripts/test.sh"),
    ("bash -e ./ci.sh", "ci.sh"), ("bash --norc ./ci.sh", "ci.sh"),
])
def test_script_path_skips_a_shell_options_value(text: str, path: str) -> None:
    assert script_path(text) == path


def test_the_shell_and_make_helpers() -> None:
    assert shell_args(command_words("bash -e -o pipefail x.sh a")) == ["x.sh", "a"]
    assert shell_args(command_words("bash -ec 'make test'")) is None
    assert shell_args(command_words("bash -- x.sh")) == ["x.sh"]
    assert shell_string(command_words("bash -c 'make test' name")) == ["make", "test"]
    assert make_args(["-j", "4", "-l", "2", "-o", "f", "-j", "build"]) == ["-j", "build"]


@pytest.mark.parametrize("text", ["hatch run lint", "hatch run lint:style", "hatch run typecheck"])
def test_hatch_run_has_the_name_trust_of_make(text: str) -> None:
    """``hatch run lint`` reads as ``make lint`` does: a lint command and a lint run (#955)."""
    assert is_lint_command(text) or text.endswith("typecheck")
    assert is_lint_run(text)


@pytest.mark.parametrize("text", ["hatch run foo", "hatch run build", "hatch run test:cov"])
def test_other_hatch_scripts_are_not_lint_runs(text: str) -> None:
    assert not is_lint_run(text)


@pytest.mark.parametrize(("text", "expected"), [
    ("bash -c 'ruff check .'", True), ('bash -c "make lint"', True), ("bash -c 'make build'", False),
])
def test_a_shell_string_is_read_for_lint_too(text: str, expected: bool) -> None:
    assert is_lint_run(text) is expected


@pytest.mark.parametrize("text", ["make -j 4 lint", "make -l 2 lint", "make -o x lint", "make -j4 lint",
                                  "make -W x lint"])
def test_make_option_values_are_dropped_on_the_lint_path_too(text: str) -> None:
    """``make -j 4 lint`` runs ``lint`` (real make exits 2 on a failing recipe)."""
    assert is_lint_command(text) and is_lint_run(text)


@pytest.mark.parametrize("text", [
    "make -n test", "make --dry-run test", "make --just-print test", "make --recon test", "make -kn test",
    "make -nk test", "make -i test", "make --ignore-errors test", "make -ik test", "make -in test",
    "make -t test", "make --touch test", "make -q test", "gmake -n check", "make -sn test",
    "make -nj4 test", "make -Onone -n test",
])
def test_a_make_run_whose_recipe_cannot_fail_runs_no_check(text: str) -> None:
    """Real GNU make exits 0 for each with a failing ``test`` recipe, so nothing gates (#955)."""
    lint = text.replace("test", "lint").replace("check", "lint")
    assert check_verdict(text) is False and not may_lint(text)
    assert not is_lint_command(lint) and not is_lint_run(lint)


@pytest.mark.parametrize("text", ["make -k test", "make -s test", "make -B test", "make -j 4 test",
                                  "make -C sub test", "make -f n.mk test",
                                  # an attached value holding n/t/q/i is no dry run (each exits 2)
                                  "make -Ctests test", "make -Cinternal test", "make -Iinclude test",
                                  "make -otarget test", "make -Wtarget test", "make -fMakefileci test",
                                  "make -kCtests test", "make -kj4 test", "make -j4 test",
                                  # GNU make 4's -O[TYPE] (--output-sync) attaches its type
                                  "make -Otarget test", "make -Oline test", "make -j4 -Oline test",
                                  "make -O test", "make -Cintegration test", "make -Itests test"])
def test_other_make_options_still_run_the_target(text: str) -> None:
    """``make -k test`` keeps going but still exits 2 with a failing recipe."""
    assert check_verdict(text) is True


def test_an_attached_make_value_keeps_a_lint_run() -> None:
    """``make -Cinternal lint`` runs ``lint`` in ``internal/`` (real make exits 2 on a failing recipe)."""
    assert is_lint_command("make -Cinternal lint") and is_lint_run("make -Cinternal lint")
    assert is_lint_command("make -j4 -Oline lint") and is_lint_run("make -j4 -Oline lint")
