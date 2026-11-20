"""Tests for the fleet run history (lib/fleet_history.py) and --compare wiring (issue #552)."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from lib import fleet_history as fh
from lib.fleet_history import (
    HistoryError,
    choose_entry,
    default_org,
    entry_conflict,
    entry_dir,
    history_root,
    latest_matching,
    persist,
)
from test_fleet import make_repo
from test_fleet_cli import fleet_cli

COMPARE_SCRIPT = Path(__file__).resolve().parents[2] / "fleet" / "scripts" / "fleet_compare.py"
STAMP = {"fleet_stamp_version": 1, "levels_version": None, "window": "6mo", "counts_only": True,
         "scans": None, "paths_hashed": False}


def _load_compare_cli() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fleet_compare_cli", COMPARE_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compare_cli = _load_compare_cli()


# --- locations -------------------------------------------------------------


def test_history_root_follows_xdg_and_ignores_a_relative_one(tmp_path):
    home = tmp_path / "home"
    assert history_root({"XDG_DATA_HOME": str(tmp_path / "x")}, home) == tmp_path / "x" / "ai-native-toolkit" / "fleet"
    default = home / ".local" / "share" / "ai-native-toolkit" / "fleet"
    assert history_root({}, home) == default
    assert history_root({"XDG_DATA_HOME": ""}, home) == default
    assert history_root({"XDG_DATA_HOME": "rel/dir"}, home) == default


def test_default_org_is_the_shared_first_segment_or_a_joined_list():
    assert default_org(["acme/svc", "acme/sub/lib"]) == "acme"
    assert default_org(["grpb/x", "grpa/y", "grpa/z"]) == "grpa+grpb"
    with pytest.raises(HistoryError):
        default_org([])


@pytest.mark.parametrize("name", ["..", ".", ".hidden", "-x", "a/b", "", "a b", "x" * 201])
def test_unsafe_names_are_refused(tmp_path, name):
    with pytest.raises(HistoryError):
        entry_dir(tmp_path, name, "2026-11-20")
    with pytest.raises(HistoryError):
        entry_dir(tmp_path, "org", name)


def test_a_symlink_on_the_way_is_refused(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "root").mkdir()
    (tmp_path / "root" / "org").symlink_to(tmp_path / "real")
    with pytest.raises(HistoryError, match="symlink"):
        entry_dir(tmp_path / "root", "org", "2026-11-20")
    assert entry_dir(tmp_path / "root", "other", "2026-11-20") == tmp_path / "root" / "other" / "2026-11-20"


# --- same-day entries ------------------------------------------------------


def _entry_with(tmp_path: Path, stamp: object) -> Path:
    entry = tmp_path / "entry"
    entry.mkdir()
    (entry / "run.json").write_text(json.dumps(stamp), encoding="utf-8")
    return entry


def test_entry_conflict_cases(tmp_path):
    assert entry_conflict(tmp_path / "absent", STAMP) is None
    (tmp_path / "file").write_text("x", encoding="utf-8")
    assert "not a directory" in (entry_conflict(tmp_path / "file", STAMP) or "")
    (tmp_path / "empty").mkdir()
    assert entry_conflict(tmp_path / "empty", STAMP) is None
    (tmp_path / "loose").mkdir()
    (tmp_path / "loose" / "repos.jsonl").write_text("", encoding="utf-8")
    assert "without a run.json" in (entry_conflict(tmp_path / "loose", STAMP) or "")
    same = _entry_with(tmp_path, STAMP)
    assert entry_conflict(same, STAMP) is None
    assert "different window" in (entry_conflict(same, {**STAMP, "window": "90d"}) or "")


@pytest.mark.parametrize("content", ["{", "[1]", json.dumps({**STAMP, "fleet_stamp_version": 7})])
def test_a_foreign_run_json_is_never_replaced(tmp_path, content):
    entry = tmp_path / "entry"
    entry.mkdir()
    (entry / "run.json").write_text(content, encoding="utf-8")
    assert entry_conflict(entry, STAMP) is not None


REPOS = frozenset({"org/a", "org/b"})


def _put(entry: Path, stamp: object, repos: frozenset[str] = REPOS) -> Path:
    entry.mkdir(parents=True, exist_ok=True)
    (entry / "run.json").write_text(json.dumps(stamp), encoding="utf-8")
    (entry / "repos.jsonl").write_text("".join(json.dumps({"repo": r}) + "\n" for r in sorted(repos)),
                                       encoding="utf-8")
    return entry


def test_choose_entry_moves_to_a_suffixed_sibling(tmp_path):
    root = tmp_path / "hist"
    first, passed = choose_entry(root, "org", "2026-11-20", STAMP, REPOS)
    assert first.name == "2026-11-20" and passed == []
    _put(first, STAMP)
    assert choose_entry(root, "org", "2026-11-20", STAMP, REPOS) == (first, [])
    other = {**STAMP, "window": "90d"}
    second, passed = choose_entry(root, "org", "2026-11-20", other, REPOS)
    assert second.name == "2026-11-20.2" and "different window" in passed[0]
    _put(second, other)
    assert choose_entry(root, "org", "2026-11-20", other, REPOS)[0] == second
    third, passed = choose_entry(root, "org", "2026-11-20", {**STAMP, "window": "1y"}, REPOS)
    assert third.name == "2026-11-20.3" and len(passed) == 2


def test_a_partial_rerun_never_replaces_the_full_fleet_entry(tmp_path):
    root = tmp_path / "hist"
    full = _put(root / "org" / "2026-11-20", STAMP)
    entry, passed = choose_entry(root, "org", "2026-11-20", STAMP, frozenset({"org/a"}))
    assert entry.name == "2026-11-20.2"
    assert "different repository set (2 repositories, this run 1)" in passed[0]
    assert (full / "repos.jsonl").read_text(encoding="utf-8").count("\n") == 2
    (full / "repos.jsonl").write_text("{bad\n", encoding="utf-8")
    assert "not readable" in (entry_conflict(full, STAMP, REPOS) or "")
    (full / "repos.jsonl").write_text("[1]\n", encoding="utf-8")
    assert "not readable" in (entry_conflict(full, STAMP, REPOS) or "")


def test_choose_entry_gives_up_past_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(fh, "MAX_SAME_DAY", 2)
    root = tmp_path / "hist"
    for name in ("2026-11-20", "2026-11-20.2"):
        (root / "org" / name).mkdir(parents=True)
        (root / "org" / name / "run.json").write_text("{", encoding="utf-8")
    with pytest.raises(HistoryError, match="same-day entries"):
        choose_entry(root, "org", "2026-11-20", STAMP, REPOS)


def test_latest_matching_picks_the_newest_matching_entry(tmp_path):
    org = tmp_path / "org"
    assert latest_matching(org, STAMP) is None
    other = {**STAMP, "window": "90d"}
    for name, stamp in (("2026-11-01", STAMP), ("2026-11-20", STAMP), ("2026-11-20.2", other),
                        ("2026-11-20.10", other), ("2026-12-01", other), ("notes", STAMP)):
        _put(org / name, stamp)
    (org / "2026-12-02").mkdir()  # no run.json
    (org / "2026-12-03").symlink_to(org / "2026-11-01")
    assert latest_matching(org, STAMP) == org / "2026-11-20"
    assert latest_matching(org, other) == org / "2026-12-01"
    assert latest_matching(org, {**STAMP, "window": "1y"}) is None
    # a newer partial rerun is passed over for the newest entry over the same set
    _put(org / "2026-11-20.3", STAMP, frozenset({"org/a"}))
    assert latest_matching(org, STAMP, REPOS) == org / "2026-11-20"
    assert latest_matching(org, STAMP, frozenset({"org/z"})) == org / "2026-11-20.3"


def test_persist_replaces_only_its_three_files(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    for name in fh.HISTORY_FILES:
        (out / name).write_text(json.dumps(STAMP) if name == "run.json" else f"{name}\n", encoding="utf-8")
    entry = tmp_path / "hist" / "org" / "2026-11-20"
    entry.mkdir(parents=True)
    (entry / "notes.txt").write_text("mine", encoding="utf-8")
    persist(out, entry, STAMP, frozenset())  # type: ignore[arg-type]
    assert sorted(p.name for p in entry.iterdir()) == ["groups.jsonl", "notes.txt", "repos.jsonl", "run.json"]
    assert (entry / "notes.txt").read_text(encoding="utf-8") == "mine"
    with pytest.raises(HistoryError):
        persist(out, entry, {**STAMP, "window": "1y"}, frozenset())  # type: ignore[arg-type]


def test_persist_leaves_no_temp_file_on_failure(tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    for name in fh.HISTORY_FILES:
        (out / name).write_text("x", encoding="utf-8")

    def boom(*_args: object) -> None:
        raise OSError("disk full")
    monkeypatch.setattr(fh.os, "rename", boom)
    entry = tmp_path / "org" / "entry"
    with pytest.raises(OSError):
        persist(out, entry, STAMP, frozenset())  # type: ignore[arg-type]
    assert list(entry.parent.iterdir()) == []


def test_a_new_entry_is_staged_and_renamed_in_whole(tmp_path):
    out = _out(tmp_path / "out", REPOS)
    entry = tmp_path / "org" / "2026-11-20"
    assert persist(out, entry, STAMP, REPOS) == entry  # type: ignore[arg-type]
    assert sorted(p.name for p in entry.parent.iterdir()) == ["2026-11-20"]
    assert sorted(p.name for p in entry.iterdir()) == ["groups.jsonl", "repos.jsonl", "run.json"]


def _out(out: Path, repos: frozenset[str], marker: str = "new") -> Path:
    out.mkdir(parents=True)
    (out / "run.json").write_text(json.dumps({**STAMP, "marker": marker}), encoding="utf-8")
    (out / "repos.jsonl").write_text("".join(json.dumps({"repo": r, "marker": marker}) + "\n"
                                             for r in sorted(repos)), encoding="utf-8")
    (out / "groups.jsonl").write_text(marker + "\n", encoding="utf-8")
    return out


@pytest.mark.parametrize("fail_at", ["repos.jsonl", "groups.jsonl", "run.json"])
def test_a_failed_same_day_replace_never_leaves_a_mixed_entry_that_is_selected(tmp_path, monkeypatch, fail_at):
    org = tmp_path / "org"
    entry = _put(org / "2026-11-20", {**STAMP, "marker": "old"})
    (entry / "groups.jsonl").write_text("old\n", encoding="utf-8")
    out = _out(tmp_path / "out", REPOS)
    real_replace = os.replace

    def flaky(src: object, dst: object) -> None:
        if Path(str(dst)) == entry / fail_at:
            raise OSError("disk full")
        real_replace(src, dst)  # type: ignore[arg-type]
    monkeypatch.setattr(fh.os, "replace", flaky)
    with pytest.raises(OSError):
        persist(out, entry, STAMP, REPOS)  # type: ignore[arg-type]
    monkeypatch.undo()
    assert [p.name for p in org.iterdir()] == ["2026-11-20"]  # no staging directory left
    groups = (entry / "groups.jsonl").read_text(encoding="utf-8")
    repos = (entry / "repos.jsonl").read_text(encoding="utf-8")
    run = entry / "run.json"
    if fail_at == "repos.jsonl":  # nothing new landed: the earlier run is whole and still selected
        assert groups == "old\n" and json.loads(run.read_text(encoding="utf-8"))["marker"] == "old"
        assert latest_matching(org, STAMP, REPOS) == entry
    else:  # part of the new run landed: the entry has no run.json, so it is never read as a run
        assert not run.exists()
        assert '"marker": "new"' in repos
        assert latest_matching(org, STAMP, REPOS) is None
        assert entry_conflict(entry, STAMP, REPOS) is not None


def test_a_replaced_same_day_entry_is_the_new_run_in_whole(tmp_path):
    org = tmp_path / "org"
    entry = _put(org / "2026-11-20", {**STAMP, "marker": "old"})
    (entry / "notes.txt").write_text("mine", encoding="utf-8")
    persist(_out(tmp_path / "out", REPOS), entry, STAMP, REPOS)  # type: ignore[arg-type]
    assert json.loads((entry / "run.json").read_text(encoding="utf-8"))["marker"] == "new"
    assert (entry / "groups.jsonl").read_text(encoding="utf-8") == "new\n"
    assert (entry / "notes.txt").read_text(encoding="utf-8") == "mine"
    assert sorted(p.name for p in org.iterdir()) == ["2026-11-20"]
    assert latest_matching(org, STAMP, REPOS) == entry


# --- the command -----------------------------------------------------------


@pytest.fixture
def froot2(tmp_path: Path) -> Path:
    root = tmp_path / "estate"
    make_repo(root, "acme/svc1", files={"CLAUDE.md": "a\n"})
    make_repo(root, "acme/svc2")
    return root


def _run(root: Path, out: Path, hist: Path, *extra: str) -> int:
    return int(fleet_cli.main([str(root), "--out", str(out), "--scans", "nested_instructions",
                               "--history-dir", str(hist), *extra]))


def _entries(hist: Path) -> list[Path]:
    return sorted(hist.glob("*/*"))


def test_a_run_is_kept_and_a_rerun_compares_with_no_movement(froot2, tmp_path, capsys):
    hist = tmp_path / "hist"
    assert _run(froot2, tmp_path / "o1", hist) == 0
    (entry,) = _entries(hist)
    assert entry.parent.name == "acme"
    assert sorted(p.name for p in entry.iterdir()) == [
        "fleet-data.json", "groups.jsonl", "repos.jsonl", "run.json"]
    assert (entry / "repos.jsonl").read_bytes() == (tmp_path / "o1" / "repos.jsonl").read_bytes()
    assert (entry / "fleet-data.json").read_bytes() == (tmp_path / "o1" / "fleet-data.json").read_bytes()
    saved = tmp_path / "saved"
    saved.mkdir()
    for p in entry.iterdir():
        (saved / p.name).write_bytes(p.read_bytes())

    assert _run(froot2, tmp_path / "o2", hist, "--compare", str(entry)) == 0
    comparison = json.loads((tmp_path / "o2" / "compare.json").read_text(encoding="utf-8"))
    assert comparison["comparable"] and not comparison["movement"]
    assert "fleet: compare: no movement" in capsys.readouterr().err
    assert _entries(hist) == [entry]  # the same-day entry was replaced, not duplicated

    # an improvement in one repository shows as a signal change
    claude = froot2 / "acme" / "svc2" / "svc2-main" / "CLAUDE.md"
    claude.write_text("b\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(claude.parent), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(claude.parent), "commit", "-q", "-m", "docs"], check=True)
    assert _run(froot2, tmp_path / "o3", hist, "--compare", str(saved)) == 0
    comparison = json.loads((tmp_path / "o3" / "compare.json").read_text(encoding="utf-8"))
    assert comparison["movement"]
    assert [r["repo"] for r in comparison["repos"]] == ["acme/svc2"]

    # a later run without --compare clears the stale compare.json it wrote
    assert _run(froot2, tmp_path / "o3", hist) == 0
    assert not (tmp_path / "o3" / "compare.json").exists()


def test_a_same_day_run_with_another_window_goes_beside_the_entry(froot2, tmp_path, capsys):
    hist = tmp_path / "hist"
    assert _run(froot2, tmp_path / "o1", hist) == 0
    (entry,) = _entries(hist)
    before = (entry / "run.json").read_bytes()
    assert _run(froot2, tmp_path / "o2", hist, "--window", "90d") == 0
    assert (entry / "run.json").read_bytes() == before
    err = capsys.readouterr().err
    assert "different window" in err and f"goes to {entry.name}.2" in err
    second = entry.parent / f"{entry.name}.2"
    assert (second / "run.json").read_bytes() == (tmp_path / "o2" / "run.json").read_bytes()

    # a bare --compare picks the latest entry whose stamp matches this run
    assert _run(froot2, tmp_path / "o3", hist, "--compare") == 0
    assert f"against {entry}" in capsys.readouterr().err
    assert json.loads((tmp_path / "o3" / "compare.json").read_text(encoding="utf-8"))["comparable"]
    assert _run(froot2, tmp_path / "o4", hist, "--window", "1y", "--compare") == 0
    assert "nothing compared" in capsys.readouterr().err
    assert not (tmp_path / "o4" / "compare.json").exists()


def test_a_partial_list_rerun_goes_beside_the_full_entry(froot2, tmp_path, capsys):
    hist = tmp_path / "hist"
    assert _run(froot2, tmp_path / "o1", hist) == 0
    (full,) = _entries(hist)
    before = (full / "repos.jsonl").read_bytes()
    listing = tmp_path / "list.txt"
    listing.write_text("acme/svc1\n", encoding="utf-8")
    assert int(fleet_cli.main(["--from-list", str(listing), str(froot2), "--out", str(tmp_path / "o2"),
                               "--scans", "nested_instructions", "--history-dir", str(hist)])) == 0
    assert (full / "repos.jsonl").read_bytes() == before
    assert [p.name for p in _entries(hist)] == [full.name, f"{full.name}.2"]
    assert "different repository set" in capsys.readouterr().err


def test_full_and_no_history_runs_are_not_kept(froot2, tmp_path, capsys):
    hist = tmp_path / "hist"
    assert _run(froot2, tmp_path / "o1", hist, "--full") == 0
    assert "--full output is not kept" in capsys.readouterr().err
    assert _run(froot2, tmp_path / "o2", hist, "--no-history") == 0
    assert not hist.exists()


def test_history_org_override_and_default_location(froot2, tmp_path, monkeypatch):
    hist = tmp_path / "hist"
    assert _run(froot2, tmp_path / "o1", hist, "--history-org", "team-x") == 0
    assert [p.parent.name for p in _entries(hist)] == ["team-x"]
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert fleet_cli.main([str(froot2), "--out", str(tmp_path / "o2"), "--scans", "nested_instructions"]) == 0
    assert len(_entries(tmp_path / "xdg" / "ai-native-toolkit" / "fleet")) == 1


@pytest.mark.parametrize("case", ["bad_org", "inside_target", "symlink", "unwritable", "bad_compare"])
def test_unusable_history_or_compare_exits_2_before_scanning(froot2, tmp_path, case, capsys):
    hist = tmp_path / "hist"
    extra: list[str] = []
    if case == "bad_org":
        extra = ["--history-org", ".."]
    elif case == "inside_target":
        hist = froot2 / "acme" / "svc1" / "svc1-main" / "hist"
    elif case == "symlink":
        (tmp_path / "real").mkdir()
        hist.mkdir()
        (hist / "acme").symlink_to(tmp_path / "real")
    elif case == "unwritable":
        hist.write_text("a file", encoding="utf-8")
    elif case == "bad_compare":
        extra = ["--compare", str(tmp_path / "absent")]
    assert _run(froot2, tmp_path / "out", hist, *extra) == 2
    assert capsys.readouterr().err.startswith("error:")
    assert not (tmp_path / "out" / "repos.jsonl").exists()


def test_compare_with_an_old_run_is_marked_not_comparable(froot2, tmp_path):
    old = tmp_path / "old"
    old.mkdir()
    (old / "repos.jsonl").write_text('{"fleet_row_version": 1, "repo": "acme/svc1", "group": "acme"}\n',
                                     encoding="utf-8")
    assert _run(froot2, tmp_path / "out", tmp_path / "hist", "--compare", str(old)) == 0
    comparison = json.loads((tmp_path / "out" / "compare.json").read_text(encoding="utf-8"))
    assert not comparison["comparable"]
    assert "before the run stamp" in comparison["reasons"][0]


def test_a_failed_history_write_keeps_the_out_results(froot2, tmp_path, monkeypatch, capsys):
    def boom(*_args: object) -> None:
        raise OSError("disk full")
    monkeypatch.setattr(fleet_cli, "persist", boom)
    assert _run(froot2, tmp_path / "out", tmp_path / "hist") == 0
    assert "history: not recorded" in capsys.readouterr().err
    assert (tmp_path / "out" / "run.json").exists()


# --- fleet_compare.py ------------------------------------------------------


def test_compare_script_exit_codes(froot2, tmp_path, capsys):
    assert _run(froot2, tmp_path / "a", tmp_path / "h", "--no-history") == 0
    assert _run(froot2, tmp_path / "b", tmp_path / "h", "--no-history") == 0
    assert _run(froot2, tmp_path / "c", tmp_path / "h", "--no-history", "--window", "90d") == 0
    capsys.readouterr()

    assert compare_cli.main([str(tmp_path / "a"), str(tmp_path / "b")]) == 0
    assert json.loads(capsys.readouterr().out)["movement"] is False
    assert compare_cli.main([str(tmp_path / "a"), str(tmp_path / "c"), "--write", str(tmp_path / "w.json")]) == 3
    assert "window changed" in json.loads((tmp_path / "w.json").read_text(encoding="utf-8"))["reasons"][0]
    assert compare_cli.main([str(tmp_path / "absent"), str(tmp_path / "b")]) == 2
    assert compare_cli.main([str(tmp_path / "a"), str(tmp_path / "b"), "--write",
                             str(tmp_path / "nodir" / "x.json")]) == 2
    stamp = json.loads((tmp_path / "b" / "run.json").read_text(encoding="utf-8"))
    (tmp_path / "b" / "run.json").write_text(json.dumps({**stamp, "fleet_stamp_version": 9}), encoding="utf-8")
    capsys.readouterr()
    assert compare_cli.main([str(tmp_path / "a"), str(tmp_path / "b")]) == 3
    later_refused = json.loads(capsys.readouterr().out)
    assert "later run:" in later_refused["reasons"][0] and later_refused["later"] is None
    (tmp_path / "a" / "run.json").unlink()
    assert compare_cli.main([str(tmp_path / "a"), str(tmp_path / "c")]) == 3
    assert "earlier run:" in json.loads(capsys.readouterr().out)["reasons"][0]
    del stamp["scan_date"]
    (tmp_path / "c" / "run.json").write_text(json.dumps(stamp), encoding="utf-8")
    assert compare_cli.main([str(tmp_path / "b"), str(tmp_path / "c")]) == 2
    assert "missing or malformed 'scan_date'" in capsys.readouterr().err


def test_compare_script_runs_standalone(froot2, tmp_path):
    assert _run(froot2, tmp_path / "a", tmp_path / "h", "--no-history") == 0
    done = subprocess.run([sys.executable, str(COMPARE_SCRIPT), str(tmp_path / "a"), str(tmp_path / "a")],
                          capture_output=True, text=True, env={**os.environ, "PYTHONPATH": ""}, check=False)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["comparable"]


# --- heatmap.py --compare goes through the same rule ------------------------


def _load_heatmap_cli() -> ModuleType:
    path = COMPARE_SCRIPT.with_name("heatmap.py")
    spec = importlib.util.spec_from_file_location("heatmap_cli_552", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_heatmap_compare_uses_the_fleet_comparison_rule(froot2, tmp_path):
    from test_fleet_groups import LEVELS_TOML
    heatmap = _load_heatmap_cli()
    maturity_model = tmp_path / "levels.toml"
    maturity_model.write_text(LEVELS_TOML, encoding="utf-8")

    def run(out: str, *extra: str) -> Path:
        assert fleet_cli.main([str(froot2), "--out", str(tmp_path / out), "--levels", str(maturity_model),
                               "--no-history", "--scans", "nested_instructions", *extra]) == 0
        return tmp_path / out

    first, other_window = run("a"), run("b", "--window", "90d")
    assert heatmap.main([str(other_window), "--compare", str(first), "--output", str(tmp_path / "w.svg")]) == 0
    assert "window changed" in (tmp_path / "w.svg").read_text(encoding="utf-8")

    (froot2 / "acme" / "svc2" / "svc2-main" / "CLAUDE.md").write_text("x\n", encoding="utf-8")
    import shutil
    shutil.rmtree(froot2 / "acme" / "svc2")
    fewer = run("c")
    assert heatmap.main([str(fewer), "--compare", str(first), "--output", str(tmp_path / "s.svg")]) == 0
    assert "measured set changed in acme" in (tmp_path / "s.svg").read_text(encoding="utf-8")

    # a bare groups.jsonl still compares levels only
    assert heatmap.main([str(fewer), "--compare", str(first / "groups.jsonl"),
                         "--output", str(tmp_path / "g.svg")]) == 0
    assert "measured set changed" not in (tmp_path / "g.svg").read_text(encoding="utf-8")


def test_compare_refuses_a_swallowed_root(froot2, tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        fleet_cli.main(["--compare", str(froot2), "--out", str(tmp_path / "o")])
    assert exc.value.code == 2
    assert "did it swallow ROOT?" in capsys.readouterr().err


def test_heatmap_reports_an_earlier_group_version_as_a_refusal(froot2, tmp_path):
    from test_fleet_groups import LEVELS_TOML
    heatmap = _load_heatmap_cli()
    maturity_model = tmp_path / "levels.toml"
    maturity_model.write_text(LEVELS_TOML, encoding="utf-8")
    for out in ("a", "b"):
        assert fleet_cli.main([str(froot2), "--out", str(tmp_path / out), "--levels", str(maturity_model),
                               "--no-history", "--scans", "nested_instructions"]) == 0
    groups = tmp_path / "a" / "groups.jsonl"
    groups.write_text(groups.read_text(encoding="utf-8").replace('"fleet_group_version":1', '"fleet_group_version":99'),
                      encoding="utf-8")
    assert heatmap.main([str(tmp_path / "b"), "--compare", str(tmp_path / "a"),
                         "--output", str(tmp_path / "v.svg")]) == 0
    assert "fleet_group_version 99" in (tmp_path / "v.svg").read_text(encoding="utf-8")


def test_view_check_refuses_a_one_side_stamped_pair(tmp_path):
    from lib.fleet_compare import view_check
    stamped, old, groups_only = tmp_path / "stamped", tmp_path / "old", tmp_path / "groups"
    for d in (stamped, old, groups_only):
        d.mkdir()
    (stamped / "run.json").write_text("{}", encoding="utf-8")
    (old / "repos.jsonl").write_text("", encoding="utf-8")
    (groups_only / "groups.jsonl").write_text("", encoding="utf-8")
    reasons, _ = view_check(stamped, old)
    assert reasons and "before the run stamp" in reasons[0]
    assert view_check(stamped, groups_only) == ([], [])


@pytest.mark.parametrize("flags", [["--no-history"], ["--full"], ["--no-history", "--compare", "EARLIER"]])
def test_an_unsafe_first_segment_runs_when_history_is_not_used(tmp_path, flags):
    root = tmp_path / "My Projects"
    make_repo(root, "svc")
    earlier = tmp_path / "earlier"
    base = [str(root), "--scans", "nested_instructions", "--history-dir", str(tmp_path / "hist")]
    assert fleet_cli.main([*base, "--out", str(earlier), "--no-history"]) == 0
    argv = [str(earlier) if f == "EARLIER" else f for f in flags]
    assert fleet_cli.main([*base, "--out", str(tmp_path / "out"), *argv]) == 0
    assert not (tmp_path / "hist").exists()


def test_an_unsafe_first_segment_with_history_exits_2_naming_the_fixes(tmp_path, capsys):
    root = tmp_path / "My Projects"
    make_repo(root, "svc")
    base = [str(root), "--scans", "nested_instructions", "--history-dir", str(tmp_path / "hist")]
    assert fleet_cli.main([*base, "--out", str(tmp_path / "o")]) == 2
    assert "--history-org" in capsys.readouterr().err
    assert fleet_cli.main([*base, "--out", str(tmp_path / "o2"), "--history-org", "mine"]) == 0


def test_view_check_raises_on_a_malformed_run(tmp_path):
    from lib.fleet_compare import view_check
    from lib.fleet_view import FleetViewError
    for name in ("now", "then"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "run.json").write_text("[1]", encoding="utf-8")
    with pytest.raises(FleetViewError):
        view_check(tmp_path / "now", tmp_path / "then")


def test_view_check_reports_unreadable_runs(tmp_path):
    from lib.fleet_compare import view_check
    for name in ("now", "then"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "run.json").write_text('{"fleet_stamp_version": 9}', encoding="utf-8")
    reasons, moved = view_check(tmp_path / "now", tmp_path / "then")
    assert "stamp version" in reasons[0] and moved == []
    assert view_check(tmp_path / "now", tmp_path / "absent") == ([], [])
