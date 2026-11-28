"""API and contract compatibility checks CI runs (issue #853): ``lib.ci.api_compat``."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import lib.scoring.level_tally as lt
from lib.ci.api_compat import scan_api_compat, tool_of
from lib.counts_only.content_free import counts_only

CID = "api-compat.1"
WF = ".github/workflows/ci.yml"
TESTS_RUN = {"enforced_check_runs": True}


def _workflow(*runs: str, extra: str = "") -> str:
    steps = "".join(f"      - run: {r}\n" for r in runs)
    return ("name: ci\non: [push, pull_request]\njobs:\n  check:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - uses: actions/checkout@v4\n" + steps + extra)


def _repo(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _block(root: Path, ci_checks: dict[str, Any] | None = None) -> dict[str, Any]:
    return dict(scan_api_compat(root, ci_checks))


def _state(root: Path, ci_checks: dict[str, Any] | None = None) -> str:
    data = {"api_compat": scan_api_compat(root, ci_checks)}
    cond = next(c for c in lt.level_result(lt.default_model(), data)["conditions"] if c["id"] == CID)
    return str(cond["state"])


APIDIFF_SH = ("#!/usr/bin/env bash\nset -euo pipefail\n"
              "go install golang.org/x/exp/cmd/apidiff@v0.0.0\n"
              "out=$(apidiff -m -incompatible \"$BASE\" ./pkg/widget)\n"
              "if [ -n \"$out\" ]; then echo \"$out\"; exit 1; fi\n")


# --- named-tool arm ------------------------------------------------------------------------

def test_an_apidiff_script_run_from_a_workflow_step_meets(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"hack/apidiff.sh": APIDIFF_SH, WF: _workflow("hack/apidiff.sh")})
    block = _block(root)
    assert block["runs"] == [{"path": WF, "tool": "apidiff"}]
    assert (block["present"], block["tools_run"], block["declared_only"]) == (True, 1, 0)
    assert _state(root) == "met"


def test_a_make_target_that_runs_apidiff_is_followed(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"Makefile": "api-check:\n\tgo run golang.org/x/exp/cmd/apidiff@v0.0.0 old new > out.txt\n"
                                        "\ttest ! -s out.txt || exit 1\n",
                            WF: _workflow("make api-check")})
    assert _block(root)["runs"] == [{"path": WF, "tool": "apidiff"}]


@pytest.mark.parametrize(("line", "tool"), [
    ("npx --yes @microsoft/api-extractor run", "api-extractor"),
    ("pnpm exec api-extractor run --verbose", "api-extractor"),
    ("uv run --locked griffe check widgets -a v1.0.0", "griffe"),
    ("cargo semver-checks check-release", "cargo-semver-checks"),
    ("buf breaking --against '.git#branch=main'", "buf-breaking"),
    ("java -jar tools/japicmp-0.18.jar -o old.jar -n new.jar --error-on-binary-incompatibility", "japicmp"),
])
def test_each_named_tool_on_a_ci_line_meets(tmp_path: Path, line: str, tool: str) -> None:
    root = _repo(tmp_path, {WF: _workflow(line)})
    assert _block(root)["runs"] == [{"path": WF, "tool": tool}]


@pytest.mark.parametrize("line", [
    "go install golang.org/x/exp/cmd/apidiff@latest",
    "npm install --save-dev @microsoft/api-extractor",
    "cargo install cargo-semver-checks --locked",
    "pip install griffe",
    "apidiff --help",
    "echo apidiff",
    "api-extractor run --local",
    "apidiff old new || true",
    "buf breaking --against main || echo changed",
])
def test_installs_help_echo_local_and_masked_runs_do_not_count(tmp_path: Path, line: str) -> None:
    root = _repo(tmp_path, {WF: _workflow(line)})
    assert _block(root)["runs"] == []


@pytest.mark.parametrize(("line", "tool"), [
    ("apidiff -incompatible old new", "apidiff"),                      # exits 0 on an incompatible change
    ("java -jar tools/japicmp-0.18.jar -o old.jar -n new.jar", "japicmp"),  # no --error-on-* flag
])
def test_a_run_that_cannot_stop_the_job_is_declared_not_counted(tmp_path: Path, line: str, tool: str) -> None:
    root = _repo(tmp_path, {WF: _workflow(line)})
    block = _block(root)
    assert (block["runs"], block["declared"], block["present"]) == ([], [{"path": WF, "tool": tool}], False)


@pytest.mark.parametrize("body", [
    "#!/bin/sh\napidiff -incompatible old new | grep -q . && exit 1\n",          # its pipe decides
    "#!/bin/sh\napidiff -incompatible old new > out.txt\ntest ! -s out.txt || exit 1\n",
])
def test_an_apidiff_output_check_in_a_followed_script_counts(tmp_path: Path, body: str) -> None:
    root = _repo(tmp_path, {"hack/api.sh": body, WF: _workflow("hack/api.sh")})
    assert _block(root)["runs"] == [{"path": WF, "tool": "apidiff"}]


def test_an_apidiff_run_checked_by_its_job_counts(tmp_path: Path) -> None:
    wf = _workflow(extra="      - run: |\n          apidiff -incompatible old new > out.txt\n"
                         "          if [ -s out.txt ]; then exit 1; fi\n")
    assert _block(_repo(tmp_path, {WF: wf}))["runs"] == [{"path": WF, "tool": "apidiff"}]


@pytest.mark.parametrize(("uses", "tool"), [
    ("obi1kenobi/cargo-semver-checks-action@v2", "cargo-semver-checks"),
    ("bufbuild/buf-breaking-action@v1", "buf-breaking"),
])
def test_a_compatibility_action_step_meets(tmp_path: Path, uses: str, tool: str) -> None:
    root = _repo(tmp_path, {WF: _workflow(extra=f"      - uses: {uses}\n")})
    block = _block(root)
    assert (block["runs"], block["declared"], block["present"]) == ([{"path": WF, "tool": tool}], [], True)
    masked = _repo(tmp_path / "m", {WF: _workflow(extra=f"      - uses: {uses}\n        continue-on-error: true\n")})
    assert _block(masked)["runs"] == []


def test_continue_on_error_masks_the_step(tmp_path: Path) -> None:
    wf = ("on: [pull_request]\njobs:\n  api:\n    runs-on: ubuntu-latest\n    steps:\n"
          "      - continue-on-error: true\n        run: apidiff old new\n")
    root = _repo(tmp_path, {WF: wf})
    assert _block(root)["present"] is False


def test_a_masked_script_line_is_not_followed(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"hack/apidiff.sh": APIDIFF_SH,
                            "hack/ci.sh": "#!/bin/sh\nhack/apidiff.sh || echo skipped\n",
                            WF: _workflow("hack/ci.sh")})
    assert _block(root)["runs"] == []


POM = ("<project><modelVersion>4.0.0</modelVersion><artifactId>widget</artifactId><build><plugins>"
       "<plugin><groupId>com.github.siom79.japicmp</groupId><artifactId>japicmp-maven-plugin</artifactId>"
       "{config}<executions><execution><goals><goal>cmp</goal></goals></execution></executions></plugin>"
       "</plugins></build></project>")
BREAK = ("<configuration><parameter><breakBuildOnBinaryIncompatibleModifications>true"
         "</breakBuildOnBinaryIncompatibleModifications></parameter></configuration>")


def test_a_maven_japicmp_goal_set_to_break_the_build_meets(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"pom.xml": POM.format(config=BREAK), WF: _workflow("mvn -B verify")})
    assert _block(root)["runs"] == [{"path": WF, "tool": "japicmp"}]
    early = _repo(tmp_path / "early", {"pom.xml": POM.format(config=BREAK), WF: _workflow("mvn -B test")})
    assert _block(early)["runs"] == []


def test_a_maven_japicmp_goal_with_its_defaults_only_reports(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"pom.xml": POM.format(config=""), WF: _workflow("mvn -B verify")})
    block = _block(root)
    assert block["runs"] == [] and {"path": WF, "tool": "japicmp"} in block["declared"]
    flagged = _repo(tmp_path / "f", {"pom.xml": POM.format(config=""), WF: _workflow(
        "mvn -B verify -Djapicmp.breakBuildOnBinaryIncompatibleModifications=true")})
    assert _block(flagged)["runs"] == [{"path": WF, "tool": "japicmp"}]


def test_an_npm_script_is_followed_one_level(tmp_path: Path) -> None:
    pkg = '{"scripts": {"api:check": "api-extractor run"}, "devDependencies": {"@microsoft/api-extractor": "7"}}'
    root = _repo(tmp_path, {"package.json": pkg, WF: _workflow("npm run api:check")})
    block = _block(root)
    assert block["runs"] == [{"path": WF, "tool": "api-extractor"}] and block["declared"] == []


# --- declared only -------------------------------------------------------------------------

def test_a_tool_named_only_in_dev_dependencies_or_docs_is_declared_only(tmp_path: Path) -> None:
    pkg = '{"devDependencies": {"@microsoft/api-extractor": "7.0.0"}}'
    root = _repo(tmp_path, {"package.json": pkg, "docs/compat.md": "Run `apidiff` before a release.\n",
                            WF: _workflow("npm test")})
    block = _block(root, TESTS_RUN)
    assert block["declared"] == [{"path": "package.json", "tool": "api-extractor"}]
    assert (block["present"], block["declared_only"], block["tools_run"]) == (False, 1, 0)
    assert _state(root, TESTS_RUN) == "unmet"


def test_a_declared_tool_with_an_unread_ci_system_reads_not_measured(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"hack/apidiff.sh": APIDIFF_SH, "Jenkinsfile": "pipeline {}\n"})
    block = _block(root)
    assert (block["present"], block.get("present_reason")) == (None, "ci_not_read")
    assert _state(root) == "not_measured"


def test_no_tool_and_no_test_reads_unmet(tmp_path: Path) -> None:
    root = _repo(tmp_path, {WF: _workflow("pytest"), "Jenkinsfile": "pipeline {}\n"})
    assert _block(root, TESTS_RUN)["present"] is False


# --- pinning-test arm ----------------------------------------------------------------------

PIN_TEST = ("from widgets.outputs import __all__\n\nEXPECTED_ALL = [\"Gadget\", \"Sprocket\"]\n\n\n"
            "def test_all_imports() -> None:\n    assert set(__all__) == set(EXPECTED_ALL)\n")


def test_an_expected_all_test_with_ci_running_tests_meets(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"tests/unit/test_imports.py": PIN_TEST, WF: _workflow("pytest")})
    block = _block(root, TESTS_RUN)
    assert (block["pins"], block["present"]) == (["tests/unit/test_imports.py"], True)
    assert _state(root, TESTS_RUN) == "met"


def test_a_pinning_test_with_no_ci_test_run_is_unmet(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"tests/test_imports.py": PIN_TEST})
    assert _block(root, {"enforced_check_runs": False})["present"] is False


def test_a_pinning_test_with_the_test_run_unsettled_reads_not_measured(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"tests/test_imports.py": PIN_TEST})
    for ci_checks in ({"enforced_check_runs": None}, None, {"available": False}):
        block = _block(root, ci_checks)
        assert (block["present"], block.get("present_reason")) == (None, "tests_not_read")


@pytest.mark.parametrize("body", [
    "def test_x() -> None:\n    assert len(__all__) > 0\n",            # no == pin
    "def test_x() -> None:\n    assert mod.__all__ == other.__all__\n",  # both sides the surface
    "def test_x() -> None:\n    assert 'EXPECTED_ALL' == 'x'  # __all__ ==\n",  # text only
    "def test_x() -> None:\n    assert set(mod.__all__) == {n for n in dir(mod) if not n.startswith('_')}\n",
    "def test_x() -> None:\n    assert sorted(mod.__all__) == sorted(n for n, _ in inspect.getmembers(mod))\n",
    "def test_x() -> None:\n    assert set(m.__all__) == set(dir(m)) - {'helper'}\n",  # a literal inside, not the side
    "def test_x() -> None:\n    assert set(m.__all__) == {n for n, _ in inspect.getmembers(m) if n not in ('a', 'b')}\n",
])
def test_a_test_that_names_all_without_pinning_it_does_not_count(tmp_path: Path, body: str) -> None:
    root = _repo(tmp_path, {"tests/test_imports.py": body})
    assert _block(root, TESTS_RUN)["pins"] == []


def test_an_exports_snapshot_in_a_js_test_pins(tmp_path: Path) -> None:
    js = "import * as api from '../src';\ntest('exports', () => {\n  expect(Object.keys(api)).toMatchSnapshot();\n});\n"
    commented = "// expect(Object.keys(api)).toMatchSnapshot();\n"
    literal = "import * as api from '../src';\nexpect(Object.keys(api)).toEqual(['Gadget', 'Sprocket']);\n"
    live = "import * as api from '../src';\nexpect(Object.keys(api)).toEqual(Object.keys(other));\n"
    root = _repo(tmp_path, {"src/index.test.ts": js, "src/other.test.ts": commented,
                            "src/literal.test.ts": literal, "src/live.test.ts": live,   # live: two surfaces, no pin
                            "tests/fixtures/index.test.ts": js})
    assert _block(root, TESTS_RUN)["pins"] == ["src/index.test.ts", "src/literal.test.ts"]


# --- known not detected --------------------------------------------------------------------

def test_an_api_surface_ratchet_test_is_known_not_detected(tmp_path: Path) -> None:
    """A committed surface file regenerated behind a flag and compared by a test (camel-shaped)."""
    java = ("class ApiSurfaceTest {\n  @Test void surfaceIsStable() throws Exception {\n"
            "    String now = ApiSurface.render();\n    if (Boolean.getBoolean(\"regenerate\")) {"
            " Files.writeString(SURFACE, now); }\n    assertEquals(Files.readString(SURFACE), now);\n  }\n}\n")
    root = _repo(tmp_path, {"core/src/test/java/ApiSurfaceTest.java": java,
                            "core/src/test/resources/api-surface.txt": "org.example.Widget#run()\n",
                            WF: _workflow("mvn -B verify")})
    block = _block(root, TESTS_RUN)
    assert (block["present"], block["runs"], block["pins"]) == (False, [], [])
    assert _state(root, TESTS_RUN) == "unmet"


def test_a_migration_check_is_known_not_detected(tmp_path: Path) -> None:
    """``makemigrations --check`` is a schema-drift check, not an API compatibility tool (django-shaped)."""
    root = _repo(tmp_path, {WF: _workflow("python manage.py makemigrations --check --dry-run", "pytest")})
    block = _block(root, TESTS_RUN)
    assert (block["present"], block["runs"]) == (False, [])
    assert _state(root, TESTS_RUN) == "unmet"


# --- GitLab --------------------------------------------------------------------------------

def test_a_gitlab_job_script_meets_and_after_script_does_not(tmp_path: Path) -> None:
    run = _repo(tmp_path / "a", {".gitlab-ci.yml": "api:\n  script:\n    - buf breaking --against main\n"})
    assert _block(run)["runs"] == [{"path": ".gitlab-ci.yml", "tool": "buf-breaking"}]
    after = _repo(tmp_path / "b", {".gitlab-ci.yml": "api:\n  script:\n    - make\n  after_script:\n"
                                   "    - buf breaking --against main\n"})
    assert _block(after)["runs"] == []


@pytest.mark.parametrize("job", [
    "api:\n  when: never\n  script:\n    - buf breaking --against main\n",
    "api:\n  rules:\n    - when: never\n  script:\n    - buf breaking --against main\n",
    "api:\n  allow_failure: true\n  script:\n    - buf breaking --against main\n",
    ".api:\n  script:\n    - buf breaking --against main\n",
])
def test_gitlab_jobs_that_never_run_or_cannot_fail_do_not_count(tmp_path: Path, job: str) -> None:
    root = _repo(tmp_path, {".gitlab-ci.yml": job})
    assert _block(root)["runs"] == []


def test_a_gitlab_hidden_job_counts_through_extends(tmp_path: Path) -> None:
    ci = ".api:\n  script:\n    - buf breaking --against main\napi:\n  extends: .api\n"
    root = _repo(tmp_path, {".gitlab-ci.yml": ci})
    assert _block(root)["runs"] == [{"path": ".gitlab-ci.yml", "tool": "buf-breaking"}]


def test_a_gitlab_hidden_script_replaced_by_the_extending_job_does_not_count(tmp_path: Path) -> None:
    ci = ".compat:\n  script:\n    - cargo semver-checks\ntest:\n  extends: .compat\n  script:\n    - cargo test\n"
    assert _block(_repo(tmp_path, {".gitlab-ci.yml": ci}))["runs"] == []
    kept = ".compat:\n  before_script:\n    - cargo semver-checks\ntest:\n  extends: .compat\n  script:\n    - cargo test\n"
    assert _block(_repo(tmp_path / "k", {".gitlab-ci.yml": kept}))["runs"] == [
        {"path": ".gitlab-ci.yml", "tool": "cargo-semver-checks"}]


def test_a_gitlab_hidden_job_taken_by_reference_reads_not_measured(tmp_path: Path) -> None:
    ci = ".api:\n  script:\n    - buf breaking --against main\napi:\n  script: !reference [.api, script]\n"
    block = _block(_repo(tmp_path, {".gitlab-ci.yml": ci}))
    assert (block["runs"], block["present"], block.get("present_reason")) == ([], None, "ci_not_read")


# --- counts-only and helpers ---------------------------------------------------------------

def test_counts_only_keeps_every_field(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"hack/apidiff.sh": APIDIFF_SH, WF: _workflow("hack/apidiff.sh"),
                            "tests/test_imports.py": PIN_TEST, "buf.yaml": "version: v1\nbreaking:\n  use: [FILE]\n"})
    block = _block(root, TESTS_RUN)
    out = counts_only({"api_compat": block})["api_compat"]
    assert out == block
    assert set(out) == {"files_read", "tools_run", "pinning_tests", "declared_only", "present", "runs",
                        "pins", "declared"}
    assert out["declared"] == [{"path": "buf.yaml", "tool": "buf-breaking"}]   # hack/apidiff.sh runs, so not listed


def test_counts_only_keeps_the_present_reason(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"tests/test_imports.py": PIN_TEST})
    out = counts_only({"api_compat": _block(root)})["api_compat"]
    assert out["present_reason"] == "tests_not_read"


def test_tool_of_reads_nothing_from_an_empty_command() -> None:
    assert tool_of([]) is None and tool_of(["go", "run", "./cmd/server"]) is None


def test_a_gradle_japicmp_task_counts_only_when_it_fails_on_modification(tmp_path: Path) -> None:
    build = ("plugins {{\n    id 'java'\n    id 'me.champeau.gradle.japicmp' version '0.4.2'\n}}\n"
             "tasks.register('japicmp', me.champeau.gradle.japicmp.JapicmpTask) {{\n{body}}}\n"
             "check.dependsOn japicmp\nkotlin {{ failOnWarning = true }}\n")
    files = {"settings.gradle": "rootProject.name = 'widget'\n", WF: _workflow("./gradlew check")}
    quiet = _repo(tmp_path / "q", {**files, "build.gradle": build.format(body="    onlyModified = true\n")})
    assert _block(quiet)["runs"] == [] and {"path": WF, "tool": "japicmp"} in _block(quiet)["declared"]
    strict = _repo(tmp_path / "s", {**files, "build.gradle": build.format(body="    failOnModification = true\n")})
    assert _block(strict)["runs"] == [{"path": WF, "tool": "japicmp"}]


@pytest.mark.parametrize("runs", [
    ("apidiff old new", "test -f go.mod || exit 1"),           # another step is another shell
    ("test -f go.mod || exit 1; apidiff old new",),            # the re-raise comes before apidiff
    ("apidiff old new; exit $?",),                              # $? is apidiff's own 0
    ("apidiff old new || exit 1",),                             # apidiff exits 0, so its fallback never runs
    ("apidiff old new || { echo broke; exit 1; }",),            # nor does a grouped fallback
    ("apidiff old new; rc=$?; exit $rc",),                      # rc holds apidiff's 0
    ("apidiff old new > report.txt", "test -f other || exit 1"),  # the exit reads nothing apidiff wrote
    ("out=$(apidiff old new)", 'if [ -n "$output" ]; then exit 1; fi'),  # $output is not $out
    ("apidiff old new > /dev/null", "grep -q x /dev/null && exit 1"),    # /dev/null holds nothing
])
def test_an_exit_that_cannot_react_to_apidiff_does_not_count(tmp_path: Path, runs: tuple[str, ...]) -> None:
    body = "      - run: |\n" + "".join(f"          {r}\n" for r in runs)   # one step, one shell
    for wf in (_workflow(*runs), _workflow(extra=body)):
        assert _block(_repo(tmp_path, {WF: wf}))["runs"] == []


def test_a_kotlin_dsl_japicmp_fail_setting_counts(tmp_path: Path) -> None:
    build = ("plugins {\n    java\n    id(\"me.champeau.gradle.japicmp\") version \"0.4.2\"\n}\n"
             "val japicmp by tasks.registering(me.champeau.gradle.japicmp.JapicmpTask::class) {\n"
             "    failOnModification.set(true)\n}\ntasks.named(\"check\") { dependsOn(japicmp) }\n")
    root = _repo(tmp_path, {"settings.gradle.kts": "rootProject.name = \"widget\"\n",
                            "build.gradle.kts": build, WF: _workflow("./gradlew check")})
    assert _block(root)["runs"] == [{"path": WF, "tool": "japicmp"}]


def test_gitlab_is_read_through_the_merged_view(tmp_path: Path) -> None:
    """An included template's ``script:`` runs in the job that extends it, unless that job sets its own."""
    files = {".gitlab-ci.yml": "include:\n  - local: ci/compat.yml\napi:\n  extends: .compat\n",
             "ci/compat.yml": ".compat:\n  script:\n    - buf breaking --against main\n"}
    assert _block(_repo(tmp_path / "a", files))["runs"] == [{"path": ".gitlab-ci.yml", "tool": "buf-breaking"}]
    replaced = {**files, ".gitlab-ci.yml": files[".gitlab-ci.yml"] + "  script:\n    - make test\n"}
    assert _block(_repo(tmp_path / "b", replaced))["runs"] == []


REVAPI_POM = ("<project><modelVersion>4.0.0</modelVersion><artifactId>widget</artifactId><build><plugins>"
              "<plugin><groupId>org.revapi</groupId><artifactId>revapi-maven-plugin</artifactId>{config}"
              "<executions><execution><goals><goal>{goal}</goal></goals></execution></executions></plugin>"
              "</plugins></build></project>")


def test_a_maven_revapi_check_goal_meets(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"pom.xml": REVAPI_POM.format(config="", goal="check"), WF: _workflow("mvn -B verify")})
    assert _block(root)["runs"] == [{"path": WF, "tool": "revapi"}]


@pytest.mark.parametrize(("config", "goal", "line"), [
    ("", "report", "mvn -B verify"),                                     # report only: never stops the build
    ("<configuration><failBuildOnProblemsFound>false</failBuildOnProblemsFound></configuration>", "check",
     "mvn -B verify"),
    ("", "check", "mvn -B verify -Drevapi.failBuildOnProblemsFound=false"),
])
def test_a_revapi_run_that_cannot_stop_the_build_is_declared(tmp_path: Path, config: str, goal: str, line: str) -> None:
    root = _repo(tmp_path, {"pom.xml": REVAPI_POM.format(config=config, goal=goal), WF: _workflow(line)})
    block = _block(root)
    assert block["runs"] == [] and block["present"] is not True   # a report goal's site phase reads unsettled
    assert "revapi" in {row["tool"] for row in block["declared"]}   # listed, never counted


# --- #1612: gaps left by #1607 -------------------------------------------------------------

def test_the_go_apidiff_action_meets_and_masked_does_not(tmp_path: Path) -> None:
    """``joelanford/go-apidiff`` exits with go-apidiff's code, non-zero on an incompatible change."""
    root = _repo(tmp_path, {WF: _workflow(extra="      - uses: joelanford/go-apidiff@v0.8.3\n")})
    block = _block(root)
    assert (block["runs"], block["declared"], block["present"]) == ([{"path": WF, "tool": "apidiff"}], [], True)
    masked = _repo(tmp_path / "m", {WF: _workflow(
        extra="      - uses: joelanford/go-apidiff@v0.8.3\n        continue-on-error: true\n")})
    assert _block(masked)["runs"] == []


@pytest.mark.parametrize("line", [
    'if [ -n "$(apidiff -incompatible old new)" ]; then exit 1; fi',
    'if [[ -n "$(apidiff -incompatible old new)" ]]; then echo broke; exit 1; fi',
    'test -n "$(apidiff -incompatible old new)" && exit 1',
    '[ -z "$(apidiff -incompatible old new)" ] || exit 1',
])
def test_an_inline_apidiff_output_test_that_re_raises_counts(tmp_path: Path, line: str) -> None:
    root = _repo(tmp_path, {WF: _workflow(line)})
    assert _block(root)["runs"] == [{"path": WF, "tool": "apidiff"}]
    script = _repo(tmp_path / "s", {"hack/api.sh": f"#!/bin/sh\n{line}\n", WF: _workflow("hack/api.sh")})
    assert _block(script)["runs"] == [{"path": WF, "tool": "apidiff"}]


def test_an_inline_apidiff_test_with_then_on_later_lines_counts(tmp_path: Path) -> None:
    body = '#!/bin/sh\nif [ -n "$(apidiff -incompatible old new)" ]; then\n  echo broke\n  exit 1\nfi\n'
    root = _repo(tmp_path, {"hack/api.sh": body, WF: _workflow("hack/api.sh")})
    assert _block(root)["runs"] == [{"path": WF, "tool": "apidiff"}]
    quiet = '#!/bin/sh\nif [ -n "$(apidiff -incompatible old new)" ]; then\n  echo broke\nfi\nexit 1\n'
    root = _repo(tmp_path / "q", {"hack/api.sh": quiet, WF: _workflow("hack/api.sh")})
    assert _block(root)["runs"] == []   # the exit after ``fi`` does not depend on the test
    step = '      - run: |\n          if [ -n "$(apidiff -incompatible old new)" ]; then\n            exit 1\n          fi\n'
    assert _block(_repo(tmp_path / "w", {WF: _workflow(extra=step)}))["runs"] == [{"path": WF, "tool": "apidiff"}]
    masked = step.replace("      - run: |\n", "      - continue-on-error: true\n        run: |\n")
    assert _block(_repo(tmp_path / "m", {WF: _workflow(extra=masked)}))["runs"] == []


@pytest.mark.parametrize("line", [
    'if [ -z "$(apidiff -incompatible old new)" ]; then exit 1; fi',   # red when nothing changed
    '[ -n "$(apidiff -incompatible old new)" ] || exit 1',            # red when nothing changed
    '[ -n "$(apidiff -incompatible old new)" ] && echo changed',       # no re-raise
    '[ -n "$(apidiff -incompatible old new)" ] && echo changed; exit 0',
    '[ -n "$(apidiff -incompatible old new)" ] && exit 1 || true',     # masked
    'if [ -n "$(apidiff -incompatible old new)" ]; then exit 0; fi',
    'if [ -n "$(apidiff --help)" ]; then exit 1; fi',                  # invokes nothing
    'echo "$(apidiff -incompatible old new)"',
])
def test_an_inline_apidiff_test_that_cannot_fail_on_a_break_does_not_count(tmp_path: Path, line: str) -> None:
    root = _repo(tmp_path, {WF: _workflow(line)})
    block = _block(root)
    assert block["runs"] == [] and block["present"] is False


@pytest.mark.parametrize("line", [
    "apidiff -incompatible old new > out.txt; test ! -s out.txt || exit 1",
    "apidiff -incompatible old new > out.txt; if [ -s out.txt ]; then exit 1; fi",
    "apidiff -incompatible old new > out.txt; grep -q . out.txt && { echo broke; exit 1; }",
    "apidiff -incompatible old new > out.txt; diff /dev/null out.txt || exit 1",
    "apidiff -incompatible old new > out.txt; [ -s out.txt ] && cat out.txt && exit 1",
    "apidiff -incompatible old new > out.txt; if grep -q . out.txt; then exit 1; fi",
    'apidiff -incompatible old new > out.txt; if ! [ -z "$(cat out.txt)" ]; then exit 1; fi',
    "apidiff -incompatible old new > out.txt; if [ $(wc -l < out.txt) -gt 0 ]; then exit 1; fi",
    "apidiff -incompatible old new > out.txt; ! grep -q . out.txt || exit 1",
    "apidiff -incompatible old new > out.txt; if [ $(wc -l < out.txt) -gt 5 ]; then exit 1; fi",   # red on a large break
])
def test_a_one_line_apidiff_output_check_counts(tmp_path: Path, line: str) -> None:
    root = _repo(tmp_path, {WF: _workflow(line)})
    assert _block(root)["runs"] == [{"path": WF, "tool": "apidiff"}]
    script = _repo(tmp_path / "s", {"hack/api.sh": f"#!/bin/sh\n{line}\n", WF: _workflow("hack/api.sh")})
    assert _block(script)["runs"] == [{"path": WF, "tool": "apidiff"}]


@pytest.mark.parametrize("runs", [
    ("apidiff old new; echo x > other.txt; test ! -s other.txt || exit 1",),   # other.txt is echo's
    ("apidiff old new; echo x > other.txt", "test ! -s other.txt || exit 1"),
    ("apidiff old new > out.txt; exit 1 || test -s out.txt",),                 # the exit reads nothing
    ("apidiff old new > out.txt || test ! -s out.txt || exit 1",),             # apidiff's own fallback
    ("apidiff old new > report.txt; cat report.txt; go build ./... || exit 1",),   # go build's fallback
    ("apidiff old new > report.txt", "cat report.txt; go build ./... || exit 1"),
    ("apidiff old new > report.txt; test -s report.txt || exit 1",),           # red only when nothing changed
    ("apidiff old new > report.txt", "if [ ! -s report.txt ]; then exit 1; fi"),
    ("apidiff old new | grep -q .; go build ./... || exit 1",),                # the pipe decides nothing
    ("apidiff old new | grep -vq x && exit 1",),                               # an inverted grep
    ("apidiff old new > out.txt; [ -s out.txt ] && echo changed || exit 1",),  # red only when nothing changed
    ("apidiff old new > out.txt", "[ -s out.txt ] && echo changed || exit 1"),
    ("apidiff old new > out.txt; if ! grep -q . out.txt; then exit 1; fi",),   # red only when nothing changed
    ("apidiff old new > out.txt; if [ $(wc -l < out.txt) -eq 0 ]; then exit 1; fi",),
    ("apidiff old new > out.txt; if [ $(wc -l < out.txt) -ge 0 ]; then exit 1; fi",),  # always true
])
def test_a_one_line_exit_that_reads_nothing_apidiff_wrote_does_not_count(tmp_path: Path, runs: tuple[str, ...]) -> None:
    body = "      - run: |\n" + "".join(f"          {r}\n" for r in runs)
    assert _block(_repo(tmp_path, {WF: _workflow(extra=body)}))["runs"] == []


def test_a_griffe_dependency_is_not_a_declared_check(tmp_path: Path) -> None:
    pyproject = ('[project]\nname = "widgets"\n[dependency-groups]\n'
                 'docs = ["mkdocstrings[python]", "griffe>=1.0", "griffe-typingdoc", "griffe-pydantic"]\n')
    root = _repo(tmp_path, {"pyproject.toml": pyproject, "Jenkinsfile": "pipeline {}\n"})
    block = _block(root)
    assert (block["declared"], block["present"], block.get("present_reason")) == ([], False, None)


@pytest.mark.parametrize(("rel", "text"), [
    ("Makefile", "api:\n\tuv run griffe check widgets -a v1.0.0\n"),
    ("pyproject.toml", '[project]\nname = "widgets"\n\n[tool.griffe]\nsearch-paths = ["src"]\n'),
])
def test_griffe_check_or_its_table_is_declared(tmp_path: Path, rel: str, text: str) -> None:
    block = _block(_repo(tmp_path, {rel: text}))
    assert block["declared"] == [{"path": rel, "tool": "griffe"}]


def test_a_shell_glob_is_not_a_yaml_alias(tmp_path: Path) -> None:
    ci = (".api:\n  script:\n    - buf breaking --against main\nlint:\n  script:\n    - ls *.txt\n    - cat [*.md]\n"
          "docs:\n  script: [ls *.txt]\n")
    block = _block(_repo(tmp_path, {".gitlab-ci.yml": ci}))
    assert (block["runs"], block["present"], block.get("present_reason")) == ([], False, None)


@pytest.mark.parametrize("use", ["api: *compat\n", "api:\n  script:\n    - *compat\n", "api:\n  <<: *compat\n",
                                 "api:\n  script: [*compat, make check]\n", "api:\n  script: [make, *compat]\n",
                                 'api:\n  "script": *compat\n', "api:\n  script: !!seq *compat\n"])
def test_a_yaml_alias_value_still_reads_not_measured(tmp_path: Path, use: str) -> None:
    """An alias as a whole YAML value (or a merge key) may run the hidden job: unsettled, never unmet."""
    ci = ".compat: &compat\n  script:\n    - buf breaking --against main\n" + use
    block = _block(_repo(tmp_path, {".gitlab-ci.yml": ci}))
    assert (block["runs"], block["present"], block.get("present_reason")) == ([], None, "ci_not_read")


@pytest.mark.parametrize("line", ["npm test", "npm t", "npm tst", "npm run test", "pnpm test", "yarn test",
                                  "/usr/local/bin/yarn test"])
def test_npm_test_and_its_aliases_are_followed(tmp_path: Path, line: str) -> None:
    pkg = '{"scripts": {"test": "api-extractor run && jest"}}'
    root = _repo(tmp_path, {"package.json": pkg, WF: _workflow(line)})
    assert _block(root)["runs"] == [{"path": WF, "tool": "api-extractor"}]


def test_bun_test_runs_buns_own_runner_not_the_script(tmp_path: Path) -> None:
    pkg = '{"scripts": {"test": "api-extractor run && jest"}}'
    root = _repo(tmp_path, {"package.json": pkg, WF: _workflow("bun test")})
    assert _block(root)["runs"] == []
    run = _repo(tmp_path / "r", {"package.json": pkg, WF: _workflow("bun run test")})
    assert _block(run)["runs"] == [{"path": WF, "tool": "api-extractor"}]


def test_the_scorer_cites_a_pin_only_when_ci_runs_tests() -> None:
    """``present`` can be true from ``runs`` alone, so the pin citation carries its own gate."""
    scorer = Path(__file__).resolve().parents[3] / "agents" / "assess-layer-scorer.md"
    para = next(p for p in scorer.read_text(encoding="utf-8").split("\n\n")
                if p.startswith("**API and contract compatibility checks.**"))
    assert "Cite a test in `pins` only when `ci_checks.enforced_check_runs` is true" in para


def test_then_on_its_own_line_after_an_output_test_counts(tmp_path: Path) -> None:
    body = "#!/bin/sh\napidiff -incompatible old new > out.txt\nif [ -s out.txt ]\nthen\n  exit 1\nfi\n"
    root = _repo(tmp_path, {"hack/api.sh": body, WF: _workflow("hack/api.sh")})
    assert _block(root)["runs"] == [{"path": WF, "tool": "apidiff"}]
    quiet = body.replace("then\n  exit 1\nfi\n", "then\n  echo changed\nfi\nexit 1\n")
    root = _repo(tmp_path / "q", {"hack/api.sh": quiet, WF: _workflow("hack/api.sh")})
    assert _block(root)["runs"] == []


@pytest.mark.parametrize("check", [
    "if grep -q . out.txt; then exit 1; fi",
    'if ! [ -z "$(cat out.txt)" ]; then exit 1; fi',
    "if [ $(wc -l < out.txt) -gt 0 ]; then exit 1; fi",
])
def test_a_later_line_if_check_that_read_met_on_main_still_counts(tmp_path: Path, check: str) -> None:
    body = f"      - run: |\n          apidiff -incompatible old new > out.txt\n          {check}\n"
    assert _block(_repo(tmp_path, {WF: _workflow(extra=body)}))["runs"] == [{"path": WF, "tool": "apidiff"}]
    script = _repo(tmp_path / "s", {"hack/api.sh": f"#!/bin/sh\napidiff -incompatible old new > out.txt\n{check}\n",
                                    WF: _workflow("hack/api.sh")})
    assert _block(script)["runs"] == [{"path": WF, "tool": "apidiff"}]


@pytest.mark.parametrize("lines", [
    ('if [ -n "$(apidiff -incompatible old new)" ]; then false; fi || true',),
    ("apidiff -incompatible old new > out.txt", "if [ -s out.txt ]; then false; fi || true"),
    ("apidiff -incompatible old new > out.txt; if [ -s out.txt ]; then false; fi || true",),
])
def test_a_check_rescued_by_a_passing_fallback_does_not_count(tmp_path: Path, lines: tuple[str, ...]) -> None:
    """``|| true`` after ``fi`` rescues the ``false``: the step stays green on a break, in either shell."""
    step = "      - run: |\n" + "".join(f"          {line}\n" for line in lines)
    assert _block(_repo(tmp_path, {WF: _workflow(extra=step)}))["runs"] == []
    script = _repo(tmp_path / "s", {"hack/api.sh": "#!/bin/sh\n" + "".join(f"{line}\n" for line in lines),
                                    WF: _workflow("hack/api.sh")})
    assert _block(script)["runs"] == []
