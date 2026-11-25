"""Reading fleet output written before the 2.0.0 maturity-model rename, lib/fleet/fleet_legacy.py (#670)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import lib.fleet.fleet_trends as ft
from lib.fleet.fleet_compare import comparability, load_run
from lib.fleet.fleet_history import entry_conflict, entry_repos, latest_matching
from lib.fleet.fleet_legacy import RENAMED_KEYS, upgrade
from lib.fleet.fleet_stamp import FleetStamp
from lib.fleet.fleet_view import load_groups
from lib.scoring.levels import load_levels
from test_fleet import make_repo
from test_fleet_cli import fleet_cli
from test_fleet_heatmap import cli as heatmap_cli
from test_fleet_next_steps import cli as next_steps_cli

FIXTURES = Path(__file__).parent / "fixtures"
LEGACY_ORG = FIXTURES / "fleet_legacy" / "acme"
LEGACY = LEGACY_ORG / "2026-11-20"
LEVELS_FILE = FIXTURES / "fleet_heatmap" / "levels.toml"
REPOS = frozenset({"acme/svc1", "acme/svc2"})


def _stamp(**override: object) -> FleetStamp:
    """A stamp the current code writes for a run matching the legacy one."""
    legacy = json.loads((LEGACY / "run.json").read_text(encoding="utf-8"))
    stamp = {**upgrade(legacy), "scan_date": "2026-12-20", **override}
    return stamp  # type: ignore[return-value]


def test_the_fixture_is_pre_rename():
    stamp = json.loads((LEGACY / "run.json").read_text(encoding="utf-8"))
    group = json.loads((LEGACY / "groups.jsonl").read_text(encoding="utf-8").splitlines()[0])
    for data in (stamp, group):
        assert "levels_version" not in data and data[next(iter(RENAMED_KEYS))] == "fixture-1"
    assert "levels" not in group and isinstance(group["rubric"], list)


def test_upgrade_moves_old_keys_and_leaves_new_data_alone():
    new = {"levels_version": "v", "levels": []}
    assert upgrade(new) is new
    old = {"rubric_version": "v", "rubric": [1], "group": "g"}
    assert upgrade(old) == {"levels_version": "v", "levels": [1], "group": "g"}
    assert old == {"rubric_version": "v", "rubric": [1], "group": "g"}  # not mutated
    both = {"rubric_version": "old", "levels_version": "new"}
    assert upgrade(both) == {"levels_version": "new"}


def test_groups_written_before_the_rename_load_under_the_new_names():
    [group] = load_groups(LEGACY)
    assert group["levels_version"] == "fixture-1"
    assert [row["id"] for row in group["levels"]] == ["org2fa", "teamdocs"]
    assert "rubric" not in group


def test_compare_loads_a_pre_rename_run_and_finds_it_comparable():
    earlier = load_run(LEGACY)
    assert earlier.stamp["levels_version"] == "fixture-1"
    reasons, _notes = comparability(earlier.stamp, _stamp())
    assert reasons == []
    reasons, _notes = comparability(earlier.stamp, _stamp(levels_version="fixture-2"))
    assert reasons == ["maturity-model version changed 'fixture-1'->'fixture-2'"]


def test_history_matches_a_pre_rename_entry():
    stamp = _stamp()
    assert entry_conflict(LEGACY, stamp, REPOS) is None
    assert latest_matching(LEGACY_ORG, stamp, REPOS) == LEGACY
    other = _stamp(levels_version="fixture-2")
    assert "different levels_version" in str(entry_conflict(LEGACY, other, REPOS))
    assert latest_matching(LEGACY_ORG, other, REPOS) is None


@pytest.mark.parametrize("plugin_version", ["1.226.0", "2.0.0"])
def test_trend_history_counts_a_pre_rename_entry(plugin_version):
    """The trend matches on window, scans and output mode, not the plugin version, so a 2.x
    run keeps drawing 1.x history entries (unlike --compare, which refuses a major change)."""
    runs = ft.history_runs(LEGACY_ORG, _stamp(plugin_version=plugin_version), REPOS, load_levels(LEVELS_FILE))
    assert [(run["label"], run["levels_version"], run["scaled"]) for run in runs] == [
        ("2026-11-20", "fixture-1", True)]
    # Without the upgrade the entry would name no model version, and trend_run drops such a run.
    raw = json.loads((LEGACY / "run.json").read_text(encoding="utf-8"))
    assert ft.trend_run(raw, load_groups(LEGACY)) is None


PLUGIN_VERSION = json.loads((Path(__file__).resolve().parents[3] / ".claude-plugin" / "plugin.json")
                            .read_text(encoding="utf-8"))["version"]


def _other_major(version: str) -> str:
    major, rest = version.split(".", 1)
    return f"{int(major) + 1}.{rest}"


@pytest.mark.parametrize("earlier_plugin", ["same", "other_major"])
def test_fleet_compare_against_a_pre_rename_run(earlier_plugin, tmp_path, capsys):
    """Same plugin major: comparable. Another major (every 1.x run seen from 2.x): refused
    for the major version alone, never for a malformed stamp or a missing maturity-model key."""
    root = tmp_path / "estate"
    make_repo(root, "acme/svc1", files={"CLAUDE.md": "a\n"})
    make_repo(root, "acme/svc2")
    earlier = tmp_path / "earlier"
    shutil.copytree(LEGACY, earlier)
    stamp = json.loads((earlier / "run.json").read_text(encoding="utf-8"))
    stamp["plugin_version"] = PLUGIN_VERSION if earlier_plugin == "same" else _other_major(PLUGIN_VERSION)
    (earlier / "run.json").write_text(json.dumps(stamp), encoding="utf-8")
    out = tmp_path / "out"
    code = fleet_cli.main([str(root), "--out", str(out), "--scans", "nested_instructions",
                           "--levels", str(LEVELS_FILE), "--no-diagrams", "--no-history",
                           "--compare", str(earlier)])
    assert code == 0, capsys.readouterr().err
    comparison = json.loads((out / "compare.json").read_text(encoding="utf-8"))
    assert comparison["earlier"]["levels_version"] == "fixture-1"
    if earlier_plugin == "same":
        assert comparison["comparable"] is True and comparison["reasons"] == []
    else:
        assert comparison["comparable"] is False
        assert comparison["reasons"] == [f"major version changed {stamp['plugin_version']}->{PLUGIN_VERSION}"]
    assert entry_repos(earlier) == REPOS


@pytest.mark.parametrize("script", ["fleet", "heatmap", "next_steps"])
@pytest.mark.parametrize("flag", [["--rubric", "x.toml"], ["--rubric=x.toml"], ["--rubric"]])
def test_the_pre_rename_flag_exits_2_naming_levels(script, flag, tmp_path, capsys):
    main = {"fleet": fleet_cli.main, "heatmap": heatmap_cli.main, "next_steps": next_steps_cli.main}[script]
    with pytest.raises(SystemExit) as exit_info:
        main([str(tmp_path), *flag])
    assert exit_info.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    [line] = captured.err.splitlines()
    assert line.endswith("error: --rubric was renamed in 2.0.0: use --levels FILE")
