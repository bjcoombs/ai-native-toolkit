"""The assessed commit's time: the anchor of every history window (issue #773).

The level is a function of the commit (#740), so a git-history window and a
"days since" figure are counted back from HEAD's committer time, never from the
clock of whoever runs the scan. ``git log --since`` filters on committer time,
so the anchor is ``%ct``; day counts subtract an author time (``%at``) from it,
which keeps the rebase-safe staleness axis ``git_churn.file_last_commit_epoch``
documents. A missing anchor (no HEAD, git failure) degrades the window scan; it
never falls back to today.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from lib.history_window import anchored_since

# The same cap as ``git_churn.GIT_TIMEOUT_SECONDS`` (that module imports this one).
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
