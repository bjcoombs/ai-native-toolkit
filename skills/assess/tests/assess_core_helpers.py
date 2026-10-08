"""Repo-seeding helpers shared by test_assess_core.py and the lib/ module
tests split out of it (not a test module).
"""
from __future__ import annotations

import json
from pathlib import Path


def _minimal_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 0, "loc": {}, "ccn": {},
        "top_hotspots": [], "top_complex": [], "top_large": [],
    }))
    return repo


_EMPTY_STATS = json.dumps({
    "files_scored": 0, "loc": {}, "ccn": {},
    "top_hotspots": [], "top_complex": [], "top_large": [],
})


def _seed_assess(repo: Path) -> None:
    (repo / ".assess").mkdir(exist_ok=True)
    (repo / ".assess" / "complexity-stats.json").write_text(_EMPTY_STATS)


# --- renamed and deleted history (rename map, pruned_finding_paths) ----------

def _renamed_and_deleted_history(repo: Path, commit) -> None:
    """old/, other/ and gone/ co-change six times; then old/ -> new/ and gone/
    is deleted, so the history names two directories that no longer exist."""
    import subprocess
    for i in range(1, 7):
        for d, stem in (("old", "x"), ("old", "y"), ("other", "z"), ("gone", "k")):
            f = repo / d / f"{stem}.py"
            f.parent.mkdir(parents=True, exist_ok=True)
            with f.open("a", encoding="utf-8") as fh:
                fh.write(f"def {stem}{i}(): return {i}\n")
        commit(f"c{i}")
    subprocess.run(["git", "-C", str(repo), "mv", "old", "new"], check=True)
    commit("rename")
    subprocess.run(["git", "-C", str(repo), "rm", "-rq", "gone"], check=True)
    commit("remove")


# ════════════════════════════════════════════════════════════════════════════
# Config-exclusion disclosure (excluded_by_config block)
# ════════════════════════════════════════════════════════════════════════════

def _write_min_stats(assess_dir: Path) -> None:
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 0, "loc": {}, "ccn": {},
        "top_hotspots": [], "top_complex": [], "top_large": [],
    }))
