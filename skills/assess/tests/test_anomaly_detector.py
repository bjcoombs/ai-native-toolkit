"""Tests for anomaly detection on /assess run output."""
from __future__ import annotations

from lib.anomaly_detector import detect_anomalies


def _ctx(**overrides) -> dict:
    """Build a healthy context, then apply overrides for the test."""
    base = {
        "prior_stats_exists": True,
        "stats_summary": {
            "files_scored": 100,
            "loc": {"p50": 30, "p95": 200, "max": 500},
            "ccn": {"p50": 3, "p95": 8, "max": 25},
            "top_hotspots": [
                {"path": "src/a.go", "loc": 400, "ccn": 25, "commits": 5},
                {"path": "src/b.go", "loc": 300, "ccn": 18, "commits": 3},
            ],
        },
        "instruction_files": {
            "CLAUDE.md": {
                "present": True, "grade": "B+", "score": 62, "line_count": 80, "subscores": {},
            },
        },
        "instructions_grade": "B+",
        "diff": {"new": 1, "graduated": 0, "regressed": 0, "persistent": 1},
    }
    for k, v in overrides.items():
        if isinstance(base.get(k), dict) and isinstance(v, dict) and v:
            base[k].update(v)
        else:
            base[k] = v
    return base


def test_no_anomalies_in_healthy_run() -> None:
    assert detect_anomalies(_ctx()) == []


def test_zero_files_scored() -> None:
    anomalies = detect_anomalies(_ctx(stats_summary={"files_scored": 0}))
    assert "ZERO_FILES_SCORED" in {a.code for a in anomalies}


def test_zero_complexity_with_files() -> None:
    anomalies = detect_anomalies(_ctx(stats_summary={
        "files_scored": 50, "ccn": {"p50": 0, "p95": 0, "max": 0},
    }))
    assert "ZERO_COMPLEXITY" in {a.code for a in anomalies}


def test_empty_hotspots_large_repo() -> None:
    anomalies = detect_anomalies(_ctx(stats_summary={
        "files_scored": 250, "top_hotspots": [],
    }))
    assert "EMPTY_HOTSPOTS" in {a.code for a in anomalies}


def test_instruction_file_grade_mismatch_long_file_low_grade() -> None:
    """An instruction file that's >200 lines but grades F is suspicious."""
    anomalies = detect_anomalies(_ctx(
        instruction_files={"CLAUDE.md": {
            "present": True, "grade": "F", "score": 10, "line_count": 350, "subscores": {},
        }},
        instructions_grade="F",
    ))
    assert "INSTRUCTION_FILE_GRADE_MISMATCH" in {a.code for a in anomalies}


def test_instruction_file_grade_mismatch_for_agents_md() -> None:
    """The check applies to any instruction filename, not just CLAUDE.md."""
    anomalies = detect_anomalies(_ctx(
        instruction_files={"AGENTS.md": {
            "present": True, "grade": "F", "score": 10, "line_count": 300, "subscores": {},
        }},
        instructions_grade="F",
    ))
    assert "INSTRUCTION_FILE_GRADE_MISMATCH" in {a.code for a in anomalies}


def test_all_hotspots_new_means_rotation_failed() -> None:
    anomalies = detect_anomalies(_ctx(
        diff={"new": 8, "graduated": 0, "regressed": 0, "persistent": 0},
        stats_summary={"files_scored": 100, "top_hotspots": [
            {"path": f"src/{i}.go", "loc": 300, "ccn": 15, "commits": 2} for i in range(8)
        ]},
    ))
    assert "ALL_NEW_HOTSPOTS" in {a.code for a in anomalies}


def test_all_new_hotspots_first_run_not_flagged() -> None:
    """On a true first run (no prior stats), ALL_NEW_HOTSPOTS must not be raised."""
    anomalies = detect_anomalies(_ctx(
        prior_stats_exists=False,
        diff={"new": 8, "graduated": 0, "regressed": 0, "persistent": 0},
        stats_summary={"files_scored": 100, "top_hotspots": [
            {"path": f"src/{i}.go", "loc": 300, "ccn": 15, "commits": 2} for i in range(8)
        ]},
    ))
    assert "ALL_NEW_HOTSPOTS" not in {a.code for a in anomalies}


def test_anomaly_detail_excludes_source_paths() -> None:
    """Every anomaly type must produce detail strings with no source file paths.

    File basenames for well-known instruction files (CLAUDE.md, AGENTS.md, etc.)
    are intentionally included - those are public knowledge and help triage.
    The exclusion is for source paths (src/foo.go, lib/bar.py, etc.).
    """
    # Trigger all 5 anomaly types
    triggers = [
        # ZERO_FILES_SCORED
        _ctx(stats_summary={"files_scored": 0}),
        # ZERO_COMPLEXITY
        _ctx(stats_summary={"files_scored": 50, "ccn": {"p50": 0, "p95": 0, "max": 0}}),
        # EMPTY_HOTSPOTS
        _ctx(stats_summary={"files_scored": 250, "top_hotspots": []}),
        # INSTRUCTION_FILE_GRADE_MISMATCH
        _ctx(
            instruction_files={"CLAUDE.md": {
                "present": True, "grade": "F", "score": 10, "line_count": 350, "subscores": {},
            }},
            instructions_grade="F",
        ),
        # ALL_NEW_HOTSPOTS
        _ctx(
            diff={"new": 8, "graduated": 0, "regressed": 0, "persistent": 0},
            stats_summary={"files_scored": 100, "top_hotspots": [
                {"path": f"src/{i}.go", "loc": 300, "ccn": 15, "commits": 2} for i in range(8)
            ]},
        ),
    ]
    for ctx in triggers:
        anomalies = detect_anomalies(ctx)
        for a in anomalies:
            # No source-path characters
            assert "/" not in a.detail, f"{a.code}: detail contains slash: {a.detail!r}"
            assert ".go" not in a.detail, f"{a.code}: detail contains .go: {a.detail!r}"
            assert ".py" not in a.detail, f"{a.code}: detail contains .py: {a.detail!r}"
            assert ".ts" not in a.detail, f"{a.code}: detail contains .ts: {a.detail!r}"


def test_anomaly_has_code_description_detail() -> None:
    anomalies = detect_anomalies(_ctx(stats_summary={"files_scored": 0}))
    assert len(anomalies) >= 1
    a = anomalies[0]
    assert a.code and a.description and a.detail


def test_no_anomalies_when_no_instruction_files() -> None:
    """A repo with no instruction files at all is a valid (if poor) state, not an anomaly.

    The grade is None (distinct from F). INSTRUCTION_FILE_GRADE_MISMATCH should not fire
    because there's no file to mismatch with.
    """
    ctx = _ctx(instruction_files={}, instructions_grade=None)
    anomalies = detect_anomalies(ctx)
    codes = {a.code for a in anomalies}
    assert "INSTRUCTION_FILE_GRADE_MISMATCH" not in codes
