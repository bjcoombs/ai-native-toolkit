"""Truth tags, the legacy layer alias and the additive condition fields of a maturity model (#843).

``lib.scoring.levels`` parses a maturity model; this module holds the rules for the six
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
the default model's twenty rows outside the layers take ``LEGACY_ROW_TRUTH``; any
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
| 5, 6, `skill-script-tests` | `behaviour` |
| 7, 8, `secrets-guard`, `skill-personal-data`, `model-ci-gated` | `governance` |
| `doc-freshness`, `contributor-files` | `docs` |
| `guardrails-stated`, `agent-consistency`, `skill-disclosure`, `skill-library` | `agent_context` |
| `agent-least-privilege`, `agent-job-write-access` | `agent_runtime`, `governance` |
| `agent-evals` | `agent_runtime`, `behaviour` |
| `docs-kept-true` | `docs`, `contracts` |
| `ci-skip-label`, `ci-test-failures-ignored` | `behaviour` |
| `agent-rewrites-instructions` | `agent_context`, `agent_runtime` |
| `ai-policy-enforced`, `ai-policy-declared` | `governance` |
| `backlog-ready` | `agent_context`, `governance` |

**Additive fields.** Each defaults to today's behaviour when absent:

- ``polarity`` on a level: ``"positive"`` (the default) or ``"hazard"``. A
  hazard is a harm, never a practice, so ``lib.scoring.level_tally`` never counts it
  toward the tally (it is listed when met) and marks its condition
  ``polarity: "hazard"``. It changes no row level. A hazard may name ``blocks``,
  the id of the Step gate it undermines (#828): while the hazard is met that gate
  does not hold, whatever its own conditions read, and the tally does not move. A
  ``blocks`` that names no gate of the model's Steps, or ``blocks`` on a positive
  level, fails the load (``apply_hazard``, ``check_hazard_gates``). Its fix path
  is the check manifest's ``fix`` for the condition, as its why is.
- ``guarded_by`` on a level: one condition id (``<row id>.<level>``), the guard
  that tests this condition's own check. Absent (the default) means no guard.
  It is one hop: ``lib.scoring.level_tally`` caps the condition's grade at
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
  path it compares with. The condition's grade is the weaker of the two signals'.
- ``predicted`` and ``review`` on a level, together or not at all (#1086): the
  direction a condition is predicted to move the agent-outcome measure
  (``lib.agent_outcome``, #1085) and the date by which ``/fleet`` history should
  show it. ``predicted`` is one of ``PREDICTIONS``: ``lower_revert`` (a met
  condition goes with a lower ``revert_share``), ``lower_rework`` (a lower
  ``rework_share``) or ``none`` (no effect predicted). ``review`` is a TOML local
  date (``review = 2027-05-23``, unquoted), kept as ISO text so the parsed model
  stays JSON. Absent (the default) means the condition has no review date and the
  exit rule never demotes it; the default model sets none (maintainer decision on
  #1086, no backfill). Neither field changes a criterion, a state, the tally or a
  Step; the catalog shows them.

The rules for the six condition fields of a maturity model (#843); `levels.parse_levels`
calls them, and the module docstring is the reference.

- **`truth`** (required, multi-valued): every level carries a non-empty array of distinct tags
  from `TRUTH_TAGS` (`behaviour`, `contracts`, `docs`, `structure`, `agent_context`,
  `agent_runtime`, `governance`, `supply_chain`). An unknown tag, an empty array or a repeat
  fails the load naming the row, the level and the cause.
- **Legacy layer alias (rule A, maintainer decision of 2026-11-22):** the alias applies only
  when **no condition in the model carries `truth`**. Each condition then takes
  `legacy_truth(row id)`: a `layer-<n>-...` row takes `LEGACY_LAYER_TRUTH[n]` (0
  `agent_context`; 1 `agent_runtime`; 2, 3, 4 `structure`; 5, 6 `behaviour`; 7, 8
  `governance`), the default model's `skill-script-tests` takes `behaviour`, `secrets-guard`, `skill-personal-data` and `model-ci-gated` take `governance`, `doc-freshness` and `contributor-files` take `docs`; `guardrails-stated`,
  `agent-consistency`, `skill-disclosure` and `skill-library` take `agent_context`, `agent-least-privilege` and `agent-job-write-access` take `agent_runtime` and `governance`, `agent-evals` takes `agent_runtime` and `behaviour`, `docs-kept-true` takes `docs` and `contracts`, `ci-skip-label` and `ci-test-failures-ignored` take `behaviour`, `agent-rewrites-instructions` takes `agent_context` and `agent_runtime`, `ai-policy-enforced` and `ai-policy-declared` take `governance`, `backlog-ready` takes `agent_context` and `governance` (`LEGACY_ROW_TRUTH`), and any other row `[]`. A
  model where some conditions carry `truth` and others do not raises **`MixedTruthError`**
  (a `LevelsError`), whose message starts `MixedTruthError:` and names the model file, the
  row id and the level of the first condition without it. There is no format-marker field.
- **`polarity`** on a level: `"positive"` (default) or `"hazard"`. A hazard is parsed as
  `polarity: "hazard"`; `level_tally` never counts it (it is listed when met) and marks its
  condition with `polarity: "hazard"`. No row level reads it. A hazard may name `blocks`, a Step gate
  id: while the hazard is met that gate does not hold (#828). `blocks` naming no gate, or
  `blocks` on a positive level, fails the load. Its fix path is the manifest condition's `fix`.
- **`guarded_by`** on a level: one condition id; default absent (no guard). One hop:
  `level_tally` caps the condition's grade at `measured` while the guard is not `met`, which
  never changes what is counted. An unknown id, the condition itself or a hazard fails the
  load naming the id and `guarded_by`.
- **`eq_signal`** on a criterion: `{ signal = "<path>", op = "eq" | "ne", eq_signal = "<path>" }`
  with no `value`; default absent (compare with `value`). The criterion compares the two
  measured signals: either unavailable is `unknown`, different kinds or a non-scalar
  `type_mismatch`, and either side an empty-set statistic takes that signal's
  `EMPTY_DENOMINATORS` code. The parsed criterion keeps the second path as its `value` as well, so the
  catalog shows what it compares with; the `PathValidator` checks both paths. The fleet
  next-step wording skips the signal's repository-native guidance for such a criterion and names
  both paths. `levels.unavailable_signal` names the unavailable side in `missing_signals` and in
  the tally's `not_measured` reason. The catalog never counts a hazard.
- **`predicted`** and **`review`** on a level, both or neither (#1086): `predicted` one of
  `PREDICTIONS` (`lower_revert`, `lower_rework`, `none`), `review` a TOML local date kept as
  ISO text. Absent means no review date, so the exit rule never demotes the condition. A bad
  value, a quoted or timed date, or one field without the other fails the load naming the row
  and level. They change no result; the catalog shows them (`catalog_md.review_cell`).

Tests: `tests/test_levels_fields.py`; `predicted` and `review` in `tests/test_levels_review.py`.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Literal, Mapping, Sequence, TypedDict

TRUTH_TAGS: tuple[str, ...] = ("behaviour", "contracts", "docs", "structure", "agent_context",
                               "agent_runtime", "governance", "supply_chain")
# The display title of each tag, in TRUTH_TAGS order: the catalog's tag section headings (#870).
TRUTH_TAG_TITLES: dict[str, str] = {
    "behaviour": "Behaviour", "contracts": "Contracts", "docs": "Docs", "structure": "Structure",
    "agent_context": "Agent context", "agent_runtime": "Agent runtime", "governance": "Governance",
    "supply_chain": "Supply chain",
}
POLARITIES: tuple[str, ...] = ("positive", "hazard")
DEFAULT_POLARITY = "positive"
HAZARD = "hazard"
EQ_SIGNAL_OPS: tuple[str, ...] = ("eq", "ne")
# The directions a condition can predict on the agent-outcome measure (#1085, #1086).
PREDICTIONS: tuple[str, ...] = ("lower_revert", "lower_rework", "none")

LEGACY_LAYER_TRUTH: dict[int, tuple[str, ...]] = {
    0: ("agent_context",), 1: ("agent_runtime",), 2: ("structure",), 3: ("structure",),
    4: ("structure",), 5: ("behaviour",), 6: ("behaviour",), 7: ("governance",), 8: ("governance",),
}
LEGACY_ROW_TRUTH: dict[str, tuple[str, ...]] = {
    "doc-freshness": ("docs",),
    "guardrails-stated": ("agent_context",),
    "agent-consistency": ("agent_context",),
    "skill-disclosure": ("agent_context",),
    "skill-library": ("agent_context",),
    "skill-script-tests": ("behaviour",),
    "skill-personal-data": ("governance",),
    "model-ci-gated": ("governance",),
    "contributor-files": ("docs",),
    "secrets-guard": ("governance",),
    "agent-least-privilege": ("agent_runtime", "governance"),
    "agent-job-write-access": ("agent_runtime", "governance"),
    "agent-evals": ("agent_runtime", "behaviour"),
    "docs-kept-true": ("docs", "contracts"),
    "ci-skip-label": ("behaviour",),
    "ci-test-failures-ignored": ("behaviour",),
    "agent-rewrites-instructions": ("agent_context", "agent_runtime"),
    "ai-policy-enforced": ("governance",),
    "ai-policy-declared": ("governance",),
    "backlog-ready": ("agent_context", "governance"),
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


class ModelDemotions(TypedDict, total=False):
    """The fleet exit rule's demoted condition ids on a model (#1086); ``lib.scoring.levels.MaturityModel``
    extends it. Never parsed: ``demoted`` is not a model-file key, so ``parse_levels`` rejects it,
    and only ``fleet.py`` sets it (``lib.fleet.condition_demotion.with_demotions``)."""

    demoted: list[str]


class ReviewFields(TypedDict, total=False):
    """A condition's exit-rule fields (#1086); ``lib.scoring.levels.Level`` extends it."""

    predicted: str  # one of PREDICTIONS
    review: str  # ISO date, YYYY-MM-DD


def apply_review(level: ReviewFields, entry: Mapping[str, Any], where: str) -> None:
    """Copy ``predicted`` and ``review`` from a raw level onto ``level``, validated; both or neither."""
    present = [k for k in ("predicted", "review") if k in entry]
    if not present:
        return
    if len(present) == 1:
        missing = "review" if present == ["predicted"] else "predicted"
        raise LevelsError(f"{where}: 'predicted' and 'review' go together; {missing!r} is missing")
    predicted, review = entry["predicted"], entry["review"]
    if predicted not in PREDICTIONS:
        raise LevelsError(f"{where}: 'predicted' must be one of {', '.join(PREDICTIONS)} (got {predicted!r})")
    if not isinstance(review, dt.date) or isinstance(review, dt.datetime):
        raise LevelsError(f"{where}: 'review' must be a TOML local date, YYYY-MM-DD unquoted (got {review!r})")
    level["predicted"] = str(predicted)
    level["review"] = review.isoformat()


class HazardFields(TypedDict, total=False):
    """A hazard condition's fields (#843, #828); ``lib.scoring.levels.Level`` extends it."""

    polarity: Literal["hazard"]  # absent: positive, the default
    blocks: str  # the id of the Step gate the hazard undermines; absent: it blocks no gate


def apply_hazard(level: HazardFields, entry: Mapping[str, Any], where: str) -> None:
    """Copy ``polarity`` and ``blocks`` from a raw level onto ``level``, validated: ``blocks``
    is one non-empty gate id and belongs to a hazard only."""
    if "polarity" in entry and parse_polarity(entry["polarity"], where) == HAZARD:
        level["polarity"] = "hazard"
    if "blocks" not in entry:
        return
    gate = entry["blocks"]
    if "polarity" not in level:
        raise LevelsError(f"{where}: 'blocks' belongs to a hazard; set polarity = \"hazard\"")
    if not isinstance(gate, str) or not gate.strip():
        raise LevelsError(f"{where}: 'blocks' must be one Step gate id (got {gate!r})")
    level["blocks"] = gate


def check_hazard_gates(rows: Sequence[Mapping[str, Any]], steps: Sequence[Mapping[str, Any]] | None) -> None:
    """Every hazard's ``blocks`` names a gate of the model's ``steps``; nothing to check without Steps."""
    if steps is None:
        return
    gates = {g["id"] for step in steps for g in step["gates"]}
    for row in rows:
        for level in row["levels"]:
            if "blocks" in level and level["blocks"] not in gates:
                raise LevelsError(f"row {row['id']!r} level {level['level']}: 'blocks' names no Step gate "
                                  f"({level['blocks']!r}; one of {', '.join(sorted(gates)) or 'none'})")
