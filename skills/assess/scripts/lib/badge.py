"""Shields.io endpoint badge for the .assess wiki.

Renders ``.assess/badge.json`` in the `shields.io endpoint schema
<https://shields.io/badges/endpoint-badge>`_ so a README can embed a live
AI-readiness badge with zero infrastructure::

    ![AI-readiness](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/<owner>/<repo>/<branch>/.assess/badge.json)

Two producers, one default:

- ``fallback_badge`` - the deterministic default ("2 findings · 0 stale
  markers"), the badge ``assess_core.py`` always writes to ``badge.json``. Its
  message is a pure function of measured run data - never an LLM-authored score
  - and ``assess_core`` stamps a ``link`` funnelling a badge-clicker to
  ``assess-report.md``, where the LLM-derived grade lives.
- ``score_badge`` - the headline form ("7.0/8 · AI-Native"), derived from the
  LLM layered score. It is the in-report headline only; it is *not* written to
  ``badge.json``, so the shipped badge never claims a grade a deterministic run
  cannot reproduce.

A badge is a self-description, so it inherits the truth-pressure rule: the
shipped badge's colours and messages are pure functions of run data (tested
thresholds, no judgement), and it only ever claims what the producing run
measured. The LLM-derived grade is one click away in the report, not baked into
a badge that would need re-earning on every scoring run.
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


# Maturity ladder: the renormalised fraction (score / denominator) mapped to a
# named tier. The top tier's floor is the same 0.875 that opens
# ``_SCORE_COLOR_RATIOS`` (the brightgreen band where "AI-Native" lands); the
# finer tiers below follow the scoring ladder documented in
# ``agents/assess-layer-scorer.md`` (>=0.625 Solid, >=0.375 Basic, else Not
# Ready), so the label finalize accepts is exactly the label the scorer was told
# to emit from the same fraction. Ordered, first match wins - the single source
# of truth for "what tier does this score earn?" that assess_finalize's
# consistency invariant reconciles the LLM-supplied ``maturity_label`` against.
_MATURITY_BANDS: list[tuple[float, str]] = [
    (0.875, "AI-Native"),
    (0.625, "Solid"),
    (0.375, "Basic"),
    (0.0, "Not Ready"),
]


def maturity_band(score: float, denominator: float = 8) -> str:
    """The canonical maturity tier a score earns over its denominator.

    Derived from the same fraction the badge colour uses; the tier names come
    from the documented scoring ladder. Used by ``assess_finalize`` to reject a
    ``maturity_label`` that overstates (or understates) the score band.
    """
    ratio = (score / denominator) if denominator else 0.0
    for floor, label in _MATURITY_BANDS:
        if ratio >= floor:
            return label
    return "Not Ready"


def _scoped_label(scope: str | None) -> str:
    """The badge label, suffixed with the scope for a `/assess <path>` run.

    ``scope`` is the repo-relative subtree (e.g. ``services/api``). None (a
    whole-repo run) yields the bare ``LABEL`` so default output is unchanged.
    """
    return f"{LABEL} ({scope})" if scope else LABEL


def score_badge(
    score: float, maturity_label: str, denominator: int = 8,
    run_id: str | None = None, scope: str | None = None,
) -> dict[str, Any]:
    """The in-report headline form: layered score + maturity label.

    No longer written to ``badge.json`` - the shipped badge is always the
    deterministic ``fallback_badge`` (which links to the report). This is the
    LLM-derived grade that appears inside ``assess-report.md``.

    ``denominator`` is 8 for a software repo (the display ceiling) and the
    count of applicable layers for a knowledge base (issue #224), so the badge
    reads e.g. ``2.5/3 · Knowledge Base · Solid`` instead of a misleading
    ``2.5/8``.

    ``run_id`` (when supplied) is stamped as a non-rendering provenance field so
    the badge traces back to the run that produced it. shields.io ignores keys
    it doesn't recognise, so the extra field never changes what the badge shows.

    ``scope`` (the repo-relative subtree) suffixes the label for a
    ``/assess <path>`` monorepo run so the badge names what it measured; None
    keeps the bare label.
    """
    badge = {
        "schemaVersion": 1,
        "label": _scoped_label(scope),
        "message": f"{score}/{denominator} · {maturity_label}",
        "color": score_color(score, denominator),
    }
    if run_id is not None:
        badge["run_id"] = run_id
    return badge


def fallback_badge(
    concern_count: int, stale_markers: int, run_id: str | None = None,
    scope: str | None = None,
) -> dict[str, Any]:
    """The default shipped badge: a deterministic, always-written self-description.

    ``assess_core.py`` writes this to ``badge.json`` on every run and stamps a
    ``link`` to ``assess-report.md``; the LLM-derived score lives in the report,
    not the badge.

    ``concern_count`` is the number of derived findings with non-empty paths
    (``refactor_boundary`` excluded - it is the positive finding);
    ``stale_markers`` is ``promissory_markers.total_stale`` (0 when the scan
    was unavailable - the message stays truthful because it only counts what
    was measured). ``run_id`` (when supplied) is stamped as a non-rendering
    provenance field, exactly as in ``score_badge``. ``scope`` suffixes the
    label for a ``/assess <path>`` run.
    """
    badge = {
        "schemaVersion": 1,
        "label": _scoped_label(scope),
        "message": f"{concern_count} findings · {stale_markers} stale markers",
        "color": (
            "green"
            if concern_count == 0 and stale_markers == 0
            else "yellow"
            if concern_count <= 2
            else "orange"
        ),
    }
    if run_id is not None:
        badge["run_id"] = run_id
    return badge


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
