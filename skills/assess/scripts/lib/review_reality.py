"""Review reality: whether merged pull requests were actually reviewed.

Layer 7 asks whether every change gets design-level feedback. A required-review
rule that every merge bypasses reads as Present and is hollow. This scan reads
the shared ``merged_sample`` (``lib/merged_sample.py``, #715): up to 30 changes
merged inside the run window, the most recently merged first (before #715 it
was the 30 most recently *opened* merged pull requests, unwindowed). It
reports, as counts and shares only, how many carried a review from someone other than the
author, a comment from a review bot, and an author who merged their own change,
next to whether the default branch requires an approving review.

Block (``review_reality`` in run-context.json)::

    {"available": True, "merged_count": int,
     "oldest_merged_days_ago": int | None,  # age of the oldest sampled merge
     "reviewed_share": float | None,      # a review by an account other than the author
     "approved_share": float | None,      # an APPROVED review by an account other than the author
     "bot_review_share": float | None,    # a comment by a bot other than github-actions
     "self_merged_share": float | None,   # author and merger the same account
     "review_required": bool | None,      # ruleset pull_request rule or classic protection,
                                          # each with required_approving_review_count >= 1
     "hollow_required_review": bool | None,  # review_required and reviewed_share < 0.2
     "required_approval_bypassed": bool | None}  # review_required and approved_share < 0.2

The two flags answer different questions. ``hollow_required_review``: did anyone
other than the author look at the change at all (an advisory bot's review
counts). ``required_approval_bypassed``: did the approval the rule demands
happen; it fires on a repo where an AI reviewer comments on every pull request
while merges land without an approval. Both are null below ``MIN_SAMPLE``
merged pull requests. The rules are read as they stand now and the sample may
predate them, so ``oldest_merged_days_ago`` lets a reader weigh a rule added
recently against older merges.

Shares are floats in [0, 1], None when nothing was sampled. ``review_required``
is None when the rules could not be read (a refused protection read on a branch
no ruleset covers), and ``hollow_required_review`` follows it. ``bot_review_share``
is None when a commenter's account type could not be learned and that decides
whether a sampled pull request counts. A comment with no author login (a deleted
account) is skipped, as in ``delivery`` (#769).

Bot classification is the sample's ``merged_sample.BotClassifier`` (shared with
``delivery``, so an account is probed once per run). ``github-actions`` comments
are status comments and never count; neither does anything in ``reviews``.

Privacy: no title, login or user name is written to the block - only counts,
shares and booleans. A failed sample read degrades the whole block to the
sample's ``{"available": False, "reason"}`` (``no_access`` on HTTP 403). GitHub access
goes through ``gh_cli``.

GitLab. When ``gitlab_cli.open_gitlab`` resolves the remote to a GitLab
project, the same block is built from the sample's merged merge requests through
``glab``: each one's ``approvals`` read. ``approved_share`` counts a merge request approved by
an account other than its author and ``self_merged_share`` one whose
``merge_user`` (else ``merged_by``) is its author. The sample's notes
(``gitlab_note_facts``, ``lib/gitlab_notes.py``, #716; read once, in memory, never
cached, only facts kept) give ``bot_review_share``, a note carrying an AI-review
marker or posted by an agent or a pinned AI-review app (a GitLab AI reviewer often
posts as an access-token user, ``project_<id>_bot_<hash>``, so it is recognised by its
output; a token user's unmarked note counts only under ``token_users_as_agents`` or an
identity rule, since coverage and release bots post the same way), and
``reviewed_share``, an approval by another account, a human comment by someone other
than the author, or AI review. Notes that
still decide nothing after up to ``NOTES_MAX_PAGES`` (3) pages / 300 notes leave that
merge request unknown and the share None; so does a call made without note facts. ``hollow_required_review`` follows
``reviewed_share`` and is False whenever approvals alone clear the threshold. A
refused notes read degrades the whole block. ``review_required`` reads the
project's approval rules: a rule (other than a ``report_approver`` rule)
requiring one or more approvals that covers the default branch - no branch
scope, or a protected-branch name or wildcard matching it - is True; a rule
scoped to "all protected branches" covers it when ``gitlab_cli.branch_protection``
finds the default branch protected, does not when the complete list leaves
it unprotected, and is unknown when that read is refused or incomplete
(read at most once per scan); a refused read is None, never False. Any failed or
refused merge-request or approvals read (403, 404 included) degrades the
whole block, never reads as "no reviews".

Layer 7 truth-pressure signal: whether merged pull requests were reviewed. The
registry entry `review_reality_from_sample` reads the shared `merged_sample`
(above; before #715 it was the 30 most recently opened merged pull requests,
unwindowed, so figures shift with the window); `scan_review_reality(repo_root,
history_window=None, run_date=None)` draws the sample itself. It
emits `review_reality: {available, merged_count, oldest_merged_days_ago,
reviewed_share, approved_share, bot_review_share, self_merged_share,
review_required, hollow_required_review, required_approval_bypassed}`.
`reviewed_share` counts a review in any state by any account other than the
author; `approved_share` counts only `APPROVED` ones;
`bot_review_share` counts a comment by a bot other than `github-actions` (nothing
in `reviews`), classified by the sample's shared `BotClassifier`. An unknown
author withholds the share (null) only when it decides a pull
request: one with a confirmed bot comment counts regardless. A comment with no
author login (a deleted account) is skipped, as in `delivery` (#769).
`review_required` reads the default branch's effective rules
(`repos/<slug>/rules/branches/<branch>`, inherited rulesets included) and then
classic protection, each needing `required_approving_review_count` of 1 or more;
null when neither says yes and one was refused. `hollow_required_review` is
`review_required` and `reviewed_share` under `HOLLOW_THRESHOLD` (0.2);
`required_approval_bypassed` is the same test on `approved_share`, so it fires
where an AI reviewer comments on every change but nobody approves. Both are null
when either input is unknown or under `MIN_SAMPLE` (5) merges were sampled. The
rules are read as they stand now, so `oldest_merged_days_ago` travels with them. Counts, shares and booleans only: no title or login reaches
the block. A failed sample read degrades the whole block to `available:
False`. On a GitLab remote (`gitlab_cli.open_gitlab`) the same block comes from the
sample's merged merge requests (`scan_gitlab_review_reality(project, mrs, notes=...)`) and
one `approvals` read each: `approved_share` (approved by an account other than the author),
`self_merged_share` (`merge_user`, else `merged_by`, is the author), and the note facts of
the private `gitlab_note_facts` spec (#716, one notes read per merge request, shared with
`delivery`): `bot_review_share` is a note with an
AI-review marker or by an agent or pinned AI-review app (an access-token user only when
it is an agent); `reviewed_share` is an approval by another account, a human comment by
someone other than the author, or AI review. A merge request whose notes still decide nothing after up to `NOTES_MAX_PAGES` (3)
pages / 300 notes makes the share null.
`hollow_required_review` follows `reviewed_share` and is False whenever approvals alone
clear the threshold. A refused or malformed notes read degrades the whole block like an
approvals read. `review_required` reads the project's approval rules: a non-`report_approver`
rule needing 1+ approvals with no branch scope, or a protected-branch name or `*`
wildcard matching the default branch, is True; a rule scoped to all protected branches
covers it when `gitlab_cli.branch_protection` finds the default branch protected, does
not when the complete list leaves it unprotected, and is null when that read (made at
most once) is refused or incomplete; a refused read is null. Any refused merge-request or approvals read (403
and 404 included) degrades the whole block. Tests: `tests/test_review_reality.py`,
`tests/test_review_reality_gitlab.py`, `tests/test_gitlab_protected_branches.py`.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from lib.fleet_config import FleetConfig
from lib.gh_cli import GhUnavailable, gh_api, unavailable
from lib.gitlab_cli import GitlabProject, branch_pattern, branch_protection, glab_api
from lib.merged_sample import (
    PR_FIELDS as PR_FIELDS,  # re-exported: the fields the sample reads
    STATUS_BOTS, BotClassifier, MergedSample, SampleOrReason, merged_sample, parse_time,
)
from lib.gitlab_notes import NoteFacts, NoteFactsOrReason, gitlab_note_facts
from lib.merged_sample import login as _login

# Under this share of reviewed merges, a required-review rule is hollow.
HOLLOW_THRESHOLD = 0.2

# Fewer merged pull requests than this are too few to call a rule bypassed:
# both flags are null below it.
MIN_SAMPLE = 5


def _share(count: int, total: int) -> float | None:
    return round(count / total, 3) if total else None


def _has_bot_comment(pr: dict[str, Any], bots: BotClassifier) -> bool | None:
    """True on a bot comment, False on none, None when an unknown author decides."""
    unknown = False
    for comment in pr.get("comments") or []:
        actor = comment.get("author") if isinstance(comment, dict) else None
        name = _login(actor)
        if name is None or name in STATUS_BOTS:
            continue  # a deleted account (no login) is skipped, as in ``delivery``
        verdict = bots.is_bot(actor)
        if verdict:
            return True
        if verdict is None:
            unknown = True
    return None if unknown else False


def _reviewed_by_other(pr: dict[str, Any], approving: bool = False) -> bool:
    """A review by an account other than the author; ``approving`` counts only
    reviews in the ``APPROVED`` state."""
    author = _login(pr.get("author"))
    for review in pr.get("reviews") or []:
        if not isinstance(review, dict):
            continue
        if approving and review.get("state") != "APPROVED":
            continue
        reviewer = _login(review.get("author"))
        if reviewer is not None and reviewer != author:
            return True
    return False


def _self_merged(pr: dict[str, Any]) -> bool:
    author, merger = _login(pr.get("author")), _login(pr.get("mergedBy"))
    return author is not None and author == merger


def _approvals(params: Any) -> int:
    n = params.get("required_approving_review_count") if isinstance(params, dict) else None
    return n if isinstance(n, int) else 0


def _ruleset_requires(slug: str, ref: str) -> bool | None:
    try:
        rules = gh_api(f"repos/{slug}/rules/branches/{ref}")
    except GhUnavailable:
        return None
    if not isinstance(rules, list):
        return None
    return any(
        isinstance(r, dict) and r.get("type") == "pull_request"
        and _approvals(r.get("parameters")) >= 1
        for r in rules
    )


def _protection_requires(slug: str, ref: str) -> bool | None:
    try:
        protection = gh_api(f"repos/{slug}/branches/{ref}/protection")
    except GhUnavailable as e:
        # "Branch not protected" is a clean no; any other failure is unknown.
        return False if "not protected" in e.reason.lower() else None
    if not isinstance(protection, dict):
        return None
    return _approvals(protection.get("required_pull_request_reviews")) >= 1


def review_required(slug: str) -> bool | None:
    """Whether the default branch requires an approving review.

    True when the effective branch rules (rulesets, including inherited ones)
    carry a ``pull_request`` rule, or classic protection sets
    ``required_approving_review_count``, of 1 or more. False when both were read
    and neither does. None when neither says True and one could not be read.
    The protection read is skipped once the rules say True.
    """
    try:
        repo = gh_api(f"repos/{slug}")
    except GhUnavailable:
        return None
    branch = repo.get("default_branch") if isinstance(repo, dict) else None
    if not isinstance(branch, str) or not branch:
        return None
    ref = quote(branch, safe="")
    by_ruleset = _ruleset_requires(slug, ref)
    if by_ruleset:
        return True
    by_protection = _protection_requires(slug, ref)
    if by_protection:
        return True
    if by_ruleset is None or by_protection is None:
        return None
    return False


def _flag(required: bool | None, share: float | None, total: int) -> bool | None:
    """True when review is required and ``share`` is under the threshold.

    False when review is not required; None when the requirement is unknown or
    fewer than ``MIN_SAMPLE`` merges were sampled (too few to call a bypass)."""
    if required is False:
        return False
    if required is None or share is None or total < MIN_SAMPLE:
        return None
    return share < HOLLOW_THRESHOLD


def _days_ago(stamp: Any, now: datetime) -> int | None:
    when = parse_time(stamp)
    return None if when is None else max(0, (now - when).days)


def summarize(prs: list[dict[str, Any]], required: bool | None,
              bots: BotClassifier | None = None,
              now: datetime | None = None) -> dict[str, Any]:
    """The available block from sampled pull requests (pure except bot probes)."""
    bots = bots or BotClassifier()
    now = now or datetime.now(timezone.utc)
    total = len(prs)
    reviewed = sum(_reviewed_by_other(pr) for pr in prs)
    approved = sum(_reviewed_by_other(pr, approving=True) for pr in prs)
    self_merged = sum(_self_merged(pr) for pr in prs)
    verdicts = [_has_bot_comment(pr, bots) for pr in prs]
    bot_share = None if None in verdicts else _share(sum(bool(v) for v in verdicts), total)
    reviewed_share = _share(reviewed, total)
    approved_share = _share(approved, total)
    ages = [d for d in (_days_ago(pr.get("mergedAt"), now) for pr in prs) if d is not None]
    return {
        "available": True,
        "merged_count": total,
        "oldest_merged_days_ago": max(ages) if ages else None,
        "reviewed_share": reviewed_share,
        "approved_share": approved_share,
        "bot_review_share": bot_share,
        "self_merged_share": _share(self_merged, total),
        "review_required": required,
        "hollow_required_review": _flag(required, reviewed_share, total),
        "required_approval_bypassed": _flag(required, approved_share, total),
    }


def _username(actor: Any) -> str | None:
    if isinstance(actor, dict):
        name = actor.get("username")
        if isinstance(name, str) and name:
            return name.lower()
    return None


def _rule_covers(rule: dict[str, Any], branch: str,
                 protected: Callable[[], bool | None]) -> bool | None:
    """Whether an approval rule applies to ``branch``; None when undecidable.

    ``protected`` answers whether ``branch`` is protected, for a rule scoped to
    all protected branches."""
    if rule.get("applies_to_all_protected_branches"):
        return protected()
    scopes = rule.get("protected_branches") or []
    if not isinstance(scopes, list):
        return None
    if not scopes:
        return True
    names = [b.get("name") for b in scopes if isinstance(b, dict)]
    if any(not isinstance(n, str) for n in names) or len(names) != len(scopes):
        return None
    return any(branch_pattern(n).match(branch) for n in names if isinstance(n, str))


def gitlab_review_required(project: GitlabProject) -> bool | None:
    """Whether the GitLab project's approval rules require an approval on the
    default branch (see the module docstring). None when unreadable or
    undecidable, never False on a refusal."""
    enc = project.encoded
    try:
        meta = glab_api(project, f"projects/{enc}")
        rules = glab_api(project, f"projects/{enc}/approval_rules?per_page=100")
    except GhUnavailable:
        return None
    branch = meta.get("default_branch") if isinstance(meta, dict) else None
    if not isinstance(branch, str) or not branch or not isinstance(rules, list):
        return None
    cache: list[bool | None] = []

    def protected() -> bool | None:
        # One protected-branch read per scan, however many rules ask.
        if not cache:
            try:
                cache.append(branch_protection(project, branch) is not None)
            except GhUnavailable:
                cache.append(None)
        return cache[0]

    unknown = False
    for rule in rules:
        if not isinstance(rule, dict):
            unknown = True
            continue
        if rule.get("rule_type") == "report_approver":
            continue
        need = rule.get("approvals_required")
        if not isinstance(need, int) or need < 1:
            continue
        covers = _rule_covers(rule, branch, protected)
        if covers:
            return True
        if covers is None:
            unknown = True
    return None if unknown else False


def _any_of(*verdicts: bool | None) -> bool | None:
    """True when any verdict is True, else None when any is unknown, else False."""
    if any(v is True for v in verdicts):
        return True
    return None if None in verdicts else False


def _approved_by_other(approvals: Any, author: str | None) -> bool:
    if not isinstance(approvals, dict):
        raise GhUnavailable("glab_bad_json: merge request approvals were not an object")
    for entry in approvals.get("approved_by") or []:
        user = _username(entry.get("user")) if isinstance(entry, dict) else None
        if user is not None and user != author:
            return True
    return False


def scan_gitlab_review_reality(project: GitlabProject, mrs: Sequence[dict[str, Any]],
                               now: datetime | None = None,
                               notes: NoteFactsOrReason = None) -> dict[str, Any]:
    """The ``review_reality`` block for a GitLab project's sampled merge requests
    (see module docstring). ``notes``: one ``NoteFacts`` per merge request, in order
    (``gitlab_notes.gitlab_note_facts``); None reads no note evidence."""
    now = now or datetime.now(timezone.utc)
    enc = project.encoded
    if isinstance(notes, dict):
        return notes  # the notes read was refused: never "no reviews"
    facts: Sequence[NoteFacts | None] = (
        notes if notes is not None and len(notes) == len(mrs) else [None] * len(mrs))
    reviewed: list[bool | None] = []
    try:
        approved = self_merged = 0
        ages: list[int] = []
        for mr in mrs:
            iid = mr.get("iid")
            if not isinstance(iid, int):
                return unavailable("glab_bad_json: a merged merge request has no iid")
            author = _username(mr.get("author"))
            merger = _username(mr.get("merge_user")) or _username(mr.get("merged_by"))
            approvals = glab_api(project, f"projects/{enc}/merge_requests/{iid}/approvals")
            by_other = _approved_by_other(approvals, author)
            approved += by_other
            fact = facts[len(reviewed)] if len(reviewed) < len(facts) else None
            reviewed.append(_any_of(by_other, fact.human_comment, fact.ai_review)
                            if fact is not None else (True if by_other else None))
            self_merged += author is not None and author == merger
            age = _days_ago(mr.get("merged_at"), now)
            if age is not None:
                ages.append(age)
    except GhUnavailable as e:
        return unavailable(e.reason)
    total = len(mrs)
    required = gitlab_review_required(project)
    approved_share = _share(approved, total)
    reviewed_share = None if None in reviewed else _share(sum(map(bool, reviewed)), total)
    bots = [f.ai_review if f is not None else None for f in facts]
    bypassed = _flag(required, approved_share, total)
    hollow = _flag(required, reviewed_share, total)
    if hollow is None and bypassed is False:
        hollow = False  # reviewed >= approved, so approvals alone can clear the hollow flag
    return {
        "available": True,
        "merged_count": total,
        "oldest_merged_days_ago": max(ages) if ages else None,
        "reviewed_share": reviewed_share,
        "approved_share": approved_share,
        "bot_review_share": None if None in bots else _share(sum(map(bool, bots)), total),
        "self_merged_share": _share(self_merged, total),
        "review_required": required,
        "hollow_required_review": hollow,
        "required_approval_bypassed": bypassed,
    }


def review_reality_from_sample(sample: SampleOrReason,
                               notes: NoteFactsOrReason = None) -> dict[str, Any]:
    """The ``review_reality`` registry entry: the block from the shared ``merged_sample``
    and, on GitLab, the ``gitlab_note_facts`` read from it.

    An unavailable sample is returned as the block, reason and all."""
    if not isinstance(sample, MergedSample):
        return sample
    changes = list(sample.changes)
    if sample.gitlab is not None:
        return scan_gitlab_review_reality(sample.gitlab, changes, notes=notes)
    if sample.github is None:
        return unavailable("no_remote: the sample names no repository")
    return summarize(changes, review_required(sample.github.slug), sample.bots)


def scan_review_reality(repo_root: Path, history_window: str | None = None,
                        run_date: str | None = None,
                        fleet_config: FleetConfig | None = None) -> dict[str, Any]:
    """Build the ``review_reality`` block for ``repo_root`` (see module docstring):
    draws the sample and its GitLab note facts, then :func:`review_reality_from_sample`."""
    today = run_date or datetime.now(timezone.utc).date().isoformat()
    sample = merged_sample(repo_root, history_window, today)
    return review_reality_from_sample(sample, gitlab_note_facts(sample, fleet_config))
