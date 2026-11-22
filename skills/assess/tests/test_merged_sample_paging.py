"""Tests for the paged merged-sample reads (lib/merged_sample.py, issue #995).

``gh`` and ``glab`` are stubbed at the process boundary (``run_cli`` in
``lib.gh_cli`` and ``lib.gitlab_cli``): the stub answers each call from the
arguments it was given and prints the CLI's own HTTP 502 or 504 line on stderr for
a page larger than it accepts, so the status parsing is exercised too. Nothing
reaches the network.
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

from lib import gh_cli, gitlab_cli  # noqa: E402
from lib import merged_sample as ms  # noqa: E402
from lib.delivery import scan_delivery  # noqa: E402
from lib.external_ci import CALLS_PER_CHANGE, read_external_ci  # noqa: E402
from lib.gh_cli import GhUnavailable, GithubRepo  # noqa: E402
from lib.gitlab_cli import GitlabProject  # noqa: E402
from lib.platform_cli import CliResult  # noqa: E402

REPO = GithubRepo("kubernetes", "kubernetes")
PROJECT = GitlabProject("gitlab.example.com", "estate/app")
SINCE = date(2026, 5, 21)
_BASE = datetime(2026, 11, 20, tzinfo=timezone.utc)


def _stamp(i: int) -> str:
    """The i-th newest merge time (i = 0 is the newest), one hour apart."""
    return (_BASE - timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pr(i: int) -> dict[str, Any]:
    return {"number": 1000 - i, "mergedAt": _stamp(i), "createdAt": _stamp(i + 2),
            "author": {"login": "alice"}, "mergedBy": {"login": "bob"},
            "reviews": [{"author": {"login": "bob"}, "state": "APPROVED",
                         "submittedAt": _stamp(i + 1)}],
            "comments": [], "reviewDecision": "APPROVED", "headRefOid": f"sha{i}"}


class FakeGh:
    """``gh pr list`` over ``n`` merged pull requests, listed by update time (here,
    merge order). A heavy read above ``max_page`` changes gets ``status``."""

    def __init__(self, n: int, max_page: int | None, status: str = "502") -> None:
        self.prs = [_pr(i) for i in range(n)]
        self.max_page = max_page
        self.status = status
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], timeout: int, cache: bool = True) -> CliResult:
        self.calls.append(cmd)
        if cmd[1:3] == ["auth", "status"]:
            return CliResult(0, "", "")
        if cmd[1] == "api" and cmd[2].endswith("/check-runs"):  # external_ci's first read
            return CliResult(0, json.dumps({"check_runs": [{"app": {"slug": "buildkite"}}]}), "")
        if cmd[1] == "api":  # a bot-account probe: every login here is a person
            return CliResult(1, "", "gh: Not Found (HTTP 404)\n")
        fields = cmd[cmd.index("--json") + 1]
        limit = int(cmd[cmd.index("--limit") + 1])
        heavy = fields != "mergedAt"
        if heavy and self.max_page is not None and limit > self.max_page:
            line = (f"HTTP {self.status}: {self.status} Bad Gateway"
                    " (https://api.github.com/graphql)")
            return CliResult(1, "", line + "\n")
        span = cmd[cmd.index("--search") + 1].split()[0].removeprefix("merged:")
        lo, _, hi = span.removeprefix(">=").partition("..")
        hits = [pr for pr in self.prs if pr["mergedAt"] >= lo and (not hi or pr["mergedAt"] <= hi)]
        rows = [pr if heavy else {"mergedAt": pr["mergedAt"]} for pr in hits[:limit]]
        return CliResult(0, json.dumps(rows), "")

    def heavy_limits(self) -> list[int]:
        return [int(c[c.index("--limit") + 1]) for c in self.calls
                if "--json" in c and c[c.index("--json") + 1] != "mergedAt"]


def _github(monkeypatch: pytest.MonkeyPatch, fake: FakeGh) -> ms.SampleOrReason:
    monkeypatch.setattr(gh_cli, "run_cli", fake)
    monkeypatch.setattr(ms, "open_gitlab", lambda root: None)
    monkeypatch.setattr(ms, "remote_kind", lambda url: "github")
    monkeypatch.setattr(ms, "git_remote_url", lambda root: "https://github.com/kubernetes/kubernetes")
    monkeypatch.setattr(ms, "open_github", lambda root: REPO)
    return ms.merged_sample(Path("."), "6mo", "2026-11-21")


def _in_ceiling(fake: FakeGh) -> bool:
    return len([c for c in fake.calls if c[1:3] == ["pr", "list"]]) <= ms.GITHUB_SAMPLE_CALLS


# ---- GitHub -------------------------------------------------------------------------

def test_a_stub_that_refuses_pages_above_25_gives_the_full_sample(monkeypatch):
    """The kubernetes/kubernetes case: 100 per page is a 502; 25 or fewer succeed."""
    fake = FakeGh(200, max_page=25)
    got = _github(monkeypatch, fake)
    assert isinstance(got, ms.MergedSample)
    assert [pr["mergedAt"] for pr in got.changes] == [_stamp(i) for i in range(ms.SAMPLE_CAP)]
    assert got.page_size == ms.PAGE_SIZE <= 25
    assert all(limit <= 25 for limit in fake.heavy_limits())
    assert _in_ceiling(fake)
    block = scan_delivery(got, {"available": True, "approved_share": 1.0,
                                "self_merged_share": 0.0})
    assert block["merged_count"] == ms.SAMPLE_CAP
    assert block["time_to_merge_hours_p50"] == 2.0  # measured, not unavailable
    assert block["sample_page_size"] == ms.PAGE_SIZE


def test_check_runs_per_change_stay_within_their_ceiling_after_a_retry(monkeypatch):
    """Paging changes the sample's listing calls, not the sample: ``external_ci`` reads
    at most ``CALLS_PER_CHANGE`` per sampled change on top of ``GITHUB_SAMPLE_CALLS``."""
    fake = FakeGh(200, max_page=ms.PAGE_SIZE // 2)
    got = _github(monkeypatch, fake)
    assert isinstance(got, ms.MergedSample) and len(got.changes) == ms.SAMPLE_CAP
    fake.calls.clear()
    reading = read_external_ci(got)
    assert reading.sampled == reading.external == ms.SAMPLE_CAP and reading.applies
    per_change = [c for c in fake.calls if c[1] == "api" and "/commits/" in c[2]]
    assert len(per_change) <= CALLS_PER_CHANGE * ms.SAMPLE_CAP
    assert len({c[2] for c in per_change}) == len(per_change)  # each head read once


@pytest.mark.parametrize("status", ["502", "504"])
def test_a_refused_page_is_read_again_once_at_half_the_size(monkeypatch, status):
    fake = FakeGh(200, max_page=ms.PAGE_SIZE // 2, status=status)
    got = _github(monkeypatch, fake)
    assert isinstance(got, ms.MergedSample)
    assert [pr["mergedAt"] for pr in got.changes] == [_stamp(i) for i in range(ms.SAMPLE_CAP)]
    assert got.page_size == ms.PAGE_SIZE // 2
    half = ms.PAGE_SIZE // 2
    assert fake.heavy_limits() == [ms.PAGE_SIZE] + [half] * -(-ms.SAMPLE_CAP // half)
    assert _in_ceiling(fake)
    assert len([c for c in fake.calls if c[1:3] == ["pr", "list"]]) == ms.GITHUB_SAMPLE_CALLS


def test_a_page_refused_at_half_the_size_is_the_reason(monkeypatch):
    fake = FakeGh(200, max_page=1)
    got = _github(monkeypatch, fake)
    assert isinstance(got, dict) and got["available"] is False
    assert got["reason"].startswith("gh_error") and "HTTP 502" in got["reason"]
    assert fake.heavy_limits() == [ms.PAGE_SIZE, ms.PAGE_SIZE // 2]  # one retry, no more


def test_another_refusal_is_not_retried(monkeypatch):
    fake = FakeGh(200, max_page=1, status="500")
    got = _github(monkeypatch, fake)
    assert isinstance(got, dict) and got["available"] is False
    assert fake.heavy_limits() == [ms.PAGE_SIZE]


@pytest.mark.parametrize("n", [3, ms.PAGE_SIZE, ms.SAMPLE_CAP, 200])
def test_paging_does_not_change_the_sample(monkeypatch, n):
    """The sample a single 100-change page read gives, at every size."""
    fake = FakeGh(n, max_page=None)
    got = _github(monkeypatch, fake)
    assert isinstance(got, ms.MergedSample)
    assert list(got.changes) == fake.prs[:ms.SAMPLE_CAP]
    assert _in_ceiling(fake)


def test_a_merge_time_tie_never_straddles_two_pages(monkeypatch):
    fake = FakeGh(ms.SAMPLE_CAP, max_page=None)
    # The first change of page two shares its merge time with the last two of page one.
    for i in (ms.PAGE_SIZE - 1, ms.PAGE_SIZE):
        fake.prs[i]["mergedAt"] = fake.prs[ms.PAGE_SIZE - 2]["mergedAt"]
    got = _github(monkeypatch, fake)
    assert isinstance(got, ms.MergedSample)
    numbers = [pr["number"] for pr in got.changes]
    assert len(numbers) == len(set(numbers)) == ms.SAMPLE_CAP
    spans = [c[c.index("--search") + 1].split()[0] for c in fake.calls[1:]]
    assert spans == [f"merged:>={_stamp(ms.PAGE_SIZE - 2)}",
                     f"merged:{_stamp(ms.SAMPLE_CAP - 1)}..{_stamp(ms.PAGE_SIZE + 1)}"]
    assert fake.heavy_limits() == [ms.PAGE_SIZE + 1, ms.PAGE_SIZE]
    assert _in_ceiling(fake)


def test_page_ends_never_add_a_page():
    light = [{"mergedAt": f"2026-11-01T00:00:{i // 3:02d}Z"} for i in range(ms.SAMPLE_CAP)]
    half = ms.PAGE_SIZE // 2
    ends, start = [], 0
    while start < len(light):
        start = ms._page_end(light, start, half)
        ends.append(start)
    assert len(ends) <= -(-len(light) // half)
    assert all(light[e - 1]["mergedAt"] != light[e]["mergedAt"] for e in ends[:-1])


def test_no_merges_makes_no_heavy_read(monkeypatch):
    fake = FakeGh(0, max_page=None)
    got = _github(monkeypatch, fake)
    assert isinstance(got, ms.MergedSample) and got.changes == () and got.page_size is None
    assert fake.heavy_limits() == []


# ---- GitLab -------------------------------------------------------------------------

def _mr(i: int) -> dict[str, Any]:
    return {"iid": 500 - i, "merged_at": _stamp(i), "created_at": _stamp(i + 3),
            "title": "t", "description": "d"}


class FakeGlab:
    """``glab api`` over ``n`` merged merge requests; a page above ``max_page`` gets
    ``status`` in glab's own form."""

    def __init__(self, n: int, max_page: int | None, status: str = "502") -> None:
        self.mrs = [_mr(i) for i in range(n)]
        self.max_page = max_page
        self.status = status
        self.paths: list[str] = []

    def __call__(self, cmd: list[str], timeout: int, cache: bool = True) -> CliResult:
        assert cache is False  # titles and descriptions stay out of the cache (#778)
        path = cmd[-1]
        self.paths.append(path)
        query = parse_qs(urlsplit(path).query)
        per_page, page = int(query["per_page"][0]), int(query["page"][0])
        if self.max_page is not None and per_page > self.max_page:
            return CliResult(1, "", f"glab: {self.status} Bad Gateway (HTTP {self.status})\n")
        rows = self.mrs[(page - 1) * per_page:page * per_page]
        return CliResult(0, json.dumps(rows), "")

    def sizes(self) -> list[int]:
        return [int(parse_qs(urlsplit(p).query)["per_page"][0]) for p in self.paths]


def _gitlab(monkeypatch: pytest.MonkeyPatch, fake: FakeGlab) -> tuple[list[dict[str, Any]], int]:
    monkeypatch.setattr(gitlab_cli, "run_cli", fake)
    return ms.gitlab_changes(PROJECT, SINCE)


def test_gitlab_reads_fetch_limit_in_pages(monkeypatch):
    fake = FakeGlab(300, max_page=25)
    got, size = _gitlab(monkeypatch, fake)
    assert [mr["iid"] for mr in got] == [500 - i for i in range(ms.SAMPLE_CAP)]
    assert size == ms.PAGE_SIZE
    assert fake.sizes() == [ms.PAGE_SIZE] * (ms.FETCH_LIMIT // ms.PAGE_SIZE)
    assert len(fake.paths) <= ms.GITLAB_SAMPLE_CALLS


@pytest.mark.parametrize("status", ["502", "504"])
def test_gitlab_retries_a_refused_page_once_at_half_the_size(monkeypatch, status):
    fake = FakeGlab(300, max_page=ms.PAGE_SIZE // 2, status=status)
    got, size = _gitlab(monkeypatch, fake)
    assert [mr["iid"] for mr in got] == [500 - i for i in range(ms.SAMPLE_CAP)]
    assert size == ms.PAGE_SIZE // 2
    assert len(fake.paths) == ms.GITLAB_SAMPLE_CALLS
    pages = [int(parse_qs(urlsplit(p).query)["page"][0]) for p in fake.paths[1:]]
    assert pages == list(range(1, ms.FETCH_LIMIT // (ms.PAGE_SIZE // 2) + 1))


def test_gitlab_switches_size_mid_listing_without_a_gap(monkeypatch):
    """A refusal on a later page keeps the offset: no merge request is skipped or repeated."""
    fake = FakeGlab(300, max_page=None)
    calls = {"n": 0}

    def flaky(cmd: list[str], timeout: int, cache: bool = True) -> CliResult:
        calls["n"] += 1
        if calls["n"] == 3:
            return CliResult(1, "", "glab: 504 Gateway Timeout (HTTP 504)\n")
        return fake(cmd, timeout, cache)

    monkeypatch.setattr(gitlab_cli, "run_cli", flaky)
    got, size = ms.gitlab_changes(PROJECT, None)
    assert size == ms.PAGE_SIZE // 2
    assert [mr["iid"] for mr in got] == [500 - i for i in range(ms.SAMPLE_CAP)]
    listed = [int(parse_qs(urlsplit(p).query)["page"][0]) for p in fake.paths]
    assert listed[:3] == [1, 2, 5]  # 40 listed at 20 a page, then page 5 at 10 a page


def test_gitlab_small_project_reads_one_page(monkeypatch):
    fake = FakeGlab(7, max_page=None)
    got, _ = _gitlab(monkeypatch, fake)
    assert len(got) == 7 and len(fake.paths) == 1


def test_gitlab_refused_twice_is_the_reason(monkeypatch):
    fake = FakeGlab(300, max_page=1)
    with pytest.raises(GhUnavailable, match="glab_error") as caught:
        _gitlab(monkeypatch, fake)
    assert caught.value.status == "502"
    assert fake.sizes() == [ms.PAGE_SIZE, ms.PAGE_SIZE // 2]


def test_the_documented_ceilings_follow_the_constants():
    half = ms.PAGE_SIZE // 2
    assert ms.PAGE_SIZE % 2 == 0 and ms.FETCH_LIMIT % ms.PAGE_SIZE == 0
    assert ms.GITHUB_SAMPLE_CALLS == 2 + -(-ms.SAMPLE_CAP // half) == 5
    assert ms.GITLAB_SAMPLE_CALLS == 1 + ms.FETCH_LIMIT // half == 11
