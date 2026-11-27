"""The refiner workflows' guardrails (issues #1321, #1327).

Three thin callers (`refine-triage.yml`, `refine-dedupe.yml`,
`refine-sweep.yml`) each call one reusable `workflow_call` workflow. These
tests pin the triggers, the kill switch, the dry-run default, the
permissions, the tool limits, the call caps, the concurrency groups and
timeouts, and the `workflow_call` inputs the callers pass.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest
import yaml  # required: the plugin contract job installs pyyaml

REPO = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO / ".github" / "workflows"
SCRIPTS = REPO / "skills" / "refine" / "scripts"
COMMANDS = REPO / "skills" / "refine" / "commands"
CALLERS = {"refine-triage.yml": "reusable-refine-triage.yml",
           "refine-dedupe.yml": "reusable-refine-dedupe.yml",
           "refine-sweep.yml": "reusable-refine-sweep.yml"}
MODEL_WORKFLOWS = ("reusable-refine-triage.yml", "reusable-refine-dedupe.yml")
DISALLOWED = {"WebFetch", "WebSearch", "Write", "Edit", "MultiEdit", "NotebookEdit"}
JOB_PERMISSIONS = {
    "plan": {"contents": "read", "issues": "read", "pull-requests": "read", "id-token": "write"},
    "apply": {"contents": "read", "issues": "write"},
    "reopen": {"contents": "read", "issues": "write"},
    "sweep": {"contents": "read", "issues": "write", "pull-requests": "read"},
    "project": {"contents": "read", "issues": "read", "pull-requests": "read"},
}
# A scope a job may hold as write without being able to change the repository:
# the OIDC token Workload Identity Federation exchanges for a Claude API token.
NOT_A_REPO_WRITE = {"id-token"}
WRITE_SCRIPTS = {p.name for p in SCRIPTS.glob("*.sh")} - {"gh.sh", "check-model-run.sh"}


def _load(name: str) -> Dict[str, Any]:
    doc: Dict[str, Any] = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    doc["on"] = doc.pop(True, doc.get("on"))  # PyYAML reads the bare key `on` as True
    return doc


def _steps(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [s for job in doc["jobs"].values() for s in job.get("steps", [])]


def _model_step(name: str) -> Dict[str, Any]:
    found = [s for s in _steps(_load(name)) if str(s.get("uses", "")).startswith("anthropics/claude-code-action@")]
    assert len(found) == 1, name
    return found[0]


# ── callers ──────────────────────────────────────────────────────────────

def test_caller_triggers() -> None:
    assert _load("refine-triage.yml")["on"] == {"issues": {"types": ["opened"]},
                                                "issue_comment": {"types": ["created"]}}
    dedupe = _load("refine-dedupe.yml")["on"]
    assert set(dedupe) == {"issues", "workflow_dispatch"} and dedupe["issues"] == {"types": ["opened"]}
    sweep = _load("refine-sweep.yml")["on"]
    assert set(sweep) == {"schedule", "workflow_dispatch"}
    hours = sweep["schedule"][0]["cron"].split()[1]
    assert len(hours.split(",")) == 2, "the sweep runs twice a day"


@pytest.mark.parametrize("caller", sorted(CALLERS))
def test_caller_permissions_are_issues_write_and_reads_only(caller: str) -> None:
    doc = _load(caller)
    expected = {"contents": "read", "issues": "write", "pull-requests": "read"}
    if caller != "refine-sweep.yml":
        expected["id-token"] = "write"  # the plan job's OIDC token for Workload Identity Federation
    assert doc["permissions"] == expected
    for job in doc["jobs"].values():
        assert "permissions" not in job


@pytest.mark.parametrize("caller", sorted(CALLERS))
def test_caller_is_one_uses_line_with_the_kill_switch(caller: str) -> None:
    jobs = _load(caller)["jobs"]
    assert len(jobs) == 1
    job = next(iter(jobs.values()))
    assert job["uses"] == f"./.github/workflows/{CALLERS[caller]}"
    assert "steps" not in job and "runs-on" not in job, "no duplicated job logic in the caller"
    assert "vars.REFINE_ENABLED == 'true'" in job["if"]
    assert job["with"]["enabled"] == "${{ vars.REFINE_ENABLED == 'true' }}"
    assert job["with"]["mode"] == "${{ vars.REFINE_MODE || 'dry-run' }}"


@pytest.mark.parametrize("caller", sorted(CALLERS))
def test_caller_inputs_exist_in_the_reusable_workflow(caller: str) -> None:
    job = next(iter(_load(caller)["jobs"].values()))
    call = _load(CALLERS[caller])["on"]["workflow_call"]
    assert set(job["with"]) <= set(call["inputs"])
    required = {k for k, v in call["inputs"].items() if v.get("required")}
    assert required <= set(job["with"])
    assert set(job.get("secrets", {})) <= set(call.get("secrets", {}))


@pytest.mark.parametrize("caller", sorted(CALLERS))
def test_only_refine_and_workflow_token_secrets(caller: str) -> None:
    for name in (caller, CALLERS[caller]):
        text = (WORKFLOWS / name).read_text(encoding="utf-8")
        assert set(re.findall(r"secrets\.(\w+)", text)) <= {"REFINE_ANTHROPIC_API_KEY", "anthropic_api_key",
                                                              "CLAUDE_CODE_OAUTH_TOKEN", "claude_code_oauth_token",
                                                              "REFINE_PROJECT_TOKEN", "project_token"}


def test_triage_caller_skips_bot_comments_and_pull_requests() -> None:
    cond = _load("refine-triage.yml")["jobs"]["triage"]["if"]
    assert "github.event.comment.user.type != 'Bot'" in cond
    assert "!github.event.issue.pull_request" in cond


# ── reusable workflows ───────────────────────────────────────────────────

@pytest.mark.parametrize("name", sorted(CALLERS.values()))
def test_reusable_is_workflow_call_only_and_safe_by_default(name: str) -> None:
    doc = _load(name)
    assert set(doc["on"]) == {"workflow_call"}
    inputs = doc["on"]["workflow_call"]["inputs"]
    assert inputs["enabled"] == {**inputs["enabled"], "type": "boolean", "default": False}
    assert inputs["mode"]["default"] == "dry-run"
    assert "permissions" not in doc
    for job_id, job in doc["jobs"].items():
        assert job["permissions"] == JOB_PERMISSIONS[job_id], job_id
        assert str(job["if"]).lstrip().startswith("inputs.enabled")
        assert isinstance(job["timeout-minutes"], int)
        if job_id != "plan":
            assert job["env"]["REFINE_MODE"] == "${{ inputs.mode }}"


@pytest.mark.parametrize("name", sorted(CALLERS.values()))
def test_reusable_inputs_cover_mode_labels_and_close_cap(name: str) -> None:
    inputs = set(_load(name)["on"]["workflow_call"]["inputs"])
    assert {"enabled", "mode"} <= inputs
    if name != "reusable-refine-dedupe.yml":
        assert {"ready_label", "triage_label", "allowed_labels", "questions_issue"} <= inputs
    if name != "reusable-refine-triage.yml":
        assert "close_cap" in inputs


@pytest.mark.parametrize("name", MODEL_WORKFLOWS)
def test_reusable_credential_inputs(name: str) -> None:
    call = _load(name)["on"]["workflow_call"]
    assert {"anthropic_federation_rule_id", "anthropic_organization_id", "anthropic_service_account_id",
            "anthropic_workspace_id"} <= set(call["inputs"])
    assert call["secrets"]["anthropic_api_key"]["required"] is False
    with_ = _model_step(name)["with"]
    assert with_["anthropic_api_key"] == "${{ secrets.anthropic_api_key }}"
    for key in ("anthropic_federation_rule_id", "anthropic_organization_id", "anthropic_service_account_id",
                "anthropic_workspace_id"):
        assert with_[key] == f"${{{{ steps.gate.outputs.auth == 'wif' && inputs.{key} || '' }}}}", key
    assert call["secrets"]["claude_code_oauth_token"]["required"] is False
    assert with_["claude_code_oauth_token"] == \
        "${{ steps.gate.outputs.auth == 'oauth' && secrets.claude_code_oauth_token || '' }}"


@pytest.mark.parametrize("caller", ["refine-triage.yml", "refine-dedupe.yml"])
def test_callers_pass_the_claude_code_oauth_token(caller: str) -> None:
    secrets = next(iter(_load(caller)["jobs"].values()))["secrets"]
    assert secrets["claude_code_oauth_token"] == "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}"


@pytest.mark.parametrize("name", sorted(CALLERS.values()))
def test_per_issue_concurrency(name: str) -> None:
    for job_id, job in _load(name)["jobs"].items():
        group = job["concurrency"]["group"]
        if name == "reusable-refine-sweep.yml":
            assert job["concurrency"]["cancel-in-progress"] is False
            continue
        assert "${{ inputs.issue_number }}" in group, job_id
        # The plan job is cancelled by a newer event; a job that writes never is.
        assert job["concurrency"]["cancel-in-progress"] is (job_id == "plan")


@pytest.mark.parametrize("name", MODEL_WORKFLOWS)
def test_model_step_guardrails(name: str) -> None:
    step = _model_step(name)
    assert re.fullmatch(r"anthropics/claude-code-action@[0-9a-f]{40}", step["uses"]), "pin the action to a sha"
    assert isinstance(step["timeout-minutes"], int)
    args = step["with"]["claude_args"]
    assert "--permission-mode" not in args, "default mode: a tool off the allow-list is denied, never reviewed"
    allowed = re.search(r'--allowedTools "([^"]+)"', args)
    assert allowed and allowed.group(1).split(",") == [
        "Bash(./.refine-toolkit/skills/refine/scripts/gh.sh:*)", "Read(./.refine/**)"]
    assert "--max-turns" in args and "--json-schema" in args
    disallowed = re.search(r'--disallowedTools "([^"]+)"', args)
    assert disallowed and set(disallowed.group(1).split(",")) >= DISALLOWED
    model = _load(name)["on"]["workflow_call"]["inputs"]["model"]["default"]
    assert "sonnet" in model
    assert "allowed_non_write_users" not in step["with"], "outside contributors get the sweep, not the model"
    assert step["with"]["prompt"].startswith("/")


@pytest.mark.parametrize("name", MODEL_WORKFLOWS)
def test_model_step_gated_on_credential_and_author(name: str) -> None:
    steps = _steps(_load(name))
    gate = next(s for s in steps if s.get("id") == "gate")
    trusted = gate["env"]["TRUSTED"]
    assert "OWNER" in trusted and "COLLABORATOR" in trusted
    assert "github.event.comment.author_association" not in trusted or \
        "github.event.issue.author_association) &&" in trusted, "the issue author must be trusted too"
    assert "github.event.issue.author_association" in trusted
    assert _model_step(name)["if"] == "steps.gate.outputs.run == 'true'"
    final = next(s for s in steps if s.get("name") == "Report final status")
    assert "check-model-run.sh" in final["run"]


@pytest.mark.parametrize("name", sorted(CALLERS.values()))
def test_write_script_caps_name_real_scripts(name: str) -> None:
    for job_id, job in _load(name)["jobs"].items():
        if job_id in ("plan", "project"):
            assert "CLAUDE_CODE_SCRIPT_CAPS" not in job["env"], f"the {job_id} job runs no write script"
            continue
        caps = json.loads(job["env"]["CLAUDE_CODE_SCRIPT_CAPS"])
        assert caps and set(caps) <= WRITE_SCRIPTS
        assert all(isinstance(v, int) and v >= 1 for v in caps.values())
    if name != "reusable-refine-triage.yml":
        job_id = "apply" if name == "reusable-refine-dedupe.yml" else "sweep"
        close = json.loads(_load(name)["jobs"][job_id]["env"]["CLAUDE_CODE_SCRIPT_CAPS"])
        assert close["close-duplicate.sh"] <= 10


@pytest.mark.parametrize("name", sorted(CALLERS.values()))
def test_checkouts_never_persist_credentials(name: str) -> None:
    for step in _steps(_load(name)):
        if str(step.get("uses", "")).startswith("actions/checkout@"):
            assert step["with"]["persist-credentials"] is False


def test_reopen_runs_no_model() -> None:
    reopen = _load("reusable-refine-triage.yml")["jobs"]["reopen"]
    assert "startsWith(github.event.comment.body, '/reopen')" in reopen["if"]
    assert not any("claude-code-action" in str(s.get("uses", "")) for s in reopen["steps"])
    assert json.loads(reopen["env"]["CLAUDE_CODE_SCRIPT_CAPS"]) == {"reopen-duplicate.sh": 1}


def test_sweep_runs_no_model_and_calls_refine_py() -> None:
    doc = _load("reusable-refine-sweep.yml")
    assert not any("claude-code-action" in str(s.get("uses", "")) for s in _steps(doc))
    runs = " ".join(str(s.get("run", "")) for s in _steps(doc))
    assert "skills/refine/scripts/refine.py" in runs and '"$APPLY" sweep' in runs
    assert doc["jobs"]["sweep"]["env"]["APPLY"].endswith("skills/refine/scripts/refine_apply.py")


def test_project_sync_is_skipped_without_a_project_and_holds_no_issue_write() -> None:
    doc = _load("reusable-refine-sweep.yml")
    call = doc["on"]["workflow_call"]
    assert call["inputs"]["project_number"]["default"] == ""
    assert call["secrets"]["project_token"]["required"] is False
    job = doc["jobs"]["project"]
    assert "inputs.project_number != ''" in job["if"] and job["needs"] == "sweep"
    assert job["permissions"]["issues"] == "read", "the project token is the only write credential"
    assert not any("claude-code-action" in str(s.get("uses", "")) for s in job["steps"])
    token_users = [s for s in job["steps"] if "secrets.project_token" in json.dumps(s)]
    assert [s.get("id") or s["name"] for s in token_users] == ["token", "Sync Status, Size and Verdict (no model)"]
    sync = token_users[-1]
    assert "skills/refine/scripts/refine_project.py" in sync["run"]
    assert sync["env"] == {"REFINE_PROJECT_TOKEN": "${{ secrets.project_token }}"}
    assert "project_token" not in json.dumps(job["env"])


def test_this_repository_syncs_its_backlog_project_from_variables() -> None:
    job = _load("refine-sweep.yml")["jobs"]["sweep"]
    assert job["with"]["project_number"] == "${{ vars.REFINE_PROJECT_NUMBER }}"
    assert job["secrets"] == {"project_token": "${{ secrets.REFINE_PROJECT_TOKEN }}"}


# ── prompts and scripts ──────────────────────────────────────────────────

@pytest.mark.parametrize("name, workflow", [("triage-issue", "reusable-refine-triage.yml"),
                                            ("dedupe", "reusable-refine-dedupe.yml")])
def test_command_allows_only_the_read_wrapper(name: str, workflow: str) -> None:
    text = (COMMANDS / f"{name}.md").read_text(encoding="utf-8")
    front = yaml.safe_load(text.split("---")[1])
    tools = [t.strip() for t in front["allowed-tools"].split(", ")]
    assert tools == ["Bash(./.refine-toolkit/skills/refine/scripts/gh.sh:*)", "Read(./.refine/**)"]
    assert _model_step(workflow)["with"]["prompt"].startswith(f"/{name} ")


def test_every_write_script_runs_one_refine_write_subcommand() -> None:
    assert WRITE_SCRIPTS == {"edit-issue-labels.sh", "comment-on-duplicates.sh", "close-duplicate.sh",
                             "write-size-marker.sh", "add-blocked-by.sh", "post-questions.sh",
                             "post-buffer-report.sh", "reopen-duplicate.sh"}
    for name in WRITE_SCRIPTS:
        text = (SCRIPTS / name).read_text(encoding="utf-8")
        assert re.search(r'exec python3 "\$\(dirname "\$0"\)/refine_write\.py" [a-z-]+ "\$@"', text), name


# ── the refinement scripts: pinned to the workflow's own commit ─────────

SPARSE_READS = {"skills/refine/", "docs/contributing/definition-of-ready.md"}
SPARSE_WRITES = {"skills/refine/scripts/"}


def _jobs_steps(name: str) -> "List[tuple[str, List[Dict[str, Any]]]]":
    return [(job_id, job["steps"]) for job_id, job in _load(name)["jobs"].items()]


@pytest.mark.parametrize("name", sorted(CALLERS.values()))
def test_scripts_are_checked_out_at_the_workflow_sha_only(name: str) -> None:
    assert not {"toolkit_repository", "toolkit_ref"} & set(_load(name)["on"]["workflow_call"]["inputs"]), \
        "no input chooses the code that runs with a write token"
    for job_id, steps in _jobs_steps(name):
        assert steps[0]["with"]["ref"] == "${{ github.event.repository.default_branch }}"
        pin = next(s for s in steps if s.get("id") == "toolkit")
        assert pin["env"] == {"TOOLKIT_REPO": "${{ job.workflow_repository }}", "TOOLKIT_SHA": "${{ job.workflow_sha }}"}
        ext = next(s for s in steps if s.get("with", {}).get("path") == ".refine-toolkit")
        assert steps.index(pin) < steps.index(ext)
        assert ext["with"]["repository"] == "${{ job.workflow_repository }}"
        assert ext["with"]["ref"] == "${{ job.workflow_sha }}"
        assert ext["with"]["persist-credentials"] is False
        sparse = set(ext["with"]["sparse-checkout"].split())
        writes = job_id in ("apply", "reopen")
        assert sparse == (SPARSE_WRITES if writes else SPARSE_READS), job_id


@pytest.mark.parametrize("name", sorted(CALLERS.values()))
def test_write_jobs_run_only_the_refine_scripts(name: str) -> None:
    for job_id, steps in _jobs_steps(name):
        runs = " ".join(str(s.get("run", "")) for s in steps)
        for banned in ("pip ", "pip3 ", "uv ", "npm ", "make ", "setup.py"):
            assert banned not in runs, (job_id, banned)
        if job_id in ("apply", "reopen"):
            assert "refine.py" not in runs, "the write job runs only refine_apply.py and the write scripts"


def _pin_step(name: str) -> Dict[str, Any]:
    return next(s for s in _load(name)["jobs"][next(iter(_load(name)["jobs"]))]["steps"] if s.get("id") == "toolkit")


def _run_pin(tmp_path: Path, name: str, repo: str, sha: str, caller: str = "acme/widgets") -> int:
    env = {"PATH": os.environ["PATH"], "GITHUB_REPOSITORY": caller, "TOOLKIT_REPO": repo, "TOOLKIT_SHA": sha}
    proc = subprocess.run(["bash", "-c", _pin_step(name)["run"]], env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=False)
    return proc.returncode


needs_bash = pytest.mark.skipif(sys.platform == "win32" or shutil.which("bash") is None,
                                reason="the step runs under bash on Linux runners")
SHA = "4411a9e9b0283becf4b924f7bce360fd7756c78c"


@needs_bash
@pytest.mark.parametrize("name", sorted(CALLERS.values()))
@pytest.mark.parametrize("sha", ["main", "refs/heads/main", "v3.0.0", "4411a9e", "", f"{SHA}; curl evil", SHA.upper()])
def test_branch_name_or_loose_ref_fails_the_job(tmp_path: Path, name: str, sha: str) -> None:
    assert _run_pin(tmp_path, name, "bjcoombs/ai-native-toolkit", sha) == 1


@needs_bash
@pytest.mark.parametrize("name", sorted(CALLERS.values()))
@pytest.mark.parametrize("repo", ["attacker/ai-native-toolkit", "bjcoombs/ai-native-toolkit-fork", ""])
def test_scripts_from_another_repository_fail_the_job(tmp_path: Path, name: str, repo: str) -> None:
    assert _run_pin(tmp_path, name, repo, SHA) == 1


@needs_bash
@pytest.mark.parametrize("name", sorted(CALLERS.values()))
@pytest.mark.parametrize("repo", ["bjcoombs/ai-native-toolkit", "acme/widgets"])
def test_toolkit_or_own_repository_at_a_full_sha_passes(tmp_path: Path, name: str, repo: str) -> None:
    assert _run_pin(tmp_path, name, repo, SHA) == 0


@pytest.mark.parametrize("name", sorted(CALLERS.values()))
def test_no_cache_in_jobs_with_a_write_token(name: str) -> None:
    for step in _steps(_load(name)):
        uses = str(step.get("uses", ""))
        assert not uses.startswith("actions/cache"), uses
        assert not uses.startswith("actions/setup-"), uses
        assert "cache" not in step.get("with", {}), uses


# ── the model never holds a write token (#1336 review) ───────────────────

@pytest.mark.parametrize("name", MODEL_WORKFLOWS)
def test_model_job_has_no_write_scope(name: str) -> None:
    jobs = _load(name)["jobs"]
    model_jobs = [j for j, job in jobs.items()
                  if any("claude-code-action" in str(s.get("uses", "")) for s in job["steps"])]
    assert model_jobs == ["plan"]
    writes = {k for k, v in jobs["plan"]["permissions"].items() if v == "write"} - NOT_A_REPO_WRITE
    assert writes == set()


@pytest.mark.parametrize("name", MODEL_WORKFLOWS)
def test_model_step_sees_only_the_read_only_job_token(name: str) -> None:
    plan = _load(name)["jobs"]["plan"]
    step = _model_step(name)
    assert step["with"]["github_token"] == "${{ github.token }}"
    assert plan["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert "env" not in step
    text = yaml.safe_dump(plan)
    assert "secrets.GITHUB_TOKEN" not in text and "REFINE_MODE" not in text


@pytest.mark.parametrize("name", MODEL_WORKFLOWS)
def test_apply_job_runs_no_model_and_reads_the_plan_artifact(name: str) -> None:
    jobs = _load(name)["jobs"]
    apply = jobs["apply"]
    assert apply["needs"] == "plan" and "needs.plan.result == 'success'" in apply["if"]
    assert not any("claude-code-action" in str(s.get("uses", "")) for s in apply["steps"])
    upload = next(s for s in jobs["plan"]["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact@"))
    download = next(s for s in apply["steps"] if str(s.get("uses", "")).startswith("actions/download-artifact@"))
    assert upload["with"]["name"] == download["with"]["name"]
    assert re.fullmatch(r"actions/download-artifact@[0-9a-f]{40}", download["uses"])
    runs = " ".join(str(s.get("run", "")) for s in apply["steps"])
    assert "--plan .refine/plan.json" in runs, "the plan is validated by refine_apply.py before any write"
    assert 'sha256sum --check --strict' in runs, "apply checks the verdict digest taken before the model ran"
    plan_steps = jobs["plan"]["steps"]
    digest = next(i for i, s in enumerate(plan_steps) if s.get("id") == "digest")
    model = next(i for i, s in enumerate(plan_steps) if s.get("id") == "model")
    assert digest < model


# ── the Claude Code OAuth token stays in the read-only model step ────────

def _uses_oauth(node: Any) -> bool:
    return "claude_code_oauth_token" in json.dumps(node)


@pytest.mark.parametrize("name", MODEL_WORKFLOWS)
def test_oauth_token_reaches_only_the_read_only_model_step(name: str) -> None:
    jobs = _load(name)["jobs"]
    for job_id, job in jobs.items():
        writes = {k for k, v in job["permissions"].items() if v == "write"} - NOT_A_REPO_WRITE
        if job_id != "plan" or writes:
            assert not _uses_oauth(job), f"{job_id} holds a write token or runs no model"
    plan = jobs["plan"]
    assert not _uses_oauth(plan.get("env", {})), "a job-level env would hand the token to every step"
    holders = [s.get("id") for s in plan["steps"] if _uses_oauth(s)]
    assert holders == ["gate", "model"]
    gate = next(s for s in plan["steps"] if s.get("id") == "gate")
    # The gate sees only whether the token is set, never its value.
    assert gate["env"]["HAS_OAUTH"] == "${{ secrets.claude_code_oauth_token != '' }}"
    assert "secrets." not in gate["run"]


def _run_gate(tmp_path: Path, name: str, key: bool, wif: bool, oauth: bool,
              trusted: bool = True, real_schemas: bool = False) -> "tuple[int, Dict[str, str], str]":
    gate = next(s for s in _steps(_load(name)) if s.get("id") == "gate")
    schema = tmp_path / ".refine-toolkit" / "skills" / "refine" / "scripts"
    schema.mkdir(parents=True, exist_ok=True)
    for f in ("triage-plan.schema.json", "dedupe-plan.schema.json"):
        text = (SCRIPTS / f).read_text(encoding="utf-8") if real_schemas else "{}"
        (schema / f).write_text(text, encoding="utf-8")
    out = tmp_path / "out"
    out.write_text("", encoding="utf-8")
    env = {"PATH": os.environ["PATH"], "GITHUB_OUTPUT": str(out), "HAS_KEY": str(key).lower(),
           "HAS_WIF": str(wif).lower(), "HAS_OAUTH": str(oauth).lower(), "TRUSTED": str(trusted).lower()}
    proc = subprocess.run(["bash", "-e", "-c", gate["run"]], env=env, cwd=tmp_path, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", check=False)
    lines = out.read_text(encoding="utf-8").splitlines()
    outputs = dict(line.split("=", 1) for line in lines if "=" in line)
    return proc.returncode, outputs, proc.stdout


@needs_bash
@pytest.mark.skipif(shutil.which("jq") is None, reason="the gate step reads the plan schema with jq")
@pytest.mark.parametrize("name", MODEL_WORKFLOWS)
@pytest.mark.parametrize("key, wif, oauth, auth", [
    (True, True, True, "api_key"), (True, False, True, "api_key"),
    (False, True, True, "wif"), (False, False, True, "oauth")])
def test_gate_picks_key_then_federation_then_oauth(tmp_path: Path, name: str, key: bool, wif: bool,
                                                  oauth: bool, auth: str) -> None:
    code, outputs, _ = _run_gate(tmp_path, name, key, wif, oauth)
    assert code == 0 and outputs["auth"] == auth and outputs["run"] == "true"


@needs_bash
@pytest.mark.parametrize("name", MODEL_WORKFLOWS)
def test_gate_fails_fast_with_no_credential(tmp_path: Path, name: str) -> None:
    code, outputs, stdout = _run_gate(tmp_path, name, False, False, False)
    assert code == 1 and "run" not in outputs
    assert "::error::No refiner credential is set" in stdout and "claude_code_oauth_token" in stdout


@needs_bash
@pytest.mark.skipif(shutil.which("jq") is None, reason="the gate step reads the plan schema with jq")
@pytest.mark.parametrize("name", MODEL_WORKFLOWS)
def test_gate_skips_an_untrusted_author_without_needing_a_credential(tmp_path: Path, name: str) -> None:
    code, outputs, _ = _run_gate(tmp_path, name, False, False, False, trusted=False)
    assert code == 0 and outputs["run"] == "false" and "auth" not in outputs


@needs_bash
@pytest.mark.skipif(shutil.which("jq") is None, reason="the gate step reads the plan schema with jq")
@pytest.mark.parametrize("name", MODEL_WORKFLOWS)
def test_plan_schema_is_safe_inside_single_quotes(tmp_path: Path, name: str) -> None:
    """The gate's schema output goes to --json-schema '<schema>' (issue #1478).

    It must hold no ' and no $schema key: Claude Code rejects the draft
    2020-12 meta-schema URI the schema files name.
    """
    assert "--json-schema '${{ steps.gate.outputs.schema }}'" in _model_step(name)["with"]["claude_args"]
    code, outputs, _ = _run_gate(tmp_path, name, True, False, False, real_schemas=True)
    assert code == 0
    plan_schema = {"reusable-refine-triage.yml": "triage-plan.schema.json",
                   "reusable-refine-dedupe.yml": "dedupe-plan.schema.json"}[name]
    source = json.loads((SCRIPTS / plan_schema).read_text(encoding="utf-8"))
    assert "$schema" in source, "the test proves nothing once the source drops $schema"
    assert "'" not in outputs["schema"]
    passed = json.loads(outputs["schema"])
    assert "$schema" not in passed
    assert passed == {k: v for k, v in source.items() if k != "$schema"}


@pytest.mark.parametrize("name", MODEL_WORKFLOWS)
def test_plan_upload_includes_the_hidden_refine_directory(name: str) -> None:
    """upload-artifact skips hidden files by default, so `.refine/` uploaded nothing (issue #1478)."""
    uploads = [s for s in _steps(_load(name)) if str(s.get("uses", "")).startswith("actions/upload-artifact@")
               and s["with"]["path"].startswith(".")]
    assert len(uploads) == 1 and uploads[0]["with"]["path"] == ".refine/"
    assert uploads[0]["with"]["include-hidden-files"] is True


def test_dispatched_issue_number_is_passed_as_a_number() -> None:
    """A workflow_dispatch number input reaches `inputs` as a string, and a
    string for a `type: number` workflow_call input fails the run before any
    job starts (issue #1478)."""
    passed = _load("refine-dedupe.yml")["jobs"]["dedupe"]["with"]["issue_number"]
    assert passed == "${{ github.event.issue.number || fromJSON(inputs.issue_number) }}"


# ── the contract draft (#1328): its own caller, so the sweep keeps read-only contents ──

CONTRACT = "reusable-refine-contract.yml"
SPARSE_CONTRACT = SPARSE_READS | {"scripts/contract/"}


def test_contract_caller_runs_after_the_sweep_with_the_kill_switch() -> None:
    doc = _load("refine-contract.yml")
    assert doc["on"]["workflow_run"] == {"workflows": ["Refine sweep"], "types": ["completed"]}
    assert doc["permissions"] == {"contents": "write", "issues": "read", "pull-requests": "write"}
    (job,) = doc["jobs"].values()
    assert job["uses"] == f"./.github/workflows/{CONTRACT}"
    assert "vars.REFINE_ENABLED == 'true'" in job["if"] and "vars.REFINE_CONTRACT_RUN_ID" in job["if"]
    assert job["with"]["mode"] == "${{ vars.REFINE_MODE || 'dry-run' }}"
    call = _load(CONTRACT)["on"]["workflow_call"]
    assert set(job["with"]) <= set(call["inputs"]) and set(job["secrets"]) <= set(call["secrets"])


def test_contract_job_is_safe_by_default_and_pinned() -> None:
    doc = _load(CONTRACT)
    call = doc["on"]["workflow_call"]
    assert call["inputs"]["enabled"]["default"] is False and call["inputs"]["mode"]["default"] == "dry-run"
    assert call["inputs"]["run_id"]["default"] == ""
    (job,) = doc["jobs"].values()
    assert job["if"].startswith("inputs.enabled") and "inputs.run_id != ''" in job["if"]
    assert job["permissions"] == {"contents": "write", "issues": "read", "pull-requests": "write"}
    assert job["concurrency"]["cancel-in-progress"] is False
    steps = job["steps"]
    assert steps[0]["with"]["ref"] == "${{ github.event.repository.default_branch }}"
    ext = next(s for s in steps if s.get("with", {}).get("path") == ".refine-toolkit")
    assert ext["with"]["ref"] == "${{ job.workflow_sha }}"
    assert set(ext["with"]["sparse-checkout"].split()) == SPARSE_CONTRACT
    for step in steps:
        if str(step.get("uses", "")).startswith("actions/checkout@"):
            assert step["with"]["persist-credentials"] is False
    runs = " ".join(str(s.get("run", "")) for s in steps)
    assert "git config" not in runs, "the token never lands in .git/config"
    assert not any("claude-code-action" in str(s.get("uses", "")) for s in steps)
    assert set(re.findall(r"secrets\.(\w+)", (WORKFLOWS / CONTRACT).read_text(encoding="utf-8"))) == {"contract_token"}


def test_contract_job_runs_the_kill_test_before_anything_is_pushed() -> None:
    steps = _load(CONTRACT)["jobs"]["contract"]["steps"]
    draft_step = next(s for s in steps if s.get("id") == "draft")
    assert '"$DRAFT" write' in draft_step["run"] and '"$DRAFT" check' in draft_step["run"]
    assert "git push" not in draft_step["run"]
    # .claude/ is ignored here and in many adopters: a plain add or status skips the draft.
    assert 'git add -f -- ".claude/contracts/${RUN_ID}.contract.md"' in draft_step["run"]
    assert "git diff --cached --quiet" in draft_step["run"] and "git status" not in draft_step["run"]
    push = next(s for s in steps if "git push" in str(s.get("run", "")))
    assert steps.index(draft_step) < steps.index(push)
    assert "inputs.mode == 'live'" in push["if"], "dry-run pushes nothing"
    assert "gh pr create" in push["run"] and "gh pr comment" in push["run"]
