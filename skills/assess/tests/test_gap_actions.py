"""Unit tests for lib.gap_actions: the deterministic Top 3 gap candidates."""
from __future__ import annotations

from lib.gap_actions import REACHABILITY_FLOOR, build_gap_actions

SOFTWARE = {"available": True, "archetype": "software"}
KB = {"available": True, "archetype": "knowledge-base"}
NO_COVERAGE = {"available": False, "source": "none found"}
COVERAGE = {"available": True, "source": "lcov.info", "format": "lcov", "parsed": True}
HOTSPOTS = [{"path": p, "loc": 2, "ccn": 1.0, "commits": 12}
            for p in ("src/zeta.py", "src/mid.py", "src/a.py", "src/b.py")]


def _docs(pct: float, doc_count: int = 10, unreachable: list[str] | None = None) -> dict:
    return {"available": True, "doc_count": doc_count, "reachability_pct": pct,
            "unreachable": unreachable or []}


def test_gap_actions_floor_is_one_half() -> None:
    assert REACHABILITY_FLOOR == 0.5


def test_gap_actions_coverage_entry_names_top_hotspots() -> None:
    gaps = build_gap_actions(NO_COVERAGE, _docs(1.0), HOTSPOTS, SOFTWARE)
    assert len(gaps) == 1
    assert gaps[0]["signal"] == "coverage_report"
    assert gaps[0]["action"]
    assert gaps[0]["paths"] == ["src/zeta.py", "src/mid.py", "src/a.py"]


def test_gap_actions_empty_when_coverage_present_and_docs_reachable() -> None:
    assert build_gap_actions(COVERAGE, _docs(0.8), HOTSPOTS, SOFTWARE) == []


def test_gap_actions_reachability_entry_below_floor() -> None:
    gaps = build_gap_actions(COVERAGE, _docs(0.3, unreachable=["docs/z.md", "docs/a.md"]), HOTSPOTS, SOFTWARE)
    assert [g["signal"] for g in gaps] == ["doc_graph"]
    assert gaps[0]["paths"] == ["docs/a.md", "docs/z.md"]
    assert "30%" in gaps[0]["action"]


def test_gap_actions_reachability_at_floor_does_not_fire() -> None:
    assert build_gap_actions(COVERAGE, _docs(REACHABILITY_FLOOR), HOTSPOTS, SOFTWARE) == []


def test_gap_actions_coverage_comes_before_reachability() -> None:
    gaps = build_gap_actions(NO_COVERAGE, _docs(0.1), HOTSPOTS, SOFTWARE)
    assert [g["signal"] for g in gaps] == ["coverage_report", "doc_graph"]


def test_gap_actions_no_markdown_is_not_a_reachability_gap() -> None:
    """A repo with no docs reports reachability 0.0 with available true: nothing
    is unreachable, so no link-the-docs action fires."""
    no_docs = {"available": True, "reason": "no markdown docs found",
               "doc_count": 0, "reachability_pct": 0.0}
    assert build_gap_actions(COVERAGE, no_docs, HOTSPOTS, SOFTWARE) == []


def test_gap_actions_unavailable_doc_graph_is_not_a_gap() -> None:
    assert build_gap_actions(COVERAGE, {"available": False, "reachability_pct": 0.0}, HOTSPOTS, SOFTWARE) == []


def test_gap_actions_missing_blocks_degrade_to_empty() -> None:
    assert build_gap_actions(None, None, None, None) == []


def test_gap_actions_no_coverage_entry_on_knowledge_base() -> None:
    assert build_gap_actions(NO_COVERAGE, _docs(1.0), HOTSPOTS, KB) == []


def test_gap_actions_no_coverage_entry_without_hotspots() -> None:
    assert build_gap_actions(NO_COVERAGE, _docs(1.0), [], SOFTWARE) == []


def test_gap_actions_coverage_entry_skips_archive_hotspots() -> None:
    hot = [{"path": "archive/legacy_service.py"}, {"path": "attic/old.py"}, *HOTSPOTS]
    gaps = build_gap_actions(NO_COVERAGE, _docs(1.0), hot, SOFTWARE)
    assert gaps[0]["paths"] == ["src/zeta.py", "src/mid.py", "src/a.py"]
    only_archive = [{"path": "archive/legacy_service.py"}]
    assert build_gap_actions(NO_COVERAGE, _docs(1.0), only_archive, SOFTWARE) == []
