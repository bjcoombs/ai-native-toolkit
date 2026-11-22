"""Keep the split Windows workflow's required check honest (#938).

`.github/workflows/windows.yml` runs the suites as parallel matrix legs, and one
aggregator job carries the required check name `windows suites + smoke runs`.
Branch protection reads only that name, so the aggregator must report on every
run and must be green only when every leg is green: a failed, cancelled or
skipped leg turns it red. The ghsync/ghreport path gate (#971) stays a step
inside its leg, so no leg is ever skipped at job level on purpose.
"""
import re
from pathlib import Path
from typing import Any

import yaml  # required: the plugin contract job installs pyyaml

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / ".github" / "workflows" / "windows.yml"
REQUIRED_NAME = "windows suites + smoke runs"
LEGS = {"assess", "scripts-contract", "ghsync", "smoke"}
# The shared setup every leg runs (plus the unnamed actions/checkout step).
SETUP_STEPS = {
    "Keep LF line endings",
    "Fetch the PR base ref",
    "Set up Python",
    "Install uv",
    "Install ripgrep",
    "Git Bash tools",
    "Python version",
}
GHSYNC_SUITE ="ghsync and ghreport suites under Git Bash"
# Every non-setup step and the one leg it runs in.
STEP_LEG = {
    "Shell scripts are LF in a CRLF checkout": "smoke",
    "Fleet golden run against the committed Linux repos.jsonl": "assess",
    "skills/assess pytest": "assess",
    "skills/assess CPU-budget tests": "assess",
    "scripts/ pytest": "scripts-contract",
    "plugin contract pytest": "scripts-contract",
    "Decide whether the ghsync and ghreport suites run": "ghsync",
    GHSYNC_SUITE: "ghsync",
    "Standalone skill ZIPs": "smoke",
    "Eval scaffolds build their fixtures": "smoke",
    "Smoke fixture estate": "smoke",
    "Smoke assess_signals.py": "smoke",
    "Smoke assess_core.py": "smoke",
    "Smoke fleet.py over a ghsync-layout root": "smoke",
    "Smoke fleet.py --from-list": "smoke",
    "Smoke start_gate.py and complete_gate.py": "smoke",
}


def _jobs() -> dict[str, Any]:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return doc["jobs"]


def _aggregator(jobs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    named = [(jid, job) for jid, job in jobs.items() if job.get("name") == REQUIRED_NAME]
    assert len(named) == 1, f"exactly one job must be named {REQUIRED_NAME!r}: {named}"
    return named[0]


def _matrix_job(jobs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    legs = [(jid, job) for jid, job in jobs.items() if "strategy" in job]
    assert len(legs) == 1, f"one matrix job expected: {[jid for jid, _ in legs]}"
    return legs[0]


def test_required_name_is_written_where_the_floor_anchor_reads_it() -> None:
    # scripts/floor_anchor.py reads job names with a line regex, not YAML:
    # a four-space, unquoted `name:` line.
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    assert f"    name: {REQUIRED_NAME}" in lines


def test_matrix_runs_every_leg_without_fail_fast() -> None:
    _, job = _matrix_job(_jobs())
    strategy = job["strategy"]
    assert strategy["fail-fast"] is False
    assert set(strategy["matrix"]["suite"]) == LEGS
    assert job["timeout-minutes"] == 45
    assert job["runs-on"] == "windows-latest"
    assert "if" not in job, "a job-level if could skip every leg"
    assert REQUIRED_NAME not in str(job.get("name", ""))


def test_aggregator_needs_the_matrix_and_always_reports() -> None:
    jobs = _jobs()
    matrix_id, _ = _matrix_job(jobs)
    _, agg = _aggregator(jobs)
    needs = agg["needs"]
    assert (needs if isinstance(needs, list) else [needs]) == [matrix_id]
    assert agg["if"] == "always()"
    assert "continue-on-error" not in agg


def test_aggregator_passes_only_on_success() -> None:
    jobs = _jobs()
    matrix_id, _ = _matrix_job(jobs)
    _, agg = _aggregator(jobs)
    steps = agg["steps"]
    assert len(steps) == 1
    step = steps[0]
    assert step["env"]["RESULT"] == f"${{{{ needs.{matrix_id}.result }}}}"
    assert '[ "$RESULT" = "success" ]' in step["run"]
    assert "continue-on-error" not in step


def test_ghsync_gate_stays_a_step_in_its_leg() -> None:
    _, job = _matrix_job(_jobs())
    by_id = {s["id"]: s for s in job["steps"] if "id" in s}
    gate = by_id["ghsync_gate"]
    assert "matrix.suite == 'ghsync'" in gate["if"]
    suite = next(s for s in job["steps"] if s.get("name") == "ghsync and ghreport suites under Git Bash")
    assert "steps.ghsync_gate.outputs.run != 'false'" in suite["if"]
    assert "matrix.suite == 'ghsync'" in suite["if"]


def test_every_suite_step_runs_in_exactly_its_leg() -> None:
    # An exact `if:` per step: a typo'd leg name or an extra `&& false` would
    # otherwise drop the step while its leg stays green.
    _, job = _matrix_job(_jobs())
    steps = {s.get("name"): s for s in job["steps"]}
    missing = sorted(set(STEP_LEG) - set(steps))
    assert not missing, f"steps the map expects but the workflow lacks: {missing}"
    for name, leg in STEP_LEG.items():
        extra = " && steps.ghsync_gate.outputs.run != 'false'" if name == GHSYNC_SUITE else ""
        expected = f"${{{{ !cancelled() && matrix.suite == '{leg}'{extra} }}}}"
        assert steps[name].get("if") == expected, name


def test_no_step_names_an_unknown_leg() -> None:
    _, job = _matrix_job(_jobs())
    for step in job["steps"]:
        cond = str(step.get("if", ""))
        for leg in re.findall(r"matrix\.suite == '([^']*)'", cond):
            assert leg in LEGS, f"{step.get('name')}: unknown leg {leg!r}"
        if "matrix.suite" in cond:
            assert step.get("name") in STEP_LEG, f"leg step missing from STEP_LEG: {step.get('name')}"
        else:
            # A step with no leg guard runs in all four legs: only shared setup may.
            label = step.get("name", step.get("uses", ""))
            assert label in SETUP_STEPS or str(label).startswith("actions/checkout@"), (
                f"{label!r} has no matrix.suite guard, so it runs in every leg"
            )


def test_superseded_pr_runs_cancel() -> None:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert doc["concurrency"] == {
        "group": "windows-${{ github.event.pull_request.number || github.sha }}",
        "cancel-in-progress": "${{ github.event_name == 'pull_request' }}",
    }
