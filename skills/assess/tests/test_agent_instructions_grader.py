"""Tests for heuristic agent-instructions grader.

Grades any of: CLAUDE.md, AGENTS.md, GEMINI.md, .cursorrules,
.github/copilot-instructions.md. The grader operates on text + freshness,
so it's filename-agnostic - the file selection lives in assess_core.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lib.agent_instructions_grader import (
    compute_size_metrics,
    count_positive_directives,
    count_tradeoff_phrases,
    count_path_references,
    count_verifiable_outcomes,
    detect_skills_delegation,
    grade_instructions,
)


@pytest.fixture
def good_text(fixtures_dir: Path) -> str:
    return (fixtures_dir / "good_instructions.md").read_text()


@pytest.fixture
def bad_text(fixtures_dir: Path) -> str:
    return (fixtures_dir / "bad_instructions.md").read_text()


def test_positive_directives_good_outscores_bad(good_text: str, bad_text: str) -> None:
    # Good fixture uses: Use, Prefer, Default to, Match, Add (positive)
    # Bad fixture uses mostly: Write, Follow, Be, Don't (negatives + generic verbs)
    assert count_positive_directives(good_text) >= 5
    assert count_positive_directives(bad_text) <= 2


def test_tradeoff_phrases_only_in_good(good_text: str, bad_text: str) -> None:
    # "because", "over X" are tradeoff signals
    assert count_tradeoff_phrases(good_text) >= 2
    assert count_tradeoff_phrases(bad_text) == 0


def test_path_references_only_in_good(good_text: str, bad_text: str) -> None:
    # Good fixture has src/auth/, src/payments/processor.py, etc.
    assert count_path_references(good_text) >= 4
    assert count_path_references(bad_text) == 0


def test_verifiable_outcomes_only_in_good(good_text: str, bad_text: str) -> None:
    # "Working if" is the signal phrase
    assert count_verifiable_outcomes(good_text) >= 1
    assert count_verifiable_outcomes(bad_text) == 0


def test_grade_returns_letter_grade(good_text: str, bad_text: str) -> None:
    good = grade_instructions(good_text, freshness_days=10)
    bad = grade_instructions(bad_text, freshness_days=10)

    assert good.grade in {"A", "A-", "B+", "B"}
    assert bad.grade in {"D", "F"}
    assert good.score > bad.score


def test_grade_penalizes_staleness(good_text: str) -> None:
    fresh = grade_instructions(good_text, freshness_days=10)
    stale = grade_instructions(good_text, freshness_days=400)
    assert stale.score < fresh.score


def test_grade_empty_string_is_F() -> None:
    empty = grade_instructions("", freshness_days=0)
    assert empty.grade == "F"
    assert empty.score == 0


def test_subscores_in_result(good_text: str) -> None:
    result = grade_instructions(good_text, freshness_days=10)
    assert result.subscores["positive_directives"] >= 5
    assert result.subscores["path_references"] >= 4
    assert "tradeoff_phrases" in result.subscores
    assert "verifiable_outcomes" in result.subscores


def test_size_metrics_accurate() -> None:
    text = "line1\nline2\nline3"
    m = compute_size_metrics(text)
    assert m["line_count"] == 3
    assert m["word_count"] == 3
    assert m["exceeds_line_threshold"] is False
    assert m["exceeds_word_threshold"] is False


def test_skills_delegation_detection() -> None:
    text = "Load Java conventions via the `java-conventions` skill."
    d = detect_skills_delegation(text)
    assert d["delegates_to_skills"] is True
    assert d["delegation_pointers"] >= 1
    assert len(d["delegation_samples"]) >= 1


def test_skills_delegation_detection_dir_pointer() -> None:
    text = "Topic guidance lives under .claude/skills/ and loads on demand."
    d = detect_skills_delegation(text)
    assert d["delegates_to_skills"] is True


def test_no_skills_delegation_in_generic_text() -> None:
    text = "Write clean code. Follow best practices."
    d = detect_skills_delegation(text)
    assert d["delegates_to_skills"] is False
    assert d["delegation_pointers"] == 0


def test_size_subscores_in_grade(good_text: str) -> None:
    result = grade_instructions(good_text, freshness_days=10)
    assert "line_count" in result.subscores
    assert "word_count" in result.subscores
    assert "bloat_penalty" in result.subscores
    assert result.subscores["bloat_penalty"] == 0  # good_instructions is small
