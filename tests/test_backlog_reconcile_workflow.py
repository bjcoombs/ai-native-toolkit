"""The scheduled backlog reconcile workflows' guardrails (issue #814).

`backlog-reconcile.yml` is a thin daily caller of `reusable-backlog-reconcile.yml`.
These tests pin the daily schedule, the token scope (`issues: write` and
`contents: read`, nothing else), the kill switch, the single writer job, the
scripts pinned to the workflow's own commit and the absence of any model.
"""
import re
from pathlib import Path
from typing import Any, Dict, List

import yaml  # required: the plugin contract job installs pyyaml

REPO = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO / ".github" / "workflows"
CALLER, REUSABLE = "backlog-reconcile.yml", "reusable-backlog-reconcile.yml"


def _load(name: str) -> Dict[str, Any]:
    doc: Dict[str, Any] = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    doc["on"] = doc.pop(True, doc.get("on"))  # PyYAML reads the bare key `on` as True
    return doc


def _steps(job: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list(job.get("steps", []))


def test_the_caller_runs_daily_with_issues_write_and_contents_read_only() -> None:
    doc = _load(CALLER)
    assert set(doc["on"]) == {"schedule", "workflow_dispatch"}
    assert len(doc["on"]["schedule"]) == 1
    minute, hour, dom, month, dow = doc["on"]["schedule"][0]["cron"].split()
    assert minute.isdigit() and hour.isdigit() and (dom, month, dow) == ("*", "*", "*"), "once a day"
    assert doc["permissions"] == {"issues": "write", "contents": "read"}
    assert doc["concurrency"] == {"group": "backlog-reconcile-run-${{ github.repository }}",
                                  "cancel-in-progress": False}, "one whole run at a time per repository"
    for job in doc["jobs"].values():
        assert "permissions" not in job


def test_the_caller_is_one_uses_line_with_the_kill_switch() -> None:
    jobs = _load(CALLER)["jobs"]
    assert len(jobs) == 1
    job = next(iter(jobs.values()))
    assert job["uses"] == f"./.github/workflows/{REUSABLE}"
    assert "steps" not in job and "runs-on" not in job
    assert job["if"] == "vars.BACKLOG_RECONCILE_ENABLED != 'false'"
    assert job["with"] == {"enabled": "${{ vars.BACKLOG_RECONCILE_ENABLED != 'false' }}"}
    call = _load(REUSABLE)["on"]["workflow_call"]
    assert set(job["with"]) <= set(call["inputs"])
    assert call["inputs"]["enabled"]["default"] is False, "off unless the caller turns it on"


def test_one_writer_job_with_the_narrowest_token() -> None:
    jobs = _load(REUSABLE)["jobs"]
    assert jobs["plan"]["permissions"] == {"contents": "read", "issues": "read"}
    assert jobs["apply"]["permissions"] == {"contents": "read", "issues": "write"}
    writers = [n for n, j in jobs.items() if "write" in j["permissions"].values()]
    assert writers == ["apply"]
    assert jobs["plan"]["if"] == "inputs.enabled"
    assert jobs["apply"]["if"] == "inputs.enabled && needs.plan.outputs.writes != '0'"


def test_no_model_no_secret_and_no_cache() -> None:
    for name in (CALLER, REUSABLE):
        text = (WORKFLOWS / name).read_text(encoding="utf-8")
        assert "claude-code-action" not in text and "secrets." not in text
    for job in _load(REUSABLE)["jobs"].values():
        for step in _steps(job):
            assert not str(step.get("uses", "")).startswith("actions/cache")
            if str(step.get("uses", "")).startswith("astral-sh/setup-uv@"):
                assert step["with"]["enable-cache"] is False


def test_every_action_is_pinned_to_a_full_sha() -> None:
    for job in _load(REUSABLE)["jobs"].values():
        for step in _steps(job):
            uses = step.get("uses")
            if uses:
                assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", uses), uses


def test_scripts_come_from_the_workflows_own_commit() -> None:
    for name, job in _load(REUSABLE)["jobs"].items():
        steps = _steps(job)
        pins = [s for s in steps if "job.workflow_sha" in str(s.get("env", {}).get("TOOLKIT_SHA", ""))]
        assert len(pins) == 1 and "^[0-9a-f]{40}$" in pins[0]["run"], name
        toolkit = [s for s in steps if s.get("with", {}).get("path") == ".reconcile-toolkit"]
        assert len(toolkit) == 1, name
        assert toolkit[0]["with"]["ref"] == "${{ job.workflow_sha }}"
        assert toolkit[0]["with"]["repository"] == "${{ job.workflow_repository }}"
        for step in steps:
            if str(step.get("uses", "")).startswith("actions/checkout@"):
                assert step["with"]["persist-credentials"] is False
        for step in steps:
            assert "${{ inputs." not in str(step.get("run", "")), "inputs reach run: through env only"


def test_the_writer_job_checks_out_the_writer_script_alone() -> None:
    apply = _load(REUSABLE)["jobs"]["apply"]
    checkouts = [s for s in _steps(apply) if str(s.get("uses", "")).startswith("actions/checkout@")]
    assert len(checkouts) == 1
    assert checkouts[0]["with"]["sparse-checkout"].split() == ["skills/assess/scripts/backlog_reconcile_apply.py"]
    assert (REPO / "skills/assess/scripts/backlog_reconcile_apply.py").is_file()
    assert any("backlog_reconcile_apply.py" in str(s.get("run", "")) for s in _steps(apply))


def test_the_plan_job_reads_the_mode_from_the_repository_config() -> None:
    plan = _load(REUSABLE)["jobs"]["plan"]
    runs = " ".join(str(s.get("run", "")) for s in _steps(plan))
    assert "reconcile" in runs and "--scheduled" in runs and "--mode" not in runs


def test_the_plan_artifact_name_survives_a_rerun_of_the_failed_job() -> None:
    names = [s["with"]["name"] for job in _load(REUSABLE)["jobs"].values() for s in _steps(job)
             if str(s.get("uses", "")).startswith(("actions/upload-artifact@", "actions/download-artifact@"))]
    assert names == ["backlog-reconcile-plan", "backlog-reconcile-plan"]
