"""Pure signal renames: an old signal path read under its new name (issues #783, #970).

``SIGNAL_ALIASES`` maps an old signal path to its new one, for pure renames only (the
meaning is unchanged; a change of meaning is never aliased, #774). 2.2.0 (#911, completing
#783) renamed the two config-only signals whose names claimed a run:
``ci_checks.test_or_lint_runs`` is ``ci_checks.test_or_lint_configured`` and
``coverage_gate.enforced`` is ``coverage_gate.configured``. Three readers apply the table:

- ``dealias_model`` (``lib.scoring.levels.parse_levels``, before validation): a ``--levels`` model
  copied from 2.1.0 that names an old path in a team row's ``signal`` or ``eq_signal`` loads
  with the new path, and one ``deprecated signal path`` line per distinct old path goes to
  stderr. Org rows are left alone (their paths are ghreport's, not a scan's).
- ``aliased`` (``lib.fleet.fleet_rescore``): a stored scan holding the old path and not the new one
  is also readable under the new path, so a re-score causes no Recount line.
- ``renamed`` (``lib.fleet.fleet_compare.compare``, both sides): the old leaf moves to the new
  path, so comparing a 2.1.0 row with a 2.2.0 row lists no added or removed leaf for a rename.

Neither scan helper overwrites a new path that is already present, and neither changes its
input. This module imports nothing from ``lib``, so ``levels`` and ``fleet_rescore`` can
both import it without a cycle. Tests: ``tests/test_signal_aliases.py``.
"""
from __future__ import annotations

import copy
import sys
from collections.abc import Mapping, Sequence
from typing import Any, TextIO

SIGNAL_ALIASES: Mapping[str, str] = {
    "ci_checks.test_or_lint_runs": "ci_checks.test_or_lint_configured",
    "coverage_gate.enforced": "coverage_gate.configured",
}

_CRITERION_PATHS = ("signal", "eq_signal")


def deprecation_line(old: str, new: str) -> str:
    """The one stderr line a model naming ``old`` gets."""
    return (f"levels: deprecated signal path {old!r}, read as {new!r}; "
            f"rename it in the maturity model")


def dealias_model(data: Mapping[str, Any], aliases: Mapping[str, str] = SIGNAL_ALIASES,
                  stream: TextIO | None = None) -> Mapping[str, Any]:
    """``data`` (a raw maturity model table) with each team criterion's old paths renamed.

    Returns ``data`` itself when nothing is renamed; otherwise a copy, after writing one
    deprecation line per distinct old path (sorted) to ``stream`` (stderr by default).
    A malformed table is passed over here and rejected by the parser as before."""
    found: set[str] = set()
    out: dict[str, Any] = copy.deepcopy(dict(data))
    rows = out.get("rows")
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or row.get("scope") == "org":
            continue
        levels = row.get("levels")
        for level in levels if isinstance(levels, list) else []:
            criteria = level.get("criteria") if isinstance(level, dict) else None
            for criterion in criteria if isinstance(criteria, list) else []:
                for key in _CRITERION_PATHS:
                    path = criterion.get(key) if isinstance(criterion, dict) else None
                    if isinstance(path, str) and path in aliases:
                        criterion[key] = aliases[path]
                        found.add(path)
    if not found:
        return data
    for old in sorted(found):
        print(deprecation_line(old, aliases[old]), file=stream or sys.stderr)
    return out


def aliased(scans: Mapping[str, Any], aliases: Mapping[str, str] = SIGNAL_ALIASES) -> dict[str, Any]:
    """``scans`` with each old aliased path also readable under its new path (never overwriting)."""
    out: dict[str, Any] = copy.deepcopy(dict(scans))
    for old, new in sorted(aliases.items()):
        value, found = _get(out, old.split("."))
        if found and not _get(out, new.split("."))[1]:
            _put(out, new.split("."), copy.deepcopy(value))
    return out


def renamed(scans: Any, aliases: Mapping[str, str] = SIGNAL_ALIASES) -> Any:
    """``scans`` with each old aliased leaf moved to its new path; a non-map is returned as is.

    An old path whose new path is already present is left in place, so a real difference
    between the two is never hidden."""
    if not isinstance(scans, dict):
        return scans
    out: dict[str, Any] = copy.deepcopy(scans)
    for old, new in sorted(aliases.items()):
        value, found = _get(out, old.split("."))
        if found and not _get(out, new.split("."))[1]:
            _put(out, new.split("."), value)
            _drop(out, old.split("."))
    return out


def _get(data: Mapping[str, Any], path: Sequence[str]) -> tuple[Any, bool]:
    node: Any = data
    for part in path:
        if not isinstance(node, dict) or part not in node:
            return None, False
        node = node[part]
    return node, True


def _put(data: dict[str, Any], path: Sequence[str], value: Any) -> None:
    node = data
    for part in path[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = node[part] = {}
        node = child
    node[path[-1]] = value


def _drop(data: dict[str, Any], path: Sequence[str]) -> None:
    parent, _ = _get(data, path[:-1])
    if isinstance(parent, dict):
        parent.pop(path[-1], None)
