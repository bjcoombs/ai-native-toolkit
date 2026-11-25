"""Unit tests for lib.report.gap_actions: the deterministic Top 3 gap candidates."""
from __future__ import annotations

from lib.report.gap_actions import REACHABILITY_FLOOR, build_gap_actions

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


def _nested(*paths: str) -> dict:
    return {"available": True, "files": [{"path": p, "grade": "C"} for p in paths]}


def test_gap_actions_nested_only_names_a_root_file_and_the_check_command() -> None:
    """#821: nested-only instruction files get the root-file advice, first."""
    grades = {"instruction_presence": "nested_only", "instruction_file_present": True,
              "instructions_grade": "C"}
    gaps = build_gap_actions(NO_COVERAGE, _docs(0.1), HOTSPOTS, SOFTWARE,
                             grades, _nested("pkg/z/AGENTS.md", "pkg/AGENTS.md"))
    assert [g["signal"] for g in gaps] == ["layer_grades", "coverage_report", "doc_graph"]
    action = gaps[0]["action"]
    assert "root instruction file" in action and "check command" in action
    assert gaps[0]["paths"] == ["pkg/AGENTS.md", "pkg/z/AGENTS.md"]


def test_gap_actions_root_and_none_presence_add_no_root_file_entry() -> None:
    for presence in ("root", "none", None):
        grades = {"instruction_presence": presence}
        assert build_gap_actions(COVERAGE, _docs(0.8), HOTSPOTS, SOFTWARE,
                                 grades, _nested("pkg/AGENTS.md")) == []


def test_each_gap_action_carries_its_scans_manifest_why() -> None:
    """#939: an entry's why is the why of the scan its signal names, static text."""
    from lib.manifest.check_manifest import core_manifest
    from lib.manifest.manifest_whys import scan_whys

    grades = {"instruction_presence": "nested_only", "instruction_file_present": True,
              "instructions_grade": "C"}
    gaps = build_gap_actions(NO_COVERAGE, _docs(0.1), HOTSPOTS, SOFTWARE,
                             grades, _nested("pkg/AGENTS.md"))
    whys = scan_whys(core_manifest())
    assert [g["why"] for g in gaps] == [whys["layer_grades"], whys["coverage_report"], whys["doc_graph"]]
    assert gaps[1]["why"] == ("It reads a coverage report the project already produced, so test depth "
                              "on the riskiest files is known instead of assumed.")


# --- removal proposals (#1084) ---------------------------------------------------

def _placement(lines: int, flagged: bool, relocatable: list[str] | None = None) -> dict:
    return {"available": True, "files": [{
        "path": "CLAUDE.md", "kind": "instruction", "always_on_lines": lines, "flagged": flagged,
        "relocatable": [{"heading": h} for h in (relocatable or [])]}]}


def test_a_flagged_instruction_file_gets_a_remove_action_with_its_measured_cost() -> None:
    from lib.report.gap_actions import REMOVAL_FINDING, REMOVE_MODE
    (gap,) = build_gap_actions(None, None, None, None, instruction_placement=_placement(312, True, ["Go tooling"]))
    assert (gap["signal"], gap["finding"], gap["mode"], gap["paths"]) == (
        "instruction_placement", REMOVAL_FINDING, REMOVE_MODE, ["CLAUDE.md"])
    assert gap["cost"] == {"metric": "always_on_lines", "path": "CLAUDE.md", "value": 312}
    assert "312" in gap["action"] and "`Go tooling`" in gap["action"] and "gate still holds" in gap["action"]
    assert gap["why"]  # the instruction_placement scan's manifest why


def test_the_trigger_is_the_scans_own_flag_never_a_second_size() -> None:
    """Over 200 always-on lines but not flagged (the share is under the scan's 0.5): no removal."""
    assert build_gap_actions(None, None, None, None, instruction_placement=_placement(400, False)) == []
    assert build_gap_actions(None, None, None, None, instruction_placement={"available": False}) == []


def test_finalize_gives_a_removal_the_remove_mode_and_keeps_keyhole_modes() -> None:
    from lib.report.gap_actions import REMOVAL_FINDING, action_mode
    from lib.keyhole.keyhole_signals import mode_for_finding
    assert action_mode(REMOVAL_FINDING) == "remove"
    assert action_mode("lying_map") == mode_for_finding("lying_map")
    assert action_mode(None) == mode_for_finding(None)


def test_counts_only_keeps_a_removals_cost_mode_and_finding_and_drops_its_words() -> None:
    from lib.counts_only.content_free import counts_only
    (gap,) = build_gap_actions(None, None, None, None, instruction_placement=_placement(312, True))
    kept = counts_only({"gap_actions": [gap]})["gap_actions"][0]
    assert (kept["finding"], kept["mode"], kept["signal"]) == ("instruction_always_on", "remove", "instruction_placement")
    assert (kept["cost"]["metric"], kept["cost"]["value"]) == ("always_on_lines", 312)
    assert "action" not in kept and "why" not in kept


def test_action_mode_values_close_over_every_mode_finalize_writes() -> None:
    from lib.report.gap_actions import ACTION_MODE_VALUES, REMOVAL_FINDING, action_mode
    from lib.keyhole.keyhole_signals import FINDING_MODES
    assert {action_mode(f) for f in [*FINDING_MODES, REMOVAL_FINDING, None]} <= ACTION_MODE_VALUES
    assert "remove" in ACTION_MODE_VALUES
