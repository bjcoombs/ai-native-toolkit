"""AI-assisted change share: a lower bound on agent use, from co-author trailers.

The readiness layers say whether a repository is set up for agents, not whether
agents are used on it. This scan counts, over the run's history window, the
commits that carry a ``Co-authored-by`` trailer naming an agent, using the one
agent-identity definition in ``lib.agent_identity`` (the same matcher the
authorship analysis uses).

Block (``ai_usage`` in run-context.json)::

    {"available": True,
     "window": str,                # the history window applied, e.g. "12mo", "90d", "all-time"
     "since": str | None,          # the git --since expression behind it; None = full history
     "merge_rule": "excluded",     # merge commits are not counted (git log --no-merges)
     "total_commits": int,         # non-merge commits reachable from HEAD in the window
     "agent_trailer_commits": int, # of those, commits with at least one agent co-author trailer
     "agent_trailer_share": float | None,  # agent_trailer_commits / total_commits; None when 0 commits
     "lower_bound": True,
     "covered_agents": [str, ...], # lib.agent_identity.COVERED_AGENTS
     "limits": [str, ...]}         # LIMITS: why the value is a floor

The value is a floor, never an estimate: a squash merge that drops the trailers,
a tool that does not write one, an agent outside ``COVERED_AGENTS`` and an
agent-authored commit with no trailer all go uncounted. Merge commits are
excluded, the precedent ``change_coupling.authorship_analysis`` set, so a
merged branch counts its own commits once.

Window. ``history_window`` is in the ``<n>d|w|mo|y`` grammar (``"12mo"``):
``assess_core`` passes the churn window ``pick_churn_window`` chose for the
treemap, normalised by ``git_churn.window_from_churn_label``, so the share
covers the same history as the churn axis; the headless signals run passes
``--window``. None (all-time churn, no stats, no git) means full history,
recorded as ``"all-time"``.

Scope: commits touching the run root (``git log -- .`` from ``repo_root``), so
a ``--scope`` run of a monorepo subtree counts that subtree's commits.

Privacy: counts, a share and fixed strings only. No name, e-mail or commit
subject reaches the block. No git history degrades to
``{"available": False, "reason"}``.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Literal, TypedDict

from lib.agent_identity import COVERED_AGENTS, coauthors_have_agent

GIT_TIMEOUT_SECONDS = 20

MERGE_RULE = "excluded"
ALL_TIME = "all-time"

LIMITS = (
    "Lower bound: squash merges that drop Co-authored-by trailers are undercounted.",
    "Lower bound: commits whose agent trailer was stripped or never written are undercounted.",
    "Lower bound: agent-authored commits without an agent co-author trailer are not counted.",
    "Lower bound: only the covered agents are recognised; other tools are not counted.",
    "Merge commits are excluded; each merged commit counts once.",
)

_WINDOW = re.compile(r"^([1-9][0-9]*)(d|w|mo|y)$")
_UNIT = {"d": "days", "w": "weeks", "mo": "months", "y": "years"}


class AiUsageBlock(TypedDict):
    """The ``ai_usage`` run-context block when history was read."""

    available: Literal[True]
    window: str
    since: str | None
    merge_rule: str
    total_commits: int
    agent_trailer_commits: int
    agent_trailer_share: float | None
    lower_bound: Literal[True]
    covered_agents: list[str]
    limits: list[str]


class AiUsageUnavailable(TypedDict):
    available: Literal[False]
    reason: str


def resolve_window(window: str | None) -> tuple[str, str | None]:
    """``(label, since)`` for a ``<n>d|w|mo|y`` window, or all history for None.

    Raises ``ValueError`` on anything else, so a malformed window never silently
    widens to full history.
    """
    if window is None:
        return ALL_TIME, None
    m = _WINDOW.match(window)
    if m is None:
        raise ValueError(f"unrecognised history window {window!r}")
    return window, f"{m.group(1)} {_UNIT[m.group(2)]} ago"


def _trailer_fields(repo_root: Path, since: str | None) -> list[str] | None:
    """One co-author field per non-merge commit in the window, or None without git."""
    cmd = ["git", "-C", str(repo_root), "log", "--no-merges",
           "--format=%x1e%(trailers:key=Co-authored-by,valueonly,separator=%x1d)"]
    if since is not None:
        cmd.append(f"--since={since}")
    cmd += ["--", "."]
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, check=True, timeout=GIT_TIMEOUT_SECONDS,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None
    # Each record starts with RS; the first split element is the empty prefix.
    return out.split("\x1e")[1:]


def scan_ai_usage(
    repo_root: Path, history_window: str | None,
) -> AiUsageBlock | AiUsageUnavailable:
    """Count the window's commits and those with an agent co-author trailer."""
    label, since = resolve_window(history_window)
    fields = _trailer_fields(repo_root, since)
    if fields is None:
        return {"available": False, "reason": "no readable git history at the run root"}
    total = len(fields)
    agent = sum(1 for f in fields if coauthors_have_agent(f.strip("\n")))
    return {
        "available": True,
        "window": label,
        "since": since,
        "merge_rule": MERGE_RULE,
        "total_commits": total,
        "agent_trailer_commits": agent,
        "agent_trailer_share": round(agent / total, 4) if total else None,
        "lower_bound": True,
        "covered_agents": list(COVERED_AGENTS),
        "limits": list(LIMITS),
    }
