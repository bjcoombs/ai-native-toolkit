"""fleet.py writes achievements.json, reads --targets and opens report.md on credit (issue #704)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from test_fleet import make_repo
from test_fleet_cli import TEAM_LEVELS, fleet_cli


def _seed_lower(entry: Path, date: str, repo: str) -> None:
    """Copy a history entry to an earlier date with ``repo`` one level lower on every row."""
    earlier = entry.parent / date
    shutil.copytree(entry, earlier)
    stamp = json.loads((earlier / "run.json").read_text(encoding="utf-8"))
    (earlier / "run.json").write_text(json.dumps({**stamp, "scan_date": date}), encoding="utf-8")
    data = json.loads((earlier / "fleet-data.json").read_text(encoding="utf-8"))
    data["run"]["scan_date"] = date
    for row in data["repos"]:
        if row["key"] == repo:
            for cell in row["rows"]:
                cell["signal"], cell["level"] = "nested_instructions.budget.claude_code.lines", cell["level"] - 1
    (earlier / "fleet-data.json").write_text(json.dumps(data), encoding="utf-8")


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "aroot"
    make_repo(root, "grpa/svc1", files={"CLAUDE.md": "rules\n"})
    make_repo(root, "grpb/lib1")
    return root


def test_a_run_credits_what_moved_since_the_history_and_reruns_byte_identical(tmp_path, monkeypatch, capsys):
    root = _root(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "levels.toml").write_text(TEAM_LEVELS, encoding="utf-8")
    (tmp_path / "targets.toml").write_text('[[target]]\nview = "repo"\nunit = "grpa/svc1"\nrow = "instructions"\n'
                                           'level = 1\n', encoding="utf-8")
    args = [str(root), "--levels", "levels.toml", "--history-dir", "hist", "--targets", "targets.toml"]
    assert fleet_cli.main([*args, "--out", "o1"]) == 0
    first = json.loads((tmp_path / "o1" / "achievements.json").read_text(encoding="utf-8"))
    assert len(first["runs"]) == 1 and first["achievements"] == []
    report = (tmp_path / "o1" / "report.md").read_text(encoding="utf-8")
    assert report.index("## What changed and who earned it") < report.index("## Activity and maturity")
    (today,) = (tmp_path / "hist" / "grpa+grpb").iterdir()
    _seed_lower(today, "2000-01-01", "grpa/svc1")
    capsys.readouterr()
    assert fleet_cli.main([*args, "--out", "o2"]) == 0
    assert "fleet: achievements: " in capsys.readouterr().err
    assert fleet_cli.main([*args, "--out", "o3"]) == 0
    assert (tmp_path / "o2" / "achievements.json").read_bytes() == (tmp_path / "o3" / "achievements.json").read_bytes()
    doc = json.loads((tmp_path / "o2" / "achievements.json").read_text(encoding="utf-8"))
    kinds = {(a["kind"], a["unit"]) for a in doc["achievements"]}
    assert ("level_up", "grpa/svc1") in kinds and ("target_hit", "grpa/svc1") in kinds
    assert "`grpa/svc1` hit its target of level 1" in (tmp_path / "o2" / "report.md").read_text(encoding="utf-8")


def test_no_history_writes_next_steps_and_a_note(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "levels.toml").write_text(TEAM_LEVELS, encoding="utf-8")
    assert fleet_cli.main([str(_root(tmp_path)), "--levels", "levels.toml", "--no-history", "--out", "o"]) == 0
    doc = json.loads((tmp_path / "o" / "achievements.json").read_text(encoding="utf-8"))
    assert doc["note"].startswith("this run is not kept in the run history")
    assert [s["unit"] for s in doc["nearest_level_ups"] if s["view"] == "repo"] == ["grpb/lib1"]


def test_a_bad_targets_file_exits_2_before_any_scan(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "levels.toml").write_text(TEAM_LEVELS, encoding="utf-8")
    (tmp_path / "t.toml").write_text('[[target]]\nunit = "x"\nrow = "nope"\nlevel = 1\n', encoding="utf-8")
    assert fleet_cli.main([str(tmp_path), "--levels", "levels.toml", "--targets", "t.toml", "--out", "out"]) == 2
    assert "error: --targets t.toml: target 1: 'row' must name a team row" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_an_achievements_failure_keeps_exit_0_and_clears_a_stale_file(tmp_path, monkeypatch, capsys):
    (tmp_path / "achievements.json").write_text("{}", encoding="utf-8")

    def boom(*_args: object, **_kwargs: object) -> None:
        raise ValueError("engine bug")
    monkeypatch.setattr(fleet_cli, "build_achievements", boom)
    assert fleet_cli._achievements(tmp_path, {}, {"scan_date": "2026-11-21"}, None, None, None, frozenset(), [], str) is None  # type: ignore[arg-type]
    assert "fleet: achievements: not written (ValueError: engine bug)" in capsys.readouterr().err
    assert not (tmp_path / "achievements.json").exists()


def test_runs_copied_into_the_history_replay_on_their_run_json_date(tmp_path, monkeypatch):
    # as a run history assembled by hand: each run made with --no-history, copied in, and
    # only run.json's scan_date set (fleet-data.json keeps the date the run was made on)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "levels.toml").write_text(TEAM_LEVELS, encoding="utf-8")
    before, after = tmp_path / "before", tmp_path / "after"
    for root, files in ((before, {}), (after, {"CLAUDE.md": "rules\n"})):
        make_repo(root, "grpa/svc1", files=files)
        make_repo(root, "grpa/svc2")
        make_repo(root, "grpb/lib1")
    for root, out, date in ((before, "r1", "2000-01-01"), (after, "r2", "2000-01-02")):
        assert fleet_cli.main([str(root), "--levels", "levels.toml", "--no-history", "--no-diagrams",
                               "--out", out]) == 0
        entry = tmp_path / "hist" / "org" / date
        shutil.copytree(tmp_path / out, entry)
        stamp = json.loads((entry / "run.json").read_text(encoding="utf-8"))
        (entry / "run.json").write_text(json.dumps({**stamp, "scan_date": date}), encoding="utf-8")
    assert fleet_cli.main([str(after), "--levels", "levels.toml", "--history-dir", "hist", "--history-org", "org",
                           "--no-diagrams", "--out", "r3"]) == 0
    doc = json.loads((tmp_path / "r3" / "achievements.json").read_text(encoding="utf-8"))
    assert doc["runs"][:2] == ["2000-01-01", "2000-01-02"]
    earned = {(a["run"], a["kind"], a["unit"]) for a in doc["achievements"]}
    assert ("2000-01-02", "level_up", "grpa/svc1") in earned
    # #772: no personal best, and no first unless --first-achievements asks for it
    assert not {kind for _run, kind, _unit in earned} & {"first", "personal_best"}
    assert fleet_cli.main([str(after), "--levels", "levels.toml", "--history-dir", "hist", "--history-org", "org",
                           "--no-diagrams", "--first-achievements", "--out", "r4"]) == 0
    doc = json.loads((tmp_path / "r4" / "achievements.json").read_text(encoding="utf-8"))
    assert ("2000-01-02", "first", "grpa/svc1") in {(a["run"], a["kind"], a["unit"]) for a in doc["achievements"]}
