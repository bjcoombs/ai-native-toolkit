"""Tests for lib/hosting/delivery.py and lib/hosting/merged_sample.py (issue #715).

The GitHub figures are computed over ``fixtures/platform/github_merged_prs.json``,
a recorded ``gh pr list --json`` response from a real repository with every
title and comment body replaced and the maintainer's login renamed. Bot probes
are answered by a stub classifier, and the sample's listing calls are replaced
in ``lib.hosting.merged_sample``, so nothing reaches the network.
"""
from __future__ import annotations

import copy
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, get_type_hints

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import lib.hosting.merged_sample as ms  # noqa: E402
from lib.hosting.delivery import (  # noqa: E402
    AI_REVIEWERS, DeliveryBlock, agent_authored, ai_reviewed, first_human_comment, percentile,
    scan_delivery,
)
from lib.counts_only.content_free import counts_only_scans  # noqa: E402
from lib.fleet_config import parse_fleet_config  # noqa: E402
from lib.hosting.gh_cli import GhUnavailable, GithubRepo  # noqa: E402
from lib.hosting.gitlab_cli import GitlabProject  # noqa: E402
from lib.hosting.gitlab_notes import NoteFacts  # noqa: E402
from lib.hosting.merged_sample import BotClassifier, MergedSample  # noqa: E402

RECORDED = Path(__file__).resolve().parent / "fixtures" / "platform" / "github_merged_prs.json"
REPO = GithubRepo("acme", "widget")
BOTS = frozenset({"claude", "coderabbitai", "codecov"})
REVIEW = {"available": True, "approved_share": 0.5, "self_merged_share": 1.0}


class StubBots(BotClassifier):
    """Answers the ``users/<login>[bot]`` probe from ``BOTS``; ``unknown`` logins are None."""

    def __init__(self, unknown: frozenset[str] = frozenset()) -> None:
        super().__init__()
        self.unknown = unknown
        self.probed: list[str] = []

    def _probe(self, name: str) -> bool | None:
        self.probed.append(name)
        return None if name in self.unknown else name in BOTS


def recorded() -> list[dict[str, Any]]:
    return json.loads(RECORDED.read_text(encoding="utf-8"))


def sample(prs: list[dict[str, Any]], bots: BotClassifier | None = None) -> MergedSample:
    return MergedSample("github", tuple(prs), date(2026, 5, 21), github=REPO,
                        bots=bots or StubBots(), page_size=ms.PAGE_SIZE if prs else None)


def _pr(created: str = "2026-11-01T00:00:00Z", merged: str = "2026-11-01T10:00:00Z",
        comments: tuple[tuple[str, str], ...] = (), reviews: tuple[tuple[str, str], ...] = ()) -> dict[str, Any]:
    return {
        "author": {"login": "alice", "is_bot": False}, "mergedBy": {"login": "alice"},
        "createdAt": created, "mergedAt": merged,
        "comments": [{"author": {"login": who}, "createdAt": at, "body": "SECRET"} for who, at in comments],
        "reviews": [{"author": {"login": who}, "submittedAt": at, "state": "COMMENTED"}
                    for who, at in reviews],
    }


# ---- percentiles ---------------------------------------------------------------

def test_percentile_is_nearest_rank():
    assert percentile([], 0.5) is None
    assert percentile([5.0], 0.9) == 5.0
    values = [4.0, 1.0, 3.0, 2.0]
    assert percentile(values, 0.5) == 2.0
    assert percentile(values, 0.9) == 4.0


# ---- the recorded GitHub response ----------------------------------------------

def test_recorded_sample_gives_every_github_figure():
    bots = StubBots()
    block = scan_delivery(sample(recorded(), bots), REVIEW)
    assert block == {
        "available": True, "merged_count": 6,
        "time_to_merge_hours_p50": 10.73, "time_to_merge_hours_p90": 12.75,
        # Only the author, bots and github-actions comment in the recording.
        "first_human_comment_hours_p50": None, "first_human_comment_hours_p90": None,
        "human_commented_share": 0.0,
        "ai_review_share": 1.0,
        "approved_share": 0.5, "self_merged_share": 1.0,
        "agent_authored_share": 0.0,
        "sample_page_size": 20,
        "unconfirmed_ai_accounts": 0,
    }
    assert "github-actions" not in bots.probed  # a status bot is never probed
    assert len(bots.probed) == len(set(bots.probed))  # one probe per login


def test_a_human_reviewer_dates_the_first_human_comment():
    prs = recorded()
    first = copy.deepcopy(prs[0])
    first["reviews"].append({"author": {"login": "bob"}, "submittedAt": "2026-11-20T23:27:07Z",
                             "state": "APPROVED"})
    block = scan_delivery(sample([first, *prs[1:]]), REVIEW)
    assert block["human_commented_share"] == round(1 / 6, 3)
    assert block["first_human_comment_hours_p50"] == 1.0
    assert block["first_human_comment_hours_p90"] == 1.0


def test_the_block_carries_no_login_title_or_body():
    text = json.dumps(scan_delivery(sample(recorded()), REVIEW)).lower()
    for word in ("maintainer", "claude", "coderabbit", "redacted", "secret"):
        assert word not in text


# ---- first human comment -------------------------------------------------------

def test_first_human_comment_skips_author_status_bots_and_bots():
    pr = _pr(comments=(("alice", "2026-11-01T01:00:00Z"), ("github-actions", "2026-11-01T02:00:00Z"),
                       ("codecov", "2026-11-01T03:00:00Z"), ("bob", "2026-11-01T05:30:00Z")),
             reviews=(("carol", "2026-11-01T04:00:00Z"),))
    assert first_human_comment(pr, StubBots()) == 4.0


def test_an_unknown_account_before_any_human_withholds_the_comment_figures():
    pr = _pr(comments=(("mystery", "2026-11-01T01:00:00Z"), ("bob", "2026-11-01T02:00:00Z")))
    block = scan_delivery(sample([pr, _pr()], StubBots(frozenset({"mystery"}))), REVIEW)
    assert block["first_human_comment_hours_p50"] is None
    assert block["human_commented_share"] is None
    assert block["time_to_merge_hours_p50"] == 10.0  # unaffected


def test_an_authorless_event_is_skipped_not_undecided():
    pr = _pr(comments=(("bob", "2026-11-01T02:00:00Z"),))
    pr["comments"].insert(0, {"author": None, "createdAt": "2026-11-01T01:00:00Z"})
    block = scan_delivery(sample([pr]), REVIEW)
    assert block["first_human_comment_hours_p50"] == 2.0
    assert block["human_commented_share"] == 1.0


def test_a_spent_probe_budget_withholds_the_comment_figures(monkeypatch):
    monkeypatch.setattr(ms, "MAX_LOGIN_PROBES", 0)
    pr = _pr(comments=(("mystery", "2026-11-01T01:00:00Z"),),
             reviews=(("carol", "2026-11-01T03:00:00Z"),))
    pr["reviews"][0]["author"]["is_bot"] = False  # typed: needs no probe
    block = scan_delivery(sample([pr, _pr()], BotClassifier()), REVIEW)
    assert block["first_human_comment_hours_p50"] is None
    assert block["first_human_comment_hours_p90"] is None
    assert block["human_commented_share"] is None
    assert block["time_to_merge_hours_p50"] == 10.0


def test_an_unknown_account_after_the_first_human_does_not_matter():
    pr = _pr(comments=(("bob", "2026-11-01T01:00:00Z"), ("mystery", "2026-11-01T02:00:00Z")))
    block = scan_delivery(sample([pr], StubBots(frozenset({"mystery"}))), REVIEW)
    assert block["first_human_comment_hours_p50"] == 1.0
    assert block["human_commented_share"] == 1.0


# ---- AI review -----------------------------------------------------------------

def test_status_bots_are_not_ai_review_nor_is_an_ai_login_the_classifier_reads_as_a_person():
    matcher = scan_matcher()
    assert ai_reviewed(_pr(comments=(("codecov", "2026-11-01T01:00:00Z"),)), StubBots(), matcher) is False
    person = _pr(comments=(("cursor", "2026-11-01T01:00:00Z"),))  # the stub reads it as a person
    assert "cursor" in AI_REVIEWERS
    assert ai_reviewed(person, StubBots(), matcher) is False


def test_pinned_agent_apps_and_config_rules_are_ai_review():
    matcher = scan_matcher()
    devin = _pr(reviews=(("devin-ai-integration", "2026-11-01T01:00:00Z"),))
    bots = StubBots()
    bots._known["devin-ai-integration"] = True
    assert ai_reviewed(devin, bots, matcher) is True  # AGENT_RULES, [bot] re-appended
    inhouse = _pr(comments=(("acme-reviewer", "2026-11-01T01:00:00Z"),))
    assert ai_reviewed(inhouse, StubBots(), matcher) is False
    configured = scan_matcher('[[ai_usage.agent_identities]]\npattern = "^acme-reviewer$"\n')
    assert ai_reviewed(inhouse, StubBots(), configured) is True


def test_an_agent_commenting_on_its_own_pull_request_is_not_ai_review():
    # gh names an app author "app/<name>" and the same app as a commenter "<name>".
    pr = _pr(comments=(("claude", "2026-11-01T01:00:00Z"),))
    pr["author"] = {"login": "app/claude", "is_bot": True}
    assert ai_reviewed(pr, StubBots(), scan_matcher()) is False
    pr["reviews"] = [{"author": {"login": "coderabbitai"}, "submittedAt": "2026-11-01T02:00:00Z"}]
    assert ai_reviewed(pr, StubBots(), scan_matcher()) is True


def test_an_ai_login_of_unknown_type_withholds_the_ai_share():
    pr = _pr(comments=(("claude", "2026-11-01T01:00:00Z"),))
    block = scan_delivery(sample([pr], StubBots(frozenset({"claude"}))), REVIEW)
    assert block["ai_review_share"] is None


def test_a_configured_agent_on_a_user_login_is_never_also_the_first_human():
    # acme-reviewer probes as a person; a config rule makes it an agent, so bob is
    # the first human and the same comment still counts as AI review.
    pr = _pr(comments=(("acme-reviewer", "2026-11-01T01:00:00Z"), ("bob", "2026-11-01T03:00:00Z")))
    configured = scan_matcher('[[ai_usage.agent_identities]]\npattern = "^acme-reviewer$"\n')
    assert first_human_comment(pr, StubBots(), configured) == 3.0
    assert first_human_comment(pr, StubBots()) == 1.0  # without the rule it reads as a person
    assert ai_reviewed(pr, StubBots(), configured) is True
    toml = '[ai_usage]\n[[ai_usage.agent_identities]]\npattern = "^acme-reviewer$"\n'
    block = scan_delivery(sample([pr]), REVIEW, parse_fleet_config(toml))
    assert block["first_human_comment_hours_p50"] == 3.0
    assert block["ai_review_share"] == 1.0


LOOSE = '[[ai_usage.agent_identities]]\npattern = "bot"\n'


def test_a_loose_config_rule_tries_the_bot_form_only_for_bots():
    # "bot" would match every "<login>[bot]"; a person is still a person.
    loose = scan_matcher(LOOSE)
    pr = _pr(comments=(("bob", "2026-11-01T02:00:00Z"),))
    assert first_human_comment(pr, StubBots(), loose) == 2.0
    assert ai_reviewed(pr, StubBots(), loose) is False
    bots = StubBots()
    bots._known["acme-ai"] = True  # a bot App: its [bot] form matches the rule
    assert ai_reviewed(_pr(comments=(("acme-ai", "2026-11-01T01:00:00Z"),)), bots, loose) is True
    assert not agent_authored(_authored({"login": "alice", "is_bot": False}), "github", loose)
    assert agent_authored(_authored({"login": "app/acme-ai", "is_bot": True}), "github", loose)


def scan_matcher(toml: str = ""):
    from lib.ai_usage import matcher_from_config
    return matcher_from_config(parse_fleet_config(toml))


# ---- agent-authored share ------------------------------------------------------

def _authored(author: dict[str, Any] | None) -> dict[str, Any]:
    pr = _pr()
    pr["author"] = author
    return pr


def test_a_pinned_agent_app_author_counts_only_when_the_author_is_a_bot():
    matcher = scan_matcher()
    assert agent_authored(_authored({"login": "app/claude", "is_bot": True}), "github", matcher)
    assert agent_authored(_authored({"login": "copilot-swe-agent", "is_bot": True}), "github", matcher)
    # a person whose login is an agent App's name is not that App
    assert not agent_authored(_authored({"login": "claude", "is_bot": False}), "github", matcher)
    assert not agent_authored(_authored({"login": "alice", "is_bot": False}), "github", matcher)
    # a dependency bot is never an agent
    assert not agent_authored(_authored({"login": "app/dependabot", "is_bot": True}), "github", matcher)
    assert not agent_authored(_authored(None), "github", matcher)  # deleted account


def test_a_config_rule_matches_an_author_by_username():
    configured = scan_matcher('[[ai_usage.agent_identities]]\npattern = "^inhouse-agent"\n')
    pr = _authored({"login": "inhouse-agent-7", "is_bot": False})
    assert not agent_authored(pr, "github", scan_matcher())
    assert agent_authored(pr, "github", configured)


def test_the_share_is_over_the_whole_sample():
    toml = '[ai_usage]\n[[ai_usage.agent_identities]]\npattern = "^inhouse-agent$"\n'
    prs = [_authored({"login": "app/devin-ai-integration", "is_bot": True}),
           _authored({"login": "inhouse-agent", "is_bot": False}),
           _pr(), _pr()]
    block = scan_delivery(sample(prs), REVIEW, parse_fleet_config(toml))
    assert block["agent_authored_share"] == 0.5
    assert scan_delivery(sample(prs), REVIEW)["agent_authored_share"] == 0.25
    text = json.dumps(block).lower()
    assert "inhouse" not in text and "devin" not in text


def test_gitlab_token_users_count_as_agents_only_when_promoted():
    mrs = ({"iid": 1, "author": {"username": "project_12_bot_4ffc9a"}},
           {"iid": 2, "author": {"username": "renovate-bot"}},
           {"iid": 3, "author": {"username": "Inhouse-Agent"}},
           {"iid": 4, "author": {"username": "alice"}})
    project = GitlabProject("gitlab.com", "grp/app")
    gl = MergedSample("gitlab", mrs, None, gitlab=project)
    assert scan_delivery(gl, REVIEW)["agent_authored_share"] == 0.0
    rules = '[[ai_usage.agent_identities]]\npattern = "^inhouse-agent$"\n'
    assert scan_delivery(gl, REVIEW, parse_fleet_config("[ai_usage]\n" + rules))["agent_authored_share"] == 0.25
    promoted = "[ai_usage]\ntoken_users_as_agents = true\n" + rules
    assert scan_delivery(gl, REVIEW, parse_fleet_config(promoted))["agent_authored_share"] == 0.5


# ---- unavailable and GitLab ----------------------------------------------------

def test_an_unavailable_sample_is_the_block():
    refused = {"available": False, "reason": "no_access: HTTP 403"}
    assert scan_delivery(refused, refused) == refused


def test_an_empty_sample_is_available_with_null_figures():
    block = scan_delivery(sample([]), {"available": True, "approved_share": None,
                                       "self_merged_share": None})
    assert block["merged_count"] == 0
    assert block["unconfirmed_ai_accounts"] == 0
    assert all(block[k] is None for k in block
               if k not in ("available", "merged_count", "unconfirmed_ai_accounts"))


def test_unavailable_review_reality_copies_nothing():
    block = scan_delivery(sample([_pr()]), {"available": False, "reason": "x"})
    assert block["approved_share"] is None and block["self_merged_share"] is None


def test_gitlab_without_note_facts_gives_time_to_merge_only():
    mrs = ({"iid": 1, "created_at": "2026-11-01T00:00:00Z", "merged_at": "2026-11-01T06:00:00Z"},
           {"iid": 2, "created_at": "2026-11-01T00:00:00Z", "merged_at": "2026-11-02T00:00:00Z"})
    project = GitlabProject("gitlab.com", "grp/app")
    block = scan_delivery(MergedSample("gitlab", mrs, None, gitlab=project), REVIEW)
    assert block["time_to_merge_hours_p50"] == 6.0
    assert block["time_to_merge_hours_p90"] == 24.0
    assert block["first_human_comment_hours_p50"] is None
    assert block["ai_review_share"] is None
    assert block["self_merged_share"] == 1.0


# ---- the sample ----------------------------------------------------------------

def test_sample_since_counts_back_from_the_run_date():
    assert ms.sample_since("6mo", "2026-11-21") == date(2026, 5, 21)
    assert ms.sample_since("90d", "2026-11-21") == date(2026, 8, 23)
    assert ms.sample_since(None, "2026-11-21") is None


def _light(n: int) -> list[dict[str, Any]]:
    # Listed by update time, so merge order is scrambled.
    return [{"mergedAt": f"2026-10-{1 + (i * 7) % 28:02d}T{i % 24:02d}:00:00Z"} for i in range(n)]


def test_github_sample_takes_the_newest_merges_in_pages(monkeypatch):
    calls: list[list[str]] = []
    light = _light(40)
    newest = sorted((c["mergedAt"] for c in light), reverse=True)

    cached: list[bool] = []

    def fake(args: list[str], timeout: int = 20, cache: bool = True) -> Any:
        calls.append(args)
        cached.append(cache)
        if args[-1] == "mergedAt":
            return light
        span = args[args.index("--search") + 1].split()[0].removeprefix("merged:")
        lo, _, hi = span.removeprefix(">=").partition("..")
        return [{"mergedAt": m, "comments": []} for m in newest
                if m >= lo and (not hi or m <= hi)] + [
            {"mergedAt": "2026-05-01T00:00:00Z"}]  # before the window: dropped

    monkeypatch.setattr(ms, "gh_json", fake)
    got, size = ms.github_changes(REPO, date(2026, 5, 21))
    assert [c["mergedAt"] for c in got] == newest[:ms.SAMPLE_CAP]
    assert size == ms.PAGE_SIZE
    page = ms.PAGE_SIZE
    assert calls[0][calls[0].index("--search") + 1] == "merged:>=2026-05-21 sort:updated-desc"
    assert calls[1][calls[1].index("--search") + 1] == f"merged:>={newest[page - 1]} sort:updated-desc"
    assert calls[2][calls[2].index("--search") + 1] == (
        f"merged:{newest[ms.SAMPLE_CAP - 1]}..{newest[page]} sort:updated-desc")
    assert [c[c.index("--limit") + 1] for c in calls] == [str(ms.FETCH_LIMIT), str(page), str(page)]
    assert calls[1][-1] == calls[2][-1] == ms.PR_FIELDS
    # Only the timestamps-only read may be cached; reviews and comments never are (#778).
    assert cached == [True, False, False]
    assert all(c[c.index("--repo") + 1] == "acme/widget" for c in calls)


def test_github_sample_with_no_merges_reads_once(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(ms, "gh_json", lambda args, timeout=20, cache=True: calls.append(args) or [])
    assert ms.github_changes(REPO, None) == ([], None)
    assert len(calls) == 1
    assert calls[0][calls[0].index("--search") + 1] == "sort:updated-desc"


def test_gitlab_sample_reads_a_short_page_once_and_orders_by_merge_time(monkeypatch):
    paths: list[str] = []
    mrs = [{"iid": i, "merged_at": f"2026-10-{i:02d}T00:00:00Z"} for i in (3, 1, 2)]
    mrs.append({"iid": 9, "merged_at": "2026-01-01T00:00:00Z"})  # updated in, merged before

    def fake(project: GitlabProject, path: str, cache: bool = True) -> Any:
        assert cache is False  # titles and descriptions stay out of the cache (#778)
        paths.append(path)
        return mrs

    monkeypatch.setattr(ms, "glab_api", fake)
    got, size = ms.gitlab_changes(GitlabProject("gitlab.com", "grp/app"), date(2026, 5, 21))
    assert [mr["iid"] for mr in got] == [3, 2, 1]
    assert size == ms.PAGE_SIZE
    assert paths == ["projects/grp%2Fapp/merge_requests?state=merged&updated_after=2026-05-21T00:00:00Z"
                     f"&order_by=updated_at&sort=desc&per_page={ms.PAGE_SIZE}&page=1"]


@pytest.mark.parametrize("listing,reason", [
    ({"not": "a list"}, "gh_bad_json"),
    ([{"number": 1}], "gh_bad_json"),  # a merged change with no merge time
])
def test_a_malformed_github_listing_is_unavailable(monkeypatch, listing, reason):
    monkeypatch.setattr(ms, "gh_json", lambda args, timeout=20, cache=True: listing)
    with pytest.raises(GhUnavailable, match=reason):
        ms.github_changes(REPO, None)


def test_a_refused_listing_is_an_unavailable_sample(monkeypatch, tmp_path):
    def refuse(args: list[str], timeout: int = 20, cache: bool = True) -> Any:
        raise GhUnavailable("no_access: HTTP 403")

    monkeypatch.setattr(ms, "open_gitlab", lambda root: None)
    monkeypatch.setattr(ms, "open_github", lambda root: REPO)
    monkeypatch.setattr(ms, "gh_json", refuse)
    assert ms.merged_sample(tmp_path, "6mo", "2026-11-21") == {
        "available": False, "reason": "no_access: HTTP 403"}


# ---- counts-only (#769) ----------------------------------------------------------

def _full_github_block() -> dict[str, Any]:
    prs = recorded()
    first = copy.deepcopy(prs[0])
    first["reviews"].append({"author": {"login": "bob"}, "submittedAt": "2026-11-20T23:27:07Z",
                             "state": "APPROVED"})
    return dict(scan_delivery(sample([first, *prs[1:]]), REVIEW))


def _full_gitlab_block() -> dict[str, Any]:
    mrs = ({"iid": 1, "created_at": "2026-11-01T00:00:00Z", "merged_at": "2026-11-01T06:00:00Z"},
           {"iid": 2, "created_at": "2026-11-01T00:00:00Z", "merged_at": "2026-11-02T00:00:00Z"})
    notes = (NoteFacts(True, True, "2026-11-01T02:00:00Z"), NoteFacts(False, False, None))
    project = GitlabProject("gitlab.com", "grp/app")
    return dict(scan_delivery(MergedSample("gitlab", mrs, None, gitlab=project,
                                           page_size=ms.PAGE_SIZE), REVIEW, notes=notes))


@pytest.mark.parametrize("build", [_full_github_block, _full_gitlab_block], ids=["github", "gitlab"])
def test_counts_only_keeps_every_delivery_figure(build):
    block = build()
    assert set(block) == set(get_type_hints(DeliveryBlock))
    assert all(value is not None for value in block.values())  # every figure is exercised
    assert counts_only_scans({"delivery": block}) == {"delivery": block}
