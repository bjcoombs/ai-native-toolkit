"""The facts behind the report's top card, each computed in one place (#1252).

Every place that states where a repository stands reads these functions: the top card
(``lib/top_card.py``), the badge message and the ``log.md`` readiness line
(``headline.summary``, worded by ``lib/headline.py``). None of them words anything; the
wording lives with the card. Each takes a ``LevelResult`` (``lib/level_tally.py``) and the
maturity model that produced it:

- ``blocking_gates(level)``: the gate blockers of the next Step, in Step order; ``[]`` at
  the top Step, for a model without Steps, or when ``level`` is not a level result;
- ``top_step(model)``: the model's highest Step; None for a model without Steps;
- ``step_gate_ids(model, step)``: the gate ids a Step lists, in order (a Step lists every
  gate below it too, so the top Step lists them all);
- ``step_name(model, step)``: the Step's name in the model, ``""`` for a Step it does not define;
- ``advisories(level, model)``: the counted conditions measured and not met that no Step
  gate waits on, that is, outside rows 1..``level`` of every gate of every Step. They are
  advice: none of them moves the Step.

``CardFact`` is one line of the report's first screen with the ``fact_id`` that names it:
``lib/top_card.py`` (``card_facts``), ``lib/profiles.py`` (``model_facts``) and
``assess_report.py`` (``report_facts``) each return their lines as facts, and
``tests/test_card_construction.py`` fails a first screen that states one fact twice or holds a
line no fact function wrote (#1253).

Tests: ``tests/test_top_card.py``, ``tests/test_card_construction.py``.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypedDict

from lib.level_result_types import Blocker
from lib.levels import MaturityModel

__all__ = ["CardFact", "advisories", "blocking_gates", "step_gate_ids", "step_name", "top_step"]


class CardFact(TypedDict):
    """One line of the report's first screen and the id of the fact it states."""

    fact_id: str
    text: str


def blocking_gates(level: Any) -> list[Blocker]:
    """The gate blockers of ``level``'s next Step, in Step order."""
    nxt = level.get("next_step") if isinstance(level, Mapping) else None
    blockers = nxt.get("blockers") if isinstance(nxt, Mapping) else None
    if not isinstance(blockers, list):
        return []
    return [b for b in blockers if isinstance(b, Mapping) and b.get("kind") == "gate"]  # type: ignore[misc]


def top_step(model: MaturityModel) -> int | None:
    """The model's highest Step, or None when it defines no Steps."""
    return max((s["step"] for s in model.get("steps", [])), default=None)


def step_gate_ids(model: MaturityModel, step: int) -> list[str]:
    """The gate ids Step ``step`` lists, in order; ``[]`` for a Step the model does not define."""
    found = next((s for s in model.get("steps", []) if s["step"] == step), None)
    return list(dict.fromkeys(g["id"] for g in found["gates"])) if found else []


def step_name(model: MaturityModel, step: int) -> str:
    """The Step's name in the model; ``""`` for a Step it does not define."""
    return next((s["name"] for s in model.get("steps", []) if s["step"] == step), "")


def _gate_conditions(model: MaturityModel) -> set[str]:
    return {f"{g['row']}.{n}" for s in model.get("steps", []) for g in s["gates"]
            for n in range(1, g["level"] + 1)}


def advisories(level: Any, model: MaturityModel) -> int:
    """Counted conditions measured and not met that no Step gate waits on."""
    conditions = level.get("conditions") if isinstance(level, Mapping) else None
    if not isinstance(conditions, list):
        return 0
    gated = _gate_conditions(model)
    return sum(1 for c in conditions if isinstance(c, Mapping) and c.get("counted") is True
               and c.get("state") == "unmet" and c.get("id") not in gated)
