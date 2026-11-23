"""Tests for scanner-change attribution of fleet comparison moves, lib/fleet/fleet_scanner_diff.py (issue #774)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import lib.fleet.fleet_compare as fc
import lib.fleet.fleet_scanner_diff as sd
from lib.fleet.fleet_compare import FleetRun, compare, summary_lines
from lib.fleet.fleet_groups import roll_up
from lib.fleet.fleet_scanner_diff import ScannerDiff, scanner_diff
from test_fleet_compare import _row, _row_move, _scans, _stamp
from test_fleet_groups import LEVELS

NESTED = "skills/assess/scripts/lib/instruction_files.py"
PRODUCERS = {"nested_instructions": frozenset({NESTED}),
             "shared_tooling": frozenset({"skills/assess/scripts/lib/shared_tooling.py"})}
EARLIER = [_row("grpa/svc1", _scans(5))]
LATER = [_row("grpa/svc1", _scans(0))]  # teamdocs 2 -> 0 on nested_instructions


def _run(rows: list[Any], signals: dict[str, frozenset[str]] | None = None, **stamp: Any) -> FleetRun:
    return FleetRun(_stamp(rows, **stamp), rows, roll_up(rows, LEVELS, {}), signals)


def _changed(*paths: str) -> ScannerDiff:
    return {"status": "changed", "changed": list(paths), "reason": None}


# --- the three triage tests ----------------------------------------------------


def test_changed_producer_module_tags_scanner_changed():
    comparison = compare(_run(EARLIER, toolkit_commit="aaa"), _run(LATER, toolkit_commit="bbb"),
                         _changed(NESTED), PRODUCERS)

    row = _row_move(comparison, "grpa", "teamdocs")
    assert row["moved"] and row["cause"] == sd.CAUSE_SCANNER_CHANGED
    assert row["scanner"] == [{"block": "nested_instructions", "modules": [NESTED]}]
    (line,) = [line for line in summary_lines(comparison) if "teamdocs" in line]
    assert "scanner_changed" in line and NESTED in line and "not read as a repository move" in line


def test_no_module_change_renders_repo_delta():
    unrelated = _changed("skills/assess/scripts/lib/agents/agent_ops.py")
    comparison = compare(_run(EARLIER, toolkit_commit="aaa"), _run(LATER, toolkit_commit="bbb"),
                         unrelated, PRODUCERS)
    row = _row_move(comparison, "grpa", "teamdocs")
    assert row["cause"] == fc.CAUSE_LEVEL and "scanner" not in row

    same = scanner_diff(_run(EARLIER).stamp, _run(LATER).stamp, diff=_no_git)
    assert same["status"] == "same"
    assert _row_move(compare(_run(EARLIER), _run(LATER), same, PRODUCERS), "grpa", "teamdocs")["cause"] \
        == fc.CAUSE_LEVEL


def test_missing_toolkit_commit_degrades_to_unknown_scope():
    earlier = _run(EARLIER, toolkit_commit=None, plugin_version="1.1.0")
    later = _run(LATER, toolkit_commit="bbb")
    diff = scanner_diff(earlier.stamp, later.stamp, diff=_no_git)
    assert diff["status"] == "unavailable"
    assert diff["reason"] == "the earlier run recorded no toolkit_commit (plugin_version '1.1.0'->'1.2.0')"

    comparison = compare(earlier, later, diff, PRODUCERS)
    assert _row_move(comparison, "grpa", "teamdocs")["cause"] == sd.CAUSE_UNKNOWN_SCOPE
    (note,) = comparison["notes"]
    assert note.startswith("scanner diff unavailable") and "toolkit_commit" in note
    assert note in summary_lines(comparison)


# --- scanner_diff --------------------------------------------------------------


def _no_git(root: Path, a: str, b: str) -> list[str] | None:
    raise AssertionError("git must not run")


def test_same_commit_never_runs_git_and_one_null_commit_is_unavailable_at_any_version():
    assert scanner_diff(_stamp([], toolkit_commit="x"), _stamp([], toolkit_commit="x"), diff=_no_git)["status"] == "same"
    assert scanner_diff(_stamp([], toolkit_commit=None), _stamp([], toolkit_commit=None),
                        diff=_no_git)["status"] == "same"
    for then, now in ((None, "a" * 40), ("a" * 40, None)):
        one_null = scanner_diff(_stamp([], toolkit_commit=then), _stamp([], toolkit_commit=now), diff=_no_git)
        assert one_null["status"] == "unavailable"
        assert one_null["reason"] is not None and "recorded no toolkit_commit" in one_null["reason"]
    both_null = scanner_diff(_stamp([], toolkit_commit=None, plugin_version="1.0.0"),
                             _stamp([], toolkit_commit=None, plugin_version="1.1.0"), diff=_no_git)
    assert both_null["status"] == "unavailable"


def test_a_diff_git_cannot_answer_is_unavailable_and_an_empty_one_is_same():
    def fails(root: Path, a: str, b: str) -> list[str] | None:
        return None

    def empty(root: Path, a: str, b: str) -> list[str] | None:
        return []

    earlier, later = _stamp([], toolkit_commit="a" * 40), _stamp([], toolkit_commit="b" * 40)
    assert scanner_diff(earlier, later, diff=empty)["status"] == "same"
    assert scanner_diff(earlier, later, diff=fails)["status"] == "unavailable"
    assert scanner_diff(earlier, later, diff=empty)["status"] == "same"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=True).stdout.strip()


def test_git_changed_files_lists_lib_files_between_two_commits(tmp_path):
    lib = tmp_path / sd.LIB_PATH
    lib.mkdir(parents=True)
    (lib / "ci_checks.py").write_text("A = 1\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("x\n", encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "one")
    first = _git(tmp_path, "rev-parse", "HEAD")
    (lib / "ci_checks.py").write_text("A = 2\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("y\n", encoding="utf-8")
    _git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qam", "two")
    second = _git(tmp_path, "rev-parse", "HEAD")

    assert sd.git_changed_files(tmp_path, first, second) == [f"{sd.LIB_PATH}/ci_checks.py"]
    assert sd.git_changed_files(tmp_path, first, "0" * 40) is None
    assert sd.git_changed_files(tmp_path / "missing", first, second) is None


# --- producers -----------------------------------------------------------------


def test_producers_map_each_block_to_its_defining_module_and_what_it_reads():
    producer_map = sd.producers()
    assert f"{sd.LIB_PATH}/ci_checks.py" in producer_map["ci_checks"]
    assert f"{sd.LIB_PATH}/check_command.py" in producer_map["ci_checks"]
    assert f"{sd.LIB_PATH}/graph/import_graph.py" in producer_map["structure"]  # via the core's structure_scan
    assert all(f"{sd.LIB_PATH}/run_context_types.py" not in paths for paths in producer_map.values())
    assert "structure_scan" not in producer_map  # a private spec publishes no block
    # a lib/ package and its relative imports (lib/test_pressure/)
    assert f"{sd.LIB_PATH}/test_pressure/mutation.py" in producer_map["test_pressure"]
    assert f"{sd.LIB_PATH}/test_pressure/__init__.py" in producer_map["test_pressure"]
    blocks = sd.changed_blocks(_changed(f"{sd.LIB_PATH}/ci_checks.py"), producer_map)
    assert blocks["ci_checks"] == [f"{sd.LIB_PATH}/ci_checks.py"]


# --- row attribution -----------------------------------------------------------


def test_a_row_reading_another_block_keeps_its_level_cause():
    reads = {"teamdocs": frozenset({"shared_tooling.available"})}
    comparison = compare(_run(EARLIER, reads, toolkit_commit="aaa"), _run(LATER, reads, toolkit_commit="bbb"),
                         _changed(NESTED), PRODUCERS)
    assert _row_move(comparison, "grpa", "teamdocs")["cause"] == fc.CAUSE_LEVEL


def test_a_signal_absent_from_the_earlier_scans_is_measured_set_movement():
    earlier = [_row("grpa/svc1", {"shared_tooling": {"available": True, "marketplace_ids": [],
                                                      "ci_source_ids": []}})]
    later = [_row("grpa/svc1", _scans(5))]
    comparison = compare(_run(earlier, toolkit_commit="aaa"), _run(later, toolkit_commit="bbb"),
                         _changed(NESTED), PRODUCERS)

    row = _row_move(comparison, "grpa", "teamdocs")
    assert row["then"]["status"] == "unknown" and row["now"] == {"status": "level", "level": 2}
    assert row["cause"] == fc.CAUSE_SET
    assert row["absent_signals"] == ["nested_instructions.budget.claude_code.lines"]
    assert any("measured-set movement" in line for line in summary_lines(comparison))
    # the other way round (a later scanner dropped the signal) is the same kind of movement
    back = compare(_run(later), _run(earlier))
    assert _row_move(back, "grpa", "teamdocs")["cause"] == fc.CAUSE_SET


def test_without_a_scanner_diff_compare_is_unchanged():
    comparison = compare(_run(EARLIER, toolkit_commit="aaa"), _run(LATER, toolkit_commit="bbb"))
    assert _row_move(comparison, "grpa", "teamdocs")["cause"] == fc.CAUSE_LEVEL
    assert comparison["notes"] == []


def test_row_signals_reads_fleet_data_cells():
    data = {"repos": [{"rows": [{"row": "teamdocs", "signal": None},
                                {"row": "teamdocs", "signal": "nested_instructions.budget.claude_code.lines"}]}]}
    assert sd.row_signals(data) == {"teamdocs": frozenset({"nested_instructions.budget.claude_code.lines"})}
    assert sd.row_signals([]) is None


# --- wiring ----------------------------------------------------------------------


def test_the_toolkit_root_holds_the_lib_directory_it_diffs():
    from lib.signals import TOOLKIT_ROOT

    assert sd.TOOLKIT_ROOT == TOOLKIT_ROOT
    assert (sd.TOOLKIT_ROOT / sd.LIB_PATH).is_dir()


def _write(out: Path, rows: list[Any], fleet_data: dict[str, Any] | None = None, **stamp: Any) -> None:
    from lib.fleet.runner import write_rows
    from lib.fleet.fleet_groups import write_groups
    from lib.fleet.fleet_stamp import write_stamp

    run = _run(rows, **stamp)
    write_rows(out, rows)
    write_groups(out, run.groups)
    write_stamp(out, run.stamp)
    if fleet_data is not None:
        (out / "fleet-data.json").write_text(json.dumps(fleet_data), encoding="utf-8")


def test_load_run_carries_the_row_signals_of_fleet_data(tmp_path):
    cells = {"repos": [{"rows": [{"row": "teamdocs", "signal": "shared_tooling.available"}]}]}
    _write(tmp_path / "a", EARLIER, cells)
    _write(tmp_path / "b", EARLIER)
    assert fc.load_run(tmp_path / "a").row_signals == {"teamdocs": frozenset({"shared_tooling.available"})}
    assert fc.load_run(tmp_path / "b").row_signals is None


def test_the_compare_cli_degrades_to_unknown_scope(tmp_path, capsys):
    from test_fleet_history import compare_cli

    _write(tmp_path / "a", EARLIER, toolkit_commit=None, plugin_version="1.1.0")
    _write(tmp_path / "b", LATER, toolkit_commit="bbb", plugin_version="1.2.0")
    assert compare_cli.main([str(tmp_path / "a"), str(tmp_path / "b")]) == 0
    captured = capsys.readouterr()
    comparison = json.loads(captured.out)
    assert _row_move(comparison, "grpa", "teamdocs")["cause"] == sd.CAUSE_UNKNOWN_SCOPE
    assert comparison["notes"][0].startswith("scanner diff unavailable")
    assert "fleet compare: scanner diff unavailable" in captured.err


def test_fleet_compare_flag_passes_the_scanner_diff(tmp_path, capsys):
    from test_fleet import make_repo
    from test_fleet_cli import fleet_cli

    root = tmp_path / "estate"
    make_repo(root, "acme/svc1", files={"CLAUDE.md": "a\n"})
    args = [str(root), "--scans", "nested_instructions", "--no-history", "--no-diagrams"]
    assert fleet_cli.main([*args, "--out", str(tmp_path / "o1")]) == 0
    stamp_path = tmp_path / "o1" / "run.json"
    stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
    # an install that is not a git checkout, one minor release earlier: comparable, diff unknown
    stamp.update(toolkit_commit=None, plugin_version=f"{stamp['plugin_version'].split('.')[0]}.999.0")
    stamp_path.write_text(json.dumps(stamp), encoding="utf-8")
    capsys.readouterr()

    assert fleet_cli.main([*args, "--out", str(tmp_path / "o2"), "--compare", str(tmp_path / "o1")]) == 0
    comparison = json.loads((tmp_path / "o2" / "compare.json").read_text(encoding="utf-8"))
    assert comparison["comparable"], comparison["reasons"]
    assert comparison["notes"][0].startswith("scanner diff unavailable")
    assert "fleet: compare: scanner diff unavailable" in capsys.readouterr().err


def test_a_degraded_block_is_not_an_absent_signal():
    degraded = {**_scans(5), "nested_instructions": {"available": False, "reason": "scan_error"}}
    comparison = compare(_run([_row("grpa/svc1", degraded)]), _run([_row("grpa/svc1", _scans(5))]))
    row = _row_move(comparison, "grpa", "teamdocs")
    assert row["then"]["status"] == "unknown"
    assert row["cause"] == fc.CAUSE_LEVEL and "absent_signals" not in row


def test_a_degraded_nested_block_is_not_an_absent_signal():
    # agent_environment.ci_duration-style: the top-level block is fine, a sub-block degraded
    degraded = {**_scans(5), "nested_instructions": {"budget": {"available": False, "reason": "scan_error"}}}
    comparison = compare(_run([_row("grpa/svc1", degraded)]), _run([_row("grpa/svc1", _scans(5))]))
    row = _row_move(comparison, "grpa", "teamdocs")
    assert row["then"]["status"] == "unknown"
    assert row["cause"] == fc.CAUSE_LEVEL and "absent_signals" not in row


def test_degraded_walks_the_signal_path():
    scans = {"a": {"b": {"available": False}, "c": {"d": 1}}, "e": {"available": False}}
    assert sd._degraded(scans, "a.b.x") and sd._degraded(scans, "e.f") and sd._degraded(scans, "a.b")
    assert not sd._degraded(scans, "a.c.d") and not sd._degraded(scans, "a.z.y")
    assert not sd._degraded(None, "a.b") and not sd._degraded({"a": 3}, "a.b")


def test_a_toolkit_commit_that_is_not_a_hash_never_reaches_git(tmp_path):
    target = tmp_path / "written"
    for bad in (f"--output={target}", "HEAD", "abc", "a" * 41, "ABCDEF1"):
        diff = scanner_diff(_stamp([], toolkit_commit=bad), _stamp([], toolkit_commit="b" * 40), diff=_no_git)
        assert diff["status"] == "unavailable" and "not a commit hash" in (diff["reason"] or "")
    # git_changed_files itself ends option parsing before the commits
    assert sd.git_changed_files(sd.TOOLKIT_ROOT, f"--output={target}", "HEAD") is None
    assert not target.exists()


def test_a_set_and_level_row_keeps_its_set_movement_under_a_new_cause():
    for diff, cause in ((_changed(NESTED), sd.CAUSE_SCANNER_CHANGED),
                        ({"status": "unavailable", "changed": [], "reason": "r"}, sd.CAUSE_UNKNOWN_SCOPE)):
        result = sd.attribute("set_and_level", absent=[], diff=diff,
                              blocks_changed={"nested_instructions": [NESTED]}, row_blocks=None,
                              moved_blocks={"nested_instructions"})
        assert result["cause"] == cause and result["set_moved"] is True
    kept = sd.attribute("level", absent=[], diff=_changed(NESTED), blocks_changed={"nested_instructions": [NESTED]},
                        row_blocks=None, moved_blocks={"nested_instructions"})
    assert "set_moved" not in kept

    # end to end: a repository joins the group while svc1's scanner-produced signal moves
    later = [*LATER, _row("grpa/svc2", _scans(0))]
    comparison = compare(_run(EARLIER, toolkit_commit="aaa"), _run(later, toolkit_commit="bbb"),
                         _changed(NESTED), PRODUCERS)
    row = _row_move(comparison, "grpa", "teamdocs")
    assert row["cause"] == sd.CAUSE_SCANNER_CHANGED and row["set_moved"] is True
    (line,) = [line for line in summary_lines(comparison) if "teamdocs" in line]
    assert "the measured set of repositories moved too" in line


def test_both_null_commits_name_both_plugin_versions():
    diff = scanner_diff(_stamp([], toolkit_commit=None, plugin_version="1.0.0"),
                        _stamp([], toolkit_commit=None, plugin_version="1.1.0"), diff=_no_git)
    assert diff["reason"] is not None
    assert diff["reason"].startswith("neither run recorded a toolkit_commit")
    assert "'1.0.0'->'1.1.0'" in diff["reason"]
