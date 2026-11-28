"""The calibration corpus (#842): lib/fleet/calibration_corpus.py and scripts/calibration_corpus.py."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

import calibration_corpus as cli
import lib.fleet.calibration_corpus as cc
from lib.fleet.runner import STATUS_FAILED, STATUS_OK, FleetRow, ScanOptions, Target, commit_state
from lib.scoring.level_tally import condition_ids, default_model
from lib.scoring.levels import parse_levels


def _model() -> Any:
    def row(rid: str) -> dict[str, Any]:
        return {"id": rid, "title": rid, "scope": "team", "aggregation": "min",
                "levels": [{"level": 1, "truth": ["docs"],
                            "criteria": [{"signal": f"fake.{rid}", "op": "eq", "value": True}]}]}
    return parse_levels({"version": "corpus-test", "rows": [row("a"), row("b"), row("c")]})


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@example.com", "-c", "user.name=T", *args],
                   check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")


def _clone(root: Path, name: str, fake: dict[str, bool]) -> Path:
    """A ghsync-layout clone whose committed ``fake.json`` is what the fake scan reads."""
    checkout = root / "org" / name / f"{name}-main"
    checkout.mkdir(parents=True)
    _git(checkout, "init", "-q")
    (checkout / "fake.json").write_text(json.dumps(fake), encoding="utf-8")
    _git(checkout, "add", "-A")
    _git(checkout, "commit", "-q", "-m", "one")
    return checkout


def _recommit(checkout: Path, fake: dict[str, bool]) -> None:
    (checkout / "fake.json").write_text(json.dumps(fake), encoding="utf-8")
    _git(checkout, "commit", "-q", "-am", "two")


def fake_scan(target: Target, scratch: Path, options: ScanOptions) -> FleetRow:
    commit = commit_state(target.checkout)
    if not (target.checkout / "fake.json").is_file():
        return {"fleet_row_version": 2, "repo": target.repo, "group": target.group, "status": STATUS_FAILED,
                "reason": "scan_error", "signals_schema_version": None, "scans": None, "active": None,
                "head_sha": None, "dirty": None, "dirty_reason": None}
    fake = json.loads((target.checkout / "fake.json").read_text(encoding="utf-8"))
    return {"fleet_row_version": 2, "repo": target.repo, "group": target.group, "status": STATUS_OK,
            "reason": None, "signals_schema_version": 1, "scans": {"fake": fake}, "active": None,
            "head_sha": commit["head_sha"], "dirty": commit["dirty"], "dirty_reason": None}


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, dict[str, Path]]:
    """Three clones seeded into an expected-state directory: a met in all, b in one, c in none."""
    monkeypatch.setattr(cli, "default_model", _model)
    root, out = tmp_path / "clones", tmp_path / "corpus"
    clones = {name: _clone(root, name, {"a": True, "b": name == "r1", "c": False}) for name in ("r1", "r2", "r3")}
    assert cli.main(["seed", str(root), "--corpus", str(out), "--jobs", "2"], scan=fake_scan) == 0
    return root, out, clones


def _run(args: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, list[str]]:
    code = cli.main(args, scan=fake_scan)
    return code, capsys.readouterr().out.splitlines()


# --- the discrimination rule ----------------------------------------------------------


def test_a_condition_met_everywhere_and_one_met_nowhere_are_both_non_discriminating() -> None:
    states = {"r1": {"a.1": "met", "b.1": "met", "c.1": "unmet"},
              "r2": {"a.1": "met", "b.1": "unmet", "c.1": "not_measured"}}
    rows = cc.discrimination(states)
    assert [(r["condition"], r["verdict"], r["met"], r["repos"]) for r in rows] == [
        ("a.1", cc.MET_IN_ALL, 2, 2), ("c.1", cc.MET_IN_NONE, 0, 2)]
    assert cc.discrimination_lines(rows) == ["non-discriminating a.1: met_in_all (2 of 2)",
                                            "non-discriminating c.1: met_in_none (0 of 2)"]
    assert cc.discrimination({}) == []


def test_a_condition_no_repository_measured_is_reported_apart() -> None:
    states = {"r1": {"d.1": "not_measured", "e.1": "not_measured"},
              "r2": {"d.1": "not_measured", "e.1": "unmet"}}
    assert [(r["condition"], r["verdict"]) for r in cc.discrimination(states)] == [
        ("d.1", cc.NOT_MEASURED_ANYWHERE), ("e.1", cc.MET_IN_NONE)]


def test_report_reads_the_expected_files_alone(corpus: tuple[Path, Path, dict[str, Path]],
                                               capsys: pytest.CaptureFixture[str]) -> None:
    _, out, _ = corpus
    code, lines = _run(["report", "--corpus", str(out)], capsys)
    assert code == 0
    assert lines == ["non-discriminating a.1: met_in_all (3 of 3)", "non-discriminating c.1: met_in_none (0 of 3)"]


# --- the diff -------------------------------------------------------------------------


def test_a_run_at_the_pinned_commits_diffs_clean(corpus: tuple[Path, Path, dict[str, Path]],
                                                 capsys: pytest.CaptureFixture[str]) -> None:
    root, out, _ = corpus
    code, lines = _run(["diff", str(root), "--corpus", str(out)], capsys)
    assert code == 0
    assert not [line for line in lines if not line.startswith("non-discriminating")]


def test_a_state_change_for_one_repository_is_one_line_naming_it_and_the_condition(
        corpus: tuple[Path, Path, dict[str, Path]], capsys: pytest.CaptureFixture[str]) -> None:
    root, out, _ = corpus
    expected = cc.load_corpus(out)
    key = cc.repo_key("org/r2")
    entry = next(e for e in expected if e["repo"] == key)
    entry["states"]["b.1"] = "met"  # the toolkit now reads r2's b.1 differently from the seed
    cc.write_expected(out, entry)
    code, lines = _run(["diff", str(root), "--corpus", str(out)], capsys)
    assert code == 1
    diff = [line for line in lines if not line.startswith("non-discriminating")]
    assert diff == [f"{key.removeprefix('h:')} b.1: expected met, got unmet"]


def test_a_clone_at_another_commit_is_refused(corpus: tuple[Path, Path, dict[str, Path]],
                                              capsys: pytest.CaptureFixture[str]) -> None:
    root, out, clones = corpus
    _recommit(clones["r3"], {"a": True, "b": True, "c": False})
    code, lines = _run(["diff", str(root), "--corpus", str(out)], capsys)
    assert code == 2
    assert lines == [f"refused {cc.repo_key('org/r3').removeprefix('h:')}: wrong_commit"]


def test_an_expected_file_with_no_clone_is_refused(tmp_path: Path, corpus: tuple[Path, Path, dict[str, Path]],
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    _, out, _ = corpus
    other = tmp_path / "elsewhere"
    _clone(other, "rx", {"a": True, "b": True, "c": False})  # not in the corpus: ignored
    code, lines = _run(["diff", str(other), "--corpus", str(out)], capsys)
    assert code == 2
    assert sorted(lines) == sorted(f"refused {cc.repo_key(f'org/{n}').removeprefix('h:')}: no_clone"
                                   for n in ("r1", "r2", "r3"))


def test_a_clone_that_fails_to_scan_is_refused(corpus: tuple[Path, Path, dict[str, Path]],
                                               capsys: pytest.CaptureFixture[str]) -> None:
    root, out, clones = corpus
    pinned = cc.pinned_key(clones["r1"])
    (clones["r1"] / "fake.json").unlink()  # fake_scan fails; HEAD is unchanged
    assert cc.pinned_key(clones["r1"]) == pinned
    code, lines = _run(["diff", str(root), "--corpus", str(out)], capsys)
    assert code == 2
    assert lines == [f"refused {cc.repo_key('org/r1').removeprefix('h:')}: scan_failed"]


def test_seed_refuses_a_clone_that_fails_to_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                 capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "default_model", _model)
    checkout = _clone(tmp_path / "clones", "r1", {})
    (checkout / "fake.json").unlink()
    code, lines = _run(["seed", str(tmp_path / "clones"), "--corpus", str(tmp_path / "c")], capsys)
    assert code == 2 and lines[0].endswith(": scan_failed")
    assert not (tmp_path / "c").exists()


def test_diff_lines_name_a_condition_one_side_lacks() -> None:
    assert cc.diff_states("h:00ff", {"a.1": "met"}, {"b.1": "unmet"}) == [
        "00ff a.1: expected met, got absent", "00ff b.1: expected absent, got unmet"]


# --- the files ------------------------------------------------------------------------


def test_the_files_name_no_repository_and_hold_no_commit(corpus: tuple[Path, Path, dict[str, Path]]) -> None:
    _, out, clones = corpus
    for path in out.glob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert "org" not in text and "r1" not in text
        assert all(str(commit_state(c)["head_sha"]) not in text for c in clones.values())
        data = json.loads(text)
        assert data["repo"].startswith("h:") and data["pinned"].startswith("h:")
        assert path.name == cc.file_name(data["repo"])
        assert data["levels_version"] == "corpus-test" and set(data["states"]) == {"a.1", "b.1", "c.1"}


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d.update(states={"a.1": "maybe"}), "states must map"),
    (lambda d: d.update(corpus_version=99), "corpus_version"),
    (lambda d: d.pop("pinned"), "fields must be"),
    (lambda d: d.update(repo=5), "must be strings"),
    (lambda d: d.update(repo="h:0000000000000000"), "file name must be"),
])
def test_a_malformed_expected_file_is_an_error(tmp_path: Path, mutate: Any, message: str) -> None:
    entry = {"corpus_version": 1, "repo": "h:00ff", "pinned": "h:11ee", "levels_version": "v",
             "states": {"a.1": "met"}}
    mutate(entry)
    tmp_path.joinpath("00ff.json").write_text(json.dumps(entry), encoding="utf-8")
    with pytest.raises(cc.CorpusError, match=message):
        cc.load_corpus(tmp_path)


def test_unreadable_json_and_a_missing_corpus(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cc.load_corpus(tmp_path / "none") == []
    code = cli.main(["diff", str(tmp_path), "--corpus", str(tmp_path / "none")])
    assert code == 2 and "no expected-state files" in capsys.readouterr().err
    assert cli.main(["report", "--corpus", str(tmp_path / "none")]) == 2
    assert "no expected-state files" in capsys.readouterr().err
    good = tmp_path / "good"
    cc.write_expected(good, {"corpus_version": 1, "repo": "h:00ff", "pinned": "h:11ee", "levels_version": "v",
                             "states": {"a.1": "met"}})
    assert cli.main(["diff", str(tmp_path / "missing"), "--corpus", str(good)]) == 2
    assert "is not a directory" in capsys.readouterr().err
    tmp_path.joinpath("bad.json").write_text("{", encoding="utf-8")
    assert cli.main(["report", "--corpus", str(tmp_path)]) == 2


def test_the_default_model_states_cover_every_condition() -> None:
    model = default_model()
    row: FleetRow = {"fleet_row_version": 2, "repo": "o/r", "group": "o", "status": STATUS_OK, "reason": None,
                     "signals_schema_version": 1, "scans": {}, "active": None, "head_sha": "0" * 40,
                     "dirty": False, "dirty_reason": None}
    states = cc.states_of(model, row)
    assert set(states) == set(condition_ids(model))
    assert set(states.values()) <= {"met", "unmet", "not_applicable", "not_measured", "declined"}


def test_seed_refuses_a_root_with_no_clones_and_names_each_key_it_writes(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "default_model", _model)
    (tmp_path / "typo").mkdir()
    assert cli.main(["seed", str(tmp_path / "typo"), "--corpus", str(tmp_path / "c")], scan=fake_scan) == 2
    assert "no clones under" in capsys.readouterr().err and not (tmp_path / "c").exists()
    for name in ("r1", "r2"):
        _clone(tmp_path / "clones", name, {"a": True})
    code, lines = _run(["seed", str(tmp_path / "clones"), "--corpus", str(tmp_path / "c")], capsys)
    keys = sorted(cc.repo_key(f"org/{n}").removeprefix("h:") for n in ("r1", "r2"))
    assert code == 0 and lines[:2] == [f"wrote {k}" for k in keys]


# --- the platform pass and the row split (#1324) ---------------------------------------


def test_platform_blocks_join_before_the_states_are_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                         capsys: pytest.CaptureFixture[str]) -> None:
    """Offline, ``c`` reads unmet; the stub platform pass sets it in r1 only, so its states move."""
    monkeypatch.setattr(cli, "default_model", _model)
    for name in ("r1", "r2"):
        _clone(tmp_path / "clones", name, {"a": True, "b": False, "c": False})
    seen: list[list[str]] = []

    def platform(rows: list[FleetRow], targets: Any, options: ScanOptions) -> list[FleetRow]:
        seen.append(sorted(r["repo"] for r in rows))
        assert options.window == cc.WINDOW
        return [{**r, "scans": {"fake": {**(r["scans"] or {})["fake"], "c": r["repo"] == "org/r1"}}}
                for r in rows if r["repo"] == "org/r1"]

    out = tmp_path / "c"
    assert cli.main(["seed", str(tmp_path / "clones"), "--corpus", str(out), "--platform"],
                    scan=fake_scan, platform=platform) == 0
    assert seen == [["org/r1", "org/r2"]]
    states = {e["repo"]: e["states"]["c.1"] for e in cc.load_corpus(out)}
    assert states == {cc.repo_key("org/r1"): "met", cc.repo_key("org/r2"): "unmet"}
    capsys.readouterr()
    # Without --platform the stub is not called, and the diff names the platform-read condition.
    code, lines = _run(["diff", str(tmp_path / "clones"), "--corpus", str(out)], capsys)
    assert code == 1 and seen == [["org/r1", "org/r2"]]
    assert [line for line in lines if " c.1:" in line and not line.startswith("non-")] == [
        f"{cc.repo_key('org/r1').removeprefix('h:')} c.1: expected met, got unmet"]


def test_the_row_split_counts_every_state_of_the_rows_conditions(
        corpus: tuple[Path, Path, dict[str, Path]], capsys: pytest.CaptureFixture[str]) -> None:
    states = {"r1": {"x.1": "met", "x.2": "not_measured", "y.1": "met"},
              "r2": {"x.1": "unmet", "x.2": "met"}, "r3": {"x.1": "unmet"}}
    split = cc.row_split(states, "x")
    assert list(split) == ["x.1", "x.2"]
    assert split["x.1"] == {"met": 1, "unmet": 2, "not_applicable": 0, "not_measured": 0, "declined": 0}
    assert split["x.2"]["met"] == 1 and split["x.2"]["not_measured"] == 1
    assert cc.split_lines(split, 3) == ["x.1: met 1, unmet 2 (of 3)", "x.2: met 1, not_measured 1 (of 3)"]
    _, out, _ = corpus
    code, lines = _run(["report", "--corpus", str(out), "--row", "b"], capsys)
    assert code == 0 and lines[0] == "b.1: met 1, unmet 2 (of 3)"
    assert cli.main(["report", "--corpus", str(out), "--row", "backlog_ready"]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "no expected-state file holds a condition of row 'backlog_ready'" in captured.err
    expected = cc.load_corpus(out)
    del expected[0]["states"]["b.1"]  # one file seeded before the row
    cc.write_expected(out, expected[0])
    code, lines = _run(["report", "--corpus", str(out), "--row", "b"], capsys)
    assert code == 0 and lines[1] == "1 of 3 expected-state files hold no condition of b: reseed them"
