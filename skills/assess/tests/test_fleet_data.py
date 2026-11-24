"""Tests for fleet-data.json, lib/fleet/fleet_data.py and its schema (issue #703)."""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from fleet_data_fixture import FIXTURES, ROWS, SALT, STAMP, VARIANTS, WEB_URLS, build, model
import lib.fleet.fleet_data as fd
from lib.counts_only.counts_only_rules import FLEET_DATA_KEPT, FLEET_DATA_RULES, classify_fleet_data
from lib.fleet.fleet_data_schema import SCHEMA_PATH, render
from lib.fleet.fleet_units import build_trees, empty_map
from lib.levels import parse_levels


def _units(data: fd.FleetData, view: str) -> dict[str, fd.DataUnit]:
    (tree,) = [v for v in data["views"] if v["view"] == view]
    return {u["key"]: u for u in tree["units"]}


def _level(unit: fd.DataUnit, row: str) -> fd.RowRollUp:
    (rolled,) = [lv for lv in unit["levels"] if lv["row"] == row]
    return rolled


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_golden_files_match_a_fresh_build(name: str) -> None:
    assert (FIXTURES / name).read_text(encoding="utf-8") == fd.dumps_fleet_data(build(VARIANTS[name]))


def test_schema_file_matches_the_typed_dicts_and_validates_every_variant() -> None:
    assert SCHEMA_PATH.read_text(encoding="utf-8") == render()
    schema = json.loads(render())
    for name in VARIANTS:
        jsonschema.validate(json.loads((FIXTURES / name).read_text(encoding="utf-8")), schema)


def test_three_level_gitlab_tree_and_roll_ups() -> None:
    data = build(fd.Privacy(full=True))
    units = _units(data, "ownership")
    assert {k: (u["kind"], u["parent"], u["repos"]) for k, u in units.items()} == {
        "ownership/grp": ("org", None, 4),
        "ownership/grp/other": ("team", "ownership/grp", 1),
        "ownership/grp/sub": ("team", "ownership/grp", 2),
    }
    org = units["ownership/grp"]
    # docs: proj1 level 2 of 2, proj2 level 1 of 2; proj3 unknown and proj4 failed are excluded, not 0
    assert _level(org, "docs") == {"row": "docs", "measured": 2, "repos": 4, "lowest_level": 1}
    assert (_level(org, "ci")["measured"], _level(org, "ci")["lowest_level"]) == (3, 0)
    assert "overall" not in org
    other = units["ownership/grp/other"]
    assert _level(other, "docs") == {"row": "docs", "measured": 0, "repos": 1, "lowest_level": None}
    assert [m["id"] for m in data["model"]] == ["org2fa", "docs", "ci"]
    assert all(lv["row"] != "org2fa" for u in units.values() for lv in u["levels"])


def test_each_repo_is_counted_once_per_view() -> None:
    data = build(fd.Privacy(full=True))
    repos = {r["key"]: r for r in data["repos"]}
    assert repos["grp/proj4"]["units"] == [{"view": "ownership", "unit": "ownership/grp"},
                                           {"view": "system", "unit": "system/checkout"}]
    for view in ("ownership", "system"):
        roots = [u for u in _units(data, view).values() if u["parent"] is None]
        placed = sum(1 for r in data["repos"] for unit in r["units"] if unit["view"] == view)
        assert sum(u["repos"] for u in roots) == placed
    checkout = _units(data, "system")["system/checkout"]
    assert (checkout["repos"], checkout["name"], checkout["owners"]) == (2, "Checkout", ["@grp/checkout-leads"])
    assert _level(checkout, "docs")["measured"] == 1  # proj4 failed: counted, not measured


def test_repo_cells_explain_the_level() -> None:
    repos = {r["key"]: r for r in build(fd.Privacy(full=True))["repos"]}
    proj2 = {c["row"]: c for c in repos["grp/sub/proj2"]["rows"]}
    assert proj2["docs"] == {"row": "docs", "status": "level", "level": 1, "top_level": 2,
                             "signal": "docs.grade", "value": "C", "reason": None, "why": None}
    assert proj2["ci"]["value"] is False and proj2["ci"]["signal"] == "ci.required"
    proj1 = {c["row"]: c for c in repos["grp/sub/proj1"]["rows"]}
    assert proj1["docs"]["signal"] is None and proj1["docs"]["level"] == 2  # top level: nothing holds it back
    proj3 = {c["row"]: c for c in repos["grp/other/proj3"]["rows"]}
    assert (proj3["docs"]["status"], proj3["docs"]["signal"], proj3["docs"]["reason"]) == (
        "unknown", "docs.lines", "unavailable")
    proj4 = repos["grp/proj4"]
    assert (proj4["status"], proj4["reason"]) == ("failed", "scan_timeout")
    assert {c["reason"] for c in proj4["rows"]} == {"not_scanned"}
    assert repos["grp/proj4"]["web_url"] == "https://gitlab.example.com/grp/proj4"
    assert repos["grp/sub/proj2"]["owners"] == ["@grp/sub/maintainers", "bob@example.com"]


def test_counts_only_drops_names_and_owners_and_reduces_values() -> None:
    data = build(fd.Privacy())
    repos = {r["key"]: r for r in data["repos"]}
    assert repos["grp/sub/proj2"]["owners"] is None and repos["grp/sub/proj2"]["owner_count"] == 2
    assert repos["grp/proj4"]["web_url"] == "https://gitlab.example.com/grp/proj4"  # an id, kept unhashed
    assert all(u["name"] is None and u["owners"] is None
               for v in data["views"] for u in v["units"])
    assert fd.Privacy().value("a long sentence about the repo") == "set"
    assert fd.Privacy().value("C") == "C" and fd.Privacy().value(3) == 3
    assert fd.Privacy(full=True).value("a sentence") == "a sentence"


def test_a_hashed_run_holds_no_names_urls_or_paths() -> None:
    text = fd.dumps_fleet_data(build(fd.Privacy(salt=SALT)))
    names = {"grp", "sub", "other", "proj1", "proj2", "proj3", "proj4", "checkout", "Checkout",
             "gitlab", "example.com", "https://", "@grp", "bob", "maintainers"}
    for name in names:
        assert name not in text, name
    data = json.loads(text)
    keys = [r["key"] for r in data["repos"]] + [u["key"] for v in data["views"] for u in v["units"]]
    assert all(k.startswith("h:") and len(k) == 18 for k in keys)
    unit_keys = {u["key"] for v in data["views"] for u in v["units"]}
    assert {u["unit"] for r in data["repos"] for u in r["units"]} <= unit_keys  # references still join
    assert {u["parent"] for v in data["views"] for u in v["units"]} - {None} <= unit_keys


def test_the_same_salt_gives_the_same_bytes_and_another_salt_does_not() -> None:
    first = fd.dumps_fleet_data(build(fd.Privacy(salt=SALT)))
    assert first == fd.dumps_fleet_data(build(fd.Privacy(salt=SALT)))
    assert first != fd.dumps_fleet_data(build(fd.Privacy(salt="other")))


def test_run_details_copy_every_stamp_field_but_the_documented_exclusions() -> None:
    from typing import get_type_hints

    from lib.fleet.fleet_stamp import FleetStamp
    stamp_keys = set(get_type_hints(FleetStamp))
    assert fd.RUN_EXCLUDED <= stamp_keys
    assert set(get_type_hints(fd.RunDetails)) == stamp_keys - fd.RUN_EXCLUDED
    assert set(fd.run_details(STAMP)) == stamp_keys - fd.RUN_EXCLUDED
    older = {k: v for k, v in STAMP.items()
             if k not in ("platform", "fleet_config_sha256", "managed_settings")}
    run = fd.run_details(older)  # type: ignore[arg-type]
    assert (run["platform"], run["fleet_config_sha256"], run["managed_settings"]) == (False, None, False)


def test_privacy_for_full_ignores_any_salt() -> None:
    assert fd.privacy_for(True, "s") == fd.Privacy(full=True, salt=None)
    assert fd.privacy_for(False, "s") == fd.Privacy(full=False, salt="s")


def _string_fields(tp: object, prefix: str = "") -> set[str]:
    """Dotted patterns of every field of ``tp`` (a TypedDict) that can hold a string."""
    import types
    from typing import Literal, Union, get_args, get_origin, get_type_hints, is_typeddict
    found: set[str] = set()
    for name, hint in get_type_hints(tp).items():
        pattern = f"{prefix}{name}"
        stack = [hint]
        while stack:
            current = stack.pop()
            if current is str or get_origin(current) is Literal:  # a Literal token is a string too
                found.add(pattern)
            elif is_typeddict(current):
                found |= _string_fields(current, pattern + ".")
            elif get_origin(current) in (list, Union, types.UnionType):
                stack.extend(get_args(current))
    return found


def test_every_string_field_has_a_rule_or_is_kept_on_purpose() -> None:
    fields = _string_fields(fd.FleetData)
    assert fields - set(FLEET_DATA_RULES) - FLEET_DATA_KEPT == set()
    assert set(FLEET_DATA_RULES) - fields == set()  # no stale rule
    assert not FLEET_DATA_KEPT & set(FLEET_DATA_RULES)


def test_every_string_field_is_classified() -> None:
    assert classify_fleet_data("views.units.name") == "drop"
    assert classify_fleet_data("not.a.field") == "drop"
    assert fd.Privacy().text("repos.reason", "a sentence with spaces") is None
    assert fd.Privacy().text("repos.status", "ok") == "ok"
    assert fd.Privacy(salt=SALT).text("repos.web_url", "https://x/y") is None
    assert fd.Privacy().text("repos.status", None) is None
    assert set(FLEET_DATA_RULES.values()) <= {"path", "token", "id", "drop", "hashed_path"}


def test_without_a_maturity_model_units_still_roll_up_counts() -> None:
    trees = build_trees([r["repo"] for r in ROWS], empty_map())
    data = fd.build_fleet_data(STAMP, ROWS, None, trees, web_urls=WEB_URLS)
    assert data["model"] == [] and all(r["rows"] == [] for r in data["repos"])
    units = _units(data, "ownership")  # unhashed counts-only: the key is the view and id
    assert (units["ownership/grp"]["repos"], units["ownership/grp"]["levels"]) == (4, [])


def _keys(node: object) -> set[str]:
    if isinstance(node, dict):
        return set(node) | {k for v in node.values() for k in _keys(v)}
    if isinstance(node, list):
        return {k for v in node for k in _keys(v)}
    return set()


@pytest.mark.parametrize("privacy", [fd.Privacy(full=True), fd.Privacy(), fd.Privacy(salt="s")])
def test_the_roll_up_names_no_average_and_no_weakest_repository(privacy: fd.Privacy) -> None:
    """#772 option A: no mean share and no repository singled out at any depth; every unit's
    row roll-up says how many of its repositories were measured, and the lowest level."""
    data = build(privacy)
    assert not _keys(data) & {"mean_share", "weakest", "overall", "share"}
    rolls = [lv for v in data["views"] for u in v["units"] for lv in u["levels"]]
    assert rolls and all(set(lv) == {"row", "measured", "repos", "lowest_level"} for lv in rolls)
    assert all(lv["measured"] <= lv["repos"] for lv in rolls)
    assert all((lv["lowest_level"] is None) == (lv["measured"] == 0) for lv in rolls)


def test_a_type_mismatch_reads_as_unknown_with_its_reason() -> None:
    levels = parse_levels({"version": "t", "rows": [{
        "id": "r", "title": "R", "scope": "team", "aggregation": "min",
        "levels": [{"level": 1, "criteria": [{"signal": "b.n", "op": "gt", "value": 0}]}]}]})
    row = dict(ROWS[0])
    row["scans"] = {"b": {"available": True, "n": "many"}}
    (cell,) = fd.repo_cells(levels["rows"], row)  # type: ignore[arg-type]
    assert (cell["status"], cell["reason"], cell["signal"]) == ("unknown", "type_mismatch", "b.n")
    assert model()["version"] == "fleet-data-1"


def test_an_unknown_cell_with_a_confirmed_level_carries_its_floor() -> None:
    """#889: level 1 holds and level 2 cannot be read, so the cell is unknown with floor 1."""
    levels = parse_levels({"version": "t", "rows": [{
        "id": "r", "title": "R", "scope": "team", "aggregation": "min",
        "levels": [{"level": 1, "criteria": [{"signal": "b.n", "op": "gt", "value": 0}]},
                   {"level": 2, "criteria": [{"signal": "b.m", "op": "gt", "value": 0}]}]}]})
    row = dict(ROWS[0])
    row["scans"] = {"b": {"available": True, "n": 3}}
    (cell,) = fd.repo_cells(levels["rows"], row)  # type: ignore[arg-type]
    assert (cell["status"], cell.get("floor")) == ("unknown", 1)
    row["scans"] = {"b": {"available": True, "n": 3, "m": 1}}
    (known,) = fd.repo_cells(levels["rows"], row)  # type: ignore[arg-type]
    assert (known["status"], known["level"], "floor" in known) == ("level", 2, False)
    row["scans"] = {"b": {"available": True, "n": "many"}}
    (none,) = fd.repo_cells(levels["rows"], row)  # type: ignore[arg-type]
    assert (none["status"], "floor" in none) == ("unknown", False)


def test_write_fleet_data_replaces_the_file(tmp_path: Path) -> None:
    data = build(fd.Privacy())
    path = fd.write_fleet_data(tmp_path / "out", data)
    assert path.name == fd.FLEET_DATA_FILE
    assert path.read_text(encoding="utf-8") == fd.dumps_fleet_data(data)
    assert sorted(p.name for p in path.parent.iterdir()) == [fd.FLEET_DATA_FILE]
