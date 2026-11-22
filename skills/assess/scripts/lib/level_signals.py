"""The signal namespace maturity model paths are checked against, and the default maturity model.

``lib.levels`` takes a pluggable ``PathValidator``; this module is the real
one. A maturity model row's ``scope`` picks the namespace:

- ``team`` paths read one repository's signals. The first segment is a block
  name from the published headless schema (``references/signals.schema.json``,
  ``scans.propertyNames``): the same name keys the block in a headless
  document's ``scans`` and in a full ``run-context.json``, so one maturity model reads
  either. Pass ``document["scans"]`` (or the run-context) as the repository's
  data. Where a block has a typed shape (``counts_only_schema.ROOTS``, the
  TypedDicts the counts-only allow-list is checked against) every further
  segment must name a field, may not step into a list, and must end on a
  scalar. A block without a typed shape is open: any sub-path is accepted, so
  a typo there loads and evaluates ``unknown`` with the path named. Run-context
  keys outside the schema (``instructions_grade``, ``skills_count``) are
  rejected: a fleet run never has them. The deterministic layer grades are
  published as the ``layer_grades`` block (``lib.layer_grades``), so a maturity model
  names ``layer_grades.instructions_grade`` instead; a typed block, so an
  unknown field under it fails at load.
- ``org`` paths read one group's settings fragment from ``ghreport``
  (``skills/ghreport/scripts/ghreport.sh``, "org / group settings"):
  ``scope``, ``provider``, and ``value`` / ``reason`` under each setting.

``DEFAULT_LEVELS`` (``skills/assess/levels/default.toml``) is a deterministic
approximation of the 0-8 layers; its header says where and why it can disagree
with the ``/assess`` layer verdicts.

Signal classification (#783). ``SIGNAL_INPUTS`` records the producer input of
every signal a maturity-model criterion reads, keyed by exact signal path so a
new signal has to be classified on purpose; ``input_class`` raises
``UnclassifiedSignal`` naming the path for one that is not. The class set
(``InputClass``) uses the #789 check-manifest evidence names plus the #783
``ci_executed`` class, and each class maps to one confidence grade
(``INPUT_GRADES``, ``config_only < declared < measured < verified``):

- ``config_text``: a value from a config file at HEAD (a threshold, a
  CODEOWNERS pattern, a settings key). Grade ``config_only``.
- ``head_text``: text at HEAD that states a practice (an instruction file, a CI
  line naming a check). Grade ``declared``.
- ``head_structure``: a measurement of the code at HEAD (import graph, LOC,
  claims checked against the tree). Grade ``measured``.
- ``git_history`` and ``execution_artifact``: commit history, and the output of
  running something at the commit. Grade ``measured``.
- ``ci_executed``: a CI line that CI runs on every change; the pipeline
  enforces its result. Grade ``verified``. No default signal has it yet:
  ``ci_checks.test_or_lint_runs`` finds a line naming a check but does not read
  its triggers, so it is ``head_text`` until a signal that does lands (#775).
- ``platform``: read from the GitHub or GitLab API (``review_reality``,
  ``agent_environment.ci_duration``). Grade ``verified``.

``criterion_grade`` grades one criterion: the grade of its signal's input
class, whatever the operator. Per the #789 grades design, the grade comes from
the evidence and is never derived per criterion, so no operator raises it: a
threshold or an absence test on a text count (``observability.rung gte 3``,
``agent_environment.check.named_total gte 2``, ``... lte 0``) is still text, and
stays ``declared`` (or ``config_only``). This supersedes the presence-test rule
in #783 requirement 2, under which text alone could reach a counted grade.
``is_declared`` is true when the grade is ``config_only`` or ``declared``: under
#744 those conditions are listed, not counted. A signal one collector sets from two inputs takes the
weaker (``review_evidence.present`` is ``head_text``; #789 3.4 splits it).
``lib.level_tally`` (#744) reads it to count conditions; ``lib.levels`` does not.

``UNMEASURED_VALUES`` names the signals whose value says the practice could not
be measured, rather than that it is absent, with the reason code the tally
gives: ``test_pressure.mutation_run`` is false until the opt-in mutation pass
runs (``needs_full_run``, never unmet: #740 decision), and
``promissory_markers.aging_reliable`` is false on a history too thin to age a
suppression (``degenerate_history``; the huddle's "stale-suppression aging
requires reliable history").

Maintainer rulings (#783 and #789 triage decisions, 2026-11-21):

- Platform-read signals are their own class, ``platform``, not an execution
  artifact: verified when measured, ``not_measured`` otherwise (the block
  reports itself unavailable, and an unknown never counts).
- Instruction files stay declared (``layer_grades.instruction_file_present`` is
  ``head_text``) but still satisfy a Step gate. Only ``measured`` and
  ``verified`` conditions count toward a level.

Tests: ``tests/test_signal_inputs.py`` (one case per default criterion signal,
so an unclassified one names itself).
"""
from __future__ import annotations

import json
import types
import typing
from functools import lru_cache
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

from lib.counts_only_schema import ROOTS
from lib.levels import Criterion, PathValidator, MaturityModel, Scope, load_levels
from lib.signals_schema import SCHEMA_PATH
from lib.signal_grades import DEFAULT_LEVELS as DEFAULT_LEVELS
from lib.signal_grades import INPUT_GRADES as INPUT_GRADES
from lib.signal_grades import LISTED_GRADES as LISTED_GRADES
from lib.signal_grades import SIGNAL_INPUTS as SIGNAL_INPUTS
from lib.signal_grades import UNMEASURED_VALUES as UNMEASURED_VALUES
from lib.signal_grades import Grade as Grade
from lib.signal_grades import InputClass as InputClass


# ghreport's settings fragment: {scope, provider, <setting>: {value, reason}}.
ORG_SETTINGS = ("two_factor", "push_rules", "default_branch_protection", "rulesets")
ORG_PATHS = frozenset(
    {"scope", "provider"}
    | {f"{name}.{leaf}" for name in ORG_SETTINGS for leaf in ("value", "reason")}
)

# Every block may report itself unavailable, typed or not.
_BLOCK_STATUS = frozenset({"available", "reason"})
_UNIONS = (Union, types.UnionType)
_SCALARS = (str, int, float, bool, type(None))


@lru_cache(maxsize=1)
def scan_names() -> frozenset[str]:
    """The block names the published headless schema allows under ``scans``."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return frozenset(schema["properties"]["scans"]["propertyNames"]["enum"])


def _typed_roots() -> dict[str, list[Any]]:
    roots: dict[str, list[Any]] = {}
    for pattern, shape in ROOTS:
        if "{}" not in pattern:
            roots.setdefault(pattern, []).append(shape)
    return roots


_ROOTS = _typed_roots()


def _fits(tp: Any, rest: list[str]) -> bool:
    """True when ``rest`` walks ``tp`` down to a scalar (or into an open value)."""
    if tp is Any or tp is object:
        return True
    origin = get_origin(tp)
    if origin in _UNIONS:
        return any(_fits(arm, rest) for arm in get_args(tp))
    if origin is typing.NotRequired or origin is typing.Required:
        return _fits(get_args(tp)[0], rest)
    if tp in _SCALARS or origin is Literal:
        return not rest
    if origin is list or tp is list:
        return False  # a criterion cannot index a list, nor compare one
    if origin is dict:
        return bool(rest) and _fits(get_args(tp)[1], rest[1:])
    if typing.is_typeddict(tp):
        if not rest:
            return False  # an object never compares to a scalar
        hints = get_type_hints(tp)
        return rest[0] in hints and _fits(hints[rest[0]], rest[1:])
    return False


def team_path_ok(path: str) -> bool:
    """A dotted path into one repository's signals (see the module doc)."""
    parts = path.split(".")
    if parts[0] not in scan_names():
        return False
    if len(parts) == 2 and parts[1] in _BLOCK_STATUS:
        return True
    # The shortest typed prefix decides: a typed block is walked from its top,
    # an untyped block from the deepest typed field it declares (if any).
    for cut in range(1, len(parts) + 1):
        shapes = _ROOTS.get(".".join(parts[:cut]))
        if shapes:
            return any(_fits(shape, parts[cut:]) for shape in shapes)
    return True


def signal_path_validator() -> PathValidator:
    """The ``PathValidator`` for ``lib.levels.load_levels``."""

    def validate(scope: Scope, path: str) -> bool:
        return path in ORG_PATHS if scope == "org" else team_path_ok(path)

    return validate


def load_default_levels() -> MaturityModel:
    """The shipped default maturity model, paths checked against the schema."""
    return load_levels(DEFAULT_LEVELS, signal_path_validator())


# --- signal classification (#783; see the module doc) -------------------------

class UnclassifiedSignal(KeyError):
    """A criterion reads a signal ``SIGNAL_INPUTS`` has no producer input class for."""


def input_class(signal: str) -> InputClass:
    """The producer input class of ``signal``; raises ``UnclassifiedSignal`` naming it."""
    try:
        return SIGNAL_INPUTS[signal]
    except KeyError:
        raise UnclassifiedSignal(
            f"{signal}: no producer input class in level_signals.SIGNAL_INPUTS") from None


def criterion_grade(criterion: Criterion) -> Grade:
    """The confidence grade of one criterion: its signal's input-class grade, whatever the op."""
    return INPUT_GRADES[input_class(criterion["signal"])]


def is_declared(criterion: Criterion) -> bool:
    """True when the criterion grades ``config_only`` or ``declared``: listed, not counted."""
    return criterion_grade(criterion) in LISTED_GRADES
