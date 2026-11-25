"""``assess_signals.py --units`` and ``--scope``: monorepo units in the headless runner (#866).

The unit scans (``units``, ``unit_readings``) run only when asked, read the headless blocks,
and give the same per-unit readings as a full /assess run. ``--scope`` measures one folder.
Counts-only output names a unit or scope folder only as its salted hash.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

import assess_signals
from lib import signals
from lib.report.catalog import build_catalog
from lib.counts_only.content_free import HASH_PREFIX, counts_only_scans, hash_value
from lib.counts_only.counts_only_rules import HASHED_PATH, classify
from lib.signals_schema import SCHEMA_PATH
from test_unit_readings import MONOREPO, _write

UNIT_PATHS = ["services/billing", "services/search", "web"]


@pytest.fixture(scope="module")
def repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _write(tmp_path_factory.mktemp("mono") / "repo", MONOREPO)


def _run(repo: Path, out: Path, *extra: str) -> dict[str, Any]:
    assert assess_signals.main([str(repo), "--out", str(out), *extra]) == 0
    doc: dict[str, Any] = json.loads(out.read_text(encoding="utf-8"))
    jsonschema.validate(doc, json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))
    return doc


@pytest.fixture(scope="module")
def full(repo: Path, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _run(repo, tmp_path_factory.mktemp("out") / "full.json", "--units")


# --- selection ---------------------------------------------------------------------

def test_unit_scans_are_known_but_run_only_when_asked() -> None:
    known = signals.known_scans()
    assert set(signals.UNIT_SCANS) <= set(known)
    assert not set(signals.UNIT_SCANS) & set(signals.select_scans(None))
    assert set(signals.UNIT_SCANS) <= set(signals.select_scans(None, units=True))
    assert signals.select_scans(["unit_readings"]) == ["unit_readings"]
    assert set(signals.select_scans(["ci_checks"], units=True)) == {"ci_checks", *signals.UNIT_SCANS}


def test_the_unit_pass_reads_the_headless_blocks() -> None:
    keys = [s.key for s in signals.SIGNAL_SCANS]
    # once each, after the headless entries whose blocks the pass reads
    assert keys.count("unit_readings") == 1 and keys.count("units") == 1
    assert keys.index("unit_readings") > max(keys.index(s.key) for s in signals.HEADLESS_SCANS)


def test_the_catalog_says_the_unit_scans_run_only_on_request() -> None:
    scans = {s["name"]: s for s in build_catalog()["scans"]}
    assert scans["units"]["headless"] == scans["unit_readings"]["headless"] == "units_only"


# --- --units -----------------------------------------------------------------------

def test_units_gives_each_unit_its_readings(full: dict[str, Any]) -> None:
    units, readings = full["scans"]["units"], full["scans"]["unit_readings"]
    assert units["unit_count"] == 3 and [u["path"] for u in units["units"]] == UNIT_PATHS
    assert readings["available"] is True
    assert [u["path"] for u in readings["units"]] == UNIT_PATHS
    billing = readings["units"][0]
    origins = {c["id"]: c["origin"] for c in billing["conditions"]}
    assert origins["layer-0-instructions.1"] == "unit" and origins["layer-5-ci.1"] == "inherited"
    assert full["stamp"]["scope"] is None


def test_headless_unit_readings_match_a_full_run(repo: Path, full: dict[str, Any]) -> None:
    from assess_core import build_run_context
    ctx = build_run_context(repo_root=repo, run_date="2026-11-23", non_interactive=True)
    assert json.loads(json.dumps(ctx["unit_readings"], default=str)) == full["scans"]["unit_readings"]


def test_plain_counts_only_drops_every_unit_folder(repo: Path, tmp_path: Path) -> None:
    doc = _run(repo, tmp_path / "c.json", "--units", "--counts-only")
    assert doc["scans"]["unit_readings"]["unit_count"] == 3
    for block in ("units", "unit_readings"):
        assert all("path" not in u for u in doc["scans"][block]["units"])
    text = json.dumps([doc["scans"]["units"], doc["scans"]["unit_readings"]])
    assert "billing" not in text and "services" not in text


def test_hashed_counts_only_keeps_each_unit_folder_as_a_joinable_hash(repo: Path, tmp_path: Path) -> None:
    first = _run(repo, tmp_path / "a.json", "--units", "--counts-only", "--hash-salt", "fleet")
    second = _run(repo, tmp_path / "b.json", "--units", "--counts-only", "--hash-salt", "fleet")
    keys = [u["path"] for u in first["scans"]["unit_readings"]["units"]]
    assert all(k.startswith(HASH_PREFIX) for k in keys) and len(set(keys)) == 3
    assert keys == [u["path"] for u in first["scans"]["units"]["units"]]  # one join key per unit
    assert keys == [u["path"] for u in second["scans"]["unit_readings"]["units"]]
    assert "billing" not in json.dumps([first["scans"]["units"], first["scans"]["unit_readings"]])


def test_hashed_path_is_dropped_plain_and_hashed_under_a_salt() -> None:
    assert classify("unit_readings.units.path") == HASHED_PATH
    block = {"unit_readings": {"available": True, "units": [{"path": "web", "conditions": []}]}}
    assert counts_only_scans(block)["unit_readings"]["units"] == [{"conditions": []}]
    hashed = counts_only_scans(block, hash_paths=True, salt="s")["unit_readings"]["units"][0]["path"]
    assert hashed.startswith(HASH_PREFIX)
    # a folder with a space still joins: its hash equals the stamp's hashed scope
    for folder in ("services/My Service", "services/billing@corp.example"):
        odd = {"unit_readings": {"available": True, "units": [{"path": folder, "conditions": []}]}}
        (unit,) = counts_only_scans(odd, hash_paths=True, salt="s")["unit_readings"]["units"]
        assert unit["path"] == hash_value(folder, "s")
        assert counts_only_scans(odd)["unit_readings"]["units"] == [{"conditions": []}]


# --- --scope -----------------------------------------------------------------------

def test_scope_measures_one_folder_and_stamps_it(repo: Path, tmp_path: Path) -> None:
    # promissory_markers reads the scope and needs no optional tool (lizard may be absent)
    whole = _run(repo, tmp_path / "w.json", "--scans", "promissory_markers,ci_checks")
    scoped = _run(repo, tmp_path / "s.json", "--scans", "promissory_markers,ci_checks", "--scope", "web")
    billing = _run(repo, tmp_path / "b.json", "--scans", "promissory_markers", "--scope", "services/billing")
    assert scoped["stamp"]["scope"] == "web"
    # the one suppression sits in services/billing: outside web, inside billing
    assert whole["scans"]["promissory_markers"]["total_markers"] == 1
    assert scoped["scans"]["promissory_markers"]["total_markers"] == 0
    assert billing["scans"]["promissory_markers"]["total_markers"] == 1
    # a repository-wide scan still reads the root CI
    assert scoped["scans"]["ci_checks"] == whole["scans"]["ci_checks"]


def test_scope_accepts_an_absolute_folder_under_the_target(repo: Path, tmp_path: Path) -> None:
    doc = _run(repo, tmp_path / "s.json", "--scans", "stats_summary", "--scope", str(repo / "services" / "search"))
    assert doc["stamp"]["scope"] == "services/search"


def test_counts_only_hides_the_scope_folder_but_keeps_it_scoped(repo: Path, tmp_path: Path) -> None:
    plain = _run(repo, tmp_path / "p.json", "--scans", "stats_summary", "--scope", "web", "--counts-only")
    assert plain["stamp"]["scope"] == assess_signals.SCOPE_REDACTED  # scoped, folder hidden
    whole = _run(repo, tmp_path / "wc.json", "--scans", "stats_summary", "--counts-only")
    assert whole["stamp"]["scope"] is None  # null is only the whole checkout
    hashed = _run(repo, tmp_path / "h.json", "--scans", "stats_summary", "--scope", "web",
                  "--counts-only", "--hash-salt", "fleet")
    units = _run(repo, tmp_path / "u.json", "--units", "--counts-only", "--hash-salt", "fleet")
    web = units["scans"]["unit_readings"]["units"][2]["path"]
    assert hashed["stamp"]["scope"] == web  # the scope joins the unit it names
    random = _run(repo, tmp_path / "r.json", "--scans", "stats_summary", "--scope", "web",
                  "--counts-only", "--hash-paths")
    assert random["stamp"]["scope"].startswith(HASH_PREFIX)


@pytest.mark.parametrize("scope", ["missing", "../outside"])
def test_a_scope_that_is_missing_or_outside_the_target_is_a_usage_error(
        repo: Path, tmp_path: Path, scope: str, capsys: pytest.CaptureFixture[str]) -> None:
    (repo.parent / "outside").mkdir(exist_ok=True)
    assert assess_signals.main([str(repo), "--out", str(tmp_path / "x.json"), "--scope", scope]) == 2
    assert "--scope" in capsys.readouterr().err
    assert not (tmp_path / "x.json").exists()


def test_scope_and_units_do_not_combine(repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        assess_signals.main([str(repo), "--out", str(tmp_path / "x.json"), "--scope", "web", "--units"])
    assert exc.value.code == 2
    # nor through --scans: the unit blocks would describe the whole checkout, not the scope
    assert assess_signals.main([str(repo), "--out", str(tmp_path / "x.json"), "--scope", "web",
                                "--scans", "units,unit_readings"]) == 2
    assert "does not combine with --scope" in capsys.readouterr().err
    assert not (tmp_path / "x.json").exists()


def test_a_scope_of_the_target_itself_is_the_whole_checkout(repo: Path, tmp_path: Path) -> None:
    for scope in (".", str(repo)):
        doc = _run(repo, tmp_path / "d.json", "--scans", "stats_summary", "--scope", scope, "--counts-only")
        assert doc["stamp"]["scope"] is None
