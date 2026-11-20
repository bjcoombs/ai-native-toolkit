"""Tests for the instruction-file score on always-on size and share (issue #560).

The grader splits the file on its headings (``lib.instruction_placement``) and
charges two kinds of line: always-on lines past 200 when they are more than
half the file, and fetch-on-need lines that a pointer would replace. This
replaced the #517 whole-file curve, its skills-delegation halving and its word
leg. The core thesis (``test_600_line_file_scores_below_the_same_file_with_a_pointer``):
a long file whose bulk belongs in a skill scores below the same file with that
section replaced by a two-line pointer, although the pointer version is all
always-on.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from lib.agent_instructions_grader import (
    ALWAYS_ON_GRADE_CAP_LINES,
    ALWAYS_ON_MAX_PENALTY,
    RELOCATABLE_MAX_PENALTY,
    SIZE_GRADE_CAP_SCORE,
    grade_instructions,
    placement_penalty,
)
from lib.instruction_content import build_repo_context
from lib.instruction_files import grade_instruction_files
from lib.instruction_placement import (
    ALWAYS_ON_FLAG_LINES,
    PlacementFile,
    analyse_file,
    detect_skills_dir,
    scan_instruction_placement,
    skill_texts,
)
from test_instruction_placement import _filler, _kubernetes_section, _rules_block, _write


def _record(always_on: int, fetch: int, flagged: bool) -> PlacementFile:
    total = always_on + fetch
    return {
        "path": "CLAUDE.md", "kind": "instruction", "total_lines": total,
        "always_on_lines": always_on, "always_on_share": round(always_on / total, 2),
        "fetch_on_need_lines": fetch, "unclassified_lines": 0, "sections_total": 1,
        "flagged": flagged, "relocatable": [], "relocatable_total": 0,
    }


def _pointer() -> str:
    return "## Kubernetes\nSee the `kubernetes-commands` skill.\n"


# --- the #419 success criterion ------------------------------------------------------


def test_600_line_file_scores_below_the_same_file_with_a_pointer(tmp_path: Path) -> None:
    """A 600-line CLAUDE.md with a 40-line identity and rules block, a 300-line
    Kubernetes section of fenced commands and a skills directory scores lower
    than the same file with that section replaced by a two-line pointer."""
    _write(tmp_path, ".claude/skills/deploy/SKILL.md", "---\nname: deploy\n---\nDeploy.\n")
    skills = skill_texts(tmp_path)
    assert list(skills) == [".claude/skills/deploy/SKILL.md"]
    full = _rules_block() + _kubernetes_section(300) + _filler(260)
    pointed = _rules_block() + _pointer() + _filler(260)
    assert len(full.splitlines()) == 600

    with_section = grade_instructions(full, 1, skills=skills, repo=None, path="CLAUDE.md")
    with_pointer = grade_instructions(pointed, 1, skills=skills, repo=None, path="CLAUDE.md")

    sub = with_section.subscores
    assert (sub["always_on_lines"], sub["fetch_on_need_lines"], sub["always_on_pct"]) == (300, 300, 50)
    assert sub["always_on_flagged"] == 0  # half the file, not more
    assert sub["relocatable_penalty"] == RELOCATABLE_MAX_PENALTY
    ptr = with_pointer.subscores
    assert (ptr["always_on_lines"], ptr["fetch_on_need_lines"], ptr["always_on_flagged"]) == (302, 0, 1)
    assert ptr["always_on_penalty"] == 11
    assert with_section.score < with_pointer.score


def test_small_file_that_is_mostly_reference_pays_for_it() -> None:
    """Share, not size: a 120-line file whose bulk is one tool's command
    reference is not flagged (under 200 always-on lines) but pays for the
    lines a pointer would replace."""
    text = _rules_block() + _kubernetes_section(80)
    assert len(text.splitlines()) == 120
    grade = grade_instructions(text, 1, repo=None)
    pointed = grade_instructions(_rules_block() + _pointer(), 1, repo=None)
    assert grade.subscores["always_on_flagged"] == 0
    assert grade.subscores["fetch_on_need_lines"] == 80
    assert grade.subscores["relocatable_penalty"] == 8
    assert pointed.subscores["bloat_penalty"] == 0
    assert grade.score < pointed.score


def test_section_a_skill_already_holds_costs_points(tmp_path: Path) -> None:
    """A section duplicating an existing SKILL.md is fetch-on-need only when the
    grader is given the skills, as the placement block is."""
    shared = "\n".join(f"Step {i}: rotate the signing key for tenant {i} in the vault."
                       for i in range(20))
    _write(tmp_path, "skills/rotate/SKILL.md", f"---\nname: rotate\n---\n{shared}\n")
    text = "# Project\n\n## Key rotation\n\n" + shared + "\n"
    without = grade_instructions(text, 1, repo=None)
    with_skills = grade_instructions(text, 1, skills=skill_texts(tmp_path), repo=None)
    assert without.subscores["fetch_on_need_lines"] == 0
    assert with_skills.subscores["fetch_on_need_lines"] == 22
    assert with_skills.subscores["relocatable_penalty"] == 3
    assert with_skills.score < without.score


# --- placement_penalty -------------------------------------------------------------


@pytest.mark.parametrize(("always_on", "penalty"), [
    (200, 0), (201, 1), (210, 1), (211, 2), (300, 10), (400, 20), (500, ALWAYS_ON_MAX_PENALTY),
    (1200, ALWAYS_ON_MAX_PENALTY),
])
def test_always_on_curve_when_flagged(always_on: int, penalty: int) -> None:
    result = placement_penalty(_record(always_on, 0, flagged=always_on > ALWAYS_ON_FLAG_LINES))
    assert result["always_on_penalty"] == penalty
    assert result["relocatable_penalty"] == 0


def test_always_on_lines_cost_nothing_when_not_flagged() -> None:
    """Half the file or less always-on: the size term is off and the share term
    (the fetch-on-need lines) carries the penalty."""
    result = placement_penalty(_record(300, 300, flagged=False))
    assert result["always_on_penalty"] == 0
    assert result["relocatable_penalty"] == RELOCATABLE_MAX_PENALTY


@pytest.mark.parametrize(("fetch", "penalty"), [
    (0, 0), (1, 1), (10, 1), (11, 2), (150, 15), (300, RELOCATABLE_MAX_PENALTY),
])
def test_relocatable_curve(fetch: int, penalty: int) -> None:
    assert placement_penalty(_record(50, fetch, flagged=False))["relocatable_penalty"] == penalty


def test_cap_needs_a_flagged_file_past_the_cap_lines() -> None:
    assert ALWAYS_ON_GRADE_CAP_LINES == 2 * ALWAYS_ON_FLAG_LINES
    assert placement_penalty(_record(ALWAYS_ON_GRADE_CAP_LINES, 0, True))["capped"] is False
    assert placement_penalty(_record(ALWAYS_ON_GRADE_CAP_LINES + 1, 0, True))["capped"] is True
    assert placement_penalty(_record(ALWAYS_ON_GRADE_CAP_LINES + 1, 500, False))["capped"] is False


# --- grade_instructions ------------------------------------------------------------


def test_small_file_no_penalty() -> None:
    grade = grade_instructions("Use bcrypt for password hashing.\n" * 100, 1, repo=None)
    assert grade.subscores["bloat_penalty"] == 0
    assert grade.subscores["size_grade_capped"] == 0


def test_bloat_penalty_is_the_sum_of_both_terms() -> None:
    text = "## Notes\n" + "x\n" * 300
    grade = grade_instructions(text, 1, repo=None)
    sub = grade.subscores
    assert sub["always_on_flagged"] == 1
    assert sub["bloat_penalty"] == sub["always_on_penalty"] + sub["relocatable_penalty"] == 11


def test_word_count_alone_no_longer_costs_points() -> None:
    """The #517 word leg is gone: the split is line-based."""
    grade = grade_instructions(" ".join(["word"] * 9000) + "\n", 1, repo=None)
    assert grade.subscores["word_count"] == 9000
    assert grade.subscores["bloat_penalty"] == 0


def test_monolith_fixture_is_flagged_and_capped(fixtures_dir: Path) -> None:
    """The 625-line monolith is all always-on: flagged, the full size penalty,
    and capped at B whether or not skills sit beside it."""
    text = (fixtures_dir / "monolithic_instructions.md").read_text()
    grade = grade_instructions(text, 10, repo=None)
    assert grade.subscores["always_on_flagged"] == 1
    assert grade.subscores["always_on_penalty"] == ALWAYS_ON_MAX_PENALTY
    assert grade.subscores["size_grade_capped"] == 1
    assert grade.score <= SIZE_GRADE_CAP_SCORE


def test_lean_fixture_repo_detects_skills_and_avoids_penalty(fixtures_dir: Path) -> None:
    repo = fixtures_dir / "lean_with_skills"
    text = (repo / "CLAUDE.md").read_text()
    skills = detect_skills_dir(repo)
    assert skills["skills_dirs_present"] is True
    assert skills["skills_count"] == 2
    assert any("java-conventions" in f for f in skills["skill_files"])
    grade = grade_instructions(text, 10, skills=skill_texts(repo), repo=None)
    assert grade.subscores["bloat_penalty"] == 0


def test_graceful_no_skills_dir(tmp_path: Path) -> None:
    skills = detect_skills_dir(tmp_path)
    assert skills == {"skills_dirs_present": False, "skills_dirs": [],
                      "skills_count": 0, "skill_files": []}
    assert skill_texts(tmp_path) == {}


# Earns well over the B ceiling before size: verifiable outcomes, tradeoffs,
# directives and existing-unverified paths (repo=None counts them all).
_RICH = (
    "Always run the tests. Never skip lint. Prefer small PRs. Must pass CI. Use `uv`.\n"
    "Tradeoff: speed over coverage because of the deadline, instead of waiting.\n"
    "Success criteria: tests pass. Verify with `pytest -q`.\n"
    "Paths: `src/a.py` `src/b.py` `src/c.py` `src/d.py` `src/e.py`.\n"
)


def test_cap_binds_when_the_score_would_be_higher(tmp_path: Path, fixtures_dir: Path) -> None:
    """The cap lowers a score above B and leaves a lower one alone."""
    root = tmp_path / "repo"
    shutil.copytree(fixtures_dir / "instruction_grading" / "repo", root)
    commands = "`npm test` `make check` `make fmt` `make lint`\n"
    rich = commands + _RICH * 3 + "x\n" * ALWAYS_ON_GRADE_CAP_LINES
    capped = grade_instructions(rich, 1, repo=build_repo_context(root))
    assert capped.subscores["verified_commands"] == 4
    assert capped.subscores["size_grade_capped"] == 1
    assert capped.subscores["score_before_penalties"] - capped.subscores["bloat_penalty"] > SIZE_GRADE_CAP_SCORE
    assert capped.score == SIZE_GRADE_CAP_SCORE
    assert capped.grade == "B"

    low = grade_instructions("x\n" * (ALWAYS_ON_GRADE_CAP_LINES + 1), 1, repo=None)
    assert low.subscores["size_grade_capped"] == 1
    assert low.score < SIZE_GRADE_CAP_SCORE


def test_small_file_is_never_capped() -> None:
    assert grade_instructions(_RICH, 1, repo=None).subscores["size_grade_capped"] == 0


# --- one computation: the grade and the block agree ---------------------------------


def test_graded_file_matches_its_placement_record(git_repo) -> None:
    """The grade's always-on numbers are the instruction_placement record's."""
    repo, commit = git_repo
    _write(repo, "CLAUDE.md", _rules_block() + _kubernetes_section(300) + _filler(260))
    shared = "\n".join(f"Step {i}: rotate the signing key for tenant {i} in the vault."
                       for i in range(20))
    _write(repo, ".claude/skills/rotate/SKILL.md", f"---\nname: rotate\n---\n{shared}\n")
    _write(repo, "AGENTS.md", "# Agents\n\n## Key rotation\n\n" + shared + "\n")
    commit("init")
    files = grade_instruction_files(repo)[0]
    entries = {rel: {"present": True} for rel in files}
    block = scan_instruction_placement(repo, entries, None)
    assert {r["path"] for r in block["files"]} == {"CLAUDE.md", "AGENTS.md"}
    for record in block["files"]:
        sub = files[record["path"]]["subscores"]
        assert sub["always_on_lines"] == record["always_on_lines"]
        assert sub["fetch_on_need_lines"] == record["fetch_on_need_lines"]
        assert sub["always_on_flagged"] == int(record["flagged"])
        assert sub["always_on_pct"] == round(record["always_on_share"] * 100)
    assert files["AGENTS.md"]["subscores"]["fetch_on_need_lines"] > 0


def test_analyse_file_is_the_graders_split() -> None:
    text = _rules_block() + _kubernetes_section(300) + _filler(260)
    record = analyse_file(text, "CLAUDE.md", "instruction", {})
    sub = grade_instructions(text, 1, repo=None, path="CLAUDE.md").subscores
    assert sub["always_on_lines"] == record["always_on_lines"]
    assert sub["fetch_on_need_lines"] == record["fetch_on_need_lines"]
