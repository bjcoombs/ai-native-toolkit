"""Step targets: the targets file, target_hit and progress (lib/fleet/fleet_targets.py, lib/fleet/fleet_step_targets.py, #945)."""
from __future__ import annotations

import json
import re
import tomllib

import jsonschema
import pytest

from fleet_data_fixture import LEVELS
import lib.fleet.fleet_achievements as fa
from lib.fleet.fleet_achievements_schema import render
from lib.fleet.fleet_credit import credit_section
from lib.fleet.fleet_data import Privacy
from lib.fleet.fleet_targets import TargetSpec, TargetsError, check_units, parse_targets, resolve
from lib.fleet.fleet_units import build_trees, empty_map
from lib.levels import MaturityModel, parse_levels
from test_fleet_achievements import Spec, _data

MODEL: MaturityModel = parse_levels(tomllib.loads(LEVELS))  # Step 2: docs gate; Step 3: docs and ci gates

# docs 0 holds the docs gate, ci False the ci gate: a and d reach Step 3, b Step 2 (ci), and c
# Step 1, two Steps below Step 3 with both its gates missing (only docs is its next Step's)
BASE: Spec = {"grp/sub/a": (1, True), "grp/sub/b": (1, False), "grp/c": (0, False), "grp/two/d": (1, True)}
HISTORY: list[tuple[str, Spec]] = [
    ("2026-11-01", BASE),
    ("2026-11-08", BASE),
    ("2026-11-15", {**BASE, "grp/sub/b": (1, True)}),  # b: ci met, Step 2 -> 3
    ("2026-11-22", {**BASE, "grp/sub/b": (2, True)}),  # b: docs 2, still Step 3
]
TEAM = ("ownership", "ownership/grp/sub")


def _build(history: list[tuple[str, Spec]] = HISTORY, model: MaturityModel = MODEL,
           specs: list[TargetSpec] | None = None) -> fa.Achievements:
    runs = [fa.HistoryRun(date, _data(date, spec)) for date, spec in history]
    targets = resolve(specs if specs is not None else [TargetSpec("ownership", "grp/sub", 3)], Privacy().key)
    return fa.build_achievements(runs[-1], runs[:-1], model, targets=targets)


def _hits(doc: fa.Achievements) -> list[tuple[str, str, int | None]]:
    return [(a["run"], a["unit"], a["evidence"]["target_step"]) for a in doc["achievements"]
            if a["kind"] == fa.TARGET_HIT]


def test_a_step_3_target_is_hit_on_the_run_the_lowest_step_reaches_3_and_no_other() -> None:
    doc = _build()
    assert _hits(doc) == [("2026-11-15", TEAM[1], 3)]
    (hit,) = [a for a in doc["achievements"] if a["kind"] == fa.TARGET_HIT]
    ev = hit["evidence"]
    assert (ev["row"], ev["from_step"], ev["to_step"], ev["target_step"]) == (None, 2, 3, 3)
    assert hit["credit"] == ("`ownership/grp/sub` reached its target of Step 3: Step 2 to Step 3 "
                             "(the lowest Step over its 2 measured repositories).")
    assert hit["previous_run"] == "2026-11-08"
    (progress,) = doc["targets"]
    assert progress == {"view": "ownership", "unit": TEAM[1], "step": 3, "lowest_step": 3, "measured": 2,
                        "repos": 2, "distance": 0, "held": 0,
                        "missing_gates": []}
    jsonschema.validate(json.loads(fa.dumps_achievements(doc)), json.loads(render()))


def test_before_the_hit_progress_lists_exactly_the_missing_gates_of_the_target_step() -> None:
    (progress,) = _build(HISTORY[:2])["targets"]
    assert progress == {"view": "ownership", "unit": TEAM[1], "step": 3, "lowest_step": 2, "measured": 2,
                        "repos": 2, "distance": 1, "held": 1,
                        "missing_gates": [{"gate": "ci", "row": "ci", "level": 1, "held": 1,
                                           "not_measured": 0, "excluded": 0}]}
    assert not _hits(_build(HISTORY[:2]))


def test_a_repository_two_steps_below_the_target_shows_every_missing_gate() -> None:
    """c sits at Step 1: its own next Step (2) is held by docs only, but Step 3 also needs ci,
    which only its ``gate_blockers`` say (#945)."""
    doc = _build(specs=[TargetSpec("ownership", "grp", 3), TargetSpec("repo", "grp/c", 3)])
    by_unit = {t["unit"]: t for t in doc["targets"]}
    org, repo = by_unit["ownership/grp"], by_unit["grp/c"]
    assert (org["lowest_step"], org["held"], org["measured"], org["repos"]) == (1, 1, 4, 4)
    assert [(g["gate"], g["held"]) for g in org["missing_gates"]] == [("docs", 1), ("ci", 1)]
    assert [(g["gate"], g["held"]) for g in repo["missing_gates"]] == [("docs", 1), ("ci", 1)]
    assert not _hits(doc)  # c holds the org at Step 1 throughout


def test_the_engines_lowest_step_is_the_bundles_and_the_next_step_hold_matches_the_roll_up() -> None:
    data = _data("2026-11-08", BASE)
    rules = {"docs": (fa.Fraction(1), 2), "ci": (fa.Fraction(1), 1)}
    subs = fa.subjects(data, rules)
    for view in data["views"]:
        for unit in view["units"]:
            sub = subs[(view["view"], unit["key"])]
            assert sub.step.lowest == unit["step"]["lowest_step"]
            assert len(sub.step.measured) == unit["step"]["measured"]
            nxt = unit["step"]["next_step"]
            if nxt is None:
                continue
            spec = TargetSpec(view["view"], unit["key"].split("/", 1)[1], nxt["step"])
            runs = [fa.HistoryRun("2026-11-08", data)]
            (progress,) = fa.build_achievements(runs[0], [], MODEL, targets=resolve([spec], Privacy().key))["targets"]
            assert progress["missing_gates"] == [g for g in nxt["gates"] if g["held"]]
            assert progress["held"] == nxt["held"]


@pytest.mark.parametrize("history", [
    # b fails its scan on the hit run: the measured set changes, so nothing is earned
    [HISTORY[1], ("2026-11-15", {**BASE, "grp/sub/b": (-1, True)})],
    # b joins on the hit run
    [("2026-11-08", {k: v for k, v in BASE.items() if k != "grp/sub/b"}), HISTORY[2]],
])
def test_an_unmeasured_or_new_repository_neither_earns_nor_blocks(history: list[tuple[str, Spec]]) -> None:
    assert not _hits(_build(history))


@pytest.mark.parametrize("history", [
    # b fails on the run where a alone already reads Step 3, and returns at Step 3
    [HISTORY[0], HISTORY[1], ("2026-11-15", {**BASE, "grp/sub/b": (-1, True)}), ("2026-11-22", HISTORY[2][1])],
    # b fails on the run before the crossing (it was at Step 2) and returns at Step 3
    [HISTORY[0], ("2026-11-08", {**BASE, "grp/sub/b": (-1, True)}), HISTORY[2]],
])
def test_a_failed_scan_before_the_crossing_never_loses_the_hit(history: list[tuple[str, Spec]]) -> None:
    """The return run compares with the latest earlier run measuring the same repositories."""
    doc = _build([*history, ("2026-11-29", HISTORY[3][1]), ("2026-12-06", HISTORY[3][1])])
    returned = history[-1][0]
    assert _hits(doc) == [(returned, TEAM[1], 3)]  # exactly once, never again on later runs
    (hit,) = [a for a in doc["achievements"] if a["kind"] == fa.TARGET_HIT]
    compared = [date for date, spec in history if spec.get("grp/sub/b", (0, True))[0] != -1][-2]
    assert hit["previous_run"] == compared and hit["evidence"]["from_step"] == 2


def test_one_crossing_is_credited_once_when_a_member_returns_after_the_others_crossed() -> None:
    """a crosses alone while b's scan fails; b's return compares with the last run measuring
    both, but the crossing was already credited after it, so there is no second hit."""
    history: list[tuple[str, Spec]] = [
        ("2026-11-01", {**BASE, "grp/sub/a": (1, False)}),  # {a, b} at Step 2
        ("2026-11-08", {**BASE, "grp/sub/a": (1, False), "grp/sub/b": (-1, True)}),  # {a} at Step 2
        ("2026-11-15", {**BASE, "grp/sub/b": (-1, True)}),  # {a} at Step 3: the crossing
        ("2026-11-22", {**BASE, "grp/sub/b": (1, True)}),  # {a, b} at Step 3
    ]
    assert _hits(_build(history)) == [("2026-11-15", TEAM[1], 3)]
    # a real drop and recovery after the credited hit is a new crossing
    again = [*history, ("2026-11-29", {**BASE, "grp/sub/b": (1, False)}), ("2026-12-06", HISTORY[3][1])]
    assert _hits(_build(again)) == [("2026-11-15", TEAM[1], 3), ("2026-12-06", TEAM[1], 3)]


def test_the_hit_fires_once_and_not_again_after_a_later_gap() -> None:
    gap = [*HISTORY[:3], ("2026-11-22", {**BASE, "grp/sub/b": (-1, True)}), ("2026-11-29", HISTORY[3][1])]
    assert _hits(_build(gap)) == [("2026-11-15", TEAM[1], 3)]


def test_a_stored_run_without_steps_is_neutral() -> None:
    runs = [fa.HistoryRun(date, _data(date, spec)) for date, spec in HISTORY[1:3]]
    for repo in runs[0].data["repos"]:  # written before #944
        for field in ("step", "next_step", "tally", "gate_blockers"):
            repo.pop(field)  # type: ignore[misc]
    targets = resolve([TargetSpec("ownership", "grp/sub", 3)], Privacy().key)
    assert not _hits(fa.build_achievements(runs[1], runs[:1], MODEL, targets=targets))


def test_progress_without_a_measured_member_reads_unknown() -> None:
    (progress,) = _build([("2026-11-01", {"grp/sub/a": (-1, True)})])["targets"]
    assert (progress["lowest_step"], progress["distance"], progress["measured"], progress["repos"]) == (None, None, 0, 1)
    assert "- `ownership/grp/sub`, Step 3: not measured this run" in credit_section(
        _build([("2026-11-01", {"grp/sub/a": (-1, True)})]), MODEL)


def test_the_report_names_the_missing_gates_and_no_count() -> None:
    assert "- `ownership/grp/sub`, Step 3: at Step 2 (2 of 2 measured); gates to go: `ci` holds back 1." \
        in credit_section(_build(HISTORY[:2]), MODEL)
    assert "- `ownership/grp/sub`, Step 3: met (Step 3, 2 of 2 measured)." in credit_section(_build(), MODEL)
    assert "threshold" not in credit_section(_build(HISTORY[:2]), MODEL)


def test_two_runs_give_identical_bytes() -> None:
    assert fa.dumps_achievements(_build()) == fa.dumps_achievements(_build())


# --- the targets file ------------------------------------------------------------


def test_a_targets_file_names_a_step() -> None:
    specs = parse_targets(tomllib.loads('[[target]]\nunit = "grp/sub"\nstep = 3\n'
                                        '[[target]]\nview = "repo"\nunit = "grp/c"\nstep = 2\n'), MODEL)
    assert specs == [TargetSpec("ownership", "grp/sub", 3), TargetSpec("repo", "grp/c", 2)]
    salted = Privacy(salt="s")
    (t, _) = resolve(specs, salted.key)
    assert t.unit == salted.key("ownership/grp/sub") and t.unit != "ownership/grp/sub" and t.step == 3


@pytest.mark.parametrize(("text", "message"), [
    ('[[target]]\nunit = "x"\nrow = "docs"\nlevel = 1\n',
     "target 1: 'row', 'level' no longer set a target; a target names a Step: step = 2"),
    ('[[target]]\nunit = "x"\nrow = "docs"\nshare = 0.5\n', "target 1: 'row', 'share' no longer set a target"),
    ('[[target]]\nunit = "x"\nstep = 3\n[[target]]\nunit = "y"\nlevel = 1\n', "target 2: 'level' no longer"),
    ('[[target]]\nunit = "x"\nstep = 1\n', "'step' must be a whole number from 2 to 3"),
    ('[[target]]\nunit = "x"\nstep = 4\n', "'step' must be a whole number from 2 to 3"),
    ('[[target]]\nunit = "x"\nstep = "3"\n', "'step' must be a whole number from 2 to 3"),
    ('[[target]]\nunit = "x"\nstep = true\n', "'step' must be a whole number from 2 to 3"),
    ('[[target]]\nunit = "x"\n', "'step' must be a whole number from 2 to 3"),
    ('[[target]]\nunit = "x"\nstep = 2\nwho = "me"\n', "unknown key(s): who"),
    ('[[target]]\nview = "people"\nunit = "x"\nstep = 2\n', "view must be one of"),
    ('[[target]]\nstep = 2\n', "'unit' must be a non-empty string"),
    ('[[target]]\nunit = "x"\nstep = 2\n[[target]]\nunit = "x"\nstep = 3\n',
     "target 2: a second target for ownership x; a unit has one Step target"),
    ('goal = 1\n', "unknown top-level key"),
    ('target = 1\n', "must be an array of tables"),
    ('target = [1]\n', "target 1: must be a table"),
])
def test_bad_targets_are_named(text: str, message: str) -> None:
    with pytest.raises(TargetsError, match=re.escape(message)):
        parse_targets(tomllib.loads(text), MODEL)


def test_a_model_without_steps_refuses_a_step_target() -> None:
    plain = parse_levels(tomllib.loads(LEVELS[:LEVELS.index("[[steps]]")]))
    for model in (plain, None):
        with pytest.raises(TargetsError, match=re.escape("needs a maturity model with [[steps]]")):
            parse_targets(tomllib.loads('[[target]]\nunit = "x"\nstep = 2\n'), model)


def test_check_units_refuses_a_unit_or_repository_not_in_the_run() -> None:
    trees = build_trees(["grp/sub/a", "grp/c"], empty_map(), {}, {})
    check_units([TargetSpec("ownership", "grp/sub", 2), TargetSpec("repo", "grp/c", 2)], trees, {"grp/sub/a", "grp/c"})
    with pytest.raises(TargetsError, match=re.escape("target 2: unit 'nope' is not a unit of the ownership view")):
        check_units([TargetSpec("ownership", "grp", 2), TargetSpec("ownership", "nope", 2)], trees, set())
    with pytest.raises(TargetsError, match=re.escape("target 1: unit 'grp' is not a unit of the system view")):
        check_units([TargetSpec("system", "grp", 2)], trees, set())
    with pytest.raises(TargetsError, match=re.escape("target 1: repository 'grp/x' is not in this run")):
        check_units([TargetSpec("repo", "grp/x", 2)], trees, {"grp/c"})
