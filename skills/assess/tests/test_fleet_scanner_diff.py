"""Tests for scanner-change attribution of fleet comparison moves, lib/fleet_scanner_diff.py (issue #774)."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from lib import fleet_compare as fc
from lib import fleet_scanner_diff as sd
from lib.fleet_compare import FleetRun, compare, summary_lines
from lib.fleet_groups import roll_up
from lib.fleet_scanner_diff import ScannerDiff, scanner_diff
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
    unrelated = _changed("skills/assess/scripts/lib/agent_ops.py")
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

    comparison = compare(earlier, later, diff, PRODUCERS)
    assert _row_move(comparison, "grpa", "teamdocs")["cause"] == sd.CAUSE_UNKNOWN_SCOPE
    (note,) = comparison["notes"]
    assert note.startswith("scanner diff unavailable") and "toolkit_commit" in note
    assert note in summary_lines(comparison)


# --- scanner_diff --------------------------------------------------------------


def _no_git(root: Path, a: str, b: str) -> list[str] | None:
    raise AssertionError("git must not run")


def test_same_commit_never_runs_git_and_a_null_commit_at_one_version_is_the_same_toolkit():
    assert scanner_diff(_stamp([], toolkit_commit="x"), _stamp([], toolkit_commit="x"), diff=_no_git)["status"] == "same"
    assert scanner_diff(_stamp([], toolkit_commit=None), _stamp([], toolkit_commit="x"), diff=_no_git)["status"] == "same"
    both_null = scanner_diff(_stamp([], toolkit_commit=None, plugin_version="1.0.0"),
                             _stamp([], toolkit_commit=None, plugin_version="1.1.0"), diff=_no_git)
    assert both_null["status"] == "unavailable"


def test_a_diff_git_cannot_answer_is_unavailable_and_an_empty_one_is_same():
    def fails(root: Path, a: str, b: str) -> list[str] | None:
        return None

    def empty(root: Path, a: str, b: str) -> list[str] | None:
        return []

    earlier, later = _stamp([], toolkit_commit="a" * 40), _stamp([], toolkit_commit="b" * 40)
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
    assert f"{sd.LIB_PATH}/import_graph.py" in producer_map["structure"]  # via the core's structure_scan
    assert all(f"{sd.LIB_PATH}/run_context_types.py" not in paths for paths in producer_map.values())
    assert "structure_scan" not in producer_map  # a private spec publishes no block
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
