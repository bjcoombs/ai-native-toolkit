"""Issue-tracker outcomes for the backlog-ready practice, level 2, read from the platform (#1323).

Configuration shows intent; the tracker shows whether issues are answered, triaged and closed.
This module reads the issues of the run's repository from GitHub (``gh api graphql``) or GitLab
(``glab api``, ``lib.hosting.issue_outcomes_gitlab``) and keeps, per issue, only times and label
names: when it opened, when it closed, when a person first responded, and its labels. No title,
body, number, URL or login leaves this module; ``lib.backlog_ready_outcomes`` turns the read into
counts and the three metrics.

**The reads.** All in a window of ``window_days`` (``[backlog_ready]``, default 90) ending at the
run time (``NOW``):

- **Issues touched in the window**: every issue updated since the window opened, so each issue
  opened or closed in it is read. On GitHub one GraphQL query per ``GITHUB_PAGE`` issues carries
  each issue's first ``TIMELINE_ITEMS`` comments, label events and close events; on GitLab the
  issues list, then each window issue's notes (one page) and its ``closed_by``.
- **Open issues older than 7 days**: their labels only.

Each list stops at ``MAX_PAGES`` pages; a list still going then is ``truncated``. The counts
read are kept and the read is still measured.

**First response.** The first comment, label event or close event by an account that is neither
the issue's author nor a bot. GitHub: an actor whose GraphQL ``__typename`` is ``Bot`` or whose
login ends ``[bot]`` is a bot. GitLab (``issue_outcomes_gitlab``): system notes are skipped (so a
label added there is not read), ``bot: true`` on the user, a project or group access-token user,
or a user whose ``users/<id>`` read says ``bot: true`` is a bot. An issue whose events ran past
what was read, or whose responder could not be classified, has an unknown response
(``response_read`` False) and counts in neither ``responded`` nor ``no_response_count``. An issue
closed by its own author is ``self_closed`` (GitHub: a read ``ClosedEvent`` whose actor is the
author is the last close read, the timeline was read whole and the issue is still closed; GitLab:
``closed_by`` is the author), so nobody owes it a response (#1587).

**Not measured.** The value is an :class:`IssueRead`, or ``{"available": False, "reason": code}``
with one of ``REASONS``: ``no_network`` (a headless run without ``--platform``, or an unreachable
host), ``no_platform_auth`` (``gh``/``glab`` missing, signed out, or a read refused with 401, 403
or 404), ``unsupported_host`` (no remote, or one that is not GitHub or a GitLab ``glab`` reaches)
and ``platform_error`` (any other failed read). It is a private registry value: the block it
feeds carries the code, never the CLI's text.

The private network spec ``backlog_outcomes`` (``checks/core/backlog_outcomes.toml``):
`backlog_outcomes(repo_root)` reads the issues of the repository's GitHub or GitLab remote, as
above, into an `IssueRead` (`provider`, `start`, `now`, `recent`, `open_old`, `truncated`; each
`Issue` holds `created`, `closed`, `first_response`, `response_read` and lower-cased `labels`).
The shapes (`Issue`, `IssueRead`, `REASONS`) live in `lib/backlog_issue_read.py`, which imports
no platform client. `offline_backlog_outcomes(repo_root)` is the headless value without `--platform`: `unsupported_host`
for no remote or an unsupported host, else `no_network`. `reason_code(reason)` maps a
`GhUnavailable` reason to one of `REASONS`. `NOW` is replaceable in tests. Reads go through
`gh_cli.gh_json` and `gitlab_cli.glab_api`; GitLab notes are read with `cache=False`. Tests:
`tests/test_backlog_ready_platform.py`.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from lib.assess_config import load_backlog_ready_thresholds
from lib.backlog_issue_read import (NO_NETWORK, NO_PLATFORM_AUTH, PLATFORM_ERROR, REASONS, UNSUPPORTED_HOST,
                                    UNTRIAGED_AFTER, Issue, IssueRead, OutcomeRead)
from lib.hosting.gh_cli import GhUnavailable, GithubRepo, gh_json, git_remote_url, open_github
from lib.hosting.gitlab_cli import open_gitlab
from lib.hosting.merged_sample import parse_time, remote_kind

# Issues per GitHub GraphQL page, timeline items read per issue, label names per issue.
GITHUB_PAGE = 50
TIMELINE_ITEMS = 25
LABELS_PER_ISSUE = 100
# Pages read per list before the read is truncated (GitHub and GitLab).
MAX_PAGES = 10


__all__ = ["NO_NETWORK", "NO_PLATFORM_AUTH", "PLATFORM_ERROR", "REASONS", "UNSUPPORTED_HOST",
           "UNTRIAGED_AFTER", "Issue", "IssueRead", "OutcomeRead", "backlog_outcomes",
           "offline_backlog_outcomes", "reason_code", "gh_is_bot", "github_read", "unread"]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


NOW: Callable[[], datetime] = _utc_now

def unread(code: str) -> dict[str, Any]:
    return {"available": False, "reason": code}


def reason_code(reason: str) -> str:
    """One of ``REASONS`` for a ``GhUnavailable`` reason (its text never leaves the run)."""
    head = reason.split(":", 1)[0]
    if head in ("gh_not_installed", "glab_not_installed", "not_authenticated", "no_access", "not_found"):
        return NO_PLATFORM_AUTH
    if head in ("unreachable", "gh_timeout", "glab_timeout"):
        return NO_NETWORK
    if head in ("no_remote", UNSUPPORTED_HOST):
        return UNSUPPORTED_HOST
    return PLATFORM_ERROR


# --- GitHub ------------------------------------------------------------------------

_ACTOR = "{ __typename login }"
RECENT_QUERY = (
    "query($owner: String!, $name: String!, $since: DateTime!, $after: String) {"
    " repository(owner: $owner, name: $name) {"
    f" issues(first: {GITHUB_PAGE}, after: $after, filterBy: {{since: $since}},"
    " orderBy: {field: CREATED_AT, direction: DESC}) {"
    " pageInfo { hasNextPage endCursor }"
    f" nodes {{ createdAt closedAt author {_ACTOR}"
    f" timelineItems(first: {TIMELINE_ITEMS}, itemTypes: [ISSUE_COMMENT, LABELED_EVENT, CLOSED_EVENT]) {{"
    " pageInfo { hasNextPage }"
    f" nodes {{ __typename ... on IssueComment {{ createdAt author {_ACTOR} }}"
    f" ... on LabeledEvent {{ createdAt actor {_ACTOR} }}"
    f" ... on ClosedEvent {{ createdAt actor {_ACTOR} }} }} }} }} }} }} }}"
)
OPEN_QUERY = (
    "query($owner: String!, $name: String!, $after: String) {"
    " repository(owner: $owner, name: $name) {"
    " issues(first: 100, after: $after, states: OPEN, orderBy: {field: CREATED_AT, direction: ASC}) {"
    " pageInfo { hasNextPage endCursor }"
    f" nodes {{ createdAt labels(first: {LABELS_PER_ISSUE}) {{ totalCount nodes {{ name }} }} }} }} }} }}"
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _gh_login(actor: Any) -> str | None:
    login = actor.get("login") if isinstance(actor, dict) else None
    return login.lower() if isinstance(login, str) and login else None


def gh_is_bot(actor: Any) -> bool:
    """A GitHub App account: GraphQL type ``Bot``, or a login ending ``[bot]``."""
    login = _gh_login(actor)
    return bool(isinstance(actor, dict) and actor.get("__typename") == "Bot"
                or (login and login.endswith("[bot]")))


def _gh_response(node: dict[str, Any]) -> tuple[datetime | None, bool, bool]:
    """The first response to one issue node, whether its timeline read settles it, and whether the
    last close read was by the issue's author (a later close by someone else decides)."""
    author = _gh_login(node.get("author"))
    timeline = _dict(node.get("timelineItems"))
    events: list[datetime] = []
    closes: list[tuple[datetime, bool]] = []
    for item in timeline.get("nodes") or []:
        if not isinstance(item, dict):
            continue
        actor = item.get("author") if item.get("__typename") == "IssueComment" else item.get("actor")
        login = _gh_login(actor)
        when = parse_time(item.get("createdAt"))
        if item.get("__typename") == "ClosedEvent" and when is not None:
            closes.append((when, login is not None and login == author))
        if login is None or when is None or login == author or gh_is_bot(actor):
            continue
        events.append(when)
    more = _dict(timeline.get("pageInfo")).get("hasNextPage") is True
    # an unread page may hold a later close; a reopened issue (no closedAt) is not closed at all
    self_closed = bool(closes) and max(closes)[1] and not more and parse_time(node.get("closedAt")) is not None
    if events:
        return min(events), True, self_closed
    return None, not more, self_closed


def _labels(node: dict[str, Any]) -> tuple[str, ...]:
    labels = _dict(node.get("labels"))
    return tuple(str(n["name"]).lower() for n in labels.get("nodes") or []
                 if isinstance(n, dict) and isinstance(n.get("name"), str))


def _gh_pages(repo: GithubRepo, query: str, extra: list[str]) -> tuple[list[dict[str, Any]], bool]:
    """Every node of a paged ``repository.issues`` query, at most ``MAX_PAGES`` pages; the flag is
    true when the list went on past them."""
    nodes: list[dict[str, Any]] = []
    after: str | None = None
    for _ in range(MAX_PAGES):
        args = ["api", "graphql", "-f", f"query={query}", "-f", f"owner={repo.owner}",
                "-f", f"name={repo.name}", *extra, *(["-f", f"after={after}"] if after else [])]
        data = gh_json(args)
        repository = data.get("data", {}).get("repository") if isinstance(data, dict) else None
        if not isinstance(repository, dict):
            raise GhUnavailable("not_found: the repository's issues could not be read")
        issues = _dict(repository.get("issues"))
        nodes += [n for n in issues.get("nodes") or [] if isinstance(n, dict)]
        page = _dict(issues.get("pageInfo"))
        after = page.get("endCursor") if page.get("hasNextPage") is True else None
        if not after:
            return nodes, False
    return nodes, True


def github_read(repo: GithubRepo, start: datetime, now: datetime) -> IssueRead:
    """The issues of a GitHub repository (see the module doc)."""
    since = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    recent_nodes, cut_recent = _gh_pages(repo, RECENT_QUERY, ["-f", f"since={since}"])
    recent = []
    for node in recent_nodes:
        created = parse_time(node.get("createdAt"))
        if created is None:
            raise GhUnavailable("gh_bad_json: an issue has no createdAt")
        first, settled, self_closed = _gh_response(node)
        recent.append(Issue(created, parse_time(node.get("closedAt")), (), first, settled,
                            self_closed=self_closed))
    open_nodes, cut_open = _gh_pages(repo, OPEN_QUERY, [])
    cutoff = now - UNTRIAGED_AFTER
    open_old = []
    for node in open_nodes:
        created = parse_time(node.get("createdAt"))
        if created is not None and created < cutoff:
            names = _labels(node)
            total = _dict(node.get("labels")).get("totalCount")
            complete = not isinstance(total, int) or total <= len(names)
            open_old.append(Issue(created, None, names, labels_complete=complete))
    return IssueRead("github", start, now, tuple(recent), tuple(open_old), cut_recent or cut_open, cut_recent,
                     cut_open)


# --- the registry values -------------------------------------------------------------

def backlog_outcomes(repo_root: Path) -> OutcomeRead:
    """The ``backlog_outcomes`` registry value (see the module doc)."""
    from lib.hosting.issue_outcomes_gitlab import gitlab_read  # the GitLab half imports this module

    thresholds, _ = load_backlog_ready_thresholds(repo_root)
    now = NOW()
    start = now - timedelta(days=thresholds.window_days)
    try:
        kind = remote_kind(git_remote_url(repo_root))
        if kind in ("none", "unsupported"):
            return unread(UNSUPPORTED_HOST)
        project = open_gitlab(repo_root)
        if project is not None:
            return gitlab_read(project, start, now)
        if kind == "platform":  # neither GitHub nor a GitLab glab reaches
            return unread(UNSUPPORTED_HOST)
        return github_read(open_github(repo_root), start, now)
    except GhUnavailable as e:
        return unread(reason_code(e.reason))


def offline_backlog_outcomes(repo_root: Path) -> OutcomeRead:
    """The value of a headless run without ``--platform``: the remote is read, the tracker is not."""
    kind = remote_kind(git_remote_url(repo_root))
    return unread(UNSUPPORTED_HOST if kind in ("none", "unsupported") else NO_NETWORK)
