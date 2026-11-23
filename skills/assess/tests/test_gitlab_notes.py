"""Tests for lib/hosting/gitlab_notes.py: classifying GitLab merge-request notes (#716)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from lib.fleet_config import parse_fleet_config  # noqa: E402
from lib.hosting.gitlab_notes import NoteClassifier, NoteFacts, classifier_from_config, note_facts  # noqa: E402

AUTHOR = "alice"
TOKEN = "project_42_bot_4ffc0de1a2b3"


def _note(user: str, body: str = "looks fine", at: str = "2026-09-01T10:00:00Z",
          **extra: object) -> dict[str, object]:
    return {"author": {"username": user, **extra}, "body": body, "created_at": at,
            "system": False}


def _facts(*notes: dict[str, object], classifier: NoteClassifier | None = None,
           complete: bool = True) -> NoteFacts:
    return note_facts(list(notes), AUTHOR, classifier or NoteClassifier(), complete)


def test_no_notes_is_a_clean_no() -> None:
    assert _facts() == NoteFacts(False, False, None)


def test_human_comment_gives_first_human_comment_at() -> None:
    facts = _facts(_note("bob", at="2026-09-01T10:00:00Z"), _note("carol", at="2026-09-02T10:00:00Z"))
    assert facts == NoteFacts(True, False, "2026-09-01T10:00:00Z")


def test_system_notes_are_ignored() -> None:
    system = {**_note("bob", "## AI Code Review"), "system": True}
    assert _facts(system) == NoteFacts(False, False, None)


def test_authors_own_notes_count_as_neither() -> None:
    assert _facts(_note("Alice")) == NoteFacts(False, False, None)


@pytest.mark.parametrize("body", [
    "## AI Code Review\n\nNo findings.",
    "### :robot: AI Review\nok",
    "**AI Code Review** summary",
    "<!-- ai-code-review:v1 -->\nFindings below",
    "<!-- This is an auto-generated comment by CodeRabbit -->",
])
def test_marker_is_ai_review_whoever_posts_it(body: str) -> None:
    facts = _facts(_note("reviewer-account", body), _note("bob", at="2026-09-03T00:00:00Z"))
    assert facts.ai_review is True
    assert facts.first_human_comment_at == "2026-09-03T00:00:00Z"  # the marker note is not human


def test_prose_mentioning_ai_review_is_a_human_comment() -> None:
    facts = _facts(_note("bob", "I agree with the AI code review said about naming."))
    assert facts == NoteFacts(True, False, "2026-09-01T10:00:00Z")


@pytest.mark.parametrize("user", [
    "claude[bot]",     # a pinned agent identity
    "coderabbitai",    # a pinned AI-review app (delivery.AI_REVIEWERS)
])
def test_ai_accounts_are_ai_review_never_human(user: str) -> None:
    assert _facts(_note(user, "plain text")) == NoteFacts(False, True, None)


@pytest.mark.parametrize("user", [TOKEN, "group_7_bot_abc123"])
def test_unmarked_token_user_note_is_automation_not_review(user: str) -> None:
    # A coverage reporter posting through a project or group access token.
    assert _facts(_note(user, "Coverage: 87.2% (+0.4%)")) == NoteFacts(False, False, None)


def test_marked_token_user_note_is_ai_review() -> None:
    assert _facts(_note(TOKEN, "## AI Code Review\nNo findings.")) == NoteFacts(False, True, None)


def test_token_users_as_agents_makes_token_notes_ai_review() -> None:
    config = parse_fleet_config("[ai_usage]\ntoken_users_as_agents = true\n")
    facts = _facts(_note(TOKEN, "Coverage: 87.2%"), classifier=classifier_from_config(config))
    assert facts == NoteFacts(False, True, None)


def test_a_bot_field_is_not_read() -> None:
    # GitLab's note author has no bot flag; a stray one changes nothing.
    assert _facts(_note("bob", bot=True)) == NoteFacts(True, False, "2026-09-01T10:00:00Z")


def test_dependency_bot_is_neither() -> None:
    assert _facts(_note("renovate-bot", "rebased")) == NoteFacts(False, False, None)


def test_config_markers_and_identities() -> None:
    config = parse_fleet_config(
        '[review]\nai_review_markers = ["Reviewed by OurBot"]\n'
        '[[ai_usage.agent_identities]]\npattern = "^review-svc$"\nfield = "username"\n')
    classifier = classifier_from_config(config)
    assert _facts(_note("bob", "reviewed BY ourbot: 2 findings"), classifier=classifier).ai_review
    assert _facts(_note("review-svc"), classifier=classifier) == NoteFacts(False, True, None)
    assert _facts(_note("bob", "reviewed by ourbot"), classifier=NoteClassifier()).ai_review is False


def test_classifier_without_config() -> None:
    assert _facts(_note(TOKEN), classifier=classifier_from_config(None)).ai_review is False


def test_incomplete_page_leaves_undecided_facts_unknown() -> None:
    assert _facts(_note("Alice"), complete=False) == NoteFacts(None, None, None)
    assert _facts(_note("bob"), complete=False) == NoteFacts(True, None, "2026-09-01T10:00:00Z")


def test_malformed_entries_are_skipped() -> None:
    notes = ["junk", {"system": False, "body": None, "author": None}, {"author": {"username": ""}}]
    assert note_facts(notes, AUTHOR, NoteClassifier()) == NoteFacts(False, False, None)


# ---- the registry entry and the GitLab delivery figures ----------------------

from lib.hosting.delivery import scan_delivery  # noqa: E402
from lib.hosting.gitlab_cli import GitlabProject  # noqa: E402
from lib.hosting.gitlab_notes import gitlab_note_facts  # noqa: E402
from lib.hosting.merged_sample import MergedSample  # noqa: E402

_MRS = ({"iid": 1, "created_at": "2026-09-01T00:00:00Z", "merged_at": "2026-09-02T00:00:00Z"},
        {"iid": 2, "created_at": "2026-09-01T00:00:00Z", "merged_at": "2026-09-03T00:00:00Z"})
_SAMPLE = MergedSample("gitlab", _MRS, None, gitlab=GitlabProject("gitlab.com", "g/a"))


def test_note_facts_spec_is_none_off_gitlab() -> None:
    assert gitlab_note_facts({"available": False, "reason": "x"}) is None
    assert gitlab_note_facts(MergedSample("github", (), None)) is None


def test_gitlab_delivery_without_or_with_refused_notes_leaves_comment_figures_none() -> None:
    for notes in (None, {"available": False, "reason": "no_access: x"}, ()):
        block = scan_delivery(_SAMPLE, {}, None, notes)
        assert block["time_to_merge_hours_p50"] == 24.0
        assert block["first_human_comment_hours_p50"] is None
        assert block["ai_review_share"] is None


def test_gitlab_delivery_from_facts() -> None:
    facts = (NoteFacts(True, True, "2026-09-01T03:00:00Z"), NoteFacts(False, False, None))
    block = scan_delivery(_SAMPLE, {}, None, facts)
    assert block["first_human_comment_hours_p50"] == 3.0
    assert block["human_commented_share"] == 0.5
    assert block["ai_review_share"] == 0.5


def test_gitlab_delivery_undecided_facts_are_none() -> None:
    facts = (NoteFacts(None, None, None), NoteFacts(True, False, "2026-09-01T01:00:00Z"))
    block = scan_delivery(_SAMPLE, {}, None, facts)
    assert block["human_commented_share"] is None
    assert block["first_human_comment_hours_p50"] is None
    assert block["ai_review_share"] is None


def test_an_agent_authors_own_notes_are_not_review() -> None:
    notes = [_note(TOKEN, "## AI Code Review\nself-review"), _note(TOKEN, "rebased")]
    assert note_facts(notes, TOKEN, NoteClassifier()) == NoteFacts(False, False, None)
