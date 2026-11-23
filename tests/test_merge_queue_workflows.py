"""Every required check also reports on the merge queue's groups (#1088).

GitHub's merge queue tests main plus the queued pull requests on a temporary
branch and merges only when every required status check passes there. A
workflow that does not trigger on `merge_group` never reports its context on
that branch, so the queue waits for it until the group times out. These tests
pin the trigger and the shape the queue needs in each workflow that produces a
required context, under the same job name it carries on a pull request.

The required set is the list branch protection holds; CLAUDE.md ("CI") names
the same eight.
"""
from pathlib import Path
from typing import Any

import pytest
import yaml  # required: the plugin contract job installs pyyaml

REPO = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO / ".github" / "workflows"
REQUIRED = {
    "skills/assess pytest",
    "scripts/ pytest",
    "plugin contract pytest",
    "ruff + mypy gates",
    "Validate PR title",
    "floor enforcement",
    "floor self-anchor",
    "windows suites + smoke runs",
}


def _load(path: Path) -> dict[str, Any]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    # PyYAML reads the bare key `on` as the boolean True.
    doc["on"] = doc.pop(True, doc.get("on"))
    return doc


def _producers() -> dict[str, tuple[Path, dict[str, Any]]]:
    """Each required context with the workflow and job that produce it."""
    found: dict[str, tuple[Path, dict[str, Any]]] = {}
    for path in sorted(WORKFLOWS.glob("*.yml")):
        doc = _load(path)
        for job in doc["jobs"].values():
            if job.get("name") in REQUIRED:
                assert job["name"] not in found, f"{job['name']!r} produced twice"
                found[job["name"]] = (path, job)
    return found


def test_every_required_context_has_one_producer() -> None:
    assert set(_producers()) == REQUIRED


@pytest.mark.parametrize("context", sorted(REQUIRED))
def test_required_context_runs_on_merge_groups(context: str) -> None:
    path, _ = _producers()[context]
    triggers = _load(path)["on"]
    assert "pull_request" in triggers, path.name
    assert triggers.get("merge_group") == {"types": ["checks_requested"]}, path.name


@pytest.mark.parametrize("context", sorted(REQUIRED))
def test_required_job_is_not_skipped_on_merge_groups(context: str) -> None:
    # A job-level `if:` that excludes merge groups reports the context as
    # skipped there; only the floor's `!cancelled()` guard and the Windows
    # aggregator's `always()` are allowed.
    _, job = _producers()[context]
    assert job.get("if") in (None, "${{ !cancelled() }}", "always()"), context


# A job keyed on the floor path filter's floor-core answer never runs in a
# merge group: the filter answers false there (FLOOR.md, "The merge queue").
NOT_IN_A_GROUP = "needs.canary-changes.outputs.floor_core_changed == 'true'"


def _reads_base_ref(step: dict[str, Any]) -> bool:
    """The step fetches the base ref, written inline or through its env."""
    env = " ".join(str(value) for value in (step.get("env") or {}).values())
    return "git fetch" in step.get("run", "") and (
        "github.base_ref" in step.get("run", "") or "github.base_ref" in env
    )


def test_steps_that_read_the_pull_request_base_skip_merge_groups() -> None:
    # A merge group has no base ref, so `git fetch origin ""` would fail the
    # job. Every step that fetches `github.base_ref`, inline or through env,
    # runs on pull requests only, or sits in a job that cannot run in a group.
    seen = 0
    for path in sorted(WORKFLOWS.glob("*.yml")):
        doc = _load(path)
        if "merge_group" not in (doc["on"] or {}):
            continue
        for job_id, job in doc["jobs"].items():
            for step in job.get("steps", []):
                if not _reads_base_ref(step):
                    continue
                seen += 1
                where = f"{path.name}:{job_id}:{step.get('name')}"
                if job.get("if") == NOT_IN_A_GROUP:
                    continue
                assert step.get("if") == "github.event_name == 'pull_request'", where
    assert seen >= 5  # tests.yml, windows.yml and three in floor.yml


def test_the_base_ref_scan_sees_an_env_indirected_fetch() -> None:
    step = {"env": {"BASE_REF": "${{ github.base_ref }}"},
            "run": 'git fetch --no-tags origin "${BASE_REF}"'}
    assert _reads_base_ref(step)


def test_title_lint_passes_merge_groups_without_a_title() -> None:
    doc = _load(WORKFLOWS / "pr-lint.yml")
    lint = doc["jobs"]["lint-title"]
    action = [s for s in lint["steps"] if "action-semantic-pull-request" in s.get("uses", "")]
    assert len(action) == 1
    assert action[0]["if"] == "github.event_name == 'pull_request'"
    assert doc["jobs"]["auto-label"]["if"] == "github.event_name == 'pull_request'"


def test_floor_markers_use_the_group_base_in_a_merge_group() -> None:
    doc = _load(WORKFLOWS / "floor.yml")
    steps = {s.get("name"): s for s in doc["jobs"]["floor"]["steps"]}
    markers = steps["Marker removal detection (base-vs-head)"]
    assert markers["env"]["QUEUE_BASE_SHA"] == "${{ github.event.merge_group.base_sha }}"
    assert 'BASE_SHA="${QUEUE_BASE_SHA}"' in markers["run"]


def test_floor_signoff_is_never_requested_inside_the_queue() -> None:
    # The filter answers false in a merge group, so the deployment review is
    # a pull-request gate only; the job stays keyed on the filter alone.
    doc = _load(WORKFLOWS / "floor.yml")
    signoff = doc["jobs"]["signoff"]
    assert signoff["environment"] == "floor-signoff"
    assert signoff["if"] == "needs.canary-changes.outputs.floor_core_changed == 'true'"
    filt = [s for s in doc["jobs"]["canary-changes"]["steps"] if s.get("id") == "filter"]
    assert len(filt) == 1
    assert filt[0]["env"]["EVENT_NAME"] == "${{ github.event_name }}"
    assert "floor_core_changed=false" in filt[0]["run"].split("exit 0")[0]
