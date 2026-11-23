"""Tests for name-only agent matches as hints that need confirmation (issue #883).

A pinned agent match with no ``[bot]`` suffix and no verified app identity (a vendor
address such as ``noreply@anthropic.com``, the ``copilot`` login, a GitLab username,
``delivery.AI_REVIEWERS`` on GitLab) is a hint: it counts as nothing until the human
confirms the account in ``[identities]`` (#765). ``[bot]``-suffix apps and AI
co-author trailers still count without confirmation.
"""
from __future__ import annotations

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
from lib.delivery import agent_authored  # noqa: E402
from lib.fleet_config import parse_fleet_config  # noqa: E402
from lib.gitlab_notes import NoteClassifier, NoteFacts, note_facts  # noqa: E402

HINTS = (
    ("noreply@anthropic.com", None),
    ("copilot@example.com", None),
    ("cursoragent@cursor.com", None),
    ("openhands@all-hands.dev", None),
    ("", "openhands-agent"),
    ("", "copilot"),
    ("1+Copilot@users.noreply.github.com", None),
)


def _confirmed(**lists: list[str]) -> AuthorMatcher:
    return AuthorMatcher(confirmed=ConfirmedIdentities.from_section(lists))


# --- the matcher ---------------------------------------------------------------

def test_counted_pinned_rules_all_carry_the_bot_suffix() -> None:
    for r in AGENT_RULES:
        assert r.pattern.pattern.endswith(r"\[bot\]$"), r.pattern.pattern
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


@pytest.mark.parametrize("user", ["coderabbitai", "claude", "cursor", "copilot", "openhands-agent"])
def test_an_unconfirmed_hint_is_neither_review_nor_human(user: str) -> None:
    assert _facts(user) == NoteFacts(False, False, None)


def test_a_marker_still_makes_a_hinted_note_ai_review() -> None:
    assert _facts("coderabbitai", body="<!-- auto-generated comment by CodeRabbit -->").ai_review is True


def test_confirmed_answers_decide_a_hinted_gitlab_account() -> None:
    assert _facts("coderabbitai", _confirmed(agents=["coderabbitai"])) == NoteFacts(False, True, None)
    assert _facts("cursor", _confirmed(people=["cursor"])) == NoteFacts(True, False, "2026-09-01T10:00:00Z")
    assert _facts("coderabbitai", _confirmed(bots=["coderabbitai"])) == NoteFacts(False, False, None)


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
