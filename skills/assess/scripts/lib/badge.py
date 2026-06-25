"""Shields.io endpoint badge for the .assess wiki.

Renders ``.assess/badge.json`` in the `shields.io endpoint schema
<https://shields.io/badges/endpoint-badge>`_ so a README can embed a live
AI-readiness badge with zero infrastructure::

    ![AI-readiness](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/<owner>/<repo>/<branch>/.assess/badge.json)

Two producers, one honest-degrade ladder:

- ``score_badge`` - the headline form ("7.0/8 · AI-Native"), written by
  ``assess_finalize.py`` from the LLM-derived layered score. Always overwrites.
- ``fallback_badge`` - the deterministic form ("2 findings · 0 stale markers"),
  written by ``assess_core.py`` only when no badge exists yet, so a repo that
  has never run the interactive scoring (a gate-only consumer) still gets a
  truthful badge instead of a score it never earned - and an existing score
  badge is never downgraded by a deterministic-only run.

A badge is a self-description, so it inherits the truth-pressure rule: colours
and messages are pure functions of run data (tested thresholds, no judgement),
and the badge only ever claims what the producing run measured.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

BADGE_FILENAME = "badge.json"
LABEL = "AI-readiness"

# Score -> shields colour, expressed as a *fraction of the denominator* so the
# bands hold whether the score is over the full 0-8 software scale or a
# renormalised knowledge-base denominator (issue #224). Ordered thresholds,
# first match wins; the top band starts where "AI-Native" lands (7/8 = 0.875),
# the bottom is reserved for near-zero scaffolding. For the default
# denominator 8 these fractions reproduce the original absolute thresholds
# exactly (0.875·8 = 7.0, 0.6875·8 = 5.5, ...).
_SCORE_COLOR_RATIOS: list[tuple[float, str]] = [
    (0.875, "brightgreen"),
    (0.6875, "green"),
    (0.5, "yellowgreen"),
    (0.3125, "yellow"),
    (0.125, "orange"),
    (0.0, "red"),
]


def score_color(score: float, denominator: float = 8) -> str:
    """Deterministic colour band for a layered score over its denominator."""
    ratio = (score / denominator) if denominator else 0.0
    for floor, color in _SCORE_COLOR_RATIOS:
        if ratio >= floor:
            return color
    return "red"


def score_badge(
    score: float, maturity_label: str, denominator: int = 8
) -> dict[str, Any]:
    """The headline badge: layered score + maturity label.

    ``denominator`` is 8 for a software repo (the display ceiling) and the
    count of applicable layers for a knowledge base (issue #224), so the badge
    reads e.g. ``2.5/3 · Knowledge Base · Solid`` instead of a misleading
    ``2.5/8``.
    """
    return {
        "schemaVersion": 1,
        "label": LABEL,
        "message": f"{score}/{denominator} · {maturity_label}",
        "color": score_color(score, denominator),
    }


def fallback_badge(concern_count: int, stale_markers: int) -> dict[str, Any]:
    """Deterministic badge for repos with no LLM-scored run.

    ``concern_count`` is the number of derived findings with non-empty paths
    (``refactor_boundary`` excluded - it is the positive finding);
    ``stale_markers`` is ``promissory_markers.total_stale`` (0 when the scan
    was unavailable - the message stays truthful because it only counts what
    was measured).
    """
    return {
        "schemaVersion": 1,
        "label": LABEL,
        "message": f"{concern_count} findings · {stale_markers} stale markers",
        "color": (
            "green"
            if concern_count == 0 and stale_markers == 0
            else "yellow"
            if concern_count <= 2
            else "orange"
        ),
    }


def concern_count_from_findings(derived_findings: list[dict]) -> int:
    """Count negative findings that actually fired (non-empty paths)."""
    return sum(
        1
        for f in derived_findings
        if isinstance(f, dict)
        and f.get("name") != "refactor_boundary"
        and f.get("paths")
    )


def write_badge(assess_dir: Path, badge: dict[str, Any]) -> None:
    (assess_dir / BADGE_FILENAME).write_text(
        json.dumps(badge, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def badge_exists(assess_dir: Path) -> bool:
    return (assess_dir / BADGE_FILENAME).exists()
