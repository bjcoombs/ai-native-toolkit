"""Tests for the Step fields of fleet-data.json (lib/fleet_data_steps.py, issue #944)."""
from __future__ import annotations

import json
from typing import Any, cast

import pytest

from fleet_data_fixture import ROWS, SALT, STAMP, VARIANTS, build, model
from lib import fleet_data as fd
from lib.counts_only import counts_only_scans
from lib.fleet import FleetRow
from lib.fleet_data_steps import NO_STEPS, RepoSteps, repo_steps, step_hold, step_roll_up
from lib.fleet_level import fleet_levels
from lib.fleet_units import build_trees, empty_map
from lib.level_result_types import Blocker
from lib.levels import StepDef


def _units(data: fd.FleetData, view: str) -> dict[str, fd.DataUnit]:
    (tree,) = [v for v in data["views"] if v["view"] == view]
    return {u["key"]: u for u in tree["units"]}


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_each_repository_step_next_step_and_tally_equal_level_json(name: str) -> None:
    privacy = VARIANTS[name]
    data = build(privacy)
    levels = fleet_levels(ROWS, model(), toolkit_commit=None, plugin_version="0")["repos"]
    by_key = {r["key"]: r for r in data["repos"]}
    for row in ROWS:
        repo = by_key[privacy.key(row["repo"])]
        if row["repo"] not in levels:  # failed: no level.json entry, nothing measured
            assert (repo["step"], repo["next_step"], repo["tally"], repo["exclusions"]) == (None, None, None, [])
            continue
        result = levels[row["repo"]]
        assert (repo["step"], repo["next_step"], repo["tally"]) == (
            result["step"], result["next_step"], result["tally"])


def test_three_level_unit_with_an_unmeasured_repository() -> None:
    units = _units(build(fd.Privacy(full=True)), "ownership")
    org = units["ownership/grp"]["step"]
    # proj4 failed: counted in repos only, never as Step 1
    assert (org["lowest_step"], org["measured"], org["repos"]) == (1, 3, 4)
    assert org["next_step"] == {"step": 2, "threshold": 0, "held": 1, "below_threshold": 0, "gates": [
        {"gate": "docs", "row": "docs", "level": 1, "held": 1, "not_measured": 1, "excluded": 0}]}
    sub = units["ownership/grp/sub"]["step"]
    assert (sub["lowest_step"], sub["measured"], sub["repos"]) == (2, 2, 2)
    assert sub["next_step"] is not None
    assert [(g["gate"], g["held"], g["excluded"]) for g in sub["next_step"]["gates"]] == [
        ("docs", 0, 0), ("ci", 2, 1)]  # every gate of the Step, zero counts kept
    checkout = _units(build(fd.Privacy(full=True)), "system")["system/checkout"]["step"]
    assert (checkout["lowest_step"], checkout["measured"], checkout["repos"]) == (2, 1, 2)


def test_an_excluded_gate_condition_is_listed_with_its_reason_and_keeps_the_step_unreached() -> None:
    repos = {r["key"]: r for r in build(fd.Privacy(full=True))["repos"]}
    proj1 = repos["grp/sub/proj1"]
    assert proj1["exclusions"] == [{"id": "ci.1", "kind": "measured_elsewhere", "source": "repo",
                                    "reason": "CI runs in the release pipeline repository."}]
    # ci.1 is met by its own criterion; excluded, it holds no gate, so Step 3 stays unreached
    assert proj1["step"] == 2 and proj1["next_step"] is not None
    assert proj1["next_step"]["blockers"] == [{"kind": "gate", "gate": "ci", "condition": "ci.1",
                                               "state": "not_applicable", "disposition": "excluded_by_owner",
                                               "exclusion_kind": "measured_elsewhere"}]
    assert proj1["tally"] is not None and proj1["tally"]["met"] == 0  # listed, never counted


@pytest.mark.parametrize("privacy", [fd.Privacy(), fd.Privacy(salt=SALT)])
def test_counts_only_keeps_the_exclusion_but_never_the_owners_reason(privacy: fd.Privacy) -> None:
    text = fd.dumps_fleet_data(build(privacy))
    assert "release pipeline" not in text and "maintainer" not in text
    (proj1,) = [r for r in json.loads(text)["repos"] if r["exclusions"]]
    assert proj1["exclusions"] == [{"id": "ci.1", "kind": "measured_elsewhere", "source": "repo", "reason": None}]


def _reduced(scans: dict[str, Any]) -> dict[str, Any]:
    """The owner_exclusions block as a hashed counts-only scan writes it (the fixture's own
    blocks are not real collectors, so they are left as they are)."""
    block = {"owner_exclusions": scans["owner_exclusions"]} if "owner_exclusions" in scans else {}
    return {**scans, **counts_only_scans(block, hash_paths=True, salt=SALT)}


def test_a_counts_only_scan_gives_the_same_steps_as_the_full_one() -> None:
    reduced: list[FleetRow] = [{**row, "scans": _reduced(row["scans"])} if row["scans"] else row for row in ROWS]
    full = fd.step_fields(model(), ROWS)
    again = fd.step_fields(model(), reduced)
    for repo, fields in full.items():
        assert {k: v for k, v in again[repo].items() if k != "exclusions"} == {
            k: v for k, v in fields.items() if k != "exclusions"}
        assert [e["id"] for e in again[repo]["exclusions"]] == [e["id"] for e in fields["exclusions"]]
    assert all(e["reason"] is None for f in again.values() for e in f["exclusions"])


def test_two_runs_give_byte_identical_output() -> None:
    for privacy in VARIANTS.values():
        assert fd.dumps_fleet_data(build(privacy)) == fd.dumps_fleet_data(build(privacy))


def test_without_a_model_no_repository_is_measured() -> None:
    trees = build_trees([r["repo"] for r in ROWS], empty_map())
    data = fd.build_fleet_data(STAMP, ROWS, None, trees)
    assert all((r["step"], r["next_step"], r["tally"], r["exclusions"]) == (None, None, None, [])
               for r in data["repos"])
    org = _units(data, "ownership")["ownership/grp"]["step"]
    assert org == {"measured": 0, "repos": 4, "lowest_step": None, "next_step": None}


STEPS: list[StepDef] = [
    {"step": 1, "name": "One", "threshold": 0, "gates": []},
    {"step": 2, "name": "Two", "threshold": 2, "gates": [{"id": "g", "row": "r", "level": 1}]},
]


def _at(step: int, met: int = 2, *gates: Blocker) -> RepoSteps:
    blockers: list[Blocker] = [*gates, *([{"kind": "threshold", "threshold": 2, "met": met}] if met < 2 else [])]
    return {"step": step, "tally": {"met": met, "measured": 0, "not_measured": 0}, "exclusions": [],
            "next_step": {"step": step + 1, "threshold": 2, "blockers": blockers} if step < 2 else None,
            "gate_blockers": list(gates)}


def test_the_roll_up_counts_the_threshold_and_ignores_repositories_already_past() -> None:
    gate: Blocker = {"kind": "gate", "gate": "g", "condition": "r.1", "state": "unmet"}
    rolled = step_roll_up(STEPS, [_at(1, 1), _at(1, 1, gate), _at(1, 2, gate), _at(2), NO_STEPS])
    assert rolled == {"measured": 4, "repos": 5, "lowest_step": 1, "next_step": {
        "step": 2, "threshold": 2, "held": 3, "below_threshold": 2,
        "gates": [{"gate": "g", "row": "r", "level": 1, "held": 2, "not_measured": 0, "excluded": 0}]}}


def test_the_hold_of_the_next_step_agrees_with_each_repositorys_own_next_step() -> None:
    """#945: ``step_hold`` reads ``gate_blockers`` and the tally; for the Step above a repository's
    own, that is exactly its ``next_step`` blockers."""
    for repo in build(fd.Privacy(full=True))["repos"]:
        nxt = repo["next_step"]
        if nxt is None:
            continue
        (definition,) = [s for s in model()["steps"] if s["step"] == nxt["step"]]
        hold = step_hold(definition, [cast(RepoSteps, repo)])
        assert {g["gate"] for g in hold["gates"] if g["held"]} == {b["gate"] for b in nxt["blockers"]
                                                                   if b["kind"] == "gate"}
        assert hold["below_threshold"] == sum(b["kind"] == "threshold" for b in nxt["blockers"])


def test_the_roll_up_at_the_top_step_or_with_none_measured_has_no_next_step() -> None:
    assert step_roll_up(STEPS, [_at(2), _at(2)])["next_step"] is None
    assert step_roll_up(STEPS, [NO_STEPS]) == {"measured": 0, "repos": 1, "lowest_step": None, "next_step": None}
    assert step_roll_up(STEPS, []) == {"measured": 0, "repos": 0, "lowest_step": None, "next_step": None}


def test_repo_steps_reads_the_first_reason_per_id_and_tolerates_a_degraded_block() -> None:
    assert repo_steps(None, None) == NO_STEPS
    result = fd.step_fields(model(), ROWS[:1])["grp/sub/proj1"]
    assert result["exclusions"][0]["reason"] == "CI runs in the release pipeline repository."
    level = fleet_levels(ROWS[:1], model(), toolkit_commit=None, plugin_version="0")["repos"]["grp/sub/proj1"]
    degraded = repo_steps(level, {"owner_exclusions": {"available": False}})
    assert degraded["exclusions"] == [{"id": "ci.1", "kind": "measured_elsewhere", "source": "repo", "reason": None}]
    twice = {"owner_exclusions": {"exclusions": [{"id": "ci.1", "reason": "first"}, {"id": "ci.1", "reason": "x"},
                                                 "not a table", {"id": 3, "reason": "y"}]}}
    assert repo_steps(level, twice)["exclusions"][0]["reason"] == "first"
