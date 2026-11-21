"""Delivery figures: how work flows through review to merge (issue #715, part of #702).

A platform block. It reads the shared ``merged_sample`` (``lib/merged_sample.py``:
up to 30 changes merged in the run window, newest merge first) and the
``review_reality`` block built from the same sample, so both blocks describe the
same changes and the shares ``review_reality`` already computes are copied, never
computed a second time. ``fleet --platform`` runs it in the platform pass
(``PLATFORM_SCANS``); without ``--platform`` it is the offline ``network``
placeholder, never zeros.

Block (``delivery``)::

    {"available": True, "merged_count": int,
     "time_to_merge_hours_p50": float | None,   # createdAt -> mergedAt
     "time_to_merge_hours_p90": float | None,
     "first_human_comment_hours_p50": float | None,  # createdAt -> first human comment
     "first_human_comment_hours_p90": float | None,
     "human_commented_share": float | None,     # changes with any human comment
     "ai_review_share": float | None,           # changes with an AI reviewer's review or comment
     "approved_share": float | None,            # copied from review_reality
     "self_merged_share": float | None,         # copied from review_reality
     "agent_authored_share": float | None}      # changes an agent identity authored

Durations are hours, rounded to 0.01, over the sampled changes that have both
timestamps; a percentile is the nearest-rank value (the ``ceil(p * n)``-th
smallest), so it is always one observed duration. Every figure is None when
nothing was sampled.

First human comment (GitHub). The earliest comment (``createdAt``) or review
(``submittedAt``) on the pull request by an account that is neither its author,
nor ``github-actions``, nor a bot (the sample's shared ``BotClassifier``), nor an
account an ``[[ai_usage.agent_identities]]`` rule matches (a configured agent on
an ordinary user login is AI review, never also a human commenter). Its
hours are counted from ``createdAt`` and only over the changes that have one;
``human_commented_share`` says how many do. An event with no author login (a
deleted account) is skipped. When an account whose type could not be learned (a
failed probe, or one left unprobed because the shared ``MAX_LOGIN_PROBES`` budget
was spent) comes before the first known human on any change, the three
first-comment figures are None: the answer depends on that account.

AI review (GitHub). A change counts when a review or comment comes from an AI
reviewer other than the change's author (an agent commenting on its own pull
request is not review): an account an ``[[ai_usage.agent_identities]]`` config rule matches by
username (the #695 list; ``[bot]`` is tried as well, since ``gh pr list`` drops
it), or a bot account (``BotClassifier`` says so) whose login is in
``AI_REVIEWERS`` or matches a pinned ``agent_identity.AGENT_RULES`` entry with
``[bot]`` appended. Unlike ``review_reality``'s ``bot_review_share``, status and
deployment bots (codecov, vercel, sonar) do not count. When an account whose
type is unknown decides a change, ``ai_review_share`` is None.

Agent-authored share (#717). The share of sampled changes whose author an agent
identity matches, by the #695 username form (``AuthorMatcher.classify_username``):
a config rule, tried on the login with and without ``[bot]``, or a pinned
``AGENT_RULES`` entry. On GitHub ``gh`` drops an App author's ``[bot]`` (and
``login`` drops ``app/``), so ``[bot]`` is re-appended for the pinned rules only
when the author object says ``is_bot``; a person whose login is ``claude`` is not
the Claude App. On GitLab the merge request author's ``username`` is classified
as it stands: an access-token user counts only under ``token_users_as_agents``,
and a dependency bot never counts. A change with no author login (a deleted
account) counts as not agent-authored. The author comes from the sample's own
listing, so no further read is made.

GitLab. The sample's merge requests give time to merge (``created_at`` ->
``merged_at``) and the copied shares; first human comment and AI review need
each merge request's notes, which #716 reads, so those figures are None here.

Privacy: counts, shares and durations only; no title, login or comment text.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal, TypedDict

from lib.agent_identity import AGENT, AuthorMatcher
from lib.ai_usage import matcher_from_config
from lib.fleet_config import FleetConfig
from lib.merged_sample import STATUS_BOTS, BotClassifier, MergedSample, SampleOrReason, login, parse_time

# AI code-review apps, by GitHub login with the ``[bot]`` suffix dropped. A login
# counts only when ``BotClassifier`` reads the account as a bot. ``gh pr list``
# drops the suffix and carries no account type for comment and review authors,
# and the classifier's ``users/<login>[bot]`` probe only shows that the App
# exists, so a person whose login equals one of these App names would read as
# that App. The names are App slugs, which few people hold. Extend with an
# ``[[ai_usage.agent_identities]]`` config rule.
AI_REVIEWERS = frozenset({
    "coderabbitai",                    # CodeRabbit
    "copilot-pull-request-reviewer",   # GitHub Copilot code review
    "claude",                          # Claude GitHub App
    "gemini-code-assist",              # Gemini Code Assist
    "sourcery-ai",                     # Sourcery
    "qodo-merge-pro",                  # Qodo Merge
    "greptile-apps",                   # Greptile
    "ellipsis-dev",                    # Ellipsis
    "cursor",                          # Cursor Bugbot
    "chatgpt-codex-connector",         # OpenAI Codex
})

_HOUR = 3600.0


class DeliveryBlock(TypedDict):
    """The ``delivery`` block when the sample was read."""

    available: Literal[True]
    merged_count: int
    time_to_merge_hours_p50: float | None
    time_to_merge_hours_p90: float | None
    first_human_comment_hours_p50: float | None
    first_human_comment_hours_p90: float | None
    human_commented_share: float | None
    ai_review_share: float | None
    approved_share: float | None
    self_merged_share: float | None
    agent_authored_share: float | None


def percentile(values: Sequence[float], p: float) -> float | None:
    """The nearest-rank ``p`` percentile (0 < p <= 1) of ``values``; None when empty."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p * len(ordered)) - 1)]


def _hours(start: Any, end: Any) -> float | None:
    a = parse_time(start)
    b = end if isinstance(end, datetime) else parse_time(end)
    if a is None or b is None:
        return None
    return round(max(0.0, (b - a).total_seconds()) / _HOUR, 2)


def _share(count: int, total: int) -> float | None:
    return round(count / total, 3) if total else None


def _events(pr: dict[str, Any]) -> list[tuple[datetime, Any]]:
    """``(time, author)`` for every dated comment and review, oldest first."""
    out: list[tuple[datetime, Any]] = []
    for kind, stamp in (("comments", "createdAt"), ("reviews", "submittedAt")):
        for item in pr.get(kind) or []:
            when = parse_time(item.get(stamp)) if isinstance(item, dict) else None
            if when is not None:
                out.append((when, item.get("author")))
    out.sort(key=lambda e: e[0])
    return out


_UNDECIDED = "undecided"


def first_human_comment(pr: dict[str, Any], bots: BotClassifier,
                        matcher: AuthorMatcher | None = None) -> float | None | str:
    """Hours to the first human comment, None for none, ``_UNDECIDED`` when an
    account of unknown type (probe failed or budget spent) comes first."""
    author = login(pr.get("author"))
    for when, actor in _events(pr):
        name = login(actor)
        if (name is None or name == author or name in STATUS_BOTS
                or (matcher is not None and _configured(matcher, name))):
            continue
        verdict = bots.is_bot(actor)
        if verdict is None:
            return _UNDECIDED
        if not verdict:
            return _hours(pr.get("createdAt"), when)
    return None


def _configured(matcher: AuthorMatcher, name: str) -> bool:
    return any(matcher.classify_username(n).rule_index is not None for n in (name, f"{name}[bot]"))


def agent_authored(change: dict[str, Any], provider: str, matcher: AuthorMatcher) -> bool:
    """True when an agent identity authored the change (see the module docstring)."""
    actor = change.get("author")
    if provider == "gitlab":
        username = actor.get("username") if isinstance(actor, dict) else None
        return isinstance(username, str) and matcher.classify_username(username).bucket == AGENT
    name = login(actor)
    if name is None:
        return False
    if _configured(matcher, name):
        return True
    forms = [name]
    if isinstance(actor, dict) and actor.get("is_bot") is True and not name.endswith("[bot]"):
        forms.append(f"{name}[bot]")
    return any(matcher.classify_username(n).bucket == AGENT for n in forms)


def _pinned(matcher: AuthorMatcher, name: str) -> bool:
    bare = name.removesuffix("[bot]")
    return bare in AI_REVIEWERS or matcher.classify_username(f"{bare}[bot]").bucket == AGENT


def ai_reviewed(pr: dict[str, Any], bots: BotClassifier, matcher: AuthorMatcher) -> bool | None:
    """True on an AI reviewer's review or comment, False on none, None when an
    account of unknown type decides."""
    unknown = False
    author = login(pr.get("author"))
    for _, actor in _events(pr):
        name = login(actor)
        if name is None or name == author:
            continue
        if _configured(matcher, name):
            return True
        if not _pinned(matcher, name):
            continue
        verdict = bots.is_bot(actor)
        if verdict:
            return True
        if verdict is None:
            unknown = True
    return None if unknown else False


def _copied(review: Any, key: str) -> float | None:
    value = review.get(key) if isinstance(review, dict) and review.get("available") else None
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _block(total: int, merge: list[float], comment: list[float] | None, commented: int | None,
           ai: int | None, review: Any) -> DeliveryBlock:
    return {
        "available": True,
        "merged_count": total,
        "time_to_merge_hours_p50": percentile(merge, 0.5),
        "time_to_merge_hours_p90": percentile(merge, 0.9),
        "first_human_comment_hours_p50": percentile(comment, 0.5) if comment is not None else None,
        "first_human_comment_hours_p90": percentile(comment, 0.9) if comment is not None else None,
        "human_commented_share": _share(commented, total) if commented is not None else None,
        "ai_review_share": _share(ai, total) if ai is not None else None,
        "approved_share": _copied(review, "approved_share"),
        "self_merged_share": _copied(review, "self_merged_share"),
        "agent_authored_share": None,  # filled by scan_delivery from the same sample
    }


def github_delivery(sample: MergedSample, review: Any, matcher: AuthorMatcher) -> DeliveryBlock:
    prs = list(sample.changes)
    merge = [h for h in (_hours(pr.get("createdAt"), pr.get("mergedAt")) for pr in prs) if h is not None]
    firsts = [first_human_comment(pr, sample.bots, matcher) for pr in prs]
    decided = _UNDECIDED not in firsts
    comment = [f for f in firsts if isinstance(f, float)] if decided else None
    commented = sum(isinstance(f, float) for f in firsts) if decided else None
    verdicts = [ai_reviewed(pr, sample.bots, matcher) for pr in prs]
    ai = None if None in verdicts else sum(bool(v) for v in verdicts)
    return _block(len(prs), merge, comment, commented, ai, review)


def gitlab_delivery(sample: MergedSample, review: Any) -> DeliveryBlock:
    mrs = list(sample.changes)
    merge = [h for h in (_hours(mr.get("created_at"), mr.get("merged_at")) for mr in mrs) if h is not None]
    return _block(len(mrs), merge, None, None, None, review)


def scan_delivery(sample: SampleOrReason, review_reality: Any,
                  fleet_config: FleetConfig | None = None) -> DeliveryBlock | dict[str, Any]:
    """The ``delivery`` registry entry (see the module docstring). An unavailable
    sample is returned as the block, reason and all."""
    if not isinstance(sample, MergedSample):
        return sample
    matcher = matcher_from_config(fleet_config)
    if sample.provider == "gitlab":
        block = gitlab_delivery(sample, review_reality)
    else:
        block = github_delivery(sample, review_reality, matcher)
    authored = sum(agent_authored(c, sample.provider, matcher) for c in sample.changes)
    block["agent_authored_share"] = _share(authored, len(sample.changes))
    return block
