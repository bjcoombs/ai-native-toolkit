"""GitHub Actions cost estimate for the /assess CI gate.

The CI-gate offer asks a user to add a workflow that runs on every pull request.
The cost of saying yes is Actions minutes, which private repositories pay for,
so the offer states it: merged pull requests in the last ``WINDOW_DAYS`` days
(one gate run each, a floor: re-pushes add runs) times ``MINUTES_PER_RUN``.

Merged pull requests come from ``gh pr list --state merged`` through
``gh_cli``, not ``git log --merges``: a squash-merging repository has no merge
commits, so git history reads zero there.

Block on success: ``{"available": True, "runs_per_month", "minutes_per_run",
"minutes_per_month", "assumption", "capped", "private"}``. ``capped`` is True
when the listing hit ``PR_LIMIT``, so the counts are lower bounds. ``private``
is ``None`` when ``gh`` cannot say. No remote, no ``gh``, no auth, a failed read or zero merged
pull requests degrade to ``{"available": False, "reason"}``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from lib.gh_cli import GhUnavailable, gh_json, open_github, unavailable

# Assumed wall-clock minutes per gate run. One measured run took 4m51s on a
# 3,009-file repository; the figure is an assumption, not a measurement of the
# repository being assessed, and the block says so.
MINUTES_PER_RUN = 5

WINDOW_DAYS = 30

# gh pr list returns 30 by default; ask for more than any realistic month.
PR_LIMIT = 1000


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_private(slug: str) -> bool | None:
    """The repository's visibility, or None when gh cannot tell."""
    try:
        info = gh_json(["repo", "view", slug, "--json", "isPrivate"])
    except GhUnavailable:
        return None
    value = info.get("isPrivate") if isinstance(info, dict) else None
    return value if isinstance(value, bool) else None


def estimate_gate_cost(repo_root: Path, now: datetime | None = None) -> dict[str, Any]:
    """Estimate the gate's monthly Actions runs and minutes from merged PRs."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=WINDOW_DAYS)
    try:
        repo = open_github(repo_root)
        prs = gh_json([
            "pr", "list", "--repo", repo.slug, "--state", "merged",
            "--search", f"merged:>={since.date().isoformat()}",
            "--limit", str(PR_LIMIT), "--json", "number,mergedAt",
        ])
    except GhUnavailable as e:
        return unavailable(e.reason)
    if not isinstance(prs, list):
        return unavailable("gh_bad_json: `gh pr list` did not return a list")

    # The search qualifier is day-granular; the exact window is applied here.
    runs = 0
    for pr in prs:
        merged = _parse_time(pr.get("mergedAt")) if isinstance(pr, dict) else None
        if merged is not None and merged >= since:
            runs += 1
    if runs == 0:
        return unavailable(
            f"no_merge_history: no pull requests merged in the last {WINDOW_DAYS} days"
        )

    capped = len(prs) >= PR_LIMIT
    return {
        "available": True,
        "runs_per_month": runs,
        "minutes_per_run": MINUTES_PER_RUN,
        "minutes_per_month": runs * MINUTES_PER_RUN,
        "assumption": (
            f"Assumes {MINUTES_PER_RUN} minutes per gate run (a fixed figure, not "
            f"measured on this repository) and one run per merged pull request: "
            f"{'at least ' if capped else ''}{runs} merged in the last {WINDOW_DAYS} days. Re-pushes to an "
            f"open pull request add runs, so the run count is a floor."
        ),
        "capped": capped,
        "private": _is_private(repo.slug),
    }
