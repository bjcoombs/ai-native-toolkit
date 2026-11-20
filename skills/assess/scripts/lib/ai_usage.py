"""AI-assisted change share: a lower bound on agent use, from co-author trailers.

The readiness layers say whether a repository is set up for agents, not whether
agents are used on it. This scan counts, over the run's history window, the
commits that carry a ``Co-authored-by`` trailer naming an AI assistant, using
the AI-assistant subset of the shared matcher in ``lib.agent_identity``. That
subset drops non-Copilot ``[bot]`` accounts, which ``authorship_analysis``
counts as agents, so the two blocks need not agree.

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

Scope: commits touching the ``--scope`` subtree when the core passes one
(``git log -- <scope>``), otherwise every commit under ``repo_root``.

Automation bots. A GitHub squash merge writes a ``Co-authored-by`` trailer for
every other commit author on the pull request, so a ``pre-commit-ci[bot]`` or
``github-actions[bot]`` fix-up commit would make a human change read as
AI-assisted. Only AI-assistant trailers count
(``agent_identity.coauthors_have_ai_assistant``: the shared matcher with
non-Copilot ``[bot]`` accounts removed), which keeps the value a floor.

Privacy: counts, a share and fixed strings only. No name, e-mail or commit
subject reaches the block. A missing git, a timeout, a non-repository and a
shallow clone each degrade to ``{"available": False, "reason"}`` with a fixed
reason of their own (``REASON_*``).
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Literal, TypedDict

from lib.agent_identity import COVERED_AGENTS, coauthors_have_ai_assistant
from lib.history_window import split_window

GIT_TIMEOUT_SECONDS = 20

MERGE_RULE = "excluded"
ALL_TIME = "all-time"

LIMITS = (
    "Lower bound: squash merges that drop Co-authored-by trailers are undercounted.",
    "Lower bound: commits whose agent trailer was stripped or never written are undercounted.",
    "Lower bound: agent-authored commits without an agent co-author trailer are not counted.",
    "Lower bound: only the covered agents are recognised; other tools are not counted.",
    "Automation bot co-authors (Dependabot, pre-commit-ci, github-actions) are not counted as AI assistance.",
    "Merge commits are excluded; each merged commit counts once.",
)

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
    try:
        count, unit = split_window(window)
    except ValueError:
        raise ValueError(f"unrecognised history window {window!r}") from None
    return window, f"{count} {_UNIT[unit]} ago"


REASON_NO_GIT = "git is not installed"
REASON_TIMEOUT = f"git log timed out after {GIT_TIMEOUT_SECONDS}s"
REASON_NOT_A_REPO = "not a git repository, or a repository with no commits"
REASON_SHALLOW = "shallow clone: history is truncated, so the share is not a floor"
REASON_SHALLOW_UNKNOWN = "could not confirm the clone is not shallow"


class _GitUnavailable(Exception):
    """A git read that failed, with a fixed reason for the block."""


def _git(repo_root: Path, *args: str) -> str:
    """``git -C repo_root <args>`` stdout; ``_GitUnavailable`` with a fixed reason."""
    try:
        return subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=True, timeout=GIT_TIMEOUT_SECONDS,
        ).stdout
    except FileNotFoundError as e:
        raise _GitUnavailable(REASON_NO_GIT) from e
    except subprocess.TimeoutExpired as e:
        raise _GitUnavailable(REASON_TIMEOUT) from e
    except subprocess.CalledProcessError as e:
        raise _GitUnavailable(REASON_NOT_A_REPO) from e


def _trailer_fields(repo_root: Path, scope: Path | None, since: str | None) -> list[str]:
    """One co-author field per non-merge commit in the window.

    Raises ``_GitUnavailable`` when git cannot answer, or when the clone is
    shallow (or cannot be shown not to be): a truncated history can push the
    share above the true value, so it is no floor.
    """
    args = ["log", "--no-merges",
            "--format=%x1e%(trailers:key=Co-authored-by,valueonly,separator=%x1d)"]
    if since is not None:
        args.append(f"--since={since}")
    args += ["--", str(scope) if scope is not None else "."]
    out = _git(repo_root, *args)
    try:
        shallow = _git(repo_root, "rev-parse", "--is-shallow-repository").strip()
    except _GitUnavailable as e:
        raise _GitUnavailable(REASON_SHALLOW_UNKNOWN) from e
    if shallow != "false":
        raise _GitUnavailable(REASON_SHALLOW if shallow == "true" else REASON_SHALLOW_UNKNOWN)
    # Each record starts with RS; the first split element is the empty prefix.
    return out.split("\x1e")[1:]


def scan_ai_usage(
    repo_root: Path, scope: Path | None, history_window: str | None,
) -> AiUsageBlock | AiUsageUnavailable:
    """Count the window's commits and those with an AI-assistant co-author trailer."""
    label, since = resolve_window(history_window)
    try:
        fields = _trailer_fields(repo_root, scope, since)
    except _GitUnavailable as e:
        return {"available": False, "reason": str(e)}
    total = len(fields)
    agent = sum(1 for f in fields if coauthors_have_ai_assistant(f.strip("\n")))
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
