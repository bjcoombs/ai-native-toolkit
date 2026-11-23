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
- **No baseline.** ``no_baseline`` is True on a first run or when the run context carries no
  committed baseline (``prior_level.baseline`` absent): there is nothing to compare with, so
  the gate passes and the log says so.

Tests: ``tests/test_gate_loss.py`` and ``tests/test_assess_gate.py``.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from lib.level_tally import default_model, gate_blockers
from lib.levels import StepDef

__all__ = ["baseline_step", "lost_gates", "no_baseline", "split_lost", "step_change"]


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


def no_baseline(ctx: Mapping[str, Any]) -> bool:
    """True on a first run, or when no committed baseline was read."""
    since = ctx.get("since_last_run")
    if not isinstance(since, Mapping):
        return _baseline(ctx) is None
    if since.get("first_run") is True:
        return True
    return not isinstance(since.get("gates_lost"), list) and _baseline(ctx) is None
