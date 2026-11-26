"""The verified tally and the autonomy Step (lib/scoring/level_tally.py, #744).

Unit cases run the engine over hand-built signals and small models; the
end-to-end cases build the #744 contract fixtures (FL5 plus CI and an
instruction file) and check that /fleet and /assess give the same result and
that a rerun writes identical bytes.
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import jsonschema
import pytest

import lib.scoring.level_tally as lt
from lib.scoring import level_signals
from lib.counts_only.content_free import counts_only
from lib.fleet.runner import STATUS_FAILED, STATUS_OK
from lib.hosting.merged_sample import UNSUPPORTED_HOST_REASON
from lib.fleet.fleet_level import LEVEL_FILE, fleet_levels, write_fleet_levels
from lib.fleet.fleet_level_schema import SCHEMA_PATH, build_schema, render
from lib.scoring.level_signals import DEFAULT_LEVELS, load_default_levels
from lib.scoring.levels import LevelsError, MaturityModel, Row, evaluate_row, parse_levels
from test_fleet_cli import fleet_cli

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _row(row_id: str, *levels: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {"id": row_id, "title": row_id, "scope": "team", "aggregation": "min",
            "levels": [{"level": n, "criteria": c} | extra.get(f"l{n}", {}) for n, c in enumerate(levels, 1)]}


def _eq(signal: str, value: Any = True) -> dict[str, Any]:
    return {"signal": signal, "op": "eq", "value": value}


def _model(*rows: dict[str, Any], steps: list[dict[str, Any]] | None = None) -> MaturityModel:
    data: dict[str, Any] = {"version": "fixture-t1", "rows": list(rows)}
    if steps is not None:
        data["steps"] = steps
    return parse_levels(data)


DOC = "doc_staleness.summary.stale_pct"  # git_history: counted
INSTR = "layer_grades.instruction_file_present"  # head_text: listed
CI = "ci_checks.test_or_lint_configured"  # head_text: listed until #775
MUT = "test_pressure.mutation_run"  # execution_artifact: counted


def _fresh(pct: int = 0) -> dict[str, Any]:
    return {"doc_staleness": {"available": True, "summary": {"stale_pct": pct}}}


def _state(result: lt.LevelResult, cid: str) -> tuple[str, str | None]:
    cond = next(c for c in result["conditions"] if c["id"] == cid)
    return cond["state"], cond.get("reason")


# --- loading: requires and Steps ---------------------------------------------

STEPS = [
    {"step": 1, "name": "Assisted"},
    {"step": 2, "name": "Supervised agent PRs",
     "gates": [{"id": "instructions", "row": "instr", "level": 1}]},
]


def test_a_model_without_steps_or_requires_loads_as_before():
    model = _model(_row("fresh", [{"signal": DOC, "op": "lte", "value": 50}]))
    assert "steps" not in model and "requires" not in model["rows"][0]["levels"][0]


@pytest.mark.parametrize("steps, message", [
    ([{"step": 2, "name": "x"}], "numbered 1..K"),
    ([{"step": 1, "name": "x", "gates": [{"id": "g", "row": "instr", "level": 1}]}], "always reached"),
    ([STEPS[0], {**STEPS[1], "gates": [{"id": "g", "row": "nope", "level": 1}]}], "unknown row"),
    ([STEPS[0], {**STEPS[1], "gates": [{"id": "g", "row": "instr", "level": 2}]}], "must be a level"),
    ([*STEPS, {"step": 3, "name": "x", "gates": []}], "gates accumulate"),
    ([STEPS[0], {**STEPS[1], "extra": 1}], "unknown key"),
    # #1083: a count threshold is refused by name, never silently ignored
    ([{**STEPS[0], "threshold": 0}, STEPS[1]], "'threshold' was removed .*gates only"),
    ([STEPS[0], {**STEPS[1], "threshold": 3}], "'threshold' was removed"),
    # gates are all that tell two Steps apart, so a Step must add one
    ([STEPS[0], {**STEPS[1], "gates": []}], "adds no gate over step 1"),
    ([*STEPS, {**STEPS[1], "step": 3}], "adds no gate over step 2"),
])
def test_a_malformed_step_fails_at_load(steps, message):
    with pytest.raises(LevelsError, match=message):
        _model(_row("instr", [_eq(INSTR)]), steps=steps)


@pytest.mark.parametrize("requires, message", [
    (["nope.1"], "unknown condition"),
    (["mut.1"], "cycle"),
    ([], "non-empty array"),
])
def test_a_bad_requires_fails_at_load(requires, message):
    with pytest.raises(LevelsError, match=message):
        _model(_row("mut", [_eq(MUT)], l1={"requires": requires}))


# --- condition states, grades and the tally ----------------------------------

def test_counted_and_listed_conditions_are_split_by_grade():
    model = _model(_row("instr", [_eq(INSTR)]), _row("fresh", [{"signal": DOC, "op": "lte", "value": 50}]))
    result = lt.level_result(model, {**_fresh(), "layer_grades": {"instruction_file_present": True}})
    assert result["counted"] == ["fresh.1"] and result["listed"] == ["instr.1"]
    assert result["tally"] == {"met": 1, "measured": 1, "not_measured": 0}
    assert [(c["id"], c["grade"], c["counted"]) for c in result["conditions"]] == [
        ("instr.1", "declared", False), ("fresh.1", "measured", True)]


def test_a_counted_ci_condition_raises_the_tally(monkeypatch):
    """I744-1's "the CI condition counts": once the CI signal grades verified (#775)."""
    model = _model(_row("ci", [_eq(CI)]))
    signals = {"ci_checks": {"available": True, "test_or_lint_configured": True}}
    assert lt.level_result(model, signals)["tally"]["met"] == 0
    monkeypatch.setitem(level_signals.SIGNAL_INPUTS, CI, "ci_executed")
    monkeypatch.setitem(lt.SIGNAL_INPUTS, CI, "ci_executed")
    assert lt.level_result(model, signals)["tally"]["met"] == 1


def test_a_condition_takes_its_weakest_criterions_grade():
    model = _model(_row("mix", [_eq(INSTR), {"signal": DOC, "op": "lte", "value": 50}]))
    result = lt.level_result(model, {**_fresh(), "layer_grades": {"instruction_file_present": True}})
    assert result["conditions"][0]["grade"] == "declared" and result["listed"] == ["mix.1"]


def test_an_unclassified_signal_is_listed_never_raised():
    """A caller's own model (the contract's LVX) reads signals the class map does not know."""
    model = _model(_row("ckc", [{"signal": "ci_checks.check_commands", "op": "gt", "value": 0}]))
    result = lt.level_result(model, {"ci_checks": {"check_commands": 2}})
    assert result["conditions"][0]["grade"] is None and result["listed"] == ["ckc.1"]
    assert result["step"] is None and result["next_step"] is None


def test_an_unknown_never_counts_and_is_listed_with_its_reason():
    model = _model(_row("fresh", [{"signal": DOC, "op": "lte", "value": 50}]))
    blind = lt.level_result(model, {"doc_staleness": {"available": False, "reason": "shallow: truncated history"}})
    assert blind["tally"] == {"met": 0, "measured": 0, "not_measured": 1}
    assert blind["not_measured"] == [{"id": "fresh.1", "reason": "shallow"}]
    assert lt.level_result(model, {})["not_measured"] == [{"id": "fresh.1", "reason": "block_absent"}]
    # a null block (an omitted spread key in the run context's entry) reads the same
    assert lt.level_result(model, {"doc_staleness": None})["not_measured"] == [
        {"id": "fresh.1", "reason": "block_absent"}]
    nulled = lt.level_result(model, {"doc_staleness": {"available": True, "summary": {"stale_pct": None}}})
    assert nulled["not_measured"] == [{"id": "fresh.1", "reason": "no_value"}]
    wrong = lt.level_result(model, {"doc_staleness": {"summary": {"stale_pct": "x"}}})
    assert wrong["not_measured"] == [{"id": "fresh.1", "reason": "type_mismatch"}]


def test_a_known_reason_sentence_maps_to_its_code():
    model = _model(_row("design", [{"signal": "structure.modularity_q", "op": "gte", "value": 0.3}]))
    result = lt.level_result(model, {"structure": {"available": False,
                                                   "reason": "no importable Python packages found"}})
    assert result["not_measured"] == [{"id": "design.1", "reason": "no_python_packages"}]


def test_mutation_not_run_is_not_measured_in_both_run_kinds():
    """#740 decision: headless (not_registered) and a full run without the opt-in pass
    (mutation_run false) both read needs_full_run, never unmet."""
    model = _model(_row("mut", [_eq(MUT)]))
    headless = {"test_pressure": {"available": False, "reason": "not_registered: assembled by assess_core"}}
    full = {"test_pressure": {"mutation_run": False}}
    for signals in (headless, full):
        assert _state(lt.level_result(model, signals), "mut.1") == ("not_measured", "needs_full_run")
    assert _state(lt.level_result(model, {"test_pressure": {"mutation_run": True}}), "mut.1") == ("met", None)


def test_thin_history_is_not_measured_not_unmet():
    model = _model(_row("lint", [_eq("promissory_markers.aging_reliable")]))
    result = lt.level_result(model, {"promissory_markers": {"aging_reliable": False}})
    assert _state(result, "lint.1") == ("not_measured", "degenerate_history")


def test_unreadable_managed_settings_are_not_measured_not_unmet():
    """#1153: a null signal names its sibling ``<leaf>_reason`` code."""
    sig = "agent_ops.summary.permissions_encoded"
    model = _model(_row("ops", [_eq(sig)]))
    summary = {"permissions_encoded": None, "permissions_encoded_reason": "managed_settings_unreadable"}
    result = lt.level_result(model, {"agent_ops": {"available": True, "summary": summary}})
    assert _state(result, "ops.1") == ("not_measured", "managed_settings_unreadable")
    bare = {"agent_ops": {"available": True, "summary": {"permissions_encoded": None}}}
    assert _state(lt.level_result(model, bare), "ops.1") == ("not_measured", "no_value")


P95 = "stats_summary.loc_by_class.source.p95"


def _sized(files: int, p95: float | None) -> dict[str, Any]:
    """A ``stats_summary`` whose production source has ``files`` files at ``p95`` lines (#820)."""
    return {"files_scored": files, "loc": {"p95": p95 or 0.0},
            "loc_by_class": {"source": {"files_scored": files, "p95": p95}}}


@pytest.mark.parametrize("stats", [
    _sized(0, None),  # no source file scored: null percentiles
    {"files_scored": 0, "loc": {}, "loc_by_class": {"source": {"files_scored": 0, "p95": 0.0}}},
])
def test_a_statistic_over_no_files_is_not_measured_in_both_run_kinds(stats):
    """#906 A, #820: a p95 of no source files is not a measurement; it reads no_source_files."""
    model = _model(_row("size", [{"signal": P95, "op": "lte", "value": 500}]))
    assert _state(lt.level_result(model, {"stats_summary": stats}), "size.1") == (
        "not_measured", "no_source_files")


def test_a_scored_set_or_an_unavailable_block_keeps_the_usual_rules():
    model = _model(_row("size", [{"signal": P95, "op": "lte", "value": 500}]))
    scored = {"stats_summary": _sized(3, 3.0)}
    big = {"stats_summary": _sized(3, 900.0)}
    no_lizard = {"stats_summary": {"available": False, "reason": "no_lizard: lizard not installed"}}
    assert _state(lt.level_result(model, scored), "size.1") == ("met", None)
    assert _state(lt.level_result(model, big), "size.1") == ("unmet", None)
    assert _state(lt.level_result(model, no_lizard), "size.1") == ("not_measured", "no_lizard")


def _default_state(signals: dict[str, Any], cid: str) -> tuple[str, str | None]:
    return _state(lt.level_result(lt.default_model(), signals), cid)


def test_a_tangled_graph_does_not_count_front_door_imports():
    """#906 C: three Go packages a->b->c and a->c have modularity 0, so level 2 waits on level 1."""
    tangled = {"structure": {"modularity_q": 0.0, "cross_package_edges": 3, "front_door_ratio": 1.0}}
    assert _default_state(tangled, "layer-2-design.1") == ("unmet", None)
    assert _default_state(tangled, "layer-2-design.2") == ("unmet", "requires:layer-2-design.1")
    modular = {"structure": {"modularity_q": 0.5, "cross_package_edges": 3, "front_door_ratio": 1.0}}
    assert _default_state(modular, "layer-2-design.2") == ("met", None)


def test_wide_review_waits_on_review_evidence():
    """#934: layer-7-review.2 cannot count while layer-7-review.1 is unmet."""
    wide = {"review_reality": {"reviewed_share": 0.9, "approved_share": 0.6}}
    assert _default_state({**wide, "review_evidence": {"present": False}}, "layer-7-review.2") == (
        "unmet", "requires:layer-7-review.1")
    assert _default_state({**wide, "review_evidence": {"present": True}}, "layer-7-review.2") == ("met", None)


def test_file_size_counts_only_beside_a_boundary_map():
    small = {"stats_summary": _sized(4, 40.0)}
    no_map = {**small, "structure_drift": {"available": False, "reason": "no_ownership_map: none"}}
    assert _default_state(no_map, "layer-4-architecture.2") == (
        "not_measured", "requires:layer-4-architecture.1")
    mapped = {**small, "structure_drift": {"tier_0": {"boundary_patterns": 2}}}
    assert _default_state(mapped, "layer-4-architecture.2") == ("met", None)
    empty = {"stats_summary": _sized(0, None),
             "structure_drift": {"tier_0": {"boundary_patterns": 2}}}
    assert _default_state(empty, "layer-4-architecture.2") == ("not_measured", "no_source_files")


@pytest.mark.parametrize("pinning, state", [
    pytest.param({"manifests": [], "manifests_total": 0, "unlocked_total": 0},
                 ("not_measured", "no_manifests"), id="CMAKE"),
    pytest.param({"manifests_total": 1, "unlocked_total": 0}, ("met", None), id="NODE-LOCKED"),
    pytest.param({"manifests_total": 1, "unlocked_total": 1}, ("unmet", None), id="NODE-UNLOCKED"),
    pytest.param({"unlocked_total": 0}, ("met", None), id="pre-927-block"),
])
def test_no_counted_manifest_reads_not_measured(pinning, state):
    """#927 A: nothing to lock is not a lockfile check passed."""
    signals = {"agent_environment": {"available": True, "pinning": pinning}}
    assert _default_state(signals, "layer-5-ci.2") == state


def test_a_real_unmet_sibling_outweighs_an_empty_set():
    model = _model(_row("size", [_eq(INSTR), {"signal": P95, "op": "lte", "value": 500}]))
    signals = {"layer_grades": {"instruction_file_present": False},
               "stats_summary": _sized(0, None)}
    assert _state(lt.level_result(model, signals), "size.1") == ("unmet", None)


def test_the_row_level_agrees_with_the_tally_on_an_empty_set():
    """A docs-only repository with a boundary map: the row is unknown above level 1, not 2."""
    docs_only = {"stats_summary": _sized(0, None),
                 "structure_drift": {"tier_0": {"boundary_patterns": 2}}}
    row = next(r for r in lt.default_model()["rows"] if r["id"] == "layer-4-architecture")
    evaluation = evaluate_row(row, docs_only)
    assert evaluation["status"] == "unknown" and evaluation["floor"] == 1
    assert evaluation["levels"][1]["unmet"][0]["reason"] == "no_source_files"


def test_a_real_unmet_criterion_outweighs_an_unknown_one():
    model = _model(_row("x", [_eq(INSTR), {"signal": DOC, "op": "lte", "value": 50}]))
    result = lt.level_result(model, {"layer_grades": {"instruction_file_present": False}})
    assert _state(result, "x.1") == ("unmet", None)


def test_a_network_block_needs_the_platform():
    model = _model(_row("rev", [{"signal": "review_reality.reviewed_share", "op": "gte", "value": 0.8}]))
    result = lt.level_result(model, {"review_reality": {"available": False, "reason": "network: reads GitHub"}})
    assert result["not_measured"] == [{"id": "rev.1", "reason": "needs_platform"}]


def test_an_unsupported_host_names_the_limit_not_the_platform():
    model = _model(_row("rev", [{"signal": "review_reality.reviewed_share", "op": "gte", "value": 0.8}]))
    block = {"available": False, "reason": UNSUPPORTED_HOST_REASON}
    result = lt.level_result(model, {"review_reality": block})
    assert result["not_measured"] == [{"id": "rev.1", "reason": "unsupported_host"}]


def test_a_condition_reads_its_own_criteria_not_the_level_below():
    model = _model(_row("fresh", [{"signal": DOC, "op": "lte", "value": 10}],
                        [{"signal": "stats_summary.loc.p95", "op": "lte", "value": 500}]))
    result = lt.level_result(model, {**_fresh(40), "stats_summary": {"loc": {"p95": 20}}})
    assert [c["state"] for c in result["conditions"]] == ["unmet", "met"]


def test_requires_carries_the_required_conditions_state():
    model = _model(_row("ci", [_eq(MUT)]), _row("mut", [_eq(DOC.replace("stale_pct", "ok"))], l1={"requires": ["ci.1"]}))
    signals = {"doc_staleness": {"summary": {"ok": True}}}
    assert _state(lt.level_result(model, {**signals, "test_pressure": {"mutation_run": True}}), "mut.1") == ("met", None)
    assert _state(lt.level_result(model, signals), "mut.1") == ("not_measured", "requires:ci.1")


# --- Steps -------------------------------------------------------------------

def _stepped() -> MaturityModel:
    return _model(
        _row("instr", [_eq(INSTR)]), _row("ci", [_eq(CI)]),
        _row("fresh", [{"signal": DOC, "op": "lte", "value": 50}], [{"signal": DOC, "op": "lte", "value": 20}]),
        steps=[*STEPS, {"step": 3, "name": "Unattended agent PRs",
                        "gates": [*STEPS[1]["gates"], {"id": "ci_runs_tests", "row": "ci", "level": 1}]}])


def test_a_missing_gate_blocks_the_next_step_and_names_it():
    result = lt.level_result(_stepped(), {**_fresh(), "layer_grades": {"instruction_file_present": False}})
    assert result["step"] == 1
    assert result["next_step"] == {"step": 2, "blockers": [
        {"kind": "gate", "gate": "instructions", "condition": "instr.1", "state": "unmet"}]}


def test_a_declared_gate_holds_while_staying_out_of_the_tally():
    signals = {**_fresh(), "layer_grades": {"instruction_file_present": True},
               "ci_checks": {"test_or_lint_configured": True}}
    result = lt.level_result(_stepped(), signals)
    assert result["step"] == 3 and result["next_step"] is None
    assert result["tally"]["met"] == 2 and "instr.1" in result["listed"]


def test_an_unmeasured_gate_blocks_and_the_tally_never_does():
    signals = {**_fresh(40), "layer_grades": {"instruction_file_present": True}}
    result = lt.level_result(_stepped(), signals)
    assert result["step"] == 2 and result["tally"]["met"] == 1
    assert result["next_step"] == {"step": 3, "blockers": [
        {"kind": "gate", "gate": "ci_runs_tests", "condition": "ci.1", "state": "not_measured",
         "reason": "block_absent"}]}


def test_gates_with_no_counted_condition_met_reach_the_top_step():
    """#1083: a tally of 0 holds no Step back once the gates hold."""
    signals = {**_fresh(90), "layer_grades": {"instruction_file_present": True},
               "ci_checks": {"test_or_lint_configured": True}}
    result = lt.level_result(_stepped(), signals)
    assert result["tally"]["met"] == 0 and (result["step"], result["next_step"]) == (3, None)


def _laddered() -> MaturityModel:
    """``_stepped`` with a Step 4 that adds a ``fresh`` gate at level 2."""
    model = _stepped()
    steps = model["steps"]
    top = {"step": 4, "name": "Autonomous loops",
           "gates": [*steps[2]["gates"], {"id": "fresh_docs", "row": "fresh", "level": 2}]}
    return {**model, "steps": [*steps, top]}


def test_ceiling_step_skips_held_gates():
    """#1374: Step 1, blocked on the instruction file, already holds the Step 3 gate."""
    signals = {**_fresh(40), "layer_grades": {"instruction_file_present": False},
               "ci_checks": {"test_or_lint_configured": True}}
    result = lt.level_result(_laddered(), signals)
    assert result["step"] == 1 and result["next_step"] is not None and result["next_step"]["step"] == 2
    assert result["ceiling_step"] == 3
    assert result["held_gates"] == ["ci_runs_tests"]
    kept = counts_only({"level": result})["level"]  # gate ids are model tokens: counts-only keeps both
    assert (kept["ceiling_step"], kept["held_gates"]) == (3, ["ci_runs_tests"])


def test_ceiling_step_is_the_next_step_when_nothing_above_is_held():
    signals = {**_fresh(90), "layer_grades": {"instruction_file_present": False}}
    result = lt.level_result(_laddered(), signals)
    assert (result["step"], result["ceiling_step"], result["held_gates"]) == (1, 2, [])


def test_ceiling_step_reaches_the_top_when_every_upper_gate_holds():
    signals = {**_fresh(10), "layer_grades": {"instruction_file_present": False},
               "ci_checks": {"test_or_lint_configured": True}}
    result = lt.level_result(_laddered(), signals)
    assert (result["step"], result["ceiling_step"]) == (1, 4)
    assert result["held_gates"] == ["ci_runs_tests", "fresh_docs"]


def test_a_held_gate_above_an_unheld_one_does_not_raise_the_ceiling():
    """The ceiling climbs Step by Step: a gate held at Step 4 is listed, but Step 3 still waits."""
    signals = {**_fresh(10), "layer_grades": {"instruction_file_present": False}}
    result = lt.level_result(_laddered(), signals)
    assert (result["step"], result["ceiling_step"], result["held_gates"]) == (1, 2, ["fresh_docs"])


def test_a_held_gate_of_the_current_step_is_not_listed():
    signals = {**_fresh(90), "layer_grades": {"instruction_file_present": True}}
    result = lt.level_result(_laddered(), signals)
    assert (result["step"], result["ceiling_step"], result["held_gates"]) == (2, 3, [])


def test_ceiling_step_is_null_at_the_top_step_and_without_steps():
    signals = {**_fresh(10), "layer_grades": {"instruction_file_present": True},
               "ci_checks": {"test_or_lint_configured": True}}
    top = lt.level_result(_laddered(), signals)
    assert (top["step"], top["next_step"], top["ceiling_step"], top["held_gates"]) == (4, None, None, [])
    flat = lt.level_result(_model(_row("instr", [_eq(INSTR)])), signals)
    assert (flat["step"], flat["ceiling_step"], flat["held_gates"]) == (None, None, [])


def _plus_met_row(model: MaturityModel, n: int) -> MaturityModel:
    """``model`` with ``n`` new rows of one counted condition each that ``_fresh()`` meets."""
    rows: list[Row] = [{"id": f"new-{i}", "title": "new", "scope": "team", "aggregation": {"rule": "min", "share": 1},
                        "levels": [{"level": 1, "criteria": [{"signal": DOC, "op": "lte", "value": 100}],
                                    "truth": ["docs"]}]} for i in range(n)]
    return {**model, "rows": [*model["rows"], *rows]}


@pytest.mark.parametrize("signals", [
    {},  # nothing measured: Step 1
    {"layer_grades": {"instruction_file_present": True}},  # the instructions gate: Step 2
    {"layer_grades": {"instruction_file_present": True}, "ci_checks": {"test_or_lint_configured": True}},
])
@pytest.mark.parametrize("added", [1, 7])
def test_a_new_counted_condition_the_repository_meets_never_moves_its_step(signals, added):
    """#1083 success criterion: the catalogue grows by met, counted conditions; the Step stays."""
    for model in (_stepped(), load_default_levels()):
        data = {**_fresh(), **signals}
        before = lt.level_result(model, data)
        after = lt.level_result(_plus_met_row(model, added), data)
        assert after["tally"]["met"] == before["tally"]["met"] + added
        assert (after["step"], after["next_step"]) == (before["step"], before["next_step"])


def test_the_default_steps_stage_their_gates():
    """I744-2: Step 2 gates on layer 0, Step 3 adds layer 5, Step 4 adds layer 6."""
    steps = load_default_levels()["steps"]
    assert [s["step"] for s in steps] == [1, 2, 3, 4]
    assert [s["name"] for s in steps] == ["Assisted", "Supervised agent PRs", "Unattended agent PRs",
                                           "Autonomous loops"]
    prefixes = [[g["row"].split("-")[1] for g in s["gates"]] for s in steps]
    assert prefixes == [[], ["0"], ["0", "5"], ["0", "5", "6"]]
    assert [g["id"] for g in steps[3]["gates"]] == ["instructions", "ci_runs_tests", "coverage_gate"]
    assert all(set(s) == {"step", "name", "gates"} for s in steps)  # gates only, no count (#1083)


# --- output: determinism, schema, counts-only --------------------------------

def test_the_result_is_identical_bytes_and_matches_the_schema():
    model = load_default_levels()
    signals = {**_fresh(), "layer_grades": {"instruction_file_present": True}}
    first, second = lt.level_result(model, signals), lt.level_result(model, copy.deepcopy(signals))
    assert lt.dumps_level(first) == lt.dumps_level(second)
    schema = build_schema()
    jsonschema.validate(first, {**schema["$defs"]["level_result"], "$defs": schema["$defs"]})


def test_the_checked_in_schema_is_generated():
    assert SCHEMA_PATH.read_text(encoding="utf-8") == render()


def test_counts_only_keeps_every_field_of_the_level_block():
    """#770's lesson: a counts-only test that fails when a level field is dropped."""
    model = load_default_levels()
    level = lt.level_result(model, {"doc_staleness": {"available": False, "reason": "shallow: x"},
                                    "test_pressure": {"mutation_run": False}})
    assert level["not_measured"] and level["next_step"]
    # A condition's why (#939) is the toolkit's own text, rejoined from measures.json by id,
    # so counts-only output carries the ids and drops it; every other field survives.
    assert all(c.get("why") for c in level["conditions"])
    bare = {**level, "conditions": [{k: v for k, v in c.items() if k != "why"} for c in level["conditions"]]}
    assert counts_only({"level": level})["level"] == bare


def test_fleet_levels_skip_a_failed_row_and_a_run_without_a_model_removes_the_file(tmp_path):
    model = _stepped()
    rows: list[Any] = [
        {"repo": "b", "status": STATUS_OK, "scans": _fresh(), "head_sha": "f" * 40, "dirty": True},
        {"repo": "a", "status": STATUS_FAILED, "scans": None, "head_sha": None, "dirty": None},
        {"repo": "c", "status": STATUS_OK, "scans": _fresh()},  # a row written before #907
    ]
    doc = fleet_levels(rows, model, toolkit_commit="abc", plugin_version="9.9.9")
    assert list(doc["repos"]) == ["b", "c"] and doc["levels_version"] == "fixture-t1"
    assert doc["commits"] == {"b": {"head_sha": "f" * 40, "dirty": True}, "c": {"head_sha": None, "dirty": None}}
    jsonschema.validate(doc, build_schema())
    path = write_fleet_levels(tmp_path, rows, model, toolkit_commit="abc", plugin_version="9.9.9")
    assert path is not None and json.loads(path.read_text(encoding="utf-8")) == doc
    assert write_fleet_levels(tmp_path, rows, None, toolkit_commit="abc", plugin_version="9.9.9") is None
    assert not (tmp_path / LEVEL_FILE).exists()


# --- end to end: the contract fixtures through /fleet and /assess -----------

FL5 = {
    "src/a.py": "def f():\n    return 1\n",
    "Makefile": "test:\n\tpython3 -m unittest discover -s tests\n",
    "tests/test_a.py": "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_a(self):\n"
                       "        self.assertTrue(True)\n",
}
CIWF = {".github/workflows/ci.yml": "name: ci\non: [pull_request]\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
                                    "    steps:\n      - uses: actions/checkout@v4\n      - run: make test\n"}
CLMD = {"CLAUDE.md": "# Project\n\nBefore every commit run `make test` and fix any failure.\n"}
FIXTURES = {"ci0": {**FL5, **CLMD}, "ci1": {**FL5, **CIWF}, "ci2": {**FL5, **CIWF, **CLMD}, "nci": FL5,
            "inc": {**FL5, ".gitlab-ci.yml": "include:\n  - component: gl.test/grp/comp/test@1.0\n"}}


def _commit(repo: Path, date: str, message: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    for args in (("add", "-A"), ("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", message)):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


@pytest.fixture(scope="module")
def estate(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("tally")
    for name, files in FIXTURES.items():
        repo = root / name / "r"
        repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True, capture_output=True)
        for rel, text in files.items():
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text(text, encoding="utf-8", newline="\n")
        _commit(repo, "2026-11-01T10:00:00Z", "one")
        (repo / ".keep").write_text("2\n", encoding="utf-8")
        _commit(repo, "2026-11-02T10:00:00Z", "two")
    (root / "list.txt").write_text("".join(f"{name}/r\n" for name in FIXTURES), encoding="utf-8")
    return root


def _fleet(estate: Path, out: str) -> dict[str, Any]:
    cwd = Path.cwd()
    os.chdir(estate)
    try:
        assert fleet_cli.main(["--from-list", "list.txt", "--levels", str(DEFAULT_LEVELS), "--out", out,
                               "--no-diagrams", "--no-history"]) == 0
    finally:
        os.chdir(cwd)
    return json.loads((estate / out / LEVEL_FILE).read_text(encoding="utf-8"))


def test_fleet_reruns_are_byte_identical_and_steps_follow_the_gates(estate):
    first = _fleet(estate, "e1")
    _fleet(estate, "e2")
    assert (estate / "e1" / LEVEL_FILE).read_bytes() == (estate / "e2" / LEVEL_FILE).read_bytes()
    repos = first["repos"]
    for name in ("ci1", "ci2"):
        assert _state(repos[f"{name}/r"], "layer-6-coverage.2") == ("not_measured", "needs_full_run")
    assert repos["ci1/r"]["next_step"]["blockers"][0]["gate"] == "instructions"
    for result in repos.values():
        nxt = result["next_step"]
        gates = {b.get("gate") for b in nxt["blockers"]} if nxt else set()
        assert "ci_runs_tests" not in gates or nxt["step"] >= 3
        assert "coverage_gate" not in gates or nxt["step"] >= 4
    # the CI condition counts (layer 5 level 1 reads a ci_executed signal, #775)
    assert repos["ci2/r"]["tally"]["met"] >= repos["ci0/r"]["tally"]["met"] + 1
    assert repos["inc/r"]["tally"]["met"] == repos["nci/r"]["tally"]["met"]
    assert repos["inc/r"]["tally"]["not_measured"] > repos["nci/r"]["tally"]["not_measured"]


def test_an_uncommitted_change_keeps_the_step_of_the_commit(estate, tmp_path):
    """#907: fleet scores HEAD's committed tree, and level.json names the commit and the dirty flag."""
    repo = tmp_path / "ci2" / "r"
    subprocess.run(["git", "clone", "-q", str(estate / "ci2" / "r"), str(repo)], check=True, capture_output=True)
    (tmp_path / "list.txt").write_text("ci2/r\n", encoding="utf-8")
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True,
                          encoding="utf-8").stdout.strip()
    clean = _fleet(tmp_path, "clean")
    (repo / "CLAUDE.md").unlink()
    (repo / "AGENTS.md").write_text("# untracked\n", encoding="utf-8")  # untracked: still ignored
    dirty = _fleet(tmp_path, "dirty")
    assert clean["repos"]["ci2/r"]["step"] > 1  # the instruction gate holds at HEAD
    assert dirty["repos"] == clean["repos"]
    assert clean["commits"] == {"ci2/r": {"head_sha": head, "dirty": False}}
    assert dirty["commits"] == {"ci2/r": {"head_sha": head, "dirty": True}}
    row = json.loads((tmp_path / "dirty" / "repos.jsonl").read_text(encoding="utf-8"))
    assert (row["head_sha"], row["dirty"]) == (head, True)
    clean_row = json.loads((tmp_path / "clean" / "repos.jsonl").read_text(encoding="utf-8"))
    assert {**row, "dirty": False} == clean_row  # every scan block reads the commit, not the copy
    assert not (repo / "CLAUDE.md").exists()


def test_assess_gives_the_fleet_level_and_step_on_the_same_commit(estate, tmp_path):
    fleet = _fleet(estate, "e3")["repos"]["ci2/r"]
    repo = tmp_path / "ci2"
    subprocess.run(["git", "clone", "-q", str(estate / "ci2" / "r"), str(repo)], check=True, capture_output=True)
    assess = repo / ".assess"
    assess.mkdir()
    treemap = subprocess.run(["uv", "run", "--script", str(SCRIPTS / "complexity-treemap.py"), str(repo), "-o",
                              str(assess / "complexity-heatmap.svg"), "--stats", str(assess / "complexity-stats.json")],
                             capture_output=True, encoding="utf-8", errors="replace")
    assert treemap.returncode == 0, treemap.stderr
    from assess_core import build_and_publish_run

    level = build_and_publish_run(repo_root=repo, run_date="2026-11-21", non_interactive=True)["level"]
    assert (level["tally"]["met"], level["step"], level["counted"], level["listed"]) == (
        fleet["tally"]["met"], fleet["step"], fleet["counted"], fleet["listed"])
    assert [n["id"] for n in level["not_measured"]] == [n["id"] for n in fleet["not_measured"]]
    on_disk = json.loads((assess / "run-context.json").read_text(encoding="utf-8"))
    # the --opt-in-mutation recount reads the stored run context: it agrees with the entry
    assert lt.run_context_level(on_disk) == on_disk["level"]
    on_disk = on_disk["level"]
    assert on_disk == level


@pytest.mark.parametrize("lint_runs", [False, None])
def test_layer_three_suppressions_earn_no_tally_credit_without_a_linter(lint_runs: bool | None) -> None:
    """Issue #782: layer-3-linters.2 requires a linter run in CI (layer-3-linters.1)."""
    signals = {"ci_checks": {"lint_runs": lint_runs},
               "promissory_markers": {"aging_reliable": True, "families": {"suppression": {"stale": 0}}}}
    state, reason = _state(lt.level_result(load_default_levels(), signals), "layer-3-linters.2")
    assert state != "met" and reason == "requires:layer-3-linters.1"
    linted = {**signals, "ci_checks": {"lint_runs": True}}
    assert _state(lt.level_result(load_default_levels(), linted), "layer-3-linters.2") == ("met", None)


def test_each_condition_carries_the_whys_of_its_criteria():
    """#939: a condition's why joins its criteria's manifest whys, in order, each once."""
    from lib.manifest.check_manifest import condition_key, core_manifest
    from lib.manifest.manifest_whys import condition_whys

    model = load_default_levels()
    whys = condition_whys(core_manifest())
    level = lt.level_result(model, {})
    by_id = {c["id"]: c for c in level["conditions"]}
    for row in model["rows"]:
        for lv in row["levels"]:
            parts = [whys[condition_key(row["id"], lv["level"], c["signal"])] for c in lv["criteria"]]
            assert by_id[f"{row['id']}.{lv['level']}"]["why"] == " ".join(dict.fromkeys(parts))
    assert len(by_id["layer-0-instructions.2"]["why"].split(". ")) > 3  # three criteria
    custom = {**model, "steps": [], "rows": [{**model["rows"][0], "id": "own-row"}]}
    assert all("why" not in c for c in lt.level_result(custom, {})["conditions"])
