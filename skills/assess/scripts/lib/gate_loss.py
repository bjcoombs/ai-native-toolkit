"""The Step gates and the Step a run lost against the committed baseline, for the CI gate (#1152).

``assess_gate.py``'s opt-in ``fail_on_gate_loss`` check reads these. Everything here only
reads the run context; nothing writes a file or recomputes ``since_last_run``:

- **Lost gates.** ``since_last_run.gates_lost`` (``lib/since_last_run.py``): the gates the
  committed baseline (``.assess/prior-level.json``, ``baseline.held_gates``) held that this
  run does not, in Step order. Empty on a first run, when the block is degraded, or when the
  changes are not known (``gates_lost`` null), so an unknown never reads as a loss.
- **Unmet or unread.** ``split_lost`` sorts each lost gate by its condition state in this
  run's ``level`` block (``level_tally.gate_blockers`` over the default model's Steps): a gate
  that now reads ``unmet``, is held by a met hazard or reads ``declined`` (#828,
  ``MEASURED_LOSS``) is a measured loss, since each is read, not unknown. One that reads anything else
  (``not_measured``, an unavailable level result, a model without that gate's condition) is
  unknown, never a loss.
- **The Step.** A Step needs its gates and nothing else (#1083), so the baseline's Step is
  the highest Step whose gates, and every lower Step's, are all in ``held_gates``
  (``baseline_step``). ``step_change`` gives (baseline Step, this run's Step) when this run's
  is lower, else None. A lost Step is always a lost gate, so the Step only words the loss.
- **Confirmed by the baseline.** ``since_last_run`` falls back to an earlier
  ``run-context.json`` left on disk when no baseline is committed; ``held_at_baseline`` keeps
  only the gates the committed baseline's ``held_gates`` lists, the only loss that can fail the
  gate. The baseline is the run's own model's record (``prior_level.model_key``, #1184): the
  default model's, or a shipped profile's under ``models``; Steps and gate states are read on
  that model's Steps (``profiles.model_for_level``).
- **No baseline.** ``no_baseline`` is True on a first run or when the run context carries no
  committed baseline for the run's model: there is nothing to compare with, so the gate passes
  and the log says so. A profile run in CI with no committed profile record reads the same.
- **Not compared.** ``not_compared`` gives the reason no gate was compared across maturity
  models (#1150): the ``since_last_run`` model-changed line. The gate prints it as a warn line
  instead of "clean snapshot", or fails under ``fail_on_gate_loss`` with a committed baseline,
  so a check that compared nothing never reads clean. ``pack_change_reason`` does the same for
  the ``since_last_run`` pack-changed and pack-not-recorded lines (#1316): a practice pack edited
  since the committed record, which its gates are not compared against.

Tests: ``tests/test_gate_loss.py`` and ``tests/test_assess_gate.py``.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from lib.scoring.level_tally import default_model, gate_blockers
from lib.scoring.levels import StepDef
from lib.model_change import PACK_CHANGED, PACK_NOT_RECORDED, changed_models
from lib.prior_level import model_key
from lib.profiles import model_for_level, profile_names

__all__ = ["baseline_step", "held_at_baseline", "lost_gates", "model_change_reason", "no_baseline",
           "not_compared", "pack_change_reason", "split_lost", "step_change"]


def _model_label(name: str) -> str:
    """``the default model``, ``profile handover`` or ``model <version>``."""
    if name == "default":
        return "the default model"
    return f"profile {name}" if name in profile_names() else f"model {name}"


def model_change_reason(ctx: Mapping[str, Any]) -> str | None:
    """``model changed (A -> B)`` when this run compared no gate because the previous run used
    another model, with the two models named as ``ModelChoice.label`` does; None otherwise."""
    models = changed_models(ctx.get("since_last_run"))
    return None if models is None else f"model changed ({' -> '.join(_model_label(m) for m in models)})"


def pack_change_reason(ctx: Mapping[str, Any]) -> str | None:
    """``pack changed (company)`` or ``pack not recorded (company)`` when this run compared no
    gate because a practice pack differs from the committed record's (#1316); None otherwise."""
    since = ctx.get("since_last_run")
    lines = since.get("lines") if isinstance(since, Mapping) else None
    for line in lines if isinstance(lines, list) else []:
        for template, label in ((PACK_CHANGED, "pack changed"), (PACK_NOT_RECORDED, "pack not recorded")):
            head, _, tail = template.partition("{}")
            if isinstance(line, str) and line.startswith(head) and line.endswith(tail):
                return f"{label} ({line[len(head):len(line) - len(tail)]})"
    return None


def not_compared(ctx: Mapping[str, Any]) -> str | None:
    """Why this run compared no gate across maturity models, or across a practice pack's content
    (#1316), with what to do, or None. Each model keeps its own committed record (#1184), so a
    change means the previous state came from a ``run-context.json`` of another model; it compares
    again once a run of this model is committed. A pack record is replaced by this run's, so the
    next run with the same pack compares again."""
    changed = model_change_reason(ctx)
    if changed is not None:
        return f"{changed}; commit a run of one maturity model so the next run compares gates"
    pack = pack_change_reason(ctx)
    return None if pack is None else f"{pack}; commit this run's record so the next run compares gates"


def _steps(level: Any = None) -> list[StepDef]:
    """The Steps of ``level``'s model (the default model's for None or a custom model)."""
    return sorted((model_for_level(level) or default_model()).get("steps", []), key=lambda s: s["step"])


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
        blockers = gate_blockers(_steps(level), conditions)
    except KeyError:
        return {}
    # a met hazard that holds the gate reads as "hazard" (#828), apart from a met condition
    return {b["gate"]: "hazard" if b.get("polarity") == "hazard" else b["state"]
            for b in blockers if "gate" in b and "state" in b}


# The blocker readings that are a measured loss (#828): a read state, never an unknown.
MEASURED_LOSS = frozenset({"unmet", "hazard", "declined"})


def split_lost(ctx: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """(lost gates this run read as lost: unmet, held by a met hazard or declined; lost gates this
    run could not read)."""
    states = _states(ctx)
    unmet: list[str] = []
    unread: list[str] = []
    for gate in lost_gates(ctx):
        (unmet if states.get(gate) in MEASURED_LOSS else unread).append(gate)
    return unmet, unread


def _baseline(ctx: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The committed baseline of this run's model (#1184): the top-level record's for the default
    model, ``models[<name>]``'s for a shipped profile, none for a custom model."""
    prior, key = ctx.get("prior_level"), model_key(ctx.get("level"))
    if isinstance(prior, Mapping) and key not in (None, "default"):
        models = prior.get("models")
        prior = models.get(key) if isinstance(models, Mapping) else None
    baseline = prior.get("baseline") if isinstance(prior, Mapping) and key is not None else None
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
    before = baseline_step([g for g in baseline["held_gates"] if isinstance(g, str)], _steps(level))
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
