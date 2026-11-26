"""The doc-sync model step's one retry (issue #1481).

`.github/workflows/doc-sync.yml` runs the model a second time only when the
first run ended cleanly without the findings document. The fixture is the
execution log of run 45861255055, as its step log printed it: a `success`
result with `is_error: false` after 13 turns and no `structured_output`.
These tests run the workflow's own jq filter against it and pin that the
retry keeps the first run's guardrails.
"""
import copy
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List

import pytest
import yaml  # required: the plugin contract job installs pyyaml

from gitbash import BASH

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / ".github" / "workflows" / "doc-sync.yml"
RECORDED = REPO / "tests" / "fixtures" / "doc_sync" / "execution-no-structured-output.json"


def _steps() -> List[Dict[str, Any]]:
    doc: Dict[str, Any] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps: List[Dict[str, Any]] = doc["jobs"]["check"]["steps"]
    return steps


def _step(step_id: str) -> Dict[str, Any]:
    return [s for s in _steps() if s.get("id") == step_id][0]


def _retries(log: Any, tmp_path: Path) -> bool:
    """Run the `missing` step's script with the log as the execution file."""
    exec_file = tmp_path / "claude-execution-output.json"
    exec_file.write_text(json.dumps(log), encoding="utf-8")
    out = tmp_path / "github_output"
    out.write_text("", encoding="utf-8")
    step = _step("missing")
    env = {**os.environ, "EXEC_FILE": exec_file.as_posix(), "NO_OUTPUT": step["env"]["NO_OUTPUT"],
           "GITHUB_OUTPUT": out.as_posix()}
    subprocess.run([BASH, "-e", "-c", step["run"]], env=env, check=True,
                   capture_output=True, encoding="utf-8", errors="replace")
    return "retry=true" in out.read_text(encoding="utf-8")


def _recorded() -> Any:
    return json.loads(RECORDED.read_text(encoding="utf-8"))


needs_jq = pytest.mark.skipif(shutil.which("jq") is None or shutil.which("bash") is None,
                              reason="the step script runs jq under bash")


@needs_jq
def test_recorded_run_without_structured_output_retries(tmp_path: Path) -> None:
    assert _retries(_recorded(), tmp_path)


@needs_jq
def test_error_result_does_not_retry(tmp_path: Path) -> None:
    log = _recorded()
    log[-1]["is_error"] = True
    log[-1]["subtype"] = "error_max_turns"
    assert not _retries(log, tmp_path)


@needs_jq
def test_log_with_no_result_entry_does_not_retry(tmp_path: Path) -> None:
    assert not _retries(_recorded()[:1], tmp_path)


@needs_jq
def test_missing_log_does_not_retry(tmp_path: Path) -> None:
    out = tmp_path / "github_output"
    out.write_text("", encoding="utf-8")
    step = _step("missing")
    env = {**os.environ, "EXEC_FILE": (tmp_path / "absent.json").as_posix(),
           "NO_OUTPUT": step["env"]["NO_OUTPUT"], "GITHUB_OUTPUT": out.as_posix()}
    subprocess.run([BASH, "-e", "-c", step["run"]], env=env, check=True,
                   capture_output=True, encoding="utf-8", errors="replace")
    assert out.read_text(encoding="utf-8") == ""


@needs_jq
def test_result_with_structured_output_does_not_retry(tmp_path: Path) -> None:
    log = copy.deepcopy(_recorded())
    log[-1]["structured_output"] = {"findings": []}
    assert not _retries(log, tmp_path)


def test_retry_runs_only_after_a_failed_first_run() -> None:
    assert "steps.model.outcome == 'failure'" in _step("missing")["if"]
    assert "steps.missing.outputs.retry == 'true'" in _step("model_retry")["if"]
    ids = [s.get("id") for s in _steps()]
    assert ids.index("model") < ids.index("missing") < ids.index("model_retry") < ids.index("plan")


def test_retry_keeps_the_first_run_scopes_credential_and_prompt() -> None:
    first, retry = _step("model"), _step("model_retry")
    assert retry["uses"] == first["uses"]
    assert retry["with"] == first["with"]
    assert retry["timeout-minutes"] == first["timeout-minutes"]
    assert retry["continue-on-error"] is True


def test_findings_and_status_come_from_the_retry_when_it_ran() -> None:
    assert "steps.model_retry.outputs.structured_output" in _step("plan")["env"]["RAW"]
    report = [s for s in _steps() if s.get("name") == "Report final status"][0]["env"]
    for key in ("OUTCOME", "CONCLUSION", "EXEC_FILE"):
        assert "steps.model_retry." in report[key]


def test_plan_upload_names_one_file() -> None:
    """A single named file under .doc-sync/ uploads without include-hidden-files.

    Run 45861898447 uploaded `.doc-sync/findings.json` as 1 file with the
    setting false. A directory path would need it set to true (issue #1478).
    """
    upload = [s for s in _steps() if str(s.get("uses", "")).startswith("actions/upload-artifact@")][0]
    path = upload["with"]["path"]
    assert path == ".doc-sync/findings.json" or upload["with"].get("include-hidden-files") is True
