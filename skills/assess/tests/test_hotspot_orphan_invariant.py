"""Executable invariant for the /assess hotspot wiki (assess-obey-thyself, task 9).

The `.assess/` wiki is a compounding history: a hotspot page survives across runs
even after the file graduates off the top list. But a page whose *source file has
been deleted* is a lying map - it keeps describing a file that no longer exists.
The contract this suite enforces:

    No active (non-retired) hotspot page references a source path absent from disk.

`prune_orphan_hotspots` maintains it by stamping every orphaned page RETIRED (the
file's history is preserved; the page just stops claiming the file is live). The
invariant helper below is the same check phrased as an assertion, so a page that
slips through the pruner - or a pruner regression - fails the build. Mirrors the
`test_self_architecture.py` idiom: a pure filesystem scan asserting a property.
"""
from __future__ import annotations

from pathlib import Path

from lib.wiki_writer import (
    RETIRED_STATUS,
    hotspot_page_source_path,
    hotspot_page_status,
    prune_orphan_hotspots,
    write_hotspot_page,
)


def _active_orphans(assess_dir: Path, repo_root: Path) -> list[str]:
    """Every source path an *active* (non-retired) hotspot page names that is
    absent from disk. The invariant holds iff this list is empty."""
    hotspots_dir = assess_dir / "hotspots"
    if not hotspots_dir.is_dir():
        return []
    orphans: list[str] = []
    for page in sorted(hotspots_dir.glob("*.md")):
        content = page.read_text(encoding="utf-8")
        path = hotspot_page_source_path(content)
        if path is None:
            continue
        if hotspot_page_status(content) == RETIRED_STATUS:
            continue  # retired pages are allowed to reference a missing file
        if not (repo_root / path).exists():
            orphans.append(path)
    return orphans


def _write_page(assess_dir: Path, path: str, status: str = "active") -> None:
    assess_dir.mkdir(parents=True, exist_ok=True)
    write_hotspot_page(
        assess_dir, path=path, first_flagged="2026-01-01", last_seen="2026-07-07",
        status=status, loc=600, ccn=30, commits=5, has_tests=None,
        history_rows="| 2026-07-07 | 600 | 30 | 5 | active |",
        briefing="x", actions="- y",
    )


def test_prune_retires_orphan_leaves_live_page(tmp_path: Path) -> None:
    """A deleted file's page is retired; a live file's page is untouched."""
    repo = tmp_path / "repo"
    assess = repo / ".assess"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "live.go").write_text("package main\n")
    _write_page(assess, "src/live.go")
    _write_page(assess, "src/gone.go")  # never created on disk

    retired = prune_orphan_hotspots(assess, repo)
    assert retired == ["src/gone.go"]

    # The invariant now holds: no active page references a missing file.
    assert _active_orphans(assess, repo) == []


def test_orphan_invariant_fails_before_prune(tmp_path: Path) -> None:
    """The invariant helper catches a surviving active orphan - proving it isn't
    vacuously passing."""
    repo = tmp_path / "repo"
    assess = repo / ".assess"
    assess.mkdir(parents=True)
    _write_page(assess, "src/gone.go")
    assert _active_orphans(assess, repo) == ["src/gone.go"]


def test_retired_page_preserves_history(tmp_path: Path) -> None:
    """Retirement stamps the status and a banner but keeps the page's history."""
    repo = tmp_path / "repo"
    assess = repo / ".assess"
    assess.mkdir(parents=True)
    _write_page(assess, "src/gone.go")

    prune_orphan_hotspots(assess, repo)
    page = next((assess / "hotspots").iterdir())
    content = page.read_text(encoding="utf-8")
    assert RETIRED_STATUS in content
    assert "Retired:" in content
    # History section and the original path are preserved.
    assert "src/gone.go" in content
    assert "## History across runs" in content
    assert hotspot_page_status(content) == RETIRED_STATUS


def test_prune_is_idempotent(tmp_path: Path) -> None:
    """A second prune retires nothing new and doesn't double-stamp the banner."""
    repo = tmp_path / "repo"
    assess = repo / ".assess"
    assess.mkdir(parents=True)
    _write_page(assess, "src/gone.go")

    assert prune_orphan_hotspots(assess, repo) == ["src/gone.go"]
    assert prune_orphan_hotspots(assess, repo) == []

    page = next((assess / "hotspots").iterdir())
    assert page.read_text(encoding="utf-8").count("Retired:") == 1


def test_prune_no_hotspots_dir(tmp_path: Path) -> None:
    """A repo with no hotspots/ directory yet prunes nothing (fresh install)."""
    repo = tmp_path / "repo"
    assess = repo / ".assess"
    assess.mkdir(parents=True)
    assert prune_orphan_hotspots(assess, repo) == []


def test_file_recreated_can_be_rewritten_active(tmp_path: Path) -> None:
    """A retired page is overwritten fresh (active) if the file returns and is
    still a hotspot - write_hotspot_page rewrites the whole page."""
    repo = tmp_path / "repo"
    assess = repo / ".assess"
    (repo / "src").mkdir(parents=True)
    _write_page(assess, "src/flap.go")
    prune_orphan_hotspots(assess, repo)
    page = next((assess / "hotspots").iterdir())
    assert hotspot_page_status(page.read_text()) == RETIRED_STATUS

    # File comes back and is re-written as a live hotspot.
    (repo / "src" / "flap.go").write_text("package main\n")
    _write_page(assess, "src/flap.go", status="regressed")
    content = page.read_text(encoding="utf-8")
    assert hotspot_page_status(content) == "regressed"
    assert "Retired:" not in content
    assert _active_orphans(assess, repo) == []
