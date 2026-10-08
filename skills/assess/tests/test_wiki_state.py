"""Tests for lib/wiki_state.py: first-flagged dates, including rekeying through
the rename map.

Moved out of test_assess_core.py when assess_core's helpers split into
lib/ modules; shared repo-seeding helpers live in assess_core_helpers.py.
"""
from __future__ import annotations

import json
from pathlib import Path

from assess_core import build_run_context
from assess_core_helpers import _renamed_and_deleted_history
from lib import wiki_state


def test_first_flagged_unknown_when_prior_seeded_without_history(tmp_path: Path) -> None:
    """When prior stats are seeded but first-flagged.json isn't, a hotspot that
    predates this run must read 'unknown', not today's date
    (issue #47, observation 7)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    # Same hotspot in prior and current -> persistent, predates this run.
    (assess_dir / "complexity-stats.prior.json").write_text(json.dumps({
        "plugin_version": "1.12.0", "files_scored": 50, "loc": {}, "ccn": {},
        "top_hotspots": [{"path": "src/old.go", "loc": 500, "ccn": 20, "commits": 5}],
    }))
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "plugin_version": "1.12.0", "files_scored": 50, "loc": {}, "ccn": {},
        "top_hotspots": [{"path": "src/old.go", "loc": 510, "ccn": 21, "commits": 6}],
    }))
    # No first-flagged.json on disk.
    build_run_context(repo_root=repo, run_date="2026-05-22")
    page = next((assess_dir / "hotspots").iterdir()).read_text(encoding="utf-8")
    assert "First flagged: unknown" in page
    assert "First flagged: 2026-05-22" not in page


def test_first_flagged_stamps_today_for_genuinely_new_hotspot(tmp_path: Path) -> None:
    """A hotspot absent from the prior snapshot is genuinely new -> today."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.prior.json").write_text(json.dumps({
        "plugin_version": "1.12.0", "files_scored": 50, "loc": {}, "ccn": {},
        "top_hotspots": [{"path": "src/old.go", "loc": 500, "ccn": 20, "commits": 5}],
    }))
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "plugin_version": "1.12.0", "files_scored": 50, "loc": {}, "ccn": {},
        "top_hotspots": [{"path": "src/fresh.go", "loc": 400, "ccn": 18, "commits": 4}],
    }))
    build_run_context(repo_root=repo, run_date="2026-05-22")
    fresh_page = next(p for p in (assess_dir / "hotspots").iterdir()
                      if p.name.startswith("src-fresh-go-"))
    assert "First flagged: 2026-05-22" in fresh_page.read_text(encoding="utf-8")


def test_first_flagged_rekeyed_through_rename_map(git_repo) -> None:
    """A first-flagged entry recorded under a renamed path moves to the current
    path with its original date, beating the 'new hotspot, stamp today' default;
    an unrenamed entry is untouched."""
    repo, commit = git_repo
    _renamed_and_deleted_history(repo, commit)
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "first-flagged.json").write_text(json.dumps(
        {"old/x.py": "2026-01-01", "other/z.py": "2026-02-02"}))
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 2, "loc": {"total": 12}, "ccn": {"max": 6, "mean": 6},
        "top_hotspots": [{"path": "new/x.py", "loc": 6, "ccn": 6, "commits": 7},
                         {"path": "other/z.py", "loc": 6, "ccn": 6, "commits": 6}],
        "top_complex": [], "top_large": [],
    }))

    build_run_context(repo_root=repo, run_date="2026-09-18")
    assert json.loads((assess_dir / "first-flagged.json").read_text()) == {
        "new/x.py": "2026-01-01", "other/z.py": "2026-02-02",
    }


def test_first_flagged_rekeyed_keeps_earliest_known_date() -> None:
    rename_map = {"a.py": "b.py", "c.py": "d.py"}
    assert wiki_state.rekey_first_flagged(
        {"a.py": "2026-03-01", "b.py": "2026-01-01", "c.py": "2026-02-02",
         "d.py": "unknown", "e.py": "2026-04-04"},
        rename_map,
    ) == {"b.py": "2026-01-01", "d.py": "2026-02-02", "e.py": "2026-04-04"}
