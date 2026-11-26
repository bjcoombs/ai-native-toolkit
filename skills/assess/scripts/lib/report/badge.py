"""Shields.io endpoint badge for the .assess wiki.

Renders ``.assess/badge.json`` in the `shields.io endpoint schema
<https://shields.io/badges/endpoint-badge>`_ so a README can embed a live
AI-readiness badge with zero infrastructure::

    ![AI-readiness](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/<owner>/<repo>/<branch>/.assess/badge.json)

The message is the run's deterministic headline summary (``lib/scoring/headline.py``,
#747): the autonomy Step and the verified tally, e.g. ``Step 3 · 4 of 4
measured``. It is a pure function of the commit and the maturity-model version,
never an LLM-authored score, so two runs on one commit write the same message.
``assess_core`` stamps a ``link`` to ``assess-report.md``, where the full
headline and the evidence behind it live.

A badge is a self-description, so it inherits the truth-pressure rule: its
colour and message come from run data alone, and it only claims what the
producing run measured. A run whose level result is unavailable reads "Step not
measured" in grey, never a guessed Step. No colour is red: a Step is a
milestone reached, not a grade.

Shields.io endpoint badge for the wiki (`.assess/badge.json`). The message is the
run's deterministic headline summary (`headline.summary` from `headline.py`, #747:
"Step 3 · 2 practices fully in place · next: coverage gate", or "Step 4 · all gates held";
no tally count, #1083; the practices fully in place lead it, left out at 0, #1334), the colour a pure function of the Step (`step_color`:
blue, blue, green, brightgreen for Steps 1-4, never red; grey with the message
"Step not measured" when the level result is unavailable). `assess_core` writes
it through `write_headline_badge` on every run, stamping a `link` to
`assess-report.md` and the `run_id`, and rewrites it after the opt-in mutation
recount; `assess_finalize` never touches it. The layered "X / 8" score badge and
its band ladder are retired. A measured repository below the first Step of a model whose Step 1
has a gate gets ``BELOW_FIRST_STEP_COLOR`` (Step 1's blue), not the not-measured grey. Tests: `tests/test_badge.py`, `tests/test_headline.py`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lib.scoring.headline import Headline

BADGE_FILENAME = "badge.json"
LABEL = "AI-readiness"

# Step -> shields colour: cool to warm achievement colours, no red (#740).
STEP_COLORS = {1: "blue", 2: "blue", 3: "green", 4: "brightgreen"}
NOT_MEASURED_COLOR = "lightgrey"
# Measured, but below the first Step of a model whose Step 1 has a gate (#889): Step 1's colour,
# never the not-measured grey.
BELOW_FIRST_STEP_COLOR = STEP_COLORS[1]


def step_color(step: int | None) -> str:
    """The badge colour for a Step; grey when the Step was not measured."""
    if step is None:
        return NOT_MEASURED_COLOR
    return STEP_COLORS.get(step, "brightgreen" if step > 4 else "blue")


def _scoped_label(scope: str | None) -> str:
    """The badge label, suffixed with the scope for a `/assess <path>` run.

    ``scope`` is the repo-relative subtree (e.g. ``services/api``). None (a
    whole-repo run) yields the bare ``LABEL`` so default output is unchanged.
    """
    return f"{LABEL} ({scope})" if scope else LABEL


def headline_badge(
    headline: Headline, run_id: str | None = None, scope: str | None = None,
) -> dict[str, Any]:
    """The shipped badge: the headline's Step and verified tally.

    ``run_id`` (when supplied) is stamped as a non-rendering provenance field so
    the badge traces back to the run that produced it; shields.io ignores keys
    it does not recognise. ``scope`` suffixes the label for a ``/assess <path>``
    monorepo run.
    """
    step = headline["step"] if headline["available"] else None
    badge: dict[str, Any] = {
        "schemaVersion": 1,
        "label": _scoped_label(scope),
        "message": headline["summary"],
        "color": BELOW_FIRST_STEP_COLOR if headline["available"] and step is None and headline["next_step"] is not None
        else step_color(step),
    }
    if run_id is not None:
        badge["run_id"] = run_id
    return badge


def write_badge(assess_dir: Path, badge: dict[str, Any]) -> None:
    (assess_dir / BADGE_FILENAME).write_text(
        json.dumps(badge, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def write_headline_badge(
    assess_dir: Path, headline: Headline, run_id: str | None = None,
    scope: str | None = None,
) -> None:
    """Write the deterministic badge from the run's headline, always.

    ``assess_core`` writes it on every run and after the opt-in mutation recount;
    ``assess_finalize`` never overwrites it. The ``link`` funnels a badge-clicker
    to ``assess-report.md``.
    """
    badge = headline_badge(headline, run_id=run_id, scope=scope)
    badge["link"] = "./assess-report.md"
    write_badge(assess_dir, badge)


def badge_exists(assess_dir: Path) -> bool:
    return (assess_dir / BADGE_FILENAME).exists()
