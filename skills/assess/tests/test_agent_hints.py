"""Tests for name-only agent matches as hints that need confirmation (issue #883).

A pinned agent match with no ``[bot]`` suffix and no verified app identity (a vendor
address such as ``noreply@anthropic.com``, the ``copilot`` login, a GitLab username,
``delivery.AI_REVIEWERS`` on GitLab) is a hint: it counts as nothing until the human
confirms the account in ``[identities]`` (#765). ``[bot]``-suffix apps and AI
co-author trailers still count without confirmation.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from lib.account_discovery import EVIDENCE, STRONG_EVIDENCE, HistoryCommit, discover, evidence_for  # noqa: E402
from lib.agent_identity import (  # noqa: E402
    AGENT,
    AGENT_HINT_RULES,
    AGENT_RULES,
    OTHER,
    AuthorMatcher,
    ConfirmedIdentities,
    coauthors_have_ai_assistant,
    compile_config_rules,
    is_agent_hint,
)
from lib.ai_usage import scan_ai_usage  # noqa: E402
from lib.counts_only import counts_only_scans  # noqa: E402
from lib.delivery import agent_authored, hinted_author, scan_delivery  # noqa: E402
from lib.gitlab_cli import GitlabProject  # noqa: E402
from lib.merged_sample import MergedSample  # noqa: E402
from lib.fleet_config import parse_fleet_config  # noqa: E402
from lib.gitlab_notes import NoteClassifier, NoteFacts, classifier_from_config, note_facts  # noqa: E402

HINTS = (
    ("noreply@anthropic.com", None),
    ("copilot@example.com", None),
    ("cursoragent@cursor.com", None),
    ("openhands@all-hands.dev", None),
    ("", "openhands-agent"),
    ("", "copilot"),
    ("1+Copilot@users.noreply.github.com", None),   # another id: a person could hold it
    ("Copilot@users.noreply.github.com", None),
)


def _confirmed(**lists: list[str]) -> AuthorMatcher:
    return AuthorMatcher(confirmed=ConfirmedIdentities.from_section(lists))


# --- the matcher ---------------------------------------------------------------

def test_counted_pinned_rules_all_carry_the_bot_suffix_or_an_id() -> None:
    for r in AGENT_RULES:
        pinned_id = r.pattern.pattern.startswith("^198982749\\+")
        assert r.pattern.pattern.endswith(r"\[bot\]$") or pinned_id, r.pattern.pattern
    for r in AGENT_HINT_RULES:
        assert "[bot]" not in r.pattern.pattern.replace("\\", "")


@pytest.mark.parametrize(("email", "username"), HINTS)
def test_name_only_matches_are_hints_not_agents(email: str, username: str | None) -> None:
    assert AuthorMatcher().classify(email, "A Person", username).bucket == OTHER
    assert is_agent_hint(email, username)


@pytest.mark.parametrize("username", ["claude[bot]", "copilot-swe-agent[bot]", "devin-ai-integration[bot]",
                                      "chatgpt-codex-connector[bot]", "amazon-q-developer[bot]"])
def test_bot_suffix_apps_still_count(username: str) -> None:
    assert AuthorMatcher().classify_username(username).bucket == AGENT
    assert not is_agent_hint("", username)


def test_the_copilot_coding_agent_commit_address_counts() -> None:
    email = "198982749+Copilot@users.noreply.github.com"
    assert AuthorMatcher().classify(email, "copilot-swe-agent[bot]").bucket == AGENT
    assert AuthorMatcher().classify(email, "Copilot").bucket == AGENT
    assert not is_agent_hint(email)
    assert discover([HistoryCommit(email, "copilot-swe-agent[bot]", "x", "")], AuthorMatcher()) == []


def test_a_confirmed_hint_counts_and_a_confirmed_person_never_does() -> None:
    assert _confirmed(agents=["noreply@anthropic.com"]).classify("noreply@anthropic.com", "Claude").bucket == AGENT
    assert _confirmed(agents=["copilot"]).classify_username("Copilot").bucket == AGENT
    assert _confirmed(people=["copilot"]).classify_username("copilot").bucket == OTHER


def test_a_config_rule_still_makes_a_hint_an_agent() -> None:
    matcher = AuthorMatcher(compile_config_rules([{"pattern": "^cursoragent@", "field": "email"}]))
    assert matcher.classify("cursoragent@cursor.com", "Cursor Agent") == (AGENT, 0)


def test_trailers_still_count_without_confirmation() -> None:
    assert coauthors_have_ai_assistant("Claude <noreply@anthropic.com>")
    assert coauthors_have_ai_assistant("Copilot <copilot@example.com>")


# --- discovery -----------------------------------------------------------------

def test_a_hint_is_strong_evidence() -> None:
    assert "pinned_agent_hint" in EVIDENCE
    assert "pinned_agent_hint" in STRONG_EVIDENCE
    assert "pinned_agent_hint" in evidence_for("noreply@anthropic.com", "Ann")
    assert "pinned_agent_hint" not in evidence_for("ann@example.com", "Ann")


def test_a_hinted_author_is_listed_until_confirmed() -> None:
    commits = [HistoryCommit("noreply@anthropic.com", "Ann Person", "fix: a", ""),
               HistoryCommit("ann@example.com", "Ann", "fix: b", "")]
    found = discover(commits, AuthorMatcher())
    assert [c["email"] for c in found] == ["noreply@anthropic.com"]
    assert "pinned_agent_hint" in found[0]["evidence"]
    assert discover(commits, _confirmed(agents=["noreply@anthropic.com"])) == []
    assert discover(commits, _confirmed(people=["noreply@anthropic.com"])) == []


# --- delivery ------------------------------------------------------------------

def test_delivery_agent_authored_needs_confirmation_for_a_hint() -> None:
    github = {"author": {"login": "copilot", "is_bot": False}}
    gitlab = {"author": {"username": "openhands-agent"}}
    assert not agent_authored(github, "github", AuthorMatcher())
    assert not agent_authored(gitlab, "gitlab", AuthorMatcher())
    assert agent_authored(github, "github", _confirmed(agents=["copilot"]))
    assert agent_authored(gitlab, "gitlab", _confirmed(agents=["openhands-agent"]))
    app = {"author": {"login": "copilot-swe-agent", "is_bot": True}}
    assert agent_authored(app, "github", AuthorMatcher())


# --- GitLab notes --------------------------------------------------------------

def _note(user: str, body: str = "looks fine") -> dict[str, Any]:
    return {"author": {"username": user}, "body": body, "created_at": "2026-09-01T10:00:00Z",
            "system": False}


def _facts(user: str, matcher: AuthorMatcher | None = None, body: str = "looks fine") -> NoteFacts:
    return note_facts([_note(user, body)], "alice", NoteClassifier(matcher), True)


@pytest.mark.parametrize("user", ["coderabbitai", "gemini-code-assist", "claude", "cursor", "copilot",
                                  "openhands-agent"])
def test_an_unconfirmed_hint_leaves_review_and_human_undecided(user: str) -> None:
    # It could be an AI reviewer or a person: never a silent False (#883).
    assert _facts(user, body="## Summary of Changes") == NoteFacts(None, None, None, frozenset({user}))


def test_a_hint_after_the_first_human_leaves_only_ai_review_undecided() -> None:
    notes = [_note("bob"), {**_note("cursor"), "created_at": "2026-09-02T10:00:00Z"}]
    facts = note_facts(notes, "alice", NoteClassifier(), True)
    assert facts == NoteFacts(True, None, "2026-09-01T10:00:00Z", frozenset({"cursor"}))


def test_a_hint_before_the_first_human_nulls_only_the_timestamp() -> None:
    facts = note_facts([_note("coderabbitai"), _note("bob")], "alice", NoteClassifier(), True)
    assert facts.human_comment is True  # a person did comment
    assert facts.first_human_comment_at is None  # the hint may have been the first person
    assert facts.ai_review is None


def test_ai_review_found_elsewhere_decides_despite_a_hint() -> None:
    notes = [_note("claude[bot]"), _note("cursor")]
    assert note_facts(notes, "alice", NoteClassifier(), True).ai_review is True


def test_a_marker_still_makes_a_hinted_note_ai_review() -> None:
    assert _facts("coderabbitai", body="<!-- auto-generated comment by CodeRabbit -->").ai_review is True


def test_confirmed_answers_decide_a_hinted_gitlab_account() -> None:
    assert _facts("coderabbitai", _confirmed(agents=["coderabbitai"])) == NoteFacts(False, True, None)
    assert _facts("cursor", _confirmed(people=["cursor"])) == NoteFacts(True, False, "2026-09-01T10:00:00Z")
    assert _facts("coderabbitai", _confirmed(bots=["coderabbitai"])) == NoteFacts(False, False, None)


def _gitlab_scan(notes: tuple[NoteFacts, ...], authors: tuple[str, ...],
                 config: str = "") -> dict[str, Any]:
    mrs = tuple({"iid": i, "author": {"username": a}, "created_at": "2026-09-01T00:00:00Z",
                 "merged_at": "2026-09-02T00:00:00Z"} for i, a in enumerate(authors, 1))
    sample = MergedSample("gitlab", mrs, None, gitlab=GitlabProject("gitlab.com", "grp/app"))
    return dict(scan_delivery(sample, {"available": True}, parse_fleet_config(config) if config else None, notes))


def test_a_gitlab_change_reviewed_only_by_a_hint_nulls_the_share_and_is_counted() -> None:
    hinted = note_facts([_note("gemini-code-assist")], "ann", NoteClassifier(), True)
    plain = note_facts([_note("bob")], "ann", NoteClassifier(), True)
    block = _gitlab_scan((hinted, plain), ("ann", "ann"))
    assert block["ai_review_share"] is None
    assert block["human_commented_share"] is None
    assert block["unconfirmed_ai_accounts"] == 1
    config = '[identities]\nagents = ["gemini-code-assist"]\n'
    classifier = classifier_from_config(parse_fleet_config(config))
    confirmed = note_facts([_note("gemini-code-assist")], "ann", classifier, True)
    block = _gitlab_scan((confirmed, plain), ("ann", "ann"), config)
    assert block["ai_review_share"] == 0.5
    assert block["human_commented_share"] == 0.5
    assert block["unconfirmed_ai_accounts"] == 0


def test_a_hinted_change_author_nulls_the_agent_authored_share() -> None:
    plain = note_facts([], "ann", NoteClassifier(), True)
    block = _gitlab_scan((plain, plain), ("openhands-agent", "ann"))
    assert block["agent_authored_share"] is None
    assert block["unconfirmed_ai_accounts"] == 1
    block = _gitlab_scan((plain, plain), ("openhands-agent", "ann"),
                         '[identities]\nagents = ["openhands-agent"]\n')
    assert block["agent_authored_share"] == 0.5
    assert block["unconfirmed_ai_accounts"] == 0


def test_github_hinted_author_unless_the_account_is_an_app() -> None:
    matcher = AuthorMatcher()
    assert hinted_author({"author": {"login": "Copilot", "is_bot": False}}, "github", matcher) == "copilot"
    assert hinted_author({"author": {"login": "copilot", "is_bot": True}}, "github", matcher) is None
    assert hinted_author({"author": {"login": "ann"}}, "github", matcher) is None
    assert hinted_author({"author": {"login": "copilot"}}, "github", _confirmed(people=["copilot"])) is None


def test_a_confirmed_bot_is_never_a_human_comment() -> None:
    assert _facts("danger-bot", _confirmed(bots=["danger-bot"])) == NoteFacts(False, False, None)


# --- the ai_usage block --------------------------------------------------------

def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   encoding="utf-8", errors="replace", env=env)


def _commit(repo: Path, day: int, name: str, email: str, trailer: str = "") -> None:
    stamp = f"2026-10-{day:02d}T10:00:00Z"
    env = {**os.environ, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
    (repo / "a.txt").write_text(str(day), encoding="utf-8")
    _git(repo, "add", "-A", env=env)
    message = f"change {day}" + (f"\n\nCo-authored-by: {trailer}" if trailer else "")
    _git(repo, "-c", f"user.name={name}", "-c", f"user.email={email}", "commit", "-q", "-m", message, env=env)


@pytest.fixture
def hinted_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, 1, "Claude", "noreply@anthropic.com")            # a hinted author
    _commit(repo, 2, "Ann", "ann@example.com", "Claude <noreply@anthropic.com>")  # a trailer
    _commit(repo, 3, "claude[bot]", "1+claude[bot]@users.noreply.github.com")      # a [bot] app
    return repo


def test_a_hinted_author_awaits_confirmation_and_a_trailer_still_counts(hinted_repo: Path) -> None:
    block = scan_ai_usage(hinted_repo, None, None)
    assert block["available"] is True
    assert block["agent_authored_commits"] == 1       # claude[bot] only
    assert block["agent_trailer_commits"] == 1
    assert block["identities_awaiting_confirmation"] == 1
    config = parse_fleet_config('[identities]\nagents = ["noreply@anthropic.com"]\n')
    confirmed = scan_ai_usage(hinted_repo, None, None, config)
    assert confirmed["available"] is True
    assert confirmed["agent_authored_commits"] == 2
    assert confirmed["identities_awaiting_confirmation"] == 0


def test_the_unconfirmed_count_survives_counts_only() -> None:
    hinted = note_facts([_note("gemini-code-assist")], "ann", NoteClassifier(), True)
    block = _gitlab_scan((hinted,), ("ann",))
    reduced = counts_only_scans({"delivery": block})["delivery"]
    assert reduced["unconfirmed_ai_accounts"] == 1
    assert "gemini" not in json.dumps(reduced) and "gemini" not in json.dumps(block)
