"""Backlog-ready level 2 (#1323): outcome metrics read from GitHub and GitLab issue trackers.

``gh`` and ``glab`` are stubbed at ``run_cli`` in ``lib.hosting.gh_cli`` and
``lib.hosting.gitlab_cli`` and answered from the recorded responses under
``fixtures/backlog_ready/platform/``. Nothing reaches the network.
"""
from __future__ import annotations

import copy
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

import lib.hosting.gh_cli as gh_cli
import lib.hosting.gitlab_cli as gitlab_cli
import lib.hosting.issue_outcomes as io
import lib.hosting.issue_outcomes_gitlab as iog
import lib.scoring.level_tally as lt
from lib.assess_config import BacklogThresholds, load_backlog_ready_thresholds
from lib.backlog_ready_outcomes import outcome_fields, scan_backlog_ready_outcomes
from lib.counts_only.content_free import counts_only
from lib.hosting.gh_cli import GithubRepo
from lib.hosting.gitlab_cli import GitlabProject
from lib.hosting.platform_cli import CliResult
from lib.run import signals
from lib.scoring.level_signals import criterion_grade, signal_path_validator
from lib.scoring.levels import MaturityModel, load_levels

FIXTURES = Path(__file__).parent / "fixtures" / "backlog_ready"
PLATFORM = FIXTURES / "platform"
NOW = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
L2 = "backlog-ready.2"
METRICS = ("first_response_median_days", "untriaged_after_7d_share", "close_open_ratio_90d")
MET = ("first_response_met", "untriaged_met", "close_open_met")


def _load(name: str) -> Any:
    return json.loads((PLATFORM / name).read_text(encoding="utf-8"))


def _repo(tmp_path: Path, config: str | None = None) -> Path:
    """The level 1 all-met fixture, so level 2 reads on top of a met level 1."""
    root = tmp_path / "repo"
    shutil.copytree(FIXTURES / "all_met", root)
    if config is not None:
        (root / ".assess").mkdir(exist_ok=True)
        (root / ".assess" / "config.toml").write_text(config, encoding="utf-8")
    return root


def _model() -> MaturityModel:
    return load_levels(FIXTURES / "levels.toml", signal_path_validator())


def _condition(block: Any, cid: str = L2) -> dict[str, Any]:
    conds = lt.level_result(_model(), {"backlog_ready": block})["conditions"]
    return dict(next(c for c in conds if c["id"] == cid))


class FakeGh:
    def __init__(self) -> None:
        self.recent = _load("github_recent.json")
        self.open = _load("github_open.json")
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], timeout: int, cache: bool = True) -> CliResult:
        self.calls.append(cmd)
        query = next(a for a in cmd if a.startswith("query="))
        return CliResult(0, json.dumps(self.open if "states: OPEN" in query else self.recent), "")

    def nodes(self) -> list[dict[str, Any]]:
        return self.recent["data"]["repository"]["issues"]["nodes"]  # type: ignore[no-any-return]


class FakeGlab:
    def __init__(self) -> None:
        self.issues = _load("gitlab_issues.json")
        self.notes = _load("gitlab_notes.json")
        self.users = _load("gitlab_users.json")
        self.open = _load("gitlab_open.json")
        self.refuse_issues = False
        self.calls: list[tuple[str, bool]] = []

    def __call__(self, cmd: list[str], timeout: int, cache: bool = True) -> CliResult:
        path = cmd[-1]
        self.calls.append((path, cache))
        if self.refuse_issues and "/issues?" in path:
            return CliResult(1, "", "glab: 403 Forbidden (HTTP 403)\n")
        if "/notes?" in path:
            iid = path.split("/issues/")[1].split("/")[0]
            return CliResult(0, json.dumps(self.notes.get(iid, [])), "")
        if path.startswith("users/"):
            uid = path.removeprefix("users/")
            if uid not in self.users:
                return CliResult(1, "", "glab: 404 Not Found (HTTP 404)\n")
            return CliResult(0, json.dumps(self.users[uid]), "")
        rows = self.open if "state=opened" in path else self.issues
        return CliResult(0, json.dumps(rows), "")


@pytest.fixture(autouse=True)
def _frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(io, "NOW", lambda: NOW)


@pytest.fixture
def gh(monkeypatch: pytest.MonkeyPatch) -> FakeGh:
    fake = FakeGh()
    monkeypatch.setattr(gh_cli, "run_cli", fake)
    monkeypatch.setattr(io, "git_remote_url", lambda root: "https://github.com/acme/widget.git")
    monkeypatch.setattr(io, "open_gitlab", lambda root: None)
    monkeypatch.setattr(io, "open_github", lambda root: GithubRepo("acme", "widget"))
    return fake


@pytest.fixture
def glab(monkeypatch: pytest.MonkeyPatch) -> FakeGlab:
    fake = FakeGlab()
    monkeypatch.setattr(gitlab_cli, "run_cli", fake)
    monkeypatch.setattr(io, "git_remote_url", lambda root: "https://gitlab.example.com/grp/app.git")
    monkeypatch.setattr(io, "open_gitlab", lambda root: GitlabProject("gitlab.example.com", "grp/app"))
    return fake


def _block(root: Path) -> dict[str, Any]:
    return dict(scan_backlog_ready_outcomes(root, io.backlog_outcomes(root)))


# --- success criterion 1: GitHub ------------------------------------------------------

def test_github_all_three_met(tmp_path: Path, gh: FakeGh) -> None:
    block = _block(_repo(tmp_path))
    assert [block[m] for m in METRICS] == [1.0, 0.1, 1.0]
    assert [block[m] for m in MET] == [True, True, True]
    assert block["outcomes"] == {
        "available": True, "window_days": 90, "opened": 3, "closed": 3, "responded": 3,
        "no_response_count": 0, "response_unread": 0, "open_after_7d": 10, "untriaged_after_7d": 1,
        "truncated": False, "thresholds_from_config": False}
    cond = _condition(block)
    assert (cond["state"], cond["grade"], cond["counted"]) == ("met", "verified", True)
    assert all(c[:3] == ["gh", "api", "graphql"] for c in gh.calls) and len(gh.calls) == 2
    assert "since=2026-08-27T12:00:00Z" in gh.calls[0]


def _slow(fake: FakeGh) -> None:
    for node in fake.nodes():
        for item in node["timelineItems"]["nodes"]:
            item["createdAt"] = item["createdAt"].replace("2026-11-01T12", "2026-11-09T12").replace(
                "2026-10-11", "2026-10-20").replace("2026-09-16", "2026-09-25")


def _unlabel(fake: FakeGh) -> None:
    for node in fake.open["data"]["repository"]["issues"]["nodes"][:3]:
        node["labels"]["nodes"] = []


def _unclosed(fake: FakeGh) -> None:
    for node in fake.nodes()[:2]:
        node["closedAt"] = None


@pytest.mark.parametrize(("change", "failing", "value"), [
    (_slow, "first_response_met", 10.0),
    (_unlabel, "untriaged_met", 0.4),
    (_unclosed, "close_open_met", 0.6667),
])
def test_github_each_metric_failing(tmp_path: Path, gh: FakeGh, change: Any, failing: str, value: float) -> None:
    change(gh)
    block = _block(_repo(tmp_path))
    assert {m: block[m] for m in MET} == {m: m != failing for m in MET}
    assert block[METRICS[MET.index(failing)]] == value
    assert _condition(block)["state"] == "unmet"


@pytest.mark.parametrize("who", ["bot", "author"])
def test_a_bot_or_author_only_response_does_not_count(tmp_path: Path, gh: FakeGh, who: str) -> None:
    node = gh.nodes()[0]
    actor = {"__typename": "Bot", "login": "refiner"} if who == "bot" else node["author"]
    node["timelineItems"]["nodes"] = [
        {"__typename": "IssueComment", "createdAt": "2026-11-01T00:10:00Z", "author": actor},
        {"__typename": "LabeledEvent", "createdAt": "2026-11-01T00:11:00Z", "actor": actor},
        {"__typename": "ClosedEvent", "createdAt": "2026-11-03T00:00:00Z", "actor": actor}]
    block = _block(_repo(tmp_path))
    assert (block["outcomes"]["responded"], block["outcomes"]["no_response_count"]) == (2, 1)
    assert block["first_response_median_days"] == 1.0


def test_a_bot_login_suffix_is_a_bot() -> None:
    assert io.gh_is_bot({"__typename": "User", "login": "renovate[bot]"})
    assert not io.gh_is_bot({"__typename": "User", "login": "bob"})


def test_a_custom_threshold_flips_a_metric(tmp_path: Path, gh: FakeGh) -> None:
    _slow(gh)
    assert _block(_repo(tmp_path / "a"))["first_response_met"] is False
    block = _block(_repo(tmp_path / "b", "[backlog_ready]\nfirst_response_max_days = 10\n"))
    assert block["first_response_met"] is True
    assert block["outcomes"]["thresholds_from_config"] is True
    assert _condition(block)["state"] == "met"


def test_a_long_timeline_with_no_response_read_is_unsettled(tmp_path: Path, gh: FakeGh) -> None:
    node = gh.nodes()[0]
    node["timelineItems"] = {"pageInfo": {"hasNextPage": True}, "nodes": []}
    block = _block(_repo(tmp_path))
    assert (block["outcomes"]["responded"], block["outcomes"]["response_unread"]) == (2, 1)


def test_a_page_cap_is_truncated_and_still_measured(tmp_path: Path, gh: FakeGh,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(io, "MAX_PAGES", 1)
    gh.recent["data"]["repository"]["issues"]["pageInfo"]["hasNextPage"] = True
    block = _block(_repo(tmp_path))
    assert block["outcomes"]["truncated"] is True
    assert block["first_response_met"] is True and len(gh.calls) == 2
    # The dropped rows are the oldest, which could only add closes: the ratio is not taken.
    assert (block["close_open_ratio_90d"], block["close_open_met"]) == (None, None)
    assert block["close_open_ratio_90d_reason"] == "window_truncated"


def test_a_capped_window_that_drops_window_issues_leaves_first_response_null(
        tmp_path: Path, gh: FakeGh, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(io, "MAX_PAGES", 1)
    nodes = gh.nodes()
    del nodes[3]  # no row from before the window: issues opened in it may have been dropped
    gh.recent["data"]["repository"]["issues"]["pageInfo"] = {"hasNextPage": True, "endCursor": "Y3Vyc29yOjM="}
    block = _block(_repo(tmp_path))
    assert block["first_response_median_days"] == 1.0 and block["first_response_met"] is None


def test_an_issue_with_unread_labels_counts_untriaged(tmp_path: Path, gh: FakeGh) -> None:
    for node in gh.open["data"]["repository"]["issues"]["nodes"][:2]:
        node["labels"]["totalCount"] = 150
    block = _block(_repo(tmp_path))
    assert block["outcomes"]["untriaged_after_7d"] == 3 and block["untriaged_met"] is False


def test_a_capped_open_list_leaves_the_untriaged_share_null(tmp_path: Path, gh: FakeGh,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(io, "MAX_PAGES", 1)
    gh.open["data"]["repository"]["issues"]["pageInfo"] = {"hasNextPage": True, "endCursor": "Y3Vyc29yOjEx"}
    block = _block(_repo(tmp_path))
    assert block["outcomes"]["truncated"] is True and block["close_open_met"] is True
    assert (block["untriaged_after_7d_share"], block["untriaged_met"]) == (None, None)
    assert block["untriaged_after_7d_share_reason"] == "open_truncated"


def test_gitlab_a_capped_open_list_leaves_the_untriaged_share_null(tmp_path: Path, glab: FakeGlab,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(iog, "GITLAB_PAGE", 10)  # the 10 open issues fill the one page read
    monkeypatch.setattr(iog, "MAX_PAGES", 1)
    block = _block(_repo(tmp_path))
    assert (block["untriaged_met"], block["untriaged_after_7d_share_reason"]) == (None, "open_truncated")


def test_unread_responses_are_not_an_empty_window(tmp_path: Path, gh: FakeGh) -> None:
    for node in gh.nodes():
        node["timelineItems"] = {"pageInfo": {"hasNextPage": True}, "nodes": []}
    block = _block(_repo(tmp_path))
    assert block["first_response_median_days_reason"] == "response_unread"
    assert block["first_response_met"] is None


def _silent(created: str) -> dict[str, Any]:
    return {"createdAt": created, "closedAt": None, "author": {"__typename": "User", "login": "zed"},
            "timelineItems": {"pageInfo": {"hasNextPage": False}, "nodes": []}}


def test_a_few_fast_answers_among_ignored_issues_read_unmet(tmp_path: Path, gh: FakeGh) -> None:
    gh.nodes().extend(_silent(f"2026-10-{d:02d}T00:00:00Z") for d in range(1, 21))
    block = _block(_repo(tmp_path))
    assert block["first_response_median_days"] == 1.0  # the CHAOSS median over answered issues
    assert block["first_response_met"] is False  # 20 issues ignored for weeks decide the median


def test_one_fresh_issue_does_not_hide_an_ignored_tracker(tmp_path: Path, gh: FakeGh) -> None:
    gh.recent["data"]["repository"]["issues"]["nodes"] = [
        *(_silent(f"2026-10-{d:02d}T00:00:00Z") for d in range(1, 11)), _silent("2026-11-25T09:00:00Z")]
    block = _block(_repo(tmp_path))
    assert (block["first_response_median_days_reason"], block["first_response_met"]) == ("no_response", False)


def test_fresh_unanswered_issues_that_could_tip_the_median_read_null(tmp_path: Path, gh: FakeGh) -> None:
    gh.nodes().extend(_silent(f"2026-11-25T0{h}:00:00Z") for h in range(4))
    block = _block(_repo(tmp_path))
    assert block["first_response_median_days"] == 1.0 and block["first_response_met"] is None


def test_an_ignored_tracker_reads_unmet_not_unmeasured(tmp_path: Path, gh: FakeGh) -> None:
    for node in gh.nodes():
        node["timelineItems"]["nodes"] = []
    block = _block(_repo(tmp_path))
    assert block["first_response_median_days"] is None
    assert (block["first_response_median_days_reason"], block["first_response_met"]) == ("no_response", False)


# --- success criterion 1 on GitLab (#1370) -------------------------------------------

def test_gitlab_all_three_met(tmp_path: Path, glab: FakeGlab) -> None:
    block = _block(_repo(tmp_path))
    assert [block[m] for m in METRICS] == [1.0, 0.1, 1.0]
    assert [block[m] for m in MET] == [True, True, True]
    assert _condition(block)["state"] == "met"
    notes = [(p, cache) for p, cache in glab.calls if "/notes?" in p]
    assert len(notes) == 3 and not any(cache for _, cache in notes)  # never cached: note bodies
    lists = [cache for p, cache in glab.calls if "/issues?" in p]
    assert len(lists) == 2 and not any(lists)  # never cached: issue titles and descriptions
    assert all(p.startswith(("projects/grp%2Fapp/", "users/")) for p, _ in glab.calls)


def test_gitlab_each_metric_failing(tmp_path: Path, glab: FakeGlab, monkeypatch: pytest.MonkeyPatch) -> None:
    slow = copy.deepcopy(glab)
    for notes in slow.notes.values():
        for n in notes:
            n["created_at"] = n["created_at"].replace("2026-11-01T12", "2026-11-09T12").replace(
                "2026-10-11", "2026-10-20")
    slow.issues[2]["closed_at"] = "2026-09-25T00:00:00Z"
    untriaged = copy.deepcopy(glab)
    for row in untriaged.open[:3]:
        row["labels"] = []
    unclosed = copy.deepcopy(glab)
    for row in unclosed.issues[:2]:
        row["closed_at"], row["closed_by"] = None, None
    for i, (fake, failing) in enumerate(((slow, "first_response_met"), (untriaged, "untriaged_met"),
                                         (unclosed, "close_open_met"))):
        monkeypatch.setattr(gitlab_cli, "run_cli", fake)
        block = _block(_repo(tmp_path / str(i)))
        assert {m: block[m] for m in MET} == {m: m != failing for m in MET}, failing


def test_gitlab_a_bot_user_and_a_system_note_do_not_count(tmp_path: Path, glab: FakeGlab) -> None:
    bob = glab.issues[0]["closed_by"]
    glab.notes["301"] = [glab.notes["301"][0],  # the refiner account: users/9 says bot
                         {"id": 20, "body": "added ~bug label", "author": bob,
                          "created_at": "2026-11-01T00:30:00Z", "system": True}]
    glab.issues[0]["closed_at"], glab.issues[0]["closed_by"] = None, None
    block = _block(_repo(tmp_path))
    assert (block["outcomes"]["responded"], block["outcomes"]["no_response_count"]) == (2, 1)
    assert ("users/9", True) in glab.calls


def test_gitlab_bot_flags_and_probe_cap(glab: FakeGlab, monkeypatch: pytest.MonkeyPatch) -> None:
    bots = iog.GitlabBots(GitlabProject("gitlab.example.com", "grp/app"))
    assert bots.is_bot({"id": 77, "bot": True}) is True
    assert bots.is_bot({"id": 78, "username": "group_12_bot_ab12"}) is True
    assert bots.is_bot({"id": 79, "username": "someone"}) is None  # the probe 404s: unknown
    assert bots.is_bot("not a user") is None
    monkeypatch.setattr(iog, "MAX_USER_PROBES", 0)
    capped = iog.GitlabBots(GitlabProject("gitlab.example.com", "grp/app"))
    assert capped.is_bot({"id": 2}) is None and capped.capped


def test_gitlab_an_unclassified_responder_leaves_the_response_unread(tmp_path: Path, glab: FakeGlab) -> None:
    del glab.users["2"]
    block = _block(_repo(tmp_path))
    assert block["outcomes"]["response_unread"] == 1


def test_gitlab_the_note_read_cap_truncates(tmp_path: Path, glab: FakeGlab,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(iog, "MAX_NOTE_READS", 1)
    block = _block(_repo(tmp_path))
    assert block["outcomes"]["truncated"] is True
    assert block["outcomes"]["response_unread"] == 2


def test_gitlab_a_403_on_issues_is_no_platform_auth(tmp_path: Path, glab: FakeGlab) -> None:
    glab.refuse_issues = True
    block = _block(_repo(tmp_path))
    assert [block[f"{m}_reason"] for m in METRICS] == ["no_platform_auth"] * 3
    assert [block[m] for m in MET] == [None, None, None]
    assert block["outcomes"] == {"available": False, "reason": "no_platform_auth"}
    assert _condition(block)["state"] == "not_measured"


# --- success criteria 2 to 5 ----------------------------------------------------------

def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_headless_reads_not_measured(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "remote", "add", "origin", "https://github.com/acme/widget.git")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "c")
    block = signals.collect(root, ["backlog_ready"])["backlog_ready"]
    assert [block[f"{m}_reason"] for m in METRICS] == ["no_network"] * 3
    assert [block[m] for m in (*METRICS, *MET)] == [None] * 6
    cond = _condition(block)
    assert cond["state"] == "not_measured"
    assert _condition(block, "backlog-ready.1")["state"] == "met"


def test_offline_with_no_remote_is_unsupported_host(tmp_path: Path) -> None:
    assert io.offline_backlog_outcomes(tmp_path) == {"available": False, "reason": "unsupported_host"}


def test_empty_window_not_measured(tmp_path: Path, gh: FakeGh) -> None:
    gh.recent["data"]["repository"]["issues"]["nodes"] = []
    gh.open["data"]["repository"]["issues"]["nodes"] = []
    block = _block(_repo(tmp_path))
    assert [block[f"{m}_reason"] for m in METRICS] == ["empty_window"] * 3
    assert [block[m] for m in MET] == [None, None, None]
    assert _condition(block)["state"] == "not_measured"


def test_counts_only_output(tmp_path: Path, gh: FakeGh, glab: FakeGlab,
                            monkeypatch: pytest.MonkeyPatch) -> None:
    text = []
    for provider in ("github", "gitlab"):
        if provider == "github":
            monkeypatch.setattr(io, "git_remote_url", lambda root: "https://github.com/acme/widget.git")
            monkeypatch.setattr(io, "open_gitlab", lambda root: None)
        block = _block(_repo(tmp_path / provider))
        out = counts_only({"backlog_ready": block})["backlog_ready"]
        for field in ("available", *METRICS, *MET, "outcomes"):
            assert out[field] == block[field], field
        result = lt.level_result(_model(), {"backlog_ready": block})
        text.append(json.dumps(block) + json.dumps(out) + json.dumps(result))
        monkeypatch.setattr(io, "open_gitlab", lambda root: GitlabProject("gitlab.example.com", "grp/app"))
        monkeypatch.setattr(io, "git_remote_url", lambda root: "https://gitlab.example.com/grp/app.git")
    blob = "\n".join(text)
    for needle in ("Fixture", "fixture", "9101", "8000", "301", "refiner", "issues/", "acme"):
        assert needle not in blob, needle


def test_the_level_2_conditions_grade_verified() -> None:
    (row,) = _model()["rows"]
    level2 = row["levels"][1]["criteria"]
    assert [c["signal"] for c in level2] == [f"backlog_ready.{m}" for m in MET]
    assert {criterion_grade(c) for c in level2} == {"verified"}


# --- the helpers ----------------------------------------------------------------------

@pytest.mark.parametrize(("reason", "code"), [
    ("gh_not_installed: x", "no_platform_auth"), ("not_authenticated: x", "no_platform_auth"),
    ("no_access: x", "no_platform_auth"), ("not_found: x", "no_platform_auth"),
    ("unreachable: x", "no_network"), ("glab_timeout: x", "no_network"),
    ("no_remote: x", "unsupported_host"), ("rate_limited: x", "platform_error"),
    ("gh_bad_json: x", "platform_error"),
])
def test_reason_codes(reason: str, code: str) -> None:
    assert io.reason_code(reason) == code


def test_an_unread_value_without_a_reason_is_a_platform_error() -> None:
    fields = outcome_fields({"available": False}, BacklogThresholds(), False, ("needs-triage",))
    assert fields["outcomes"] == {"available": False, "reason": "platform_error"}


@pytest.mark.parametrize(("config", "want", "flag"), [
    ("", BacklogThresholds(), False),
    ("[backlog_ready]\nfirst_response_max_days = 5\nuntriaged_max_share = 0.5\n"
     "close_open_min_ratio = 1\nwindow_days = 30\n", BacklogThresholds(5.0, 0.5, 1.0, 30), True),
    ("[backlog_ready]\nfirst_response_max_days = -1\nuntriaged_max_share = 2\n"
     "close_open_min_ratio = true\nwindow_days = 0\n", BacklogThresholds(), False),
    ("backlog_ready = 3\n", BacklogThresholds(), False),
])
def test_thresholds_load_with_defaults(tmp_path: Path, config: str, want: BacklogThresholds, flag: bool) -> None:
    (tmp_path / ".assess").mkdir()
    (tmp_path / ".assess" / "config.toml").write_text(config, encoding="utf-8")
    assert load_backlog_ready_thresholds(tmp_path) == (want, flag)


def test_the_window_follows_the_config(tmp_path: Path, gh: FakeGh) -> None:
    block = _block(_repo(tmp_path, "[backlog_ready]\nwindow_days = 30\n"))
    assert block["outcomes"]["window_days"] == 30 and block["outcomes"]["opened"] == 1
    assert "since=2026-10-26T12:00:00Z" in gh.calls[0]


@pytest.mark.parametrize(("url", "reason"), [
    (None, "unsupported_host"), ("https://bitbucket.org/acme/widget.git", "unsupported_host"),
])
def test_a_remote_no_read_supports_is_unsupported_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                       url: str | None, reason: str) -> None:
    monkeypatch.setattr(io, "git_remote_url", lambda root: url)
    assert io.backlog_outcomes(tmp_path) == {"available": False, "reason": reason}


def test_a_self_managed_host_glab_does_not_reach_is_unsupported(tmp_path: Path,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(io, "git_remote_url", lambda root: "https://git.example.com/grp/app.git")
    monkeypatch.setattr(io, "open_gitlab", lambda root: None)
    assert io.backlog_outcomes(tmp_path) == {"available": False, "reason": "unsupported_host"}


def test_a_refused_github_read_is_no_platform_auth(tmp_path: Path, gh: FakeGh,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gh_cli, "run_cli", lambda cmd, timeout, cache=True: CliResult(
        1, "", "gh: Resource not accessible by integration (HTTP 403)\n"))
    assert io.backlog_outcomes(tmp_path) == {"available": False, "reason": "no_platform_auth"}


def test_a_null_repository_is_no_platform_auth(tmp_path: Path, gh: FakeGh) -> None:
    gh.recent = {"data": {"repository": None}}
    assert io.backlog_outcomes(tmp_path) == {"available": False, "reason": "no_platform_auth"}


# --- #1587: false unmets left by #1323 -------------------------------------------------

def _own_notes(start: str, n: int) -> list[dict[str, Any]]:
    """``n`` notes by issue 303's author, a minute apart from ``start`` (an hour, ``HH``)."""
    author = {"id": 4, "username": "dave-fixture"}
    return [{"id": 100 + i, "body": "x", "author": author, "system": False,
             "created_at": f"2026-09-15T{start}:{i % 60:02d}:{i // 60:02d}Z"} for i in range(n)]


@pytest.mark.parametrize(("other", "responded", "unread"), [
    (None, 2, 1),  # a full page: an unread note on page 2 may come before the close
    ("01", 3, 0),  # a response on the page, before any note page 2 could hold
])
def test_gitlab_a_full_notes_page_leaves_a_later_close_unread(
        tmp_path: Path, glab: FakeGlab, monkeypatch: pytest.MonkeyPatch, other: str | None,
        responded: int, unread: int) -> None:
    monkeypatch.setattr(iog, "GITLAB_PAGE", 5)
    notes = _own_notes("02", 5)
    if other is not None:
        notes[0] = {**notes[0], "author": {"id": 5, "username": "erin-fixture"}, "created_at": "2026-09-15T01:30:00Z"}
    glab.notes["303"] = notes
    glab.issues[2]["user_notes_count"] = 9
    block = _block(_repo(tmp_path))
    assert (block["outcomes"]["responded"], block["outcomes"]["response_unread"]) == (responded, unread)


def _self_closed(created: str) -> dict[str, Any]:
    zed = {"__typename": "User", "login": "zed"}
    closed = created.replace("T00", "T05")
    return {"createdAt": created, "closedAt": closed, "author": zed,
            "timelineItems": {"pageInfo": {"hasNextPage": False},
                              "nodes": [{"__typename": "ClosedEvent", "createdAt": closed, "actor": zed}]}}


def test_issues_closed_by_their_author_are_not_overdue(tmp_path: Path, gh: FakeGh) -> None:
    gh.nodes().extend(_self_closed(f"2026-10-{d:02d}T00:00:00Z") for d in range(1, 21))
    block = _block(_repo(tmp_path))
    assert block["outcomes"]["no_response_count"] == 20  # still counted as unanswered (CHAOSS)
    assert block["first_response_met"] is True  # nobody owed a response to an issue its author closed


def test_an_issue_closed_by_a_bot_with_no_response_stays_overdue(tmp_path: Path, gh: FakeGh) -> None:
    for d in range(1, 21):
        node = _self_closed(f"2026-10-{d:02d}T00:00:00Z")
        node["timelineItems"]["nodes"][0]["actor"] = {"__typename": "Bot", "login": "stale"}
        gh.nodes().append(node)
    assert _block(_repo(tmp_path))["first_response_met"] is False


def test_gitlab_issues_closed_by_their_author_are_not_overdue(tmp_path: Path, glab: FakeGlab) -> None:
    for d in range(1, 21):
        row = copy.deepcopy(glab.issues[3])
        row.update(iid=400 + d, created_at=f"2026-10-{d:02d}T00:00:00Z", closed_at=f"2026-10-{d:02d}T05:00:00Z",
                   closed_by=row["author"], user_notes_count=0)
        glab.issues.append(row)
    block = _block(_repo(tmp_path))
    assert block["outcomes"]["no_response_count"] == 20
    assert block["first_response_met"] is True
