"""Tests for the fleet group roll-up, lib/fleet/fleet_groups.py (issue #568)."""
from __future__ import annotations

import json
import tomllib

import jsonschema
import pytest

import lib.fleet.fleet_groups as fg
from lib.fleet.runner import FLEET_ROW_VERSION, FleetRow
from lib.fleet.fleet_group_schema import SCHEMA_PATH, build_schema, render, type_schema
from lib.levels import parse_levels
from lib.level_signals import DEFAULT_LEVELS, load_default_levels
from test_fleet_cli import fleet_cli, froot  # noqa: F401 - froot is a fixture

LEVELS_TOML = """\
version = "fixture-1"

[[rows]]
id = "org2fa"
title = "Two-factor"
scope = "org"

  [[rows.levels]]
  level = 1
  criteria = [{ signal = "two_factor.value", op = "eq", value = "on" }]

[[rows]]
id = "teamdocs"
title = "Team docs"
scope = "team"
aggregation = "min"

  [[rows.levels]]
  level = 1
  criteria = [{ signal = "nested_instructions.budget.claude_code.lines", op = "gt", value = 0 }]

  [[rows.levels]]
  level = 2
  criteria = [{ signal = "shared_tooling.available", op = "eq", value = true }]
"""


def _fragment(scope: str, two_factor: str, reason: str | None = None) -> dict[str, object]:
    return {"scope": scope, "provider": "gitlab",
            "two_factor": {"value": two_factor, "reason": reason},
            "push_rules": {"value": "deny_delete_tag, member_check", "reason": None},
            "default_branch_protection": {"value": "full", "reason": None},
            "rulesets": {"value": "unknown", "reason": "endpoint missing: GitLab has no group rulesets"}}


def _row(repo: str, scans: dict[str, object] | None) -> FleetRow:  # scans: test data
    return {"fleet_row_version": FLEET_ROW_VERSION, "repo": repo, "group": repo.rsplit("/", 1)[0],
            "status": "ok" if scans is not None else "failed",
            "reason": None if scans is not None else "not_a_git_work_tree",
            "signals_schema_version": 1 if scans is not None else None, "scans": scans,
            "active": True if scans is not None else None}


def _scans(lines: int, markets: list[str], ci: list[str] | None = None) -> dict[str, object]:
    return {"nested_instructions": {"budget": {"claude_code": {"lines": lines}}},
            "shared_tooling": {"available": True, "marketplace_ids": markets, "ci_source_ids": ci or []}}


LEVELS = parse_levels(tomllib.loads(LEVELS_TOML))


# --- settings ----------------------------------------------------------------


def test_load_settings_from_a_file_an_array_and_a_render_dir(tmp_path):
    one = tmp_path / "one.json"
    one.write_text(json.dumps(_fragment("grpa", "on")), encoding="utf-8")
    assert list(fg.load_settings(one)) == ["grpa"]

    many = tmp_path / "many.json"
    many.write_text(json.dumps([_fragment("grpa", "on"), _fragment("grpb", "off")]), encoding="utf-8")
    assert sorted(fg.load_settings(many)) == ["grpa", "grpb"]

    render_dir = tmp_path / "render"
    (render_dir / "settings").mkdir(parents=True)
    (render_dir / "repo.json").write_text("{}", encoding="utf-8")  # a per-repo fragment: ignored
    (render_dir / "settings" / "0.json").write_text(json.dumps(_fragment("grpb", "off")), encoding="utf-8")
    assert list(fg.load_settings(render_dir)) == ["grpb"]
    assert list(fg.load_settings(render_dir / "settings")) == ["grpb"]


@pytest.mark.parametrize(("content", "message"), [
    (None, "not found"),
    ("{not json", "unreadable"),
    (json.dumps({"two_factor": {}}), "no 'scope'"),
    (json.dumps([_fragment("g", "on"), _fragment("g", "off")]), "given twice"),
])
def test_load_settings_errors(tmp_path, content, message):
    path = tmp_path / "s.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    with pytest.raises(fg.SettingsError, match=message):
        fg.load_settings(path)


def test_unknown_settings_become_unavailable_and_counts_only_reduces_text():
    settings = fg.normalise_settings({**_fragment("g", "unknown", "denied: no access (HTTP 403)"),
                                      "malformed": "on"})
    assert settings == {
        "default_branch_protection": {"value": "full", "reason": None},
        "malformed": {"available": False, "reason": "unknown"},
        "push_rules": {"value": "deny_delete_tag, member_check", "reason": None},
        "rulesets": {"available": False, "reason": "endpoint missing: GitLab has no group rulesets"},
        "two_factor": {"available": False, "reason": "denied: no access (HTTP 403)"},
    }
    assert fg.reduce_settings(settings) == {
        "default_branch_protection": {"value": "full", "reason": None},
        "malformed": {"available": False, "reason": "unknown"},
        "push_rules": {"value": "set", "reason": None},
        "rulesets": {"available": False, "reason": "unknown"},
        "two_factor": {"available": False, "reason": "unknown"},
    }
    assert fg.reduce_settings({"limit": {"value": 5, "reason": None}}) == {"limit": {"value": 5, "reason": None}}


def test_settings_join_exact_then_nearest_ancestor():
    by_scope = {"top": _fragment("top", "on"), "top/sub": _fragment("top/sub", "off")}
    assert fg.settings_for("top/sub", by_scope)[0] == "top/sub"
    assert fg.settings_for("top/other/deep", by_scope)[0] == "top"
    assert fg.settings_for("elsewhere", by_scope) == (None, None)


# --- roll-up -------------------------------------------------------------------


def test_group_rows_carry_levels_rules_settings_and_distinct_ids():
    rows = [
        _row("grpa/svc1", _scans(20, ["marketplace:mkt-a"], ["gitlab_project:grp/ci"])),
        _row("grpa/svc2", _scans(3, ["marketplace:mkt-b", "marketplace:mkt-a"], ["gitlab_project:grp/ci"])),
        _row("grpb/lib1", _scans(0, ["marketplace:mkt-a"])),
    ]
    by_scope = {"grpa": _fragment("grpa", "on"), "grpb": _fragment("grpb", "off")}
    groups = fg.roll_up(reversed(rows), LEVELS, by_scope)

    assert [g["group"] for g in groups] == ["grpa", "grpb"]
    grpa, grpb = groups
    assert (grpa["repos"], grpa["scanned"], grpa["failed"]) == (2, 2, 0)
    assert (grpa["distinct_marketplaces"], grpa["distinct_ci_sources"]) == (2, 1)
    assert (grpb["distinct_marketplaces"], grpb["distinct_ci_sources"]) == (1, 0)
    assert grpa["settings_scope"] == "grpa" and grpa["settings"]["push_rules"] == {"value": "set", "reason": None}
    levels = {r["id"]: r for r in grpa["levels"]}
    assert levels["org2fa"]["level"] == 1 and levels["org2fa"]["aggregation"] is None
    assert levels["teamdocs"]["level"] == 2
    assert levels["teamdocs"]["aggregation"] == {"rule": "min", "share": 1}
    assert {r["id"]: r["level"] for r in grpb["levels"]} == {"org2fa": 0, "teamdocs": 0}
    assert grpa["levels_version"] == "fixture-1"
    tops = {row["id"]: len(row["levels"]) for row in LEVELS["rows"]}
    assert {r["id"]: r["top_level"] for r in grpa["levels"]} == tops  # #684: a share needs no model file
    for group in groups:
        jsonschema.validate(group, build_schema())


def test_a_group_row_written_before_top_level_still_validates_and_loads(tmp_path):
    from lib.fleet.fleet_view import load_groups
    rows = [_row("grpa/svc1", _scans(20, []))]
    (group,) = fg.roll_up(rows, LEVELS, {"grpa": _fragment("grpa", "on")})
    legacy = {**group, "levels": [{k: v for k, v in r.items() if k != "top_level"} for r in group["levels"]]}
    jsonschema.validate(legacy, build_schema())  # top_level is additive, not required
    (tmp_path / "groups.jsonl").write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    assert "top_level" not in load_groups(tmp_path)[0]["levels"][0]


def test_unknown_setting_and_failed_repo_give_unknown_never_zero():
    rows = [_row("grpa/svc1", _scans(20, [])), _row("grpa/broken", None)]
    groups = fg.roll_up(rows, LEVELS, {"grpa": _fragment("grpa", "unknown", "denied (HTTP 403)")})
    (grpa,) = groups
    assert (grpa["repos"], grpa["scanned"], grpa["failed"]) == (2, 1, 1)
    levels = {r["id"]: r for r in grpa["levels"]}
    assert levels["org2fa"]["status"] == "unknown" and levels["org2fa"]["level"] is None
    assert levels["org2fa"]["missing_signals"] == ["two_factor.value"]
    assert levels["teamdocs"]["status"] == "unknown"
    assert levels["teamdocs"]["unknown_repos"] == ["grpa/broken"]
    jsonschema.validate(grpa, build_schema())


def test_no_shared_tooling_evidence_gives_null_counts_not_zero():
    degraded = {"shared_tooling": {"available": False, "reason": "no_git"}}
    rows = [_row("g/a", None), _row("g/b", degraded), _row("g/c", {"other": {}})]
    (group,) = fg.roll_up(rows, None, {})
    assert group["shared_tooling_repos"] == 0
    assert group["distinct_ci_sources"] is None and group["distinct_marketplaces"] is None
    jsonschema.validate(group, build_schema())
    (some,) = fg.roll_up([*rows, _row("g/d", _scans(1, []))], None, {})
    assert (some["shared_tooling_repos"], some["distinct_marketplaces"]) == (1, 0)


def test_no_levels_no_settings_and_full_output():
    rows = [_row("g/r", _scans(1, ["marketplace:x"]))]
    (bare,) = fg.roll_up(rows, None, {})
    assert bare["levels"] == [] and bare["levels_version"] is None
    assert bare["settings"] is None and bare["settings_scope"] is None
    (full,) = fg.roll_up(rows, None, {"g": _fragment("g", "on")}, counts_only=False)
    assert full["settings"]["push_rules"] == {"value": "deny_delete_tag, member_check", "reason": None}
    jsonschema.validate(full, build_schema())


def test_repo_signals_is_the_scans_map():
    assert fg.repo_signals(_row("g/r", {"a": {"n": 1}})) == {"a": {"n": 1}}
    assert fg.repo_signals(_row("g/r", None)) == {}


def test_org_rows_also_read_scope_and_provider():
    maturity_model = parse_levels(tomllib.loads("""\
version = "p"
[[rows]]
id = "gl"
title = "On GitLab"
scope = "org"
  [[rows.levels]]
  level = 1
  criteria = [{ signal = "provider", op = "eq", value = "gitlab" }, { signal = "scope", op = "eq", value = "top" }]
"""))
    (row,) = fg.roll_up([_row("top/sub/r", _scans(1, []))], maturity_model, {"top": _fragment("top", "off")})
    assert row["settings_scope"] == "top" and row["levels"][0]["level"] == 1
    assert fg.settings_signals(None, None) is None


def test_write_groups_is_sorted_compact_jsonl(tmp_path, monkeypatch):
    groups = fg.roll_up([_row("g/r", _scans(1, []))], None, {})
    path = fg.write_groups(tmp_path / "out", groups)
    assert path.read_text(encoding="utf-8") == json.dumps(groups[0], sort_keys=True, separators=(",", ":")) + "\n"

    def fail(*_args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(fg.os, "replace", fail)
    with pytest.raises(OSError):
        fg.write_groups(tmp_path / "out2", groups)
    assert list((tmp_path / "out2").iterdir()) == []


def test_schema_file_matches_the_type():
    assert SCHEMA_PATH.read_text(encoding="utf-8") == render(), (
        "regenerate: cd skills/assess && uv run --locked python scripts/lib/fleet/fleet_group_schema.py")


def test_schema_mapper_rejects_an_unknown_type():
    with pytest.raises(TypeError):
        type_schema(set[str])


# --- end to end ------------------------------------------------------------------


def test_fleet_run_writes_group_rows_byte_identically(froot, tmp_path):  # noqa: F811
    maturity_model = tmp_path / "rub.toml"
    maturity_model.write_text(LEVELS_TOML, encoding="utf-8")
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps([_fragment("grpa", "on"), _fragment("grpb", "off")]), encoding="utf-8")
    argv = [str(froot), "--levels", str(maturity_model), "--settings", str(settings)]

    assert fleet_cli.main([*argv, "--out", str(tmp_path / "o1")]) == 0
    assert fleet_cli.main([*argv, "--out", str(tmp_path / "o2")]) == 0
    first = (tmp_path / "o1" / fg.GROUPS_FILE).read_bytes()
    assert first == (tmp_path / "o2" / fg.GROUPS_FILE).read_bytes()

    groups = {g["group"]: g for g in map(json.loads, first.decode().splitlines())}
    assert sorted(groups) == ["grpa", "grpb"]
    assert groups["grpa"]["distinct_marketplaces"] == 2
    assert groups["grpb"]["distinct_marketplaces"] == 1  # lib1's settings entry names mkt-a
    assert {r["id"]: r["level"] for r in groups["grpa"]["levels"]} == {"org2fa": 1, "teamdocs": 0}
    assert {r["id"]: r["level"] for r in groups["grpb"]["levels"]} == {"org2fa": 0, "teamdocs": 0}
    assert groups["grpa"]["settings"]["two_factor"] == {"value": "on", "reason": None}
    for group in groups.values():
        jsonschema.validate(group, build_schema())


def test_bad_settings_exit_2(froot, tmp_path, capsys):  # noqa: F811
    assert fleet_cli.main([str(froot), "--settings", str(tmp_path / "absent.json"),
                           "--out", str(tmp_path / "o")]) == 2
    assert "settings" in capsys.readouterr().err
    assert not (tmp_path / "o").exists()


@pytest.mark.parametrize("layout", ["empty", "no_json", "empty_render_dir"])
def test_a_settings_dir_with_no_fragment_files_is_an_error(tmp_path, layout):
    folder = tmp_path / "settings_in"
    folder.mkdir()
    if layout == "no_json":
        (folder / "notes.txt").write_text("x", encoding="utf-8")
    if layout == "empty_render_dir":
        (folder / "settings").mkdir()
    with pytest.raises(fg.SettingsError, match="no fragment files"):
        fg.load_settings(folder)


def test_empty_settings_dir_exits_2(froot, tmp_path, capsys):  # noqa: F811
    (tmp_path / "empty").mkdir()
    assert fleet_cli.main([str(froot), "--settings", str(tmp_path / "empty"),
                           "--out", str(tmp_path / "o")]) == 2
    assert "no fragment files" in capsys.readouterr().err
    assert not (tmp_path / "o").exists()


def test_levels_path_outside_the_namespace_exits_2(froot, tmp_path, capsys):  # noqa: F811
    maturity_model = tmp_path / "bad.toml"
    maturity_model.write_text(LEVELS_TOML.replace("shared_tooling.available", "scans.shared_tooling.available"),
                      encoding="utf-8")
    assert fleet_cli.main([str(froot), "--levels", str(maturity_model), "--out", str(tmp_path / "o")]) == 2
    assert "scans.shared_tooling.available" in capsys.readouterr().err


def test_unknown_org_path_exits_2_naming_it(froot, tmp_path, capsys):  # noqa: F811
    maturity_model = tmp_path / "bad.toml"
    maturity_model.write_text(LEVELS_TOML.replace("two_factor.value", "two_factor_required"), encoding="utf-8")
    assert fleet_cli.main([str(froot), "--levels", str(maturity_model), "--out", str(tmp_path / "o")]) == 2
    assert "two_factor_required" in capsys.readouterr().err
    assert not (tmp_path / "o").exists()


def test_default_levels_loads_and_rolls_up(froot, tmp_path):  # noqa: F811
    """The shipped maturity model (#564) passes the same validation and yields a level or unknown per row."""
    assert fleet_cli.main([str(froot), "--levels", str(DEFAULT_LEVELS), "--out", str(tmp_path / "o"),
                           "--jobs", "2"]) == 0
    rows = [json.loads(line) for line in (tmp_path / "o" / fg.GROUPS_FILE).read_text(encoding="utf-8").splitlines()]
    default = load_default_levels()
    for row in rows:
        assert row["levels_version"] == default["version"]
        assert [r["id"] for r in row["levels"]] == [r["id"] for r in default["rows"]]
        assert all(r["status"] in ("level", "unknown") for r in row["levels"])
        jsonschema.validate(row, build_schema())


def test_additive_repo_row_fields_are_tolerated():
    row = {**_row("g/r", _scans(1, ["marketplace:x"])), "active": True}
    (group,) = fg.roll_up([row], LEVELS, {})  # type: ignore[list-item]
    assert group["distinct_marketplaces"] == 1


def test_union_of_groups_counts_distinct_marketplaces(froot, tmp_path):  # noqa: F811
    """grpa plus grpb reference two distinct marketplaces between them."""
    assert fleet_cli.main([str(froot), "--out", str(tmp_path / "o"), "--scans", "shared_tooling"]) == 0
    rows = [json.loads(line) for line in (tmp_path / "o" / "repos.jsonl").read_text(encoding="utf-8").splitlines()]
    ids = {i for row in rows for i in row["scans"]["shared_tooling"]["marketplace_ids"]}
    assert len(ids) == 2


def test_a_fragment_teams_list_is_not_a_setting() -> None:
    fragment = {**_fragment("acme", "on"), "provider": "github",
                "teams": [{"slug": "eng", "parent": None, "repos": ["svc"]}]}
    assert "teams" not in fg.normalise_settings(fragment)
