"""Per-practice not-measured counts by reason in the fleet views (issue #1350, requirements 2 and 4).

A practice no measured repository holds is "not found in N repositories; M not measured" while
any repository reads it unknown, so a fleet report never says it is in place nowhere on data the
scan could not read. The not-measured count is split by reason code (an unread include against
any other cause).
"""
from __future__ import annotations

from fleet_data_fixture import STAMP
from lib.fleet.runner import FLEET_ROW_VERSION, FleetRow
from lib.fleet.fleet_data import FleetData, Privacy, build_fleet_data
from lib.fleet.fleet_not_measured import absence_line
from lib.fleet.fleet_opportunities import build_opportunities
from lib.fleet.fleet_opportunities_section import opportunities_section, whats_working
from lib.fleet.fleet_units import build_trees, empty_map
from lib.run_context_types import JsonDict
from test_fleet_opportunities import model


def _row(repo: str, ci: bool | None, basis: str = "no_check_command", *, available: bool = True) -> FleetRow:
    block: JsonDict = ({"available": True, "enforced_check_runs": ci, "enforced_basis": basis} if available
                       else {"available": False, "reason": "not_registered"})
    scans: JsonDict = {"layer_grades": {"available": True, "instruction_file_present": True}, "ci_checks": block}
    return {"fleet_row_version": FLEET_ROW_VERSION, "repo": repo, "group": "grp", "status": "ok",
            "reason": None, "signals_schema_version": 1, "scans": scans, "active": True}


def _fleet() -> list[FleetRow]:
    """One repository measured without CI checks; three not measured, two of them on an include."""
    return [_row("grp/a", False), _row("grp/b", None, "included_jobs"), _row("grp/c", None, "included_jobs"),
            _row("grp/d", None, available=False)]


def _data(rows: list[FleetRow]) -> FleetData:
    repos = [r["repo"] for r in rows]
    return build_fleet_data(STAMP, rows, model(), build_trees(repos, empty_map(), {}, {}), privacy=Privacy())


def test_in_place_counts_the_repositories_not_measured_by_reason() -> None:
    opps = build_opportunities(model(), _fleet(), Privacy().key)
    assert opps is not None
    ci = next(p for p in opps["in_place"] if p["row"] == "ci")
    assert (ci["at_top"], ci["measured"], ci["not_measured"]) == (0, 1, 3)
    assert ci["not_measured_reasons"] == [{"reason": "included_jobs", "count": 2},
                                          {"reason": "needs_full_run", "count": 1}]
    instr = next(p for p in opps["in_place"] if p["row"] == "instr")
    assert (instr["not_measured"], instr["not_measured_reasons"]) == (0, [])


def test_a_practice_with_an_unknown_repository_is_never_in_place_nowhere() -> None:
    opps = build_opportunities(model(), _fleet(), Privacy().key)
    assert opps is not None
    ci = next(p for p in opps["in_place"] if p["row"] == "ci")
    line = absence_line(ci, "CI")
    assert line == ("- CI: not found at its top level in 1 measured repository; 3 not measured "
                    "(`included_jobs` 2, `needs_full_run` 1).")
    assert "anywhere" not in line and "nowhere" not in line


def test_with_every_repository_measured_the_practice_is_not_in_place_in_any() -> None:
    opps = build_opportunities(model(), [_row("grp/a", False), _row("grp/b", False)], Privacy().key)
    assert opps is not None
    ci = next(p for p in opps["in_place"] if p["row"] == "ci")
    assert absence_line(ci, "CI") == "- CI: not in place at its top level in any of the 2 measured repositories."


def test_whats_working_lists_the_practices_no_measured_repository_holds() -> None:
    opps = build_opportunities(model(), _fleet(), Privacy().key)
    assert opps is not None
    lines = whats_working(opps, {"instr": "Instructions", "ci": "CI"})
    assert "Not held by any measured repository yet:" in lines
    assert "- CI: not found at its top level in 1 measured repository; 3 not measured (`included_jobs` 2, `needs_full_run` 1)." in lines


def test_the_fleet_data_carries_the_counts_and_counts_only_keeps_them() -> None:
    data = _data(_fleet())
    opps = data["opportunities"]
    assert opps is not None
    ci = next(p for p in opps["in_place"] if p["row"] == "ci")
    assert ci["not_measured"] == 3 and ci["not_measured_reasons"][0] == {"reason": "included_jobs", "count": 2}
    section = opportunities_section(data, None)
    assert section is not None and "3 not measured" in section
