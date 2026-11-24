"""The Step gates and the Step a run lost against the committed baseline, for the CI gate (#1152).

``assess_gate.py``'s opt-in ``fail_on_gate_loss`` check reads these. Everything here only
reads the run context; nothing writes a file or recomputes ``since_last_run``:

- **Lost gates.** ``since_last_run.gates_lost`` (``lib/since_last_run.py``): the gates the
  committed baseline (``.assess/prior-level.json``, ``baseline.held_gates``) held that this
  run does not, in Step order. Empty on a first run, when the block is degraded, or when the
  changes are not known (``gates_lost`` null), so an unknown never reads as a loss.
- **Unmet or unread.** ``split_lost`` sorts each lost gate by its condition state in this
  run's ``level`` block (``level_tally.gate_blockers`` over the default model's Steps): only a
  gate that now reads ``unmet`` is a measured loss. One that reads anything else
  (``not_measured``, an unavailable level result, a model without that gate's condition) is
  unknown, never a loss.
- **The Step.** A Step needs its gates and nothing else (#1083), so the baseline's Step is
  the highest Step whose gates, and every lower Step's, are all in ``held_gates``
  (``baseline_step``). ``step_change`` gives (baseline Step, this run's Step) when this run's
  is lower, else None. A lost Step is always a lost gate, so the Step only words the loss.
- **Confirmed by the baseline.** ``since_last_run`` falls back to an earlier
  ``run-context.json`` left on disk when no baseline is committed; ``held_at_baseline`` keeps
  only the gates the committed ``prior_level.baseline.held_gates`` lists, the only loss that
  can fail the gate.
- **No baseline.** ``no_baseline`` is True on a first run or when the run context carries no
  committed baseline (``prior_level.baseline`` absent): there is nothing to compare with, so
  the gate passes and the log says so.
- **Not compared.** ``not_compared`` gives the reason no gate was compared across maturity
  models (#1150): the ``since_last_run`` model-changed line, or, for a run under a profile or
  ``--levels`` model, that the committed baseline holds the default model's gates only. The
  gate prints it as a warn line instead of "clean snapshot", so a check that compared nothing
  never reads clean.

Tests: ``tests/test_gate_loss.py`` and ``tests/test_assess_gate.py``.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from lib.level_tally import default_model, gate_blockers
from lib.levels import StepDef
from lib.model_change import model_changed_line, model_name
from lib.prior_level import default_level

__all__ = ["baseline_step", "held_at_baseline", "lost_gates", "no_baseline", "not_compared",
           "split_lost", "step_change"]


def not_compared(ctx: Mapping[str, Any]) -> str | None:
    """Why this run compared no gate with the committed baseline across maturity models, or None."""
    changed = model_changed_line(ctx.get("since_last_run"))
    if changed is not None:
        return changed
    level = ctx.get("level")
    if default_level(level):
        return None
    name = model_name(level["levels_version"]) if isinstance(level, Mapping) else "?"
    return f"this run used {name}, and the committed baseline holds the default model's gates only"


def _steps() -> list[StepDef]:
    return sorted(default_model().get("steps", []), key=lambda s: s["step"])


def lost_gates(ctx: Mapping[str, Any]) -> list[str]:
    """``since_last_run.gates_lost``, or empty when not known."""
    since = ctx.get("since_last_run")
    if not isinstance(since, Mapping) or since.get("available") is False:
        return []
    lost = since.get("gates_lost")
    return [g for g in lost if isinstance(g, str)] if isinstance(lost, list) else []


def _states(ctx: Mapping[str, Any]) -> dict[str, str]:
    """Each gate that does not hold this run, with its condition state; empty when the level
    result is unavailable or comes from a model without the default model's gate conditions."""
    level = ctx.get("level")
    conditions = level.get("conditions") if isinstance(level, Mapping) else None
    if not isinstance(conditions, list):
        return {}
    try:
        blockers = gate_blockers(_steps(), conditions)
    except KeyError:
        return {}
    return {b["gate"]: b["state"] for b in blockers if "gate" in b and "state" in b}


def split_lost(ctx: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """(lost gates that now read unmet, lost gates this run could not read)."""
    states = _states(ctx)
    unmet: list[str] = []
    unread: list[str] = []
    for gate in lost_gates(ctx):
        (unmet if states.get(gate) == "unmet" else unread).append(gate)
    return unmet, unread


def _baseline(ctx: Mapping[str, Any]) -> Mapping[str, Any] | None:
    prior = ctx.get("prior_level")
    baseline = prior.get("baseline") if isinstance(prior, Mapping) else None
    if isinstance(baseline, Mapping) and isinstance(baseline.get("held_gates"), list):
        return baseline
    return None


def baseline_step(held: Sequence[str], steps: Sequence[StepDef] | None = None) -> int | None:
    """The highest Step whose gates, and every lower Step's, are all in ``held``."""
    ordered = sorted(steps, key=lambda s: s["step"]) if steps is not None else _steps()
    reached: int | None = None
    for step in ordered:
        if any(g["id"] not in held for g in step["gates"]):
            break
        reached = step["step"]
    return reached


def step_change(ctx: Mapping[str, Any]) -> tuple[int, int] | None:
    """(the baseline's Step, this run's Step) when this run's is lower; None otherwise."""
    baseline = _baseline(ctx)
    level = ctx.get("level")
    now = level.get("step") if isinstance(level, Mapping) else None
    if baseline is None or not isinstance(now, int):
        return None
    before = baseline_step([g for g in baseline["held_gates"] if isinstance(g, str)])
    if before is None or before <= now:
        return None
    return before, now


def held_at_baseline(ctx: Mapping[str, Any], gates: Sequence[str]) -> list[str]:
    """The ``gates`` the committed baseline's ``held_gates`` lists. ``since_last_run`` can
    also compare with an earlier ``run-context.json`` left on disk; only the committed
    baseline confirms a loss that can fail the gate."""
    baseline = _baseline(ctx)
    held = set(baseline["held_gates"]) if baseline is not None else set()
    return [g for g in gates if g in held]


def no_baseline(ctx: Mapping[str, Any]) -> bool:
    """True on a first run, or when no committed baseline was read."""
    since = ctx.get("since_last_run")
    if isinstance(since, Mapping) and since.get("first_run") is True:
        return True
    return _baseline(ctx) is None
