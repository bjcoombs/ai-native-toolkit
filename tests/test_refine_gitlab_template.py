"""The GitLab refiner template's guardrails, mirroring the GitHub workflows' (issue #1327).

`templates/refine.yml` is included with one `include:` line. These tests pin
what `tests/test_refine_workflows.py` pins for the reusable workflows: the
same `refine.py` entry point, the kill switch, the dry-run default, the model
in a job with a read-only token only, a separate apply job with no model that
holds the write token and validates the plan, the model's allow-listed tools,
the call caps, and the toolkit pinned to a release tag or a full commit sha.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest
import yaml  # required: the plugin contract job installs pyyaml
from refine_workflow_expr import github_caps

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "templates" / "refine.yml"
WORKFLOWS = REPO / ".github" / "workflows"
SCRIPTS = REPO / "skills" / "refine" / "scripts"
WRITE_SCRIPTS = {p.name for p in SCRIPTS.glob("*.sh")} - {"gh.sh", "check-model-run.sh"}
DISALLOWED = {"WebFetch", "WebSearch", "Write", "Edit", "MultiEdit", "NotebookEdit"}
JOBS = ("refine-verdict", "refine-plan", "refine-apply")


def _docs() -> Tuple[Dict[str, Any], Dict[str, Any]]:
    spec, jobs = yaml.safe_load_all(TEMPLATE.read_text(encoding="utf-8"))
    return spec["spec"]["inputs"], jobs


def _job(name: str) -> Dict[str, Any]:
    """A job with `.refine-base` merged in, as `extends` does (variables deep-merged)."""
    _, jobs = _docs()
    base, job = jobs[".refine-base"], jobs[name]
    assert job["extends"] == ".refine-base"
    merged = {**base, **job}
    merged["variables"] = {**base.get("variables", {}), **job.get("variables", {})}
    return merged


def _script(name: str) -> str:
    return "\n".join(str(line) for line in _job(name)["script"])


def _tag() -> str:
    return "v" + json.loads((REPO / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]


# ── shape, pin, kill switch, dry-run ─────────────────────────────────────

def test_three_jobs_and_one_hidden_base() -> None:
    _, jobs = _docs()
    assert set(jobs) == {".refine-base", *JOBS}


def test_toolkit_pin_follows_the_plugin_version() -> None:
    inputs, _ = _docs()
    assert inputs["toolkit_ref"]["default"] == _tag()
    assert re.fullmatch(inputs["toolkit_ref"]["regex"], _tag())
    assert not re.fullmatch(inputs["toolkit_ref"]["regex"], "main")
    header = TEMPLATE.read_text(encoding="utf-8").split("spec:", 1)[0]
    assert f"ai-native-toolkit/{_tag()}/templates/refine.yml" in header


def test_the_code_is_chosen_by_inputs_never_by_a_variable() -> None:
    before = "\n".join(_job("refine-apply")["before_script"])
    assert "'$[[ inputs.toolkit_ref ]]'" in before and "'$[[ inputs.toolkit_url ]]'" in before
    for name in JOBS:
        assert not {k for k in _job(name)["variables"] if "TOOLKIT" in k}, name


def test_tools_are_installed_at_pinned_versions() -> None:
    inputs, _ = _docs()
    assert re.fullmatch(r"[0-9a-f]{64}", inputs["glab_sha256"]["default"])
    assert re.fullmatch(r"\d+\.\d+\.\d+", inputs["glab_version"]["default"])
    assert re.fullmatch(r"\d+\.\d+\.\d+", inputs["claude_code_version"]["default"])
    before = "\n".join(_job("refine-verdict")["before_script"])
    assert "sha256sum --check --strict" in before
    assert '"@anthropic-ai/claude-code@$[[ inputs.claude_code_version ]]"' in _script("refine-plan")


@pytest.mark.parametrize("name", JOBS)
def test_kill_switch_comes_first_and_jobs_run_on_the_default_branch_only(name: str) -> None:
    rules = _job(name)["rules"]
    assert rules[0] == {"if": '$REFINE_ENABLED != "true"', "when": "never"}
    assert rules[1]["if"].startswith("$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH &&")
    assert "merge_request_event" not in json.dumps(rules) and "push" not in json.dumps(rules)
    assert len(rules) == 2


@pytest.mark.parametrize("name", JOBS)
def test_dry_run_is_the_default_and_settings_are_variables(name: str) -> None:
    variables = _job(name)["variables"]
    assert variables["REFINE_MODE"] == "dry-run"
    assert variables["REFINE_PLATFORM"] == "gitlab"
    assert {"REFINE_READY_LABEL", "REFINE_TRIAGE_LABEL", "REFINE_ALLOWED_LABELS", "REFINE_CLOSE_CAP",
            "REFINE_MAX_POINTS", "REFINE_BUILDER_SLOTS", "REFINE_MODEL"} <= set(variables)
    assert "REFINE_ENABLED" not in variables, "the kill switch has no default that could turn it on"
    assert isinstance(_job(name)["timeout"], str)


def test_defaults_match_the_github_workflow_inputs() -> None:
    variables = _job("refine-apply")["variables"]
    triage = yaml.safe_load((WORKFLOWS / "reusable-refine-triage.yml").read_text(encoding="utf-8"))
    inputs = triage[True]["workflow_call"]["inputs"]
    assert variables["REFINE_ALLOWED_LABELS"] == inputs["allowed_labels"]["default"]
    assert variables["REFINE_READY_LABEL"] == inputs["ready_label"]["default"]
    assert variables["REFINE_MODEL"] == inputs["model"]["default"]
    assert int(variables["REFINE_MAX_POINTS"]) == inputs["max_points"]["default"]


# ── the same entry point as the GitHub workflow ─────────────────────────

REFINE_CALL = re.compile(r"python3 (\S+/refine\.py) ")


def test_verdict_job_calls_the_github_workflows_refine_py() -> None:
    github = {m for name in ("reusable-refine-sweep.yml", "reusable-refine-triage.yml")
              for m in REFINE_CALL.findall((WORKFLOWS / name).read_text(encoding="utf-8"))}
    ours = REFINE_CALL.findall(_script("refine-verdict"))
    assert ours and set(ours) == github == {".refine-toolkit/skills/refine/scripts/refine.py"}
    assert "--platform gitlab" in _script("refine-verdict")
    for name in ("refine-plan", "refine-apply"):
        assert not REFINE_CALL.findall(_script(name)), f"{name} reads the verdict job's artifact"


# ── the model never holds a write token ──────────────────────────────────

def test_only_the_plan_job_runs_the_model() -> None:
    assert [n for n in JOBS if "claude -p" in _script(n)] == ["refine-plan"]
    for name in ("refine-verdict", "refine-apply"):
        assert "npm " not in _script(name) and "claude" not in _script(name)


@pytest.mark.parametrize("name", ["refine-verdict", "refine-plan"])
def test_read_jobs_hold_only_the_read_token_and_refuse_the_write_token(name: str) -> None:
    job = _job(name)
    assert job["variables"]["GITLAB_TOKEN"] == "$REFINE_READ_TOKEN"
    assert "environment" not in job, "the write token is scoped to the refine-apply environment"
    assert "CLAUDE_CODE_SCRIPT_CAPS" not in _script(name)
    script = _script(name)
    guard = script.index('if [ -n "${REFINE_WRITE_TOKEN:-}" ]; then')
    assert "exit 1" in script[guard:guard + 200]
    assert guard < script.find("python3")


def test_model_step_guardrails() -> None:
    script = _script("refine-plan")
    allowed = re.search(r'--allowedTools "([^"]+)"', script)
    assert allowed and allowed.group(1).split(",") == [
        "Bash(./.refine-toolkit/skills/refine/scripts/gh.sh:*)", "Read(./.refine/**)"]
    disallowed = re.search(r'--disallowedTools "([^"]+)"', script)
    assert disallowed and set(disallowed.group(1).split(",")) >= DISALLOWED
    assert "--permission-mode" not in script and "--dangerously" not in script
    assert "--max-turns" in script and "--json-schema" in script
    assert "env -u REFINE_WRITE_TOKEN -u CI_JOB_TOKEN" in script
    assert "timeout 600 " in script
    assert "check-model-run.sh" in script
    assert '"/$1 REPO: $CI_PROJECT_PATH ISSUE: $2 VERDICT: .refine/verdict.json"' in script
    assert "run_model triage-issue" in script and "run_model dedupe" in script


def test_model_runs_only_for_a_trusted_author_with_a_credential() -> None:
    script = _script("refine-plan")
    assert 'if [ -z "${REFINE_ANTHROPIC_API_KEY:-}" ]' in script
    trusted = script.index("trusted-author")
    assert trusted < script.index("run_model triage-issue \"$n\"")


def test_the_plan_job_hands_over_plans_only() -> None:
    plan = _job("refine-plan")
    assert plan["needs"] == ["refine-verdict"]
    assert plan["artifacts"]["paths"] == [".refine/plans/", ".refine/model/"], "never the verdict"
    assert plan["allow_failure"] == {"exit_codes": [7]}


# ── the apply job: no model, write token, schema-checked plans ──────────

def test_apply_job_holds_the_write_token_in_its_environment() -> None:
    job = _job("refine-apply")
    assert job["environment"]["name"] == "refine-apply"
    assert job["variables"]["GITLAB_TOKEN"] == "$REFINE_WRITE_TOKEN"
    assert job["resource_group"] == "refine-apply"
    assert {n["job"] for n in job["needs"]} == {"refine-verdict", "refine-plan"}


def test_write_token_is_named_only_by_the_apply_job_and_the_guards() -> None:
    for name in ("refine-verdict", "refine-plan"):
        mentions = [line for line in _script(name).splitlines() if "REFINE_WRITE_TOKEN" in line]
        assert all("-n \"${REFINE_WRITE_TOKEN:-}\"" in m or "reached" in m or "env -u" in m for m in mentions), mentions


def test_apply_runs_only_the_refine_scripts_and_validates_plans() -> None:
    script = _script("refine-apply")
    for banned in ("pip ", "pip3 ", "uv ", "npm ", "make ", "claude", "refine.py "):
        assert banned not in script, banned
    assert '--plan ".refine/plans/$n-triage-issue.json"' in script
    assert '--plan ".refine/plans/$n-dedupe.json"' in script
    assert '"$APPLY" sweep --verdict' in script
    assert 'if [ "$REFINE_MODE" = "live" ]; then' in script, "live mode needs the write token"


def test_plan_files_written_are_the_ones_applied() -> None:
    plan, apply = _script("refine-plan"), _script("refine-apply")
    assert '".refine/plans/$2-$1.json"' in plan
    for command in ("triage-issue", "dedupe"):
        assert f".refine/plans/$n-{command}.json" in apply


CLOSE_CAP_SPLICE = "'\"${REFINE_CLOSE_CAP:-10}\"'"


def _caps(script: str, close_cap: int = 10) -> List[Dict[str, int]]:
    script = script.replace(CLOSE_CAP_SPLICE, str(close_cap))
    return [json.loads(m) for m in re.findall(r"CLAUDE_CODE_SCRIPT_CAPS='([^']+)'", script)]


def test_call_caps_name_real_scripts_and_match_github() -> None:
    caps = _caps(_script("refine-apply"))
    assert len(caps) == 3
    for cap in caps:
        assert cap and set(cap) <= WRITE_SCRIPTS and all(isinstance(v, int) and v >= 1 for v in cap.values())
    github = {}
    for name, job in (("reusable-refine-triage.yml", "apply"), ("reusable-refine-dedupe.yml", "apply"),
                      ("reusable-refine-sweep.yml", "sweep")):
        doc = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
        github[name] = json.loads(github_caps(doc["jobs"][job]["env"]["CLAUDE_CODE_SCRIPT_CAPS"], 10))
    assert caps == list(github.values())


def test_sweep_close_cap_follows_refine_close_cap() -> None:
    """An adopter who raises REFINE_CLOSE_CAP gets that many closes per sweep, not 10 (#1345)."""
    script = _script("refine-apply")
    assert script.count(CLOSE_CAP_SPLICE) == 1
    assert _caps(script, 20)[2]["close-duplicate.sh"] == 20


@pytest.mark.skipif(sys.platform == "win32" or shutil.which("bash") is None, reason="the job runs bash")
@pytest.mark.parametrize("value, expected", [("", 10), ("25", 25)])
def test_sweep_caps_line_runs_with_an_empty_close_cap(value: str, expected: int) -> None:
    """An empty REFINE_CLOSE_CAP variable falls back to 10, as Settings.close_cap does."""
    line = next(ln.strip() for ln in _script("refine-apply").splitlines() if CLOSE_CAP_SPLICE in ln)
    proc = subprocess.run(["bash", "-c", line + '; printf %s "$CLAUDE_CODE_SCRIPT_CAPS"'],
                          env={"PATH": os.environ["PATH"], "REFINE_CLOSE_CAP": value},
                          capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    assert json.loads(proc.stdout)["close-duplicate.sh"] == expected


# ── the pin, run ─────────────────────────────────────────────────────────

needs_bash = pytest.mark.skipif(sys.platform == "win32" or shutil.which("bash") is None,
                                reason="the step runs under bash on Linux runners")


def _run_pin(tmp_path: Path, ref: str) -> int:
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    (stub / "git").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (stub / "git").chmod(0o755)
    step = _job("refine-verdict")["before_script"][0].replace("$[[ inputs.toolkit_ref ]]", ref)
    step = step.replace("$[[ inputs.toolkit_url ]]", "https://example.invalid/toolkit.git")
    env = {"PATH": f"{stub}{os.pathsep}{os.environ['PATH']}"}
    proc = subprocess.run(["bash", "-c", "set -e\n" + step], env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=False, cwd=tmp_path)
    return proc.returncode


SHA = "4411a9e9b0283becf4b924f7bce360fd7756c78c"


@needs_bash
@pytest.mark.parametrize("ref", ["main", "refs/heads/main", "v3.0", "4411a9e", "", f"{SHA}; curl evil", SHA.upper(),
                                 "v1.2.3-rc1"])
def test_a_branch_or_loose_ref_fails_the_job(tmp_path: Path, ref: str) -> None:
    assert _run_pin(tmp_path, ref) == 1


@needs_bash
@pytest.mark.parametrize("ref", [SHA, "v4.0.0"])
def test_a_full_sha_or_release_tag_passes(tmp_path: Path, ref: str) -> None:
    assert _run_pin(tmp_path, ref) == 0


@pytest.mark.skipif(shutil.which("jq") is None, reason="the plan job builds the schema with jq")
@pytest.mark.parametrize("schema", ["triage-plan.schema.json", "dedupe-plan.schema.json"])
def test_plan_schema_drops_the_meta_schema_key(schema: str) -> None:
    """run_model passes --json-schema "$(jq -c <filter> "$3")" (issue #1478).

    Claude Code rejects the draft 2020-12 meta-schema URI the schema files
    name, so the filter must drop the $schema key.
    """
    lines = [ln.strip() for ln in _script("refine-plan").splitlines() if ln.strip().startswith("--json-schema")]
    assert len(lines) == 1
    line = lines[0]
    assert line.startswith('--json-schema "$(jq -c ') and line.endswith(' "$3")" > "$log"')
    jq_filter = line.split("jq -c ", 1)[1].split(" ", 1)[0].strip("'")
    source = json.loads((SCRIPTS / schema).read_text(encoding="utf-8"))
    assert "$schema" in source, "the test proves nothing once the source drops $schema"
    out = subprocess.run(["jq", "-c", jq_filter, str(SCRIPTS / schema)], capture_output=True,
                         encoding="utf-8", errors="replace", check=True).stdout
    assert "'" not in out
    assert json.loads(out) == {k: v for k, v in source.items() if k != "$schema"}
