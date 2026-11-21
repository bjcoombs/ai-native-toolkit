"""AI-assisted change share: a lower bound on agent use, from trailers and authors.

The readiness layers say whether a repository is set up for agents, not whether
agents are used on it. This scan counts, over the run's history window, the
commits that carry a ``Co-authored-by`` trailer naming an AI assistant (the
AI-assistant subset of the shared matcher in ``lib.agent_identity``, which drops
non-Copilot ``[bot]`` accounts, so it need not agree with ``authorship_analysis``)
and, since #695, the commits whose *author* is an agent identity
(``agent_identity.AuthorMatcher``: pinned agent apps plus the ``[ai_usage]``
config rules). A commit with both counts once in the either share.

Block (``ai_usage`` in run-context.json)::

    {"available": True,
     "window": str,                # the history window applied, e.g. "12mo", "90d", "all-time"
     "since": str | None,          # the absolute git --since bound behind it ("@<epoch>",
                                   # counted back from HEAD's commit time); None = full history
     "merge_rule": "excluded",     # merge commits are not counted (git log --no-merges)
     "total_commits": int,         # non-merge commits reachable from HEAD in the window
     "agent_trailer_commits": int, # of those, commits with at least one agent co-author trailer
     "agent_trailer_share": float | None,  # agent_trailer_commits / total_commits; None when 0 commits
     "agent_authored_commits": int,   # commits whose author is an agent identity
     "agent_authored_share": float | None,
     "agent_either_commits": int,     # trailer or agent author; a commit with both counts once
     "agent_either_share": float | None,
     "dependency_bot_commits": int,   # Renovate / Dependabot authors: never counted as agent
     "token_user_commits": int,       # GitLab access-token users not promoted to agent
     "agent_identity_rule_commits": [int, ...],  # per [[ai_usage.agent_identities]] rule, by position
     "lower_bound": True,
     "covered_agents": [str, ...], # lib.agent_identity.COVERED_AGENTS (trailers)
     "covered_author_agents": [str, ...],  # lib.agent_identity.COVERED_AUTHOR_AGENTS
     "limits": [str, ...]}         # LIMITS: why the value is a floor

Authors (#695). Each commit's author (``%ae``, ``%an``) goes into one bucket
(the order is in ``lib.agent_identity``): a dependency bot, an agent (a config
rule first, crediting the first rule that matches, then a pinned default), a
GitLab project or group access-token user, or other. Token users are counted
apart in ``token_user_commits`` and become agents only through a config rule or
``token_users_as_agents = true``. ``agent_identity_rule_commits[i]`` is the
number of commits credited to config rule ``i``: the merged list's order, where
the ``--fleet-config`` rules keep their index in every repository. The block
never carries a pattern or a matched identity.

The value is a floor, never an estimate: a squash merge that drops the trailers,
a tool that does not write one, an agent outside the covered and configured
identities, and an agent committing under a person's identity with no trailer
all go uncounted. Merge commits are
excluded, the precedent ``change_coupling.authorship_analysis`` set, so a
merged branch counts its own commits once.

Window. ``history_window`` is in the ``<n>d|w|mo|y`` grammar (``"12mo"``):
``assess_core`` passes the churn window ``pick_churn_window`` chose for the
treemap, normalised by ``git_churn.window_from_churn_label``, so the share
covers the same history as the churn axis; the headless signals run passes
``--window``. None (all-time churn, no stats, no git) means full history,
recorded as ``"all-time"``. The window counts back from HEAD's committer time,
not from today (issue #773), so the same commit gives the same counts on any
date; ``since`` records the absolute ``@<epoch>`` bound.

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
from typing import Literal, NamedTuple, TypedDict

from lib.agent_identity import (
    AGENT,
    COVERED_AGENTS,
    COVERED_AUTHOR_AGENTS,
    DEPENDENCY_BOT,
    TOKEN_USER,
    AuthorMatcher,
    coauthors_have_ai_assistant,
    compile_config_rules,
)
from lib.commit_anchor import since_args
from lib.fleet_config import FleetConfig, ai_usage_section
from lib.history_window import split_window

GIT_TIMEOUT_SECONDS = 20

MERGE_RULE = "excluded"
ALL_TIME = "all-time"

LIMITS = (
    "Lower bound: squash merges that drop Co-authored-by trailers are undercounted.",
    "Lower bound: commits whose agent trailer was stripped or never written are undercounted.",
    "Lower bound: only the covered agents and the configured agent identities are recognised; other tools are not counted.",
    "Lower bound: an agent committing under a person's identity, with no trailer, is not counted.",
    "Automation bot co-authors (Dependabot, pre-commit-ci, github-actions) are not counted as AI assistance.",
    "Dependency bot authors (Renovate, Dependabot) are counted apart and never as agents.",
    "GitLab access-token users are counted apart and are agents only when a configured rule says so.",
    "Merge commits are excluded; each merged commit counts once.",
)

class AiUsageBlock(TypedDict):
    """The ``ai_usage`` run-context block when history was read."""

    available: Literal[True]
    window: str
    since: str | None
    merge_rule: str
    total_commits: int
    agent_trailer_commits: int
    agent_trailer_share: float | None
    agent_authored_commits: int
    agent_authored_share: float | None
    agent_either_commits: int
    agent_either_share: float | None
    dependency_bot_commits: int
    token_user_commits: int
    agent_identity_rule_commits: list[int]
    lower_bound: Literal[True]
    covered_agents: list[str]
    covered_author_agents: list[str]
    limits: list[str]


class AiUsageUnavailable(TypedDict):
    available: Literal[False]
    reason: str


def resolve_window(window: str | None) -> str:
    """The block's label for a ``<n>d|w|mo|y`` window, or ``"all-time"`` for None.

    Raises ``ValueError`` on anything else, so a malformed window never silently
    widens to full history.
    """
    if window is None:
        return ALL_TIME
    try:
        split_window(window)
    except ValueError:
        raise ValueError(f"unrecognised history window {window!r}") from None
    return window


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


class _Commit(NamedTuple):
    author_email: str
    author_name: str
    coauthors: str  # the Co-authored-by values joined by GS


def _commits(repo_root: Path, scope: Path | None, bound: list[str]) -> list[_Commit]:
    """Author and co-author fields of each non-merge commit in the window.

    Raises ``_GitUnavailable`` when git cannot answer, or when the clone is
    shallow (or cannot be shown not to be): a truncated history can push the
    share above the true value, so it is no floor.
    """
    args = ["log", "--no-merges",
            "--format=%x1e%ae%x1f%an%x1f%(trailers:key=Co-authored-by,valueonly,separator=%x1d)"]
    args += [*bound, "--", str(scope) if scope is not None else "."]
    out = _git(repo_root, *args)
    try:
        shallow = _git(repo_root, "rev-parse", "--is-shallow-repository").strip()
    except _GitUnavailable as e:
        raise _GitUnavailable(REASON_SHALLOW_UNKNOWN) from e
    if shallow != "false":
        raise _GitUnavailable(REASON_SHALLOW if shallow == "true" else REASON_SHALLOW_UNKNOWN)
    # Each record starts with RS; the first split element is the empty prefix.
    commits = []
    for record in out.split("\x1e")[1:]:
        email, _, rest = record.partition("\x1f")
        name, _, coauthors = rest.partition("\x1f")
        commits.append(_Commit(email, name, coauthors.strip("\n")))
    return commits


def _share(count: int, total: int) -> float | None:
    return round(count / total, 4) if total else None


def matcher_from_config(config: FleetConfig | None) -> AuthorMatcher:
    """The author matcher for one repository's merged ``[ai_usage]`` section."""
    if config is None:
        return AuthorMatcher()
    section = ai_usage_section(config)
    rules = compile_config_rules(section.get("agent_identities", []))
    return AuthorMatcher(rules, section.get("token_users_as_agents", False))


def scan_ai_usage(
    repo_root: Path, scope: Path | None, history_window: str | None,
    fleet_config: FleetConfig | None = None,
) -> AiUsageBlock | AiUsageUnavailable:
    """Count the window's commits, those with an AI-assistant co-author trailer and
    those an agent identity authored (``fleet_config``: the merged ``[ai_usage]`` rules)."""
    label = resolve_window(history_window)
    # Counted back from HEAD's commit time, never today (#773); no HEAD, no window.
    bound = since_args(repo_root, history_window, None)
    if bound is None:
        return {"available": False, "reason": REASON_NOT_A_REPO}
    since = bound[0].partition("=")[2] if bound else None
    matcher = matcher_from_config(fleet_config)
    try:
        commits = _commits(repo_root, scope, bound)
    except _GitUnavailable as e:
        return {"available": False, "reason": str(e)}
    total = len(commits)
    agent = authored = either = dependency = token = 0
    per_rule = [0] * len(matcher.config_rules)
    for c in commits:
        trailer = coauthors_have_ai_assistant(c.coauthors)
        cls = matcher.classify(c.author_email, c.author_name)
        is_author = cls.bucket == AGENT
        agent += trailer
        authored += is_author
        either += trailer or is_author
        dependency += cls.bucket == DEPENDENCY_BOT
        token += cls.bucket == TOKEN_USER
        if cls.rule_index is not None:
            per_rule[cls.rule_index] += 1
    return {
        "available": True,
        "window": label,
        "since": since,
        "merge_rule": MERGE_RULE,
        "total_commits": total,
        "agent_trailer_commits": agent,
        "agent_trailer_share": _share(agent, total),
        "agent_authored_commits": authored,
        "agent_authored_share": _share(authored, total),
        "agent_either_commits": either,
        "agent_either_share": _share(either, total),
        "dependency_bot_commits": dependency,
        "token_user_commits": token,
        "agent_identity_rule_commits": per_rule,
        "lower_bound": True,
        "covered_agents": list(COVERED_AGENTS),
        "covered_author_agents": list(COVERED_AUTHOR_AGENTS),
        "limits": list(LIMITS),
    }
