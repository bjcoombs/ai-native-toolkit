"""Caller-supplied maturity rubrics: TOML loader and deterministic evaluator.

An organisation running a maturity programme scores against its own rubric:
rows (practices or capabilities), each with numbered whole levels, each level
a list of criteria over named signal paths. This module loads such a rubric
and evaluates it from signals alone, with no model in the path, so a fleet
scan reaches the same levels every time.

Rubric file (TOML, read with the stdlib ``tomllib``):

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
  criteria = [{ signal = "instructions_grade", op = "in", value = ["A", "B", "C"] }]
```

Semantics:

- Levels are whole numbers numbered 1..K with no gap; level 0 is the implicit
  "no level reached". A row reaches level N only when every criterion of N and
  of every lower level holds. No halves, no averages.
- A signal is unavailable when its key is missing, its value is null, or the
  path runs through a block carrying ``available: false`` (reading that
  ``available`` flag itself is allowed). A criterion whose signal is
  unavailable, or whose value has the wrong type for the operator, is
  ``unknown``. A row with any unknown criterion, at any level, is ``unknown``
  with every such signal named: never 0 and never passed. Every criterion at
  every level is evaluated, so the result does not depend on criterion order
  and the unmet criteria of every level are exposed for next-step guidance.
  Consequence: a null that a scan means as "absent" (``instructions_grade:
  null``) reads as unknown here.
- ``team`` rows run against each repository's ``run-context.json``, then roll
  up: ``min`` is the level every repository reaches, ``share`` the highest
  level reached by at least that share of the repositories passed in (the
  caller passes the active ones). An unknown repository is bounded both ways
  (level 0 and level K); the team level is reported only when both bounds
  agree, otherwise the row is unknown. No repositories at all is unknown.
- ``org`` rows run once against the group settings; settings of ``None`` (not
  gathered) make every org row unknown.

Loading raises ``RubricError`` before any evaluation on a malformed rubric: a
missing version, an unset or invalid scope, a gap or duplicate in level
numbering, an unknown operator or key, a team row with no aggregation rule, or
a signal path the pluggable ``PathValidator`` rejects. ``dumps_result`` gives
byte-identical JSON for identical inputs.
"""
from __future__ import annotations

import json
import tomllib
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Mapping, NotRequired, TypedDict

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

_ROOT_KEYS = frozenset({"version", "rows"})
_ROW_KEYS = frozenset({"id", "title", "scope", "aggregation", "share", "levels"})
_LEVEL_KEYS = frozenset({"level", "criteria"})
_CRITERION_KEYS = frozenset({"signal", "op", "value"})


class RubricError(ValueError):
    """The rubric cannot be loaded; the message names the row and the fault."""


class Criterion(TypedDict):
    signal: str
    op: str
    value: Scalar | list[Scalar]


class Level(TypedDict):
    level: int
    criteria: list[Criterion]


class Aggregation(TypedDict):
    rule: str
    share: int | float


class Row(TypedDict):
    id: str
    title: str
    scope: Scope
    aggregation: Aggregation | None
    levels: list[Level]


class Rubric(TypedDict):
    version: str
    rows: list[Row]


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
    missing_signals: list[str]
    levels: list[LevelOutcome]


class RowResult(TypedDict):
    id: str
    title: str
    scope: Scope
    status: Literal["level", "unknown"]
    level: int | None
    missing_signals: list[str]
    levels: NotRequired[list[LevelOutcome]]
    aggregation: NotRequired[Aggregation]
    repos: NotRequired[dict[str, Evaluation]]
    unknown_repos: NotRequired[list[str]]


class RubricResult(TypedDict):
    rubric_version: str
    group: str
    rows: list[RowResult]


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def known_paths(org: Iterable[str], team: Iterable[str]) -> PathValidator:
    """A validator accepting exactly the given paths per scope."""
    allowed: dict[Scope, frozenset[str]] = {"org": frozenset(org), "team": frozenset(team)}
    return lambda scope, path: path in allowed[scope]


def load_rubric(path: Path, validator: PathValidator | None = None) -> Rubric:
    """Read and validate a TOML rubric file; raise ``RubricError`` on any fault."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise RubricError(f"cannot read rubric {path.name}: {exc}") from exc
    return parse_rubric(data, validator)


def parse_rubric(data: Mapping[str, Any], validator: PathValidator | None = None) -> Rubric:
    """Validate an already-parsed rubric table; raise ``RubricError`` on any fault."""
    _check_keys(data, _ROOT_KEYS, "rubric")
    version = data.get("version")
    if not isinstance(version, str) or not version.strip():
        raise RubricError("rubric: 'version' must be a non-empty string")
    raw_rows = data.get("rows")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise RubricError("rubric: 'rows' must be a non-empty array of tables")
    rows: list[Row] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_rows):
        row = _parse_row(raw, index)
        if row["id"] in seen:
            raise RubricError(f"row {row['id']!r}: duplicate row id")
        seen.add(row["id"])
        rows.append(row)
    if validator is not None:
        _validate_paths(rows, validator)
    return {"version": version, "rows": rows}


def _check_keys(table: Mapping[str, Any], allowed: frozenset[str], where: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise RubricError(f"{where}: unknown key(s) {', '.join(unknown)}")


def _text(table: Mapping[str, Any], key: str, where: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RubricError(f"{where}: '{key}' must be a non-empty string")
    return value


def _parse_row(raw: Any, index: int) -> Row:
    if not isinstance(raw, dict):
        raise RubricError(f"row #{index + 1}: must be a table")
    row_id = _text(raw, "id", f"row #{index + 1}")
    where = f"row {row_id!r}"
    _check_keys(raw, _ROW_KEYS, where)
    title = _text(raw, "title", where)
    scope = raw.get("scope")
    if scope not in SCOPES:
        raise RubricError(f"{where}: 'scope' must be one of {', '.join(SCOPES)} (got {scope!r})")
    aggregation = _parse_aggregation(raw, where, scope)
    levels = _parse_levels(raw.get("levels"), where)
    return {"id": row_id, "title": title, "scope": scope, "aggregation": aggregation, "levels": levels}


def _parse_aggregation(raw: Mapping[str, Any], where: str, scope: Scope) -> Aggregation | None:
    rule = raw.get("aggregation")
    share: Any = raw.get("share")
    if scope == "org":
        if rule is not None or share is not None:
            raise RubricError(f"{where}: an org row takes no aggregation rule")
        return None
    if rule not in AGGREGATION_RULES:
        raise RubricError(
            f"{where}: a team row needs 'aggregation' set to one of "
            f"{', '.join(AGGREGATION_RULES)} (got {rule!r})"
        )
    if rule == "min":
        if share is not None:
            raise RubricError(f"{where}: 'share' applies only to aggregation = \"share\"")
        return {"rule": "min", "share": 1}
    if not _is_number(share) or not 0 < share <= 1:
        raise RubricError(f"{where}: 'share' must be a number in (0, 1] (got {share!r})")
    return {"rule": "share", "share": share}


def _parse_levels(raw: Any, where: str) -> list[Level]:
    if not isinstance(raw, list) or not raw:
        raise RubricError(f"{where}: 'levels' must be a non-empty array of tables")
    levels: list[Level] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise RubricError(f"{where}: each level must be a table")
        _check_keys(entry, _LEVEL_KEYS, where)
        number = entry.get("level")
        if not isinstance(number, int) or isinstance(number, bool):
            raise RubricError(f"{where}: 'level' must be a whole number (got {number!r})")
        criteria = entry.get("criteria")
        if not isinstance(criteria, list) or not criteria:
            raise RubricError(f"{where} level {number}: 'criteria' must be a non-empty array")
        parsed = [_parse_criterion(c, f"{where} level {number}") for c in criteria]
        levels.append({"level": number, "criteria": parsed})
    levels.sort(key=lambda lv: lv["level"])
    numbers = [lv["level"] for lv in levels]
    if numbers != list(range(1, len(numbers) + 1)):
        raise RubricError(
            f"{where}: levels must be numbered 1..{len(numbers)} with no gap or "
            f"duplicate (got {numbers})"
        )
    return levels


def _parse_criterion(raw: Any, where: str) -> Criterion:
    if not isinstance(raw, dict):
        raise RubricError(f"{where}: each criterion must be a table")
    _check_keys(raw, _CRITERION_KEYS, where)
    signal = _text(raw, "signal", where)
    if any(not part for part in signal.split(".")):
        raise RubricError(f"{where}: malformed signal path {signal!r}")
    op = raw.get("op")
    if op not in OPERATORS:
        raise RubricError(f"{where}: unknown operator {op!r} (one of {', '.join(OPERATORS)})")
    value: Any = raw.get("value")
    _check_operand(op, value, f"{where} {signal}")
    return {"signal": signal, "op": op, "value": value}


def _check_operand(op: str, value: Any, where: str) -> None:
    if op in _ORDER_OPS:
        if not _is_number(value):
            raise RubricError(f"{where}: '{op}' needs a number (got {value!r})")
    elif op in _MEMBER_OPS:
        kinds = {_kind(v) for v in value} if isinstance(value, list) else {None}
        if not value or len(kinds) != 1 or None in kinds:
            raise RubricError(f"{where}: '{op}' needs a non-empty array of one scalar type")
    elif _kind(value) is None:
        raise RubricError(f"{where}: '{op}' needs a string, number or boolean")


def _validate_paths(rows: list[Row], validator: PathValidator) -> None:
    bad = sorted(
        {
            f"{row['scope']}:{c['signal']} (row {row['id']!r})"
            for row in rows
            for level in row["levels"]
            for c in level["criteria"]
            if not validator(row["scope"], c["signal"])
        }
    )
    if bad:
        raise RubricError(f"unknown signal path(s): {'; '.join(bad)}")


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


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
    if actual is _UNAVAILABLE:
        return outcome
    outcome["actual"] = actual
    expected = _kind(value[0]) if isinstance(value, list) else _kind(value)
    if _kind(actual) != expected:
        outcome["reason"] = "type_mismatch"
        return outcome
    outcome["reason"] = None
    outcome["status"] = "met" if _COMPARE[op](actual, value) else "unmet"
    return outcome


def evaluate_row(row: Row, data: JsonDict | None) -> Evaluation:
    """One row against one subject, every criterion at every level."""
    outcomes: list[LevelOutcome] = []
    missing: set[str] = set()
    reached = 0
    climbing = True
    for level in row["levels"]:
        results = [_evaluate_criterion(c, data) for c in level["criteria"]]
        unmet = [r for r in results if r["status"] != "met"]
        missing.update(r["signal"] for r in unmet if r["status"] == "unknown")
        met: bool | None
        if any(r["status"] == "unmet" for r in unmet):
            met = False
        else:
            met = None if unmet else True
        outcomes.append({"level": level["level"], "met": met, "unmet": unmet})
        climbing = climbing and met is True
        if climbing:
            reached = level["level"]
    if missing:
        return {"status": "unknown", "level": None, "missing_signals": sorted(missing), "levels": outcomes}
    return {"status": "level", "level": reached, "missing_signals": [], "levels": outcomes}


def _share_level(levels: list[int], share: Fraction, top: int) -> int:
    """Highest level at least ``share`` of ``levels`` reach (0 always qualifies)."""
    best = 0
    for candidate in range(1, top + 1):
        if sum(1 for lv in levels if lv >= candidate) >= share * len(levels):
            best = candidate
    return best


def _team_result(row: Row, repos: dict[str, JsonDict]) -> RowResult:
    aggregation = row["aggregation"]
    assert aggregation is not None  # parse_rubric guarantees it on team rows
    evaluations = {name: evaluate_row(row, repos[name]) for name in sorted(repos)}
    unknown = [name for name, ev in evaluations.items() if ev["level"] is None]
    missing = sorted({s for name in unknown for s in evaluations[name]["missing_signals"]})
    top = len(row["levels"])
    share = Fraction(str(aggregation["share"]))
    known = [ev["level"] for ev in evaluations.values() if ev["level"] is not None]
    low = _share_level(known + [0] * len(unknown), share, top)
    high = _share_level(known + [top] * len(unknown), share, top)
    determined = bool(evaluations) and low == high
    return {
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


def _org_result(row: Row, settings: JsonDict | None) -> RowResult:
    evaluation = evaluate_row(row, settings)
    return {
        "id": row["id"],
        "title": row["title"],
        "scope": "org",
        "status": evaluation["status"],
        "level": evaluation["level"],
        "missing_signals": evaluation["missing_signals"],
        "levels": evaluation["levels"],
    }


def evaluate(rubric: Rubric, group: Group) -> RubricResult:
    """Every row of ``rubric`` against ``group``, stamped with the rubric version."""
    rows = [
        _org_result(row, group["settings"]) if row["scope"] == "org" else _team_result(row, group["repos"])
        for row in rubric["rows"]
    ]
    return {"rubric_version": rubric["version"], "group": group["name"], "rows": rows}


def dumps_result(result: RubricResult) -> str:
    """Canonical JSON: sorted keys, fixed indent, trailing newline."""
    return json.dumps(result, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
