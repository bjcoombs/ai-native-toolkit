"""Approval integrity: who approved each sampled merged change, on which commit, and when (#1664).

``review_reality`` (``lib/hosting/review_reality.py``) reads these counts beside its shares.
``approved_share`` keeps its meaning: an ``APPROVED`` review by any account other than the
author, bots included (#1660 question 4). The counts below separate a person's approval
from a bot's, and read when it happened, from the fields the shared ``merged_sample``
already holds (``lib/hosting/merged_sample.py``: ``reviews[].state``, ``.submittedAt``,
``.commit.oid``, ``headRefOid``, ``mergedAt``, ``mergedBy``), so no listing is added. The
one extra cost is the sample's shared ``BotClassifier``: an approver the comment pass has not
already classified costs one ``users/<login>[bot]`` probe, from the same
``MAX_LOGIN_PROBES`` (20) budget the comments spend first. Past that cap an approver is
unknown, and the counts it decides read null.

A **qualifying approval** is an ``APPROVED`` review (GitHub) or an ``approved_by`` entry
(GitLab) by an account that is neither the change's author nor a bot. Each count is a
number of sampled changes:

- ``independent_approval_count``: changes with a qualifying approval;
  ``no_independent_approval_count`` the rest of the sample.
- ``bot_only_approval_count``: changes approved by another account, every one a bot.
- ``stale_approval_count``: changes whose qualifying approvals all name a ``commit.oid``
  other than ``headRefOid``. GitLab gives no reviewed commit: always null there.
- ``approved_after_merge_count``: changes whose earliest qualifying approval is after
  ``mergedAt`` (GitLab: ``approved_by[].approved_at`` against ``merged_at``; null where
  GitLab does not give the time).
- ``self_merged_unapproved_count``: changes merged by their author with no qualifying
  approval.
- ``bot_authored_count``: changes a bot authored. The other counts still cover every
  sampled change, so the approval counts add up to ``merged_count``.

Who is a bot. A confirmed ``[identities]`` entry decides first (``.assess/config.toml``,
``references/identities.md``): ``people`` are people; ``bots`` and ``agents`` are bots, as an
agent's approval is not a person's. On GitHub the login is tried as given and with
``[bot]``, since ``gh`` drops the suffix. Otherwise GitHub asks the sample's shared
``BotClassifier`` (``github-actions`` is always a bot), and GitLab, which has no bot flag,
reads the note classifier's rules (``lib/hosting/gitlab_notes.py``): an agent, an access-token
user or a dependency bot is a bot, an unconfirmed AI-name hint is unknown, anyone else a
person. A review with no author login (a deleted account) is skipped, as ``approved_share``
skips it.

Unknown is never a false count. A change whose answer depends on an account of unknown bot
status, on a missing commit or on an unreadable time is undecided, and one undecided change
makes that count null. A known answer is kept where the unknown account cannot change it:
a person's approval on the head commit before the merge settles all three approval facts.

Privacy: logins are read in memory only; the counts are the only output. Tests:
``tests/test_approval_integrity.py``.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, TypedDict

from lib.agent_identity import AuthorMatcher
from lib.hosting.gitlab_notes import NoteClassifier
from lib.hosting.merged_sample import STATUS_BOTS, BotClassifier, login, parse_time

Party = Literal["person", "bot"] | None
PartyOf = Callable[[Any], Party]

COUNT_FIELDS = (
    "independent_approval_count", "no_independent_approval_count",
    "bot_only_approval_count", "stale_approval_count", "approved_after_merge_count",
    "self_merged_unapproved_count", "bot_authored_count",
)


class ApprovalIntegrityCounts(TypedDict):
    """The counts ``review_reality`` carries (see the module docstring)."""

    independent_approval_count: int | None
    no_independent_approval_count: int | None
    bot_only_approval_count: int | None
    stale_approval_count: int | None
    approved_after_merge_count: int | None
    self_merged_unapproved_count: int | None
    bot_authored_count: int | None


@dataclass(frozen=True)
class Approval:
    """One approval by an account other than the author. ``stale`` is None when the
    reviewed commit or the head is unknown; ``at`` None when the time is unreadable."""

    party: Party
    stale: bool | None
    at: datetime | None


@dataclass(frozen=True)
class Change:
    author: Party
    self_merged: bool
    approvals: tuple[Approval, ...]
    merged_at: datetime | None


def _confirmed(matcher: AuthorMatcher, names: Sequence[str]) -> Party:
    for name in names:
        confirmed = matcher.confirmed_username(name)
        if confirmed == "person":
            return "person"
        if confirmed in ("bot", "agent"):
            return "bot"
    return None


def github_party(bots: BotClassifier, matcher: AuthorMatcher) -> PartyOf:
    """A GitHub actor's party: ``[identities]`` first, then the shared classifier."""
    def party(actor: Any) -> Party:
        name = login(actor)
        if name is None:
            return None
        if name in STATUS_BOTS:
            return "bot"
        confirmed = _confirmed(matcher, (name, f"{name}[bot]"))
        if confirmed is not None:
            return confirmed
        verdict = bots.is_bot(actor)
        return None if verdict is None else "bot" if verdict else "person"
    return party


def gitlab_party(classifier: NoteClassifier) -> PartyOf:
    """A GitLab user's party, by username alone (GitLab has no bot flag)."""
    def party(user: Any) -> Party:
        name = user.get("username") if isinstance(user, dict) else None
        if not isinstance(name, str) or not name:
            return None
        confirmed = _confirmed(classifier.matcher, (name,))
        if confirmed is not None:
            return confirmed
        if classifier.is_ai(user) or classifier.is_automation(user):
            return "bot"
        return None if classifier.is_hint(user) else "person"
    return party


def _independent(c: Change) -> bool | None:
    parties = [a.party for a in c.approvals]
    if "person" in parties:
        return True
    return None if None in parties else False


def _bot_only(c: Change) -> bool | None:
    parties = [a.party for a in c.approvals]
    if not parties or "person" in parties:
        return False
    return None if None in parties else True


def _stale(c: Change) -> bool | None:
    people = [a for a in c.approvals if a.party == "person"]
    maybe = [a for a in c.approvals if a.party is None]
    if any(a.stale is False for a in people):
        return False
    if not people and not maybe:
        return False  # no qualifying approval: nothing to be stale
    if maybe or any(a.stale is None for a in people):
        return None
    return True


def _after_merge(c: Change) -> bool | None:
    people = [a for a in c.approvals if a.party == "person"]
    maybe = [a for a in c.approvals if a.party is None]
    if not people and not maybe:
        return False
    if not people or c.merged_at is None:
        return None
    known = [a.at for a in people if a.at is not None]
    if known and min(known) <= c.merged_at:
        return False  # any other approval, unknown time or account, can only be earlier still
    if maybe or len(known) < len(people):
        return None
    return True


def _self_merged_unapproved(c: Change) -> bool | None:
    if not c.self_merged:
        return False
    independent = _independent(c)
    return None if independent is None else not independent


def _count(flags: Sequence[bool | None]) -> int | None:
    return None if None in flags else sum(bool(f) for f in flags)


def counts(changes: Sequence[Change], stale_readable: bool = True) -> ApprovalIntegrityCounts:
    """The counts over ``changes``; ``stale_readable`` False (GitLab) makes the stale count null."""
    independent = _count([_independent(c) for c in changes])
    return {
        "independent_approval_count": independent,
        "no_independent_approval_count": None if independent is None else len(changes) - independent,
        "bot_only_approval_count": _count([_bot_only(c) for c in changes]),
        "stale_approval_count": _count([_stale(c) for c in changes]) if stale_readable else None,
        "approved_after_merge_count": _count([_after_merge(c) for c in changes]),
        "self_merged_unapproved_count": _count([_self_merged_unapproved(c) for c in changes]),
        "bot_authored_count": _count([None if c.author is None else c.author == "bot"
                                      for c in changes]),
    }


def _oid(value: Any) -> str | None:
    oid = value.get("oid") if isinstance(value, dict) else None
    return oid if isinstance(oid, str) and oid else None


def github_change(pr: dict[str, Any], party: PartyOf) -> Change:
    """One sampled pull request as a :class:`Change`."""
    author = login(pr.get("author"))
    head = pr.get("headRefOid")
    head = head if isinstance(head, str) and head else None
    approvals: list[Approval] = []
    for review in pr.get("reviews") or []:
        if not isinstance(review, dict) or review.get("state") != "APPROVED":
            continue
        reviewer = login(review.get("author"))
        if reviewer is None or reviewer == author:
            continue
        oid = _oid(review.get("commit"))
        stale = None if oid is None or head is None else oid != head
        approvals.append(Approval(party(review.get("author")), stale,
                                  parse_time(review.get("submittedAt"))))
    merger = login(pr.get("mergedBy"))
    return Change(party(pr.get("author")), author is not None and author == merger,
                  tuple(approvals), parse_time(pr.get("mergedAt")))


def github_counts(prs: Sequence[dict[str, Any]], bots: BotClassifier,
                  matcher: AuthorMatcher) -> ApprovalIntegrityCounts:
    """The counts over a GitHub sample (``gh pr list`` objects)."""
    party = github_party(bots, matcher)
    return counts([github_change(pr, party) for pr in prs])


def _gl_name(user: Any) -> str | None:
    name = user.get("username") if isinstance(user, dict) else None
    return name.lower() if isinstance(name, str) and name else None


def gitlab_change(mr: dict[str, Any], approvals: Any, party: PartyOf) -> Change:
    """One sampled merge request and its ``approvals`` read as a :class:`Change`."""
    author = _gl_name(mr.get("author"))
    merger = _gl_name(mr.get("merge_user")) or _gl_name(mr.get("merged_by"))
    entries = approvals.get("approved_by") if isinstance(approvals, dict) else None
    out: list[Approval] = []
    for entry in entries or []:
        user = entry.get("user") if isinstance(entry, dict) else None
        name = _gl_name(user)
        if name is None or name == author:
            continue
        out.append(Approval(party(user), None, parse_time(entry.get("approved_at"))))
    return Change(party(mr.get("author")), author is not None and author == merger,
                  tuple(out), parse_time(mr.get("merged_at")))


def gitlab_counts(mrs: Sequence[dict[str, Any]], approvals: Sequence[Any],
                  classifier: NoteClassifier) -> ApprovalIntegrityCounts:
    """The counts over a GitLab sample; ``approvals`` is each merge request's read, in order."""
    party = gitlab_party(classifier)
    return counts([gitlab_change(mr, a, party) for mr, a in zip(mrs, approvals)],
                  stale_readable=False)
