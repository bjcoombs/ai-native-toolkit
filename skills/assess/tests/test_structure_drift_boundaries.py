"""structure_drift Tier 0 ``boundary_patterns``: matched declarations less the catch-all (#785).

A CODEOWNERS whose only pattern is ``*`` names one owner for the whole tree and no
module boundary, so Layer 4 level 1 reads ``boundary_patterns`` instead of
``matched_patterns``. Kept apart from ``test_structure_drift.py``, which sits near its
file-size ceiling.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from lib.context_blocks import structure_drift_tier_0
from lib.structure_drift import detect_path_existence_drift

_ARCH = "# Architecture\n\nThe entry point is `src/a.py`.\n"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   encoding="utf-8", errors="replace",
                   env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull})


def _repo(root: Path, files: dict[str, str]) -> Path:
    for rel, text in {"src/a.py": "x = 1\n", **files}.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=a@example.com", "-c", "user.name=A", "commit", "-qm", "init")
    return root


@pytest.mark.parametrize(("files", "expected"), [
    pytest.param({".github/CODEOWNERS": "* @x\n"}, 0, id="star"),
    pytest.param({"CODEOWNERS": "/** @x\n"}, 0, id="slash-double-star"),
    pytest.param({"CODEOWNERS": "/* @x\n** @y\n"}, 0, id="slash-star-and-double-star"),
    pytest.param({".github/CODEOWNERS": "* @x\nsrc/ @team\n"}, 1, id="star-plus-src"),
    pytest.param({".github/CODEOWNERS": "src/ @team\n"}, 1, id="src-only"),
    pytest.param({"ARCHITECTURE.md": _ARCH}, 1, id="architecture-one-path"),
    pytest.param({".github/CODEOWNERS": "* @x\n", "ARCHITECTURE.md": _ARCH}, 1,
                 id="star-plus-architecture"),
    pytest.param({".github/CODEOWNERS": "* @x\nghost/ @team\n"}, 0, id="unmatched-path-not-counted"),
])
def test_boundary_patterns_excludes_catch_all(tmp_path: Path, files: dict[str, str], expected: int) -> None:
    repo = _repo(tmp_path / "r", files)
    tier_0 = detect_path_existence_drift(repo)
    assert tier_0["boundary_patterns"] == expected
    # matched_patterns keeps its meaning: the catch-all still matches files.
    assert tier_0["matched_patterns"] >= expected
    block = structure_drift_tier_0(repo)
    assert block is not None
    assert block["tier_0"]["boundary_patterns"] == expected


def test_no_ownership_map_omits_the_block(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r", {})
    assert detect_path_existence_drift(repo)["boundary_patterns"] == 0
    assert structure_drift_tier_0(repo) is None
