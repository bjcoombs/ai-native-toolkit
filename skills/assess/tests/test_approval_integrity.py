"""Tests for lib/hosting/approval_integrity.py (#1664): the approval-integrity counts
the ``review_reality`` block carries beside ``approved_share``.

Every fixture is invented: the accounts are ``ann``, ``ben``, ``cat`` (people),
``lintbot`` and ``deploy-helper`` (bots) and ``mystery`` (an account whose bot status
cannot be learned).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from lib.agent_identity import AuthorMatcher, ConfirmedIdentities
from lib.counts_only.content_free import counts_only
from lib.fleet_config import parse_fleet_config
from lib.hosting.approval_integrity import (
    COUNT_FIELDS, github_counts, gitlab_counts,
)
from lib.hosting.gitlab_notes import classifier_from_config
from lib.hosting.merged_sample import BotClassifier
from lib.hosting.review_reality import summarize

HEAD = "c0ffee"
OLD = "facade"
MERGED = "2026-09-10T12:00:00Z"
BEFORE = "2026-09-09T12:00:00Z"
AFTER = "2026-09-11T12:00:00Z"


class FixedBots(BotClassifier):
    """A classifier whose probe answers from a table, never the network."""

    def __init__(self, answers: dict[str, bool | None] | None = None) -> None:
        super().__init__()
        self.answers = {"lintbot": True, "deploy-helper": True, "mystery": None,
                        **(answers or {})}

    def _probe(self, name: str) -> bool | None:
        return self.answers.get(name, False)


def _review(who: str, *, state: str = "APPROVED", commit: str | None = HEAD,
            at: str = BEFORE) -> dict[str, Any]:
    review: dict[str, Any] = {"author": {"login": who}, "state": state, "submittedAt": at}
    if commit is not None:
        review["commit"] = {"oid": commit}
    return review


def _pr(*reviews: dict[str, Any], author: str = "ann", merger: str = "ben",
        author_is_bot: bool | None = False) -> dict[str, Any]:
    actor: dict[str, Any] = {"login": author}
    if author_is_bot is not None:
        actor["is_bot"] = author_is_bot
    return {"author": actor, "mergedBy": {"login": merger}, "mergedAt": MERGED,
            "headRefOid": HEAD, "reviews": list(reviews), "comments": []}


def _counts(prs: list[dict[str, Any]], bots: BotClassifier | None = None,
            matcher: AuthorMatcher | None = None) -> dict[str, Any]:
    return dict(github_counts(prs, bots or FixedBots(), matcher or AuthorMatcher()))


# ---- GitHub: each count ---------------------------------------------------

def test_independent_approval_by_a_person_on_the_head_commit() -> None:
    assert _counts([_pr(_review("ben"))]) == {
        "independent_approval_count": 1, "no_independent_approval_count": 0,
        "bot_only_approval_count": 0, "stale_approval_count": 0,
        "approved_after_merge_count": 0, "self_merged_unapproved_count": 0,
        "bot_authored_count": 0,
    }


def test_bot_only_approval_is_not_independent() -> None:
    out = _counts([_pr(_review("lintbot")), _pr(_review("lintbot"), _review("deploy-helper"))])
    assert out["independent_approval_count"] == 0
    assert out["no_independent_approval_count"] == 2
    assert out["bot_only_approval_count"] == 2


def test_a_person_beside_a_bot_is_independent_not_bot_only() -> None:
    out = _counts([_pr(_review("lintbot"), _review("cat"))])
    assert out["independent_approval_count"] == 1
    assert out["bot_only_approval_count"] == 0


def test_the_authors_own_approval_and_other_states_never_qualify() -> None:
    prs = [_pr(_review("ann")), _pr(_review("ben", state="COMMENTED")),
           _pr(_review("ben", state="CHANGES_REQUESTED")), _pr()]
    out = _counts(prs)
    assert out["independent_approval_count"] == 0
    assert out["no_independent_approval_count"] == 4
    assert out["bot_only_approval_count"] == 0


def test_approval_on_an_older_commit_is_stale() -> None:
    prs = [_pr(_review("ben", commit=OLD)),
           _pr(_review("ben", commit=OLD), _review("cat", commit=HEAD)),  # one fresh: not stale
           _pr(_review("lintbot", commit=HEAD), _review("ben", commit=OLD))]  # the bot's does not qualify
    out = _counts(prs)
    assert out["stale_approval_count"] == 2


def test_stale_reads_null_when_a_commit_is_unknown() -> None:
    out = _counts([_pr(_review("ben", commit=None))])
    assert out["stale_approval_count"] is None
    no_head = _pr(_review("ben"))
    del no_head["headRefOid"]
    assert _counts([no_head])["stale_approval_count"] is None


def test_a_change_with_no_qualifying_approval_is_never_stale() -> None:
    out = _counts([_pr(_review("lintbot", commit=OLD)), _pr()])
    assert out["stale_approval_count"] == 0


def test_approval_after_merge_counts_on_the_earliest_qualifying_approval() -> None:
    prs = [_pr(_review("ben", at=AFTER)),
           _pr(_review("ben", at=AFTER), _review("cat", at=BEFORE)),  # earliest before: no
           _pr(_review("lintbot", at=BEFORE), _review("ben", at=AFTER))]  # bot's does not qualify
    out = _counts(prs)
    assert out["approved_after_merge_count"] == 2


def test_approval_after_merge_reads_null_on_an_unreadable_time() -> None:
    assert _counts([_pr(_review("ben", at="not a time"))])["approved_after_merge_count"] is None


def test_a_known_early_approval_settles_after_merge_beside_an_unreadable_time() -> None:
    pr = _pr(_review("ben", at="not a time"), _review("cat", at=BEFORE))
    assert _counts([pr])["approved_after_merge_count"] == 0


def test_approvers_past_the_shared_probe_cap_read_null() -> None:
    """The comment pass and the approvers share ``MAX_LOGIN_PROBES``: once it is spent, an
    approver never classified before is unknown, and the counts it decides read null."""
    import lib.hosting.merged_sample as ms

    probed: list[str] = []

    class CountingBots(BotClassifier):
        def _probe(self, name: str) -> bool | None:
            if self._probes >= ms.MAX_LOGIN_PROBES:
                return None
            self._probes += 1
            probed.append(name)
            return False

    bots = CountingBots()
    for i in range(ms.MAX_LOGIN_PROBES):  # the comment pass spends the budget first
        assert bots.is_bot({"login": f"commenter{i}"}) is False
    out = _counts([_pr(_review("ben"))], bots=bots)
    assert len(probed) == ms.MAX_LOGIN_PROBES
    assert out["independent_approval_count"] is None
    assert out["bot_only_approval_count"] is None
    # An approver the comment pass already classified costs no probe and stays known.
    assert _counts([_pr(_review("commenter0"))], bots=bots)["independent_approval_count"] == 1


def test_self_merged_unapproved() -> None:
    prs = [_pr(merger="ann"), _pr(_review("lintbot"), merger="ann"),
           _pr(_review("ben"), merger="ann"), _pr(merger="ben")]
    out = _counts(prs)
    assert out["self_merged_unapproved_count"] == 2


def test_bot_authored_count() -> None:
    prs = [_pr(author="deploy-helper", author_is_bot=True), _pr(author="ann")]
    assert _counts(prs)["bot_authored_count"] == 1


def test_github_actions_approval_is_a_bot() -> None:
    out = _counts([_pr(_review("github-actions"))])
    assert out["bot_only_approval_count"] == 1
    assert out["independent_approval_count"] == 0


# ---- unknown bot status reads null, never a false count ---------------------

def test_unknown_reviewer_makes_the_approval_counts_null() -> None:
    out = _counts([_pr(_review("mystery")), _pr(_review("ben"))])
    for key in ("independent_approval_count", "no_independent_approval_count",
                "bot_only_approval_count"):
        assert out[key] is None, key
    # Fresh and before the merge: whoever the unknown account is, neither count moves.
    assert out["stale_approval_count"] == 0
    assert out["approved_after_merge_count"] == 0
    late = _counts([_pr(_review("mystery", commit=OLD, at=AFTER))])
    assert late["stale_approval_count"] is None
    assert late["approved_after_merge_count"] is None
    assert out["bot_authored_count"] == 0


def test_unknown_reviewer_beside_a_person_still_decides_independence() -> None:
    out = _counts([_pr(_review("mystery"), _review("ben", commit=HEAD, at=BEFORE))])
    assert out["independent_approval_count"] == 1
    assert out["bot_only_approval_count"] == 0
    # A known-fresh, known-early approval settles both, whatever the unknown one is.
    assert out["stale_approval_count"] == 0
    assert out["approved_after_merge_count"] == 0


def test_unknown_reviewer_beside_a_stale_person_leaves_stale_null() -> None:
    out = _counts([_pr(_review("mystery"), _review("ben", commit=OLD, at=AFTER))])
    assert out["stale_approval_count"] is None
    assert out["approved_after_merge_count"] is None


def test_unknown_reviewers_alone_settle_false_when_every_one_is_fresh_and_early() -> None:
    out = _counts([_pr(_review("mystery"), _review("enigma"))],
                  bots=FixedBots({"enigma": None}))
    assert out["independent_approval_count"] is None
    assert out["stale_approval_count"] == 0
    assert out["approved_after_merge_count"] == 0


def test_one_late_or_stale_unknown_reviewer_keeps_the_count_null() -> None:
    """An early fresh unknown could be a bot, leaving the late stale one as the person's."""
    pr = _pr(_review("mystery"), _review("enigma", commit=OLD, at=AFTER))
    out = _counts([pr], bots=FixedBots({"enigma": None}))
    assert out["stale_approval_count"] is None
    assert out["approved_after_merge_count"] is None


def test_unknown_reviewer_on_a_self_merge_leaves_that_count_null() -> None:
    out = _counts([_pr(_review("mystery"), merger="ann")])
    assert out["self_merged_unapproved_count"] is None


def test_unknown_author_makes_bot_authored_null() -> None:
    out = _counts([_pr(author="mystery", author_is_bot=None)])
    assert out["bot_authored_count"] is None


def test_deleted_reviewer_is_skipped_as_approved_share_skips_it() -> None:
    pr = _pr({"author": None, "state": "APPROVED", "submittedAt": BEFORE})
    out = _counts([pr])
    assert out["independent_approval_count"] == 0
    assert out["bot_only_approval_count"] == 0


# ---- [identities] decides ahead of the probe --------------------------------

def test_identities_bots_and_people_override_the_probe() -> None:
    matcher = AuthorMatcher(confirmed=ConfirmedIdentities(
        bots=frozenset({"reviewer-app[bot]"}), people=frozenset({"mystery"})))
    prs = [_pr(_review("reviewer-app")), _pr(_review("mystery"))]
    out = _counts(prs, matcher=matcher)
    assert out["bot_only_approval_count"] == 1
    assert out["independent_approval_count"] == 1


def test_a_confirmed_agent_approval_is_not_independent() -> None:
    matcher = AuthorMatcher(confirmed=ConfirmedIdentities(agents=frozenset({"cat"})))
    out = _counts([_pr(_review("cat"))], matcher=matcher)
    assert out["independent_approval_count"] == 0
    assert out["bot_only_approval_count"] == 1


# ---- the block: existing fields unchanged ------------------------------------

def test_summarize_keeps_approved_share_with_bots_and_adds_the_counts() -> None:
    prs = [_pr(_review("lintbot")), _pr(_review("ben")), _pr(), _pr(merger="ann")]
    block = summarize(prs, True, FixedBots(), matcher=AuthorMatcher())
    assert block["approved_share"] == 0.5  # a bot's approval still counts here (#1660 question 4)
    assert block["independent_approval_count"] == 1
    assert block["no_independent_approval_count"] == 3
    assert block["bot_only_approval_count"] == 1
    assert block["self_merged_unapproved_count"] == 1
    assert set(COUNT_FIELDS) <= set(block)


def test_empty_sample_reads_zero_counts() -> None:
    assert set(_counts([]).values()) == {0}


def test_counts_survive_counts_only() -> None:
    block = {"available": True, "merged_count": 3,
             **{k: i for i, k in enumerate(COUNT_FIELDS)}}
    out = counts_only({"review_reality": block})
    assert out["review_reality"] == block


# ---- GitLab -------------------------------------------------------------------

def _mr(author: str = "ann", merger: str = "ben") -> dict[str, Any]:
    return {"iid": 1, "author": {"username": author}, "merge_user": {"username": merger},
            "merged_at": MERGED}


def _approvals(*entries: tuple[str, str | None]) -> dict[str, Any]:
    out = []
    for who, at in entries:
        entry: dict[str, Any] = {"user": {"username": who}}
        if at is not None:
            entry["approved_at"] = at
        out.append(entry)
    return {"approved_by": out}


def test_gitlab_counts_people_bots_and_stale_null() -> None:
    config = parse_fleet_config('[identities]\nbots = ["lintbot"]\n')
    classifier = classifier_from_config(config)
    mrs = [_mr(), _mr(), _mr(merger="ann"), _mr(author="project_7_bot_0a1b2c")]
    approvals = [_approvals(("ben", BEFORE)), _approvals(("lintbot", BEFORE)),
                 _approvals(), _approvals(("cat", AFTER))]
    out = dict(gitlab_counts(mrs, approvals, classifier))
    assert out == {
        "independent_approval_count": 2, "no_independent_approval_count": 2,
        "bot_only_approval_count": 1, "stale_approval_count": None,
        "approved_after_merge_count": 1, "self_merged_unapproved_count": 1,
        "bot_authored_count": 1,
    }


def test_gitlab_approval_time_missing_reads_null() -> None:
    classifier = classifier_from_config(None)
    out = gitlab_counts([_mr()], [_approvals(("ben", None))], classifier)
    assert out["approved_after_merge_count"] is None
    assert out["independent_approval_count"] == 1
