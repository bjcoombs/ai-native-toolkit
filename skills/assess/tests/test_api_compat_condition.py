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
              "apidiff -m -incompatible \"$BASE\" ./pkg/widget\n")


# --- named-tool arm ------------------------------------------------------------------------

def test_an_apidiff_script_run_from_a_workflow_step_meets(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"hack/apidiff.sh": APIDIFF_SH, WF: _workflow("hack/apidiff.sh")})
    block = _block(root)
    assert block["runs"] == [{"path": WF, "tool": "apidiff"}]
    assert (block["present"], block["tools_run"], block["declared_only"]) == (True, 1, 0)
    assert _state(root) == "met"


def test_a_make_target_that_runs_apidiff_is_followed(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"Makefile": "api-check:\n\tgo run golang.org/x/exp/cmd/apidiff@v0.0.0 old new\n",
                            WF: _workflow("make api-check")})
    assert _block(root)["runs"] == [{"path": WF, "tool": "apidiff"}]


@pytest.mark.parametrize(("line", "tool"), [
    ("npx --yes @microsoft/api-extractor run", "api-extractor"),
    ("pnpm exec api-extractor run --verbose", "api-extractor"),
    ("uv run --locked griffe check widgets -a v1.0.0", "griffe"),
    ("cargo semver-checks check-release", "cargo-semver-checks"),
    ("buf breaking --against '.git#branch=main'", "buf-breaking"),
    ("java -jar tools/japicmp-0.18.jar -o old.jar -n new.jar", "japicmp"),
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


def test_a_maven_japicmp_goal_bound_to_verify_meets(tmp_path: Path) -> None:
    pom = ("<project><modelVersion>4.0.0</modelVersion><artifactId>widget</artifactId><build><plugins>"
           "<plugin><groupId>com.github.siom79.japicmp</groupId><artifactId>japicmp-maven-plugin</artifactId>"
           "<executions><execution><goals><goal>cmp</goal></goals></execution></executions></plugin>"
           "</plugins></build></project>")
    root = _repo(tmp_path, {"pom.xml": pom, WF: _workflow("mvn -B verify")})
    assert _block(root)["runs"] == [{"path": WF, "tool": "japicmp"}]
    early = _repo(tmp_path / "early", {"pom.xml": pom, WF: _workflow("mvn -B test")})
    assert _block(early)["runs"] == []


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
])
def test_a_test_that_names_all_without_pinning_it_does_not_count(tmp_path: Path, body: str) -> None:
    root = _repo(tmp_path, {"tests/test_imports.py": body})
    assert _block(root, TESTS_RUN)["pins"] == []


def test_an_exports_snapshot_in_a_js_test_pins(tmp_path: Path) -> None:
    js = "import * as api from '../src';\ntest('exports', () => {\n  expect(Object.keys(api)).toMatchSnapshot();\n});\n"
    commented = "// expect(Object.keys(api)).toMatchSnapshot();\n"
    root = _repo(tmp_path, {"src/index.test.ts": js, "src/other.test.ts": commented,
                            "tests/fixtures/index.test.ts": js})
    assert _block(root, TESTS_RUN)["pins"] == ["src/index.test.ts"]


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
    "api:\n  when: never\n  script:\n    - apidiff old new\n",
    "api:\n  rules:\n    - when: never\n  script:\n    - apidiff old new\n",
    "api:\n  allow_failure: true\n  script:\n    - apidiff old new\n",
    ".api:\n  script:\n    - apidiff old new\n",
])
def test_gitlab_jobs_that_never_run_or_cannot_fail_do_not_count(tmp_path: Path, job: str) -> None:
    root = _repo(tmp_path, {".gitlab-ci.yml": job})
    assert _block(root)["runs"] == []


def test_a_gitlab_hidden_job_counts_through_extends(tmp_path: Path) -> None:
    ci = ".api:\n  script:\n    - apidiff old new\napi:\n  extends: .api\n"
    root = _repo(tmp_path, {".gitlab-ci.yml": ci})
    assert _block(root)["runs"] == [{"path": ".gitlab-ci.yml", "tool": "apidiff"}]


def test_a_gitlab_hidden_job_taken_by_reference_reads_not_measured(tmp_path: Path) -> None:
    ci = ".api:\n  script:\n    - apidiff old new\napi:\n  script: !reference [.api, script]\n"
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
    assert out["declared"] == [{"path": "buf.yaml", "tool": "buf-breaking"}]


def test_counts_only_keeps_the_present_reason(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"tests/test_imports.py": PIN_TEST})
    out = counts_only({"api_compat": _block(root)})["api_compat"]
    assert out["present_reason"] == "tests_not_read"


def test_tool_of_reads_nothing_from_an_empty_command() -> None:
    assert tool_of([]) is None and tool_of(["go", "run", "./cmd/server"]) is None
