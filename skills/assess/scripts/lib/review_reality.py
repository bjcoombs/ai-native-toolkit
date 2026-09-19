"""Review reality: whether merged pull requests were actually reviewed.

Layer 7 asks whether every change gets design-level feedback. A required-review
rule that every merge bypasses reads as Present and is hollow. This scan samples
the ``DEFAULT_LIMIT`` most recently opened merged pull requests through ``gh``
(``gh pr list`` orders by creation, not merge) and reports, as
counts and shares only, how many carried a review from someone other than the
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
whether a sampled pull request counts.

Bot classification. ``gh pr list`` gives a comment author a ``login`` only, with
a bot's ``[bot]`` suffix dropped. An author object that carries ``is_bot`` or
``type`` is classified from it; a login ending ``[bot]`` is a bot; any other
login is probed once with ``gh api users/<login>[bot]`` (a GitHub App's bot
account; ``[`` cannot appear in a person's login): an answer of type ``Bot``
is a bot, a 404 is a person, anything else leaves it unknown, as does a
comment with no author login. At most
``MAX_LOGIN_PROBES`` distinct logins are probed. ``github-actions`` comments are
status comments and never count; neither does anything in ``reviews``.

Privacy: no title, login or user name is written to the block - only counts,
shares and booleans. A failed pull-request read degrades the whole block to
``{"available": False, "reason"}`` (``no_access`` on HTTP 403). GitHub access
goes through ``gh_cli``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from lib.gh_cli import GhUnavailable, gh_api, gh_json, open_github, unavailable

# Merged pull requests sampled per run.
DEFAULT_LIMIT = 30

# Under this share of reviewed merges, a required-review rule is hollow.
HOLLOW_THRESHOLD = 0.2

# Fewer merged pull requests than this are too few to call a rule bypassed:
# both flags are null below it.
MIN_SAMPLE = 5

# Distinct unmarked comment logins probed for a bot account per run.
MAX_LOGIN_PROBES = 20

# Status-comment bot; its comments are not review.
_STATUS_BOTS = frozenset({"github-actions", "github-actions[bot]"})

# The issue's field list plus mergedAt (for the sample's age). reviewDecision is
# fetched because the issue names it but is not scored: it reflects the current
# rule, not whether anyone other than the author reviewed.
PR_FIELDS = "author,mergedBy,mergedAt,reviews,reviewDecision,comments"


def _login(actor: Any) -> str | None:
    if isinstance(actor, dict) and isinstance(actor.get("login"), str) and actor["login"]:
        return actor["login"].lower()
    return None


def _share(count: int, total: int) -> float | None:
    return round(count / total, 3) if total else None


class _BotClassifier:
    """Decides whether a comment author is a bot; caches one probe per login."""

    def __init__(self) -> None:
        self._known: dict[str, bool | None] = {}
        self._probes = 0

    def is_bot(self, actor: Any) -> bool | None:
        login = _login(actor)
        if login is None:
            # No author login (a deleted account, a malformed entry): unknown.
            return None
        if isinstance(actor.get("is_bot"), bool):
            return actor["is_bot"]
        if isinstance(actor.get("type"), str):
            return actor["type"] == "Bot"
        if login.endswith("[bot]"):
            return True
        if login not in self._known:
            self._known[login] = self._probe(login)
        return self._known[login]

    def _probe(self, login: str) -> bool | None:
        if self._probes >= MAX_LOGIN_PROBES:
            return None
        self._probes += 1
        try:
            user = gh_api(f"users/{quote(login + '[bot]', safe='')}")
        except GhUnavailable as e:
            return False if e.reason.startswith("not_found") else None
        return isinstance(user, dict) and user.get("type") == "Bot"


def _has_bot_comment(pr: dict, bots: _BotClassifier) -> bool | None:
    """True on a bot comment, False on none, None when an unknown author decides."""
    unknown = False
    for comment in pr.get("comments") or []:
        actor = comment.get("author") if isinstance(comment, dict) else None
        if _login(actor) in _STATUS_BOTS:
            continue
        verdict = bots.is_bot(actor)
        if verdict:
            return True
        if verdict is None:
            unknown = True
    return None if unknown else False


def _reviewed_by_other(pr: dict, approving: bool = False) -> bool:
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


def _self_merged(pr: dict) -> bool:
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
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0, (now - when).days)


def summarize(prs: list[dict], required: bool | None,
              bots: _BotClassifier | None = None,
              now: datetime | None = None) -> dict[str, Any]:
    """The available block from sampled pull requests (pure except bot probes)."""
    bots = bots or _BotClassifier()
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


def scan_review_reality(repo_root: Path, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    """Build the ``review_reality`` run-context block (see module docstring)."""
    try:
        repo = open_github(repo_root)
        got = gh_json(["pr", "list", "--repo", repo.slug, "--state", "merged",
                       "--limit", str(limit), "--json", PR_FIELDS])
    except GhUnavailable as e:
        return unavailable(e.reason)
    if not isinstance(got, list):
        return unavailable("gh_bad_json: `gh pr list` did not return a list")
    prs = [pr for pr in got if isinstance(pr, dict)][:limit]
    return summarize(prs, review_required(repo.slug))
