"""Caller-supplied maturity models: TOML loader and deterministic evaluator.

An organisation running a maturity programme scores against its own maturity model:
rows (practices or capabilities), each with numbered whole levels, each level
a list of criteria over named signal paths. This module loads such a maturity model
and evaluates it from signals alone, with no model in the path, so a fleet
scan reaches the same levels every time.

Maturity model file (TOML, read with the stdlib ``tomllib``):

```toml
version = "2026.11"            # stamped on every result

[[rows]]
id = "two-factor"
title = "Enforced two-factor authentication"
scope = "org"                  # evaluated once per group, from group settings

  [[rows.levels]]
  level = 1
  criteria = [{ signal = "two_factor_required", op = "eq", value = true }]

[[rows]]
id = "agent-instructions"
title = "Agent instruction file"
scope = "team"                 # evaluated per repository, then rolled up
aggregation = "share"          # "min", or "share" with a share in (0, 1]
share = 0.75

  [[rows.levels]]
  level = 1
  criteria = [{ signal = "layer_grades.instructions_grade", op = "in", value = ["A", "B", "C"] }]
```

Semantics:

- Levels are whole numbers numbered 1..K with no gap; level 0 is the implicit
  "no level reached". A row reaches level N only when every criterion of N and
  of every lower level holds. No halves, no averages.
- A signal is unavailable when its key is missing, its value is null, or the
  path runs through a block carrying ``available: false`` (reading that
  ``available`` flag itself is allowed). A criterion whose signal is
  unavailable, or whose value has the wrong type for the operator, is
  ``unknown``. Unknown criteria give a row bounds: ``low`` is the level the
  definitely-met levels climb to, ``high`` the level below the first level
  above ``low`` that is definitely unmet (K when there is none). The row is
  ``unknown`` only when the bounds differ, so it is never reported as a false 0
  or a false pass; when a definitely unmet level caps it, the level is known
  even with a higher-level signal missing. ``missing_signals`` names every
  signal whose criterion is unknown, unavailable or mistyped, either way, so a
  non-empty list does not by itself mean ``unknown``. An ``unknown`` row whose
  ``low`` is at least 1 also carries ``floor`` (that ``low``, #697): every
  criterion up to it is met, and only a higher level's signal is unmeasured.
  The key is absent otherwise (never null), so a known row and an unknown row
  with nothing confirmed read exactly as before. Every criterion at every level is evaluated, so the result does
  not depend on criterion order and the unmet criteria of every level are
  exposed for next-step guidance.
  Consequence: a null that a scan means as "absent" (``instructions_grade:
  null``) reads as unknown here; ``layer_grades.instruction_file_present``
  carries that absence as a boolean.
- A statistic over an empty set is unknown, never met or unmet (#906): a signal
  listed in ``signal_grades.EMPTY_DENOMINATORS`` whose set's count reads exactly
  0 (``stats_summary.loc_by_class.source.p95`` with its ``files_scored`` 0) gives
  an ``unknown`` criterion whose ``reason`` is the table's code (``no_source_files``). A count
  that is unavailable or absent leaves the usual rules in place: that branch is
  kept for the unmeasured count, such as a headless ``stats_summary`` without
  lizard (``no_lizard``) or a full run whose treemap step wrote no
  ``complexity-stats.json`` (``no_complexity_stats``, ``lib.run_scope``, #959),
  which must not read as a measured empty set.
- ``actual`` in a criterion outcome is the resolved value only when it is a
  finite scalar; an object, a list or a non-finite number is reported as null
  (its kind is already explained by ``type_mismatch``), so a result never
  carries a NaN.
- ``team`` rows run against each repository's ``run-context.json``, then roll
  up: ``min`` is the level every repository reaches, ``share`` the highest
  level reached by at least that share of the repositories passed in (the
  caller passes the active ones). Each unknown repository contributes its own
  bounds; the team level is reported only when the roll-up of the low bounds
  equals the roll-up of the high bounds, otherwise the row is unknown, with the
  roll-up of the low bounds as its ``floor`` when that is at least 1. No
  repositories at all is unknown. A team row's ``missing_signals`` lists the
  signals unavailable in any repository, so it can be non-empty while
  ``status`` is ``level``; ``unknown_repos`` names the repositories whose own
  level is undetermined.
- ``org`` rows run once against the group settings; settings of ``None`` (not
  gathered) make every org row unknown.

Two optional additions feed the verified tally and the Steps (``lib/level_tally.py``,
#744); neither changes a row's level above:

- ``requires`` on a level: condition ids (``<row id>.<level>``) that must be met
  for this condition to count as met, for a dependency that is real (mutation
  credit needs CI running tests). The row's own level ignores it.
- ``[[steps]]``: the autonomy Steps, numbered 1..K. Each has a ``name`` and
  ``gates``, each ``{ id, row, level }``: the gate holds when that row's
  conditions up to ``level`` are met. Step 1 has no gates; each later Step keeps every
  gate of the one below and adds one. A Step needs its gates and nothing else, so a new
  condition never moves a Step (#1083); an old ``threshold`` key is refused by name.

Six condition fields come from ``lib/levels_fields.py`` (#843), which documents them:
``truth`` (required tags, with the legacy layer alias for a model that carries none) and the
additive ``polarity``, ``guarded_by``, ``eq_signal``, ``predicted`` and ``review`` (#1086).

Loading raises ``LevelsError`` before any evaluation on a malformed maturity model: a
missing version, an unset or invalid scope, a gap or duplicate in level
numbering, an unknown operator or key, a team row with no aggregation rule,
a ``requires`` naming a condition that does not exist (or itself, or a cycle),
a missing, empty or unknown ``truth`` (``MixedTruthError`` when only some conditions
carry it), a bad ``polarity``, ``guarded_by``, ``eq_signal``, ``predicted`` or ``review``, a malformed Step, or a signal path the pluggable ``PathValidator`` rejects (``UnknownSignalError``: its line and the closest signals, ``lib/levels_hints.py``). A renamed 2.1.0 path loads under its new
name with one stderr line (``lib.signal_aliases.dealias_model``, #970). ``dumps_result`` gives
byte-identical JSON for identical inputs.

Full reference: lib/docs/levels.md.
"""
from __future__ import annotations

import argparse
import json
import math
import tomllib
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Mapping, NotRequired, TypedDict

from lib.levels_fields import LevelsError as LevelsError, MixedTruthError as MixedTruthError
from lib.levels_fields import check_guards, check_truth, legacy_truth, parse_eq_signal, parse_guard
from lib.levels_fields import ModelDemotions, ReviewFields, apply_review, parse_polarity, parse_truth
from lib.levels_hints import UnknownSignalError as UnknownSignalError
from lib.signal_aliases import dealias_model
from lib.signal_grades import EMPTY_DENOMINATORS

Scope = Literal["org", "team"]
Scalar = str | int | float | bool
# Run-context blocks and group settings are JSON whose keys vary by signal.
JsonDict = dict[str, Any]

# ``(scope, signal_path) -> bool``: True when the path names a known signal in
# that scope's namespace (group settings for org, run-context for team).
PathValidator = Callable[[Scope, str], bool]

SCOPES: tuple[Scope, ...] = ("org", "team")
AGGREGATION_RULES = ("min", "share")
_ORDER_OPS = ("lt", "lte", "gt", "gte")
_MEMBER_OPS = ("in", "not_in")
_EQUALITY_OPS = ("eq", "ne")
OPERATORS = _EQUALITY_OPS + _ORDER_OPS + _MEMBER_OPS

_ROOT_KEYS = frozenset({"version", "rows", "steps"})
_ROW_KEYS = frozenset({"id", "title", "scope", "aggregation", "share", "levels"})
_LEVEL_KEYS = frozenset({"level", "criteria", "requires", "truth", "polarity", "guarded_by", "predicted", "review"})
_STEP_KEYS = frozenset({"step", "name", "gates"})
# The 2.x and 3.0 Step count key, refused by name since #1083: Steps are gates only.
REMOVED_STEP_KEY = "threshold"
_GATE_KEYS = frozenset({"id", "row", "level"})
_CRITERION_KEYS = frozenset({"signal", "op", "value", "eq_signal"})

# The option that named the maturity-model file before 2.0.0 (#670). It is still
# accepted by the parser so that using it fails loudly, never silently.
RENAMED_FLAG = "--rubric"
LEVELS_FLAG = "--levels"


class _RenamedFlag(argparse.Action):
    """Exit 2 with one line naming ``--levels``, whatever value (or none) follows."""

    def __call__(self, parser: argparse.ArgumentParser, namespace: argparse.Namespace,
                 values: object, option_string: str | None = None) -> None:
        parser.exit(2, f"{parser.prog}: error: {RENAMED_FLAG} was renamed in 2.0.0: use {LEVELS_FLAG} FILE\n")


def add_renamed_flag(parser: argparse.ArgumentParser) -> None:
    """Register the pre-2.0.0 option on ``parser`` so using it exits 2 naming ``--levels``."""
    parser.add_argument(RENAMED_FLAG, action=_RenamedFlag, nargs="?", default=argparse.SUPPRESS,
                        help=argparse.SUPPRESS)


class Criterion(TypedDict):
    signal: str
    op: str
    # the operand; for an ``eq_signal`` criterion, the second path (lib/levels_fields.py)
    value: Scalar | list[Scalar]
    eq_signal: NotRequired[str]


class Level(ReviewFields):  # predicted, review: the exit rule (#1086)
    level: int
    criteria: list[Criterion]
    # condition ids (``<row id>.<level>``) this condition needs met (#744)
    requires: NotRequired[list[str]]
    truth: list[str]  # declared, or resolved by the legacy layer alias (#843)
    polarity: NotRequired[Literal["hazard"]]  # absent: positive, the default
    guarded_by: NotRequired[str]  # one condition id whose state gates a verified grade


class Aggregation(TypedDict):
    rule: str
    share: int | float


class Row(TypedDict):
    id: str
    title: str
    scope: Scope
    aggregation: Aggregation | None
    levels: list[Level]


class StepGate(TypedDict):
    """A Step gate: ``row``'s conditions 1..``level`` must all be met (#744)."""

    id: str
    row: str
    level: int


class StepDef(TypedDict):
    step: int
    name: str
    gates: list[StepGate]  # the only thing a Step needs (#1083)


class MaturityModel(ModelDemotions):  # demoted: set by fleet.py only (#1086)
    version: str
    rows: list[Row]
    steps: NotRequired[list[StepDef]]  # absent: the model defines no Steps


class Group(TypedDict):
    """One evaluation subject: group settings plus its repositories."""

    name: str
    settings: JsonDict | None
    repos: dict[str, JsonDict]


Status = Literal["met", "unmet", "unknown"]


class CriterionOutcome(TypedDict):
    signal: str
    op: str
    value: Scalar | list[Scalar]
    status: Status
    actual: Any
    reason: str | None


class LevelOutcome(TypedDict):
    level: int
    met: bool | None
    unmet: list[CriterionOutcome]


class Evaluation(TypedDict):
    """One row against one subject (a repository or the group settings)."""

    status: Literal["level", "unknown"]
    level: int | None
    # [low, high]: the reachable level range; equal when the level is known.
    bounds: list[int]
    # The low bound of an unknown evaluation when it is at least 1 (#697); absent otherwise.
    floor: NotRequired[int]
    missing_signals: list[str]
    levels: list[LevelOutcome]


class RowResult(TypedDict):
    id: str
    title: str
    scope: Scope
    status: Literal["level", "unknown"]
    level: int | None
    floor: NotRequired[int]  # an unknown row's confirmed low bound, when at least 1 (#697)
    missing_signals: list[str]
    levels: NotRequired[list[LevelOutcome]]
    aggregation: NotRequired[Aggregation]
    repos: NotRequired[dict[str, Evaluation]]
    unknown_repos: NotRequired[list[str]]


class LevelsResult(TypedDict):
    levels_version: str
    group: str
    rows: list[RowResult]


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def known_paths(org: Iterable[str], team: Iterable[str]) -> PathValidator:
    """A validator accepting exactly the given paths per scope."""
    allowed: dict[Scope, frozenset[str]] = {"org": frozenset(org), "team": frozenset(team)}
    return lambda scope, path: path in allowed[scope]


def load_levels(path: Path, validator: PathValidator | None = None) -> MaturityModel:
    """Read and validate a TOML maturity model file; raise ``LevelsError`` on any fault."""
    try:
        data = tomllib.loads(text := path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise LevelsError(f"cannot read maturity model {path.name}: {exc}") from exc
    try:
        return parse_levels(data, validator, source=str(path))
    except UnknownSignalError as exc:  # name the line of each unknown path (#1151)
        raise exc.located(text) from None


def parse_levels(data: Mapping[str, Any], validator: PathValidator | None = None, *,
                 source: str = "<table>") -> MaturityModel:
    """Validate an already-parsed maturity model table; raise ``LevelsError`` on any fault.

    ``source`` names the model in a ``MixedTruthError`` (``load_levels`` passes the path).
    """
    data = dealias_model(data)  # a 2.1.0 signal path loads under its new name (#970)
    _check_keys(data, _ROOT_KEYS, "maturity model")
    version = data.get("version")
    if not isinstance(version, str) or not version.strip():
        raise LevelsError("maturity model: 'version' must be a non-empty string")
    raw_rows = data.get("rows")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise LevelsError("maturity model: 'rows' must be a non-empty array of tables")
    rows: list[Row] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_rows):
        row = _parse_row(raw, index)
        if row["id"] in seen:
            raise LevelsError(f"row {row['id']!r}: duplicate row id")
        seen.add(row["id"])
        rows.append(row)
    _resolve_truth(raw_rows, rows, source)
    _check_requires(rows)
    check_guards({condition_id(r["id"], lv["level"]): lv["guarded_by"] for r in rows for lv in r["levels"]
                  if "guarded_by" in lv},
                 {condition_id(r["id"], lv["level"]): "polarity" in lv for r in rows for lv in r["levels"]})
    if validator is not None:
        _validate_paths(rows, validator)
    model: MaturityModel = {"version": version, "rows": rows}
    if "steps" in data:
        model["steps"] = _parse_steps(data["steps"], rows)
    return model


def _resolve_truth(raw_rows: list[Any], rows: list[Row], source: str) -> None:
    """Apply the legacy alias when no level declares ``truth``; reject a mixed model."""
    declared = {(row["id"], entry.get("level")) for raw, row in zip(raw_rows, rows)
                for entry in raw["levels"] if "truth" in entry}
    untagged = [(row["id"], lv["level"]) for row in rows for lv in row["levels"]
                if (row["id"], lv["level"]) not in declared]
    if check_truth(untagged, sum(len(row["levels"]) for row in rows), source):
        for row in rows:
            for lv in row["levels"]:
                lv["truth"] = legacy_truth(row["id"])


def _check_keys(table: Mapping[str, Any], allowed: frozenset[str], where: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise LevelsError(f"{where}: unknown key(s) {', '.join(unknown)}")


def _text(table: Mapping[str, Any], key: str, where: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise LevelsError(f"{where}: '{key}' must be a non-empty string")
    return value


def _parse_row(raw: Any, index: int) -> Row:
    if not isinstance(raw, dict):
        raise LevelsError(f"row #{index + 1}: must be a table")
    row_id = _text(raw, "id", f"row #{index + 1}")
    where = f"row {row_id!r}"
    _check_keys(raw, _ROW_KEYS, where)
    title = _text(raw, "title", where)
    scope = raw.get("scope")
    if scope not in SCOPES:
        raise LevelsError(f"{where}: 'scope' must be one of {', '.join(SCOPES)} (got {scope!r})")
    aggregation = _parse_aggregation(raw, where, scope)
    levels = _parse_levels(raw.get("levels"), where)
    return {"id": row_id, "title": title, "scope": scope, "aggregation": aggregation, "levels": levels}


def _parse_aggregation(raw: Mapping[str, Any], where: str, scope: Scope) -> Aggregation | None:
    rule = raw.get("aggregation")
    share: Any = raw.get("share")
    if scope == "org":
        if rule is not None or share is not None:
            raise LevelsError(f"{where}: an org row takes no aggregation rule")
        return None
    if rule not in AGGREGATION_RULES:
        raise LevelsError(
            f"{where}: a team row needs 'aggregation' set to one of "
            f"{', '.join(AGGREGATION_RULES)} (got {rule!r})"
        )
    if rule == "min":
        if share is not None:
            raise LevelsError(f"{where}: 'share' applies only to aggregation = \"share\"")
        return {"rule": "min", "share": 1}
    if not _is_number(share) or not 0 < share <= 1:
        raise LevelsError(f"{where}: 'share' must be a number in (0, 1] (got {share!r})")
    return {"rule": "share", "share": share}


def _parse_levels(raw: Any, where: str) -> list[Level]:
    if not isinstance(raw, list) or not raw:
        raise LevelsError(f"{where}: 'levels' must be a non-empty array of tables")
    levels: list[Level] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise LevelsError(f"{where}: each level must be a table")
        _check_keys(entry, _LEVEL_KEYS, where)
        number = entry.get("level")
        if not isinstance(number, int) or isinstance(number, bool):
            raise LevelsError(f"{where}: 'level' must be a whole number (got {number!r})")
        criteria = entry.get("criteria")
        if not isinstance(criteria, list) or not criteria:
            raise LevelsError(f"{where} level {number}: 'criteria' must be a non-empty array")
        here = f"{where} level {number}"
        parsed = [_parse_criterion(c, here) for c in criteria]
        level: Level = {"level": number, "criteria": parsed,
                        "truth": parse_truth(entry["truth"], here) if "truth" in entry else []}
        if "polarity" in entry and parse_polarity(entry["polarity"], here) == "hazard":
            level["polarity"] = "hazard"
        if "guarded_by" in entry:
            level["guarded_by"] = parse_guard(entry["guarded_by"], here)
        apply_review(level, entry, here)
        if "requires" in entry:
            requires = entry["requires"]
            if (not isinstance(requires, list) or not requires
                    or not all(isinstance(r, str) and r.strip() for r in requires)):
                raise LevelsError(f"{where} level {number}: 'requires' must be a non-empty array of condition ids")
            level["requires"] = list(requires)
        levels.append(level)
    levels.sort(key=lambda lv: lv["level"])
    numbers = [lv["level"] for lv in levels]
    if numbers != list(range(1, len(numbers) + 1)):
        raise LevelsError(
            f"{where}: levels must be numbered 1..{len(numbers)} with no gap or "
            f"duplicate (got {numbers})"
        )
    return levels


def _parse_criterion(raw: Any, where: str) -> Criterion:
    if not isinstance(raw, dict):
        raise LevelsError(f"{where}: each criterion must be a table")
    _check_keys(raw, _CRITERION_KEYS, where)
    signal = _text(raw, "signal", where)
    if any(not part for part in signal.split(".")):
        raise LevelsError(f"{where}: malformed signal path {signal!r}")
    op = raw.get("op")
    if op not in OPERATORS:
        raise LevelsError(f"{where}: unknown operator {op!r} (one of {', '.join(OPERATORS)})")
    if "eq_signal" in raw:
        other = parse_eq_signal(raw, op, f"{where} {signal}")
        return {"signal": signal, "op": op, "value": other, "eq_signal": other}
    value: Any = raw.get("value")
    _check_operand(op, value, f"{where} {signal}")
    return {"signal": signal, "op": op, "value": value}


def _check_operand(op: str, value: Any, where: str) -> None:
    if op in _ORDER_OPS:
        if not _is_number(value):
            raise LevelsError(f"{where}: '{op}' needs a number (got {value!r})")
    elif op in _MEMBER_OPS:
        kinds = {_kind(v) for v in value} if isinstance(value, list) else {None}
        if not value or len(kinds) != 1 or None in kinds:
            raise LevelsError(f"{where}: '{op}' needs a non-empty array of one scalar type")
    elif _kind(value) is None:
        raise LevelsError(f"{where}: '{op}' needs a string, number or boolean")


def condition_id(row_id: str, level: int) -> str:
    """The id of one condition: a row's level, ``<row id>.<level>`` (#744)."""
    return f"{row_id}.{level}"


def _check_requires(rows: list[Row]) -> None:
    """Every ``requires`` names an existing condition, never itself, with no cycle."""
    edges = {condition_id(row["id"], lv["level"]): lv.get("requires", []) for row in rows for lv in row["levels"]}
    for cid, needs in edges.items():
        unknown = sorted(n for n in needs if n not in edges)
        if unknown:
            raise LevelsError(f"condition {cid!r}: 'requires' names unknown condition(s) {', '.join(unknown)}")
    done: set[str] = set()

    def visit(cid: str, path: tuple[str, ...]) -> None:
        if cid in path:
            raise LevelsError(f"condition {cid!r}: 'requires' forms a cycle ({' -> '.join((*path, cid))})")
        if cid in done:
            return
        for need in edges[cid]:
            visit(need, (*path, cid))
        done.add(cid)

    for cid in sorted(edges):
        visit(cid, ())


def _whole(value: Any) -> int | None:
    """``value`` when it is a whole number (a bool is not), else None."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _parse_gate(raw: Any, where: str, rows: Mapping[str, Row]) -> StepGate:
    if not isinstance(raw, dict):
        raise LevelsError(f"{where}: each gate must be a table")
    _check_keys(raw, _GATE_KEYS, where)
    gate_id, row_id = _text(raw, "id", where), _text(raw, "row", where)
    level = _whole(raw.get("level"))
    if row_id not in rows:
        raise LevelsError(f"{where} gate {gate_id!r}: unknown row {row_id!r}")
    if level is None or not 1 <= level <= len(rows[row_id]["levels"]):
        raise LevelsError(f"{where} gate {gate_id!r}: 'level' must be a level of row {row_id!r} "
                          f"(got {raw.get('level')!r})")
    return {"id": gate_id, "row": row_id, "level": level}


def _parse_steps(raw: Any, rows: list[Row]) -> list[StepDef]:
    """``[[steps]]``: numbered 1..K, gates accumulating, no count threshold (#1083)."""
    if not isinstance(raw, list) or not raw:
        raise LevelsError("maturity model: 'steps' must be a non-empty array of tables")
    by_id = {row["id"]: row for row in rows}
    steps: list[StepDef] = []
    for index, entry in enumerate(raw):
        where = f"step #{index + 1}"
        if not isinstance(entry, dict):
            raise LevelsError(f"{where}: must be a table")
        if REMOVED_STEP_KEY in entry:
            raise LevelsError(f"{where}: 'threshold' was removed (issue #1083): a Step needs its gates only")
        _check_keys(entry, _STEP_KEYS, where)
        number, gates = _whole(entry.get("step")), entry.get("gates", [])
        if number != index + 1 or number is None:
            raise LevelsError(f"{where}: steps must be numbered 1..K in order (got {entry.get('step')!r})")
        if not isinstance(gates, list):
            raise LevelsError(f"{where}: 'gates' must be an array of tables")
        step: StepDef = {"step": number, "name": _text(entry, "name", where),
                         "gates": [_parse_gate(g, where, by_id) for g in gates]}
        if number == 1 and step["gates"]:
            raise LevelsError(f"{where}: step 1 is always reached: no gates")
        if steps:
            below = steps[-1]
            missing = [g["id"] for g in below["gates"] if g not in step["gates"]]
            if missing:
                raise LevelsError(f"{where}: gates accumulate; missing {', '.join(missing)} from step {number - 1}")
            if len(step["gates"]) == len(below["gates"]):
                raise LevelsError(f"{where}: step {number} adds no gate over step {number - 1}, "
                                  "so it is always held with it (Steps read gates only, #1083)")
        ids = [g["id"] for g in step["gates"]]
        if len(set(ids)) != len(ids):
            raise LevelsError(f"{where}: duplicate gate id")
        steps.append(step)
    return steps


def _validate_paths(rows: list[Row], validator: PathValidator) -> None:
    bad = {(row["scope"], signal, row["id"]) for row in rows for level in row["levels"] for c in level["criteria"]
           for signal in (c["signal"], *([c["eq_signal"]] if "eq_signal" in c else []))
           if not validator(row["scope"], signal)}
    if bad:
        raise UnknownSignalError(list(bad))


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------


def _is_number(value: Any) -> bool:
    """A finite int or float. TOML allows nan and inf; they compare False and
    serialise to invalid JSON, so neither an operand nor a signal may be one."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    # An int is always finite; math.isfinite would convert it to float and
    # raise OverflowError on a huge JSON integer, aborting a fleet run.
    return isinstance(value, int) or math.isfinite(value)


def _kind(value: Any) -> str | None:
    if isinstance(value, bool):
        return "bool"
    if _is_number(value):
        return "number"
    if isinstance(value, str):
        return "string"
    return None


_UNAVAILABLE = object()


def resolve_signal(data: JsonDict | None, path: str) -> Any:
    """The value at a dotted path, or ``_UNAVAILABLE`` (see module docstring)."""
    node: Any = data
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return _UNAVAILABLE
        if node.get("available") is False and part != "available":
            return _UNAVAILABLE
        node = node[part]
    if node is None or (isinstance(node, dict) and node.get("available") is False):
        return _UNAVAILABLE
    return node


_COMPARE: dict[str, Callable[[Any, Any], bool]] = {
    "eq": lambda a, v: bool(a == v),
    "ne": lambda a, v: bool(a != v),
    "lt": lambda a, v: bool(a < v),
    "lte": lambda a, v: bool(a <= v),
    "gt": lambda a, v: bool(a > v),
    "gte": lambda a, v: bool(a >= v),
    "in": lambda a, v: a in v,
    "not_in": lambda a, v: a not in v,
}


def _empty_set(data: JsonDict | None, signal: str) -> str | None:
    """The ``EMPTY_DENOMINATORS`` code when ``signal``'s set has a count of exactly 0."""
    rule = EMPTY_DENOMINATORS.get(signal)
    if rule is None:
        return None
    count = resolve_signal(data, rule[0])
    if isinstance(count, (int, float)) and not isinstance(count, bool) and count == 0:
        return rule[1]
    return None


def unavailable_signal(criterion: Criterion, data: Any) -> str:
    """The path to name for an unknown criterion: its ``eq_signal`` when that is the side
    unavailable and ``signal`` is not, else ``signal``."""
    if ("eq_signal" in criterion and resolve_signal(data, criterion["signal"]) is not _UNAVAILABLE
            and resolve_signal(data, criterion["eq_signal"]) is _UNAVAILABLE):
        return criterion["eq_signal"]
    return criterion["signal"]


def _evaluate_criterion(criterion: Criterion, data: JsonDict | None) -> CriterionOutcome:
    op, value = criterion["op"], criterion["value"]
    actual = resolve_signal(data, criterion["signal"])
    outcome: CriterionOutcome = {
        "signal": criterion["signal"],
        "op": op,
        "value": value,
        "status": "unknown",
        "actual": None,
        "reason": "unavailable",
    }
    empty = _empty_set(data, criterion["signal"])
    if empty is None and "eq_signal" in criterion:  # either side may be a statistic over nothing
        empty = _empty_set(data, criterion["eq_signal"])
    if empty is not None:  # before availability: a full run leaves loc {} with no file scored
        outcome["reason"] = empty
        return outcome
    if actual is _UNAVAILABLE:
        return outcome
    kind = _kind(actual)
    # Only a finite scalar is echoed (_kind is None for anything else): a dict
    # or list may hold a NaN that json.loads accepted and dumps_result must
    # never write (issue #602).
    outcome["actual"] = None if kind is None else actual
    if "eq_signal" in criterion:  # compare two measured signals (lib/levels_fields.py)
        other = resolve_signal(data, criterion["eq_signal"])
        if other is _UNAVAILABLE:
            return outcome
        value, expected = other, _kind(other)
        if kind is None or expected is None:
            kind = None
    else:
        expected = _kind(value[0]) if isinstance(value, list) else _kind(value)
    if kind != expected or kind is None:
        outcome["reason"] = "type_mismatch"
        return outcome
    outcome["reason"] = None
    outcome["status"] = "met" if _COMPARE[op](actual, value) else "unmet"
    return outcome


def evaluate_row(row: Row, data: JsonDict | None) -> Evaluation:
    """One row against one subject, every criterion at every level."""
    outcomes: list[LevelOutcome] = []
    missing: set[str] = set()
    low = 0
    high = len(row["levels"])
    climbing = True
    for level in row["levels"]:
        results = [(c, _evaluate_criterion(c, data)) for c in level["criteria"]]
        unmet = [r for _, r in results if r["status"] != "met"]
        missing.update(unavailable_signal(c, data) for c, r in results if r["status"] == "unknown")
        met: bool | None
        if any(r["status"] == "unmet" for r in unmet):
            met = False
        else:
            met = None if unmet else True
        outcomes.append({"level": level["level"], "met": met, "unmet": unmet})
        climbing = climbing and met is True
        if climbing:
            low = level["level"]
        elif met is False and high == len(row["levels"]):
            high = level["level"] - 1
    known = low == high
    evaluation: Evaluation = {
        "status": "level" if known else "unknown",
        "level": low if known else None,
        "bounds": [low, high],
        "missing_signals": sorted(missing),
        "levels": outcomes,
    }
    if not known and low >= 1:
        evaluation["floor"] = low
    return evaluation


def floor_of(entry: Mapping[str, Any]) -> int | None:
    """The confirmed floor of an unknown row or evaluation, or None.

    Reads the optional ``floor`` key (#697): a whole number of at least 1 on a
    row whose status is ``unknown``. Absent (a row written before #697, a known
    row, nothing confirmed) or malformed reads as None, never as a level.
    """
    floor = entry.get("floor")
    if entry.get("status") == "level" or type(floor) is not int or floor < 1:
        return None
    return floor


def share_level(levels: list[int], share: Fraction, top: int) -> int:
    """Highest level at least ``share`` of ``levels`` reach (0 always qualifies)."""
    best = 0
    for candidate in range(1, top + 1):
        if sum(1 for lv in levels if lv >= candidate) >= share * len(levels):
            best = candidate
    return best


def _team_result(row: Row, repos: dict[str, JsonDict]) -> RowResult:
    aggregation = row["aggregation"]
    assert aggregation is not None  # parse_levels guarantees it on team rows
    evaluations = {name: evaluate_row(row, repos[name]) for name in sorted(repos)}
    unknown = [name for name, ev in evaluations.items() if ev["level"] is None]
    missing = sorted({s for ev in evaluations.values() for s in ev["missing_signals"]})
    top = len(row["levels"])
    share = Fraction(str(aggregation["share"]))
    low = share_level([ev["bounds"][0] for ev in evaluations.values()], share, top)
    high = share_level([ev["bounds"][1] for ev in evaluations.values()], share, top)
    determined = bool(evaluations) and low == high
    result: RowResult = {
        "id": row["id"],
        "title": row["title"],
        "scope": "team",
        "status": "level" if determined else "unknown",
        "level": low if determined else None,
        "missing_signals": missing,
        "aggregation": aggregation,
        "repos": evaluations,
        "unknown_repos": unknown,
    }
    if not determined and low >= 1:
        result["floor"] = low
    return result


def _org_result(row: Row, settings: JsonDict | None) -> RowResult:
    evaluation = evaluate_row(row, settings)
    result: RowResult = {
        "id": row["id"],
        "title": row["title"],
        "scope": "org",
        "status": evaluation["status"],
        "level": evaluation["level"],
        "missing_signals": evaluation["missing_signals"],
        "levels": evaluation["levels"],
    }
    if "floor" in evaluation:
        result["floor"] = evaluation["floor"]
    return result


def evaluate(maturity_model: MaturityModel, group: Group) -> LevelsResult:
    """Every row of ``maturity_model`` against ``group``, stamped with the maturity-model version."""
    rows = [
        _org_result(row, group["settings"]) if row["scope"] == "org" else _team_result(row, group["repos"])
        for row in maturity_model["rows"]
    ]
    return {"levels_version": maturity_model["version"], "group": group["name"], "rows": rows}


def dumps_result(result: LevelsResult) -> str:
    """Canonical JSON: sorted keys, fixed indent, trailing newline.

    ``allow_nan=False``: a NaN or infinity anywhere raises ``ValueError``
    rather than writing a document no strict JSON parser reads.
    """
    return json.dumps(result, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
