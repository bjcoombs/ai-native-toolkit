"""Truth tags, the legacy layer alias and the additive condition fields of a maturity model (#843).

``lib.levels`` parses a maturity model; this module holds the rules for four
condition fields it reads, so the loader stays one file and the rules one page.

**``truth``** (on a level, so on one condition) is required and multi-valued: a
non-empty array of distinct tags from ``TRUTH_TAGS``, the truth the condition
keeps honest (``skills/assess/references/scoring-model.md``). A tag outside the
set, an empty array or a repeated tag fails the load, naming the row, the level
and the cause.

**The legacy layer alias.** A model written before #843 carries no ``truth``
anywhere. The alias applies only when **no condition in the model carries
``truth``**: every condition then resolves its tags from its row
(``legacy_truth``). A row id ``layer-<n>-...`` takes ``LEGACY_LAYER_TRUTH[n]``;
the default model's three rows outside the layers take ``LEGACY_ROW_TRUTH``; any
other row (a caller's own model) resolves to no tag, ``[]``, since it names no
layer. A model where some conditions carry ``truth`` and others do not is
rejected with ``MixedTruthError``, whose message starts with that name and gives
the model file, the row id and the level of the first condition without it. No
format-marker field decides the rule: a partly migrated model always fails.

Layer to truth, the documented mapping of the alias:

| Layer / row | Truth |
|---|---|
| 0 (`layer-0-...`) | `agent_context` |
| 1 | `agent_runtime` |
| 2, 3, 4 | `structure` |
| 5, 6 | `behaviour` |
| 7, 8 | `governance` |
| `doc-freshness` | `docs` |
| `guardrails-stated`, `agent-consistency` | `agent_context` |

**Additive fields.** Each defaults to today's behaviour when absent:

- ``polarity`` on a level: ``"positive"`` (the default) or ``"hazard"``. A
  hazard is a harm, never a practice, so ``lib.level_tally`` never counts it
  toward the tally (it is listed when met) and marks its condition
  ``polarity: "hazard"``. It changes no row level, and no rule lets a hazard
  block a Step yet (#828).
- ``guarded_by`` on a level: one condition id (``<row id>.<level>``), the guard
  that tests this condition's own check. Absent (the default) means no guard.
  It is one hop: ``lib.level_tally`` caps the condition's grade at
  ``measured`` while the guard is not ``met``, so the condition is ``verified``
  only when its check is itself under test. The grade cap never changes what is
  counted (``measured`` and ``verified`` both count), its state, a row level or
  a Step. An id that names no condition, the condition itself or a hazard (whose
  ``met`` means the harm is present, so it cannot stand for "under test") fails
  the load naming the id and ``guarded_by``.
- ``eq_signal`` on a criterion: a second signal path, in the form
  ``{ signal = "<path>", op = "eq", eq_signal = "<path>" }`` (``op`` ``eq`` or
  ``ne``, no ``value``). The criterion compares the two measured signals, for
  "generated equals source" and "docs agree with config". Absent (the default)
  means the criterion compares ``signal`` with its ``value``. Either signal
  unavailable makes the criterion ``unknown``; two values of different kinds,
  or a value that is not a scalar, ``type_mismatch``; either side an empty-set
  statistic (``signal_grades.EMPTY_DENOMINATORS``) takes that signal's code. The parsed criterion
  keeps the path as its ``value`` too, so every reader of ``value`` shows the
  path it compares with.
"""
from __future__ import annotations

from typing import Any, Mapping

TRUTH_TAGS: tuple[str, ...] = ("behaviour", "contracts", "docs", "structure", "agent_context",
                               "agent_runtime", "governance", "supply_chain")
POLARITIES: tuple[str, ...] = ("positive", "hazard")
DEFAULT_POLARITY = "positive"
HAZARD = "hazard"
EQ_SIGNAL_OPS: tuple[str, ...] = ("eq", "ne")

LEGACY_LAYER_TRUTH: dict[int, tuple[str, ...]] = {
    0: ("agent_context",), 1: ("agent_runtime",), 2: ("structure",), 3: ("structure",),
    4: ("structure",), 5: ("behaviour",), 6: ("behaviour",), 7: ("governance",), 8: ("governance",),
}
LEGACY_ROW_TRUTH: dict[str, tuple[str, ...]] = {
    "doc-freshness": ("docs",),
    "guardrails-stated": ("agent_context",),
    "agent-consistency": ("agent_context",),
}


class LevelsError(ValueError):
    """The maturity model cannot be loaded; the message names the row and the fault."""


class MixedTruthError(LevelsError):
    """Some conditions carry ``truth`` and others do not, so neither ``truth`` nor the alias applies."""


def parse_truth(raw: Any, where: str) -> list[str]:
    """A declared ``truth`` array, validated; ``where`` names the row and the level."""
    if not isinstance(raw, list):
        raise LevelsError(f"{where}: 'truth' must be an array of tags (got {raw!r})")
    if not raw:
        raise LevelsError(f"{where}: 'truth' is empty; give at least one of {', '.join(TRUTH_TAGS)}")
    unknown = [t for t in raw if t not in TRUTH_TAGS]
    if unknown:
        raise LevelsError(f"{where}: unknown truth tag(s) {', '.join(map(repr, unknown))} "
                          f"(one of {', '.join(TRUTH_TAGS)})")
    if len(set(raw)) != len(raw):
        raise LevelsError(f"{where}: 'truth' repeats a tag ({raw!r})")
    return list(raw)


def legacy_truth(row_id: str) -> list[str]:
    """The alias tags of a condition in row ``row_id`` (see the module doc)."""
    if row_id in LEGACY_ROW_TRUTH:
        return list(LEGACY_ROW_TRUTH[row_id])
    head, _, rest = row_id.partition("-")
    number, _, _ = rest.partition("-")
    if head == "layer" and number.isdigit() and int(number) in LEGACY_LAYER_TRUTH:
        return list(LEGACY_LAYER_TRUTH[int(number)])
    return []


def check_truth(untagged: list[tuple[str, int]], total: int, source: str) -> bool:
    """True when the legacy alias applies (no condition tagged); raise on a mixed model.

    ``untagged`` lists ``(row id, level)`` of each condition without ``truth``, in
    model order; ``total`` is the number of conditions; ``source`` names the file.
    """
    if len(untagged) == total:
        return True
    if untagged:
        row_id, level = untagged[0]
        raise MixedTruthError(
            f"MixedTruthError: maturity model {source}: row {row_id!r} level {level} has no 'truth' "
            f"while other conditions carry it ({len(untagged)} of {total} conditions untagged); the "
            "legacy layer alias applies only when no condition carries 'truth'")
    return False


def parse_polarity(raw: Any, where: str) -> str:
    if raw not in POLARITIES:
        raise LevelsError(f"{where}: 'polarity' must be one of {', '.join(POLARITIES)} (got {raw!r})")
    return str(raw)


def parse_guard(raw: Any, where: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise LevelsError(f"{where}: 'guarded_by' must be one condition id, <row id>.<level> (got {raw!r})")
    return raw


def check_guards(guards: Mapping[str, str], hazards: Mapping[str, bool]) -> None:
    """Every ``guarded_by`` names an existing, positive condition other than its own.

    ``hazards`` maps every condition id to True when it is a hazard."""
    for cid, guard in sorted(guards.items()):
        if guard == cid:
            raise LevelsError(f"condition {cid!r}: 'guarded_by' names the condition itself")
        if guard not in hazards:
            raise LevelsError(f"condition {cid!r}: 'guarded_by' names unknown condition {guard!r}")
        if hazards[guard]:
            raise LevelsError(f"condition {cid!r}: 'guarded_by' names hazard condition {guard!r}")


def parse_eq_signal(raw: Mapping[str, Any], op: Any, where: str) -> str:
    """The second signal path of an ``eq_signal`` criterion, validated."""
    other = raw.get("eq_signal")
    if not isinstance(other, str) or not other.strip() or any(not part for part in other.split(".")):
        raise LevelsError(f"{where}: 'eq_signal' must be a signal path (got {other!r})")
    if "value" in raw:
        raise LevelsError(f"{where}: a criterion with 'eq_signal' takes no 'value'")
    if op not in EQ_SIGNAL_OPS:
        raise LevelsError(f"{where}: 'eq_signal' needs op {' or '.join(EQ_SIGNAL_OPS)} (got {op!r})")
    return other
