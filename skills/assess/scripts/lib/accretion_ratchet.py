"""Accretion-ratchet scan: files that only ever grow.

The first of the three write-side tendencies named in this repo's north star.
An agent does what is asked, and what is asked is feature after feature -
nothing in that loop ever asks for a refactor, so files only grow. Absent a
consciously requested restructuring, size and complexity ratchet monotonically
upward. This module turns that tendency into a deterministic signal: the file
whose accumulated line count never meaningfully comes back down.

The instrument walks a file's full numstat history in *author-time order* and
accumulates net delta (additions - deletions). A file is flagged when two
conditions hold together:

  - **Monotonic growth.** The running net-delta is non-decreasing across the
    history - the file gained lines and effectively never gave them back. A
    single late refactor that cuts the file is enough to clear the flag.
  - **Low deletion fraction.** Across its whole history, deletions are a small
    share of total churn (``deletions / (additions + deletions)`` below a
    threshold). A file that churns by rewriting - deleting as much as it adds -
    is being maintained, not merely accreted, even if its net size still drifts
    up.

Both are required: net growth alone is normal for any developing file; it is
growth *with almost no deletion pressure* that fingerprints pure accretion.

Determinism is non-negotiable - same repo, byte-identical output. The history
is parsed once and sorted by author time (``%at``) with the commit SHA as a
tie-breaker, so the accumulation order never depends on git's traversal
heuristics or clone-to-clone ref ordering. ``--no-renames`` keeps a file under
its current name only (a rename is not accretion), and ``--no-merges`` keeps
merge double-counts out of the totals.

Pure subprocess (git) + stdlib. No LLM calls, no heavy dependencies, every git
call capped by ``GIT_TIMEOUT_SECONDS``. Degrades to ``available: False`` on any
git failure and to ``reliable: False`` on degenerate history (shallow clone,
squashed import - same verdict as every other churn consumer), never raises out
of ``scan_accretion_ratchet``.

CLI (standalone use)::

    uv run accretion_ratchet.py <repo_root> [--deletion-threshold 0.15] [--json OUT]
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from lib.git_churn import GIT_TIMEOUT_SECONDS, churn_is_degenerate
except ImportError:  # standalone CLI: script dir is lib/, put scripts/ on path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from lib.git_churn import GIT_TIMEOUT_SECONDS, churn_is_degenerate

# A file is flagged only when deletions are below this share of its total churn:
# deletions / (additions + deletions). 0.15 means "fewer than ~1 deleted line
# for every 6 lines of churn" - the fingerprint of a file that is appended to
# rather than reworked. A file that churns by rewriting clears the flag.
DELETION_FRACTION_THRESHOLD = 0.15

# Renames register as a single all-additions commit under the new name, which
# would read as perfect accretion. Requiring at least this many commits filters
# those single-touch artifacts: accretion is a property of repeated growth.
MIN_COMMITS_FOR_ACCRETION = 3

# Average days per month for the time-span readout (Gregorian mean).
DAYS_PER_MONTH = 30.44

# Cap the files carried into run-context.json so a pathological repo can't bloat
# the bus; the scan still measures every file, only the report list is capped.
MAX_TOP_OFFENDERS = 10


@dataclass(frozen=True)
class AccretionFile:
    """A file whose line count only ever ratcheted upward.

    ``net_additions`` is the final accumulated additions - deletions (always
    positive for a flagged file). ``deletion_fraction`` is deletions over total
    churn across the whole history. ``time_span_months`` is the span from the
    file's first to last commit, a context cue for how long the ratchet ran.
    """

    path: str
    net_additions: int
    commit_count: int
    deletion_fraction: float
    time_span_months: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "net_additions": self.net_additions,
            "commit_count": self.commit_count,
            "deletion_fraction": round(self.deletion_fraction, 4),
            "time_span_months": round(self.time_span_months, 1),
        }


@dataclass
class AccretionScan:
    """Result of an accretion-ratchet scan.

    ``available`` is False when the directory is not a git repo or git is
    unreachable. ``reliable`` is False on degenerate history (shallow clone,
    squashed import) where the per-file commit distribution carries no signal -
    same verdict definition as every other churn consumer. ``files`` holds the
    flagged ``AccretionFile`` records, sorted by net additions descending.
    """

    available: bool
    reason: str = ""
    reliable: bool = True
    deletion_fraction_threshold: float = DELETION_FRACTION_THRESHOLD
    files: list[AccretionFile] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        top = self.files[:MAX_TOP_OFFENDERS]
        return {
            "available": self.available,
            "reason": self.reason,
            "reliable": self.reliable,
            "deletion_fraction_threshold": self.deletion_fraction_threshold,
            "total_accreting": len(self.files),
            "top_offenders": [f.to_dict() for f in top],
        }


@dataclass
class _FileHistory:
    """Per-file accumulation state, built up while walking the commit history."""

    additions: int = 0
    deletions: int = 0
    commit_count: int = 0
    first_time: int = 0
    last_time: int = 0
    # Running net delta after each commit, in author-time order. Monotonicity is
    # judged off this sequence, not the endpoints, so a file that grew, was cut
    # back, then grew again is not mistaken for a pure ratchet.
    net_sequence: list[int] = field(default_factory=list)


def _repo_top(repo_root: Path) -> str | None:
    """Absolute repo top-level for ``repo_root``, or None if not in a git repo."""
    try:
        return subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True, timeout=GIT_TIMEOUT_SECONDS,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _accumulate_history(repo_top: str) -> dict[str, _FileHistory]:
    """Walk the full numstat history and accumulate per-file growth.

    Returns ``{repo-relative-path: _FileHistory}``. Each commit contributes its
    author time (``%at``) plus the numstat rows that follow it. The history is
    sorted by (author_time, sha) before accumulation so the running net-delta
    sequence is built in a clone-independent order - the load-bearing
    reproducibility guarantee. Binary files (numstat ``-`` for added/removed)
    are skipped: they carry no line-count signal.
    """
    # \x1e (RS) opens each commit record; the header is "<author_time> <sha>",
    # then the numstat rows ("added\tremoved\tpath") on their own lines.
    cmd = [
        "git", "-C", repo_top, "log",
        "--no-merges", "--no-renames", "--numstat",
        "--pretty=format:\x1e%at %H",
    ]
    try:
        raw = subprocess.run(
            cmd, capture_output=True, text=True, check=True, errors="replace",
            timeout=GIT_TIMEOUT_SECONDS,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return {}

    # Parse into (author_time, sha, [(added, removed, path), ...]) per commit,
    # then sort. We do NOT trust git's emission order: --reverse / traversal
    # order can differ across clones, so the ordering pin is an explicit sort
    # on (author_time, sha) rather than the order git happened to print.
    commits: list[tuple[int, str, list[tuple[int, int, str]]]] = []
    for chunk in raw.split("\x1e"):
        if not chunk.strip():
            continue
        lines = chunk.splitlines()
        header = lines[0].split(" ", 1)
        if len(header) != 2:
            continue
        try:
            author_time = int(header[0])
        except ValueError:
            continue
        sha = header[1]
        rows: list[tuple[int, int, str]] = []
        for row in lines[1:]:
            parts = row.split("\t")
            if len(parts) < 3:
                continue
            added_s, removed_s, path = parts[0], parts[1], parts[2]
            # Binary files numstat as "-\t-\t<path>": no line-count signal.
            if not added_s.isdigit() or not removed_s.isdigit():
                continue
            rows.append((int(added_s), int(removed_s), path))
        commits.append((author_time, sha, rows))

    commits.sort(key=lambda c: (c[0], c[1]))

    histories: dict[str, _FileHistory] = defaultdict(_FileHistory)
    for author_time, _sha, rows in commits:
        for added, removed, path in rows:
            hist = histories[path]
            hist.additions += added
            hist.deletions += removed
            hist.commit_count += 1
            if hist.first_time == 0:
                hist.first_time = author_time
            hist.last_time = author_time
            hist.net_sequence.append(hist.additions - hist.deletions)
    return histories


def _is_monotonic_nondecreasing(sequence: list[int]) -> bool:
    """True when the running net-delta never falls below an earlier high.

    A single step down (a commit that deleted more than it added, net) breaks
    the ratchet - that is the deletion pressure the signal looks for. An empty
    or single-point sequence is vacuously monotonic.
    """
    return all(b >= a for a, b in zip(sequence, sequence[1:]))


def _build_accretion_file(
    path: str, hist: _FileHistory, deletion_threshold: float
) -> AccretionFile | None:
    """Promote one file's history to an AccretionFile, or None if it isn't accreting.

    Applies the multi-commit gate, the monotonic-growth test, and the
    deletion-fraction threshold. A file passes only when it grew across multiple
    commits, its running net-delta never came back down, and its deletions stayed
    below ``deletion_threshold`` share of total churn. The threshold is the
    caller's value (see :func:`scan_accretion_ratchet`) so the cut applied is the
    one reported, with no second filter downstream.
    """
    if hist.commit_count < MIN_COMMITS_FOR_ACCRETION:
        return None

    total_churn = hist.additions + hist.deletions
    if total_churn == 0:
        return None
    deletion_fraction = hist.deletions / total_churn
    if deletion_fraction >= deletion_threshold:
        return None

    net = hist.additions - hist.deletions
    # A net-zero or net-negative file is not accreting even if its history reads
    # as non-decreasing (e.g. a file that was only ever deleted from).
    if net <= 0:
        return None
    if not _is_monotonic_nondecreasing(hist.net_sequence):
        return None

    span_days = max(0, (hist.last_time - hist.first_time)) / 86400
    time_span_months = span_days / DAYS_PER_MONTH
    return AccretionFile(
        path=path,
        net_additions=net,
        commit_count=hist.commit_count,
        deletion_fraction=deletion_fraction,
        time_span_months=time_span_months,
    )


def scan_accretion_ratchet(
    repo_root: Path,
    deletion_threshold: float = DELETION_FRACTION_THRESHOLD,
) -> AccretionScan:
    """Full pipeline: parse numstat history -> accumulate -> flag pure-growth files.

    Returns an :class:`AccretionScan`. ``available`` is False when ``repo_root``
    is not inside a git repo or git is unreachable; ``reliable`` is False on
    degenerate history. Flagged files are sorted by net additions descending,
    then by path for a stable tie-break.
    """
    repo_top = _repo_top(repo_root)
    if repo_top is None:
        return AccretionScan(available=False, reason="not a git repository")

    try:
        histories = _accumulate_history(repo_top)
        if not histories:
            return AccretionScan(available=False, reason="no commit history")

        # Degenerate history (every file ~1 commit: shallow clone, squashed
        # import) means the accumulation sequence carries no signal. Report what
        # we found but mark it unreliable so nothing downstream reads it as a
        # clean bill of health. Same verdict definition as git_churn.
        commit_counts = [h.commit_count for h in histories.values()]
        reliable = not churn_is_degenerate(commit_counts)

        files: list[AccretionFile] = []
        for path, hist in histories.items():
            accreting = _build_accretion_file(path, hist, deletion_threshold)
            if accreting is not None:
                files.append(accreting)

        files.sort(key=lambda f: (-f.net_additions, f.path))
        return AccretionScan(
            available=True,
            reliable=reliable,
            deletion_fraction_threshold=deletion_threshold,
            files=files,
        )
    except Exception as exc:  # noqa: BLE001 - degrade, never crash the core
        return AccretionScan(
            available=False, reason=f"{type(exc).__name__}: {exc}"
        )


def main() -> int:
    import argparse
    import time

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("repo_root", type=Path)
    ap.add_argument(
        "--deletion-threshold", type=float, default=DELETION_FRACTION_THRESHOLD,
        help="flag files whose deletion fraction is below this (default 0.15)",
    )
    ap.add_argument("--json", type=Path, help="write full summary JSON here")
    args = ap.parse_args()

    t0 = time.monotonic()
    scan = scan_accretion_ratchet(args.repo_root.resolve(), args.deletion_threshold)
    elapsed = time.monotonic() - t0
    s = scan.summary()
    s["elapsed_seconds"] = round(elapsed, 2)

    if args.json:
        args.json.write_text(json.dumps(s, indent=2))

    if not scan.available:
        print(f"unavailable: {scan.reason}")
        return 1
    reliability = "reliable" if scan.reliable else "UNRELIABLE (degenerate history)"
    print(
        f"scanned in {elapsed:.2f}s  threshold={scan.deletion_fraction_threshold} "
        f"deletion-fraction  {reliability}"
    )
    print(f"accreting files: {s['total_accreting']}")
    print("\ntop offenders (net additions, never meaningfully cut back):")
    for f in s["top_offenders"]:
        print(
            f"  +{f['net_additions']:>7}  {f['commit_count']:>3} commits  "
            f"del {f['deletion_fraction']:.2f}  over {f['time_span_months']:.1f}mo  "
            f"{f['path']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
