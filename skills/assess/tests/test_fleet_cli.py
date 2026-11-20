"""End-to-end tests for skills/fleet/scripts/fleet.py over real assess_signals runs (issue #567)."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import jsonschema
import pytest

from lib.counts_only import classify
from lib.fleet_schema import build_schema
from test_fleet import make_repo

FLEET_SCRIPT = Path(__file__).resolve().parents[2] / "fleet" / "scripts" / "fleet.py"

RUBRIC = """\
version = "fixture-1"

[[rows]]
id = "org2fa"
title = "Two-factor"
scope = "org"

  [[rows.levels]]
  level = 1
  criteria = [{ signal = "two_factor.value", op = "eq", value = "on" }]
"""


def _load_cli() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fleet_cli", FLEET_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fleet_cli = _load_cli()


@pytest.fixture(scope="module")
def froot(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The contract's FROOT: grpa/svc1, grpa/svc2, grpb/lib1 in the ghsync layout."""
    root = tmp_path_factory.mktemp("fleet") / "froot"
    make_repo(root, "grpa/svc1", files={
        "CLAUDE.md": "".join(f"line {i}\n" for i in range(20)),
        ".claude-plugin/marketplace.json": json.dumps({"name": "mkt-a", "plugins": []}),
    })
    make_repo(root, "grpa/svc2", files={
        ".claude-plugin/marketplace.json": json.dumps({"name": "mkt-b", "plugins": []}),
    })
    make_repo(root, "grpb/lib1", files={
        ".claude/settings.json": json.dumps(
            {"extraKnownMarketplaces": {"mkt-a": {"source": {"source": "git", "url": "grpa/svc1"}}}}),
    })
    return root


def _snapshot(root: Path) -> dict[str, str]:
    """Every checkout's status and full file listing (including .git), to prove no writes."""
    state: dict[str, str] = {}
    # The listing is read first and this status runs with optional locks off:
    # a plain ``git status`` would itself refresh a racy .git/index.
    for checkout in sorted(root.glob("*/*/*-main")):
        listing = sorted(f"{p.relative_to(checkout)}:{p.stat().st_mtime_ns}"
                         for p in checkout.rglob("*") if p.is_file())
        status = subprocess.run(["git", "-C", str(checkout), "status", "--porcelain", "--ignored"],
                                capture_output=True, text=True, check=True,
                                env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"}).stdout
        state[str(checkout)] = status + "\n".join(listing)
    return state


def _rows(out: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in (out / "repos.jsonl").read_text(encoding="utf-8").splitlines()]


def _long_strings(value: object, pattern: str = "") -> list[str]:
    """Strings over 40 characters whose field the counts-only rules do not classify as a path."""
    if isinstance(value, str):
        return [pattern] if len(value) > 40 and classify(pattern) is None else []
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in _long_strings(v, f"{pattern}.{k}" if pattern else k)]
    if isinstance(value, list):
        return [s for v in value for s in _long_strings(v, pattern)]
    return []


def test_root_run_is_byte_identical_and_matches_the_list_run(froot, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "rub.toml").write_text(RUBRIC, encoding="utf-8")
    before = _snapshot(froot)

    assert fleet_cli.main([str(froot), "--rubric", "rub.toml", "--out", "out1"]) == 0
    assert fleet_cli.main([str(froot), "--rubric", "rub.toml", "--out", "out2", "--jobs", "1"]) == 0
    listing = tmp_path / "list.txt"
    listing.write_text("".join(f"{c}\n" for c in sorted(froot.glob("*/*/*-main"))), encoding="utf-8")
    assert fleet_cli.main(["--from-list", str(listing), "--rubric", "rub.toml", "--out", "out3"]) == 0

    first = (tmp_path / "out1" / "repos.jsonl").read_bytes()
    assert first == (tmp_path / "out2" / "repos.jsonl").read_bytes()
    assert first == (tmp_path / "out3" / "repos.jsonl").read_bytes()
    assert sorted(p.name for p in (tmp_path / "out1").iterdir()) == ["groups.jsonl", "repos.jsonl", "run.json"]
    groups = (tmp_path / "out1" / "groups.jsonl").read_bytes()
    assert groups == (tmp_path / "out2" / "groups.jsonl").read_bytes()
    assert groups == (tmp_path / "out3" / "groups.jsonl").read_bytes()
    stamp = (tmp_path / "out1" / "run.json").read_bytes()
    assert stamp == (tmp_path / "out2" / "run.json").read_bytes()
    assert stamp == (tmp_path / "out3" / "run.json").read_bytes()
    assert json.loads(stamp)["rubric_version"] == "fixture-1"
    assert str(froot).encode() not in stamp
    assert _snapshot(froot) == before, "the fleet run wrote into a target"

    rows = _rows(tmp_path / "out1")
    assert [(r["repo"], r["group"], r["status"]) for r in rows] == [
        ("grpa/svc1", "grpa", "ok"), ("grpa/svc2", "grpa", "ok"), ("grpb/lib1", "grpb", "ok")]
    schema = build_schema()
    for row in rows:
        jsonschema.validate(row, schema)
        assert _long_strings(row) == []
        assert str(froot) not in json.dumps(row)
    svc1 = rows[0]["scans"]
    assert isinstance(svc1, dict)
    assert svc1["shared_tooling"]["marketplace_ids"] == ["marketplace:mkt-a"]
    assert svc1["nested_instructions"]["budget"]["claude_code"]["files"] == ["CLAUDE.md"]


def test_a_broken_checkout_is_a_failed_row_and_the_run_completes(froot, tmp_path, capsys):
    root = tmp_path / "froot"
    shutil.copytree(froot, root, symlinks=True)
    lib1 = root / "grpb" / "lib1" / "lib1-main"
    shutil.rmtree(lib1 / ".git")
    (lib1 / ".git").write_text("", encoding="utf-8")

    assert fleet_cli.main([str(root), "--out", str(tmp_path / "out")]) == 0

    rows = {r["repo"]: r for r in _rows(tmp_path / "out")}
    assert rows["grpb/lib1"]["status"] == "failed"
    assert rows["grpb/lib1"]["reason"] == "not_a_git_work_tree"
    assert rows["grpa/svc1"]["status"] == rows["grpa/svc2"]["status"] == "ok"
    assert rows["grpa/svc1"]["scans"]
    assert "1 failed" in capsys.readouterr().err


def test_hash_salt_hashes_paths_and_full_keeps_text(froot, tmp_path):
    assert fleet_cli.main([str(froot), "--out", str(tmp_path / "h"), "--hash-salt", "s",
                           "--scans", "shared_tooling"]) == 0
    hashed = _rows(tmp_path / "h")[0]["scans"]
    assert isinstance(hashed, dict)
    assert hashed["shared_tooling"]["marketplaces"][0]["path"] != ".claude-plugin/marketplace.json"
    assert fleet_cli.main([str(froot), "--out", str(tmp_path / "f"), "--full",
                           "--scans", "shared_tooling", "--window", "90d"]) == 0
    full = _rows(tmp_path / "f")[0]["scans"]
    assert isinstance(full, dict)
    assert full["shared_tooling"]["marketplaces"][0]["path"] == ".claude-plugin/marketplace.json"


@pytest.mark.parametrize("argv", [
    ["--out", "o"],
    ["ROOT", "--out", "o", "--full", "--hash-paths"],
    ["ROOT", "--out", "o", "--hash-salt", ""],
    ["ROOT", "--out", "o", "--jobs", "0"],
])
def test_usage_errors(froot, argv):
    with pytest.raises(SystemExit) as exc:
        fleet_cli.main([str(froot) if a == "ROOT" else a for a in argv])
    assert exc.value.code == 2


@pytest.mark.parametrize("case", ["no_root", "no_list", "empty", "rubric", "scans", "window", "inside"])
def test_refusals_exit_2(froot, tmp_path, case, capsys):
    (tmp_path / "bad.toml").write_text('version = "x"\n[[rows]]\nid = "r"\n', encoding="utf-8")
    (tmp_path / "empty").mkdir()
    argv = {
        "no_root": [str(tmp_path / "absent")],
        "no_list": ["--from-list", str(tmp_path / "absent.txt")],
        "empty": [str(tmp_path / "empty")],
        "rubric": [str(froot), "--rubric", str(tmp_path / "bad.toml")],
        "scans": [str(froot), "--scans", "no_such_scan"],
        "window": [str(froot), "--window", "soon"],
        "inside": [str(froot)],
    }[case]
    out = froot / "grpa" / "svc1" / "svc1-main" / "x" if case == "inside" else tmp_path / "out"
    assert fleet_cli.main([*argv, "--out", str(out)]) == 2
    assert capsys.readouterr().err.startswith("error:")
    assert not out.exists()


def test_script_runs_standalone(froot, tmp_path):
    """The documented invocation: the script finds the assess lib on its own."""
    env = {**os.environ, "PYTHONPATH": ""}
    done = subprocess.run([sys.executable, str(FLEET_SCRIPT), str(froot), "--out", str(tmp_path / "o"),
                           "--scans", "agent_ops"], capture_output=True, text=True, env=env, check=False)
    assert done.returncode == 0, done.stderr
    assert len(_rows(tmp_path / "o")) == 3
