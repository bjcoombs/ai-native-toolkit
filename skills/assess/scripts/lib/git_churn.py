"""Shared git-churn machinery for the /assess deterministic core.

`complexity-treemap.py` (code heatmap), `docs-staleness-treemap.py` (docs
heatmap) and `doc_staleness.py` (Layer 0 staleness metric) all need the same
two things: a per-file commit count over a window, and a way to pick a window
that gives a visible gradient. This module is the single source for both so
churn is computed one way, not three (PRD: "do not reinvent churn").

Pure subprocess + git. No heavy dependencies, so it imports cleanly in the
deterministic core (which runs with only networkx) as well as in the treemap
scripts (which also pull in lizard/matplotlib/squarify).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Cap every git call so a stuck invocation (huge repo, lock contention, a hung
# credential prompt) degrades to "no churn data" rather than blocking the run.
GIT_TIMEOUT_SECONDS = 20


def git_churn_scores(root: Path, since: str | None = None) -> dict[Path, int]:
    """Return {abs_path: commit_count} for files under root tracked in git.

    Returns empty dict if root is not inside a git repo. Commit counts are
    over the full history reachable from HEAD by default. Pass `since` as
    a git-compatible date expression (e.g. "6 months ago") to window the
    count. Renames are not followed - a file gets credit only under its
    current name.
    """
    try:
        repo_top = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
            timeout=GIT_TIMEOUT_SECONDS,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return {}
    repo = Path(repo_top).resolve()

    cmd = ["git", "-C", repo_top, "log",
           "--pretty=format:", "--name-only"]
    if since:
        cmd.append(f"--since={since}")
    try:
        raw = subprocess.run(
            cmd, capture_output=True, text=True, check=True,
            timeout=GIT_TIMEOUT_SECONDS,
        ).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return {}

    counts: dict[Path, int] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        path = (repo / line).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            continue
        counts[path] = counts.get(path, 0) + 1
    return counts


def file_last_commit_days(path: Path) -> int | None:
    """Days since `path` was last committed in git. None if not tracked.

    Distinct from churn count - this is the staleness axis (how long since
    the file last moved), used to colour the docs-staleness heatmap and to
    compute the doc-vs-code staleness ratio. None means "no git history for
    this file" so callers can degrade rather than treating untracked as fresh.
    """
    import datetime as _dt

    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%ct", "--", str(path)],
            cwd=path.parent if path.parent.exists() else Path.cwd(),
            capture_output=True, text=True, check=False,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    raw = out.stdout.strip()
    if not raw:
        return None
    try:
        ts = int(raw)
    except ValueError:
        return None
    delta = _dt.datetime.now().timestamp() - ts
    return max(0, int(delta // 86400))


# Time windows tried in order from narrowest to widest. The first one
# that gives both gradient depth (max >= MIN_MAX) AND visual coverage
# (>= MIN_COVERAGE_PCT of files touched) wins. If nothing qualifies, we
# fall back to the widest window that returned any data. since=None
# means "full history".
CHURN_WINDOWS: list[tuple[str, str | None]] = [
    ("last 12mo", "12 months ago"),
    ("last 24mo", "24 months ago"),
    ("last 5y",   "5 years ago"),
    ("all-time",  None),
]
MIN_MAX = 3          # max commits per file must clear this for visible gradient
MIN_COVERAGE_PCT = 10.0   # % of scored files with any activity in the window


def pick_churn_window(
    root: Path, file_paths: list[Path],
) -> tuple[dict[Path, int] | None, str | None]:
    """Walk CHURN_WINDOWS narrowest-to-widest; return the first window
    that gives both gradient depth and visual coverage. Falls back to
    the widest non-empty window if none qualify.

    `file_paths` is the set of scored files; "touched" means touched AND
    scoreable, so coverage % is comparable across windows. Returns
    (per-file-count-dict, "commits (<label>)") or (None, None) when the
    path isn't inside a git repo.
    """
    total_files = max(len(file_paths), 1)
    widest_fallback: tuple[dict[Path, int], str] | None = None

    for label, since in CHURN_WINDOWS:
        data = git_churn_scores(root, since=since)
        if not data:
            continue
        scored_hits = {p: data[p] for p in file_paths if p in data}
        if not scored_hits:
            continue
        if widest_fallback is None or label == CHURN_WINDOWS[-1][0]:
            widest_fallback = (data, label)
        coverage = 100.0 * len(scored_hits) / total_files
        max_commits = max(scored_hits.values())
        if max_commits >= MIN_MAX and coverage >= MIN_COVERAGE_PCT:
            if label != CHURN_WINDOWS[0][0]:
                print(f"note: activity sparse - widened window to "
                      f"{label} ({coverage:.0f}% of files touched, "
                      f"max {max_commits} commits).",
                      file=sys.stderr)
            return ({p: data.get(p, 0) for p in file_paths},
                    f"commits ({label})")

    if widest_fallback is not None:
        data, label = widest_fallback
        scored_hits = {p: data[p] for p in file_paths if p in data}
        coverage = 100.0 * len(scored_hits) / total_files
        max_commits = max(scored_hits.values()) if scored_hits else 0
        print(f"note: activity below thresholds in every window; "
              f"using {label} ({coverage:.0f}% coverage, "
              f"max {max_commits}).", file=sys.stderr)
        return ({p: data.get(p, 0) for p in file_paths},
                f"commits ({label})")
    return None, None
