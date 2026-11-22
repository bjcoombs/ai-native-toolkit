"""Tests for issue #920: common test runners and wrappers in CI (``lib.check_command``).

Each listed runner reads true at Layer 5 level 1; an unrecognised target of a runner whose
recipe is not read (``make foo``, ``task foo``) reads null (``check_target``), never false.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lib.check_command import check_verdict, is_check_command, is_lint_command, is_lint_run, may_lint
from lib.agent_environment import scan_agent_environment
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
    "docker run --rm -v src:/src img pytest", "pnpm turbo run test",
    # Calibration round 2 (#920): Gradle test tasks, rake, Rails and mobile toolchains.
    "./gradlew testDebugUnitTest", "./gradlew :app:testReleaseUnitTest", "gradle testDebug",
    "./gradlew connectedAndroidTest", "rake test", "rake spec", "bundle exec rake test",
    "bin/test", "bin/rails test", "flutter test", "xcodebuild test -scheme App",
    "xcodebuild -workspace App.xcworkspace -scheme App -destination 'platform=iOS' test",
    "dart test", "php artisan test", "docker compose run --pull always app pytest",
    "nx run app:test:ci", "nx run app:test:production", "./gradlew connectedDebugAndroidTest", "php vendor/bin/phpunit", "./gradlew testDemoDebug",
])
def test_a_test_runner_or_wrapped_test_is_a_check(text: str) -> None:
    assert check_verdict(text) is True
    assert is_check_command(text)


@pytest.mark.parametrize("text", [
    "make foo", "task foo", "make", "task", "just foo", "turbo run e2e",
    "npx nx affected -t e2e", "hatch run foo", "bundle exec rake", "uv run make foo",
    "docker compose run app", "deno task foo", "make XCODEBUILD_ARGUMENT=-quiet xcodebuild",
    # Package scripts, committed scripts and containers whose body is not read (#920).
    "npm run foo", "pnpm run foo", "pnpm foo", "yarn foo", "./scripts/foo.sh",
    "bash scripts/foo.sh", "docker run img", "docker run --rm img foo",
    "scripts/test-data/seed.sh", "./tests/fixtures/make_db.sh",   # by file name, not directory
])
def test_an_unrecognised_target_reads_null(text: str) -> None:
    """The recipe is not read, so the target may run a check: unknown, never false."""
    assert check_verdict(text) is None
    assert not is_check_command(text)


@pytest.mark.parametrize("text", [
    "dotnet build", "swift build", "mix compile", "bazel build //...", "make install",
    "task deps", "make clean install", "bundle install", "make build", "make deploy",
    "make format", "turbo run build", "npm run build", "npm run build:prod", "pnpm build",
    "yarn install", "yarn", "npm run", "./scripts/install.sh", "./scripts/deploy.sh",
    "./configure", "./gradlew assemble", "docker run img echo hi", "docker build .",
    "bash -c 'foo'", "/usr/bin/foo", "./gradlew assembleDebug", "xcodebuild build -scheme App",
    "xcodebuild -scheme test build", "flutter build apk", "dart compile exe bin/x.dart",
    "php artisan migrate",
    # #926: a package-manager head running a target runner reads as that runner.
    "pnpm turbo run build", "yarn nx affected -t build", "pnpm nx run app:build", "pnpm make build",
    # Review (#924): a directory names no check, a YAML path value is not a script, a
    # container is read by its entrypoint, and a detached one cannot fail its step.
    "./ci/deploy.sh", "bash ci/deploy.sh", "test-results/junit.xml", "coverage/lcov.info", "scripts/foo",
    "docker run -v a:b img ./scripts/deploy.sh", "./gradlew testClasses",
    "./gradlew compileTestJava", "./gradlew assembleAndroidTest",
    "./gradlew assembleDebugAndroidTest", "./gradlew bundleDebugAndroidTest",
    "./gradlew installDebugAndroidTest", "./gradlew testFixturesJar", "./gradlew :lib:testFixturesClasses", "nx run app:build:test", "docker run -d img pytest", "docker compose run -d app pytest",
    "docker run --entrypoint echo img pytest", "npm ci", "uv sync --extra test",
    "go build ./...", "docker compose build", "docker compose up -d", "npx",
    "pnpm exec", "hatch run", "make --version", "just --list", "task --list", "rake -T",
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
    ("php artisan test", False), ("php vendor/bin/phpunit", False),
    ("hatch test", False), ("flutter test", False), ("bin/rails test", False), ("ctest", False), ("vendor/bin/phpunit", False),
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


@pytest.mark.parametrize("run", [*_ISSUE_RUNNERS, "./gradlew testDemoDebug", "bundle exec rake test",
                                 "dart test", "php artisan test"])
def test_each_listed_runner_reads_true_in_ci(tmp_path: Path, run: str) -> None:
    assert _scan(tmp_path, _gha(run)) == (True, "enforced_check")


@pytest.mark.parametrize("run", ["make foo", "task foo", "make", "hatch run foo",
                                 "docker compose run app", "npm run foo", "yarn foo",
                                 "./scripts/foo.sh", "docker run img",
                                 "make XCODEBUILD_ARGUMENT=-quiet xcodebuild"])
def test_an_unrecognised_target_in_ci_reads_check_target(tmp_path: Path, run: str) -> None:
    assert _scan(tmp_path, _gha(run)) == (None, "check_target")


@pytest.mark.parametrize(("files", "expected"), [
    (_gha("make install"), (False, "no_check_command")),
    (_gha("npm ci", "npm run build", "./scripts/deploy.sh"), (False, "no_check_command")),
    ({".github/workflows/ci.yml": "on: pull_request\njobs:\n  b:\n    runs-on: x\n    steps:\n"
      "      - run: npm run build\n      - uses: actions/upload-artifact@v4\n        with:\n"
      "          path: |\n            test-results/junit.xml\n            coverage/lcov.info\n"},
     (False, "no_check_command")),
    (_gha("./ci/deploy.sh"), (False, "no_check_command")),
    ({".gitlab-ci.yml": "build:\n  cache:\n    paths:\n      - ./node_modules\n  script:\n    - echo hi\n"},
     (False, "no_check_command")),
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


def test_a_named_dotnet_test_that_ci_runs_lifts_the_layer_five_cap(tmp_path: Path) -> None:
    """Instruction files get the wider vocabulary too: ``agent_environment.layer5_cap``
    reads a named ``dotnet test`` that CI runs as a resolved check (on main it was not named)."""
    files = {**_gha("dotnet test"), "app.csproj": "<Project />\n",
             "CLAUDE.md": "# Repo\n\n```bash\ndotnet test\n```\n"}
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"]):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)
    block = scan_agent_environment(tmp_path, {"CLAUDE.md": {"grade": "B"}}, None, probe_ci=False)
    assert [(n["command"], n["verdict"]) for n in block["check"]["named"]] == [
        ("dotnet test", "resolved")]
    assert "check_named" not in block["layer5_cap"]["unmet"]


@pytest.mark.parametrize(("text", "expected"), [
    ("pnpm pnpm foo", None), ("yarn yarn", None), ("bun bun x", None), ("npm npm", False),
    ("pnpm turbo run test", True), ("yarn yarn test", True),
])
def test_a_script_named_like_its_package_manager_terminates(text: str, expected: bool | None) -> None:
    """A first positional repeating the head must not re-read the whole command (no recursion)."""
    assert check_verdict(text) is expected


def test_scan_survives_a_repeated_package_manager_word(tmp_path: Path) -> None:
    assert _scan(tmp_path, _gha("pnpm pnpm foo", "yarn yarn", "bun bun x"))[1] in (
        "check_target", "no_check_command")


@pytest.mark.parametrize("text", [
    "uv run foo", "poetry run foo", "npx foo", "pnpm exec foo", "yarn dlx foo", "bunx foo",
    "pipx run foo", "pipx run --spec foo==1 foo", "uvx foo", "bundle exec foo",
    "uv run mkdocs", "uv run locust -f load.py",
])
def test_an_unknown_program_behind_a_wrapper_reads_null(text: str) -> None:
    """Unknown beats wrong: the wrapper runs a program whose checks are not read."""
    assert check_verdict(text) is None


@pytest.mark.parametrize(("text", "expected"), [
    ("uv run pytest", True), ("poetry run pytest -q", True), ("npx jest", True),
    ("pipx run tox", True), ("uvx nox", True), ("bunx vitest run", True),
    ("uv run deploy", False), ("npx build-thing", False), ("uv run python -m build", False),
    ("npx eslint .", False), ("uv run echo hi", False),
    ("npx", False), ("uv run", False), ("pipx install foo", False),
    # Deploy, publish and install lines behind a wrapper stay false (#924 review).
    ("npx wrangler deploy", False), ("npx changeset publish", False), ("uvx twine upload", False),
    ("npx playwright install --with-deps", False), ("npx prisma migrate deploy", False),
    ("bundle exec fastlane beta", False), ("npx lerna publish", False), ("npx lerna version", False), ("uv run mkdocs build --strict", False),
])
def test_known_programs_behind_a_wrapper_stay_definite(text: str, expected: bool) -> None:
    assert check_verdict(text) is expected


@pytest.mark.parametrize("text", ["npx lerna run test", "yarn lerna run test"])
def test_a_lerna_run_reads_like_its_package_manager_form(text: str) -> None:
    """``lerna run test`` runs each package's test script: never a wrong 0."""
    assert check_verdict(text) is not False
