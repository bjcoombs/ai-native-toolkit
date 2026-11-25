"""Golden fixture fleet run: Windows and Linux produce the same repos.jsonl (#701, #713).

The fixture estate is built at test time with LF bytes and fixed commit dates.
Every history window and day count (``ai_usage`` commits in the window,
``last_commit_days``) is counted back from HEAD's commit time (issue #773), so
the file reads the same on every day without masking; ``active`` is fleet run
metadata measured from the scan date, and with commit dates years in the past
it is false on any later date. The
Linux job proves the committed ``repos.jsonl`` is Linux-true; the Windows job
(.github/workflows/windows.yml) compares its own run against the same file, so
a separator, encoding or line-ending difference fails there.

A second run scores the same estate with ``fixtures/fleet_golden/levels/levels.toml`` and no
``--pack`` (#1276): its ``repos.jsonl``, ``run.json`` and ``level.json`` must match the
committed copies beside that model byte for byte, with the toolkit commit, plugin version and
run date pinned.

Regenerate after a scan changes its output, on Linux with ripgrep installed:

    cd skills/assess && FLEET_GOLDEN_REGEN=1 uv run --locked pytest tests/test_fleet_golden.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from test_fleet_cli import fleet_cli

GOLDEN = Path(__file__).parent / "fixtures" / "fleet_golden" / "repos.jsonl"
DATE = "2020-01-01T00:00:00+00:00"

# Paths at several depths and a non-ASCII name and body, so separators and
# encodings both reach the output.
ESTATE: dict[str, dict[str, str]] = {
    "grpa/svc1": {
        "CLAUDE.md": "# svc1\n\nRun `make check` before pushing.\n",
        "Makefile": "check:\n\tpytest\n",
        "src/svc1/__init__.py": "",
        "src/svc1/app.py": "from svc1 import util\n\n\ndef run(x):\n    if x:\n        return util.twice(x)\n    return 0\n",
        "src/svc1/util.py": "def twice(x):\n    return 2 * x  # noqa: E501\n",
        "tests/test_app.py": "from svc1.app import run\n\n\ndef test_run():\n    assert run(1) == 2\n",
        ".github/workflows/ci.yml": "on: push\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n      - run: make check\n",
    },
    "grpa/sub/deep/svc2": {
        "AGENTS.md": "# svc2\n\nSee docs/guide.md.\n",
        "docs/guide.md": "# Guide\n\nCafé crème: the service reads `src/main.py`.\n",
        "src/main.py": "# TODO: split this module\nimport os\n\n\ndef main():\n    return os.sep\n",
        "src/naïve.py": "VALUE = 'ü'\n",
    },
    "grpb/lib1": {
        "README.md": "# lib1\n",
        "lib/core.py": "# FIXME(#12): remove after the migration\nx = 1  # type: ignore[assignment]\n",
        ".claude/settings.json": '{"permissions": {"allow": ["Bash(make check)"]}}\n',
    },
}


def _git(repo: Path, *args: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_DATE": DATE, "GIT_COMMITTER_DATE": DATE}
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   encoding="utf-8", errors="replace", env=env)


def _leaves(value: object, prefix: str) -> dict[str, object]:
    """Every scalar in a JSON value keyed by its dotted path."""
    if isinstance(value, dict):
        return {k: v for key, item in value.items() for k, v in _leaves(item, f"{prefix}.{key}").items()}
    if isinstance(value, list):
        return {k: v for i, item in enumerate(value) for k, v in _leaves(item, f"{prefix}[{i}]").items()}
    return {prefix: value}


def _differences(produced: bytes, golden: bytes) -> list[str]:
    """The dotted paths whose values differ, so a Windows log names the field."""
    got = {r["repo"]: r for r in map(json.loads, produced.decode("utf-8").splitlines())}
    want = {r["repo"]: r for r in map(json.loads, golden.decode("utf-8").splitlines())}
    flat_got = {k: v for repo, row in got.items() for k, v in _leaves(row, repo).items()}
    flat_want = {k: v for repo, row in want.items() for k, v in _leaves(row, repo).items()}
    return [f"{key}: got {flat_got.get(key, '<absent>')!r}, golden {flat_want.get(key, '<absent>')!r}"
            for key in sorted(flat_got.keys() | flat_want.keys())
            if flat_got.get(key, "<absent>") != flat_want.get(key, "<absent>")]


def _build_estate(root: Path) -> None:
    for slug, files in ESTATE.items():
        repo_dir = root / slug
        (repo_dir / "worktree").mkdir(parents=True)
        checkout = repo_dir / f"{repo_dir.name}-main"
        checkout.mkdir()
        _git(checkout, "init", "-q", "-b", "main")
        _git(checkout, "config", "user.email", "t@example.com")
        _git(checkout, "config", "user.name", "t")
        _git(checkout, "config", "core.autocrlf", "false")
        for name, text in files.items():
            path = checkout / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode("utf-8"))  # LF on every platform
        _git(checkout, "add", "-A")
        _git(checkout, "commit", "-q", "-m", "init")


@pytest.mark.skipif(shutil.which("rg") is None, reason="needs ripgrep")
def test_fixture_fleet_run_matches_the_committed_linux_repos_jsonl(tmp_path, monkeypatch):
    root = tmp_path / "froot"
    _build_estate(root)
    monkeypatch.chdir(tmp_path)

    assert fleet_cli.main([str(root), "--out", "out", "--jobs", "1",
                           "--no-history", "--no-diagrams"]) == 0

    produced = (tmp_path / "out" / "repos.jsonl").read_bytes()
    if os.environ.get("FLEET_GOLDEN_REGEN") == "1":
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_bytes(produced)
    golden = GOLDEN.read_bytes()
    assert _differences(produced, golden) == [], (
        "fleet repos.jsonl differs from the committed Linux golden; if a scan changed on "
        "purpose, regenerate it (see this module's docstring)")
    assert b"\r\n" not in produced, "repos.jsonl must be written with LF line endings"
    assert produced == golden


LEVELS_GOLDEN = GOLDEN.parent / "levels"
LEVELS_OUTPUTS = ("repos.jsonl", "run.json", "level.json")


@pytest.mark.skipif(shutil.which("rg") is None, reason="needs ripgrep")
def test_a_levels_run_without_pack_writes_the_committed_outputs(tmp_path, monkeypatch):
    """#1276: a fleet --levels run without --pack writes repos.jsonl, run.json and level.json
    byte for byte as committed, so adding --pack changed nothing for a run without one. The
    toolkit commit, plugin version and run date are pinned; the rest is the run's own."""
    root = tmp_path / "froot"
    _build_estate(root)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(fleet_cli, "toolkit_commit", lambda: "0" * 40)
    monkeypatch.setattr(fleet_cli, "read_plugin_version", lambda: "0.0.0")
    monkeypatch.setattr(fleet_cli, "utc_today", lambda: date(2020, 1, 2))

    assert fleet_cli.main([str(root), "--out", "out", "--jobs", "1", "--no-history", "--no-diagrams",
                           "--levels", str(LEVELS_GOLDEN / "levels.toml")]) == 0

    for name in LEVELS_OUTPUTS:
        produced = (tmp_path / "out" / name).read_bytes()
        if os.environ.get("FLEET_GOLDEN_REGEN") == "1":
            (LEVELS_GOLDEN / name).write_bytes(produced)
        assert produced == (LEVELS_GOLDEN / name).read_bytes(), (
            f"{name} differs from the committed golden; if a scan or the stamp changed on purpose, "
            "regenerate it (see this module's docstring)")
