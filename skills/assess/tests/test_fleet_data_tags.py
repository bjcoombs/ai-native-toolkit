"""Tests for the per-tag tally of fleet-data.json (lib/fleet/fleet_data_tags.py, issue #1073)."""
from __future__ import annotations

import json
from typing import Any

import pytest

from fleet_data_fixture import ROWS, SALT, STAMP, VARIANTS, build, model
import lib.fleet.fleet_data as fd
from lib.fleet.fleet_data_tags import member_tags, repo_tags, tag_roll_up
from lib.fleet.fleet_level import fleet_levels
from lib.fleet.fleet_rescore import rescore
from lib.fleet.fleet_units import build_trees, empty_map
from lib.scoring.headline import headline
from lib.scoring.level_result_types import TagCell
from lib.scoring.levels_fields import TRUTH_TAGS


def _cell(tag: str, met: int = 0, measured: int = 0, verified: int = 0, not_measured: int = 0,
          hazards: int = 0, declined: int = 0) -> TagCell:
    return {"tag": tag, "met": met, "measured": measured, "verified": verified,
            "not_measured": not_measured, "hazards": hazards, "declined": declined}


def _units(data: fd.FleetData, view: str) -> dict[str, fd.DataUnit]:
    (tree,) = [v for v in data["views"] if v["view"] == view]
    return {u["key"]: u for u in tree["units"]}


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_each_repository_tally_equals_its_own_headline_tags(name: str) -> None:
    privacy = VARIANTS[name]
    data = build(privacy)
    levels = fleet_levels(ROWS, model(), toolkit_commit=None, plugin_version="0")["repos"]
    by_key = {r["key"]: r for r in data["repos"]}
    for row in ROWS:
        repo = by_key[privacy.key(row["repo"])]
        if row["repo"] not in levels:  # failed: not measured, never a row of zeros
            assert repo["tags"] is None
            continue
        assert repo["tags"] == headline(levels[row["repo"]], model())["tags"]
        assert repo["tags"]


def test_a_condition_with_two_tags_counts_in_each_cell() -> None:
    repos = {r["key"]: r for r in build(fd.Privacy(full=True))["repos"]}
    cells = {c["tag"]: c for c in repos["grp/other/proj3"]["tags"] or []}
    # docs.1 carries agent_context and docs; proj3 has no docs signal, so both are not measured
    assert cells["agent_context"]["not_measured"] == 1
    assert cells["docs"]["not_measured"] == 2  # docs.1 and docs.2
    assert [c["tag"] for c in repos["grp/other/proj3"]["tags"] or []] == [
        t for t in TRUTH_TAGS if t in cells]  # the catalog's tag order


def test_the_unit_roll_up_sums_the_measured_repositories_and_counts_the_failed_in_repos_only() -> None:
    data = build(fd.Privacy(full=True))
    repos = {r["key"]: r for r in data["repos"]}
    org = _units(data, "ownership")["ownership/grp"]["tags"]
    assert (org["measured"], org["repos"]) == (3, 4)  # proj4 failed
    measured = [repos[k]["tags"] for k in ("grp/sub/proj1", "grp/sub/proj2", "grp/other/proj3")]
    assert org == tag_roll_up([*measured, None])


def test_tag_roll_up_adds_every_count_and_keeps_the_tag_order() -> None:
    rolled = tag_roll_up([
        [_cell("docs", 1, 2, 1, 0, 0, 1), _cell("behaviour", 0, 1, hazards=1)],
        [_cell("behaviour", 1, 1, 1), _cell("governance", not_measured=2)],
        None,
    ])
    assert rolled == {"measured": 2, "repos": 3, "cells": [
        _cell("behaviour", 1, 2, 1, 0, 1, 0), _cell("docs", 1, 2, 1, 0, 0, 1),
        _cell("governance", not_measured=2)]}
    assert tag_roll_up([None, None]) == {"measured": 0, "repos": 2, "cells": []}


def test_without_a_model_no_repository_has_a_tally() -> None:
    data = fd.build_fleet_data(STAMP, ROWS, None, build_trees([r["repo"] for r in ROWS], empty_map()))
    assert all(r["tags"] is None for r in data["repos"])
    assert _units(data, "ownership")["ownership/grp"]["tags"] == {"measured": 0, "repos": 4, "cells": []}
    assert repo_tags(None, model()) is None


def test_history_without_the_field_reads_as_not_measured_never_zero() -> None:
    assert member_tags({"key": "k"}) is None
    assert member_tags({"key": "k", "tags": None}) is None
    assert member_tags({"key": "k", "tags": [_cell("docs", 1, 1)]}) == [_cell("docs", 1, 1)]
    stored = [_cell("docs", 1, 1), {"tag": "not-a-tag", "met": 1}]
    assert member_tags({"key": "k", "tags": stored}) is None  # one bad cell: never a partial tally


def test_a_rescore_rebuilds_the_tally_of_an_entry_written_before_it() -> None:
    current = build(fd.Privacy())
    old: Any = json.loads(fd.dumps_fleet_data(current))
    for repo in old["repos"]:
        del repo["tags"]
    for view in old["views"]:
        for unit in view["units"]:
            del unit["tags"]
    again = rescore(old, ROWS, model(), fd.Privacy().key)
    assert again is not None
    assert [r["tags"] for r in again["repos"]] == [r["tags"] for r in current["repos"]]
    assert [u["tags"] for v in again["views"] for u in v["units"]] == [
        u["tags"] for v in current["views"] for u in v["units"]]


@pytest.mark.parametrize("privacy", [fd.Privacy(), fd.Privacy(salt=SALT)])
def test_counts_only_writes_only_fixed_tag_names_and_counts(privacy: fd.Privacy) -> None:
    data = build(privacy)
    cells = [c for r in data["repos"] for c in r["tags"] or []]
    cells += [c for v in data["views"] for u in v["units"] for c in u["tags"]["cells"]]
    assert cells
    for cell in cells:
        assert cell["tag"] in TRUTH_TAGS
        assert all(type(v) is int for k, v in cell.items() if k != "tag")
    text = fd.dumps_fleet_data(data)
    if privacy.salt is not None:
        assert "proj" not in text and "grp" not in text


def test_the_full_and_counts_only_tallies_agree() -> None:
    def tallies(privacy: fd.Privacy) -> list[Any]:
        data = build(privacy)
        return sorted(json.dumps(r["tags"], sort_keys=True) for r in data["repos"])
    assert tallies(fd.Privacy(full=True)) == tallies(fd.Privacy()) == tallies(fd.Privacy(salt=SALT))
