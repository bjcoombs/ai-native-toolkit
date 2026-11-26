"""The monorepo unit rows of fleet-data.json (lib/fleet/fleet_data_units.py, issue #866).

A repository scanned with ``--monorepo-units`` expands into one row per unit: its Step, the
Step above, the first gate blocking it and its per-tag tally, never an "N of M" figure. The
unit's folder is written in clear only in --full output, and as its salted hash under
--hash-paths.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import assess_signals
import lib.fleet.fleet_data as fd
from fleet_data_fixture import ROWS, SALT, VARIANTS, build, model
from lib.counts_only.content_free import HASH_PREFIX, hash_value
from lib.fleet.fleet_data_units import next_blocker, unit_rows
from lib.fleet.fleet_publish import check_strings
from lib.fleet.runner import ScanOptions, signals_command
from lib.scoring.headline import tag_cells
from lib.scoring.level_tally import default_model, level_result
from test_fleet_cli import fleet_cli
from test_unit_readings import MONOREPO, _write

PROJ2 = next(r for r in ROWS if r["repo"] == "grp/sub/proj2")


def _repos(privacy: fd.Privacy) -> dict[str, fd.DataRepo]:
    return {r["key"]: r for r in build(privacy)["repos"]}


# --- the rows ----------------------------------------------------------------------

def test_each_unit_reads_its_own_blocks_over_the_repository_scans() -> None:
    api, web = unit_rows(model(), PROJ2["scans"]) or []
    # apps/api: its own docs at grade A hold docs.1 and docs.2; the inherited ci does not hold
    assert (api["key"], api["step"], api["next_step"]) == ("apps/api", 2, 3)
    assert api["next_blocker"] == {"gate": "ci", "state": "unmet", "disposition": None}
    # apps/web: its own empty docs leave docs.1 unmet, below the repository's own Step 2
    assert (web["key"], web["step"], web["next_step"]) == ("apps/web", 1, 2)
    assert web["next_blocker"] == {"gate": "docs", "state": "unmet", "disposition": None}
    scans = {**(PROJ2["scans"] or {}), "docs": {"available": True, "lines": 30, "grade": "A"}}
    assert api["tags"] == tag_cells(level_result(model(), scans), model())
    assert not {"tally", "measured", "met"} & set(api)  # no N of M on a unit row


def test_a_repository_not_measured_by_unit_has_no_rows() -> None:
    assert unit_rows(None, PROJ2["scans"]) is None  # no model
    assert unit_rows(model(), {"docs": {"available": True}}) is None  # no --monorepo-units
    assert unit_rows(model(), None) is None
    assert unit_rows(model(), {"unit_readings": {"available": False, "reason": "units_unavailable"}}) is None
    assert unit_rows(model(), {"unit_readings": {"available": True, "units": []}}) == []


def test_next_blocker_is_null_at_the_top_step() -> None:
    top = level_result(model(), {"docs": {"available": True, "lines": 3, "grade": "A"},
                                 "ci": {"available": True, "required": True}})
    assert top["next_step"] is None and next_blocker(top) is None


# --- fleet-data.json ---------------------------------------------------------------

def test_fleet_data_names_a_unit_only_in_full_or_hashed() -> None:
    full = _repos(fd.Privacy(full=True))
    assert [u["key"] for u in full["grp/sub/proj2"]["unit_rows"] or []] == ["apps/api", "apps/web"]
    plain = _repos(fd.Privacy())
    assert [u["key"] for u in plain["grp/sub/proj2"]["unit_rows"] or []] == [None, None]
    hashed = _repos(fd.Privacy(salt=SALT))
    keys = [u["key"] for u in hashed[hash_value("grp/sub/proj2", SALT)]["unit_rows"] or []]
    assert keys == [hash_value("apps/api", SALT), hash_value("apps/web", SALT)]
    # a key the scan already hashed is kept, so a hashed row joins
    row = {**PROJ2["scans"]["unit_readings"]["units"][0], "path": keys[0]}  # type: ignore[index]
    (again,) = fd.public_unit_rows(unit_rows(model(), {**(PROJ2["scans"] or {}), "unit_readings": {
        "available": True, "units": [row]}}), fd.Privacy(salt=SALT)) or []
    assert again["key"] == keys[0]


def test_repositories_without_unit_readings_or_failed_carry_null() -> None:
    repos = _repos(fd.Privacy(full=True))
    assert repos["grp/sub/proj1"]["unit_rows"] is None
    assert repos["grp/proj4"]["unit_rows"] is None  # failed


@pytest.mark.parametrize("name", ["counts-only.json", "hashed.json"])
def test_counts_only_unit_rows_pass_the_publish_check(name: str) -> None:
    data = build(VARIANTS[name])
    assert check_strings(data["repos"], "repos", hashed=VARIANTS[name].salt is not None) == []


def test_the_publish_check_refuses_a_clear_or_unhashed_unit_folder() -> None:
    node = [{"unit_rows": [{"key": "apps/api"}]}]
    assert check_strings(node, "repos", hashed=False) == [
        "repos.unit_rows.key: written only hashed, under --hash-paths"]
    assert check_strings(node, "repos", hashed=True) == [
        "repos.unit_rows.key: written only hashed, under --hash-paths"]
    assert check_strings([{"unit_rows": [{"key": HASH_PREFIX + "0" * 16}]}], "repos", hashed=True) == []


# --- the scan child ----------------------------------------------------------------

def test_the_offline_child_gets_units_and_the_platform_pass_does_not() -> None:
    assert "--units" not in signals_command(Path("/c"), Path("/o.json"), ScanOptions())
    assert "--units" in signals_command(Path("/c"), Path("/o.json"), ScanOptions(units=True))
    assert "--units" not in signals_command(Path("/c"), Path("/o.json"), ScanOptions(units=True, platform=True))


# --- parity with /assess and the CLI -----------------------------------------------

@pytest.fixture(scope="module")
def mono(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _write(tmp_path_factory.mktemp("mono") / "root" / "grp" / "mono" / "mono-main", MONOREPO)


def test_a_unit_row_step_equals_the_assess_unit_step(mono: Path, tmp_path: Path) -> None:
    from assess_core import build_run_context
    ctx: dict[str, Any] = build_run_context(repo_root=mono, run_date="2026-11-23", non_interactive=True)
    assert assess_signals.main([str(mono), "--out", str(tmp_path / "d.json"), "--units"]) == 0
    scans = json.loads((tmp_path / "d.json").read_text(encoding="utf-8"))["scans"]
    rows = unit_rows(default_model(), scans) or []
    assert [(r["key"], r["step"]) for r in rows] == [(u["path"], u["step"]) for u in ctx["unit_levels"]["units"]]


def test_fleet_monorepo_units_needs_levels(mono: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                           capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        fleet_cli.main([str(mono.parents[2]), "--out", "o", "--monorepo-units", "--no-history"])
    assert exc.value.code == 2 and "--monorepo-units needs --levels" in capsys.readouterr().err
    assert not (tmp_path / "o").exists()


def test_fleet_monorepo_units_writes_hashed_unit_rows(mono: Path, tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    levels = Path(assess_signals.__file__).resolve().parents[1] / "levels" / "default.toml"
    args = [str(mono.parents[2]), "--levels", str(levels), "--no-history", "--no-diagrams"]
    assert fleet_cli.main([*args, "--out", "plain"]) == 0
    assert fleet_cli.main([*args, "--out", "units", "--monorepo-units", "--hash-salt", "s"]) == 0
    (plain,) = json.loads((tmp_path / "plain" / "fleet-data.json").read_text(encoding="utf-8"))["repos"]
    assert plain["unit_rows"] is None
    text = (tmp_path / "units" / "fleet-data.json").read_text(encoding="utf-8")
    (repo,) = json.loads(text)["repos"]
    rows = repo["unit_rows"]
    assert [r["key"] for r in rows] == [hash_value(p, "s") for p in ("services/billing", "services/search", "web")]
    assert all(isinstance(r["step"], int) and r["tags"] for r in rows)
    assert "billing" not in text and "services" not in text


# --- skill units (#1098) -------------------------------------------------------------

def test_a_unit_row_carries_the_unit_kind() -> None:
    readings = PROJ2["scans"]["unit_readings"]["units"]  # type: ignore[index]
    marked = [{**readings[0], "kind": "skill"}, {**readings[1], "kind": "bogus"}]
    skill, other = unit_rows(model(), {**(PROJ2["scans"] or {}), "unit_readings": {
        "available": True, "units": marked}}) or []
    assert skill["kind"] == "skill"
    assert other["kind"] is None  # an unknown token is no kind
    # a stored reading from before units had a kind
    assert [r["kind"] for r in unit_rows(model(), PROJ2["scans"]) or []] == [None, None]
    (public,) = fd.public_unit_rows([skill], fd.Privacy()) or []
    assert (public["key"], public["kind"]) == (None, "skill")  # counts-only keeps the token


def test_a_skills_repository_reads_skill_by_skill(tmp_path: Path) -> None:
    from test_unit_components import PLUGIN
    repo = _write(tmp_path / "plugin", PLUGIN)
    assert assess_signals.main([str(repo), "--out", str(tmp_path / "d.json"), "--units"]) == 0
    scans = json.loads((tmp_path / "d.json").read_text(encoding="utf-8"))["scans"]
    rows = unit_rows(default_model(), scans) or []
    assert [(r["key"], r["kind"]) for r in rows] == [
        ("agents", "agents"), ("commands", "commands"), ("skills/plain", "skill"), ("skills/tooled", "skill")]
    assert all(r["step"] is not None for r in rows)


def test_the_fleet_schema_writes_a_literal_as_an_enum() -> None:
    from typing import Literal

    from lib.fleet.fleet_group_schema import type_schema
    assert type_schema(Literal["skill", "agents"]) == {"enum": ["skill", "agents"]}
    assert type_schema(Literal["skill"] | None) == {"anyOf": [{"enum": ["skill"]}, {"type": "null"}]}


# --- per-practice counts per org unit (#1366, part 1 of #1314) -----------------------

def _practice_counts(privacy: fd.Privacy) -> dict[str, dict[str, dict[str, int]]]:
    """Each org unit's ``row -> {met, measured, repos}``, keyed by the unit's clear key."""
    keys = {privacy.key(k): k for k in ("ownership/grp", "ownership/grp/other", "ownership/grp/sub",
                                       "system/checkout")}
    return {keys[u["key"]]: {lv["row"]: {"met": lv["met"], "measured": lv["measured"], "repos": lv["repos"]}
                             for lv in u["levels"]}
            for v in build(privacy)["views"] for u in v["units"]}


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_each_org_unit_counts_met_of_measured_per_practice(name: str) -> None:
    """met: measured repositories whose level result holds the row at its top (#1474); measured: a
    known level; repos: every repository below the unit. proj3's unknown docs and the failed proj4
    count in repos only."""
    assert _practice_counts(VARIANTS[name]) == {
        # docs: proj1 at 2 of 2, proj2 at 1 of 2; ci: proj1 and proj3 at 1 of 1, proj2 at 0. No
        # level result lists a row in in_place with top, so none is met (opportunities.in_place: 0)
        "ownership/grp": {"docs": {"met": 0, "measured": 2, "repos": 4},
                          "ci": {"met": 0, "measured": 3, "repos": 4}},
        "ownership/grp/sub": {"docs": {"met": 0, "measured": 2, "repos": 2},
                              "ci": {"met": 0, "measured": 2, "repos": 2}},
        "ownership/grp/other": {"docs": {"met": 0, "measured": 0, "repos": 1},
                                "ci": {"met": 0, "measured": 1, "repos": 1}},
        "system/checkout": {"docs": {"met": 0, "measured": 1, "repos": 2},
                            "ci": {"met": 0, "measured": 1, "repos": 2}},
    }


def test_met_never_exceeds_measured_and_a_row_with_no_levels_is_never_met() -> None:
    assert fd.row_roll_up("r", [(2, 2, True), (1, 2, False), (None, 2, False), (0, 0, False)]) == {
        "row": "r", "met": 1, "measured": 2, "repos": 4, "lowest_level": 1}
    assert fd.row_roll_up("r", []) == {"row": "r", "met": 0, "measured": 0, "repos": 0, "lowest_level": None}


def test_met_reads_the_level_result_not_the_row_level() -> None:
    # #1474: at the top level by evaluate_row but not held at top by the level result: not met
    assert fd.row_roll_up("r", [(2, 2, False), (2, 2, True)])["met"] == 1
