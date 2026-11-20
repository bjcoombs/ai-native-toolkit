"""Reading fleet output written before the 2.0.0 maturity-model rename, lib/fleet_legacy.py (#670)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from lib import fleet_trends as ft
from lib.fleet_compare import comparability, load_run
from lib.fleet_history import entry_conflict, entry_repos, latest_matching
from lib.fleet_legacy import RENAMED_KEYS, upgrade
from lib.fleet_stamp import FleetStamp
from lib.fleet_view import load_groups
from lib.levels import load_levels
from test_fleet import make_repo
from test_fleet_cli import fleet_cli

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


def test_trend_history_counts_a_pre_rename_entry():
    runs = ft.history_runs(LEGACY_ORG, _stamp(), REPOS, load_levels(LEVELS_FILE))
    assert [(run["label"], run["levels_version"], run["scaled"]) for run in runs] == [
        ("2026-11-20", "fixture-1", True)]
    # Without the upgrade the entry would name no model version, and trend_run drops such a run.
    raw = json.loads((LEGACY / "run.json").read_text(encoding="utf-8"))
    assert ft.trend_run(raw, load_groups(LEGACY)) is None


def test_fleet_compare_against_a_pre_rename_run(tmp_path, capsys):
    root = tmp_path / "estate"
    make_repo(root, "acme/svc1", files={"CLAUDE.md": "a\n"})
    make_repo(root, "acme/svc2")
    earlier = tmp_path / "earlier"
    shutil.copytree(LEGACY, earlier)
    out = tmp_path / "out"
    code = fleet_cli.main([str(root), "--out", str(out), "--scans", "nested_instructions",
                           "--rubric", str(LEVELS_FILE), "--no-diagrams", "--no-history",
                           "--compare", str(earlier)])
    assert code == 0, capsys.readouterr().err
    comparison = json.loads((out / "compare.json").read_text(encoding="utf-8"))
    assert comparison["earlier"]["levels_version"] == "fixture-1"
    assert not any("maturity-model version" in reason for reason in comparison["reasons"])
    assert entry_repos(earlier) == REPOS
