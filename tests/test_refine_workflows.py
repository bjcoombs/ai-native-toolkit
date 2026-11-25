"""The refiner workflows' guardrails (issues #1321, #1327).

Three thin callers (`refine-triage.yml`, `refine-dedupe.yml`,
`refine-sweep.yml`) each call one reusable `workflow_call` workflow. These
tests pin the triggers, the kill switch, the dry-run default, the
permissions, the tool limits, the call caps, the concurrency groups and
timeouts, and the `workflow_call` inputs the callers pass.
"""
import json
import re
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
def test_caller_permissions_are_exactly_contents_read_issues_write(caller: str) -> None:
    doc = _load(caller)
    assert doc["permissions"] == {"contents": "read", "issues": "write"}
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
        assert set(re.findall(r"secrets\.(\w+)", text)) <= {"REFINE_ANTHROPIC_API_KEY", "anthropic_api_key"}


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
    assert "permissions" not in doc, "jobs take the caller's grant; a called workflow cannot widen it"
    for job in doc["jobs"].values():
        assert "permissions" not in job
        assert str(job["if"]).lstrip().startswith("inputs.enabled")
        assert isinstance(job["timeout-minutes"], int)
        assert job["env"]["REFINE_MODE"] == "${{ inputs.mode }}"


@pytest.mark.parametrize("name", sorted(CALLERS.values()))
def test_reusable_inputs_cover_mode_labels_and_close_cap(name: str) -> None:
    inputs = set(_load(name)["on"]["workflow_call"]["inputs"])
    assert {"enabled", "mode", "toolkit_repository", "toolkit_ref"} <= inputs
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
    assert with_["anthropic_federation_rule_id"] == "${{ inputs.anthropic_federation_rule_id }}"


@pytest.mark.parametrize("name", sorted(CALLERS.values()))
def test_per_issue_concurrency(name: str) -> None:
    for job_id, job in _load(name)["jobs"].items():
        group = job["concurrency"]["group"]
        if name == "reusable-refine-sweep.yml":
            assert job["concurrency"]["cancel-in-progress"] is False
            continue
        assert "${{ inputs.issue_number }}" in group, job_id
        assert job["concurrency"]["cancel-in-progress"] is (job_id != "reopen")


@pytest.mark.parametrize("name", MODEL_WORKFLOWS)
def test_model_step_guardrails(name: str) -> None:
    step = _model_step(name)
    assert re.fullmatch(r"anthropics/claude-code-action@[0-9a-f]{40}", step["uses"]), "pin the action to a sha"
    assert isinstance(step["timeout-minutes"], int)
    args = step["with"]["claude_args"]
    assert "--permission-mode auto" in args
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
    for job in _load(name)["jobs"].values():
        caps = json.loads(job["env"]["CLAUDE_CODE_SCRIPT_CAPS"])
        assert caps and set(caps) <= WRITE_SCRIPTS
        assert all(isinstance(v, int) and v >= 1 for v in caps.values())
    if name != "reusable-refine-triage.yml":
        close = json.loads(next(iter(_load(name)["jobs"].values()))["env"]["CLAUDE_CODE_SCRIPT_CAPS"])
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


@pytest.mark.parametrize("name", sorted(CALLERS.values()))
def test_toolkit_scripts_come_from_the_default_branch_in_this_repository(name: str) -> None:
    for job in _load(name)["jobs"].values():
        steps = job["steps"]
        assert steps[0]["with"]["ref"] == "${{ github.event.repository.default_branch }}"
        link = next(s for s in steps if s.get("run") == "ln -s . .refine-toolkit")
        assert link["if"] == "inputs.toolkit_repository == github.repository"
        external = next(s for s in steps if s.get("with", {}).get("path") == ".refine-toolkit")
        assert external["if"] == "inputs.toolkit_repository != github.repository"
    for caller, reusable in CALLERS.items():
        if reusable == name:
            job = next(iter(_load(caller)["jobs"].values()))
            assert "toolkit_repository" not in job["with"], "the default names this repository"
