"""Under --hash-paths no repository id appears in any file under --out (issue #909)."""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
from pathlib import Path
from types import ModuleType

import jsonschema
import pytest

import lib.fleet.runner as fleet
from lib.counts_only.content_free import hash_value, is_hashed
from lib.fleet.runner import ScanOptions, Target
from lib.fleet.fleet_data import Privacy
from lib.fleet.fleet_hashed import public_freshness, public_groups, public_ids, public_rows
from test_fleet import make_checkout, make_repo
from test_fleet_cli import fleet_cli

ROOT_DIR = Path(__file__).resolve().parents[3]
LEVELS = ROOT_DIR / "skills" / "assess" / "levels" / "default.toml"
SCHEMAS = ROOT_DIR / "skills" / "fleet" / "references"
NEXT_STEPS = ROOT_DIR / "skills" / "fleet" / "scripts" / "fleet_next_steps.py"
REPOS = ("zqorg/zqalpha", "zqorg/zqbeta", "zqorg/zqgamma", "zqorg/zqteam/zqdelta")
NAMES = ("zqalpha", "zqbeta", "zqgamma", "zqdelta", "zqorg", "zqteam")
DESCRIBES = '[[describes]]\npath = "docs/alpha.md"\nrepo = "zqorg/zqalpha"\nscope = ["src/"]\n'


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


next_steps_cli = _load(NEXT_STEPS, "fleet_next_steps_cli")


@pytest.fixture
def zroot(tmp_path: Path) -> Path:
    """Three repositories with names no output prose holds; zqgamma's checkout is broken (failed row)."""
    root = tmp_path / "estate"
    make_repo(root, "zqorg/zqalpha", files={
        "CLAUDE.md": "Run `make test`.\n", "src/app.py": "x = 1\n",
        ".github/workflows/ci.yml": "on: [push]\njobs:\n  t:\n    runs-on: x\n    steps:\n      - run: pytest\n",
    })
    make_repo(root, "zqorg/zqbeta", files={"docs/alpha.md": "# Alpha\n", ".assess/config.toml": DESCRIBES})
    (root / "zqorg" / "zqgamma" / "zqgamma-main").mkdir(parents=True)
    make_repo(root, "zqorg/zqteam/zqdelta")
    return root


def _files(out: Path) -> dict[str, str]:
    return {p.relative_to(out).as_posix(): p.read_text(encoding="utf-8")
            for p in sorted(out.rglob("*")) if p.is_file()}


SHA_RE = re.compile(r"\b[0-9a-f]{40}\b")


def _leaks(files: dict[str, str]) -> list[str]:
    """Every repository or group name in a path or a file, and any 40-hex commit sha other
    than ``run.json``'s ``toolkit_commit`` (the toolkit's own, not a target's)."""
    toolkit = json.loads(files["run.json"])["toolkit_commit"] if "run.json" in files else None
    names = [f"{path}: {name}" for path, text in files.items() for name in NAMES
             if name in path or name in text]
    shas = [f"{path}: {sha}" for path, text in files.items() for sha in SHA_RE.findall(text) if sha != toolkit]
    return names + shas


def _jsonl(text: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _run(root: Path, out: Path, hist: Path, *extra: str, pages: list[str] | None = None) -> None:
    assert fleet_cli.main([str(root), "--out", str(out), "--levels", str(LEVELS),
                           "--history-dir", str(hist), *extra]) == 0
    assert next_steps_cli.main([str(out), "--levels", str(LEVELS), *(pages or [])]) == 0


@pytest.mark.parametrize("flag", [["--hash-paths"], ["--hash-salt", "s909"]])
def test_no_repository_id_in_any_output_file(zroot, tmp_path, flag):
    out = tmp_path / "out"
    _run(zroot, out, tmp_path / "hist", *flag)
    files = _files(out)
    for expected in ("repos.jsonl", "groups.jsonl", "level.json", "fleet-data.json", "doc_freshness.jsonl",
                     "agent_clusters.json", "report.md", "quadrant.svg", "teams.md"):
        assert expected in files
    assert _leaks(files) == []
    kept = _files(tmp_path / "hist")  # the entry's directory is <org>/<date>, outside --out
    assert kept and [n for text in kept.values() for n in NAMES if n in text] == []


def test_hashed_ids_join_across_the_files_and_reruns_are_byte_identical(zroot, tmp_path):
    out, again = tmp_path / "out", tmp_path / "again"
    _run(zroot, out, tmp_path / "hist", "--hash-salt", "s909", "--no-history")
    _run(zroot, again, tmp_path / "hist", "--hash-salt", "s909", "--no-history")
    files = _files(out)
    assert files == _files(again)
    expected = {hash_value(repo, "s909") for repo in REPOS}
    rows = _jsonl(files["repos.jsonl"])
    ids = {row["repo"] for row in rows}
    assert ids == expected
    scanned = {row["repo"] for row in rows if row["status"] == "ok"}
    assert set(json.loads(files["level.json"])["repos"]) == scanned  # level.json holds scanned rows
    assert {repo["key"] for repo in json.loads(files["fleet-data.json"])["repos"]} == ids
    unknown = {r for g in _jsonl(files["groups.jsonl"]) for lv in g["levels"] for r in lv["unknown_repos"]}
    assert unknown and unknown <= ids
    fresh = _jsonl(files["doc_freshness.jsonl"])
    assert {(f["docs_repo"], f["repo"]) for f in fresh} == {
        (hash_value("zqorg/zqbeta", "s909"), hash_value("zqorg/zqalpha", "s909"))}
    groups = {row["group"] for row in rows}
    assert groups == {hash_value(g, "s909") for g in ("zqorg", "zqorg/zqteam")}
    assert groups == {g["group"] for g in _jsonl(files["groups.jsonl"])}
    assert {f"teams/{g.replace(':', '-')}.md" for g in groups} <= set(files)
    for name in ("repos.jsonl", "groups.jsonl"):
        schema = json.loads((SCHEMAS / name.replace(".jsonl", ".schema.json")).read_text(encoding="utf-8"))
        for line in _jsonl(files[name]):
            jsonschema.validate(line, schema)
    jsonschema.validate(json.loads(files["level.json"]),
                        json.loads((SCHEMAS / "level.schema.json").read_text(encoding="utf-8")))


def test_a_fixed_salt_rerun_compares_against_the_kept_hashed_run(zroot, tmp_path):
    hist = tmp_path / "hist"
    _run(zroot, tmp_path / "o1", hist, "--hash-salt", "s909")
    _run(zroot, tmp_path / "o2", hist, "--hash-salt", "s909", "--compare")
    assert _leaks(_files(tmp_path / "o2")) == []  # compare.json and slope.svg included
    comparison = json.loads((tmp_path / "o2" / "compare.json").read_text(encoding="utf-8"))
    assert comparison["comparable"] is True
    assert comparison["repositories"]["added"] == [] and comparison["repositories"]["removed"] == []
    assert len(list(hist.glob("*/*"))) == 1  # the same-day entry over the same hashed set is replaced


def test_default_counts_only_output_keeps_repository_ids(zroot, tmp_path):
    out = tmp_path / "out"
    _run(zroot, out, tmp_path / "hist", "--no-history")
    rows = _jsonl(_files(out)["repos.jsonl"])
    assert [row["repo"] for row in rows] == list(REPOS)


def _head(checkout: Path) -> str:
    return subprocess.run(["git", "-C", str(checkout), "rev-parse", "HEAD"], check=True, capture_output=True,
                          encoding="utf-8").stdout.strip()


def test_the_commit_sha_is_hashed_and_dirty_kept_under_a_salt(zroot, tmp_path):
    """#907: a commit sha finds its repository by search, so --hash-paths hashes it with the id;
    the default counts-only output keeps it in clear, and dirty is kept either way."""
    alpha = zroot / "zqorg" / "zqalpha" / "zqalpha-main"
    (alpha / "CLAUDE.md").unlink()  # dirty: scored from HEAD
    heads = [_head(c) for c in (alpha, zroot / "zqorg" / "zqbeta" / "zqbeta-main")]
    out, plain = tmp_path / "out", tmp_path / "plain"
    _run(zroot, out, tmp_path / "hist", "--hash-salt", "s907")
    _run(zroot, plain, tmp_path / "hist2", "--no-history")
    texts = {**_files(out), **{f"hist/{k}": v for k, v in _files(tmp_path / "hist").items()}}
    assert [f"{path}: {head[:12]}" for path, text in texts.items() for head in heads if head[:12] in text] == []
    rows = {row["repo"]: row for row in _jsonl(_files(out)["repos.jsonl"])}
    hashed_alpha = rows[hash_value("zqorg/zqalpha", "s907")]
    assert (hashed_alpha["head_sha"], hashed_alpha["dirty"]) == (hash_value(heads[0], "s907"), True)
    commits = json.loads(_files(out)["level.json"])["commits"]
    assert commits[hash_value("zqorg/zqalpha", "s907")] == {"head_sha": hash_value(heads[0], "s907"), "dirty": True}
    clear = {row["repo"]: row for row in _jsonl(_files(plain)["repos.jsonl"])}
    assert (clear["zqorg/zqalpha"]["head_sha"], clear["zqorg/zqalpha"]["dirty"]) == (heads[0], True)
    assert clear["zqorg/zqgamma"]["head_sha"] is None  # a failed row has no commit


def test_public_helpers_are_the_identity_without_a_salt():
    row: fleet.FleetRow = {"fleet_row_version": 2, "repo": "g/r", "group": "g", "status": "ok", "reason": None,
                           "signals_schema_version": 1, "scans": {}, "active": None}
    assert public_rows([row], None) == [row]
    assert public_groups([], None) == []
    assert public_freshness([], None) == []
    assert public_ids(["g/r"], None) == frozenset({"g/r"})
    hashed = public_rows([row, {**row, "repo": "g/a"}], "s")
    assert [r["repo"] for r in hashed] == sorted(hash_value(r, "s") for r in ("g/r", "g/a"))
    assert all(r["group"] == hash_value("g", "s") for r in hashed)


def test_privacy_key_keeps_an_id_the_outputs_already_hashed():
    """A stored repos.jsonl row is already hashed, so the re-score joins it to fleet-data keys."""
    hashed = hash_value("g/r", "s")
    assert is_hashed(hashed) and not is_hashed("g/r")
    assert Privacy(salt="s").key(hashed) == hashed
    assert Privacy(salt="s").key("g/r") == hashed


def test_no_stderr_log_is_written_under_hashing(tmp_path, monkeypatch):
    script = tmp_path / "fake_signals.py"
    script.write_text("import sys\nsys.stderr.write('/x/zqalpha boom')\nsys.exit(4)\n", encoding="utf-8")
    monkeypatch.setattr(fleet, "SIGNALS_SCRIPT", script)
    logs = tmp_path / "out" / fleet.LOGS_DIR
    target = Target("zqorg/zqalpha", make_checkout(tmp_path / "r"))
    row = fleet.scan_target(target, tmp_path, ScanOptions(log_dir=logs, hash_salt="s"))
    assert row["reason"] == "scan_exit_4"
    assert not logs.exists() or not any(logs.rglob("*"))



def _kept_earlier(hist: Path, date: str = "2026-01-05") -> None:
    """Move the one kept entry to an earlier date, so the next run replays it."""
    (entry,) = sorted(hist.glob("*/*"))
    moved = entry.parent / date
    entry.rename(moved)
    stamp = json.loads((moved / "run.json").read_text(encoding="utf-8"))
    (moved / "run.json").write_text(json.dumps({**stamp, "scan_date": date}), encoding="utf-8")


@pytest.mark.parametrize(("salt", "rescored"), [("s909", True), ("other", False)])
def test_a_version_bump_rescores_a_hashed_entry_only_under_the_same_salt(zroot, tmp_path, salt, rescored):
    hist, bumped = tmp_path / "hist", tmp_path / "bumped.toml"
    text = LEVELS.read_text(encoding="utf-8")
    version = text.split('version = "', 1)[1].split('"', 1)[0]
    bumped.write_text(text.replace(f'version = "{version}"', f'version = "{version}-bumped"', 1), encoding="utf-8")
    _run(zroot, tmp_path / "o1", hist, "--hash-salt", "s909")
    _kept_earlier(hist)
    assert fleet_cli.main([str(zroot), "--out", str(tmp_path / "o2"), "--levels", str(bumped), "--all-scans",
                           "--history-dir", str(hist), "--hash-salt", salt]) == 0
    doc = json.loads((tmp_path / "o2" / "achievements.json").read_text(encoding="utf-8"))
    assert bool(doc["rescored_runs"]) is rescored


ORG_ROW = """

[[rows]]
id = "org2fa"
title = "Two-factor"
scope = "org"

  [[rows.levels]]
  level = 1
  truth = ["governance"]
  criteria = [{ signal = "two_factor.value", op = "eq", value = "on" }]
"""


def test_team_pages_rejoin_settings_through_the_run_salt(zroot, tmp_path, capsys):
    levels = tmp_path / "levels.toml"
    levels.write_text(LEVELS.read_text(encoding="utf-8") + ORG_ROW, encoding="utf-8")
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps([{"scope": "zqorg", "provider": "github",
                                     "two_factor": {"value": "on", "reason": None}}]), encoding="utf-8")
    out = tmp_path / "out"
    assert fleet_cli.main([str(zroot), "--out", str(out), "--levels", str(levels), "--no-history",
                           "--hash-salt", "s909", "--settings", str(settings)]) == 0
    pages = [str(out), "--levels", str(levels), "--settings", str(settings)]
    assert next_steps_cli.main([*pages, "--hash-salt", "s909"]) == 0
    files = _files(out)
    assert _leaks(files) == []
    groups = _jsonl(files["groups.jsonl"])
    assert {g["settings_scope"] for g in groups} == {hash_value("zqorg", "s909")}  # nested: the ancestor's
    assert all(lv["level"] == 1 for g in groups for lv in g["levels"] if lv["id"] == "org2fa")
    assert next_steps_cli.main(pages) == 2
    assert "pass the --hash-salt" in capsys.readouterr().err
    assert next_steps_cli.main([*pages, "--hash-salt", "wrong"]) == 2  # no fragment joins: levels disagree
