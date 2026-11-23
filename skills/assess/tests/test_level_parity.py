"""/assess and /fleet give the same level block on one commit, reasons included (#908).

``test_level_tally`` checks the tally and the Step on the #744 contract fixtures.
These are the #740 parent check's fixtures, where a reason used to differ: a
repository with no remote (``no_remote`` in both, never ``needs_platform``) and
no ownership map (``no_ownership_map`` in both, never ``block_absent``). The
whole block is compared, every condition's state and reason with it.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from lib.fleet.fleet_level import LEVEL_FILE
from lib.level_signals import DEFAULT_LEVELS
from test_fleet_cli import fleet_cli
from test_level_tally import _commit

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

PYPROJECT = ('[project]\nname = "app"\nversion = "0.1.0"\ndependencies = ["requests"]\n'
             "[tool.coverage.report]\nfail_under = 80\n")
FIXTURES: dict[str, dict[str, str]] = {
    "bare": {"main.py": 'print("hi")\n'},
    "locknoci": {"pyproject.toml": PYPROJECT, "uv.lock": "version = 1\n", "m.py": "x=1\n"},
    "gotangle": {
        "go.mod": "module example.com/t\ngo 1.22\n",
        "a/a.go": 'package a\nimport ("example.com/t/b"; "example.com/t/c")\nfunc A() int { return b.B() + c.C() }\n',
        "b/b.go": 'package b\nimport "example.com/t/c"\nfunc B() int { return c.C() }\n',
        "c/c.go": "package c\nfunc C() int { return 1 }\n",
    },
}


@pytest.fixture(scope="module")
def fleet_levels(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, Any]]:
    root = tmp_path_factory.mktemp("parity")
    for name, files in FIXTURES.items():
        repo = root / name / "r"
        repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True, capture_output=True)
        for rel, text in files.items():
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text(text, encoding="utf-8", newline="\n")
        _commit(repo, "2026-01-01T10:00:00Z", "init")
    (root / "list.txt").write_text("".join(f"{name}/r\n" for name in FIXTURES), encoding="utf-8")
    cwd = Path.cwd()
    os.chdir(root)
    try:
        assert fleet_cli.main(["--from-list", "list.txt", "--levels", str(DEFAULT_LEVELS), "--out", "out",
                               "--no-diagrams", "--no-history"]) == 0
    finally:
        os.chdir(cwd)
    return root, json.loads((root / "out" / LEVEL_FILE).read_text(encoding="utf-8"))["repos"]


@pytest.mark.parametrize("name", list(FIXTURES))
def test_assess_and_fleet_give_identical_level_blocks(fleet_levels, tmp_path, name):
    root, repos = fleet_levels
    repo = tmp_path / name
    subprocess.run(["git", "clone", "-q", str(root / name / "r"), str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "remote", "remove", "origin"], check=True, capture_output=True)
    assess = repo / ".assess"
    assess.mkdir()
    treemap = subprocess.run(["uv", "run", "--script", str(SCRIPTS / "complexity-treemap.py"), str(repo), "-o",
                              str(assess / "complexity-heatmap.svg"), "--stats", str(assess / "complexity-stats.json")],
                             capture_output=True, encoding="utf-8", errors="replace")
    assert treemap.returncode == 0, treemap.stderr
    from assess_core import build_and_publish_run

    level = build_and_publish_run(repo_root=repo, run_date="2026-11-22", non_interactive=True)["level"]
    assert level == repos[f"{name}/r"]
    reasons = {c["id"]: c.get("reason") for c in level["conditions"]}
    assert reasons["layer-4-architecture.1"] == "no_ownership_map"
    assert reasons["layer-7-review.1"] == reasons["layer-7-review.2"] == "no_remote"
