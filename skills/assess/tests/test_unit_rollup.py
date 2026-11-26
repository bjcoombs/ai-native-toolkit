"""The repository roll-up over monorepo units and the unit-unlock ranking (lib/unit_rollup.py, #865).

The lowest unit's Step plus "N of M measured units" at the next Step (#800 decision 3,
#772 option A), and the changes ranked by the units one change lifts, repository-wide first.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from assess_report import render_report
from lib.run import core_scans as cs
from lib import unit_rollup as ur
from lib.counts_only.content_free import counts_only
from lib.scoring.level_tally import default_model
from lib.run.scan_registry import SCANS
from test_unit_readings import MONOREPO, _write

INSTR = "layer-0-instructions.1"
CI = "layer-5-ci.1"


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    from assess_core import build_run_context
    repo = _write(tmp_path_factory.mktemp("mono") / "repo", MONOREPO)
    ctx: dict[str, Any] = build_run_context(repo_root=repo, run_date="2026-11-23",
                                            non_interactive=True)
    return ctx


def _unit(path: str, step: int | None, blockers: list[dict[str, Any]] | None = None,
          conditions: dict[str, tuple[str, str]] | None = None) -> dict[str, Any]:
    """A hand-built ``unit_levels`` unit: ``conditions`` maps an id to (state, origin)."""
    conds = [{"id": cid, "state": st, "counted": True, "origin": origin}
             for cid, (st, origin) in (conditions or {}).items()]
    nxt = {"step": step + 1, "blockers": blockers} if blockers is not None and step else None
    return {"path": path, "step": step, "next_step": nxt,
            "tally": {"met": 0, "measured": 0, "not_measured": 0, "verified": 0},
            "conditions": conds, "inherited": []}


def _gate(gate: str, cid: str, state: str = "unmet", **extra: Any) -> dict[str, Any]:
    return {"kind": "gate", "gate": gate, "condition": cid, "state": state, **extra}




def _block(*units: dict[str, Any], count: int | None = None) -> dict[str, Any]:
    return {"available": True, "unit_count": len(units) if count is None else count,
            "unit_count_reason": None, "levels_version": "x", "units": list(units)}


def _no_instructions(path: str, origin: str = "inherited") -> dict[str, Any]:
    """A Step 1 unit held back from Step 2 by the instruction file alone."""
    return _unit(path, 1, [_gate("instructions", INSTR)], {INSTR: ("unmet", origin)})


# --- the #800 fixture -------------------------------------------------------------

def test_the_fixture_rolls_up_to_the_lowest_unit(run: dict[str, Any]) -> None:
    block = run["unit_rollup"]
    # Steps read gates only (#1083): every unit holds the instructions and CI gates, so all
    # three sit at Step 3 whatever their tallies, and none holds the coverage gate of Step 4
    steps = [u["step"] for u in run["unit_levels"]["units"]]
    assert block["available"] is True
    assert block["unit_count"] == 3 and block["units"] == 3 and block["measured"] == 3
    assert block["lowest_step"] == min(steps) == 3
    assert block["next_step"] == 4
    assert block["at_next_step"] == sum(s >= 4 for s in steps) == 0
    assert block["lines"][0] == "Units: Step 3 (lowest unit) · 0 of 3 measured units at Step 4"
    assert list(run).index("unit_rollup") > list(run).index("unit_levels")


def test_the_repository_step_and_headline_are_untouched(run: dict[str, Any]) -> None:
    from lib.exec_summary import summarised
    from lib.scoring.headline import run_context_headline
    from lib.scoring.level_tally import run_context_level
    expected = summarised(run_context_headline(run["level"]), run["level"], run.get("since_last_run"),
                          default_model(), None, run.get("unit_rollup"), run["measured_commit"].get("head_sha"))
    assert run["headline"] == expected
    assert run["level"]["step"] == run_context_level(run)["step"]
    assert "Units:" not in run["headline"]["markdown"]


def test_the_report_renders_the_roll_up_under_the_headline(run: dict[str, Any]) -> None:
    report = render_report(run, "mono")
    head = run["headline"]["markdown"]
    assert f"{head}\n\n{run['unit_rollup']['markdown']}" in report


def test_no_instruction_file_anywhere_ranks_the_root_file_first(tmp_path: Path) -> None:
    """The Helidon spike on #800: one root instruction file lifts every unit at once."""
    from assess_core import build_run_context
    bare = {k: v for k, v in MONOREPO.items() if not k.endswith("CLAUDE.md")}
    ctx = build_run_context(repo_root=_write(tmp_path / "repo", bare), run_date="2026-11-23",
                            non_interactive=True)
    block = ctx["unit_rollup"]
    assert block["unlocks"][0] == {"gate": "instructions", "scope": "repo_wide", "changes": 1,
                                   "held": 3, "lifted": 3, "lift_per_change": 3}
    assert block["lines"] == [
        "Units: Step 1 (lowest unit) · 0 of 3 measured units at Step 2",
        "Next unlock across units: an instruction file an agent reads is committed, once at the root, "
        "moves 3 units to their next Step"]


# --- the ranking ----------------------------------------------------------------------

def test_a_repo_wide_change_counts_once_and_lifts_every_unit() -> None:
    block = ur.unit_rollup(_block(*(_no_instructions(p) for p in ("a", "b", "c"))))
    assert block["unlocks"] == [{"gate": "instructions", "scope": "repo_wide", "changes": 1,
                                 "held": 3, "lifted": 3, "lift_per_change": 3}]
    assert block["lines"] == [
        "Units: Step 1 (lowest unit) · 0 of 3 measured units at Step 2",
        "Next unlock across units: an instruction file an agent reads is committed, once at the root, "
        "moves 3 units to their next Step"]


def test_a_repo_wide_change_ranks_above_unit_local_ones() -> None:
    ci_local = _unit("d", 2, [_gate("ci_runs_tests", CI)], {CI: ("unmet", "unit")})
    block = ur.unit_rollup(_block(ci_local, ci_local | {"path": "e"},
                                  _no_instructions("a"), _no_instructions("b")))
    assert [(e["gate"], e["scope"]) for e in block["unlocks"]] == [
        ("instructions", "repo_wide"), ("ci_runs_tests", "unit")]
    local = block["unlocks"][1]
    # each unit needs its own change: two changes, each lifting one unit
    assert (local["changes"], local["held"], local["lifted"], local["lift_per_change"]) == (2, 2, 2, 1)


def test_repo_wide_wins_a_tie_on_units_lifted() -> None:
    block = ur.unit_rollup(_block(_no_instructions("a", origin="unit"),
                                  _unit("b", 2, [_gate("ci_runs_tests", CI)], {CI: ("unmet", "inherited")})))
    assert [e["scope"] for e in block["unlocks"]] == ["repo_wide", "unit"]


def test_a_gate_is_not_the_last_blocker_beside_another_gate() -> None:
    two_gates = _unit("a", 2, [_gate("instructions", INSTR), _gate("ci_runs_tests", CI)],
                      {INSTR: ("unmet", "inherited"), CI: ("unmet", "inherited")})
    block = ur.unit_rollup(_block(two_gates, two_gates))
    assert all(e["lifted"] == 0 and e["lift_per_change"] == 0 for e in block["unlocks"])
    assert len(block["lines"]) == 1  # no change lifts a unit: no next-unlock line


def test_a_lone_gate_lifts_its_unit_whatever_the_tally() -> None:
    """#1083: a Step reads its gates only, so the last gate's change lifts the unit."""
    block = ur.unit_rollup(_block(_no_instructions("a"), _no_instructions("b")))
    assert block["unlocks"][0]["lifted"] == 2


@pytest.mark.parametrize("second", ["not_measured", "not_applicable"])
def test_a_gate_above_level_1_with_an_unmeasured_condition_lifts_no_unit(second: str) -> None:
    import copy
    model = copy.deepcopy(default_model())
    for step in model["steps"]:
        for gate in step["gates"]:
            if gate["id"] == "instructions":
                gate["level"] = 2
    unit = _unit("a", 1, [_gate("instructions", INSTR)],
                 {INSTR: ("unmet", "inherited"), "layer-0-instructions.2": (second, "inherited")})
    block = ur.unit_rollup(_block(unit, unit | {"path": "b"}), model)
    assert [(e["held"], e["lifted"]) for e in block["unlocks"]] == [(2, 0)]
    assert len(block["lines"]) == 1


@pytest.mark.parametrize("blocker", [
    _gate("instructions", INSTR, state="not_measured", reason="no_value"),
    _gate("instructions", INSTR, disposition="excluded_by_owner", exclusion_kind="other"),
])
def test_an_unmeasured_or_excluded_gate_is_no_change_to_rank(blocker: dict[str, Any]) -> None:
    unit = _unit("a", 1, [blocker], {INSTR: ("not_measured", "inherited")})
    assert ur.unit_rollup(_block(unit, unit | {"path": "b"}))["unlocks"] == []


def test_a_gate_with_no_condition_read_is_never_claimed_at_the_root() -> None:
    bare = _unit("a", 1, [_gate("instructions", INSTR)], {})
    block = ur.unit_rollup(_block(bare, bare | {"path": "b"}))
    assert [(e["scope"], e["lifted"]) for e in block["unlocks"]] == [("unit", 0)]
    assert len(block["lines"]) == 1  # no evidence the unit lifts: no next-unlock line


def test_a_unit_local_change_lifts_one_unit_to_its_next_step() -> None:
    block = ur.unit_rollup(_block(_no_instructions("a", origin="unit"), _unit("b", 2)))
    assert block["lines"][1].endswith(", in one unit, moves 1 unit to its next Step")


# --- unknown stays unknown, single units ----------------------------------------------

def test_an_unmeasured_unit_is_counted_but_never_read_as_step_1() -> None:
    block = ur.unit_rollup(_block(_unit("a", 2), _unit("b", None), _unit("c", 3)))
    assert (block["units"], block["measured"], block["lowest_step"]) == (3, 2, 2)
    assert block["lines"][0] == "Units: Step 2 (lowest unit) · 1 of 2 measured units at Step 3 · 1 not measured"


def test_no_measured_unit_leaves_the_step_null() -> None:
    block = ur.unit_rollup(_block(_unit("a", None), _unit("b", None)))
    assert block["lowest_step"] is None and block["next_step"] is None and block["at_next_step"] is None
    assert block["lines"] == ["Units: Step not measured for any of 2 units"]


def test_the_top_step_has_no_next_step() -> None:
    block = ur.unit_rollup(_block(_unit("a", 4), _unit("b", 4)))
    assert block["next_step"] is None and block["at_next_step"] is None
    assert block["lines"] == ["Units: Step 4 (lowest unit) · 2 of 2 measured units at the top Step"]


def test_a_null_unit_count_stays_null() -> None:
    block = ur.unit_rollup(_block(_unit("a", 1), _unit("b", 1), count=None) | {
        "unit_count": None, "unit_count_reason": "unreadable_manifest"})
    assert block["unit_count"] is None and block["unit_count_reason"] == "unreadable_manifest"
    assert block["units"] == 2


@pytest.mark.parametrize("levels", [None, {"available": False, "units": []}])
def test_unavailable_unit_levels_read_unavailable(levels: Any) -> None:
    block = ur.unit_rollup(levels)
    assert block["available"] is False and block["unit_count"] is None
    assert block["lines"] == [] and block["markdown"] == ""


def test_a_single_unit_renders_nothing_and_leaves_the_report_unchanged(run: dict[str, Any]) -> None:
    single = ur.unit_rollup(_block(_no_instructions(".")))
    assert single["lines"] == [] and single["markdown"] == ""
    assert single["unlocks"]  # the numbers are still there for readers
    bare = {k: v for k, v in run.items() if k != "unit_rollup"}
    assert render_report({**bare, "unit_rollup": single}, "r") == render_report(bare, "r")


# --- registry, counts-only ---------------------------------------------------------------

def test_the_registry_entry_reads_unit_levels() -> None:
    spec = next(s for s in SCANS if s.key == "unit_rollup")
    assert spec.fn is cs.unit_rollup
    # the core wrapper also reads the run's model choice, to label the line (#1150)
    assert tuple(spec.reads) == (*ur.READS, "maturity_model") and ur.READS == ("unit_levels", "units")
    keys = [s.key for s in SCANS]
    assert keys.index("unit_levels") < keys.index("unit_rollup")


def test_counts_only_keeps_every_number_and_names_no_unit(run: dict[str, Any]) -> None:
    block = ur.unit_rollup(_block(*(_no_instructions(p) for p in ("svc/a", "svc/b"))))
    out = counts_only({"unit_rollup": block})["unit_rollup"]
    assert "svc/" not in json.dumps(out)
    # the text is dropped; a dropped list keeps only its length, as for headline.lines
    assert "markdown" not in out and out["lines"] == len(block["lines"])
    for key in ("available", "unit_count", "units", "measured", "lowest_step", "next_step",
                "at_next_step", "unlocks"):
        assert out[key] == block[key], key
    assert "services/" not in json.dumps(counts_only({"unit_rollup": run["unit_rollup"]}))
