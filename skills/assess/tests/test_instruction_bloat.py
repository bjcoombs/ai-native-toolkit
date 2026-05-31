"""Tests for the instruction-file bloat penalty and skills-delegation credit.

The core thesis (see `test_monolith_scores_strictly_below_lean_plus_skills`):
an oversized monolithic instruction file that is NOT factored into on-demand
skills scores STRICTLY BELOW an equivalent lean-file-plus-skills repo. The
monolith is penalized, not merely annotated. Conservative thresholds ensure
small/legitimate instruction files are never penalized.
"""
from __future__ import annotations

from pathlib import Path

from lib.agent_instructions_grader import (
    SIZE_THRESHOLD_LINES,
    compute_bloat_penalty,
    compute_size_metrics,
    detect_skills_delegation,
    detect_skills_dir,
    grade_instructions,
)


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


def test_oversized_but_factored_into_skills_no_penalty(fixtures_dir: Path) -> None:
    """An oversized hub file is NOT penalized when skills are present - it may
    be a hub that points to skills (progressive disclosure)."""
    text = (fixtures_dir / "monolithic_instructions.md").read_text()
    size = compute_size_metrics(text)
    assert size["exceeds_line_threshold"] is True

    # skills_present short-circuits the penalty even for an oversized file.
    penalty, msg = compute_bloat_penalty(
        size, skills_present=True, delegates_to_skills=False
    )
    assert penalty == 0
    assert msg is None

    # delegation pointers in the text alone also suppress the penalty.
    penalty2, _ = compute_bloat_penalty(
        size, skills_present=False, delegates_to_skills=True
    )
    assert penalty2 == 0


def test_monolith_scores_strictly_below_lean_plus_skills(fixtures_dir: Path) -> None:
    """REGRESSION TEST (core thesis): equivalent guidance scores STRICTLY LOWER
    as an unfactored monolith than as a lean-file-plus-skills repo.

    Grading the same guidance two ways isolates the penalty as the sole
    difference: one repo inlines everything (no skills factoring), the other
    factors it into on-demand skills. The monolith is penalized, not merely
    annotated, so its score is strictly lower.
    """
    text = (fixtures_dir / "monolithic_instructions.md").read_text()

    monolith_grade = grade_instructions(
        text, freshness_days=10, skills_present=False, delegates_to_skills=False
    )
    factored_grade = grade_instructions(
        text, freshness_days=10, skills_present=True
    )

    assert monolith_grade.subscores["bloat_penalty"] >= 5
    assert factored_grade.subscores["bloat_penalty"] == 0
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
        text, freshness_days=10, skills_present=skills["skills_dirs_present"]
    )
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
    assert penalty == 5


def test_penalty_tiers_by_lines() -> None:
    """Penalty escalates with overage: -5 / -10 / -15."""
    def lines_penalty(n: int) -> int:
        size = compute_size_metrics("x\n" * n)
        return compute_bloat_penalty(size, False, False)[0]

    assert lines_penalty(600) == 5    # 500-750
    assert lines_penalty(800) == 10   # 750-1000
    assert lines_penalty(1200) == 15  # 1000+


def test_penalty_tiers_by_words() -> None:
    """Word-count tiers mirror the line tiers at 3000/4500/6000."""
    def words_penalty(n: int) -> int:
        # Few lines, many words: isolates the word-count metric.
        size = compute_size_metrics(" ".join(["word"] * n))
        return compute_bloat_penalty(size, False, False)[0]

    assert words_penalty(3500) == 5    # 3000-4500
    assert words_penalty(5000) == 10   # 4500-6000
    assert words_penalty(7000) == 15   # 6000+


def test_penalty_takes_higher_of_two_metrics() -> None:
    """When both metrics exceed, the higher penalty wins."""
    # 600 lines (-5 by lines) but 7000 words (-15 by words) -> expect -15.
    text = ("word " * 12 + "\n") * 600
    size = compute_size_metrics(text)
    assert size["line_count"] > SIZE_THRESHOLD_LINES
    assert size["word_count"] > 6000
    penalty, _ = compute_bloat_penalty(size, False, False)
    assert penalty == 15
