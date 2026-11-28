"""Every change merged in an absolute period, read in full or marked incomplete (#1668).

The shared ``merged_sample`` (``lib/hosting/merged_sample.py``) is a sample: at most 30
changes over a relative window. An auditor who samples changes needs the whole population
of an absolute period, and a population with a gap is worse than none, because it reads as
complete. This module reads that population for ``--period YYYY-MM-DD..YYYY-MM-DD``
(``assess_signals.py`` and ``assess_core.py``; both days inclusive, UTC) and fails closed:
any cap, ceiling or refused read marks the census incomplete, and every count read from it
is null. The sample is unchanged and never reads the census.

Two registry entries. ``merged_census`` is the private ``network=True`` collector; it reads
``repo_root`` and the provided input ``census_period`` (a :class:`Period`, or None without
``--period``). With no period it returns the ``not_requested`` unavailable block and makes
no call. ``change_census`` is the published block built from it, with ``fleet_config``
supplying ``[identities]`` for the approval-integrity counts. A headless run leaves
``change_census`` out of its document without ``--period`` (``lib/run/signals.py``
``PERIOD_ONLY_SCANS``), so a run without the flag is unchanged; ``--period`` needs
``--platform`` there.

GitHub. First the merge times: ``gh pr list --state merged --search "merged:A..B" --limit
LIST_LIMIT --json mergedAt``. GitHub search returns at most 1000 results, so a listing that
comes back full is split into two date spans and each is listed again; a single day still
full is ``truncated``. Then the heavy read, as the sample pages it (#995): the merge times,
newest first, are cut into pages of ``PAGE_SIZE`` (20), never splitting one second across
two pages, and each page is one ``gh pr list`` over its ``merged:<oldest>..<newest>`` span
reading ``CENSUS_FIELDS`` (the sample's ``PR_FIELDS`` plus ``closingIssuesReferences``). A
page refused with HTTP 502 or 504, or the CLI timeout, is read again once at
``PAGE_SIZE // 2`` and the rest of the census uses that size; a second refusal is
``truncated``. A page that returns a different number of changes than its span listed is
``inconsistent``.

GitLab. ``projects/<id>/merge_requests?state=merged&updated_after=<A>T00:00:00Z
&order_by=updated_at&sort=desc&per_page=GITLAB_PER_PAGE&page=<n>`` (100, read again once at
50 on a 502, 504 or timeout) until a short page; a merge is never after its last update, so
every merge in the period is listed. The merges whose ``merged_at`` is inside the period are
kept, newest first, and each costs two reads: ``merge_requests/<iid>/approvals`` and
``merge_requests/<iid>/closes_issues?per_page=1``.

Call ceiling. One census makes at most ``CENSUS_CALL_CEILING`` (1000) CLI calls (``gh pr
list`` on GitHub, ``glab api`` on GitLab), retries included; the call past it is never made
and the census is ``truncated``. On GitHub that covers a full 1000-change listing and its 50
heavy pages many times over; on GitLab, two reads per merge request bound it near 490
merges. ``gh`` pages a listing internally, 100 results per GraphQL request, so one call can
be several requests. The approval counts' bot probes are outside the ceiling: the shared
``BotClassifier`` caps them at ``MAX_LOGIN_PROBES`` (20), past which a count reads null.

Reasons. A remote neither read supports, no remote, a missing or signed-out CLI are the
``gh_cli.unavailable`` block, as for the sample: nothing was read. Once the census starts,
any failure keeps ``available: true`` and sets ``population_complete: false`` with
``incomplete_reason``: ``truncated`` (a cap, the ceiling, or a refusal after the retry),
``no_access`` (HTTP 403), ``not_found`` (404), ``inconsistent``, or the code of the CLI's
reason (``gh_error``, ``gh_bad_json``, ``glab_bad_json``, ...).

Linked issues (#1660 question 9). A change is linked when it has a closing reference:
GitHub ``closingIssuesReferences`` non-empty (a closing keyword, or an issue linked in the
pull request's Development sidebar), GitLab ``closes_issues`` non-empty. An issue mentioned
in a title, body or branch name does not count; the body is never read.
``unlinked_change_count`` is the changes with no closing reference, null when one change's
references could not be read.

Block (``change_census`` in run-context.json)::

    {"available": True, "provider": "github" | "gitlab", "population": "census",
     "period_start": "YYYY-MM-DD", "period_end": "YYYY-MM-DD",
     "population_complete": bool, "incomplete_reason": str,   # only when incomplete
     "census_calls": int, "census_call_ceiling": int,
     "merged_count": int | None, "unlinked_change_count": int | None,
     # the seven approval-integrity counts (lib/hosting/approval_integrity.py, #1664)
     # over the census; ``review_reality`` keeps them over the sample
     "independent_approval_count", ..., "bot_authored_count"}

Privacy. Logins, titles, bodies and issue numbers are read in memory only and never
cached (every read passes ``cache=False``); the block carries counts, dates, flags and
codes. Tests: ``tests/test_merged_census.py``.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, TypedDict

from lib.fleet_config import FleetConfig
from lib.history.ai_usage import matcher_from_config
from lib.hosting.approval_integrity import COUNT_FIELDS, github_counts, gitlab_counts
from lib.hosting.gh_cli import GhUnavailable, GithubRepo, gh_json, git_remote_url, open_github, unavailable
from lib.hosting.gitlab_cli import GitlabProject, glab_api, open_gitlab
from lib.hosting.gitlab_notes import classifier_from_config
from lib.hosting.merged_sample import (
    NO_REMOTE_REASON, PAGE_SIZE, PR_FIELDS, RETRY_STATUSES, UNSUPPORTED_HOST_REASON,
    BotClassifier, _page_end, parse_time, remote_kind,
)

# The most results one GitHub search listing returns.
LIST_LIMIT = 1000

# The most CLI calls one census makes (see the module docstring).
CENSUS_CALL_CEILING = 1000

# Merge requests per GitLab list page; read again once at half on a 502, 504 or timeout.
GITLAB_PER_PAGE = 100

CENSUS_FIELDS = PR_FIELDS + ",closingIssuesReferences"

NOT_REQUESTED_REASON = "not_requested: no --period was given, so no census was read"
NETWORK_REASON = "network: the census reads GitHub or GitLab; run with --platform"

_PERIOD_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})$")

Provider = Literal["github", "gitlab"]


@dataclass(frozen=True)
class Period:
    """An absolute period, both days inclusive."""

    start: date
    end: date

    @property
    def label(self) -> str:
        return f"{self.start.isoformat()}..{self.end.isoformat()}"


def parse_period(text: str) -> Period:
    """``YYYY-MM-DD..YYYY-MM-DD`` as a :class:`Period`; raises ``ValueError`` otherwise."""
    match = _PERIOD_RE.match(text or "")
    if match is None:
        raise ValueError(f"invalid period {text!r}: expected YYYY-MM-DD..YYYY-MM-DD")
    try:
        start, end = date.fromisoformat(match.group(1)), date.fromisoformat(match.group(2))
    except ValueError:
        raise ValueError(f"invalid period {text!r}: not a calendar date") from None
    if start > end:
        raise ValueError(f"invalid period {text!r}: the start is after the end")
    return Period(start, end)


class CensusIncomplete(Exception):
    """The census stopped; ``code`` is its ``incomplete_reason``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class _Budget:
    calls: int = 0

    def take(self) -> None:
        if self.calls >= CENSUS_CALL_CEILING:
            raise CensusIncomplete("truncated")
        self.calls += 1


@dataclass(frozen=True)
class MergedCensus:
    """The census (see the module docstring). ``changes`` are newest merge first, each
    provider's raw objects; ``linked`` holds one flag per change (None: unreadable);
    ``approvals`` one GitLab approvals read per change (empty on GitHub)."""

    provider: Provider
    period: Period
    changes: tuple[dict[str, Any], ...]
    linked: tuple[bool | None, ...]
    complete: bool
    reason: str | None
    calls: int
    approvals: tuple[Any, ...] = ()
    bots: BotClassifier = field(default_factory=BotClassifier, compare=False)


CensusOrReason = MergedCensus | dict[str, Any]


def _too_heavy(e: GhUnavailable) -> bool:
    return e.status in RETRY_STATUSES or e.reason.startswith(("gh_timeout", "glab_timeout"))


def _code(e: GhUnavailable) -> str:
    """A refusal's ``incomplete_reason``: ``truncated`` for a gateway timeout, else its code."""
    if _too_heavy(e):
        return "truncated"
    code = e.reason.split(":", 1)[0].strip()
    return code if re.fullmatch(r"[a-z][a-z0-9_]{0,39}", code) else "unavailable"


def _in_period(stamp: Any, period: Period) -> bool | None:
    when = parse_time(stamp)
    return None if when is None else period.start <= when.date() <= period.end


def _gh_list(repo: GithubRepo, search: str, limit: int, fields: str,
             budget: _Budget) -> list[dict[str, Any]]:
    budget.take()
    got = gh_json(["pr", "list", "--repo", repo.slug, "--state", "merged", "--search", search,
                   "--limit", str(limit), "--json", fields], cache=False)
    if not isinstance(got, list):
        raise GhUnavailable("gh_bad_json: `gh pr list` did not return a list")
    return [pr for pr in got if isinstance(pr, dict)]


def _github_stamps(repo: GithubRepo, period: Period, budget: _Budget) -> list[dict[str, Any]]:
    """Every merge time in the period, newest first, split into date spans under the cap."""
    spans = [(period.start, period.end)]
    found: list[dict[str, Any]] = []
    while spans:
        lo, hi = spans.pop()
        got = _gh_list(repo, f"merged:{lo.isoformat()}..{hi.isoformat()}", LIST_LIMIT,
                       "mergedAt", budget)
        if len(got) >= LIST_LIMIT:
            if lo == hi:
                raise CensusIncomplete("truncated")
            mid = lo + (hi - lo) // 2
            spans += [(lo, mid), (mid + timedelta(days=1), hi)]
            continue
        found += got
    kept: list[tuple[datetime, dict[str, Any]]] = []
    for pr in found:
        when = parse_time(pr.get("mergedAt"))
        if when is None:
            raise GhUnavailable("gh_bad_json: a merged change has no mergedAt")
        if period.start <= when.date() <= period.end:
            kept.append((when, pr))
    kept.sort(key=lambda pair: pair[0], reverse=True)
    return [pr for _, pr in kept]


def _github_census(repo: GithubRepo, period: Period, budget: _Budget,
                   out: list[dict[str, Any]]) -> None:
    """Append every pull request merged in the period to ``out``, page by page."""
    stamps = _github_stamps(repo, period, budget)
    size, read = PAGE_SIZE, 0
    while read < len(stamps):
        page = stamps[read:_page_end(stamps, read, size)]
        span = f"merged:{page[-1]['mergedAt']}..{page[0]['mergedAt']}"
        try:
            got = _gh_list(repo, span, max(size, len(page)), CENSUS_FIELDS, budget)
        except GhUnavailable as e:
            if size < PAGE_SIZE or not _too_heavy(e):
                raise
            size = PAGE_SIZE // 2
            continue
        if len(got) != len(page):
            raise CensusIncomplete("inconsistent")
        out += got
        read += len(page)


def _github_linked(pr: dict[str, Any]) -> bool | None:
    refs = pr.get("closingIssuesReferences")
    if isinstance(refs, dict):
        refs = refs.get("nodes")
    return bool(refs) if isinstance(refs, list) else None


def _glab(project: GitlabProject, path: str, budget: _Budget) -> Any:
    budget.take()
    return glab_api(project, path, cache=False)  # titles and descriptions: in memory only


def _gitlab_census(project: GitlabProject, period: Period, budget: _Budget,
                   out: list[dict[str, Any]], approvals: list[Any], linked: list[bool | None]) -> None:
    """Append every merge request merged in the period, its approvals and link flag."""
    enc, size = project.encoded, GITLAB_PER_PAGE
    listed: list[Any] = []
    while True:
        try:
            page = _glab(project, f"projects/{enc}/merge_requests?state=merged"
                                  f"&updated_after={period.start.isoformat()}T00:00:00Z"
                                  f"&order_by=updated_at&sort=desc&per_page={size}"
                                  f"&page={len(listed) // size + 1}", budget)
        except GhUnavailable as e:
            if size < GITLAB_PER_PAGE or not _too_heavy(e):
                raise
            size = GITLAB_PER_PAGE // 2
            continue
        if not isinstance(page, list):
            raise GhUnavailable("glab_bad_json: the merged merge request list was not a list")
        listed += page
        if len(page) < size:
            break
    kept: list[tuple[datetime, dict[str, Any]]] = []
    for mr in listed:
        if not isinstance(mr, dict):
            continue
        inside = _in_period(mr.get("merged_at"), period)
        if inside is None:
            raise GhUnavailable("glab_bad_json: a merged merge request has no merged_at")
        when = parse_time(mr.get("merged_at"))
        if inside and when is not None:
            kept.append((when, mr))
    kept.sort(key=lambda pair: pair[0], reverse=True)
    for _, mr in kept:
        iid = mr.get("iid")
        if not isinstance(iid, int):
            raise GhUnavailable("glab_bad_json: a merged merge request has no iid")
        approvals.append(_glab(project, f"projects/{enc}/merge_requests/{iid}/approvals", budget))
        closes = _glab(project, f"projects/{enc}/merge_requests/{iid}/closes_issues?per_page=1", budget)
        linked.append(bool(closes) if isinstance(closes, list) else None)
        out.append(mr)


def merged_census(repo_root: Path, census_period: Period | None) -> CensusOrReason:
    """The ``merged_census`` registry value (see the module docstring)."""
    if census_period is None:
        return unavailable(NOT_REQUESTED_REASON)
    try:
        kind = remote_kind(git_remote_url(repo_root))
        if kind == "none":
            return unavailable(NO_REMOTE_REASON)
        if kind == "unsupported":
            return unavailable(UNSUPPORTED_HOST_REASON)
        project = open_gitlab(repo_root)
        if project is None and kind == "platform":
            return unavailable(UNSUPPORTED_HOST_REASON)
        repo = open_github(repo_root) if project is None else None
    except GhUnavailable as e:
        return unavailable(e.reason)
    budget = _Budget()
    changes: list[dict[str, Any]] = []
    approvals: list[Any] = []
    linked: list[bool | None] = []
    reason: str | None = None
    try:
        if project is not None:
            _gitlab_census(project, census_period, budget, changes, approvals, linked)
        elif repo is not None:
            _github_census(repo, census_period, budget, changes)
            linked = [_github_linked(pr) for pr in changes]
    except CensusIncomplete as e:
        reason = e.code
    except GhUnavailable as e:
        reason = _code(e)
    return MergedCensus("gitlab" if project is not None else "github", census_period,
                        tuple(changes), tuple(linked), reason is None, reason, budget.calls,
                        tuple(approvals))


def offline_merged_census(repo_root: Path, census_period: Period | None) -> CensusOrReason:
    """The ``merged_census`` value without the platform: no call is made."""
    if census_period is None:
        return unavailable(NOT_REQUESTED_REASON)
    kind = remote_kind(git_remote_url(repo_root))
    if kind == "none":
        return unavailable(NO_REMOTE_REASON)
    if kind == "unsupported":
        return unavailable(UNSUPPORTED_HOST_REASON)
    return unavailable(NETWORK_REASON)


class ChangeCensusBlock(TypedDict, total=False):
    """``run-context.json`` ``change_census`` (see the module doc)."""

    available: bool
    reason: str
    provider: Literal["github", "gitlab"]
    population: Literal["census"]
    period_start: str
    period_end: str
    population_complete: bool
    incomplete_reason: str
    census_calls: int
    census_call_ceiling: int
    merged_count: int | None
    unlinked_change_count: int | None
    independent_approval_count: int | None
    no_independent_approval_count: int | None
    bot_only_approval_count: int | None
    stale_approval_count: int | None
    approved_after_merge_count: int | None
    self_merged_unapproved_count: int | None
    bot_authored_count: int | None


def _unlinked(flags: Sequence[bool | None]) -> int | None:
    return None if None in flags else sum(not f for f in flags)


def change_census(census: CensusOrReason, fleet_config: FleetConfig | None) -> ChangeCensusBlock:
    """The ``change_census`` block; an unavailable census is returned as the block."""
    if not isinstance(census, MergedCensus):
        return census  # type: ignore[return-value]  # the unavailable block, reason and all
    block: ChangeCensusBlock = {
        "available": True, "provider": census.provider, "population": "census",
        "period_start": census.period.start.isoformat(),
        "period_end": census.period.end.isoformat(),
        "population_complete": census.complete,
        "census_calls": census.calls, "census_call_ceiling": CENSUS_CALL_CEILING,
    }
    if not census.complete:
        block["incomplete_reason"] = census.reason or "unavailable"
        block["merged_count"] = block["unlinked_change_count"] = None
        for name in COUNT_FIELDS:
            block[name] = None  # type: ignore[literal-required]
        return block
    changes = list(census.changes)
    block["merged_count"] = len(changes)
    block["unlinked_change_count"] = _unlinked(census.linked)
    if census.provider == "gitlab":
        counts = gitlab_counts(changes, census.approvals, classifier_from_config(fleet_config))
    else:
        counts = github_counts(changes, census.bots, matcher_from_config(fleet_config))
    block.update(counts)  # type: ignore[typeddict-item]
    return block
