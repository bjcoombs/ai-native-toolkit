"""The exit rule: demote a condition with no measured effect on agent outcomes by its review date (#1086).

A condition enters the maturity model on the #842 discrimination rule and states a
predicted direction and a review date (``predicted`` and ``review``, ``lib/scoring/levels_fields.py``).
This module is the way out. It reads the fleet's effect estimates
(``lib.fleet.condition_effect``) and decides, for each condition with a review date, one
``status``:

- ``not_due``: the review date is after this run's scan date.
- ``gate_exempt``: a Step gate reads the condition (``level_tally.gate_conditions``). It is
  never demoted, so a demotion never moves a Step.
- ``insufficient_evidence``: some measure the prediction names is ``low_confidence`` or has no
  interval. Thin evidence demotes nothing (maintainer triage decision on #1086).
- ``effect_detected``: some measure the prediction names has an interval that excludes 0 on the
  predicted side, or on either side for ``none``.
- ``contrary_effect``: an interval excludes 0 on the side a ``lower_*`` prediction rules out (the
  repositories that meet the condition have the higher share). It is an effect, so the condition
  is not demoted; the status names it so the condition's prediction can be reviewed.
- ``demoted``: every measure the prediction names has enough repositories on each side and an
  interval that includes 0. The condition becomes informational: it still shows, with its
  state, and counts toward nothing (``level_tally``, ``disposition: "demoted_by_exit_rule"``).

The measures a prediction names: ``lower_revert`` reads ``revert_share``, ``lower_rework`` reads
``rework_share``, and ``none`` reads both. A condition with no review date is never listed:
nothing is demoted until a PR sets a date (maintainer decision on #1086, no backfill).

**Who decides.** Only the fleet, from measured outcomes. ``fleet.py --levels`` builds this
record from its own ``condition_effect.json`` (this run plus its comparable history), applies
``demoted`` to every level result of this run by carrying it on the model (``with_demotions``:
repositories, units, tags and re-scored history alike, so they agree), writes
``demotions.json`` to ``--out`` and keeps it in the run history entry
(``fleet_history.OPTIONAL_HISTORY_FILES``), so each demotion is recorded with its evidence and
needs no hand edit. No ``--fleet-config`` key and no ``.assess/config.toml`` key reads or
sets it, so a repository cannot demote its own conditions, and a plain ``/assess`` demotes
nothing.

Document (``demotions.json``)::

    {"demotions_version": 1, "levels_version": str, "scan_date": str,
     "demoted": [condition id, ...],
     "conditions": [{"id": str, "predicted": str, "review": str, "status": str,
                     "evidence": [MeasureEffect, ...]}]}

Counts, shares, dates and condition ids only, as ``condition_effect.json``.

The fleet exit rule (#1086): `decide(model, effects, scan_date)` gives each condition with a
`review` date a status (`not_due`, `gate_exempt`, `insufficient_evidence`,
`effect_detected`, `contrary_effect`, `demoted`) from the measures its `predicted` direction names
(`PREDICTION_MEASURES`), with those measures' effects as evidence; `demoted` lists the ids.
Only `fleet.py` builds it, from its own `condition_effect.json`; `write_demotions` saves
`demotions.json`. Tests: `tests/test_condition_demotion.py`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict, cast

from lib.fleet.condition_effect import ConditionEffectDoc, MeasureEffect
from lib.scoring.level_tally import gate_conditions
from lib.scoring.levels import MaturityModel

DEMOTIONS_FILE = "demotions.json"
DEMOTIONS_VERSION = 1
# The agent_outcome shares each predicted direction is checked on (lib/scoring/levels_fields.py PREDICTIONS).
PREDICTION_MEASURES: dict[str, tuple[str, ...]] = {
    "lower_revert": ("revert_share",),
    "lower_rework": ("rework_share",),
    "none": ("revert_share", "rework_share"),
}
STATUSES = ("not_due", "gate_exempt", "insufficient_evidence", "effect_detected", "contrary_effect", "demoted")


class Demotion(TypedDict):
    id: str
    predicted: str
    review: str
    status: str  # one of STATUSES
    evidence: list[MeasureEffect]


class DemotionsDoc(TypedDict):
    demotions_version: int
    levels_version: str
    scan_date: str
    demoted: list[str]
    conditions: list[Demotion]


def _contrary(effect: MeasureEffect, predicted: str) -> bool:
    """The interval excludes 0 on the side the prediction rules out: a ``lower_*`` condition whose
    met repositories have the higher share. ``none`` predicts no direction, so nothing is contrary."""
    interval = effect["interval"]
    return predicted != "none" and interval is not None and interval[0] > 0.0


def _status(evidence: list[MeasureEffect], predicted: str) -> str:
    if not evidence or any(e["low_confidence"] or e["includes_zero"] is None for e in evidence):
        return "insufficient_evidence"
    if all(e["includes_zero"] for e in evidence):
        return "demoted"
    return "contrary_effect" if any(_contrary(e, predicted) for e in evidence) else "effect_detected"


def decide(model: MaturityModel, effects: ConditionEffectDoc, scan_date: str) -> DemotionsDoc:
    """The exit rule's record for this run: one entry per condition with a review date."""
    gates = gate_conditions(model)
    out: list[Demotion] = []
    for cond in effects["conditions"]:
        predicted, review = cond["predicted"], cond["review"]
        if predicted is None or review is None:
            continue
        measures = PREDICTION_MEASURES[predicted]
        evidence = [e for e in cond["effects"] if e["measure"] in measures]
        if review > scan_date:  # ISO dates compare as text
            status = "not_due"
        elif cond["id"] in gates:
            status = "gate_exempt"
        else:
            status = _status(evidence, predicted)
        out.append(Demotion(id=cond["id"], predicted=predicted, review=review, status=status,
                            evidence=evidence))
    return DemotionsDoc(demotions_version=DEMOTIONS_VERSION, levels_version=model["version"],
                        scan_date=scan_date, demoted=[d["id"] for d in out if d["status"] == "demoted"],
                        conditions=out)


def with_demotions(model: MaturityModel, demoted: list[str]) -> MaturityModel:
    """A copy of ``model`` carrying the exit rule's ``demoted`` ids, which ``level_tally`` reads.
    ``fleet.py`` alone calls it, after deciding, so every result of the run applies the same set."""
    return cast(MaturityModel, {**model, "demoted": list(demoted)})


def write_demotions(out_dir: Path, doc: DemotionsDoc) -> Path:
    path = out_dir / DEMOTIONS_FILE
    path.write_text(json.dumps(doc, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8",
                    newline="\n")
    return path
