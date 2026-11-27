"""Tests for comparing two fleet runs, lib/fleet/fleet_compare.py (issue #552)."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

import lib.fleet.fleet_compare as fc
import lib.signal_meaning as sm
from lib.fleet.runner import FLEET_ROW_VERSION, FleetRow, write_rows
from lib.fleet.fleet_compare import FleetRun, compare, load_run, signal_changes
from lib.fleet.fleet_groups import roll_up, write_groups
from lib.fleet.fleet_view import FleetViewError
from lib.fleet.fleet_stamp import FleetStamp, build_stamp, write_stamp
from test_fleet_groups import LEVELS


def _scans(lines: int, shared: bool = True) -> dict[str, Any]:
    return {"nested_instructions": {"budget": {"claude_code": {"lines": lines}}},
            "shared_tooling": {"available": shared, "marketplace_ids": [], "ci_source_ids": []}}


def _row(repo: str, scans: dict[str, Any] | None, active: bool = True) -> FleetRow:
    ok = scans is not None
    return {"fleet_row_version": FLEET_ROW_VERSION, "repo": repo, "group": repo.rsplit("/", 1)[0],
            "status": "ok" if ok else "failed", "reason": None if ok else "scan_error",
            "signals_schema_version": 1 if ok else None, "scans": scans, "active": active if ok else None}


def _stamp(rows: list[FleetRow], **overrides: Any) -> FleetStamp:
    args: dict[str, Any] = {"toolkit_commit": "abc", "plugin_version": "1.2.0", "today": date(2026, 11, 20),
                            "window": "6mo", "levels_version": "fixture-1", "counts_only": True,
                            "paths_hashed": False, "scans": None}
    args.update(overrides)
    return build_stamp(rows, **args)


def _run(rows: list[FleetRow], **overrides: Any) -> FleetRun:
    return FleetRun(_stamp(rows, **overrides), rows, roll_up(rows, LEVELS, {}))


def _row_move(comparison: fc.Comparison, group: str, row_id: str) -> fc.RowMove:
    (g,) = [g for g in comparison["groups"] if g["group"] == group]
    (row,) = [r for r in g["rows"] if r["id"] == row_id]
    return row


EARLIER = [_row("grpa/svc1", _scans(0, shared=False)), _row("grpb/lib1", _scans(5)), _row("grpb/gone", _scans(5))]
LATER = [_row("grpa/svc1", _scans(20)), _row("grpb/lib1", _scans(5))]


def test_an_improvement_is_a_level_change_and_a_removal_a_set_change():
    comparison = compare(_run(EARLIER), _run(LATER))

    assert comparison["comparable"] and comparison["movement"]
    improved = _row_move(comparison, "grpa", "teamdocs")
    assert improved["then"] == {"status": "level", "level": 0}
    assert improved["now"] == {"status": "level", "level": 2}
    assert improved["cause"] == fc.CAUSE_LEVEL
    assert comparison["repositories"]["removed"] == ["grpb/gone"]
    (grpb,) = [g for g in comparison["groups"] if g["group"] == "grpb"]
    assert grpb["set_changed"] and grpb["set"]["removed"] == ["grpb/gone"]
    assert not _row_move(comparison, "grpb", "teamdocs")["moved"]
    (svc1,) = comparison["repos"]
    assert svc1["repo"] == "grpa/svc1"
    assert {c["path"] for c in svc1["signals"]} == {
        "nested_instructions.budget.claude_code.lines", "shared_tooling.available"}


def test_a_level_move_with_a_set_move_is_attributed_to_the_set():
    earlier = [_row("g/a", _scans(5)), _row("g/weak", _scans(0))]
    later = [_row("g/a", _scans(5))]
    move = _row_move(compare(_run(earlier), _run(later)), "g", "teamdocs")
    assert move["moved"] and move["cause"] == fc.CAUSE_SET


def test_a_level_move_with_set_and_evidence_moving_cannot_be_split():
    earlier = [_row("g/a", _scans(5, shared=False)), _row("g/weak", _scans(0))]
    later = [_row("g/a", _scans(5))]
    move = _row_move(compare(_run(earlier), _run(later)), "g", "teamdocs")
    assert move["cause"] == fc.CAUSE_SET_AND_LEVEL


def test_set_movement_kinds():
    earlier = [_row("g/fails", _scans(1)), _row("g/back", None), _row("g/sleeps", _scans(1)),
               _row("g/wakes", _scans(1), active=False)]
    later = [_row("g/fails", None), _row("g/back", _scans(1)), _row("g/sleeps", _scans(1), active=False),
             _row("g/wakes", _scans(1)), _row("g/new", _scans(1))]
    sets = compare(_run(earlier), _run(later))["repositories"]
    assert sets == {"added": ["g/new"], "removed": [], "no_longer_scanned": ["g/fails"],
                    "newly_scanned": ["g/back"], "no_longer_active": ["g/sleeps"], "newly_active": ["g/wakes"]}


def test_a_group_present_on_one_side_moves_as_a_set_change():
    comparison = compare(_run([_row("old/x", _scans(1))]), _run([_row("new/x", _scans(1))]))
    gone = _row_move(comparison, "old", "teamdocs")
    assert gone["now"] is None and gone["cause"] == fc.CAUSE_SET
    assert _row_move(comparison, "new", "teamdocs")["then"] is None


def test_an_org_row_moves_with_settings_not_with_the_set():
    frag_off = {"scope": "g", "provider": "gitlab", "two_factor": {"value": "off", "reason": None}}
    frag_on = {**frag_off, "two_factor": {"value": "on", "reason": None}}
    earlier_rows = [_row("g/a", _scans(1)), _row("g/b", _scans(1))]
    later_rows = [_row("g/a", _scans(1))]
    earlier = FleetRun(_stamp(earlier_rows), earlier_rows, roll_up(earlier_rows, LEVELS, {"g": frag_off}))
    later = FleetRun(_stamp(later_rows), later_rows, roll_up(later_rows, LEVELS, {"g": frag_on}))
    comparison = compare(earlier, later)
    move = _row_move(comparison, "g", "org2fa")
    assert move["moved"] and move["cause"] == fc.CAUSE_LEVEL
    (g,) = comparison["groups"]
    assert g["settings_changed"]


def test_an_org_row_on_both_sides_is_a_level_change_even_when_the_set_moved():
    earlier_rows = [_row("g/a", _scans(1)), _row("g/b", _scans(1))]
    later_rows = [_row("g/a", _scans(1))]
    earlier = _run(earlier_rows)
    later = _run(later_rows)
    for entry in later.groups[0]["levels"]:
        if entry["scope"] == "org":  # a level move with the settings unchanged
            entry["status"], entry["level"] = "level", 1
    comparison = compare(earlier, later)
    (g,) = comparison["groups"]
    assert g["set_changed"] and not g["settings_changed"]
    move = _row_move(comparison, "g", "org2fa")
    assert move["moved"] and move["cause"] == fc.CAUSE_LEVEL


def test_an_org_row_on_one_side_is_a_set_change():
    comparison = compare(_run([_row("old/x", _scans(1))]), _run([_row("new/x", _scans(1))]))
    assert _row_move(comparison, "old", "org2fa")["cause"] == fc.CAUSE_SET


def test_an_unchanged_rerun_shows_no_movement():
    comparison = compare(_run(EARLIER), _run(EARLIER))
    assert comparison["comparable"] and not comparison["movement"]
    assert comparison["repos"] == []
    assert all(not row["moved"] and row["cause"] is None for g in comparison["groups"] for row in g["rows"])
    assert fc.comparison_lines(comparison) == ["no movement"]


@pytest.mark.parametrize(("override", "reason"), [
    ({"levels_version": "fixture-2"}, "maturity-model version changed"),
    ({"window": "90d"}, "window changed"),
    ({"counts_only": False}, "counts-only mode changed"),
    ({"scans": ["agent_ops"]}, "scan selection changed"),
    ({"paths_hashed": True}, "path hashing changed"),
])
def test_runs_that_differ_in_what_was_measured_are_not_comparable(override, reason):
    comparison = compare(_run(EARLIER), _run(LATER, **override))
    assert not comparison["comparable"]
    assert any(reason in r for r in comparison["reasons"])
    assert comparison["groups"] == [] and comparison["repos"] == [] and not comparison["movement"]
    assert fc.comparison_lines(comparison)[0].startswith("not comparable: ")


def test_runs_across_a_major_bump_under_the_same_model_are_comparable():
    # #867: the stamped versions decide comparability, not the plugin's major version
    comparison = compare(_run(EARLIER, plugin_version="2.9.0"), _run(LATER, plugin_version="3.1.0"))
    assert comparison["comparable"] is True and comparison["reasons"] == []
    assert _row_move(comparison, "grpa", "teamdocs")["cause"] == fc.CAUSE_LEVEL
    assert fc.comparability(_stamp(EARLIER, plugin_version="2.9.0"), _stamp(LATER, plugin_version="4.0.0")) == ([], [])


def _versioned(repo: str, scans: dict[str, Any], version: int) -> FleetRow:
    return {**_row(repo, scans), "signals_schema_version": version}


def test_a_signal_whose_meaning_changed_reads_as_absent_in_the_earlier_run():
    # #867: observability.rung changed meaning at signals_schema_version 4 (#823)
    then = [_versioned("g/a", {"observability": {"rung": 1, "kept": 1}}, 3)]
    now = [_versioned("g/a", {"observability": {"rung": 3, "kept": 2}}, 4)]
    (repo,) = compare(_run(then), _run(now))["repos"]
    assert repo["signals"] == [{"path": "observability.kept", "then": 1, "now": 2},
                               {"path": "observability.rung", "now": 3}]
    same = [_versioned("g/a", {"observability": {"rung": 1, "kept": 1}}, 4)]
    (repo,) = compare(_run(same), _run(now))["repos"]
    assert {"path": "observability.rung", "then": 1, "now": 3} in repo["signals"]


def test_a_level_move_on_a_changed_signal_is_measured_set_movement(monkeypatch):
    # #867: a row that moved on a signal whose meaning changed did not move on the repository
    monkeypatch.setattr(sm, "MEANING_CHANGES", {2: ("shared_tooling.available",)})
    earlier = [_versioned("grpa/svc1", _scans(20, shared=False), 1)]
    later = [_versioned("grpa/svc1", _scans(20), 2)]
    comparison = compare(_run(earlier), _run(later))
    move = _row_move(comparison, "grpa", "teamdocs")
    assert move["moved"] and move["cause"] == fc.CAUSE_SET
    assert move["absent_signals"] == ["shared_tooling.available"]
    (repo,) = comparison["repos"]
    assert repo["signals"] == [{"path": "shared_tooling.available", "now": True}]


def test_a_schema_version_this_toolkit_does_not_know_reads_every_earlier_signal_as_absent():
    then = [_versioned("g/a", {"observability": {"kept": 1}}, 1)]
    now = [_versioned("g/a", {"observability": {"kept": 1}}, 99)]
    (repo,) = compare(_run(then), _run(now))["repos"]
    assert repo["signals"] == [{"path": "observability.kept", "now": 1}]
    unknown = [_versioned("g/a", {"observability": {"kept": 1}}, 99)]
    rerun = compare(_run(unknown), _run(unknown))
    assert rerun["repos"] == [] and not rerun["movement"]  # one version, known or not: as they are


def test_the_later_run_at_the_lower_schema_version_is_the_side_that_reads_as_absent():
    then = _versioned("g/a", {"observability": {"rung": 3, "kept": 1}}, 4)
    now = _versioned("g/a", {"observability": {"rung": 1, "kept": 1}}, 3)
    assert fc.aligned(then, now) == ({"observability": {"rung": 3, "kept": 1}}, {"observability": {"kept": 1}},
                                     ["observability.rung"])
    (repo,) = compare(_run([then]), _run([now]))["repos"]
    assert repo["signals"] == [{"path": "observability.rung", "then": 3}]


def test_hashed_paths_on_both_sides_carry_a_note():
    comparison = compare(_run(EARLIER, paths_hashed=True), _run(LATER, paths_hashed=True))
    assert comparison["comparable"] and comparison["notes"] == [fc.HASH_NOTE]


def test_signal_changes_report_leaves_and_absent_sides():
    then = {"a": {"x": 1, "y": [1, 2]}, "gone": {"v": True}}
    now = {"a": {"x": 2, "y": [1, 2]}, "new": {"v": 0}}
    assert signal_changes(then, now) == [
        {"path": "a.x", "then": 1, "now": 2},
        {"path": "gone.v", "then": True},
        {"path": "new.v", "now": 0},
    ]
    assert signal_changes({"a": {}}, {"a": {}}) == []


def test_summary_lines_list_set_and_level_moves():
    lines = fc.comparison_lines(compare(_run(EARLIER), _run(LATER)))
    assert "removed: grpb/gone" in lines
    assert "grpa teamdocs: 0 -> 2 (level)" in lines


# --- loading and writing ---------------------------------------------------


def _write_run(out: Path, rows: list[FleetRow]) -> FleetRun:
    run = _run(rows)
    write_rows(out, rows)
    write_groups(out, run.groups)
    write_stamp(out, run.stamp)
    return run


def test_load_run_round_trips_and_comparison_is_byte_stable(tmp_path):
    earlier = _write_run(tmp_path / "e", EARLIER)
    later = _write_run(tmp_path / "l", LATER)
    loaded = load_run(tmp_path / "e")
    assert loaded.stamp == earlier.stamp and loaded.repos == sorted(EARLIER, key=lambda r: r["repo"])
    first = fc.dumps_comparison(compare(load_run(tmp_path / "e"), load_run(tmp_path / "l")))
    assert first == fc.dumps_comparison(compare(earlier, later))
    assert str(tmp_path) not in first


def test_load_run_refuses_other_versions_and_missing_files(tmp_path):
    _write_run(tmp_path / "r", EARLIER)
    stamp = json.loads((tmp_path / "r" / "run.json").read_text(encoding="utf-8"))
    (tmp_path / "r" / "run.json").write_text(json.dumps({**stamp, "fleet_stamp_version": 99}), encoding="utf-8")
    with pytest.raises(fc.RunVersionError, match="stamp version"):
        load_run(tmp_path / "r")

    _write_run(tmp_path / "rows", EARLIER)
    (tmp_path / "rows" / "repos.jsonl").write_text('{"fleet_row_version": 1}\n', encoding="utf-8")
    with pytest.raises(fc.RunVersionError, match="fleet_row_version 1"):
        load_run(tmp_path / "rows")

    _write_run(tmp_path / "unstamped", EARLIER)
    (tmp_path / "unstamped" / "run.json").unlink()
    with pytest.raises(fc.RunVersionError, match="before the run stamp"):
        load_run(tmp_path / "unstamped")

    with pytest.raises(FleetViewError, match="not a fleet run"):
        load_run(tmp_path / "absent")
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "run.json").write_text("[1]", encoding="utf-8")
    with pytest.raises(FleetViewError, match="not a JSON object"):
        load_run(tmp_path / "bad")
    (tmp_path / "bad" / "run.json").write_text("{", encoding="utf-8")
    with pytest.raises(FleetViewError, match="cannot read"):
        load_run(tmp_path / "bad")


def test_first_version_reports_malformed_lines(tmp_path):
    path = tmp_path / "x.jsonl"
    path.write_text("\n{bad\n", encoding="utf-8")
    with pytest.raises(FleetViewError, match="not JSON"):
        fc._first_version(path, "k")
    path.write_text("[1]\n", encoding="utf-8")
    assert fc._first_version(path, "k") is None
    with pytest.raises(FleetViewError, match="cannot read"):
        fc._first_version(tmp_path / "absent", "k")


def test_write_and_clear_comparison_touch_only_this_tools_file(tmp_path, monkeypatch):
    comparison = compare(_run(EARLIER), _run(LATER))
    path = fc.write_comparison(tmp_path, comparison)
    assert json.loads(path.read_text(encoding="utf-8")) == json.loads(fc.dumps_comparison(comparison))
    assert fc.clear_stale_comparison(tmp_path) and not path.exists()
    assert not fc.clear_stale_comparison(tmp_path)

    path.write_text('{"mine": 1}', encoding="utf-8")
    assert not fc.clear_stale_comparison(tmp_path) and path.exists()
    path.write_text("not json", encoding="utf-8")
    assert not fc.clear_stale_comparison(tmp_path) and path.exists()
    path.unlink()
    real = tmp_path / "elsewhere.json"
    fc.write_comparison(tmp_path / "w", comparison).rename(real)
    path.symlink_to(real)
    assert not fc.clear_stale_comparison(tmp_path) and real.exists()

    def boom(*_args: object) -> None:
        raise OSError("disk full")
    monkeypatch.setattr(fc.os, "replace", boom)
    with pytest.raises(OSError):
        fc.write_comparison(tmp_path / "fail", comparison)
    assert list((tmp_path / "fail").iterdir()) == []


# --- the platform pass (#714) ----------------------------------------------


def test_a_platform_run_does_not_compare_with_an_offline_run():
    comparison = compare(_run(EARLIER), _run(LATER, platform=True))
    assert not comparison["comparable"]
    assert any(r.startswith("platform pass changed False->True") for r in comparison["reasons"])
    assert compare(_run(EARLIER, platform=True), _run(LATER, platform=True))["comparable"]


def test_a_managed_settings_run_does_not_compare_with_one_without_it():
    """#1153: reading the machine's policy moves the permissions practice, so it is a
    comparability key, and a stamp written before it reads as not read."""
    comparison = compare(_run(EARLIER), _run(LATER, managed_settings=True))
    assert not comparison["comparable"]
    assert "managed settings read changed False->True" in comparison["reasons"]
    old: Any = {k: v for k, v in _stamp(EARLIER).items() if k != "managed_settings"}
    assert fc.comparability(old, _stamp(LATER)) == ([], [])


def test_a_stamp_from_before_714_reads_as_no_platform_pass():
    old: Any = {k: v for k, v in _stamp(EARLIER).items() if k != "platform"}
    assert fc.comparability(old, _stamp(LATER)) == ([], [])
    assert fc.comparability(old, _stamp(LATER, platform=True))[0] != []


TEAM = {"name": "team", "version": "2026.11", "sha256": "a" * 64}


def test_two_stamps_differing_only_in_packs_give_the_rule_reason():
    """#1273: a run scored with another practice pack is not comparable, like a rules change."""
    then, now = _stamp(EARLIER, packs=[TEAM]), _stamp(LATER, packs=[{**TEAM, "sha256": "b" * 64}])
    reasons, _ = fc.comparability(then, now)
    assert reasons == ["practice packs changed team@aaaaaaaaaaaa->team@bbbbbbbbbbbb"]
    assert fc.comparability(_stamp(EARLIER, packs=[TEAM]), _stamp(LATER, packs=[TEAM])) == ([], [])


def test_a_stamp_without_packs_reads_as_no_pack():
    """A stamp written before #1273, or by a run without --pack, has no packs key."""
    assert "packs" not in _stamp(EARLIER)
    assert fc.comparability(_stamp(EARLIER), _stamp(LATER, packs=[])) == ([], [])
    reasons, _ = fc.comparability(_stamp(EARLIER), _stamp(LATER, packs=[TEAM]))
    assert reasons == ["practice packs changed none->team@aaaaaaaaaaaa"]


def test_two_runs_differing_only_in_packs_are_refused_by_compare():
    """#1276: --compare of two runs whose packs differ is not comparable, so no movement is
    read from a pack coming or going."""
    comparison = compare(_run(EARLIER), _run(LATER, packs=[TEAM]))
    assert not comparison["comparable"]
    assert comparison["reasons"] == ["practice packs changed none->team@aaaaaaaaaaaa"]
    assert compare(_run(EARLIER, packs=[TEAM]), _run(LATER, packs=[TEAM]))["comparable"]
