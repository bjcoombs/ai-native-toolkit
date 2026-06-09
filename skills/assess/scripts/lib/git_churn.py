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

import math
import subprocess
import sys
from collections.abc import Iterable
from functools import lru_cache
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


def git_commit_info(root: Path) -> dict:
    """Capture the commit the scan measured, so the report can pin its absolute
    LOC/CCN figures to a snapshot and warn when that snapshot is stale.

    Issue #59 observed figures drifting 15-25% low because a run measured an
    older commit than the one a reader later compared against. Absolute numbers
    are only trustworthy against a named commit; this surfaces that commit plus
    two staleness signals.

    Returns a dict:
      - ``available``: False (with ``reason``) when ``root`` isn't a git repo or
        git is unreachable; the report then omits the snapshot line.
      - ``head_sha`` / ``head_short``: the commit HEAD pointed at during the scan.
      - ``committed_date``: ISO-8601 author date of HEAD.
      - ``subject``: HEAD's commit subject line.
      - ``dirty``: True when tracked files have uncommitted changes, so the
        measured numbers reflect the working tree, not any single commit.
      - ``upstream``: the upstream tracking ref (e.g. ``origin/main``) or None.
      - ``behind``: commits HEAD is behind ``upstream`` (0 = up to date,
        None = no upstream configured), i.e. how stale the snapshot is vs remote.
    """
    def _git(*args: str) -> str | None:
        try:
            out = subprocess.run(
                ["git", "-C", str(root), *args],
                capture_output=True, text=True, check=True,
                timeout=GIT_TIMEOUT_SECONDS,
            )
        except (subprocess.CalledProcessError, FileNotFoundError,
                subprocess.TimeoutExpired):
            return None
        return out.stdout.strip()

    head_sha = _git("rev-parse", "HEAD")
    if not head_sha:
        return {"available": False,
                "reason": "not a git repo or no commits on HEAD"}

    info: dict = {
        "available": True,
        "head_sha": head_sha,
        "head_short": head_sha[:12],
        "committed_date": _git("show", "-s", "--format=%cd", "--date=short",
                               "HEAD"),
        "subject": _git("show", "-s", "--format=%s", "HEAD"),
        # `--porcelain` with untracked excluded: a non-empty result means the
        # scan saw uncommitted edits to tracked files, so its numbers don't
        # match the HEAD commit exactly.
        "dirty": bool(_git("status", "--porcelain", "--untracked-files=no")),
        "upstream": None,
        "behind": None,
    }

    upstream = _git("rev-parse", "--abbrev-ref", "--symbolic-full-name",
                    "@{upstream}")
    if upstream:
        info["upstream"] = upstream
        behind = _git("rev-list", "--count", "HEAD..@{upstream}")
        try:
            info["behind"] = int(behind) if behind is not None else None
        except ValueError:
            info["behind"] = None
    return info


def file_last_commit_epoch(path: Path) -> int | None:
    """Unix timestamp (epoch seconds) of `path`'s last commit. None if untracked.

    The raw commit time, before the days-ago conversion `file_last_commit_days`
    applies. Provenance-aware staleness needs the absolute timestamp so it can
    compare a generated doc against its declared source on the same axis (epoch
    seconds), independent of "now" - see `lib.doc_provenance`.
    """
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
        return int(raw)
    except ValueError:
        return None


def file_last_commit_days(path: Path) -> int | None:
    """Days since `path` was last committed in git. None if not tracked.

    Distinct from churn count - this is the staleness axis (how long since
    the file last moved), used to colour the docs-staleness heatmap and to
    compute the doc-vs-code staleness ratio. None means "no git history for
    this file" so callers can degrade rather than treating untracked as fresh.
    """
    import datetime as _dt

    ts = file_last_commit_epoch(path)
    if ts is None:
        return None
    delta = _dt.datetime.now().timestamp() - ts
    return max(0, int(delta // 86400))


@lru_cache(maxsize=8)
def tracked_files(root: Path) -> frozenset[Path] | None:
    """Resolved absolute paths of git-tracked files under `root`.

    This is the precise definition of "files in the repo": it excludes
    untracked and ignored files (e.g. a contributor's personal CLAUDE.md left
    in the working tree). Returns None when `root` isn't a git repo, so callers
    fall back to a plain filesystem walk.

    Cached (read-only result) so the several callers in one assessment - doc
    graph, staleness (x2), instruction grading - don't each shell out to git.
    Pass an already-resolved `root` for cache hits.
    """
    try:
        repo_top = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True, timeout=GIT_TIMEOUT_SECONDS,
        ).stdout.strip()
        raw = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--full-name", "-z"],
            capture_output=True, text=True, check=True, timeout=GIT_TIMEOUT_SECONDS,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None
    repo = Path(repo_top)
    out: set[Path] = set()
    for rel in raw.split("\0"):
        if not rel:
            continue
        try:
            out.add((repo / rel).resolve())
        except OSError:
            continue
    return frozenset(out)


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


# A churn window is "degenerate" when its commits-per-file distribution is
# effectively flat: almost every file that moved in the window shows a single
# commit. That is the fingerprint of a history with no usable churn signal -
# a shallow clone, a fresh import, or a squashed/extracted source tree where
# every file was created in one bulk commit. A count of "1 commit" there is an
# extraction artifact, not a measure of how much the file actually churns.
#
# The danger is that downstream signals read the count as if it meant activity:
# `code_churn_in_window` swells to ~= the file count, the doc-staleness ratio
# inflates, and a *precise* doc->code association (high `subject_method`
# confidence) then stamps a high-confidence `lying_map` onto pure noise. The two
# properties are independent - association precision says *which* code a doc maps
# to; measurement reliability says whether the churn count *means* anything - and
# only this flag carries the second. Consumers that read it degrade: cap finding
# confidence, drop churn-derived findings from the summary, flatten the treemap
# saturation axis. This is the single source of truth; consumers thread the
# boolean rather than recomputing the distribution.
DEGENERATE_CHURN_P95 = 1
# Below this many files with any activity the distribution is too small to judge.
# A three-file utility repo where each file shows one commit is not the
# shallow-clone / squashed-history artifact this guards against, so we never call
# degeneracy on a handful of files - we'd risk flattening a genuinely tiny repo.
MIN_ACTIVE_FILES_FOR_DEGENERACY = 5


def churn_is_degenerate(
    commit_counts: Iterable[int],
    min_active_files: int = MIN_ACTIVE_FILES_FOR_DEGENERACY,
    p95_threshold: int = DEGENERATE_CHURN_P95,
) -> bool:
    """True when a per-file commit-count distribution carries no churn signal.

    `commit_counts` is any iterable of per-file commit counts - the values of a
    churn map (``pick_churn_window`` / ``git_churn_scores`` output). Files with
    zero commits in the window are ignored: degeneracy is a property of the
    *shape* of the activity among files that actually moved, not of how many sat
    idle. Among those active files we take the 95th percentile (nearest-rank, so
    a couple of genuinely-churned files can't mask an otherwise-flat import) and
    call the window degenerate when it is at or below ``p95_threshold`` (1 by
    default - "almost every touched file shows a single commit"). p95 rather than
    max so the detector reports on the bulk of the distribution, equivalent to
    "near-zero variance across files".

    Returns False when fewer than ``min_active_files`` files have any activity:
    too few data points to distinguish a degenerate history from a legitimately
    small or quiet repo, where flattening the churn signal would be the wrong
    call. Pure and side-effect-free so every consumer (doc-staleness join,
    keyhole summary, treemap) can read the same verdict off the same data.
    """
    active = sorted(c for c in commit_counts if c and c > 0)
    if len(active) < min_active_files:
        return False
    # Nearest-rank p95: index of the ceil(0.95 * n)-th value (1-based), so for an
    # all-ones distribution p95 == 1 and a single outlier among thousands of
    # ones still leaves p95 == 1.
    idx = max(0, math.ceil(0.95 * len(active)) - 1)
    return active[idx] <= p95_threshold
