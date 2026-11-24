"""Load errors for an unknown signal path that name the line and the closest real signals (#1151).

``lib.levels`` raises ``UnknownSignalError`` when its ``PathValidator`` refuses a criterion's
signal path. The error keeps the message ``lib.levels`` always gave, ``unknown signal
path(s): team:coverage.gate (row 'coverage-80')``, and adds two things to each path:

- **the fix.** ``closest(scope, path)`` runs ``difflib.get_close_matches`` over the signal
  paths a criterion can name in that scope (``lib.level_signals.published_paths``) and keeps
  up to three. The entry then ends ``did you mean coverage_gate.configured or
  coverage_gate.available?``. With no close path it points at ``docs/what-we-measure.md``,
  which lists every signal.
- **the line.** ``UnknownSignalError.located(text)`` adds ``line N``: the first line of the
  model file at or after the row's ``id = "..."`` line that holds the quoted path.
  ``lib.levels.load_levels`` calls it, since only it holds the file text; a model passed to
  ``parse_levels`` as a table has no lines to name.

``lib.level_signals`` imports ``lib.levels``, so this module reads it only when an error is
built, never at import.

Tests: ``tests/test_levels_hints.py``.
"""
from __future__ import annotations

import difflib
import re

from lib.levels_fields import LevelsError

__all__ = ["CATALOG", "UnknownSignalError", "closest"]

# Where a reader finds every signal a criterion can name, relative to the repository root.
CATALOG = "docs/what-we-measure.md"

# (scope, signal path, row id) for each refused path.
BadPath = tuple[str, str, str]


def closest(scope: str, path: str, limit: int = 3) -> list[str]:
    """Up to ``limit`` published signal paths of ``scope`` close to ``path``, closest first."""
    from lib.level_signals import published_paths  # lib.level_signals imports lib.levels

    if scope not in ("org", "team"):
        return []
    return difflib.get_close_matches(path, published_paths(scope), n=limit, cutoff=0.6)


def _fix(scope: str, path: str) -> str:
    names = closest(scope, path)
    if not names:
        return f"no signal has a close name; {CATALOG} lists them all"
    choices = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} or {names[-1]}"
    return f"did you mean {choices}?"


def _line(text: str, row_id: str, path: str) -> int | None:
    """The 1-based line holding ``"path"`` at or after the line that declares ``row_id``."""
    lines = text.splitlines()
    declares = re.compile(r'^\s*id\s*=\s*"' + re.escape(row_id) + '"')
    start: int = next((i for i, line in enumerate(lines) if declares.match(line)), 0)
    quoted = f'"{path}"'
    order: list[int] = [*range(start, len(lines)), *range(0, start)]
    for index in order:
        if quoted in lines[index]:
            return index + 1
    return None


class UnknownSignalError(LevelsError):
    """One or more criterion signal paths the validator refused, each with its fix."""

    def __init__(self, bad: list[BadPath], lines: dict[BadPath, int] | None = None) -> None:
        self.bad = sorted(bad)
        entries = []
        for item in self.bad:
            scope, path, row_id = item
            where = f" at line {lines[item]}" if lines and item in lines else ""
            entries.append(f"{scope}:{path} (row {row_id!r}){where}, {_fix(scope, path)}")
        super().__init__(f"unknown signal path(s): {'; '.join(entries)}")

    def located(self, text: str) -> UnknownSignalError:
        """The same error with the model file's line number on each path it can find."""
        found = {item: line for item in self.bad if (line := _line(text, item[2], item[1])) is not None}
        return UnknownSignalError(self.bad, found)
