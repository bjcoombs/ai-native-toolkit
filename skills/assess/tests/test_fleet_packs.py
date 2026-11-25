"""``fleet.py --pack``: the same practice packs on every repository, reported per pack (#1276).

Part E of #1266. A fleet over three fixture repositories with a ``company`` and a ``team``
pack: the per-pack met counts in ``level.json``, ``fleet-data.json``, ``report.md`` and the
page; a repository whose pack check could not be read counted as not measured; no fixture
path or file text in any output; a malformed pack refused before any scan.

    cd skills/assess && uv run --locked pytest tests/test_fleet_packs.py
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.fleet import fleet_packs as fp
from lib.fleet.fleet_level import fleet_levels
from lib.fleet.runner import ScanOptions, signals_command
from lib.scoring.levels import parse_levels
from lib.pack_layering import layer_packs
from lib.packs import PackError, parse_pack
from test_fleet import make_repo
from test_fleet_cli import LEVELS, fleet_cli

COMPANY = """\
[pack]
name = "company"
version = "2026.11"

[[checks]]
id = "glossary"
title = "Domain glossary"
why = "Agents need the domain's words."
exists = "docs/glossary.md"

[[checks]]
id = "owner"
title = "Owner declared"
why = "An agent needs to know who to ask."
key_exists = { file = "service.json", key = "metadata.owner" }

[[rows]]
id = "company-glossary"
title = "Domain glossary"
scope = "team"
aggregation = "min"
  [[rows.levels]]
  level = 1
  truth = ["docs"]
  criteria = [{ signal = "pack.company.glossary", op = "eq", value = true }]

[[rows]]
id = "company-owner"
title = "Service owner declared"
scope = "team"
aggregation = "min"
  [[rows.levels]]
  level = 1
  truth = ["docs"]
  criteria = [{ signal = "pack.company.owner", op = "eq", value = true }]
"""

TEAM = """\
[pack]
name = "team"
version = "1"

[[checks]]
id = "adrs"
title = "Decision records"
why = "Decisions written down outlive the people who made them."
glob_count = "docs/adr/*.md"

[[rows]]
id = "team-adrs"
title = "Decision records"
scope = "team"
aggregation = "min"
  [[rows.levels]]
  level = 1
  truth = ["docs"]
  criteria = [{ signal = "pack.team.adrs", op = "gte", value = 1 }]
"""

# Text and names no output may carry: every one is a fixture path or file content.
SECRET_TEXT = "zebra-sentinel-text"
ESTATE = {
    "grpa/svc1": {"docs/glossary.md": f"# {SECRET_TEXT}\n", "service.json": json.dumps({"metadata": {"owner": "quokka-owner"}}),
                  "docs/adr/0001-first.md": f"{SECRET_TEXT}\n"},
    "grpa/svc2": {"docs/glossary.md": "# words\n", "service.json": "{not json " + SECRET_TEXT},
    "grpb/lib1": {"README.md": "# lib1\n"},
}
PATH_WORDS = ("glossary.md", "service.json", "0001-first", "docs/adr", "quokka-owner")


@pytest.fixture(scope="module")
def estate(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("fleet-packs")
    root = base / "froot"
    for slug, files in ESTATE.items():
        make_repo(root, slug, files=files)
    (base / "levels.toml").write_text(LEVELS, encoding="utf-8")
    (base / "company.pack.toml").write_text(COMPANY, encoding="utf-8")
    (base / "team.pack.toml").write_text(TEAM, encoding="utf-8")
    return base


@pytest.fixture(scope="module")
def run_out(estate: Path) -> Path:
    out = estate / "out"
    assert fleet_cli.main([str(estate / "froot"), "--levels", str(estate / "levels.toml"),
                           "--pack", str(estate / "company.pack.toml"), "--pack", str(estate / "team.pack.toml"),
                           "--history-dir", str(estate / "history"), "--out", str(out)]) == 0
    return out


def _practices(roll_up: list[dict[str, object]]) -> dict[str, dict[str, tuple[object, object, object]]]:
    return {str(p["name"]): {str(q["condition"]): (q["met"], q["measured"], q["not_measured"])  # type: ignore[index]
                             for q in p["practices"]}  # type: ignore[attr-defined]
            for p in roll_up}


EXPECTED = {
    "company": {"company-glossary.1": (2, 3, 0), "company-owner.1": (1, 2, 1)},
    "team": {"team-adrs.1": (1, 3, 0)},
}


def test_every_repository_is_scored_per_pack_in_level_json(run_out: Path) -> None:
    level = json.loads((run_out / "level.json").read_text(encoding="utf-8"))
    assert [p["name"] for p in level["packs"]] == ["company", "team"]
    assert _practices(level["packs"]) == EXPECTED
    assert {p["repos"] for p in level["packs"]} == {3}
    sources = {c["id"]: c.get("source") for r in level["repos"].values() for c in r["conditions"]}
    assert sources["company-glossary.1"] == "company" and sources["team-adrs.1"] == "team"


def test_a_pack_check_that_cannot_be_read_is_not_measured_never_unmet(run_out: Path) -> None:
    level = json.loads((run_out / "level.json").read_text(encoding="utf-8"))
    by_repo = {repo: {c["id"]: c["state"] for c in r["conditions"]} for repo, r in level["repos"].items()}
    states = sorted(cs["company-owner.1"] for cs in by_repo.values())
    assert states == ["met", "not_measured", "unmet"]
    rows = [json.loads(line) for line in (run_out / "repos.jsonl").read_text(encoding="utf-8").splitlines()]
    owner = sorted(json.dumps(r["scans"]["pack"]["company"]["owner"]) for r in rows)
    assert owner == ['false', 'true', '{"available": false}']


def test_fleet_data_report_and_page_name_each_pack(run_out: Path) -> None:
    data = json.loads((run_out / "fleet-data.json").read_text(encoding="utf-8"))
    assert _practices(data["packs"]) == EXPECTED
    report = (run_out / "report.md").read_text(encoding="utf-8")
    section = report[report.index("## Practice packs"):]
    assert section.splitlines()[2].startswith("In place: 3 practices from 2 packs")
    assert "### Pack company (version 2026.11)" in section and "### Pack team (version 1)" in section
    assert "| Service owner declared | 1 | 2 | 1 |" in section
    page = (run_out / "fleet-view.html").read_text(encoding="utf-8")
    assert '"packs":[{"name":"company"' in page


def test_run_json_names_each_pack_and_the_history_keeps_it(run_out: Path, estate: Path) -> None:
    stamp = json.loads((run_out / "run.json").read_text(encoding="utf-8"))
    assert [(p["name"], p["version"]) for p in stamp["packs"]] == [("company", "2026.11"), ("team", "1")]
    assert all(len(p["sha256"]) == 64 for p in stamp["packs"])
    kept = list((estate / "history").rglob("run.json"))
    assert len(kept) == 1 and json.loads(kept[0].read_text(encoding="utf-8"))["packs"] == stamp["packs"]


def test_no_fixture_path_or_file_text_reaches_the_outputs(run_out: Path) -> None:
    for name in ("repos.jsonl", "fleet-data.json", "report.md", "level.json", "run.json", "fleet-view.html"):
        text = (run_out / name).read_text(encoding="utf-8")
        assert SECRET_TEXT not in text, name
        for word in PATH_WORDS:
            assert word not in text, (name, word)


def test_a_malformed_pack_exits_2_before_any_repository_is_scanned(estate: Path, tmp_path: Path,
                                                                      monkeypatch: pytest.MonkeyPatch,
                                                                      capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "bad.pack.toml"
    bad.write_text('[pack]\nname = "bad"\nversion = "1"\n\n[[checks]]\nid = "x"\ntitle = "t"\nwhy = "w"\n'
                   'command = "make lint"\n', encoding="utf-8")
    scanned: list[object] = []
    monkeypatch.setattr(fleet_cli, "run_fleet", lambda *a, **k: scanned.append(a) or [])
    out = tmp_path / "out"
    assert fleet_cli.main([str(estate / "froot"), "--levels", str(estate / "levels.toml"), "--pack", str(bad),
                           "--no-history", "--out", str(out)]) == 2
    assert scanned == [] and not out.exists()
    assert "--pack:" in capsys.readouterr().err


def test_pack_without_a_model_is_a_usage_error(estate: Path, tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        fleet_cli.main([str(estate / "froot"), "--pack", str(estate / "team.pack.toml"), "--out", str(tmp_path)])
    assert exc.value.code == 2


def test_a_pack_row_clashing_with_the_base_model_exits_2(estate: Path, tmp_path: Path) -> None:
    clash = tmp_path / "clash.pack.toml"
    clash.write_text(TEAM.replace('id = "team-adrs"', 'id = "org2fa"'), encoding="utf-8")
    assert fleet_cli.main([str(estate / "froot"), "--levels", str(estate / "levels.toml"), "--pack", str(clash),
                           "--no-history", "--out", str(tmp_path / "out")]) == 2


# --- units -----------------------------------------------------------------------------


def test_each_scan_reads_the_private_copies_in_pass_order_and_the_platform_pass_none(tmp_path: Path) -> None:
    loaded = fp.FleetPacks((parse_pack(_toml(COMPANY)), parse_pack(_toml(TEAM))), (COMPANY, TEAM))
    paths = fp.pack_copies(tmp_path, loaded)
    assert [p.read_text(encoding="utf-8") for p in paths] == [COMPANY, TEAM]
    assert [p.name for p in paths] == ["company.pack.toml", "team.pack.toml"]
    command = signals_command(tmp_path, tmp_path / "o.json", ScanOptions(packs=paths))
    assert command[-4:] == ["--pack", str(paths[0]), "--pack", str(paths[1])]
    assert "--pack" not in signals_command(tmp_path, tmp_path / "o.json", ScanOptions(packs=paths, platform=True))


def test_a_pack_read_twice_by_name_is_refused(estate: Path) -> None:
    with pytest.raises(PackError, match="already passed"):
        fp.load_fleet_packs([str(estate / "team.pack.toml"), str(estate / "team.pack.toml")])


def test_a_run_without_packs_writes_no_packs_key_and_no_section() -> None:
    model = parse_levels(_toml(LEVELS))
    assert "packs" not in fleet_levels([], model, toolkit_commit=None, plugin_version="x")
    assert fp.pack_roll_up(model, [], []) == []
    assert fp.pack_roll_up(None, [{"name": "team"}], []) == []
    assert fp.packs_section([]) is None


def test_a_hazard_practice_holds_when_unmet_and_a_checks_only_pack_lists_none() -> None:
    pack = parse_pack(_toml(TEAM))
    model = layer_packs(parse_levels(_toml(LEVELS)), "levels.toml", [pack])
    result = {"conditions": [{"id": "team-adrs.1", "row": "team-adrs", "level": 1, "state": "unmet",
                              "polarity": "hazard", "source": "team"}]}
    roll = fp.pack_roll_up(model, [{"name": "team", "version": "1", "sha256": "s"},
                                   {"name": "checks", "version": "2", "sha256": "t"}], [result, None])  # type: ignore[list-item]
    assert roll[0]["practices"][0]["met"] == 1 and roll[0]["repos"] == 1
    assert roll[1]["practices"] == []
    section = fp.packs_section(roll)  # type: ignore[arg-type]
    assert section is not None and "In place: 1 practice from 2 packs, read over 1 scanned repository." in section
    assert "This pack adds checks only" in section



def test_a_pack_with_rows_lists_its_practices_when_no_repository_scanned() -> None:
    """The practices come from the model, so a run where every scan failed still names them."""
    model = layer_packs(parse_levels(_toml(LEVELS)), "levels.toml", [parse_pack(_toml(TEAM))])
    roll = fp.pack_roll_up(model, [{"name": "team", "version": "1", "sha256": "s"}], [None])
    assert roll[0]["repos"] == 0
    assert [(p["condition"], p["met"], p["measured"], p["not_measured"]) for p in roll[0]["practices"]] == [
        ("team-adrs.1", 0, 0, 0)]
    section = fp.packs_section(roll)  # type: ignore[arg-type]
    assert section is not None and "adds checks only" not in section


def test_an_owner_excluded_condition_is_in_neither_column() -> None:
    model = layer_packs(parse_levels(_toml(LEVELS)), "levels.toml", [parse_pack(_toml(TEAM))])
    result = {"conditions": [{"id": "team-adrs.1", "row": "team-adrs", "level": 1, "state": "not_applicable",
                              "source": "team"}]}
    roll = fp.pack_roll_up(model, [{"name": "team", "version": "1", "sha256": "s"}], [result])  # type: ignore[list-item]
    assert roll[0]["repos"] == 1
    assert (roll[0]["practices"][0]["measured"], roll[0]["practices"][0]["not_measured"]) == (0, 0)


def test_a_levels_file_holding_the_default_model_names_its_rows_default(tmp_path: Path) -> None:
    """The base source does not depend on the file name, so a copy keeps the rules hash."""
    from lib.scoring.signal_grades import DEFAULT_LEVELS
    copy = tmp_path / "renamed.toml"
    copy.write_text(DEFAULT_LEVELS.read_text(encoding="utf-8"), encoding="utf-8")
    team = parse_pack(_toml(TEAM))
    model = fp.fleet_model(copy, copy.name, [team])
    assert {row.get("source") for row in model["rows"]} == {"default", "team"}
    custom = tmp_path / "levels.toml"
    custom.write_text(LEVELS, encoding="utf-8")
    assert fp.fleet_model(custom, custom.name, [team])["rows"][0].get("source") == "levels.toml"
    named_default = parse_pack(_toml(TEAM.replace('name = "team"', 'name = "default"').replace("pack.team.", "pack.default.")))
    with pytest.raises(PackError):
        fp.fleet_model(copy, copy.name, [named_default])


HAZARD = TEAM.replace('id = "team-adrs"', 'id = "team-envfiles"').replace(
    'title = "Decision records"\nscope', 'title = "Committed .env files"\nscope').replace(
    'truth = ["docs"]', 'truth = ["docs"]\n  polarity = "hazard"')


def test_a_hazard_row_is_labelled_free_of_it_never_meet_it() -> None:
    """A hazard names a harm: its count is the repositories free of it, and both the report and
    the page say so, apart from the practices."""
    from lib.fleet.fleet_page_assets import SCRIPT
    model = layer_packs(parse_levels(_toml(LEVELS)), "levels.toml", [parse_pack(_toml(HAZARD))])
    result = {"conditions": [{"id": "team-envfiles.1", "row": "team-envfiles", "level": 1, "state": "unmet",
                              "polarity": "hazard", "source": "team"}]}
    roll = fp.pack_roll_up(model, [{"name": "team", "version": "1", "sha256": "s"}], [result])  # type: ignore[list-item]
    practice = roll[0]["practices"][0]
    assert practice["polarity"] == "hazard" and practice["met"] == 1
    section = fp.packs_section(roll)  # type: ignore[arg-type]
    assert section is not None
    assert "| Hazard | Repositories free of it | Measured | Not measured |" in section
    assert "| Committed .env files | 1 | 1 | 0 |" in section
    assert "Repositories that meet it" not in section
    assert 'q.polarity === "hazard" ? " free of it" : " meet it"' in SCRIPT
    plain = fp.pack_roll_up(layer_packs(parse_levels(_toml(LEVELS)), "levels.toml", [parse_pack(_toml(TEAM))]),
                            [{"name": "team", "version": "1", "sha256": "s"}], [None])
    assert "polarity" not in plain[0]["practices"][0]

def _toml(text: str) -> dict[str, object]:
    import tomllib
    return tomllib.loads(text)
