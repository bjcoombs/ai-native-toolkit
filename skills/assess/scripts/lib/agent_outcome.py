"""Revert and rework rate of agent commits: an informational outcome measure (#1085).

The readiness layers and the gates say how a repository is set up for agents;
nothing says whether agent work on it holds. This scan takes the agent commits
of the run window, the commits ``ai_usage`` counts in ``agent_either_commits``
(an AI-assistant ``Co-authored-by`` trailer, or an author the
``lib.agent_identity`` matcher puts in the agent bucket, ``[identities]`` from
``.assess/config.toml`` included), and records two outcomes:

- **reverted**: a later commit in the window says ``This reverts commit <sha>``
  (the ``git revert`` message) naming it, or restores the prior content: its
  ``git patch-id --stable`` equals the patch-id of the agent commit's diff
  reversed, and it comes after the agent commit in ``--topo-order``.
- **reworked**: some line the agent commit added is no longer attributed to it
  by ``git blame`` at its newest descendant on HEAD's history within ``REWORK_DAYS``
  days of it (``git rev-list -1 --ancestry-path --before``; a rename adds no line): another commit rewrote or deleted
  it. Only commits at least ``REWORK_DAYS`` older than HEAD's commit time are
  observed (a younger one has not had the full span), and of those the newest
  ``REWORK_SAMPLE`` are checked, so the cost stays bounded on a busy history.

Informational only: no Step, count, headline or maturity-model row reads it.

Block (``agent_outcome`` in run-context.json)::

    {"available": True,
     "window": str, "since": str | None,  # as ai_usage: the same window and bound
     "informational": True,
     "agent_rule": "trailer_or_author",   # which commits count as agent commits
     "agent_commits": int,                # == ai_usage.agent_either_commits
     "reverted_commits": int,             # agent commits reverted, either way
     "reverted_by_message": int,          # named by a "This reverts commit" message
     "reverted_by_restore": int,          # undone by a later inverse diff
     "revert_share": float | None,        # reverted_commits / agent_commits
     "revert_null_reason": str | None,    # "no_agent_commits" when the share is null
     "rework_days": int,                  # REWORK_DAYS
     "rework_observed_commits": int,      # agent commits >= rework_days older than HEAD
     "rework_sampled_commits": int,       # of those, the newest REWORK_SAMPLE checked
     "reworked_commits": int,
     "rework_share": float | None,        # reworked_commits / rework_sampled_commits
     "rework_null_reason": str | None,    # "no_agent_commits" | "none_old_enough"
     "limits": [str, ...]}                # LIMITS

Unknown reads null, never 0: a share with no commit to divide by is null with a
fixed token reason. A missing git, a timeout, a non-repository and a shallow
clone (a truncated history hides the reverting commit) each make the block
``{"available": False, "reason": "<code>: <sentence>"}`` with a code from
``REASON_CODES`` (declared as ``unknown_reasons`` in ``checks/core/agent_outcome.toml``),
which counts-only output keeps (``lib.counts_only.reason_codes``).

Scope: the agent commits are those touching the ``--scope`` subtree, as in
``ai_usage``, and rework reads only the lines they added inside it; the reverting and restoring commits are looked for across the
whole repository, since a path filter would cut the diffs the patch-ids compare.

Privacy: counts, shares and fixed strings only. No sha, path, name or subject
reaches the block.

Agent commit outcomes (#1085), informational. `scan_agent_outcome(repo_root, scope,
history_window, fleet_config)` reads the window's agent commits through
`ai_usage.window_commits` (trailer or agent author, `agent_rule: "trailer_or_author"`,
the `agent_either_commits` set) and emits `agent_outcome: {available, window, since,
informational, agent_rule, agent_commits, reverted_commits, reverted_by_message,
reverted_by_restore, revert_share, revert_null_reason, rework_days,
rework_observed_commits, rework_sampled_commits, reworked_commits, rework_share,
rework_null_reason, limits[]}`. Reverted: a later window commit's message names the
sha (`This reverts commit`, abbreviated ids matched by prefix) or its stable patch-id
equals the agent commit's reversed patch-id and it precedes it in `--topo-order`.
Reworked: `git blame` at its newest descendant on HEAD's history within `REWORK_DAYS`
(`--ancestry-path`) attributes fewer lines of some file to it than it added (`--numstat -M`,
so a rename adds no line; binary files and files gone by then skipped); observed only when `REWORK_DAYS` older than HEAD, newest
`REWORK_SAMPLE` checked. A share is null with a token reason (`no_agent_commits`,
`none_old_enough`). Git failures and shallow clones degrade to `available: false` with
a `code: sentence` reason from `REASON_CODES`. No Step, count or headline reads it.
Tests: `tests/test_agent_outcome.py`.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Literal, TypedDict

from lib.agent_identity import AGENT, coauthors_have_ai_assistant
from lib.ai_usage import (
    REASON_NO_GIT,
    REASON_NOT_A_REPO,
    REASON_SHALLOW,
    REASON_SHALLOW_UNKNOWN,
    REASON_TIMEOUT,
    matcher_from_config,
    resolve_window,
    window_commits,
)
from lib.account_discovery import HistoryCommit
from lib.fleet_config import FleetConfig

GIT_TIMEOUT_SECONDS = 60
REWORK_DAYS = 14
REWORK_SAMPLE = 50
AGENT_RULE = "trailer_or_author"
DAY_SECONDS = 86400

NO_AGENT_COMMITS = "no_agent_commits"
NONE_OLD_ENOUGH = "none_old_enough"

# A git read past the windowed log failed on a repository that log could read.
REASON_GIT_FAILED = "a git read of the history failed"

# The reason code each git failure degrades to, as ``code: sentence``.
REASON_CODES: dict[str, str] = {
    REASON_NO_GIT: "no_git",
    REASON_TIMEOUT: "git_timeout",
    REASON_NOT_A_REPO: "not_a_repo",
    REASON_SHALLOW: "shallow_clone",
    REASON_SHALLOW_UNKNOWN: "shallow_unknown",
    REASON_GIT_FAILED: "git_failed",
}
_OWN_TIMEOUT = f"git timed out after {GIT_TIMEOUT_SECONDS}s"

LIMITS = (
    "Informational: no Step, count or headline reads this block.",
    "Agent commits are those ai_usage counts (trailer or agent author), so the set is a lower bound.",
    "A revert is found by a 'This reverts commit' message naming the commit, or by a later "
    "commit whose diff is the exact inverse; a partial or hand-edited revert is not counted.",
    f"Rework: a line the commit added is no longer attributed to it by git blame {REWORK_DAYS} "
    "days later; a file renamed or deleted by then is skipped, and reformatting counts as rework.",
    f"Only agent commits at least {REWORK_DAYS} days older than HEAD are checked for rework, "
    f"the newest {REWORK_SAMPLE} of them.",
    "Reverting and restoring commits are looked for in the same window, on HEAD's history only.",
)

_REVERTS = re.compile(r"This reverts commit ([0-9a-f]{7,64})", re.IGNORECASE)


class AgentOutcomeBlock(TypedDict):
    """The ``agent_outcome`` run-context block when history was read."""

    available: Literal[True]
    window: str
    since: str | None
    informational: Literal[True]
    agent_rule: str
    agent_commits: int
    reverted_commits: int
    reverted_by_message: int
    reverted_by_restore: int
    revert_share: float | None
    revert_null_reason: str | None
    rework_days: int
    rework_observed_commits: int
    rework_sampled_commits: int
    reworked_commits: int
    rework_share: float | None
    rework_null_reason: str | None
    limits: list[str]


class AgentOutcomeUnavailable(TypedDict):
    available: Literal[False]
    reason: str


class _GitFailed(Exception):
    """A git read that failed; the message is a ``code: sentence`` reason."""


def _git(repo_root: Path, *args: str, stdin: str | None = None) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(repo_root), *args], input=stdin,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=True, timeout=GIT_TIMEOUT_SECONDS,
        ).stdout
    except FileNotFoundError as e:
        raise _GitFailed(f"no_git: {REASON_NO_GIT}") from e
    except subprocess.TimeoutExpired as e:
        raise _GitFailed(f"git_timeout: {_OWN_TIMEOUT}") from e
    except subprocess.CalledProcessError as e:
        raise _GitFailed(f"git_failed: {REASON_GIT_FAILED}") from e


def _share(count: int, total: int) -> float | None:
    return round(count / total, 4) if total else None


def agent_commits(commits: list[HistoryCommit], fleet_config: FleetConfig | None) -> list[HistoryCommit]:
    """The commits ``ai_usage`` counts in ``agent_either_commits``, in log order."""
    matcher = matcher_from_config(fleet_config)
    return [c for c in commits
            if coauthors_have_ai_assistant(c.coauthors)
            or matcher.classify(c.author_email, c.author_name).bucket == AGENT]


def _window_log(repo_root: Path, since: str | None) -> list[tuple[str, str]]:
    """(sha, full message) of every non-merge commit in the window, newest first in
    topological order, across the whole repository."""
    bound = [f"--since={since}"] if since else []
    out = _git(repo_root, "log", "--no-merges", "--topo-order", "--format=%x1e%H%x1f%B", *bound)
    rows = []
    for record in out.split("\x1e")[1:]:
        sha, _, body = record.partition("\x1f")
        rows.append((sha.strip(), body))
    return rows


def _patch_ids(text: str) -> dict[str, str]:
    """sha -> stable patch-id of ``git patch-id`` over ``commit <sha>`` diffs."""
    ids = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            ids[parts[1]] = parts[0]
    return ids


def _diff_patch_ids(repo_root: Path, shas: list[str], reverse: bool) -> dict[str, str]:
    if not shas:
        return {}
    args = ["log", "--no-walk=unsorted", "--stdin", "-p", "--no-renames", "--no-prefix",
            "--format=commit %H"]
    diff = _git(repo_root, *args, *(["-R"] if reverse else []), stdin="\n".join(shas) + "\n")
    return _patch_ids(_git(repo_root, "patch-id", "--stable", stdin=diff))


def _reverted(repo_root: Path, agents: list[HistoryCommit],
              since: str | None) -> tuple[set[str], set[str]]:
    """The agent shas reverted by message, and those restored by an inverse diff."""
    log = _window_log(repo_root, since)
    by_message: set[str] = set()
    named = [m.group(1).lower() for _, body in log for m in _REVERTS.finditer(body)]
    for c in agents:
        if any(c.sha.startswith(n) for n in named):
            by_message.add(c.sha)
    order = {sha: i for i, (sha, _) in enumerate(log)}
    forward = _diff_patch_ids(repo_root, [sha for sha, _ in log], reverse=False)
    by_patch: dict[str, list[str]] = {}
    for sha, pid in forward.items():
        by_patch.setdefault(pid, []).append(sha)
    inverse = _diff_patch_ids(repo_root, [c.sha for c in agents], reverse=True)
    by_restore: set[str] = set()
    for sha, pid in inverse.items():
        here = order.get(sha)
        if here is not None and any(order.get(s, here) < here for s in by_patch.get(pid, [])):
            by_restore.add(sha)
    return by_message, by_restore


def _scope_prefix(repo_root: Path, scope: Path | None) -> str:
    """The scope as a repo-relative POSIX prefix; empty for the whole repository."""
    if scope is None:
        return ""
    if scope.is_absolute():
        try:
            scope = scope.resolve().relative_to(repo_root.resolve())
        except ValueError:
            return ""
    rel = scope.as_posix().strip("/")
    return "" if rel in ("", ".") else rel


def _added_lines(repo_root: Path, sha: str, scope: Path | None = None) -> dict[str, int]:
    """Lines the commit added under ``scope``, per path it leaves; binary files left out.

    Renames are detected (``-M``), so a pure ``git mv`` adds no line and a rename with
    an edit adds only the edited lines, under the new path: ``git blame`` follows the
    rename back and credits the moved lines to their first commit."""
    # No pathspec: one would limit the diff before -M, so a file renamed into the scope
    # would read as a whole-file addition. The scope filters the new paths instead.
    out = _git(repo_root, "show", "--numstat", "-z", "-M", "--format=", sha)
    prefix = _scope_prefix(repo_root, scope)
    fields = out.lstrip("\n").split("\0")
    added: dict[str, int] = {}
    i = 0
    while i < len(fields):
        cols = fields[i].lstrip("\n").split("\t", 2)
        if len(cols) != 3:
            i += 1
            continue
        path = cols[2]
        i += 1
        if not path:  # a rename: the old path, then the new one, follow
            path = fields[i + 1] if i + 1 < len(fields) else ""
            i += 2
        if prefix and not (path == prefix or path.startswith(prefix + "/")):
            continue
        if path and cols[0].isdigit() and int(cols[0]) > 0:
            added[path] = int(cols[0])
    return added


def _surviving(repo_root: Path, sha: str, at: str, path: str) -> int | None:
    """Lines of ``path`` at ``at`` that ``git blame`` attributes to ``sha``; None when the
    path is gone at ``at``."""
    if not _git(repo_root, "ls-tree", "--name-only", at, "--", path).strip():
        return None
    # --no-ignore-revs-file: a blame.ignoreRevsFile in the user's config would make the
    # attribution depend on the machine, or fail outright where the file is missing.
    out = _git(repo_root, "blame", "--no-ignore-revs-file", "-l", "-s", "--root", at, "--", path)
    return sum(1 for line in out.splitlines() if line.startswith(sha))


def _reworked(repo_root: Path, commit: HistoryCommit, scope: Path | None = None) -> bool:
    """Whether a line the commit added is not its own in blame ``REWORK_DAYS`` later.

    The blame point is the newest descendant of the commit on HEAD's history within the
    span (``--ancestry-path``), so a mainline commit that lacks the change (a branch
    merged after the span) is never read; with no descendant in the span, nothing
    could have rewritten it."""
    horizon = commit.committed_at + REWORK_DAYS * DAY_SECONDS
    at = _git(repo_root, "rev-list", "-1", "--ancestry-path", f"--before=@{horizon}",
              f"{commit.sha}..HEAD").strip()
    if not at:
        return False
    for path, count in _added_lines(repo_root, commit.sha, scope).items():
        if not _git(repo_root, "log", "--format=%H", f"{commit.sha}..{at}", "--", path).strip():
            continue  # nothing touched the file in the span
        kept = _surviving(repo_root, commit.sha, at, path)
        if kept is not None and kept < count:
            return True
    return False


def _unavailable(reason: str) -> AgentOutcomeUnavailable:
    return {"available": False, "reason": reason}


def scan_agent_outcome(
    repo_root: Path, scope: Path | None, history_window: str | None,
    fleet_config: FleetConfig | None = None,
) -> AgentOutcomeBlock | AgentOutcomeUnavailable:
    """The revert and rework counts of the window's agent commits (informational)."""
    label = resolve_window(history_window)
    read = window_commits(repo_root, scope, history_window)
    if isinstance(read, str):
        return _unavailable(f"{REASON_CODES.get(read, 'unavailable')}: {read}")
    commits, since = read
    agents = agent_commits(commits, fleet_config)
    try:
        by_message, by_restore = _reverted(repo_root, agents, since) if agents else (set(), set())
        head_time = int(_git(repo_root, "log", "-1", "--format=%ct", "HEAD").strip() or 0)
        observed = [c for c in agents if c.committed_at + REWORK_DAYS * DAY_SECONDS <= head_time]
        sample = sorted(observed, key=lambda c: c.committed_at, reverse=True)[:REWORK_SAMPLE]
        reworked = sum(_reworked(repo_root, c, scope) for c in sample)
    except _GitFailed as e:
        return _unavailable(str(e))
    reverted = by_message | by_restore
    rework_null = NO_AGENT_COMMITS if not agents else NONE_OLD_ENOUGH if not sample else None
    return {
        "available": True,
        "window": label,
        "since": since,
        "informational": True,
        "agent_rule": AGENT_RULE,
        "agent_commits": len(agents),
        "reverted_commits": len(reverted),
        "reverted_by_message": len(by_message),
        "reverted_by_restore": len(by_restore),
        "revert_share": _share(len(reverted), len(agents)),
        "revert_null_reason": None if agents else NO_AGENT_COMMITS,
        "rework_days": REWORK_DAYS,
        "rework_observed_commits": len(observed),
        "rework_sampled_commits": len(sample),
        "reworked_commits": reworked,
        "rework_share": _share(reworked, len(sample)),
        "rework_null_reason": rework_null,
        "limits": list(LIMITS),
    }
