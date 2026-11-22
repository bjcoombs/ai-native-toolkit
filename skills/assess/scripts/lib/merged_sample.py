"""The shared sample of merged changes the platform blocks read (issue #715).

``review_reality`` and ``delivery`` describe the same changes: the registry runs
:func:`merged_sample` once, as the private ``merged_sample`` spec
(``network=True``), and both blocks read it, as does the private ``external_ci``
spec (``lib/external_ci.py``, #928) that ``ci_checks`` reads. Under ``assess_signals.py
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
  steps. ``gh pr list --search "merged:>=<since> sort:updated-desc" --json
  mergedAt`` lists up to ``FETCH_LIMIT`` merges; a change's ``updatedAt`` is never
  before its ``mergedAt``, so the newest merges are among the most recently
  updated. Their ``SAMPLE_CAP`` newest ``mergedAt`` values, newest first, are cut
  into pages of ``PAGE_SIZE``, and each page is one ``gh pr list --limit
  <PAGE_SIZE>`` that reads ``PR_FIELDS`` (the heavy fields: reviews, comments)
  for the changes merged in that page's span: ``merged:>=<oldest>`` for the
  newest page (today's read when the sample fits one page), ``merged:<oldest>..
  <newest>`` for the others (#995). A page runs on past ``PAGE_SIZE`` rather than
  split changes merged in the same second, so no change is in two spans; its
  ``--limit`` is the page size, or its length when it ran on. The pages are joined,
  ordered by ``mergedAt`` and cut to ``SAMPLE_CAP``.
- **GitLab.** ``projects/<id>/merge_requests?state=merged&updated_after=<since>
  &order_by=updated_at&sort=desc&per_page=<PAGE_SIZE>&page=<n>`` (``updated_after``
  and ``order_by=updated_at`` work on every GitLab version, unlike ordering by
  ``merged_at``), page after page until ``FETCH_LIMIT`` merge requests are listed
  or a page comes back short (#995), then the merges with ``merged_at`` inside
  the window, newest first, cut to ``SAMPLE_CAP``.

Paging (#995). One 100-change GraphQL page with nested reviews and comments
times out on a large repository (HTTP 502 on kubernetes/kubernetes), so the
heavy reads ask for ``PAGE_SIZE`` changes at a time. A page refused with HTTP
502 or 504, or with no answer inside the CLI timeout, is read again once at
``PAGE_SIZE // 2``, and every later page of the sample uses that size; a second
such refusal is the sample's reason. The sample's ``page_size`` is the size its
heavy read finished at (None when no heavy read was made: a GitHub window with
no merges), and ``delivery.sample_page_size`` carries it as a count. The sample
size and its order do not depend on the page size.

Call ceiling. The sample costs at most ``GITHUB_SAMPLE_CALLS`` ``gh pr list``
calls (the light listing, then ``ceil(SAMPLE_CAP / PAGE_SIZE)`` pages, or after
one retry ``1 + ceil(SAMPLE_CAP / (PAGE_SIZE // 2))``) and at most
``GITLAB_SAMPLE_CALLS`` ``glab api`` calls (``FETCH_LIMIT / PAGE_SIZE`` pages, or
after one retry ``1 + FETCH_LIMIT / (PAGE_SIZE // 2)``). Per-change reads made
from the sample do not depend on the page size, because the sample stays at most
``SAMPLE_CAP`` changes: ``lib/external_ci.py`` adds at most ``CALLS_PER_CHANGE``
REST reads per change (``CALLS_PER_CHANGE * SAMPLE_CAP``, 60, on GitHub), and GitLab
reads each merge request's approvals and notes.

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
a refusal is never an empty sample. A remote on a host neither read supports
(Bitbucket, Azure DevOps, or a host glab does not reach: GitHub Enterprise, an
unsigned GitLab) is ``unsupported_host``, never ``network``: no scan option reads it
as things stand. For a GitLab host the remedy is glab, so the reason sentence says so.

The value holds titles, logins and comment text in memory only. It is private:
no run-context block, counts-only document or fleet row carries it. Nor does
the ``--platform`` response cache (#778): the GitHub ``PR_FIELDS`` read (reviews
and comments with their bodies) and the GitLab merged list (titles and
descriptions) are read with ``cache=False``; only the light ``--json mergedAt``
listing, timestamps alone, may be cached. GitLab notes are never cached either
(``lib/gitlab_notes.py``, #716).

The shared sample of merged changes (#715), registered as the private
`network=True` spec `merged_sample` with reads `repo_root`, `history_window` and
`run_date`; `review_reality` and `delivery` read it, so one listing serves both
blocks and they describe the same changes; the private `external_ci` spec (#928) reads it
too. Both reads classify the remote with
`remote_kind` (#908), from the URL alone: `none` (no remote, a URL neither
`gh_cli.parse_github_remote` nor `gitlab_cli.parse_remote` reads, or a
`gitlab_cli.GITHUB_HOSTS` host whose path the GitHub parser rejects), `github`,
`unsupported` (a `UNSUPPORTED_HOSTS` host: Bitbucket, Azure DevOps, Codeberg, Gitea, sourcehut,
or a `*.visualstudio.com` host), or `platform` (any other host). Its `offline_fn`,
`offline_merged_sample` (a headless run without `--platform`), gives `NO_REMOTE_REASON`
(`no_remote`) for `none`, `UNSUPPORTED_HOST_REASON` (`unsupported_host`) for `unsupported`
and `NETWORK_REASON` otherwise. The full read gives `no_remote` for `none` too,
`unsupported_host` for `unsupported` without probing glab, and `unsupported_host` for a
`platform` host that glab does not reach (GitHub Enterprise, a GitLab glab is not signed in
to): no scan option reads it, so the tally names the host limit instead of `needs_platform`
and its advice never says to rerun with `--platform` (#959). A `platform` host reads
`network` offline because only the platform pass can settle it. Up to `SAMPLE_CAP` (30) changes merged
on or after `fleet_activity.window_start(history_window, run_date)`, newest
`merged_at` first; `history_window` None (interactive `/assess` with no churn
window) means no cutoff. GitHub: `gh pr list --repo <slug> --state merged
--search "merged:>=<since> sort:updated-desc" --limit FETCH_LIMIT --json mergedAt`
(an update is never before its merge, so the newest merges are among the most
recently updated), then the 30 newest `mergedAt` values cut into pages of `PAGE_SIZE`
(20), each one `gh pr list --limit <PAGE_SIZE> --json PR_FIELDS`
(`author,mergedBy,mergedAt,reviews,reviewDecision,comments,createdAt,headRefOid`;
`headRefOid` is the head commit `external_ci.py` reads, #928) over the page's span: `merged:>=<oldest>` for the newest page, `merged:<oldest>..<newest>` for the
others (#995). A page runs on past `PAGE_SIZE` rather than split changes merged in the
same second, so the spans are disjoint. GitLab:
`projects/<id>/merge_requests?state=merged&updated_after=<since>T00:00:00Z&order_by=updated_at&sort=desc&per_page=<PAGE_SIZE>&page=<n>`
page after page until `FETCH_LIMIT` (100) are listed or a page is short, then filtered
and ordered by `merged_at` client-side (works on GitLab versions without
`order_by=merged_at`). Paging (#995): one 100-change GraphQL page with nested reviews
and comments returned HTTP 502 on kubernetes/kubernetes. A heavy page refused with
HTTP 502 or 504 (`GhUnavailable.status`, set by `gh_cli` and `gitlab_cli` from the
CLI's stderr) or with no answer inside the CLI timeout is read again once at
`PAGE_SIZE // 2`, and the rest of the sample uses that size; a second refusal is the
reason. `MergedSample.page_size` is the size the heavy read finished at (None when no
heavy read was made), published as `delivery.sample_page_size`. Call ceiling:
`GITHUB_SAMPLE_CALLS` (5) `gh pr list` calls and `GITLAB_SAMPLE_CALLS` (11) `glab api`
calls, each counting one retry; the per-change reads made from the sample do not depend
on the page size, since the sample stays at most `SAMPLE_CAP`: `external_ci.py` adds at
most `CALLS_PER_CHANGE` (2) REST reads per change, `CALLS_PER_CHANGE * SAMPLE_CAP` (60),
and GitLab reads each merge request's approvals and notes. The `PR_FIELDS` read and the GitLab merged list are made
with `cache=False`: reviews, comments, titles and descriptions are read in memory only and
never reach the `--platform` response cache; only the light `--json mergedAt` read is
cached (#778). A change with no readable merge time is
`<tool>_bad_json`. The value is a `MergedSample` (provider, `changes` as raw provider
dicts, `since`, `github` or `gitlab`, and one `BotClassifier` every reader shares,
so an account is probed once); any refusal is `{"available": false, "reason"}`,
which each reader returns as its own block. `BotClassifier` (moved from
`review_reality`): an author object with `is_bot`/`type` is classified from it, a
`[bot]` login is a bot, any other login is probed once with `gh api
users/<login>[bot]` (at most `MAX_LOGIN_PROBES`, 20): type `Bot` is a bot, 404 a
person, anything else unknown. The value holds logins and text in memory only and
never reaches a block. `login` lower-cases and drops `gh`'s `app/` prefix on an app author, so the author compares equal to the same app as a commenter. Tests: `tests/test_delivery.py`,
`tests/test_platform_argv_contract.py`, `tests/test_merged_sample_paging.py` (gh and glab
stubbed at `run_cli`: a 502 above 25 per page, a 502 or 504 at the page size, the ceilings).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from lib.fleet_activity import window_start
from lib.gh_cli import (
    NETWORK_REASON, GhUnavailable, GithubRepo, gh_api, gh_json, git_remote_url, open_github,
    parse_github_remote, unavailable,
)
from lib.gitlab_cli import GITHUB_HOSTS, GitlabProject, glab_api, open_gitlab, parse_remote

# Changes in the sample: the most recently merged in the window.
SAMPLE_CAP = 30

# Changes listed to find the newest merges.
FETCH_LIMIT = 100

# Changes per heavy read (the GitHub PR_FIELDS pages, the GitLab merged-list
# pages); a page refused with HTTP 502 or 504 is read again once at half this
# size (#995). Even, and FETCH_LIMIT a multiple of it, so GitLab's page offsets
# line up at either size.
PAGE_SIZE = 20

# HTTP statuses a too-heavy page gets (a gateway timeout); read again smaller.
RETRY_STATUSES = frozenset({"502", "504"})

# The most calls the sample's reads make (see the module docstring).
GITHUB_SAMPLE_CALLS = 1 + 1 + -(-SAMPLE_CAP // (PAGE_SIZE // 2))
GITLAB_SAMPLE_CALLS = 1 + FETCH_LIMIT // (PAGE_SIZE // 2)

# Distinct unmarked comment logins probed for a bot account per sample.
MAX_LOGIN_PROBES = 20

# Status-comment bot; its comments and reviews are never review.
STATUS_BOTS = frozenset({"github-actions", "github-actions[bot]"})

# The pull request fields the sample reads. ``reviewDecision`` is fetched but not
# scored: it reflects the current rule, not whether anyone other than the author
# reviewed. ``createdAt`` dates time to merge and time to first human comment.
# ``headRefOid`` is the head commit whose check runs ``lib/external_ci.py`` reads (#928).
PR_FIELDS = "author,mergedBy,mergedAt,reviews,reviewDecision,comments,createdAt,headRefOid"

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
    """The sample (see the module docstring). ``changes`` are newest merge first;
    ``page_size`` is the size the heavy read finished at, None when none was made."""

    provider: Provider
    changes: tuple[dict[str, Any], ...]
    since: date | None
    github: GithubRepo | None = None
    gitlab: GitlabProject | None = None
    bots: BotClassifier = field(default_factory=BotClassifier, compare=False)
    page_size: int | None = None


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


def _too_heavy(e: GhUnavailable) -> bool:
    """A refusal that a smaller page may get past: HTTP 502 or 504, or the CLI timeout."""
    return e.status in RETRY_STATUSES or e.reason.startswith(("gh_timeout", "glab_timeout"))


def _gh_merged(repo: GithubRepo, search: str, limit: int, fields: str,
               cache: bool) -> list[dict[str, Any]]:
    """One ``gh pr list`` read. Each caller states ``cache``: only the light ``mergedAt``
    read may be cached; the ``PR_FIELDS`` read carries review and comment bodies, so it
    passes ``cache=False`` and is read in memory only."""
    got = gh_json(["pr", "list", "--repo", repo.slug, "--state", "merged", "--search",
                   f"{search} sort:updated-desc".strip(), "--limit", str(limit),
                   "--json", fields], cache=cache)
    if not isinstance(got, list):
        raise GhUnavailable("gh_bad_json: `gh pr list` did not return a list")
    return [pr for pr in got if isinstance(pr, dict)]


def _span(page: list[dict[str, Any]], newest_page: bool) -> str:
    """The ``merged:`` qualifier for one page of the newest merges (newest first)."""
    oldest, newest = page[-1]["mergedAt"], page[0]["mergedAt"]
    return f"merged:>={oldest}" if newest_page else f"merged:{oldest}..{newest}"


def _github_pages(repo: GithubRepo, light: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """The ``PR_FIELDS`` read of ``light`` (newest merge first), page by page, and the
    page size it finished at; one retry at half the size (see the module docstring)."""
    size, read = PAGE_SIZE, 0
    got: list[dict[str, Any]] = []
    while read < len(light):
        page = light[read:_page_end(light, read, size)]
        try:
            got += _gh_merged(repo, _span(page, read == 0), max(size, len(page)), PR_FIELDS,
                              cache=False)
        except GhUnavailable as e:
            if size < PAGE_SIZE or not _too_heavy(e):
                raise
            size = PAGE_SIZE // 2
            continue
        read += len(page)
    return got, size


def _page_end(light: list[dict[str, Any]], start: int, size: int) -> int:
    """Where the page from ``start`` ends: ``size`` changes on, moved on past any change
    merged in the same second as the page's last, so no merge time straddles two pages
    and the spans stay disjoint. Never more pages than ``ceil(len(light) / size)``."""
    end = min(start + size, len(light))
    while end < len(light) and light[end - 1]["mergedAt"] == light[end]["mergedAt"]:
        end += 1
    return end


def github_changes(repo: GithubRepo, since: date | None) -> tuple[list[dict[str, Any]], int | None]:
    """The GitHub sample's pull requests and the page size read at (see the module docstring)."""
    floor = f"merged:>={since.isoformat()}" if since else ""
    light = _newest(_gh_merged(repo, floor, FETCH_LIMIT, "mergedAt", cache=True),
                    "mergedAt", since, "gh")
    if not light:
        return [], None
    prs, size = _github_pages(repo, light)
    return _newest(prs, "mergedAt", since, "gh"), size


def gitlab_changes(project: GitlabProject, since: date | None) -> tuple[list[dict[str, Any]], int]:
    """The GitLab sample's merge requests and the page size read at (see the module docstring)."""
    after = f"&updated_after={since.isoformat()}T00:00:00Z" if since else ""
    size = PAGE_SIZE
    got: list[Any] = []
    while len(got) < FETCH_LIMIT:
        try:
            page = glab_api(project, f"projects/{project.encoded}/merge_requests?state=merged{after}"
                                     f"&order_by=updated_at&sort=desc&per_page={size}"
                                     f"&page={len(got) // size + 1}",
                            cache=False)  # titles and descriptions: read in memory only
        except GhUnavailable as e:
            if size < PAGE_SIZE or not _too_heavy(e):
                raise
            size = PAGE_SIZE // 2
            continue
        if not isinstance(page, list):
            raise GhUnavailable("glab_bad_json: the merged merge request list was not a list")
        got += page
        if len(page) < size:
            break
    return _newest([mr for mr in got if isinstance(mr, dict)], "merged_at", since, "glab"), size


def merged_sample(repo_root: Path, history_window: str | None, run_date: str) -> SampleOrReason:
    """The ``merged_sample`` registry value (see the module docstring)."""
    since = sample_since(history_window, run_date)
    try:
        kind = remote_kind(git_remote_url(repo_root))
        if kind == "unsupported":  # no read supports the host: never probe glab for it
            return unavailable(UNSUPPORTED_HOST_REASON)
        project = open_gitlab(repo_root)
        if project is not None:
            mrs, size = gitlab_changes(project, since)
            return MergedSample("gitlab", tuple(mrs), since, gitlab=project, page_size=size)
        if kind == "platform":
            # A host that is neither GitHub nor a GitLab glab reaches (GitHub
            # Enterprise, a GitLab glab is not signed in to): the platform pass
            # cannot read it either, so the reason names the host limit (#959).
            return unavailable(UNSUPPORTED_HOST_REASON)
        repo = open_github(repo_root)
        prs, read_at = github_changes(repo, since)
        return MergedSample("github", tuple(prs), since, github=repo, page_size=read_at)
    except GhUnavailable as e:
        return unavailable(e.reason)


NO_REMOTE_REASON = "no_remote: no GitHub or GitLab remote to read merged changes from"
UNSUPPORTED_HOST_REASON = ("unsupported_host: the remote's host is not GitHub or a GitLab host glab "
                           "reaches; merged changes are read from GitHub and GitLab only (if the "
                           "host is a GitLab, install glab and sign it in to the host)")

# Hosts no merged-changes read supports, known from the URL alone (#959). Any other
# non-GitHub host may be a self-managed GitLab, which only a glab probe settles.
UNSUPPORTED_HOSTS = frozenset({
    "bitbucket.org", "dev.azure.com", "ssh.dev.azure.com", "codeberg.org", "gitea.com",
    "git.sr.ht",
})
UNSUPPORTED_HOST_SUFFIXES = (".visualstudio.com",)

RemoteKind = Literal["none", "github", "platform", "unsupported"]


def remote_kind(url: str | None) -> RemoteKind:
    """What a remote URL is, read from the URL alone (no ``gh`` or ``glab``, #908).

    ``github``: a URL ``parse_github_remote`` reads. ``none``: no URL, one neither
    parser reads, or a GitHub host whose path ``parse_github_remote`` rejects;
    :func:`merged_sample` ends at ``no_remote`` for each. ``unsupported``: a
    host in ``UNSUPPORTED_HOSTS`` (Bitbucket, Azure DevOps, ...), which no read
    supports. ``platform``: any other host, a GitLab candidate only a ``glab``
    probe can settle.
    """
    if url is None:
        return "none"
    if parse_github_remote(url) is not None:
        return "github"
    parsed = parse_remote(url)
    if parsed is None or parsed[0] in GITHUB_HOSTS:
        return "none"
    host = parsed[0].split(":", 1)[0]
    if host in UNSUPPORTED_HOSTS or host.endswith(UNSUPPORTED_HOST_SUFFIXES):
        return "unsupported"
    return "platform"


def offline_merged_sample(repo_root: Path, history_window: str | None, run_date: str) -> SampleOrReason:
    """The ``merged_sample`` value of a headless run without ``--platform`` (#908).

    Pure git, no ``gh`` or ``glab``. A :func:`remote_kind` of ``none`` gets
    ``no_remote``, the answer :func:`merged_sample` reaches (``open_gitlab``
    returns None, ``open_github`` raises ``no_remote``). An ``unsupported`` host
    gets ``unsupported_host``, as the full read does, so the advice never says to
    rerun with ``--platform`` for a host it cannot read (#959). Any other remote
    needs the platform to settle: ``network``.
    """
    del history_window, run_date  # the offline value reads the remote only
    kind = remote_kind(git_remote_url(repo_root))
    if kind == "none":
        return unavailable(NO_REMOTE_REASON)
    if kind == "unsupported":
        return unavailable(UNSUPPORTED_HOST_REASON)
    return unavailable(NETWORK_REASON)
