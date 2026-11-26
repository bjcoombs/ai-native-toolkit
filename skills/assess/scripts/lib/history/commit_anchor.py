"""The assessed commit's time: the anchor of every history window (issue #773).

The level is a function of the commit (#740), so a git-history window and a
"days since" figure are counted back from HEAD's committer time, never from the
clock of whoever runs the scan. ``git log --since`` filters on committer time,
so the anchor is ``%ct``; day counts subtract an author time (``%at``) from it,
which keeps the rebase-safe staleness axis ``git_churn.file_last_commit_epoch``
documents. A missing anchor (no HEAD, git failure) degrades the window scan; it
never falls back to today.

The anchor of every history window (issue #773). `head_commit_epoch(root)` is HEAD's
committer time (`%ct`, the time `git log --since` filters on) or None; `since_args(root,
window, anchor_epoch)` gives `["--since=@<epoch>"]` through
`history_window.anchored_since`, `[]` for all history, and None when a window has no
anchor so the caller degrades. Used by `git_churn` and `understanding_analysis`;
`ai_usage` reads HEAD through its own `_git` so a missing git or a timeout keeps its
reason, then builds the bound with `anchored_since`. Tests: `tests/test_commit_anchor.py` (anchored values on a
dated fixture repository, and the same `scans` from two faked dates, Python's clock and
git's), `tests/test_no_wall_clock.py` (an AST guard: no wall-clock read
outside an allowlist of run-metadata and network functions, no relative git date
literal, and every date bound a headless run passes to git starts with `@`).
Outside the guarantee: the network scans (`merged_sample`, `review_reality`,
`delivery`, `gate_cost`, `config_drift`) read forge state that is not a function of the
commit, so Layer 7 level 2 (`layer-7-review`) is not yet same-commit-same-level; they
are unavailable in a headless run. A dirty working tree is never a function of a commit.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from lib.history.history_window import anchored_since

# Cap every git call so a stuck invocation (huge repo, lock contention, a hung
# credential prompt) degrades to "no churn data" rather than blocking the run.
# The one definition: git_churn imports and re-exports it.
GIT_TIMEOUT_SECONDS = 20


def head_commit_epoch(root: Path) -> int | None:
    """HEAD's committer time (``%ct``) for the repository at ``root``; None
    when there is no HEAD or git fails."""
    try:
        return int(subprocess.run(
            ["git", "-C", str(root), "log", "-1", "--format=%ct", "HEAD"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
            timeout=GIT_TIMEOUT_SECONDS,
        ).stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired,
            ValueError):
        return None


def since_args(root: Path, window: str | None, anchor_epoch: int | None) -> list[str] | None:
    """``["--since=@<epoch>"]`` for ``window`` counted back from ``anchor_epoch``
    (HEAD's committer time when None), ``[]`` for all history, and None when a
    window has no anchor, so the caller degrades rather than widen to all history."""
    if window is None:
        return []
    anchor = anchor_epoch if anchor_epoch is not None else head_commit_epoch(root)
    if anchor is None:
        return None
    since = anchored_since(window, anchor)
    return [] if since is None else [f"--since={since}"]
