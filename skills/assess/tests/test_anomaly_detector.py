"""Tests for anomaly detection on /assess run output."""
from __future__ import annotations

from lib.anomaly_detector import Anomaly, detect_anomalies


def _ctx(**overrides) -> dict:
    """Build a healthy context, then apply overrides for the test."""
    base = {
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
        if isinstance(base.get(k), dict) and isinstance(v, dict):
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


def test_anomaly_detail_excludes_paths() -> None:
    """Detail strings must never contain file paths - they ship to public issues."""
    anomalies = detect_anomalies(_ctx(stats_summary={"files_scored": 0}))
    for a in anomalies:
        assert "/" not in a.detail
        assert ".go" not in a.detail
        assert ".py" not in a.detail


def test_anomaly_has_code_description_detail() -> None:
    anomalies = detect_anomalies(_ctx(stats_summary={"files_scored": 0}))
    assert len(anomalies) >= 1
    a = anomalies[0]
    assert a.code and a.description and a.detail
