"""The GitLab CI template and job script for the regression gate (#1152).

``templates/assess-gate.yml`` (repository root, CI/CD component layout) clones
the toolkit at a pinned tag and runs ``scripts/gitlab_gate.sh``, the GitLab
twin of ``action.yml``. GitLab itself is not available here, so these tests:

- check the template's structure: a ``spec:inputs`` header, then one job on
  merge request pipelines with full history, which runs the job script;
- hold the template and script to the Action's contract: the same config
  default, the same pins, the same three render commands and the gate;
- run the job script on a fixture project with a stubbed ``curl``: it passes
  on the committed baseline, fails when CI stops running the tests, and a
  missing or refused token for the merge request note never fails the job.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ASSESS = Path(__file__).resolve().parents[1]
REPO = ASSESS.parents[1]
TEMPLATE = REPO / "templates" / "assess-gate.yml"
SCRIPT = ASSESS / "scripts" / "gitlab_gate.sh"
ACTION = REPO / "action.yml"
PLUGIN = REPO / ".claude-plugin" / "plugin.json"
README = REPO / "README.md"


def _docs() -> list[dict]:
    return list(yaml.safe_load_all(TEMPLATE.read_text(encoding="utf-8")))


def _spec() -> dict:
    return _docs()[0]["spec"]["inputs"]


def _job() -> dict:
    jobs = _docs()[1]
    assert list(jobs) == ["assess-gate"]
    return jobs["assess-gate"]


def _version() -> str:
    return json.loads(PLUGIN.read_text(encoding="utf-8"))["version"]


# --- template structure ---------------------------------------------------

def test_template_is_a_component_with_spec_inputs():
    """``templates/<name>.yml`` with a ``spec:`` header then ``---`` is the CI/CD
    component layout; the same file works with ``include: remote:``."""
    docs = _docs()
    assert len(docs) == 2
    assert set(docs[0]) == {"spec"}
    assert set(_spec()) == {"config", "fail_on_gate_loss", "mr_note", "toolkit_ref",
                            "toolkit_url", "image", "stage"}
    for name, spec in _spec().items():
        assert spec.get("description"), name
        assert "default" in spec, name
    assert _spec()["fail_on_gate_loss"]["type"] == "boolean"
    assert _spec()["mr_note"]["type"] == "boolean"


def test_every_input_is_used_and_every_reference_is_an_input():
    text = TEMPLATE.read_text(encoding="utf-8").split("\n---\n", 1)[1]
    used = set(re.findall(r"\$\[\[\s*inputs\.(\w+)\s*\]\]", text))
    assert used == set(_spec())


def test_job_runs_on_merge_requests_with_full_history():
    job = _job()
    assert job["rules"] == [{"if": '$CI_PIPELINE_SOURCE == "merge_request_event"'}]
    assert job["variables"]["GIT_DEPTH"] == "0"
    assert "allow_failure" not in job  # the gate's verdict must be able to fail the pipeline


def test_job_runs_the_toolkit_script_and_degrades_a_failed_fetch():
    script = "\n".join(_job()["script"])
    assert 'git clone --quiet --depth 1 --branch "$ASSESS_TOOLKIT_REF" "$ASSESS_TOOLKIT_URL"' in script
    # Inputs reach the shell through quoted variables, never interpolated into script:.
    assert "$[[" not in script
    assert _job()["variables"]["ASSESS_TOOLKIT_REF"] == "$[[ inputs.toolkit_ref ]]"
    assert "skills/assess/scripts/gitlab_gate.sh" in script
    # A failed fetch or a toolkit without the script is a notice and exit 0.
    assert script.count("exit 0") == 2


def test_config_input_mirrors_the_action():
    action = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
    assert _spec()["config"]["default"] == action["inputs"]["config"]["default"]


def test_both_hosts_expose_fail_on_gate_loss_with_different_defaults():
    """One shared option: off in the Action (warn-only), on in the GitLab template."""
    action = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
    assert action["inputs"]["fail_on_gate_loss"]["default"] == "false"
    assert _spec()["fail_on_gate_loss"]["default"] is True


def test_toolkit_pin_follows_the_plugin_version():
    """The release PR moves this pin with plugin.json: the template at tag vX runs vX."""
    tag = f"v{_version()}"
    assert _spec()["toolkit_ref"]["default"] == tag
    include = f"ai-native-toolkit/{tag}/templates/assess-gate.yml"
    assert include in TEMPLATE.read_text(encoding="utf-8")
    assert include in README.read_text(encoding="utf-8")


def test_template_is_git_tracked():
    """The repo .gitignore starts with /*; the root templates/ dir is allowlisted."""
    out = subprocess.run(["git", "-C", str(REPO), "check-ignore", "-q", str(TEMPLATE)],
                         capture_output=True, encoding="utf-8", errors="replace")
    assert out.returncode == 1


# --- job script contract (drift against action.yml) ------------------------

def _pin(pattern: str, text: str) -> str:
    m = re.search(pattern, text)
    assert m, pattern
    return m.group(1)


def test_script_pins_match_the_action():
    action = ACTION.read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")
    assert _pin(r"RG_VERSION=(\S+)", script) == _pin(r"ripgrep/releases/download/([\d.]+)/", action)
    assert _pin(r"UV_VERSION=(\S+)", script) == _pin(r"astral\.sh/uv/([\d.]+)/", action)
    assert "latest" not in script


def test_script_runs_the_same_render_and_gate_as_the_action():
    action = ACTION.read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")
    for cmd in ("uv run scripts/complexity-treemap.py", "uv run scripts/assess_core.py",
                "uv run scripts/assess_report.py", "uv run scripts/assess_gate.py"):
        assert cmd in action and cmd in script, cmd


# --- the job script on a fixture project -----------------------------------

needs_bash = pytest.mark.skipif(sys.platform == "win32", reason="the job script runs on Linux GitLab runners")

CI_TESTS = "test:\n  script:\n    - pytest\n"
CI_NO_TESTS = "build:\n  script:\n    - python -c \"print('built')\"\n"
INCLUDE = ("include:\n  - remote: https://raw.githubusercontent.com/bjcoombs/ai-native-toolkit/"
           "v3.0.0/templates/assess-gate.yml\n")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   encoding="utf-8", errors="replace")


def _commit_all(repo: Path, msg: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qm", msg)


def _stub_curl(tmp_path: Path, status: str) -> Path:
    """A ``curl`` that records its arguments and answers with ``status``."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    curl = bin_dir / "curl"
    curl.write_text(f'#!/usr/bin/env bash\nprintf "%s\\0" "$@" > "{tmp_path}/curl.args"\n'
                    f'printf "{status}"\n', encoding="utf-8")
    curl.chmod(0o755)
    return bin_dir


def _run(project: Path, tmp_path: Path, status: str = "201", **env: str) -> subprocess.CompletedProcess[str]:
    base = {k: v for k, v in os.environ.items() if not k.startswith(("CI_", "ASSESS_"))}
    base["PATH"] = f"{_stub_curl(tmp_path, status)}{os.pathsep}{base['PATH']}"
    base.update({"ASSESS_PROJECT_DIR": str(project), "GIT_CONFIG_GLOBAL": os.devnull, **env})
    return subprocess.run(["bash", str(SCRIPT)], capture_output=True, encoding="utf-8",
                          errors="replace", env=base, timeout=600)


def _make_project(repo: Path, include: str) -> Path:
    """A small Python project at Step 3: instructions, and CI running pytest."""
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / "AGENTS.md").write_text("Run `pytest` before committing.\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_calc.py").write_text(
        "from src.calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text('[project]\nname = "fx"\nversion = "0.1.0"\n', encoding="utf-8")
    (repo / ".gitignore").write_text(".assess/run-context*.json\n.assess/complexity-stats.prior.json\n"
                                     ".assess/.cache/\n", encoding="utf-8")
    (repo / ".gitlab-ci.yml").write_text(include + CI_TESTS, encoding="utf-8")
    _commit_all(repo, "init")
    return repo


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    return _make_project(tmp_path_factory.mktemp("gitlab-project"), INCLUDE)


@needs_bash
def test_fixture_passes_on_the_baseline_and_fails_when_ci_stops_running_tests(project, tmp_path):
    # The first run has no baseline: nothing to lose, so it passes and writes one.
    first = _run(project, tmp_path / "a")
    assert first.returncode == 0, first.stdout + first.stderr
    assert "Step 3 of 4" in first.stdout
    assert "RESULT: PASS" in first.stdout
    baseline = json.loads((project / ".assess" / "prior-level.json").read_text(encoding="utf-8"))
    assert "ci_runs_tests" in baseline["baseline"]["held_gates"]
    _commit_all(project, "assess baseline")

    # The baseline commit itself: every gate still holds.
    again = _run(project, tmp_path / "b")
    assert again.returncode == 0, again.stdout + again.stderr
    assert "gate lost" not in again.stdout

    # CI stops running the tests: the merge request fails on the lost gate.
    (project / ".gitlab-ci.yml").write_text(INCLUDE + CI_NO_TESTS, encoding="utf-8")
    _commit_all(project, "drop the test job")
    lost = _run(project, tmp_path / "c")
    assert lost.returncode == 1, lost.stdout + lost.stderr
    assert "FAIL gate lost: ci_runs_tests" in lost.stdout
    assert "(Step 3 -> Step 2)" in lost.stdout
    assert "RESULT: FAIL" in lost.stdout

    # The same loss with the input turned off is a warn line and a pass.
    warn = _run(project, tmp_path / "d", ASSESS_FAIL_ON_GATE_LOSS="false")
    assert warn.returncode == 0
    assert "warn gate lost: ci_runs_tests" in warn.stdout


@needs_bash
def test_note_posts_with_a_provided_token(project, tmp_path):
    out = _run(project, tmp_path, "201", CI_MERGE_REQUEST_IID="7", CI_API_V4_URL="https://gl.example/api/v4",
               CI_PROJECT_ID="41", CI_MERGE_REQUEST_PROJECT_ID="42", ASSESS_GATE_TOKEN="secret-token",
               ASSESS_FAIL_ON_GATE_LOSS="false")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "posted the summary as a merge request note" in out.stdout
    assert "secret-token" not in out.stdout + out.stderr
    args = (tmp_path / "curl.args").read_text(encoding="utf-8").split("\0")
    assert "PRIVATE-TOKEN: secret-token" in args
    assert "https://gl.example/api/v4/projects/42/merge_requests/7/notes" in args
    assert args[args.index("--max-time") + 1] == "30"
    body = args[args.index("--form-string") + 1]
    assert body.startswith("body=#### AI-readiness gate") and "/assess gate" in body
    assert "Resolved" not in body and "Installed" not in body  # no uv progress from stderr


@needs_bash
@pytest.mark.parametrize("env,expect", [
    ({}, "no token is available"),
    ({"CI_JOB_TOKEN": "job"}, "was not posted (HTTP 403)"),
])
def test_note_without_a_working_token_never_fails_the_job(project, tmp_path, env, expect):
    out = _run(project, tmp_path, "403", CI_MERGE_REQUEST_IID="7", CI_API_V4_URL="https://gl.example/api/v4",
               CI_PROJECT_ID="42", ASSESS_FAIL_ON_GATE_LOSS="false", **env)
    assert out.returncode == 0, out.stdout + out.stderr
    assert expect in out.stdout


@needs_bash
def test_no_note_outside_a_merge_request(project, tmp_path):
    out = _run(project, tmp_path, "201", ASSESS_FAIL_ON_GATE_LOSS="false", CI_JOB_TOKEN="job")
    assert out.returncode == 0
    assert "not a merge request pipeline" in out.stdout
    assert not (tmp_path / "curl.args").exists()


@needs_bash
def test_render_failure_is_a_skip_notice(tmp_path):
    """A project the core cannot render (a file, not a directory) is infrastructure, never a red job."""
    not_a_dir = tmp_path / "a-file"
    not_a_dir.write_text("x", encoding="utf-8")
    out = _run(not_a_dir, tmp_path, ASSESS_FAIL_ON_GATE_LOSS="true")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "infrastructure failure rendering the snapshot" in out.stdout


@needs_bash
def test_another_unreadable_include_reads_not_measured_and_only_warns(tmp_path):
    """The gate's own include is known to run no tests; any other remote include could
    hold them, so removing the local test job reads not measured: a warn, never a fail."""
    other = INCLUDE + "  - remote: https://ci.example.com/shared/jobs.yml\n"
    repo = _make_project(tmp_path / "project", other)
    assert _run(repo, tmp_path / "a").returncode == 0
    _commit_all(repo, "assess baseline")
    (repo / ".gitlab-ci.yml").write_text(other + CI_NO_TESTS, encoding="utf-8")
    _commit_all(repo, "drop the test job")
    out = _run(repo, tmp_path / "b")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "warn no longer measured: ci_runs_tests" in out.stdout
    assert "RESULT: PASS" in out.stdout


@needs_bash
def test_first_run_reports_no_baseline_yet(tmp_path):
    repo = _make_project(tmp_path / "project", INCLUDE)
    out = _run(repo, tmp_path / "a")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "no baseline yet" in out.stdout


def _mr_case(tmp_path: Path, tamper: str) -> tuple[Path, subprocess.CompletedProcess[str]]:
    """main holds the baseline at Step 3; a feature branch drops the test job and
    tampers with the baseline; the job runs as that branch's merge request."""
    repo = _make_project(tmp_path / "project", INCLUDE)
    assert _run(repo, tmp_path / "a").returncode == 0
    _commit_all(repo, "assess baseline")
    _git(repo, "remote", "add", "origin", str(repo))  # the fetch path: no origin/main yet
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / ".gitlab-ci.yml").write_text(INCLUDE + CI_NO_TESTS, encoding="utf-8")
    if tamper == "delete":
        (repo / ".assess" / "prior-level.json").unlink()
    elif tamper == "refreeze":  # /assess run on the branch, its loss committed as the baseline
        _commit_all(repo, "drop the test job")
        _run(repo, tmp_path / "b", ASSESS_FAIL_ON_GATE_LOSS="false")
    _commit_all(repo, "tamper with the baseline")
    return repo, _run(repo, tmp_path / "c", CI_MERGE_REQUEST_TARGET_BRANCH_NAME="main")


@needs_bash
@pytest.mark.parametrize("tamper", ["delete", "refreeze"])
def test_a_change_cannot_switch_off_its_own_gate(tmp_path, tamper):
    _, out = _mr_case(tmp_path, tamper)
    assert out.returncode == 1, out.stdout + out.stderr
    assert "this change edits .assess/prior-level.json" in out.stdout
    assert "comparing with the gate baseline committed on main" in out.stdout
    assert "FAIL gate lost: ci_runs_tests" in out.stdout


@needs_bash
def test_a_target_without_a_baseline_passes_with_a_note(tmp_path):
    repo = _make_project(tmp_path / "project", INCLUDE)
    _git(repo, "update-ref", "refs/remotes/origin/main", "main")
    _git(repo, "checkout", "-q", "-b", "feature")
    out = _run(repo, tmp_path / "a", CI_MERGE_REQUEST_TARGET_BRANCH_NAME="main")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "main has no committed .assess/prior-level.json, so there is no baseline yet" in out.stdout
    assert "no baseline yet: commit" in out.stdout


@needs_bash
@pytest.mark.parametrize("target", ["no-such-branch", "-bad"])
def test_an_unreadable_target_is_a_notice(tmp_path, target):
    repo = _make_project(tmp_path / "project", INCLUDE)
    out = subprocess.run(["bash", str(ASSESS / "scripts" / "base_baseline.sh"), str(repo), target],
                         capture_output=True, encoding="utf-8", errors="replace")
    assert out.returncode == 0
    assert "so the gate compares with the baseline in this branch" in out.stdout
