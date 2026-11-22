"""Keep the split Windows workflow's required check honest (#938).

`.github/workflows/windows.yml` runs the suites as parallel matrix legs, and one
aggregator job carries the required check name `windows suites + smoke runs`.
Branch protection reads only that name, so the aggregator must report on every
run and must be green only when every leg is green: a failed, cancelled or
skipped leg turns it red. The ghsync/ghreport path gate (#971) stays a step
inside its leg, so no leg is ever skipped at job level on purpose.
"""
from pathlib import Path
from typing import Any

import yaml  # required: the plugin contract job installs pyyaml

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / ".github" / "workflows" / "windows.yml"
REQUIRED_NAME = "windows suites + smoke runs"
LEGS = {"assess", "scripts-contract", "ghsync", "smoke"}


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


def test_every_suite_step_belongs_to_one_leg() -> None:
    _, job = _matrix_job(_jobs())
    seen: set[str] = set()
    for step in job["steps"]:
        cond = str(step.get("if", ""))
        for leg in LEGS:
            if f"matrix.suite == '{leg}'" in cond:
                seen.add(leg)
                assert "!cancelled()" in cond, step.get("name")
    assert seen == LEGS
