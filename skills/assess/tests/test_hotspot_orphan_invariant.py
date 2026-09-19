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

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from assess_core import build_run_context
from assess_finalize import finalize_run
from lib.badge import maturity_band
from lib.wiki_writer import (
    RETIRED_EXCLUDED_STATUS,
    RETIRED_STATUS,
    slug_for_path,
    verify_log_chain,
    hotspot_page_source_path,
    hotspot_page_status,
    is_retired_status,
    prune_orphan_hotspots,
    retire_excluded_hotspots,
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
        if is_retired_status(hotspot_page_status(content)):
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


# --- excluded after an unfinalized run (#356) ---------------------------------
#
# A file first flagged by a core run that was never finalized, then excluded in
# `.assess/config.toml` before the next run, was never part of a finished
# assessment. Its page is retired (not deleted, not left live), its
# first-flagged.json entry is dropped, and it does not re-enter index.md as
# "graduated" through the rotated prior stats. An excluded file first flagged in
# a finalized run keeps its page and its date.

_EXCLUDE_DAY = "2026-09-18"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@example.com", "-c", "user.name=T", *args],
        check=True, capture_output=True, text=True, env=os.environ,
    )


@pytest.fixture
def excl_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    for rel in ("src/hot.py", "gen/big.py", "vendor/fin.py"):
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text("def f(a):\n    return a\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "c1")
    (repo / ".assess").mkdir()
    return repo


def _stats(assess_dir: Path, paths: list[str]) -> None:
    current = assess_dir / "complexity-stats.json"
    if current.exists():
        shutil.copy(current, assess_dir / "complexity-stats.prior.json")
    current.write_text(json.dumps({
        "files_scored": len(paths), "loc": {"total": len(paths)},
        "ccn": {"max": 1, "mean": 1},
        "top_hotspots": [{"path": p, "loc": 1, "ccn": 1, "commits": 1} for p in paths],
        "top_complex": [], "top_large": [],
    }), encoding="utf-8")


def _core(repo: Path, day: str = _EXCLUDE_DAY) -> dict:
    return build_run_context(repo_root=repo, run_date=day, non_interactive=True)


def _finalize(assess_dir: Path, ctx: dict) -> None:
    (assess_dir / ".cache").mkdir(exist_ok=True)
    (assess_dir / ".cache" / "finalize-input.json").write_text(json.dumps({
        "run_id": ctx["run_id"], "score": 4.0, "denominator": 8,
        "maturity_label": maturity_band(4.0, 8),
        "top_action": "fixture action", "hotspot_actions": {},
    }), encoding="utf-8")
    finalize_run(assess_dir=assess_dir)


def _status(assess_dir: Path, path: str) -> str | None:
    page = assess_dir / "hotspots" / f"{slug_for_path(path)}.md"
    return hotspot_page_status(page.read_text(encoding="utf-8")) if page.exists() else None


def _exclude(repo: Path, dirs: list[str]) -> None:
    (repo / ".assess" / "config.toml").write_text(f"exclude_dirs = {dirs!r}\n", encoding="utf-8")


def test_core_retires_page_excluded_after_unfinalized_run(excl_repo: Path) -> None:
    assess = excl_repo / ".assess"
    _stats(assess, ["src/hot.py", "vendor/fin.py"])
    _finalize(assess, _core(excl_repo, "2026-09-17"))
    _stats(assess, ["gen/big.py", "src/hot.py", "vendor/fin.py"])
    _core(excl_repo)  # flags gen/big.py, never finalized
    _exclude(excl_repo, ["gen", "vendor"])
    _stats(assess, ["src/hot.py"])
    ctx = _core(excl_repo)
    _finalize(assess, ctx)

    assert _status(assess, "gen/big.py") == RETIRED_EXCLUDED_STATUS
    for live in ("vendor/fin.py", "src/hot.py"):
        status = _status(assess, live)
        assert status is not None and not status.startswith("retired")
    flagged = json.loads((assess / "first-flagged.json").read_text(encoding="utf-8"))
    assert flagged == {"src/hot.py": "2026-09-17", "vendor/fin.py": "2026-09-17"}
    index = (assess / "index.md").read_text(encoding="utf-8")
    assert "gen/big.py" not in index
    assert [p["path"] for p in ctx["diff_detail"]["graduated"]] == ["vendor/fin.py"]
    assert ctx["retired_excluded_hotspots"] == ["gen/big.py"]
    assert ctx["dropped_first_flagged"] == ["gen/big.py"]
    assert verify_log_chain(assess) == (True, None)


def test_excluded_after_unfinalized_run_chain_of_superseded_runs(excl_repo: Path) -> None:
    """The file stays provisional across a second unfinalized run that
    supersedes the first, where it is no longer "new"."""
    assess = excl_repo / ".assess"
    _stats(assess, ["src/hot.py"])
    _finalize(assess, _core(excl_repo, "2026-09-17"))
    _stats(assess, ["gen/big.py", "src/hot.py"])
    _core(excl_repo)
    _stats(assess, ["gen/big.py", "src/hot.py"])
    _core(excl_repo)  # gen/big.py is persistent here, still unfinalized
    _exclude(excl_repo, ["gen"])
    _stats(assess, ["src/hot.py"])
    _core(excl_repo)

    assert _status(assess, "gen/big.py") == RETIRED_EXCLUDED_STATUS
    flagged = json.loads((assess / "first-flagged.json").read_text(encoding="utf-8"))
    assert flagged == {"src/hot.py": "2026-09-17"}


def test_excluded_after_unfinalized_run_on_new_commit_is_kept(excl_repo: Path) -> None:
    """A run on a new commit does not supersede the unfinalized one, so the
    rule does not fire and the file graduates as before."""
    assess = excl_repo / ".assess"
    _stats(assess, ["gen/big.py", "src/hot.py"])
    _core(excl_repo)
    (excl_repo / "src" / "other.py").write_text("x = 1\n", encoding="utf-8")
    _git(excl_repo, "add", "-A")
    _git(excl_repo, "commit", "-q", "-m", "c2")
    _exclude(excl_repo, ["gen"])
    _stats(assess, ["src/hot.py"])
    ctx = _core(excl_repo)

    assert ctx["retired_excluded_hotspots"] == []
    status = _status(assess, "gen/big.py")
    assert status is not None and not status.startswith("retired")


def test_excluded_after_unfinalized_run_ignores_pre_upgrade_run_context(excl_repo: Path) -> None:
    """A superseded run-context without provisional_first_flagged (written
    before the key existed) retires nothing, even for a path it lists as new."""
    assess = excl_repo / ".assess"
    _stats(assess, ["gen/big.py", "src/hot.py"])
    _core(excl_repo)
    ctx_path = assess / "run-context.json"
    prior = json.loads(ctx_path.read_text(encoding="utf-8"))
    assert "gen/big.py" in [h["path"] for h in prior["diff_detail"]["new"]]
    del prior["provisional_first_flagged"]
    ctx_path.write_text(json.dumps(prior), encoding="utf-8")
    _exclude(excl_repo, ["gen"])
    _stats(assess, ["src/hot.py"])
    ctx = _core(excl_repo)

    assert ctx["retired_excluded_hotspots"] == []
    assert ctx["dropped_first_flagged"] == []
    status = _status(assess, "gen/big.py")
    assert status is not None and not status.startswith("retired")
    flagged = json.loads((assess / "first-flagged.json").read_text(encoding="utf-8"))
    assert flagged["gen/big.py"] == _EXCLUDE_DAY


def test_prune_leaves_excluded_retired_page_alone(tmp_path: Path) -> None:
    """A page already retired for another reason is not re-stamped when its
    file is later deleted."""
    repo = tmp_path / "repo"
    assess = repo / ".assess"
    _write_page(assess, "gen/big.py", status=RETIRED_EXCLUDED_STATUS)
    assert prune_orphan_hotspots(assess, repo) == []
    page = next((assess / "hotspots").iterdir())
    assert hotspot_page_status(page.read_text(encoding="utf-8")) == RETIRED_EXCLUDED_STATUS
    # The file is absent from disk, yet the invariant holds: the page is retired.
    assert _active_orphans(assess, repo) == []


def test_retire_excluded_skips_page_without_status_token(tmp_path: Path) -> None:
    """A page with no status token cannot be stamped, so it is reported as
    unstamped rather than retired, and the core keeps its first-flagged entry."""
    assess = tmp_path / ".assess"
    _write_page(assess, "gen/big.py")
    _write_page(assess, "gen/odd.py")
    odd = assess / "hotspots" / f"{slug_for_path('gen/odd.py')}.md"
    odd.write_text("# Hotspot: `gen/odd.py`\n\nno metadata line\n", encoding="utf-8")
    before = odd.read_text(encoding="utf-8")

    retired, unstamped = retire_excluded_hotspots(assess, ["gen/odd.py", "gen/big.py", "gen/none.py"])
    assert retired == ["gen/big.py"]
    assert unstamped == ["gen/odd.py"]
    assert odd.read_text(encoding="utf-8") == before
