"""Tests for the merged-change census over an absolute period (lib/hosting/merged_census.py, #1668).

``gh`` and ``glab`` are stubbed at the process boundary (``run_cli`` in ``lib.hosting.gh_cli``
and ``lib.hosting.gitlab_cli``), as ``tests/test_merged_sample_paging.py`` does. Every fixture
is invented: the accounts are ``dev-one`` and ``dev-two`` (people), the repository
``example-org/widgets``. Nothing reaches the network.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import assess_signals
import lib.hosting.gh_cli as gh_cli
import lib.hosting.gitlab_cli as gitlab_cli
import lib.hosting.merged_census as mc
import lib.hosting.merged_sample as ms
from lib.counts_only.content_free import counts_only_scans
from lib.hosting.approval_integrity import COUNT_FIELDS
from lib.hosting.gh_cli import GithubRepo
from lib.hosting.gitlab_cli import GitlabProject
from lib.hosting.platform_cli import CliResult
from lib.run import signals

REPO = GithubRepo("example-org", "widgets")
PROJECT = GitlabProject("gitlab.example.com", "example-org/widgets")
PERIOD = mc.Period(date(2026, 7, 1), date(2026, 9, 30))
_END = datetime(2026, 9, 30, 20, tzinfo=timezone.utc)

DEPENDENT = ("merged_count", "unlinked_change_count", *COUNT_FIELDS)


def _stamp(i: int) -> str:
    """The i-th newest merge time (i = 0 is the newest), eight hours apart: 250 changes
    span about 83 days, inside the period."""
    return (_END - timedelta(hours=8 * i)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pr(i: int, *, linked: bool = True) -> dict[str, Any]:
    closing = [{"number": 5000 + i, "url": f"https://github.com/example-org/widgets/issues/{5000 + i}"}]
    return {"number": 900 + i, "title": f"Change title {i}", "body": "Refs #77",
            "mergedAt": _stamp(i), "createdAt": _stamp(i + 2),
            "author": {"login": "dev-one"}, "mergedBy": {"login": "dev-two"},
            "reviews": [{"author": {"login": "dev-two"}, "state": "APPROVED",
                         "submittedAt": _stamp(i + 1), "commit": {"oid": f"sha{i}"}}],
            "comments": [], "reviewDecision": "APPROVED", "headRefOid": f"sha{i}",
            "closingIssuesReferences": closing if linked else []}


def _bounds(span: str) -> tuple[str, str]:
    """A ``merged:<lo>..<hi>`` span as comparable strings; a bare date ``hi`` covers its day."""
    lo, _, hi = span.removeprefix("merged:").partition("..")
    return lo, hi + "T23:59:59Z" if len(hi) == 10 else hi


class FakeGh:
    """``gh pr list`` over ``prs``. A heavy read whose span reaches the oldest change gets
    ``fail_status`` when set; the listing caps at ``list_limit``, as GitHub search does."""

    def __init__(self, prs: list[dict[str, Any]], fail_last: str | None = None) -> None:
        self.prs = prs
        self.fail_last = fail_last
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], timeout: int, cache: bool = True) -> CliResult:
        self.calls.append(cmd)
        assert cache is False or cmd[1] != "pr", "the census is never cached"
        if cmd[1:3] == ["auth", "status"]:
            return CliResult(0, "", "")
        if cmd[1] == "api":  # a bot-account probe: every login here is a person
            return CliResult(1, "", "gh: Not Found (HTTP 404)\n")
        fields = cmd[cmd.index("--json") + 1]
        limit = int(cmd[cmd.index("--limit") + 1])
        lo, hi = _bounds(cmd[cmd.index("--search") + 1].split()[0])
        hits = [pr for pr in self.prs if lo <= pr["mergedAt"] <= hi]
        heavy = fields != "mergedAt"
        if heavy and self.fail_last and any(pr is self.prs[-1] for pr in hits):
            line = f"HTTP {self.fail_last}: Bad Gateway (https://api.github.com/graphql)"
            return CliResult(1, "", line + "\n")
        rows = [pr if heavy else {"mergedAt": pr["mergedAt"]} for pr in hits[:limit]]
        return CliResult(0, json.dumps(rows), "")

    def pr_list_calls(self) -> list[list[str]]:
        return [c for c in self.calls if c[1:3] == ["pr", "list"]]

    def heavy(self) -> list[list[str]]:
        return [c for c in self.pr_list_calls() if c[c.index("--json") + 1] != "mergedAt"]


def _github(monkeypatch: pytest.MonkeyPatch, fake: FakeGh,
            period: mc.Period | None = PERIOD) -> mc.CensusOrReason:
    monkeypatch.setattr(gh_cli, "run_cli", fake)
    monkeypatch.setattr(mc, "open_gitlab", lambda root: None)
    monkeypatch.setattr(mc, "git_remote_url", lambda root: "https://github.com/example-org/widgets")
    monkeypatch.setattr(mc, "open_github", lambda root: REPO)
    return mc.merged_census(Path("."), period)


def _block(census: mc.CensusOrReason) -> dict[str, Any]:
    return dict(mc.change_census(census, None))


# ---- the period ---------------------------------------------------------------------

def test_a_period_parses_both_days_inclusive() -> None:
    period = mc.parse_period("2026-07-01..2026-09-30")
    assert period == PERIOD and period.label == "2026-07-01..2026-09-30"
    assert mc.parse_period("2026-07-01..2026-07-01").start == date(2026, 7, 1)


@pytest.mark.parametrize("text", ["2026-07-01", "2026-09-30..2026-07-01", "2026-07-01..soon",
                                  "6mo", "2026-07-01...2026-09-30", ""])
def test_a_malformed_or_reversed_period_is_refused(text: str) -> None:
    with pytest.raises(ValueError):
        mc.parse_period(text)


# ---- GitHub -------------------------------------------------------------------------

def test_a_census_over_250_changes_reads_every_one_in_date_span_pages(monkeypatch) -> None:
    monkeypatch.setattr(mc, "LIST_LIMIT", 100)  # 250 changes do not fit one listing
    fake = FakeGh([_pr(i) for i in range(250)])
    census = _github(monkeypatch, fake)
    assert isinstance(census, mc.MergedCensus) and census.complete
    assert len({pr["number"] for pr in census.changes}) == 250
    listings = [c for c in fake.pr_list_calls() if c not in fake.heavy()]
    assert len(listings) > 1  # the period was split into date spans
    assert all(len(c) for c in fake.heavy())
    assert len(fake.heavy()) == -(-250 // mc.PAGE_SIZE)
    block = _block(census)
    assert block["available"] is True and block["population"] == "census"
    assert block["population_complete"] is True and "incomplete_reason" not in block
    assert block["merged_count"] == 250 and block["unlinked_change_count"] == 0
    assert block["independent_approval_count"] == 250
    assert block["period_start"] == "2026-07-01" and block["period_end"] == "2026-09-30"
    assert block["census_calls"] == census.calls <= mc.CENSUS_CALL_CEILING


def test_a_502_on_the_last_page_after_one_retry_is_truncated(monkeypatch) -> None:
    fake = FakeGh([_pr(i) for i in range(250)], fail_last="502")
    block = _block(_github(monkeypatch, fake))
    assert block["available"] is True
    assert block["population_complete"] is False and block["incomplete_reason"] == "truncated"
    assert all(block[name] is None for name in DEPENDENT)
    limits = [int(c[c.index("--limit") + 1]) for c in fake.heavy()]
    assert limits[-2:] == [mc.PAGE_SIZE, mc.PAGE_SIZE // 2]  # retried once at half, no more


def test_another_refusal_names_its_code_and_nulls_every_count(monkeypatch) -> None:
    fake = FakeGh([_pr(i) for i in range(30)], fail_last="500")
    block = _block(_github(monkeypatch, fake))
    assert block["population_complete"] is False and block["incomplete_reason"] == "gh_error"
    assert all(block[name] is None for name in DEPENDENT)


def test_a_single_day_still_over_the_listing_cap_is_truncated(monkeypatch) -> None:
    monkeypatch.setattr(mc, "LIST_LIMIT", 5)
    same_day = [{**_pr(i), "mergedAt": f"2026-08-01T0{i}:00:00Z"} for i in range(6)]
    block = _block(_github(monkeypatch, FakeGh(same_day)))
    assert block["population_complete"] is False and block["incomplete_reason"] == "truncated"
    assert block["merged_count"] is None


def test_the_census_stops_at_its_call_ceiling(monkeypatch) -> None:
    monkeypatch.setattr(mc, "CENSUS_CALL_CEILING", 4)
    fake = FakeGh([_pr(i) for i in range(250)])
    census = _github(monkeypatch, fake)
    assert isinstance(census, mc.MergedCensus)
    assert len(fake.pr_list_calls()) == 4 == census.calls
    block = _block(census)
    assert block["population_complete"] is False and block["incomplete_reason"] == "truncated"
    assert block["census_call_ceiling"] == 4
    assert all(block[name] is None for name in DEPENDENT)


def test_the_documented_ceiling_covers_a_full_listing_and_its_pages() -> None:
    assert mc.CENSUS_CALL_CEILING >= 1 + -(-mc.LIST_LIMIT // mc.PAGE_SIZE)
    assert str(mc.CENSUS_CALL_CEILING) in (mc.__doc__ or "")


def test_a_closing_reference_links_a_change_and_a_body_mention_does_not(monkeypatch) -> None:
    prs = [_pr(0), _pr(1, linked=False), _pr(2)]
    assert "#" in prs[1]["body"]  # mentions an issue, closes none
    block = _block(_github(monkeypatch, FakeGh(prs)))
    assert block["merged_count"] == 3 and block["unlinked_change_count"] == 1


def test_an_unreadable_closing_reference_makes_only_the_link_count_null(monkeypatch) -> None:
    prs = [_pr(0), {**_pr(1), "closingIssuesReferences": None}]
    block = _block(_github(monkeypatch, FakeGh(prs)))
    assert block["population_complete"] is True and block["merged_count"] == 2
    assert block["unlinked_change_count"] is None


def test_changes_merged_outside_the_period_are_not_read(monkeypatch) -> None:
    prs = [_pr(0), {**_pr(1), "mergedAt": "2026-10-01T00:00:01Z"},
           {**_pr(2), "mergedAt": "2026-06-30T23:59:59Z"}]
    block = _block(_github(monkeypatch, FakeGh(prs)))
    assert block["merged_count"] == 1


def test_an_empty_period_is_a_complete_census_of_nothing(monkeypatch) -> None:
    fake = FakeGh([])
    block = _block(_github(monkeypatch, fake))
    assert block["population_complete"] is True and block["merged_count"] == 0
    assert fake.heavy() == []


def test_no_period_makes_no_call(monkeypatch) -> None:
    fake = FakeGh([_pr(i) for i in range(5)])
    got = _github(monkeypatch, fake, period=None)
    assert fake.calls == []
    assert isinstance(got, dict) and got["reason"].startswith("not_requested")


def test_no_remote_is_unavailable_not_incomplete(monkeypatch) -> None:
    monkeypatch.setattr(mc, "git_remote_url", lambda root: None)
    got = mc.merged_census(Path("."), PERIOD)
    assert isinstance(got, dict) and got["available"] is False
    assert got["reason"].startswith("no_remote")
    assert _block(got) == got


def test_offline_census_reads_network_or_not_requested(monkeypatch) -> None:
    monkeypatch.setattr(mc, "git_remote_url", lambda root: "https://github.com/example-org/widgets")
    assert mc.offline_merged_census(Path("."), PERIOD)["reason"].startswith("network")
    assert mc.offline_merged_census(Path("."), None)["reason"].startswith("not_requested")


# ---- GitLab -------------------------------------------------------------------------

def _mr(i: int, *, linked: bool = True, merged_at: str | None = None) -> dict[str, Any]:
    return {"iid": 40 + i, "title": f"MR title {i}", "description": "See #77",
            "merged_at": merged_at or _stamp(i), "author": {"username": "dev-one"},
            "merge_user": {"username": "dev-two"}, "linked": linked}


class FakeGlab:
    def __init__(self, mrs: list[dict[str, Any]], refuse: str = "") -> None:
        self.mrs = mrs
        self.refuse = refuse
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], timeout: int, cache: bool = True) -> CliResult:
        self.calls.append(cmd)
        assert cache is False, "the census is never cached"
        path = cmd[-1]
        if self.refuse and self.refuse in path:
            return CliResult(1, "", "glab: 403 Forbidden (HTTP 403)\n")
        url = urlsplit(path)
        query = parse_qs(url.query)
        by_iid = {mr["iid"]: mr for mr in self.mrs}
        if url.path.endswith("/approvals"):
            return CliResult(0, json.dumps({"approved_by": [
                {"user": {"username": "dev-two"}, "approved_at": "2026-07-01T00:00:00Z"}]}), "")
        if url.path.endswith("/closes_issues"):
            iid = int(url.path.split("/")[-2])
            return CliResult(0, json.dumps([{"iid": 3}] if by_iid[iid]["linked"] else []), "")
        size, page = int(query["per_page"][0]), int(query["page"][0])
        rows = [{k: v for k, v in mr.items() if k != "linked"} for mr in self.mrs]
        return CliResult(0, json.dumps(rows[(page - 1) * size:page * size]), "")


def _gitlab(monkeypatch: pytest.MonkeyPatch, fake: FakeGlab) -> mc.CensusOrReason:
    monkeypatch.setattr(gitlab_cli, "run_cli", fake)
    monkeypatch.setattr(mc, "git_remote_url", lambda root: "https://gitlab.example.com/example-org/widgets")
    monkeypatch.setattr(mc, "open_gitlab", lambda root: PROJECT)
    return mc.merged_census(Path("."), PERIOD)


def test_gitlab_census_reads_every_page_and_links_by_closes_issues(monkeypatch) -> None:
    mrs = [_mr(i, linked=i % 5 != 0) for i in range(250)]
    mrs.append(_mr(999, merged_at="2026-10-02T00:00:00Z"))  # merged after the period
    fake = FakeGlab(mrs)
    census = _gitlab(monkeypatch, fake)
    assert isinstance(census, mc.MergedCensus) and census.complete
    block = _block(census)
    assert block["provider"] == "gitlab" and block["population_complete"] is True
    assert block["merged_count"] == 250 and block["unlinked_change_count"] == 50
    assert block["independent_approval_count"] == 250
    assert block["stale_approval_count"] is None  # GitLab names no reviewed commit
    listed = [c for c in fake.calls if "merge_requests?state=merged" in c[-1]]
    assert len(listed) == -(-251 // mc.GITLAB_PER_PAGE) + (251 % mc.GITLAB_PER_PAGE == 0)
    assert all("updated_after=2026-07-01T00:00:00Z" in c[-1] for c in listed)


def test_gitlab_refused_approvals_read_is_no_access_and_nulls_counts(monkeypatch) -> None:
    block = _block(_gitlab(monkeypatch, FakeGlab([_mr(i) for i in range(3)], refuse="/approvals")))
    assert block["population_complete"] is False and block["incomplete_reason"] == "no_access"
    assert all(block[name] is None for name in DEPENDENT)


# ---- the runner ---------------------------------------------------------------------

def _runner(monkeypatch: pytest.MonkeyPatch, fake: FakeGh) -> None:
    monkeypatch.setattr(gh_cli, "run_cli", fake)
    for module in (mc, ms):
        monkeypatch.setattr(module, "open_gitlab", lambda root: None)
        monkeypatch.setattr(module, "git_remote_url",
                            lambda root: "https://github.com/example-org/widgets")
        monkeypatch.setattr(module, "open_github", lambda root: REPO)


def test_without_a_period_the_runner_makes_no_census_call(monkeypatch, tmp_path) -> None:
    fake = FakeGh([_pr(i) for i in range(5)])
    _runner(monkeypatch, fake)
    blocks = signals.collect(tmp_path, ["review_reality", "change_census"], platform=True)
    assert "change_census" not in blocks
    assert not any("closingIssuesReferences" in " ".join(c) for c in fake.calls)
    assert not any("merged:2026-07-01..2026-09-30" in " ".join(c) for c in fake.calls)


def test_a_period_run_reads_the_census_and_counts_only_keeps_no_identity(monkeypatch, tmp_path) -> None:
    fake = FakeGh([_pr(0), _pr(1, linked=False)])
    _runner(monkeypatch, fake)
    blocks = signals.collect(tmp_path, ["change_census"], platform=True, period=PERIOD)
    block = blocks["change_census"]
    assert block["merged_count"] == 2 and block["unlinked_change_count"] == 1
    text = json.dumps(counts_only_scans(blocks))
    for needle in ("dev-one", "dev-two", "Change title", "5000", "#77", "widgets", "example-org"):
        assert needle not in text
    kept = counts_only_scans(blocks)["change_census"]
    assert kept["population"] == "census" and kept["period_start"] == "2026-07-01"
    assert kept["merged_count"] == 2


def test_assess_signals_refuses_a_period_without_platform(tmp_path, capsys) -> None:
    out = tmp_path.parent / f"{tmp_path.name}-out.json"
    code = assess_signals.main([str(tmp_path), "--out", str(out), "--period", "2026-07-01..2026-09-30"])
    assert code == 2 and "--platform" in capsys.readouterr().err


def test_assess_signals_refuses_a_malformed_period(tmp_path, capsys) -> None:
    out = tmp_path.parent / f"{tmp_path.name}-out.json"
    code = assess_signals.main([str(tmp_path), "--out", str(out), "--platform", "--period", "6mo"])
    assert code == 2 and "--period" in capsys.readouterr().err
