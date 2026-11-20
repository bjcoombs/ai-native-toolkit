"""The signal namespace rubric paths are checked against, and the default rubric.

``lib.rubric`` takes a pluggable ``PathValidator``; this module is the real
one. A rubric row's ``scope`` picks the namespace:

- ``team`` paths read one repository's signals. The first segment is a block
  name from the published headless schema (``references/signals.schema.json``,
  ``scans.propertyNames``): the same name keys the block in a headless
  document's ``scans`` and in a full ``run-context.json``, so one rubric reads
  either. Pass ``document["scans"]`` (or the run-context) as the repository's
  data. Where a block has a typed shape (``counts_only_schema.ROOTS``, the
  TypedDicts the counts-only allow-list is checked against) every further
  segment must name a field, may not step into a list, and must end on a
  scalar. A block without a typed shape is open: any sub-path is accepted, so
  a typo there loads and evaluates ``unknown`` with the path named. Run-context
  keys outside the schema (``instructions_grade``, ``skills_count``) are
  rejected: a fleet run never has them. The deterministic layer grades are
  published as the ``layer_grades`` block (``lib.layer_grades``), so a rubric
  names ``layer_grades.instructions_grade`` instead; a typed block, so an
  unknown field under it fails at load.
- ``org`` paths read one group's settings fragment from ``ghreport``
  (``skills/ghreport/scripts/ghreport.sh``, "org / group settings"):
  ``scope``, ``provider``, and ``value`` / ``reason`` under each setting.

``DEFAULT_RUBRIC`` (``skills/assess/rubrics/default.toml``) is a deterministic
approximation of the 0-8 layers; its header says where and why it can disagree
with the ``/assess`` layer verdicts.
"""
from __future__ import annotations

import json
import types
import typing
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

from lib.counts_only_schema import ROOTS
from lib.rubric import PathValidator, Rubric, Scope, load_rubric
from lib.signals_schema import SCHEMA_PATH

DEFAULT_RUBRIC = Path(__file__).resolve().parents[2] / "rubrics" / "default.toml"

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
    """The ``PathValidator`` for ``lib.rubric.load_rubric``."""

    def validate(scope: Scope, path: str) -> bool:
        return path in ORG_PATHS if scope == "org" else team_path_ok(path)

    return validate


def load_default_rubric() -> Rubric:
    """The shipped default rubric, paths checked against the schema."""
    return load_rubric(DEFAULT_RUBRIC, signal_path_validator())
