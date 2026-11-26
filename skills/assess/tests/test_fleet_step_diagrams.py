"""The Step diagrams (#941): the Step funnel, one change away and the Step movement grid.

Goldens: ``tests/fixtures/fleet_step_diagrams/`` (regenerate with
``uv run --locked python tests/fleet_step_diagrams_fixture.py`` from ``skills/assess``).
"""
from __future__ import annotations

import itertools
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import fleet_step_diagrams_fixture as fx
import lib.fleet.fleet_step_funnel as funnel
import lib.fleet.fleet_step_lifts as lifts
import lib.fleet.fleet_step_movement as movement
from lib.fleet.fleet_compare import load_run, stored_steps
from lib.fleet.fleet_data import level_results
from lib.fleet.fleet_diagrams import DIAGRAM_FILES, build_diagrams
from lib.fleet.fleet_history import OPTIONAL_HISTORY_FILES
from lib.fleet.fleet_level import LEVEL_FILE
from lib.fleet.fleet_opportunities import build_opportunities
from lib.scoring.level_result_types import LevelResult
from lib.scoring.level_signals import DEFAULT_LEVELS
from lib.scoring.levels import MaturityModel, condition_id
from lib.vocabulary import banned_hits
from svg_cascade import overridden_text_attributes
from test_fleet_cli import fleet_cli, froot  # noqa: F401 - froot is a fixture

FILES = (funnel.FILE, lifts.FILE, movement.FILE)
_SVG_TEXT = re.compile(r"<(text|title|desc)\b[^>]*>(.*?)</\1>", re.S)


def _prose(svg: str) -> str:
    return "\n".join(re.sub(r"<[^>]+>", " ", body) for _, body in _SVG_TEXT.findall(svg))


@pytest.mark.parametrize("name", sorted(fx.goldens()))
def test_render_matches_golden(name: str) -> None:
    assert fx.goldens()[name] == (fx.FIXTURES / name).read_text(encoding="utf-8")


def test_two_renders_are_byte_identical() -> None:
    assert fx.goldens() == fx.goldens()


@pytest.mark.parametrize("name", sorted(fx.goldens()))
def test_svg_is_well_formed_themed_and_plain(name: str) -> None:
    text = fx.goldens()[name]
    ET.fromstring(text)
    assert "<script" not in text and "href" not in text
    assert "prefers-color-scheme: dark" in text
    assert overridden_text_attributes(text) == []
    prose = _prose(text)
    assert banned_hits(prose) == []
    assert not re.search(r"\blayer\b", prose, re.I)  # no layer numbers or condition ids drawn
    assert "grp/" not in text  # no repository is named


# --- funnel ---------------------------------------------------------------------


def test_funnel_segments_sum_to_each_step_and_a_double_hold_counts_once() -> None:
    view = funnel.funnel_view(fx.MODEL, fx.data())
    for bar in view["bars"]:
        assert sum(s["count"] for s in bar["segments"]) == bar["count"]
    step1 = view["bars"][0]
    # delta is held by both Step 2 gates: once under instructions (first in model order)
    assert step1["count"] == 4 and step1["held_by_more"] == 1
    assert [(s["gate"], s["kind"], s["count"]) for s in step1["segments"]] == [
        ("instructions", "unmet", 3), ("agent_use", "not_measured", 1)]
    assert view["bars"][-1]["segments"] == [{"gate": None, "kind": "top", "reason": None, "count": 1}]


def test_a_not_measured_gate_is_hatched_with_its_reason_and_a_failed_scan_is_never_step_1() -> None:
    view = funnel.funnel_view(fx.MODEL, fx.data())
    assert view["not_measured"] == [("scan_timeout", 1)]
    assert sum(b["count"] for b in view["bars"]) == 6  # the failed scan is in no bar
    svg = funnel.render(fx.MODEL, fx.data())
    assert 'fill="url(#fleet-step-funnel-fleet-hatch)"' in svg and "data-mark=\"hatched\"" in svg
    assert "agent_use was not measured (needs_platform)" in svg
    assert "never drawn at Step 1: 1 scan_timeout" in svg


def test_an_owner_set_aside_gate_is_its_own_hatched_segment() -> None:
    data = fx.data()
    blocker = data["repos"][2]["next_step"]["blockers"][0]
    blocker.update(state="not_applicable", disposition="excluded_by_owner")
    view = funnel.funnel_view(fx.MODEL, data)
    assert ("instructions", "set_aside", 1) in [(s["gate"], s["kind"], s["count"]) for s in view["bars"][0]["segments"]]
    assert "the owner set instructions aside" in funnel.render(fx.MODEL, data)


# --- one change away ------------------------------------------------------------


def _brute_force(model: MaturityModel, results: list[LevelResult]) -> dict[tuple[str, int], int]:
    """Flip each unmet condition to met alone, recompute the Step from the gates, and count the
    repositories exactly one flip lifts to their next Step."""
    steps = sorted(model["steps"], key=lambda s: s["step"])

    def step_of(states: dict[str, str]) -> int:
        held = 0
        for s in steps:
            if any(states[condition_id(g["row"], n)] != "met" for g in s["gates"] for n in range(1, g["level"] + 1)):
                break
            held = s["step"]
        return held

    out: dict[tuple[str, int], int] = {}
    for result in results:
        if result["step"] is None or result["next_step"] is None:
            continue
        states = {c["id"]: c["state"] for c in result["conditions"]}
        assert step_of(states) == result["step"]
        lifting = [cid for cid, state in states.items() if state == "unmet"
                   and step_of({**states, cid: "met"}) >= result["next_step"]["step"]]
        if len(lifting) == 1:
            key = (lifting[0], result["next_step"]["step"])
            out[key] = out.get(key, 0) + 1
    return out


def test_step_lifts_equal_a_brute_force_over_every_scan_combination() -> None:
    rows = tuple(fx.row(f"grp/r{i}", fx.scans(lines, agent, ci)) for i, (lines, agent, ci)
                 in enumerate(itertools.product((0, 10), (0, 3, None), (True, False))))
    results = [r for r in level_results(fx.MODEL, rows).values() if r is not None]
    found = {(lift["condition"], lift["step"]): lift["repo_count"] for lift in lifts.step_lifts(fx.MODEL, results)}
    assert found == _brute_force(fx.MODEL, results) and found


def test_step_lifts_rank_by_count_and_never_count_an_unknown_gate() -> None:
    found = fx.data()["opportunities"]["step_lifts"]
    assert [(f["gate"], f["step"], f["repo_count"]) for f in found] == [("instructions", 2, 2), ("ci_runs_tests", 3, 1)]
    svg = lifts.render(fx.data())
    assert "1 repository waits on a gate that was not measured" in svg
    assert "1 repository had no measured Step" in svg


def test_build_opportunities_writes_step_lifts_without_repository_keys() -> None:
    results = level_results(fx.MODEL, fx.ROWS)
    opps = build_opportunities(fx.MODEL, fx.ROWS, lambda k: "hashed", results)
    assert opps is not None and opps["step_lifts"] == fx.data()["opportunities"]["step_lifts"]
    assert "grp/" not in json.dumps(opps["step_lifts"])


def test_no_lift_still_draws_a_line_not_a_zero_bar() -> None:
    svg = lifts.render({"repos": [], "opportunities": {"step_lifts": []}})
    assert "No single change takes a repository to its next Step" in svg and "<rect x=\"16.0\" y=\"78" not in svg


# --- Step movement --------------------------------------------------------------


def _cells(moves: movement.StepMoves) -> dict[tuple[str, str], tuple[int, int]]:
    return {(c["then"], c["now"]): (c["count"], c["rule_only"]) for c in moves["cells"]}


def test_grid_cells_sum_to_the_repositories_in_both_runs_and_added_and_removed_keep_their_lanes() -> None:
    moves = movement.step_moves(fx.MODEL, fx.earlier_run(), fx.data())
    cells = _cells(moves)
    both = {r["repo"] for r in fx.EARLIER_ROWS} & {r["repo"] for r in fx.ROWS}
    assert sum(n for (t, w), (n, _) in cells.items() if t != movement.ADDED and w != movement.REMOVED) == len(both)
    assert {k: v for k, v in cells.items() if movement.ADDED in k or movement.REMOVED in k} == {
        (movement.ADDED, "1"): (1, 0), ("3", movement.REMOVED): (1, 0)}
    assert cells[("2", "3")] == (1, 0)  # alpha gained CI: a real move
    assert cells[(movement.NOT_MEASURED, movement.NOT_MEASURED)] == (1, 0)  # never Step 1


def test_a_rule_change_move_is_counted_apart_and_drawn_dashed() -> None:
    earlier = fx.earlier_run(**fx.RULE_CHANGE)
    assert fx.reasons(earlier)  # a version reason the re-score bridges
    moves = movement.step_moves(fx.MODEL, earlier, fx.data(), fx.reasons(earlier))
    assert moves["comparable"] and moves["rescored"] and moves["split"]
    cells = _cells(moves)
    assert cells[("2", "1")] == (1, 1)  # charlie: Step 2 under the old rules, 1 now and re-scored
    assert cells[("2", "3")] == (1, 0)  # alpha: a real move, solid
    svg = movement.render(moves)
    assert svg.count('data-mark="rule-change"') == 1 and "1 by rules" in svg
    assert re.search(r'data-then="2" data-now="3"><rect[^>]*class="f-up s-rule" stroke-width="1"/>', svg)


def test_an_earlier_run_without_level_json_is_not_split() -> None:
    moves = movement.step_moves(fx.MODEL, fx.earlier_run(stored=False), fx.data())
    assert not moves["split"] and all(c["rule_only"] == 0 for c in moves["cells"])
    assert "kept no Step results" in movement.render(moves)


def test_a_non_comparable_pair_shows_the_reasons_and_no_grid() -> None:
    moves = movement.step_moves(fx.MODEL, fx.earlier_run(), fx.data(), fx.NOT_COMPARABLE)
    assert not moves["comparable"] and moves["cells"] == []
    svg = movement.render(moves)
    assert "Not compared" in svg and "window changed" in svg and "data-then" not in svg


def test_an_unreadable_earlier_run_is_a_fixed_reason_never_its_path() -> None:
    moves = movement.step_moves(fx.MODEL, "/home/someone/acme-fleet: no run.json", fx.data())
    assert moves["reasons"] == [movement.UNREADABLE]
    assert "acme" not in movement.render(moves)


# --- wiring ---------------------------------------------------------------------


def test_a_one_repository_fleet_draws_all_three() -> None:
    one = fx.data(fx.ROWS[:1])
    diagrams = build_diagrams(fx.MODEL, list(fx.ROWS[:1]), "6mo", data=one, earlier=fx.earlier_run(fx.EARLIER_ROWS[:1]))
    by_file = {d["file"]: d for d in diagrams}
    for name in FILES:
        assert by_file[name]["svg"] is not None, by_file[name]["skipped"]
    assert set(FILES) <= set(DIAGRAM_FILES)


def test_step_diagrams_say_why_they_are_skipped() -> None:
    no_steps: MaturityModel = {**fx.MODEL, "steps": []}
    by_file = {d["file"]: d for d in build_diagrams(no_steps, list(fx.ROWS), "6mo", data=fx.data(), earlier=None)}
    assert by_file[funnel.FILE]["skipped"] == "skipped: the maturity model defines no Steps."
    assert by_file[movement.FILE]["skipped"].startswith("skipped: no earlier run compared")
    by_file = {d["file"]: d for d in build_diagrams(fx.MODEL, list(fx.ROWS), "6mo", data=None)}
    assert by_file[lifts.FILE]["skipped"] == "skipped: the run wrote no fleet-data.json to read the Steps from."


def test_level_json_is_kept_in_the_history_and_read_back_as_stored_steps(tmp_path: Path) -> None:
    assert LEVEL_FILE in OPTIONAL_HISTORY_FILES
    (tmp_path / LEVEL_FILE).write_text(json.dumps({"repos": {"a": {"step": 2}, "b": {"step": None}}}),
                                       encoding="utf-8")
    assert stored_steps(tmp_path / LEVEL_FILE) == {"a": 2, "b": None}
    assert stored_steps(tmp_path / "absent.json") is None


@pytest.mark.parametrize("mode", [[], ["--full"], ["--hash-salt", "s3cret"]], ids=["counts-only", "full", "hashed"])
def test_fleet_writes_the_step_diagrams_naming_no_repository(froot: Path, tmp_path: Path,  # noqa: F811
                                                             mode: list[str]) -> None:
    out, second = tmp_path / "out", tmp_path / "second"
    history = ["--no-history"] if mode == ["--full"] else ["--history-dir", str(tmp_path / "h")]
    base = [str(froot), "--levels", str(DEFAULT_LEVELS), *history, *mode]
    assert fleet_cli.main([*base, "--out", str(out)]) == 0
    assert fleet_cli.main([*base, "--out", str(second), "--compare", str(out)]) == 0
    report = (second / "report.md").read_text(encoding="utf-8")
    for name in FILES:
        text = (second / name).read_text(encoding="utf-8")
        for leak in ("grpa", "grpb", "svc1", "svc2", "lib1", str(froot)):
            assert leak not in text, (name, leak)
        assert f"](./{name})" in report
    assert load_run(out).steps is not None


def test_hashed_runs_that_share_no_id_are_not_comparable() -> None:
    earlier = fx.earlier_run()
    hashed = type(earlier)({**earlier.stamp, "paths_hashed": True}, [{**r, "repo": "h" + r["repo"]} for r in earlier.repos],
                           [], None, None)
    moves = movement.step_moves(fx.MODEL, hashed, fx.data())
    assert not moves["comparable"] and moves["reasons"] == [movement.NO_JOIN]


def test_a_stored_step_this_model_lacks_keeps_its_own_lane() -> None:
    steps = {**fx.OLD_RULES_STEPS, "grp/alpha": 5}
    moves = movement.step_moves(fx.MODEL, fx.earlier_run(levels_version="old", steps=steps), fx.data())
    cells = _cells(moves)
    assert moves["steps"] == [1, 2, 3, 5] and cells[("5", "3")] == (1, 0)
    both = {r["repo"] for r in fx.EARLIER_ROWS} & {r["repo"] for r in fx.ROWS}
    assert sum(n for (t, w), (n, _) in cells.items() if t != movement.ADDED and w != movement.REMOVED) == len(both)


def test_a_rules_hash_change_under_one_version_still_draws_the_grid_with_rule_only_counts() -> None:
    """#952: an edited --levels under one version is a rule reason, bridged by the re-score."""
    from lib.fleet.fleet_compare import comparability
    from lib.fleet.fleet_method import rules_sha256

    base = fx.earlier_run(steps=fx.OLD_RULES_STEPS)
    earlier = type(base)({**base.stamp, "rules_sha256": "0" * 64}, base.repos, [], None, base.steps)
    run = {"levels_version": fx.MODEL["version"], "rules_sha256": rules_sha256(fx.MODEL)}
    reasons = comparability(earlier.stamp, {**earlier.stamp, **run})[0]  # type: ignore[typeddict-item]
    assert reasons and all("rules changed" in r for r in reasons)
    moves = movement.step_moves(fx.MODEL, earlier, {**fx.data(), "run": run}, reasons)
    assert moves["comparable"] and moves["rescored"]
    assert _cells(moves)[("2", "1")] == (1, 1)
    assert "other maturity-model rules" in movement.render(moves)
    blocked = movement.step_moves(fx.MODEL, earlier, {**fx.data(), "run": run}, [*reasons, *fx.NOT_COMPARABLE])
    assert blocked["reasons"] == fx.NOT_COMPARABLE  # only the rule reason is bridged
