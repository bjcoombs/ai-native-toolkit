"""Signals whose meaning changed at a ``signals_schema_version`` bump (issue #867).

``SIGNALS_SCHEMA_VERSION`` (``lib/run/signals.py``) is bumped when a scanner change alters
when an existing signal reads true or null without a new signal name. ``MEANING_CHANGES``
records, for each version from 2, the signal paths whose meaning changed at the bump to it,
so the fleet comparison can enforce the convention instead of stating it: a repository
scanned at an earlier version reads each such signal as absent, never as a value to compare.

- ``stale_paths(then, now)``: the paths that changed meaning between two versions (either
  order), an empty set at one version (known or not), or None when a version is not an integer from 1 to
  the newest in the table (another toolkit's version: every signal may have changed).
- ``without(scans, paths)``: ``scans`` with each path (a block, or a dotted leaf or sub-block)
  removed; the input is never changed.
- ``held(scans, path)``: ``path`` is present in ``scans``.
- ``reads(path, signals)``: a changed path touches one of the signals a row reads (equal, or
  one a prefix of the other on a ``.`` boundary).

A pure rename is aliased instead (``lib/signal_aliases.py``). ``tests/test_signal_meaning.py``
fails when ``SIGNALS_SCHEMA_VERSION`` moves without an entry here, so a bump cannot ship
without saying what changed. This module imports nothing from ``lib``, so
``lib/fleet/fleet_compare.py`` imports it without a cycle.
"""
from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping
from typing import Any

# Version -> the signal paths whose meaning changed at the bump to it. Newest last.
MEANING_CHANGES: Mapping[int, tuple[str, ...]] = {
    # 2.2.0: the convention was stated; its two renames are aliases (lib/signal_aliases.py)
    2: (),
    # 3.0.0, #821: nested-only instruction files count; instruction_file_present may read null
    3: ("layer_grades.instruction_file_present", "layer_grades.instructions_grade"),
    # 4.0.0: #823 observability counts standard loggers and tracing, not test paths; #1045
    # loaded_in_full takes a file over 5000 tokens; #1152 toolkit_gate counts as resolved
    4: ("observability.rung", "observability.instrumented", "instruction_placement.loaded_in_full",
        "ci_checks.resolved_includes"),
}


def _known(version: object) -> int | None:
    return version if type(version) is int and 1 <= version <= max(MEANING_CHANGES) else None


def stale_paths(then: object, now: object) -> frozenset[str] | None:
    """The signal paths whose meaning changed between versions ``then`` and ``now``.

    Empty at one version, even one this toolkit does not know; otherwise None when either is
    not a version this toolkit knows."""
    if type(then) is int and type(now) is int and then == now:
        return frozenset()
    a, b = _known(then), _known(now)
    if a is None or b is None:
        return None
    low, high = min(a, b), max(a, b)
    return frozenset(path for version in range(low + 1, high + 1)
                     for path in MEANING_CHANGES.get(version, ()))


def without(scans: Any, paths: Iterable[str]) -> Any:
    """``scans`` with each of ``paths`` removed (a copy); a non-map is returned as is."""
    if not isinstance(scans, dict):
        return scans
    out: dict[str, Any] = copy.deepcopy(scans)
    for path in sorted(paths):
        *head, last = path.split(".")
        node: Any = out
        for part in head:
            node = node.get(part) if isinstance(node, dict) else None
        if isinstance(node, dict):
            node.pop(last, None)
    return out


def held(scans: Any, path: str) -> bool:
    """``path`` is present in ``scans``."""
    node = scans
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return True


def reads(path: str, signals: Iterable[str]) -> bool:
    """``path`` and one of ``signals`` are the same signal, or one holds the other."""
    return any(path == s or s.startswith(path + ".") or path.startswith(s + ".") for s in signals)
