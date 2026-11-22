"""The verified tally and the autonomy Step: result = f(commit, maturity-model version) (#744).

``lib.levels`` gives each row a whole level. This module reads the same maturity
model as a flat list of **conditions**, one per row level (``<row id>.<level>``,
``lib.levels.condition_id``), and computes from one repository's signals alone,
with no prior run, no stored state and no model in the path:

- each condition's **state**: ``met``, ``unmet`` or ``not_measured`` (with a
  reason code). ``not_applicable`` and ``declined`` are reserved states that no
  rule produces yet (#827, #828), so readers accept all five;
- each condition's **grade**, the weakest ``lib.level_signals.criterion_grade``
  of its criteria. ``measured`` and ``verified`` conditions are **counted**;
  ``config_only`` and ``declared`` ones, and any criterion signal with no
  producer class (a caller's own model), are **listed**: shown, never counted;
- the **tally** (the level): ``met`` is the number of counted conditions met,
  ``measured`` the number of counted conditions met or unmet, and
  ``not_measured`` the number of conditions of any grade not measured. An
  unknown never earns anything, so a blind run neither lowers nor raises ``met``;
- the **Step**: the highest Step whose threshold ``met`` reaches and whose gates
  hold, every lower Step held too, plus the next Step and what blocks it.

A condition reads its own criteria only; a level does not need the level below
it (the row's level in ``lib.levels`` still climbs). A real dependency is
written as ``requires`` on the level: a condition whose own criteria are met
takes the state of the first required condition that is not met (``unmet``, or
``not_measured`` with that condition's reason), with reason
``requires:<condition id>``.

Two additive model fields (``lib/levels_fields.py``, #843) adjust this, and
neither moves a row level or, in the shipped model, anything at all: a
``polarity = "hazard"`` condition is a harm, so it is never counted (listed when
met) and carries ``polarity: "hazard"``; a ``guarded_by`` condition's grade is
capped at ``measured`` while its guard is not ``met``, so it reads ``verified``
only when its own check is under test, and what is counted does not change.

Gates read state, never grade. A declared condition (an instruction file) or a
config-only one (an enforced coverage gate) holds its gate while staying out of
the tally (#783 decision). A gate holds when every condition of its row up to
its level is ``met``; a blocker names the first condition that is not, with its
state and reason.

Reason codes of a ``not_measured`` condition (the first unknown criterion's):

- a ``level_signals.UNMEASURED_VALUES`` code, when a criterion's signal holds
  the value that means "could not be measured" (``mutation_run`` false:
  ``needs_full_run``, never unmet, #740; ``aging_reliable`` false:
  ``degenerate_history``). Any other unmet criterion makes the condition unmet;
- a ``level_signals.EMPTY_DENOMINATORS`` code, when a criterion's signal is a
  statistic over a set whose count is 0 (``stats_summary.loc.p95`` with
  ``files_scored`` 0: ``no_scored_files``, #906). ``lib.levels`` reads that
  criterion as unknown, so the row level and the condition agree, and a real
  unmet sibling criterion still makes the condition unmet;
- ``needs_full_run``: the block is ``not_registered`` in a headless run;
- ``needs_platform``: the block needs GitHub or GitLab (``network``);
- the block's own reason code (``lib.counts_only.reason_code``, or the code a
  known reason sentence maps to, ``reason_token``), when a block on
  the path reports ``available: false``;
- ``block_absent`` (the run has no such block, or it is null), ``no_value`` (the signal is
  missing or null) or ``type_mismatch``.

Every value is a count, a boolean, an id from the model or a reason code, so the
block survives counts-only output unchanged, and ``dumps_level`` gives identical
bytes for identical inputs. ``/assess`` writes the result as the run context's
``level`` block (``run_context_level``); ``/fleet`` writes one per repository to
``level.json`` (``lib/fleet_level.py``). Shape: ``LevelResult`` (``lib/level_result_types.py``); the published
schema is ``skills/fleet/references/level.schema.json``.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from lib.reason_codes import reason_code, reason_token
from lib.level_result_types import Blocker, ConditionResult, LevelResult, NextStep
from lib.levels import Criterion, CriterionOutcome, Evaluation, LevelOutcome, MaturityModel, Row, StepDef
from lib.levels import condition_id, evaluate_row, unavailable_signal
from lib.levels import load_levels
from lib.signal_grades import DEFAULT_LEVELS, EMPTY_DENOMINATORS, INPUT_GRADES, SIGNAL_INPUTS, UNMEASURED_VALUES

# Bumped when the result's shape or a state rule changes.
# Unreleased (the 3.0.0 release PR bumps it once): a condition may carry
# ``polarity: "hazard"``, and a hazard is never counted (#843).
LEVEL_RESULT_VERSION = 2

STATES = ("met", "unmet", "not_applicable", "not_measured", "declined")
COUNTED_GRADES = frozenset({"measured", "verified"})
_GRADE_ORDER = ("config_only", "declared", "measured", "verified")
NEEDS_FULL_RUN = "needs_full_run"
NEEDS_PLATFORM = "needs_platform"
# Block reason codes that a different kind of run would measure (lib.signals).
_RUN_KIND_REASONS = {"not_registered": NEEDS_FULL_RUN, "network": NEEDS_PLATFORM}
# The codes lib.levels gives a criterion whose signal is a statistic over an empty set.
EMPTY_SET_CODES = frozenset(code for _, code in EMPTY_DENOMINATORS.values())


def _block_reason(data: Mapping[str, Any] | None, signal: str) -> str:
    """The reason code for an unavailable signal (see the module doc)."""
    # A null block reads as an absent one: an omitted spread key is null in the run
    # context's entry and missing on a recount, and both must give the same code.
    if not isinstance(data, Mapping) or data.get(signal.split(".")[0]) is None:
        return "block_absent"
    node: Any = data
    for part in signal.split("."):
        if isinstance(node, Mapping) and node.get("available") is False:
            code = reason_code(node.get("reason"))
            if code == "unavailable":  # a sentence with a known code (counts-only's REASON rule)
                code = reason_token(str(node.get("reason") or "")) or code
            return _RUN_KIND_REASONS.get(code, code)
        if not isinstance(node, Mapping) or part not in node:
            return "no_value"
        node = node[part]
    return "no_value"


def _grade(row: Row, level: int) -> str | None:
    grades = [INPUT_GRADES[SIGNAL_INPUTS[c["signal"]]] if c["signal"] in SIGNAL_INPUTS else None
              for c in row["levels"][level - 1]["criteria"]]
    if any(g is None for g in grades):
        return None
    return min((g for g in grades if g is not None), key=_GRADE_ORDER.index)


def _unmeasured(outcome: CriterionOutcome) -> str | None:
    """The ``UNMEASURED_VALUES`` code when an unmet criterion's value means "not measured"."""
    rule = UNMEASURED_VALUES.get(outcome["signal"])
    if outcome["status"] != "unmet" or rule is None or outcome["actual"] is not rule[0]:
        return None
    return rule[1]


def _own_state(outcome: LevelOutcome, criteria: list[Criterion],
               data: Mapping[str, Any] | None) -> tuple[str, str | None]:
    """A condition's state from its own criteria, with the reason when not measured."""
    if any(u["status"] == "unmet" and _unmeasured(u) is None for u in outcome["unmet"]):
        return "unmet", None
    if not outcome["unmet"]:
        return "met", None
    first = outcome["unmet"][0]
    code = _unmeasured(first)
    if code is not None:
        return "not_measured", code
    if first["reason"] in EMPTY_SET_CODES or first["reason"] == "type_mismatch":
        return "not_measured", first["reason"]
    criterion = next(c for c in criteria if (c["signal"], c["op"], c["value"])
                     == (first["signal"], first["op"], first["value"]))
    return "not_measured", _block_reason(data, unavailable_signal(criterion, data))


def _conditions(model: MaturityModel, data: Mapping[str, Any] | None) -> list[ConditionResult]:
    own: dict[str, tuple[str, str | None]] = {}
    built: list[ConditionResult] = []
    requires: dict[str, list[str]] = {}
    guards: dict[str, str] = {}
    for row in model["rows"]:
        evaluation: Evaluation = evaluate_row(row, dict(data) if data is not None else None)
        for outcome, level in zip(evaluation["levels"], row["levels"]):
            cid = condition_id(row["id"], level["level"])
            state, reason = _own_state(outcome, level["criteria"], data)
            own[cid] = (state, reason)
            requires[cid] = level.get("requires", [])
            if "guarded_by" in level:
                guards[cid] = level["guarded_by"]
            grade = _grade(row, level["level"])
            hazard = level.get("polarity") == "hazard"
            entry: ConditionResult = {"id": cid, "row": row["id"], "level": level["level"], "grade": grade,
                                      "counted": grade in COUNTED_GRADES and not hazard, "state": state}
            if hazard:
                entry["polarity"] = "hazard"
            built.append(entry)
    final: dict[str, tuple[str, str | None]] = {}

    def resolve(cid: str) -> tuple[str, str | None]:
        # parse_levels rejects unknown ids and cycles, so this terminates
        if cid not in final:
            state, reason = own[cid]
            if state == "met":
                for need in requires[cid]:
                    need_state, _ = resolve(need)
                    if need_state != "met":
                        state, reason = need_state, f"requires:{need}"
                        break
            final[cid] = (state, reason)
        return final[cid]

    for entry in built:
        entry["state"], reason = resolve(entry["id"])
        if reason is not None:
            entry["reason"] = reason
    for entry in built:  # one hop: a guard's own guard does not matter here
        guard = guards.get(entry["id"])
        if guard is not None and entry["grade"] == "verified" and final[guard][0] != "met":
            entry["grade"] = "measured"
    return built


def _gate_blocker(gate_id: str, row_id: str, level: int,
                  by_id: Mapping[str, ConditionResult]) -> Blocker | None:
    for n in range(1, level + 1):
        cond = by_id[condition_id(row_id, n)]
        if cond["state"] != "met":
            blocker: Blocker = {"kind": "gate", "gate": gate_id, "condition": cond["id"], "state": cond["state"]}
            if "reason" in cond:
                blocker["reason"] = cond["reason"]
            return blocker
    return None


def _blockers(step: StepDef, met: int, by_id: Mapping[str, ConditionResult]) -> list[Blocker]:
    found = [b for g in step["gates"] if (b := _gate_blocker(g["id"], g["row"], g["level"], by_id)) is not None]
    if met < step["threshold"]:
        found.append({"kind": "threshold", "threshold": step["threshold"], "met": met})
    return found


def level_result(model: MaturityModel, data: Mapping[str, Any] | None) -> LevelResult:
    """The tally and the Step of one repository's signals (``scans`` or a run context)."""
    conditions = _conditions(model, data)
    counted = [c for c in conditions if c["counted"]]
    met = sum(c["state"] == "met" for c in counted)
    by_id = {c["id"]: c for c in conditions}
    step: int | None = None
    next_step: NextStep | None = None
    for definition in model.get("steps", []):
        blockers = _blockers(definition, met, by_id)
        if blockers:
            next_step = {"step": definition["step"], "threshold": definition["threshold"], "blockers": blockers}
            break
        step = definition["step"]
    return {
        "level_result_version": LEVEL_RESULT_VERSION,
        "levels_version": model["version"],
        "tally": {"met": met, "measured": sum(c["state"] in ("met", "unmet") for c in counted),
                  "not_measured": sum(c["state"] == "not_measured" for c in conditions)},
        "counted": [c["id"] for c in counted if c["state"] == "met"],
        "listed": [c["id"] for c in conditions if not c["counted"] and c["state"] == "met"],
        "not_measured": [{"id": c["id"], "reason": c.get("reason", "no_value")}
                         for c in conditions if c["state"] == "not_measured"],
        "conditions": conditions,
        "step": step,
        "next_step": next_step,
    }


def default_model() -> MaturityModel:
    """The shipped model, read without the path validator (``level_signals`` applies it
    in the tests), so the run context's entry never imports the scanners."""
    return load_levels(DEFAULT_LEVELS)


def model_blocks(model: MaturityModel) -> tuple[str, ...]:
    """The blocks a model's criteria read (each signal's first segment), sorted."""
    return tuple(sorted({c["signal"].split(".")[0] for row in model["rows"]
                         for level in row["levels"] for c in level["criteria"]}))


# The run-context blocks the ``level`` entry reads (lib/scan_registry.py).
DEFAULT_BLOCKS = model_blocks(default_model())


def run_context_level(ctx: Mapping[str, Any]) -> LevelResult:
    """The run context's ``level`` block: the default maturity model over the run context."""
    return level_result(default_model(), ctx)


def dumps_level(result: Any) -> str:
    """Canonical JSON (sorted keys, fixed indent, trailing newline), as ``levels.dumps_result``."""
    return json.dumps(result, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
