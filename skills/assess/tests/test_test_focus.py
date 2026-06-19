"""Contract tests for ``lib/test_focus.compute_test_focus``.

Cover each signal classification, the risk-band assignment by hotspot position,
the ranking order, the ``covered_clean`` filter, and the honest no-coverage
degrade (``coverage_data=None`` -> every entry ``unknown_no_coverage`` and
``coverage_present: False``).
"""
from __future__ import annotations

import sys
from pathlib import Path

# scripts/ on the path so ``lib`` imports resolve the same way the orchestrator does.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib.test_focus import compute_test_focus  # noqa: E402


def _hot(*paths: str) -> list[dict]:
    """Build a top_hotspots-shaped list (ranked, each entry a dict with a path)."""
    return [{"path": p} for p in paths]


def _coverage(per_file: dict[str, float], overall: float = 0.5) -> dict:
    return {"_overall": overall, "per_file": per_file}


def _empty_heuristics() -> dict:
    return {
        "assertion_on_internal": [],
        "untested_boundaries": [],
        "duplicate_truth": [],
    }


def _entry(block: dict, path: str) -> dict:
    return next(e for e in block["entries"] if e["path"] == path)


# ── signal classification ─────────────────────────────────────────────────────

def test_no_covering_test_when_file_absent_from_report() -> None:
    block = compute_test_focus(_hot("a.py"), _coverage({"other.py": 0.8}),
                               _empty_heuristics())
    entry = _entry(block, "a.py")
    assert entry["test_signal"] == "no_covering_test"
    assert entry["suggested_action"] == "add_tests"
    assert entry["hollow_heuristic_kinds"] == []


def test_no_covering_test_when_line_rate_zero() -> None:
    block = compute_test_focus(_hot("a.py"), _coverage({"a.py": 0.0}),
                               _empty_heuristics())
    assert _entry(block, "a.py")["test_signal"] == "no_covering_test"


def test_covered_but_hollow_when_in_a_heuristic_bucket() -> None:
    heuristics = {
        "assertion_on_internal": [],
        "untested_boundaries": [{"file": "a.py", "line": 3, "operator": "<="}],
        "duplicate_truth": [],
    }
    block = compute_test_focus(_hot("a.py"), _coverage({"a.py": 0.9}), heuristics)
    entry = _entry(block, "a.py")
    assert entry["test_signal"] == "covered_but_hollow"
    assert entry["suggested_action"] == "strengthen_assertions"
    assert entry["hollow_heuristic_kinds"] == ["untested_boundaries"]


def test_covered_but_hollow_matches_test_file_key() -> None:
    """assertion_on_internal entries name the file under ``test_file``; a hot file
    matching that key still counts as hollow."""
    heuristics = {
        "assertion_on_internal": [
            {"test_file": "a.py", "subject_function": "test_x:obj",
             "internal_field": "_y", "confidence": "medium"}
        ],
        "untested_boundaries": [],
        "duplicate_truth": [],
    }
    block = compute_test_focus(_hot("a.py"), _coverage({"a.py": 0.9}), heuristics)
    assert _entry(block, "a.py")["hollow_heuristic_kinds"] == ["assertion_on_internal"]


def test_multiple_hollow_kinds_collected_in_report_order() -> None:
    heuristics = {
        "assertion_on_internal": [{"test_file": "a.py", "internal_field": "_y"}],
        "untested_boundaries": [{"file": "a.py", "line": 1, "operator": "<"}],
        "duplicate_truth": [{"file": "a.py", "field_name": "x", "derives_from": "y"}],
    }
    block = compute_test_focus(_hot("a.py"), _coverage({"a.py": 0.9}), heuristics)
    assert _entry(block, "a.py")["hollow_heuristic_kinds"] == [
        "assertion_on_internal", "untested_boundaries", "duplicate_truth",
    ]


def test_covered_clean_is_filtered_out() -> None:
    block = compute_test_focus(_hot("a.py"), _coverage({"a.py": 0.95}),
                               _empty_heuristics())
    assert block["entries"] == []
    assert block["total_focus_targets"] == 0
    assert block["coverage_present"] is True


# ── risk bands ────────────────────────────────────────────────────────────────

def test_risk_band_by_hotspot_position() -> None:
    paths = [f"f{i}.py" for i in range(10)]
    # No coverage report -> every file is a focus target, so all 10 appear.
    block = compute_test_focus(_hot(*paths), None, _empty_heuristics())
    band = {e["path"]: e["risk_band"] for e in block["entries"]}
    assert [band[f"f{i}.py"] for i in range(3)] == ["high", "high", "high"]
    assert [band[f"f{i}.py"] for i in range(3, 7)] == ["medium"] * 4
    assert [band[f"f{i}.py"] for i in range(7, 10)] == ["low"] * 3


def test_files_beyond_top_ten_are_excluded() -> None:
    paths = [f"f{i}.py" for i in range(13)]
    block = compute_test_focus(_hot(*paths), None, _empty_heuristics())
    assert block["total_focus_targets"] == 10
    assert all(int(e["path"][1:-3]) < 10 for e in block["entries"])


# ── ranking ───────────────────────────────────────────────────────────────────

def test_ranking_risk_band_dominates_then_signal_severity() -> None:
    # low-risk file with the most severe signal vs high-risk with a milder one:
    # the high-risk file must still rank first (band dominates).
    hot = _hot(*[f"f{i}.py" for i in range(8)])  # f0-f2 high, f3-f6 medium, f7 low
    coverage = _coverage({
        "f0.py": 0.95,  # high, covered_clean -> filtered
        "f7.py": 0.0,   # low, no_covering_test (severe)
    })
    # f0 filtered; f1,f2 high no_covering_test; f3-f6 medium; f7 low severe.
    block = compute_test_focus(hot, coverage, _empty_heuristics())
    ranked = [e["path"] for e in block["entries"]]
    # First entries are the high-band files, low-band f7 is last despite severity.
    assert ranked[0] in {"f1.py", "f2.py"}
    assert ranked[-1] == "f7.py"
    assert block["entries"][0]["risk_band"] == "high"


def test_signal_severity_orders_within_a_band() -> None:
    # Two high-risk files: one with no test (severe), one covered-but-hollow.
    hot = _hot("a.py", "b.py")
    coverage = _coverage({"b.py": 0.9})  # a.py absent -> no_covering_test
    heuristics = {
        "assertion_on_internal": [],
        "untested_boundaries": [{"file": "b.py", "line": 1, "operator": "<"}],
        "duplicate_truth": [],
    }
    block = compute_test_focus(hot, coverage, heuristics)
    ranked = [e["path"] for e in block["entries"]]
    assert ranked == ["a.py", "b.py"]  # no_covering_test outranks covered_but_hollow


# ── honest degrade ────────────────────────────────────────────────────────────

def test_no_coverage_degrades_to_unknown_not_clean() -> None:
    paths = [f"f{i}.py" for i in range(3)]
    block = compute_test_focus(_hot(*paths), None, _empty_heuristics())
    assert block["coverage_present"] is False
    assert block["available"] is True
    assert block["total_focus_targets"] == 3
    for entry in block["entries"]:
        assert entry["test_signal"] == "unknown_no_coverage"
        assert entry["suggested_action"] == "add_tests"
        assert entry["hollow_heuristic_kinds"] == []


def test_empty_inputs_produce_empty_block() -> None:
    block = compute_test_focus([], None, None)
    assert block == {
        "available": True,
        "coverage_present": False,
        "entries": [],
        "total_focus_targets": 0,
    }


def test_bare_string_hotspot_entries_supported() -> None:
    block = compute_test_focus(["a.py", "b.py"], None, _empty_heuristics())
    assert {e["path"] for e in block["entries"]} == {"a.py", "b.py"}


def test_malformed_hotspot_entries_are_skipped() -> None:
    block = compute_test_focus(
        [{"path": "a.py"}, {"no_path": 1}, None, 42],
        _coverage({"a.py": 0.0}),
        _empty_heuristics(),
    )
    assert [e["path"] for e in block["entries"]] == ["a.py"]
