"""Tests for the instruction-file size curve, its skills-delegation halving, and the size grade cap.

The core thesis (see `test_monolith_scores_strictly_below_lean_plus_skills`):
an oversized monolithic instruction file that is NOT factored into on-demand
skills scores STRICTLY BELOW the same file in a repo that delegates to skills,
and the delegating repo still pays half the penalty: an always-loaded file
costs context on every task whether or not skills exist. Past about twice the
curve's start the grade is capped at B either way.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from lib.agent_instructions_grader import (
    SIZE_GRADE_CAP_LINES,
    SIZE_GRADE_CAP_SCORE,
    SIZE_GRADE_CAP_WORDS,
    SIZE_MAX_PENALTY,
    SIZE_THRESHOLD_LINES,
    SIZE_THRESHOLD_WORDS,
    compute_bloat_penalty,
    compute_size_metrics,
    detect_skills_delegation,
    detect_skills_dir,
    grade_instructions,
)
from lib.instruction_content import build_repo_context


def test_bloat_penalty_on_monolith(fixtures_dir: Path) -> None:
    """Monolithic 600+ line file without skills gets a bloat penalty."""
    text = (fixtures_dir / "monolithic_instructions.md").read_text()
    size = compute_size_metrics(text)
    assert size["exceeds_line_threshold"] is True

    penalty, msg = compute_bloat_penalty(
        size, skills_present=False, delegates_to_skills=False
    )
    assert penalty >= 5
    assert msg is not None
    assert "factor guidance into on-demand skills" in msg


def test_no_bloat_penalty_with_skills(fixtures_dir: Path) -> None:
    """Lean file with skills delegation gets no bloat penalty."""
    repo = fixtures_dir / "lean_with_skills"
    text = (repo / "CLAUDE.md").read_text()
    size = compute_size_metrics(text)
    skills = detect_skills_dir(repo)
    delegation = detect_skills_delegation(text)

    penalty, msg = compute_bloat_penalty(
        size,
        skills["skills_dirs_present"],
        delegation["delegates_to_skills"],
    )
    assert penalty == 0
    assert msg is None


def test_oversized_but_factored_into_skills_is_halved_not_waived(fixtures_dir: Path) -> None:
    """An oversized hub file in a repo that delegates to skills pays half the
    penalty: the skills move topic guidance out, but this file still loads on
    every task."""
    text = (fixtures_dir / "monolithic_instructions.md").read_text()
    size = compute_size_metrics(text)
    assert size["exceeds_line_threshold"] is True
    full, full_msg = compute_bloat_penalty(size, skills_present=False, delegates_to_skills=False)
    assert full == SIZE_MAX_PENALTY
    assert full_msg is not None and "factor guidance into on-demand skills" in full_msg

    for skills_present, delegates in ((True, False), (False, True), (True, True)):
        penalty, msg = compute_bloat_penalty(
            size, skills_present=skills_present, delegates_to_skills=delegates
        )
        assert penalty == SIZE_MAX_PENALTY // 2
        assert msg is not None and "halves the penalty" in msg


@pytest.mark.parametrize(("lines", "full", "halved"), [
    (200, 0, 0), (201, 1, 1), (210, 1, 1), (220, 2, 1), (230, 3, 2),
    (300, 10, 5), (500, SIZE_MAX_PENALTY, SIZE_MAX_PENALTY // 2),
])
def test_delegation_halves_rounding_up(lines: int, full: int, halved: int) -> None:
    size = compute_size_metrics("x\n" * lines)
    assert compute_bloat_penalty(size, False, False)[0] == full
    assert compute_bloat_penalty(size, True, False)[0] == halved
    assert compute_bloat_penalty(size, False, True)[0] == halved


def test_monolith_scores_strictly_below_lean_plus_skills(fixtures_dir: Path) -> None:
    """REGRESSION TEST (core thesis): the same guidance scores STRICTLY LOWER
    as an unfactored monolith than in a repo that delegates to skills, and
    the delegating repo still pays a penalty.

    Grading the same text two ways isolates the delegation as the sole
    difference.
    """
    text = (fixtures_dir / "monolithic_instructions.md").read_text()

    monolith_grade = grade_instructions(
        text, freshness_days=10, skills_present=False, delegates_to_skills=False, repo=None)
    factored_grade = grade_instructions(
        text, freshness_days=10, skills_present=True, repo=None)

    assert monolith_grade.subscores["bloat_penalty"] >= 5
    assert 0 < factored_grade.subscores["bloat_penalty"] < monolith_grade.subscores["bloat_penalty"]
    assert monolith_grade.score < factored_grade.score


def test_lean_fixture_repo_detects_skills_and_avoids_penalty(fixtures_dir: Path) -> None:
    """End-to-end on the lean fixture repo: skills dir is detected, delegation
    pointers are present, and the graded file carries no bloat penalty."""
    repo = fixtures_dir / "lean_with_skills"
    text = (repo / "CLAUDE.md").read_text()

    skills = detect_skills_dir(repo)
    assert skills["skills_dirs_present"] is True
    assert skills["skills_count"] == 2
    assert any("java-conventions" in f for f in skills["skill_files"])

    grade = grade_instructions(
        text, freshness_days=10, skills_present=skills["skills_dirs_present"], repo=None)
    assert grade.subscores["bloat_penalty"] == 0


def test_graceful_no_skills_dir(tmp_path: Path) -> None:
    """Repos without any skills directory degrade gracefully."""
    skills = detect_skills_dir(tmp_path)
    assert skills["skills_dirs_present"] is False
    assert skills["skills_count"] == 0
    assert skills["skill_files"] == []
    assert skills["skills_dirs"] == []


def test_small_file_no_penalty() -> None:
    """Small/legitimate instruction files are never penalized."""
    small_text = "Use bcrypt for password hashing.\n" * 100  # 100 lines
    size = compute_size_metrics(small_text)

    assert size["exceeds_line_threshold"] is False
    assert size["exceeds_word_threshold"] is False
    penalty, msg = compute_bloat_penalty(
        size, skills_present=False, delegates_to_skills=False
    )
    assert penalty == 0
    assert msg is None


def test_threshold_boundary_lines() -> None:
    """Files at exactly the line threshold are not penalized; one more is."""
    boundary_text = "Line\n" * SIZE_THRESHOLD_LINES
    size = compute_size_metrics(boundary_text)
    assert size["line_count"] == SIZE_THRESHOLD_LINES
    assert size["exceeds_line_threshold"] is False

    over_text = "Line\n" * (SIZE_THRESHOLD_LINES + 1)
    over_size = compute_size_metrics(over_text)
    assert over_size["exceeds_line_threshold"] is True
    penalty, _ = compute_bloat_penalty(
        over_size, skills_present=False, delegates_to_skills=False
    )
    assert penalty == 1


def test_penalty_curve_by_lines() -> None:
    """One point per SIZE_LINES_PER_POINT lines past 200, capped at SIZE_MAX_PENALTY."""
    def lines_penalty(n: int) -> int:
        size = compute_size_metrics("x\n" * n)
        return compute_bloat_penalty(size, False, False)[0]

    assert lines_penalty(200) == 0
    assert lines_penalty(210) == 1
    assert lines_penalty(300) == 10
    assert lines_penalty(400) == 20
    assert lines_penalty(500) == SIZE_MAX_PENALTY
    assert lines_penalty(1200) == SIZE_MAX_PENALTY


def test_penalty_curve_by_words() -> None:
    """Words follow the same curve from 2400 words, one point per 120."""
    def words_penalty(n: int) -> int:
        # Few lines, many words: isolates the word-count metric.
        size = compute_size_metrics(" ".join(["word"] * n))
        return compute_bloat_penalty(size, False, False)[0]

    assert words_penalty(2400) == 0
    assert words_penalty(2401) == 1
    assert words_penalty(3600) == 10
    assert words_penalty(9000) == SIZE_MAX_PENALTY


def test_penalty_takes_higher_of_two_metrics() -> None:
    """When both metrics exceed, the higher penalty wins."""
    # 250 lines (-5 by lines) but 4791 words (-20 by words) -> expect -20.
    text = ("word " * 19 + "word\n") * 239 + "x\n" * 11
    size = compute_size_metrics(text)
    assert size["line_count"] == 250
    assert size["word_count"] == 4791
    penalty, _ = compute_bloat_penalty(size, False, False)
    assert penalty == 20


# --- Size grade cap -----------------------------------------------------------

# Earns well over the B ceiling before size: verifiable outcomes, tradeoffs,
# directives and existing-unverified paths (repo=None counts them all).
_RICH = (
    "Always run the tests. Never skip lint. Prefer small PRs. Must pass CI. Use `uv`.\n"
    "Tradeoff: speed over coverage because of the deadline, instead of waiting.\n"
    "Success criteria: tests pass. Verify with `pytest -q`.\n"
    "Paths: `src/a.py` `src/b.py` `src/c.py` `src/d.py` `src/e.py`.\n"
)


def _padded(lines: int) -> str:
    return _RICH + "x\n" * (lines - _RICH.count("\n"))


def test_cap_constants_are_twice_the_curve_start() -> None:
    assert SIZE_GRADE_CAP_LINES == 2 * SIZE_THRESHOLD_LINES
    assert SIZE_GRADE_CAP_WORDS == 2 * SIZE_THRESHOLD_WORDS


@pytest.mark.parametrize("skills_present", [False, True])
def test_grade_cap_by_lines(skills_present: bool) -> None:
    at = grade_instructions(_padded(SIZE_GRADE_CAP_LINES), 1, skills_present=skills_present,
                            delegates_to_skills=skills_present, repo=None)
    over = grade_instructions(_padded(SIZE_GRADE_CAP_LINES + 1), 1, skills_present=skills_present,
                              delegates_to_skills=skills_present, repo=None)
    assert at.subscores["size_grade_capped"] == 0
    assert over.subscores["size_grade_capped"] == 1
    assert over.score <= SIZE_GRADE_CAP_SCORE
    assert over.grade == "B" or over.score < 50


def test_grade_cap_by_words_with_skills() -> None:
    """A short file of very long lines is capped on words, skills or not."""
    long_words = _RICH + " ".join(["word"] * (SIZE_GRADE_CAP_WORDS + 1)) + "\n"
    grade = grade_instructions(long_words, 1, skills_present=True, repo=None)
    assert grade.subscores["line_count"] < SIZE_GRADE_CAP_LINES
    assert grade.subscores["size_grade_capped"] == 1
    assert grade.score <= SIZE_GRADE_CAP_SCORE


def test_cap_binds_when_the_score_would_be_higher(tmp_path: Path, fixtures_dir: Path) -> None:
    """The cap lowers a score above B and leaves a lower one alone."""
    root = tmp_path / "repo"
    shutil.copytree(fixtures_dir / "instruction_grading" / "repo", root)
    commands = "`npm test` `make check` `make fmt` `make lint`\n"
    rich = commands + _RICH * 3 + " ".join(["word"] * SIZE_GRADE_CAP_WORDS) + "\n"
    capped = grade_instructions(rich, 1, skills_present=True, repo=build_repo_context(root))
    assert capped.subscores["verified_commands"] == 4
    assert capped.subscores["size_grade_capped"] == 1
    assert capped.subscores["score_before_penalties"] - capped.subscores["bloat_penalty"] > SIZE_GRADE_CAP_SCORE
    assert capped.score == SIZE_GRADE_CAP_SCORE
    assert capped.grade == "B"

    plain = "x\n" * (SIZE_GRADE_CAP_LINES + 1)
    low = grade_instructions(plain, 1, repo=None)
    assert low.subscores["size_grade_capped"] == 1
    assert low.score < SIZE_GRADE_CAP_SCORE


def test_small_file_is_never_capped() -> None:
    grade = grade_instructions(_RICH, 1, repo=None)
    assert grade.subscores["size_grade_capped"] == 0
