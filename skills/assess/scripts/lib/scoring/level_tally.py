"""The verified tally and the autonomy Step: result = f(commit, maturity-model version) (#744).

``lib.scoring.levels`` gives each row a whole level. This module reads the same maturity
model as a flat list of **conditions**, one per row level (``<row id>.<level>``,
``lib.scoring.levels.condition_id``), and computes from one repository's signals alone,
with no prior run, no stored state and no model in the path:

- each condition's **state**: ``met``, ``unmet`` or ``not_measured`` (with a
  reason code), or ``not_applicable`` when the owner excluded it (below).
  ``declined`` is a reserved state that no rule produces yet (#828), so readers
  accept all five;
- each condition's **grade**, the weakest ``lib.scoring.level_signals.criterion_grade``
  of its criteria. ``measured`` and ``verified`` conditions are **counted**;
  ``config_only`` and ``declared`` ones, and any criterion signal with no
  producer class (a caller's own model), are **listed**: shown, never counted;
- the **tally** (the level): ``met`` is the number of counted conditions met,
  ``measured`` the number of counted conditions met or unmet, and
  ``not_measured`` the number of conditions of any grade not measured. An
  unknown never earns anything, so a blind run neither lowers nor raises ``met``;
- the **Step**: the highest Step whose gates hold, every lower Step held too,
  plus the next Step and the gates that block it. A Step reads its gates and
  nothing else (#1083): the tally never moves it, so a condition added to the
  model that a repository already meets leaves its Step where it was.

A condition reads its own criteria only; a level does not need the level below
it (the row's level in ``lib.scoring.levels`` still climbs). A real dependency is
written as ``requires`` on the level: a condition whose own criteria are met
takes the state of the first required condition that is not met (``unmet``, or
``not_measured`` with that condition's reason), with reason
``requires:<condition id>``.

Two additive model fields (``lib/scoring/levels_fields.py``, #843) adjust this, and
neither moves a row level or, in the shipped model, anything at all: a
``polarity = "hazard"`` condition is a harm, so it is never counted (listed when
met) and carries ``polarity: "hazard"``; a ``guarded_by`` condition's grade is
capped at ``measured`` while its guard is not ``met``, so it reads ``verified``
only when its own check is under test, and what is counted does not change.

**Owner exclusions** (``lib/owner_exclusions.py``, #910) are passed in, never
read from the signals. An excluded condition reads ``not_applicable`` with
``disposition: excluded_by_owner`` and its ``exclusion_kind``, whatever its own
criteria say; it is neither counted nor in the denominator, so it neither earns
nor costs. A condition that requires it takes ``not_applicable`` with reason
``requires:<id>``: an exclusion never unlocks a dependent. The result lists the
exclusions under ``excluded`` (ids and kinds only; the owner's reason stays in the
run context's ``owner_exclusions`` block). With no exclusions the result is
byte-identical to one computed without them.

Gates read state, never grade. A declared condition (an instruction file) or a
config-only one (an enforced coverage gate) holds its gate while staying out of
the tally (#783 decision). A gate holds when every condition of its row up to
its level is ``met``; a blocker names the first condition that is not, with its
state and reason, and its disposition when the owner excluded it. An excluded
gate condition never holds its gate, so its Step cannot be reached while the
exclusion stands (#744 decision).

Reason codes of a ``not_measured`` condition (the first unknown criterion's):

- a ``level_signals.UNMEASURED_VALUES`` code, when a criterion's signal holds
  the value that means "could not be measured" (``mutation_run`` false:
  ``needs_full_run``, never unmet, #740; ``aging_reliable`` false:
  ``degenerate_history``). Any other unmet criterion makes the condition unmet;
- a ``level_signals.EMPTY_DENOMINATORS`` code, when a criterion's signal is a
  statistic over a set whose count is 0 (``stats_summary.loc_by_class.source.p95``
  with its ``files_scored`` 0: ``no_source_files``, #906, #820). ``lib.scoring.levels`` reads that
  criterion as unknown, so the row level and the condition agree, and a real
  unmet sibling criterion still makes the condition unmet;
- ``needs_full_run``: the block is ``not_registered`` in a headless run;
- ``needs_platform``: the block needs GitHub or GitLab (``network``). A remote
  on a host no platform read supports (Bitbucket, GitHub Enterprise) is the
  block's own ``unsupported_host`` instead, since ``--platform`` cannot read it (#959);
- the block's own reason code (``lib.counts_only.content_free.reason_code``, or the code a
  known reason sentence maps to, ``reason_token``), when a block on
  the path reports ``available: false``;
- the code in a null signal's sibling ``<leaf>_reason`` field, such as
  ``agent_ops.summary.permissions_encoded_reason`` ``managed_settings_unreadable`` (#1153);
- ``block_absent`` (the run has no such block, or it is null), ``no_value`` (the signal is
  missing or null) or ``type_mismatch``.

Every value is a count, a boolean, an id from the model or a reason code, so the
block survives counts-only output unchanged, and ``dumps_level`` gives identical
bytes for identical inputs. ``/assess`` writes the result as the run context's
``level`` block (``run_context_level``); ``/fleet`` writes one per repository to
``level.json`` (``lib/fleet/fleet_level.py``). Shape: ``LevelResult`` (``lib/scoring/level_result_types.py``); the published
schema is ``skills/fleet/references/level.schema.json``.

The verified tally and the autonomy Step: result = f(commit, maturity-model version), from
one repository's signals with no prior run and no model in the path (#744). Each row level
of the model is one condition, `<row id>.<level>`, read on its own criteria (a level does
not need the level below; `requires` states a real dependency). `level_result(model, data)`
returns a `LevelResult` (shapes in `level_result_types.py`):

- `conditions[]`: `id`, `row`, `level`, `grade` (the weakest `level_signals` criterion grade;
  null for a signal with no producer class), `counted` (grade `measured` or `verified`, and
  not excluded), `state` (`met`, `unmet`, `not_measured`, or `not_applicable` when the owner
  excluded it; `declined` is reserved for #828) and, when not measured or held by a `requires`
  that is not met, a `reason` code. An owner-excluded condition also carries
  `disposition: "excluded_by_owner"` and `exclusion_kind`. `why` (#939) is the static text of
  the check manifest's whys for the condition's criteria, in criterion order, each once, joined
  by one space (`condition_why`); it is absent when no criterion has one (a caller's own model),
  and identical in `/assess` and `/fleet`. A
  `polarity = "hazard"` condition is never counted and carries `polarity: "hazard"`; a
  `guarded_by` condition reads `measured`, not `verified`, while its guard is not met (#843).
  `source` (#1274) names the base model or practice pack the condition's row came from; it is
  present only on a model a pack added rows to (`lib/pack_layering.py`).
- Owner exclusions: `level_result(model, data, exclusions)` takes the `OwnerExclusion`
  entries (`owner_exclusions.py`, #910). An excluded condition is `not_applicable` whatever its
  criteria say, out of the tally and the denominator; a condition that `requires` it is
  `not_applicable` with `requires:<id>`; an excluded gate condition never holds its gate, so
  its Step stays unreachable and the blocker carries the disposition and kind. `excluded[]`
  (`{id, kind, source}`) is present only when an exclusion applies, so a result without
  exclusions is unchanged. Ids the model does not define are ignored.
- Exit-rule demotions (#1086): a model's `demoted` ids are the conditions the fleet's exit rule
  demoted (`lib/fleet/condition_demotion.py`). Only `fleet.py` sets them, on the model every
  fleet result reads (`with_demotions`), so repository, unit, tag and re-scored results agree; a
  model file cannot carry the key and `/assess` never sets it, so no repository input reaches it.
  A demoted condition keeps its state, is never counted (it is listed when met) and carries
  `disposition: "demoted_by_exit_rule"`. A gate condition (`gate_conditions`) or an unknown id
  is ignored, so a demotion changes the count and never a Step. An owner exclusion wins.
- `tally`: `met` (the level: counted conditions met), `measured` (counted conditions met or
  unmet) and `not_measured` (conditions of any grade not measured). An unknown never counts.
- `counted` and `listed`: the ids of the counted and the listed (config-only, declared or
  unclassified) conditions that are met. `not_measured[]`: `{id, reason}`.
- `tags` (#940, `lib/scoring/level_tags.py` `tag_rows`): one row per truth tag, all of `TRUTH_TAGS` in
  that order, with `conditions` (the conditions carrying the tag, 0 for a tag none carries
  yet), `countable` and `set_aside` (by grade, and of those excluded or declined), `met` and `measured` (the headline strip cell's, `tag_cells`, never a second
  tally) `not_measured[]` (`{id, reason}`) and `countable_not_measured[]`, its countable subset. A multi-tagged condition is on each of its rows
  and counted once in `tally`; `measured` 0 means not measured, never a share of 0.
- `step` (null without `[[steps]]`, and null below Step 1 when Step 1 has a gate that does
  not hold, with `next_step` naming Step 1, #889) and `next_step` (`step`, `blockers[]`; null at the top
  Step): a Step is held when every gate holds (the gate row's conditions up to its level all
  `met`, whatever their grade), every lower Step held too. Gates only (#1083): the tally never
  moves a Step. A blocker is `{kind: "gate", gate, condition, state, reason?}` for the first
  condition of a gate that is not met.

Reason codes: an `EMPTY_DENOMINATORS` code, checked first (a statistic over a set whose count
is 0, such as `stats_summary.loc_by_class.source.p95` with its `files_scored` 0: `no_source_files`, #906 and #820, and
`agent_environment.pinning.unlocked_total` with `manifests_total` 0: `no_manifests`, #927); an
`UNMEASURED_VALUES` code; `needs_full_run` for a `not_registered` block;
`needs_platform` for a `network` one; another unavailable block's own code, such as
`unsupported_host` or `no_complexity_stats` (or the code its
known reason sentence maps to); `block_absent`, `no_value`, `type_mismatch`; and
`requires:<id>`. Every string is an id, a state, a grade or a code, so the block survives
counts-only output whole. `run_context_level(ctx)` runs the default model over a run context:
the `level` block, a `scan_registry` entry (`core_scans.level`, which reads `level_tally.LEVEL_READS`:
the blocks the default model names, `DEFAULT_BLOCKS`, then `owner_exclusions`; a spread key such as `structure` is readable) recounted by `assess_core.py --opt-in-mutation`. `dumps_level` is
byte-identical on rerun. Schema: `skills/fleet/references/level.schema.json`,
`$defs.level_result`. Tests: `tests/test_level_tally.py`, including the #744 contract
fixtures through `/fleet` and `/assess` on the same commit.
"""
from __future__ import annotations

import json
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from lib.counts_only.reason_codes import reason_code, reason_token
from lib.scoring.level_tags import tag_rows
from lib.scoring.level_result_types import Blocker, ConditionResult, ExcludedCondition, LevelResult, NextStep
from lib.scoring.levels import Criterion, CriterionOutcome, Evaluation, LevelOutcome, MaturityModel, Row, StepDef
from lib.scoring.levels import condition_id, evaluate_row, unavailable_signal
from lib.scoring.levels import load_levels
from lib.scoring.signal_grades import DEFAULT_LEVELS, EMPTY_DENOMINATORS, INPUT_GRADES, SIGNAL_INPUTS, UNMEASURED_VALUES

# Bumped when the result's shape or a state rule changes.
# History: skills/assess/changes/history/level_result.md, newest entry first.
LEVEL_RESULT_VERSION = 4

STATES = ("met", "unmet", "not_applicable", "not_measured", "declined")
COUNTED_GRADES = frozenset({"measured", "verified"})
_GRADE_ORDER = ("config_only", "declared", "measured", "verified")
NEEDS_FULL_RUN = "needs_full_run"
NEEDS_PLATFORM = "needs_platform"
# Block reason codes that a different kind of run would measure (lib.signals).
_RUN_KIND_REASONS = {"not_registered": NEEDS_FULL_RUN, "network": NEEDS_PLATFORM}
# The codes lib.scoring.levels gives a criterion whose signal is a statistic over an empty set.
EMPTY_SET_CODES = frozenset(code for _, code in EMPTY_DENOMINATORS.values())
EXCLUDED_BY_OWNER = "excluded_by_owner"
# A condition the fleet's exit rule demoted (lib/fleet/condition_demotion.py, #1086): listed, never counted.
DEMOTED = "demoted_by_exit_rule"


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
        if node[part] is None and isinstance(node.get(f"{part}_reason"), str):
            # a null signal's sibling `<leaf>_reason` code (#1153)
            return reason_code(node[f"{part}_reason"])
        node = node[part]
    return "no_value"


def _grade(row: Row, level: int) -> str | None:
    # an eq_signal criterion reads two signals, so both grade it (lib/scoring/levels_fields.py)
    paths = [p for c in row["levels"][level - 1]["criteria"]
             for p in (c["signal"], *([c["eq_signal"]] if "eq_signal" in c else []))]
    grades = [INPUT_GRADES[SIGNAL_INPUTS[p]] if p in SIGNAL_INPUTS else None for p in paths]
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


def _mark(entry: ConditionResult, exclusion: ExcludedCondition | None, demoted: bool) -> None:
    """The condition's disposition: the owner's exclusion wins over the exit rule's demotion."""
    if exclusion is not None:
        entry["disposition"] = EXCLUDED_BY_OWNER
        entry["exclusion_kind"] = exclusion["kind"]
    elif demoted:
        entry["disposition"] = DEMOTED


def _conditions(model: MaturityModel, data: Mapping[str, Any] | None,
                excluded: Mapping[str, ExcludedCondition], demoted: Collection[str] = ()) -> list[ConditionResult]:
    own: dict[str, tuple[str, str | None]] = {}
    built: list[ConditionResult] = []
    requires: dict[str, list[str]] = {}
    guards: dict[str, str] = {}
    for row in model["rows"]:
        evaluation: Evaluation = evaluate_row(row, dict(data) if data is not None else None)
        for outcome, level in zip(evaluation["levels"], row["levels"]):
            cid = condition_id(row["id"], level["level"])
            state, reason = (("not_applicable", None) if cid in excluded
                             else _own_state(outcome, level["criteria"], data))
            own[cid] = (state, reason)
            requires[cid] = level.get("requires", [])
            if "guarded_by" in level:
                guards[cid] = level["guarded_by"]
            grade = _grade(row, level["level"])
            hazard = level.get("polarity") == "hazard"
            entry: ConditionResult = {"id": cid, "row": row["id"], "level": level["level"], "grade": grade,
                                      "counted": (grade in COUNTED_GRADES and cid not in excluded and not hazard
                                                  and cid not in demoted),
                                      "state": state}
            if hazard:
                entry["polarity"] = "hazard"
            if "source" in row:
                entry["source"] = row["source"]
            _mark(entry, excluded.get(cid), cid in demoted)
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


def condition_why(row_id: str, level: int, criteria: Sequence[Criterion],
                  whys: Mapping[str, str] | None = None) -> str:
    """The whys of a condition's criteria from the core check manifest (#939), in criterion
    order, each once, joined by one space; ``""`` when no criterion has a manifest why (a
    caller's own model). ``whys`` is ``manifest_whys.condition_whys`` of the core manifest,
    built here when not given."""
    from lib.manifest.check_manifest import condition_key, core_manifest  # lazy: the manifest loads once
    from lib.manifest.manifest_whys import condition_whys
    whys = condition_whys(core_manifest()) if whys is None else whys
    found = [whys.get(condition_key(row_id, level, c["signal"]), "") for c in criteria]
    return " ".join(dict.fromkeys(w for w in found if w))


def _add_whys(model: MaturityModel, conditions: Sequence[ConditionResult]) -> None:
    """Set each condition's ``why`` (``condition_why``) where its criteria have one."""
    from lib.manifest.check_manifest import core_manifest
    from lib.manifest.manifest_whys import condition_whys
    whys = condition_whys(core_manifest())
    by_id = {c["id"]: c for c in conditions}
    for row in model["rows"]:
        for level in row["levels"]:
            if (why := condition_why(row["id"], level["level"], level["criteria"], whys)):
                by_id[condition_id(row["id"], level["level"])]["why"] = why


def _gate_blocker(gate_id: str, row_id: str, level: int,
                  by_id: Mapping[str, ConditionResult]) -> Blocker | None:
    for n in range(1, level + 1):
        cond = by_id[condition_id(row_id, n)]
        if cond["state"] != "met":
            blocker: Blocker = {"kind": "gate", "gate": gate_id, "condition": cond["id"], "state": cond["state"]}
            if "reason" in cond:
                blocker["reason"] = cond["reason"]
            if "disposition" in cond:
                blocker["disposition"] = cond["disposition"]
            if "exclusion_kind" in cond:
                blocker["exclusion_kind"] = cond["exclusion_kind"]
            return blocker
    return None


def gate_blockers(steps: Sequence[StepDef], conditions: Sequence[ConditionResult]) -> list[Blocker]:
    """Every gate of ``steps`` that does not hold, once per gate id in Step order, from a level
    result's ``conditions`` (#945: a Step target reads the gates of a Step above the next one)."""
    by_id = {c["id"]: c for c in conditions}
    seen: set[str] = set()
    out: list[Blocker] = []
    for step in steps:
        for gate in step["gates"]:
            if gate["id"] in seen:
                continue
            seen.add(gate["id"])
            if (blocker := _gate_blocker(gate["id"], gate["row"], gate["level"], by_id)) is not None:
                out.append(blocker)
    return out


def _blockers(step: StepDef, by_id: Mapping[str, ConditionResult]) -> list[Blocker]:
    """The gates of ``step`` that do not hold: the only thing that holds a Step back (#1083)."""
    return [b for g in step["gates"] if (b := _gate_blocker(g["id"], g["row"], g["level"], by_id)) is not None]


def gate_conditions(model: MaturityModel) -> frozenset[str]:
    """Every condition a Step gate reads: its row's levels 1 up to the gate's level."""
    return frozenset(condition_id(g["row"], n) for step in model.get("steps", []) for g in step["gates"]
                     for n in range(1, g["level"] + 1))


def condition_ids(model: MaturityModel) -> frozenset[str]:
    """Every condition id of a model."""
    return frozenset(condition_id(row["id"], level["level"]) for row in model["rows"] for level in row["levels"])


def _excluded(model: MaturityModel, exclusions: Sequence[Mapping[str, Any]]) -> dict[str, ExcludedCondition]:
    """The exclusions that name a condition of the model, first entry per id."""
    known: Collection[str] = condition_ids(model)
    out: dict[str, ExcludedCondition] = {}
    for e in exclusions:
        cid = e.get("id")
        if isinstance(cid, str) and cid in known and cid not in out:
            out[cid] = {"id": cid, "kind": str(e.get("kind") or "other"), "source": str(e.get("source") or "repo")}
    return out


def level_result(model: MaturityModel, data: Mapping[str, Any] | None,
                 exclusions: Sequence[Mapping[str, Any]] = ()) -> LevelResult:
    """The tally and the Step of one repository's signals (``scans`` or a run context).

    ``exclusions`` are the owner's (``OwnerExclusion`` entries, ``lib/owner_exclusions.py``);
    an id the model does not define is ignored. The model's ``demoted`` ids are the fleet exit
    rule's (#1086), set only by ``fleet.py``; a gate condition among them is ignored, so a
    demotion never moves a Step."""
    excluded = _excluded(model, exclusions)
    demoted = frozenset(model.get("demoted", ())) & condition_ids(model) - gate_conditions(model)
    conditions = _conditions(model, data, excluded, demoted)
    _add_whys(model, conditions)
    counted = [c for c in conditions if c["counted"]]
    met = sum(c["state"] == "met" for c in counted)
    by_id = {c["id"]: c for c in conditions}
    step: int | None = None
    next_step: NextStep | None = None
    for definition in model.get("steps", []):
        blockers = _blockers(definition, by_id)
        if blockers:
            next_step = {"step": definition["step"], "blockers": blockers}
            break
        step = definition["step"]
    result: LevelResult = {
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
    if excluded:
        result["excluded"] = [excluded[c["id"]] for c in conditions if c["id"] in excluded]
    result["tags"] = tag_rows(result, model)
    return result


def default_model() -> MaturityModel:
    """The shipped model, read without the path validator (``level_signals`` applies it
    in the tests), so the run context's entry never imports the scanners."""
    return load_levels(DEFAULT_LEVELS)


def model_blocks(model: MaturityModel) -> tuple[str, ...]:
    """The blocks a model's criteria read (each signal's first segment), sorted."""
    return tuple(sorted({c["signal"].split(".")[0] for row in model["rows"]
                         for level in row["levels"] for c in level["criteria"]}))


# The run-context blocks the default model's criteria read.
DEFAULT_BLOCKS = model_blocks(default_model())
# The run-context block holding the owner's exclusions (lib/owner_exclusions.py).
EXCLUSIONS_BLOCK = "owner_exclusions"
# The run-context blocks the ``level`` entry reads, in order (lib/scan_registry.py).
LEVEL_READS = (*DEFAULT_BLOCKS, EXCLUSIONS_BLOCK)


def run_exclusions(ctx: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The exclusions in a run context's ``owner_exclusions`` block; none when it is absent or degraded."""
    block = ctx.get(EXCLUSIONS_BLOCK)
    entries = block.get("exclusions") if isinstance(block, Mapping) else None
    return [e for e in entries if isinstance(e, Mapping)] if isinstance(entries, list) else []


def run_context_level(ctx: Mapping[str, Any]) -> LevelResult:
    """The run context's ``level`` block: the default maturity model over the run context,
    with the owner's exclusions from its ``owner_exclusions`` block."""
    return level_result(default_model(), ctx, run_exclusions(ctx))


def dumps_level(result: Any) -> str:
    """Canonical JSON (sorted keys, fixed indent, trailing newline), as ``levels.dumps_result``."""
    return json.dumps(result, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
