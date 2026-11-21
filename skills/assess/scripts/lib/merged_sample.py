"""The shared sample of merged changes the platform blocks read (issue #715).

``review_reality`` and ``delivery`` describe the same changes: the registry runs
:func:`merged_sample` once, as the private ``merged_sample`` spec
(``network=True``), and both blocks read it. Under ``assess_signals.py
--platform`` it runs because a scan in ``PLATFORM_SCANS`` reads it; without
``--platform`` it is never called. Interactive ``/assess`` runs it like any
other registry entry.

The sample. Up to ``SAMPLE_CAP`` changes merged inside the run window, the most
recently merged first (by ``merged_at``). The window is the registry input
``history_window`` (``--window`` in a headless run, ``6mo`` by default) counted
back from ``run_date`` by ``fleet_activity.window_start``, so a rerun on the
same day draws the same window. ``history_window`` None (an interactive run
with no churn window) means no cutoff: the latest ``SAMPLE_CAP`` merges.

- **GitHub.** No listing orders by merge time, so the sample is read in two
  calls. ``gh pr list --search "merged:>=<since> sort:updated-desc" --json
  mergedAt`` lists up to ``FETCH_LIMIT`` merges; a change's ``updatedAt`` is never
  before its ``mergedAt``, so the newest merges are among the most recently
  updated. Their ``SAMPLE_CAP``-th newest ``mergedAt`` is the floor of a second
  ``gh pr list --search "merged:>=<floor> ..."`` that reads ``PR_FIELDS`` (the
  heavy fields: reviews, comments) for those changes only. The result is
  ordered by ``mergedAt`` and cut to ``SAMPLE_CAP``.
- **GitLab.** One read: ``projects/<id>/merge_requests?state=merged
  &updated_after=<since>&order_by=updated_at&sort=desc&per_page=FETCH_LIMIT``
  (``updated_after`` and ``order_by=updated_at`` work on every GitLab version,
  unlike ordering by ``merged_at``), then the merges with ``merged_at`` inside the
  window, newest first, cut to ``SAMPLE_CAP``.

A change merged in the window but last updated before the ``FETCH_LIMIT``
most recently updated ones is missed; that needs more than ``FETCH_LIMIT``
changes updated after it.

Value. :class:`MergedSample` on success: the provider, the repository (``github``
or ``gitlab``), the raw change objects (each provider's own field names), the
window's first day, and one :class:`BotClassifier` that every reader uses, so
an account probed for ``review_reality`` is not probed again for ``delivery``
and the two blocks never disagree about who is a bot. A remote that is not
GitHub or GitLab, a missing or logged-out CLI, or any refused or malformed
listing returns ``{"available": False, "reason"}`` (``no_access`` on 403,
``not_found`` on 404), which each reader passes through as its own block;
a refusal is never an empty sample.

The value holds titles, logins and comment text in memory only. It is private:
no run-context block, counts-only document or fleet row carries it. Nor does
the ``--platform`` response cache (#778): the GitHub ``PR_FIELDS`` read (reviews
and comments with their bodies) and the GitLab merged list (titles and
descriptions) are read with ``cache=False``; only the light ``--json mergedAt``
listing, timestamps alone, may be cached. GitLab notes are never cached either
(``lib/gitlab_notes.py``, #716).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from lib.fleet_activity import window_start
from lib.gh_cli import GhUnavailable, GithubRepo, gh_api, gh_json, open_github, unavailable
from lib.gitlab_cli import GitlabProject, glab_api, open_gitlab

# Changes in the sample: the most recently merged in the window.
SAMPLE_CAP = 30

# Changes listed to find the newest merges (one page on GitLab).
FETCH_LIMIT = 100

# Distinct unmarked comment logins probed for a bot account per sample.
MAX_LOGIN_PROBES = 20

# Status-comment bot; its comments and reviews are never review.
STATUS_BOTS = frozenset({"github-actions", "github-actions[bot]"})

# The pull request fields the sample reads. ``reviewDecision`` is fetched but not
# scored: it reflects the current rule, not whether anyone other than the author
# reviewed. ``createdAt`` dates time to merge and time to first human comment.
PR_FIELDS = "author,mergedBy,mergedAt,reviews,reviewDecision,comments,createdAt"

Provider = Literal["github", "gitlab"]


def login(actor: Any) -> str | None:
    """A GitHub actor's ``login``, lower-cased; None when absent.

    ``gh`` names a GitHub App that authored a pull request ``app/<name>`` and the
    same account as a commenter or reviewer ``<name>``; the prefix is dropped so
    the two compare equal."""
    if isinstance(actor, dict):
        value = actor.get("login")
        if isinstance(value, str) and value:
            return value.lower().removeprefix("app/") or None
    return None


class BotClassifier:
    """Decides whether a GitHub comment or review author is a bot; one probe per login.

    ``gh pr list`` gives a comment or review author a ``login`` only, with a bot's
    ``[bot]`` suffix dropped. An author object that carries ``is_bot`` or ``type``
    is classified from it; a login ending ``[bot]`` is a bot; any other login is
    probed once with ``gh api users/<login>[bot]`` (a GitHub App's bot account;
    ``[`` cannot appear in a person's login): type ``Bot`` is a bot, a 404 is a
    person, anything else is unknown (None), as is an author with no login. At
    most ``MAX_LOGIN_PROBES`` distinct logins are probed.
    """

    def __init__(self) -> None:
        self._known: dict[str, bool | None] = {}
        self._probes = 0

    def is_bot(self, actor: Any) -> bool | None:
        name = login(actor)
        if name is None:
            return None
        flag = actor.get("is_bot")
        if isinstance(flag, bool):
            return flag
        kind = actor.get("type")
        if isinstance(kind, str):
            return kind == "Bot"
        if name.endswith("[bot]"):
            return True
        if name not in self._known:
            self._known[name] = self._probe(name)
        return self._known[name]

    def _probe(self, name: str) -> bool | None:
        if self._probes >= MAX_LOGIN_PROBES:
            return None
        self._probes += 1
        try:
            user = gh_api(f"users/{quote(name + '[bot]', safe='')}")
        except GhUnavailable as e:
            return False if e.reason.startswith("not_found") else None
        return isinstance(user, dict) and user.get("type") == "Bot"


@dataclass(frozen=True)
class MergedSample:
    """The sample (see the module docstring). ``changes`` are newest merge first."""

    provider: Provider
    changes: tuple[dict[str, Any], ...]
    since: date | None
    github: GithubRepo | None = None
    gitlab: GitlabProject | None = None
    bots: BotClassifier = field(default_factory=BotClassifier, compare=False)


# What the registry passes a reader: the sample, or the unavailable block a
# reader returns as its own (also the registry's degrade marker on a raise).
SampleOrReason = MergedSample | dict[str, Any]


def parse_time(stamp: Any) -> datetime | None:
    """An ISO 8601 timestamp (``Z`` or an offset) as an aware UTC datetime; None otherwise."""
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc)


def sample_since(history_window: str | None, run_date: str) -> date | None:
    """The window's first day, or None for no cutoff (no window)."""
    if not history_window:
        return None
    return window_start(history_window, date.fromisoformat(run_date))


def _newest(changes: list[dict[str, Any]], key: str, since: date | None,
            tool: str) -> list[dict[str, Any]]:
    """Changes merged on or after ``since``, newest ``key`` first, at most ``SAMPLE_CAP``.

    A merged change with no readable merge time is a malformed listing
    (``<tool>_bad_json``), never silently left out."""
    kept: list[tuple[datetime, dict[str, Any]]] = []
    for change in changes:
        when = parse_time(change.get(key))
        if when is None:
            raise GhUnavailable(f"{tool}_bad_json: a merged change has no {key}")
        if since is None or when.date() >= since:
            kept.append((when, change))
    kept.sort(key=lambda pair: pair[0], reverse=True)
    return [c for _, c in kept[:SAMPLE_CAP]]


def _gh_merged(repo: GithubRepo, floor: str, fields: str) -> list[dict[str, Any]]:
    """One ``gh pr list`` read. Only the light ``mergedAt`` read may be cached: the
    ``PR_FIELDS`` read carries review and comment bodies, so it is read in memory only."""
    search = f"merged:>={floor} sort:updated-desc" if floor else "sort:updated-desc"
    got = gh_json(["pr", "list", "--repo", repo.slug, "--state", "merged", "--search", search,
                   "--limit", str(FETCH_LIMIT), "--json", fields], cache=fields == "mergedAt")
    if not isinstance(got, list):
        raise GhUnavailable("gh_bad_json: `gh pr list` did not return a list")
    return [pr for pr in got if isinstance(pr, dict)]


def github_changes(repo: GithubRepo, since: date | None) -> list[dict[str, Any]]:
    """The GitHub sample's pull requests (two reads; see the module docstring)."""
    light = _newest(_gh_merged(repo, since.isoformat() if since else "", "mergedAt"),
                    "mergedAt", since, "gh")
    if not light:
        return []
    floor = str(light[-1]["mergedAt"])
    return _newest(_gh_merged(repo, floor, PR_FIELDS), "mergedAt", since, "gh")


def gitlab_changes(project: GitlabProject, since: date | None) -> list[dict[str, Any]]:
    """The GitLab sample's merge requests (one read; see the module docstring)."""
    after = f"&updated_after={since.isoformat()}T00:00:00Z" if since else ""
    got = glab_api(project, f"projects/{project.encoded}/merge_requests?state=merged{after}"
                            f"&order_by=updated_at&sort=desc&per_page={FETCH_LIMIT}",
                   cache=False)  # titles and descriptions: read in memory only
    if not isinstance(got, list):
        raise GhUnavailable("glab_bad_json: the merged merge request list was not a list")
    return _newest([mr for mr in got if isinstance(mr, dict)], "merged_at", since, "glab")


def merged_sample(repo_root: Path, history_window: str | None, run_date: str) -> SampleOrReason:
    """The ``merged_sample`` registry value (see the module docstring)."""
    since = sample_since(history_window, run_date)
    try:
        project = open_gitlab(repo_root)
        if project is not None:
            return MergedSample("gitlab", tuple(gitlab_changes(project, since)), since,
                                gitlab=project)
        repo = open_github(repo_root)
        return MergedSample("github", tuple(github_changes(repo, since)), since, github=repo)
    except GhUnavailable as e:
        return unavailable(e.reason)
