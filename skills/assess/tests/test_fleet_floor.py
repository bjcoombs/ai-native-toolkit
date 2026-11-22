"""A level's floor when only a higher level's signal is unmeasured (issue #697).

``lib.levels`` gives an unknown row ``floor`` when its confirmed criteria reach
at least level 1; ``lib.fleet_groups`` carries it, and ``full_run_only`` names
the missing signals only a full ``/assess`` measures; the views print "at least".
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

import assess_signals
from lib import fleet_heatmap as fh
from lib import fleet_layer_bars as flb
from lib import fleet_maturity as fm
from lib import fleet_next_steps as ns
from lib import fleet_quadrant as fq
from lib import signals
from lib.fleet import FLEET_ROW_VERSION, FleetRow
from lib.fleet_groups import FLEET_GROUP_VERSION, full_run_only_signals, group_row
from lib.fleet_view import FleetViewError, _check_row
from lib.level_signals import load_default_levels
from lib.levels import MaturityModel, evaluate, evaluate_row, floor_of, parse_levels

GATE = {"signal": "gate.enforced", "op": "eq", "value": True}
MUTATION = {"signal": "pressure.mutation_run", "op": "eq", "value": True}


def _model(aggregation: str = "min") -> MaturityModel:
    return parse_levels({"version": "t-1", "rows": [
        {"id": "cov", "title": "Coverage", "scope": "team", "aggregation": aggregation,
         "levels": [{"level": 1, "criteria": [GATE]}, {"level": 2, "criteria": [MUTATION]}]},
        {"id": "two", "title": "Two-factor", "scope": "org",
         "levels": [{"level": 1, "criteria": [{"signal": "tf.value", "op": "eq", "value": "on"}]},
                    {"level": 2, "criteria": [{"signal": "push.value", "op": "eq", "value": "on"}]}]},
    ]})


NOT_REGISTERED = {"available": False, "reason": "not_registered"}


def _scans(gate: bool | None = True, pressure: Any = None) -> dict[str, Any]:
    scans: dict[str, Any] = {"pressure": NOT_REGISTERED if pressure is None else pressure}
    if gate is not None:
        scans["gate"] = {"available": True, "enforced": gate}
    return scans


def _row(repo: str, scans: dict[str, Any] | None) -> FleetRow:
    return {"fleet_row_version": FLEET_ROW_VERSION, "repo": repo, "group": repo.rsplit("/", 1)[0],
            "status": "ok" if scans is not None else "failed",
            "reason": None if scans is not None else "scan_error",
            "signals_schema_version": 1 if scans is not None else None, "scans": scans,
            "active": True if scans is not None else None}


def _cov(row: dict[str, Any]) -> dict[str, Any]:
    entry: dict[str, Any] = next(e for e in row["levels"] if e["id"] == "cov")
    return entry


# --- lib.levels ----------------------------------------------------------------


def test_a_met_level_with_only_a_higher_signal_missing_has_a_floor():
    row = _model()["rows"][0]
    ev = evaluate_row(row, _scans())
    assert (ev["status"], ev["level"], ev["bounds"], ev["floor"]) == ("unknown", None, [1, 2], 1)
    assert floor_of(ev) == 1


@pytest.mark.parametrize("data", [
    {},  # nothing measured
    _scans(gate=None, pressure={"available": True, "mutation_run": True}),  # level 1 missing, 2 present
])
def test_no_floor_when_level_one_is_not_confirmed(data):
    ev = evaluate_row(_model()["rows"][0], data)
    assert ev["status"] == "unknown" and "floor" not in ev and floor_of(ev) is None


def test_a_known_row_never_carries_a_floor():
    ev = evaluate_row(_model()["rows"][0], _scans(gate=False))
    assert (ev["status"], ev["level"]) == ("level", 0) and "floor" not in ev
    assert floor_of({"status": "level", "level": 1, "floor": 1}) is None
    assert floor_of({"status": "unknown", "floor": True}) is None  # a bool is not a floor


def test_team_and_org_results_carry_the_rolled_up_floor():
    result = evaluate(_model(), {"name": "g", "settings": {"tf": {"value": "on"}},
                                 "repos": {"a": _scans(), "b": _scans()}})
    team, org = result["rows"]
    assert (team["status"], team["floor"]) == ("unknown", 1)
    assert (org["status"], org["floor"]) == ("unknown", 1)
    # one repository below the floor pulls a min roll-up's floor to 0: no key
    capped = evaluate(_model(), {"name": "g", "settings": None, "repos": {"a": _scans(), "b": {}}})
    assert "floor" not in capped["rows"][0]


# --- groups.jsonl ----------------------------------------------------------------


def test_group_row_carries_floor_and_full_run_only():
    row = group_row("g", [_row("g/a", _scans())], _model(), {})
    entry = _cov(dict(row))
    assert (entry["status"], entry["level"], entry["floor"]) == ("unknown", None, 1)
    assert entry["full_run_only"] == ["pressure.mutation_run"]


def test_full_run_only_reads_the_code_of_a_full_reason_sentence():
    full = {"available": False, "reason": signals.UNREGISTERED_REASON}
    net = {"available": False, "reason": signals.NETWORK_REASON}
    for block in (full, net):
        assert _cov(dict(group_row("g", [_row("g/a", _scans(pressure=block))], _model(), {})))[
            "full_run_only"] == ["pressure.mutation_run"]


def test_full_run_only_reasons_are_derived_from_the_runner_reasons():
    assert signals.FULL_RUN_ONLY_REASONS == {"not_registered", "network"}


@pytest.mark.parametrize("pressure", [
    {"available": False, "reason": "no_remote"},  # another code: a plain gap
    {"available": True, "mutation_run": "yes"},  # a type mismatch, not a full-run gap
])
def test_other_gaps_are_not_full_run_only(pressure):
    entry = _cov(dict(group_row("g", [_row("g/a", _scans(pressure=pressure))], _model(), {})))
    assert entry["floor"] == 1 and "full_run_only" not in entry


def test_a_failed_repository_missing_the_signal_keeps_it_off_full_run_only():
    evaluations = {"a": evaluate_row(_model()["rows"][0], _scans()),
                   "b": evaluate_row(_model()["rows"][0], {})}
    assert full_run_only_signals(evaluations, {"a": _scans(), "b": {}}) == []
    assert full_run_only_signals({"a": evaluations["a"]}, {"a": _scans()}) == ["pressure.mutation_run"]


def test_rows_without_a_floor_are_unchanged():
    row = group_row("g", [_row("g/a", _scans(gate=False))], _model(), {})
    assert set(_cov(dict(row))) == {"id", "scope", "status", "level", "top_level", "aggregation",
                                    "missing_signals", "unknown_repos"}


def test_fleet_view_reads_rows_with_and_without_floor_and_rejects_a_malformed_one():
    base = {"fleet_group_version": FLEET_GROUP_VERSION, "group": "g", "levels_version": "t-1"}
    entry = {"id": "cov", "scope": "team", "status": "unknown", "level": None,
             "missing_signals": ["x"], "unknown_repos": ["g/a"], "aggregation": None}
    _check_row({**base, "levels": [entry]}, "old")  # written before #697
    _check_row({**base, "levels": [{**entry, "floor": 1, "full_run_only": ["x"]}]}, "new")
    for bad in ({"floor": 0}, {"floor": "1"}, {"floor": 1, "status": "level", "level": 1},
                {"full_run_only": "x"}):
        with pytest.raises(FleetViewError):
            _check_row({**base, "levels": [{**entry, **bad}]}, "bad")


# --- the shared mean ---------------------------------------------------------------


def test_floor_mean_counts_floored_rows_at_their_floor_beside_the_known_mean():
    cells = [(2, None, 2), (None, 1, 4), (None, None, 3)]
    assert fm.mean_share((level, top) for level, _, top in cells) == 1.0
    assert fm.floor_mean(cells) == (1.0 + 0.25) / 2  # a bound over more rows; can sit lower
    assert fm.has_floor(cells) and not fm.has_floor([(2, None, 2), (None, None, 3)])
    assert fm.floor_mean([(None, None, 3)]) is None


# --- views ---------------------------------------------------------------------------


def test_heatmap_draws_a_floor_apart_from_known_and_unknown():
    row = group_row("g", [_row("g/a", _scans())], _model(), {})
    columns = fh.columns_for([row], _model())
    svg = fh.render_heatmap([row], columns)
    assert ">1+ a<" in svg and "url(#fleet-floor)" in svg and 'id="fleet-floor"' in svg
    assert "at least level 1 of 2" in svg and "needs a full /assess: pressure.mutation_run" in svg
    plain = group_row("g", [_row("g/a", {})], _model(), {})
    assert "fleet-floor" not in fh.render_heatmap([plain], columns)


def test_layer_bars_and_quadrant_show_the_floor():
    model = _model()
    repos = [_row("g/a", _scans()), _row("g/b", _scans(gate=False)), _row("g/c", {})]
    bars = flb.layer_bars(model, repos)
    assert bars[0]["unknown"] == ["g/a", "g/c"] and bars[0]["floored"] == {"g/a": 1}
    assert flb.floor_counts(bars[0]) == [(1, 1)]
    svg = flb.render_layer_bars(bars)
    assert "at least level 1 of 2 for g/a" in svg and "level unknown for g/c" in svg
    assert "g/a (at least 1)" in svg
    points = {p["repo"]: p for p in fq.repo_points(model, repos)}
    assert points["g/a"]["maturity"] is None and points["g/a"]["maturity_floor"] == 0.5
    assert "maturity_floor" not in points["g/b"] and "maturity_floor" not in points["g/c"]
    assert "g/a (level unknown, at least 0.50 with floors" in fq.render_quadrant(list(points.values()), "6mo")


def test_next_steps_say_at_least_and_needs_a_full_assess():
    model = _model()
    rows = [_row("g/a", _scans())]
    group = group_row("g", rows, model, {})
    results = ns.evaluate_group(model, "g", rows, {})
    page = ns.group_page(model, group, rows, results)
    assert "| Coverage | at least 1 of 2 (higher unknown) |" in page
    assert "In place: at least level 1 of 2, confirmed; higher levels are not measured yet (up to 2)." in page
    assert ns.FULL_RUN_HINT in page


# --- end to end: the real headless runner ---------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                   encoding="utf-8", errors="replace", check=True)


@pytest.mark.parametrize("counts_only", [True, False])
def test_a_headless_coverage_gate_repository_gets_layer_6_floor_1(tmp_path, counts_only):
    repo = tmp_path / "svc"
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / "pyproject.toml").write_text("[tool.coverage.report]\nfail_under = 100\n", encoding="utf-8")
    (repo / ".github" / "workflows" / "ci.yml").write_text(
        "on: push\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - run: pytest --cov --cov-fail-under=100\n", encoding="utf-8")
    _git(tmp_path, "init", "-q", str(repo))
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=a@example.com", "-c", "user.name=A", "commit", "-qm", "init")
    out = tmp_path / "signals.json"
    flags = ["--counts-only"] if counts_only else []
    assert assess_signals.main([str(repo), "--out", str(out), "--scans",
                                "coverage_gate,test_pressure", *flags]) == 0
    scans = json.loads(out.read_text(encoding="utf-8"))["scans"]
    row = group_row("g", [_row("g/svc", scans)], load_default_levels(), {})
    entry = next(e for e in row["levels"] if e["id"] == "layer-6-coverage")
    assert (entry["status"], entry["level"], entry.get("floor")) == ("unknown", None, 1)
    assert "test_pressure.mutation_run" in entry.get("full_run_only", [])


# --- compare, slope and trend ---------------------------------------------------------


def test_a_floor_appearing_is_not_a_move_but_is_carried():
    from lib import fleet_compare as fc
    rows = [_row("g/a", _scans())]
    old = group_row("g", rows, _model(), {})
    old_levels = [{k: v for k, v in e.items() if k not in ("floor", "full_run_only")} for e in old["levels"]]
    then: Any = {**old, "levels": old_levels}  # an entry written before #697
    now = group_row("g", rows, _model(), {})
    change = fc.group_change("g", {"g/a": rows[0]}, {"g/a": rows[0]}, then, now, set())
    cov = next(r for r in change["rows"] if r["id"] == "cov")
    assert cov["moved"] is False and cov["now"] == {"status": "unknown", "level": None, "floor": 1}
    assert fc._show(cov["now"]) == "unknown, at least 1"


def test_slope_and_trend_carry_the_bound_without_moving_the_line():
    from lib import fleet_slope as fs
    from lib import fleet_trends as ft
    state: Any = {"status": "unknown", "level": None, "floor": 1}
    known: Any = {"status": "level", "level": 2}
    move: Any = {"id": "cov", "scope": "team", "then": state, "now": known, "moved": True, "cause": "level"}
    comparison: Any = {"groups": [{"group": "g", "rows": [move]}], "earlier": None, "later": None}
    group = fs.slope_view(_model(), comparison)["groups"][0]
    assert (group["then"], group["now"]) == (None, None)  # the line pairs known rows only
    assert (group["floor_then"], group["floor_now"]) == (0.5, 1.0)
    run = ft.trend_run({"levels_version": "t-1", "scan_date": "2026-11-20"},
                       [group_row("g", [_row("g/a", _scans())], _model(), {})])
    assert run is not None and run["means"]["g"] is None and run["floor_means"] == {"g": 0.5}


def test_charts_print_the_lower_bound_as_visible_text():
    from lib import fleet_slope as fs
    from lib import fleet_trends as ft
    floored: Any = {"status": "unknown", "level": None, "floor": 1}
    known: Any = {"status": "level", "level": 1}
    rows: Any = [{"id": "cov", "scope": "team", "then": known, "now": floored, "moved": True,
                  "cause": "level"}]
    view = fs.slope_view(_model(), {"groups": [  # type: ignore[typeddict-item]
        {"group": "g", "rows": rows},
        {"group": "h", "rows": [{**rows[0], "now": known, "moved": False, "cause": None}]}],
        "earlier": None, "later": None})
    slope = fs.render_slope(view)
    assert "at least: g 0.50 → 0.50" in slope and "with floors at least 0.50 → 0.50" not in slope
    run = ft.trend_run({"levels_version": "t-1", "scan_date": "2026-11-20"},
                       [group_row("g", [_row("g/a", _scans())], _model(), {}),
                        group_row("h", [_row("h/a", _scans(gate=False))], _model(), {})])
    assert run is not None
    assert "at least, on 2026-11-20: g 0.50" in " ".join(ft._bound_lines(run, ["g", "h"]))
    repos = [_row("g/a", _scans(pressure={"available": True, "mutation_run": False})),
             _row("g/b", _scans())]
    points = fq.repo_points(_model(), repos)
    for p in points:
        p["commits"] = 3
    quadrant = fq.render_quadrant(points, "6mo")
    assert "At least, with floors (plotted repositories)" not in quadrant  # g/b is not plotted
    assert "g/b (level unknown, at least 0.50 with floors, 3 commits)" in quadrant


def test_rows_without_floors_render_no_floor_text():
    from lib import fleet_trends as ft
    run = ft.trend_run({"levels_version": "t-1", "scan_date": "2026-11-20"},
                       [group_row("g", [_row("g/a", _scans(gate=False))], _model(), {})])
    assert run is not None and "floor_means" not in run and ft._bound_lines(run, ["g"]) == []


def test_a_platform_scan_block_is_not_full_run_only_and_gets_the_platform_hint():
    block = signals.PLATFORM_SCANS[0]  # review_reality: fleet --platform measures it
    model = parse_levels({"version": "t-1", "rows": [
        {"id": "rev", "title": "Review", "scope": "team", "aggregation": "min",
         "levels": [{"level": 1, "criteria": [GATE]},
                    {"level": 2, "criteria": [{"signal": f"{block}.reviewed_share", "op": "gte",
                                               "value": 0.5}]}]}]})
    for reason in ("network", signals.NETWORK_REASON):
        scans = {"gate": {"available": True, "enforced": True},
                 block: {"available": False, "reason": reason}}
        rows = [_row("g/a", scans)]
        group = group_row("g", rows, model, {})
        entry = group["levels"][0]
        assert entry.get("floor") == 1 and "full_run_only" not in entry
        page = ns.group_page(model, group, rows, ns.evaluate_group(model, "g", rows, {}))
        assert ns.PLATFORM_HINT in page and ns.FULL_RUN_HINT not in page
        assert "fleet --platform" in ns.PLATFORM_HINT and "does close" in ns.PLATFORM_HINT
