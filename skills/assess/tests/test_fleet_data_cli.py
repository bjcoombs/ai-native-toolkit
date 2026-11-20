"""fleet.py writes fleet-data.json and reads --units (issue #703)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from test_fleet_cli import LEVELS, fleet_cli, froot  # noqa: F401 - froot is a fixture

UNITS = """\
[[unit]]
view = "system"
id = "core"
kind = "system"

[[unit]]
id = "platform"
kind = "org"

[[repo]]
repo = "grpa/svc1"
ownership = "platform"
system = "core"

[[repo]]
repo = "nothere/repo"
system = "core"
"""


def _data(out: Path) -> dict[str, object]:
    return json.loads((out / "fleet-data.json").read_text(encoding="utf-8"))


def test_units_file_places_repos_and_a_rerun_is_byte_identical(froot, tmp_path, monkeypatch, capsys):  # noqa: F811
    monkeypatch.chdir(tmp_path)
    (tmp_path / "levels.toml").write_text(LEVELS, encoding="utf-8")
    (tmp_path / "units.toml").write_text(UNITS, encoding="utf-8")
    args = [str(froot), "--levels", "levels.toml", "--units", "units.toml", "--no-history", "--no-diagrams"]
    assert fleet_cli.main([*args, "--out", "o1"]) == 0
    assert "fleet: units: nothere/repo is mapped but not in this run; skipped" in capsys.readouterr().err
    assert fleet_cli.main([*args, "--out", "o2"]) == 0
    assert (tmp_path / "o1" / "fleet-data.json").read_bytes() == (tmp_path / "o2" / "fleet-data.json").read_bytes()
    data = _data(tmp_path / "o1")
    repos = {r["key"]: r for r in data["repos"]}  # type: ignore[attr-defined]
    assert repos["grpa/svc1"]["units"] == [{"unit": "ownership/platform", "view": "ownership"},
                                           {"unit": "system/core", "view": "system"}]
    assert repos["grpa/svc2"]["units"] == [{"unit": "ownership/grpa", "view": "ownership"}]


def test_hashed_run_names_no_repository(froot, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.chdir(tmp_path)
    assert fleet_cli.main([str(froot), "--out", "out", "--hash-salt", "s", "--no-history", "--no-diagrams"]) == 0
    text = (tmp_path / "out" / "fleet-data.json").read_text(encoding="utf-8")
    for name in ("grpa", "grpb", "svc1", "svc2", "lib1", str(froot)):
        assert name not in text
    assert json.loads(text)["run"]["paths_hashed"] is True


@pytest.mark.parametrize(("text", "message"), [
    ("[[unit]]\nid = 'x'\nkind = 'squad'\n", "unknown kind 'squad'"),
    ("[[repo]]\nrepo = 'grpa/svc1'\nownership = 'nope'\n", "ownership unit 'nope' is not a unit"),
])
def test_a_bad_units_file_exits_2_before_any_scan(froot, tmp_path, monkeypatch, capsys, text, message):  # noqa: F811
    monkeypatch.chdir(tmp_path)
    (tmp_path / "units.toml").write_text(text, encoding="utf-8")
    assert fleet_cli.main([str(froot), "--out", "out", "--units", "units.toml", "--no-history"]) == 2
    assert message in capsys.readouterr().err
    assert not (tmp_path / "out" / "repos.jsonl").exists()
