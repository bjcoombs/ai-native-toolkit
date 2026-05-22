"""Heuristic agent-instructions grader.

Filename-agnostic. Scores any agent instruction file - CLAUDE.md, AGENTS.md,
GEMINI.md, .cursorrules, .github/copilot-instructions.md - on signals that
correlate with usefulness to an LLM contributor:

    positive_directives: "Use X", "Prefer Y", "Default to Z" (positive framing)
    tradeoff_phrases:    "because", "over X", "instead of", "rather than"
    path_references:     file paths like src/foo/bar.py, tests/..., etc.
    verifiable_outcomes: "Working if", "verify:", "success criteria"
    freshness:           days since last git modification

No LLM calls. Pure regex + arithmetic. Deterministic.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


POSITIVE_DIRECTIVE_PATTERNS = [
    r"\bUse\b",
    r"\bPrefer\b",
    r"\bChoose\b",
    r"\bDefault to\b",
    r"\bMatch\b",
    r"\bAdd\b",
    r"\bRun\b",
]

TRADEOFF_PATTERNS = [
    r"\bbecause\b",
    r"\bover\s+\w+",
    r"\binstead of\b",
    r"\brather than\b",
    r"\btradeoff\b",
]

PATH_PATTERN = re.compile(
    r"`[^`]*[/\\][^`\s]+`"          # backtick-wrapped paths
    r"|(?:^|\s)[\w./-]+/[\w./-]+"   # bare paths with at least one slash
)

VERIFIABLE_PATTERNS = [
    r"\bworking if\b",
    r"\bverify:\b",
    r"\bsuccess criteria\b",
    r"\bacceptance criteria\b",
]


@dataclass
class Grade:
    score: int
    grade: str
    subscores: dict[str, int] = field(default_factory=dict)


def _count(text: str, patterns: list[str]) -> int:
    total = 0
    for p in patterns:
        total += len(re.findall(p, text, re.IGNORECASE))
    return total


def count_positive_directives(text: str) -> int:
    return _count(text, POSITIVE_DIRECTIVE_PATTERNS)


def count_tradeoff_phrases(text: str) -> int:
    return _count(text, TRADEOFF_PATTERNS)


def count_path_references(text: str) -> int:
    return len(PATH_PATTERN.findall(text))


def count_verifiable_outcomes(text: str) -> int:
    return _count(text, VERIFIABLE_PATTERNS)


def _letter_grade(score: int) -> str:
    if score >= 80:
        return "A"
    if score >= 70:
        return "A-"
    if score >= 60:
        return "B+"
    if score >= 50:
        return "B"
    if score >= 40:
        return "C"
    if score >= 25:
        return "D"
    return "F"


def grade_instructions(text: str, freshness_days: int) -> Grade:
    """Score an agent instruction file (CLAUDE.md / AGENTS.md / GEMINI.md / etc.) and return a Grade.

    Scoring weights (max 100):
        positive_directives:  3 points each, capped at 30
        tradeoff_phrases:     5 points each, capped at 25
        path_references:      3 points each, capped at 20
        verifiable_outcomes:  10 points each, capped at 15
        freshness penalty:    -10 if > 365 days, -5 if > 180, 0 otherwise
                              +10 baseline if file has any content
    """
    if not text.strip():
        return Grade(score=0, grade="F", subscores={})

    sub = {
        "positive_directives": count_positive_directives(text),
        "tradeoff_phrases": count_tradeoff_phrases(text),
        "path_references": count_path_references(text),
        "verifiable_outcomes": count_verifiable_outcomes(text),
    }

    score = 10  # baseline for non-empty content
    score += min(sub["positive_directives"] * 3, 30)
    score += min(sub["tradeoff_phrases"] * 5, 25)
    score += min(sub["path_references"] * 3, 20)
    score += min(sub["verifiable_outcomes"] * 10, 15)

    if freshness_days > 365:
        score -= 10
    elif freshness_days > 180:
        score -= 5

    score = max(0, min(score, 100))
    return Grade(score=score, grade=_letter_grade(score), subscores=sub)
