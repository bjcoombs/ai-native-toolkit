"""The one history-window grammar (issue #601).

A window is ``<n>d``, ``<n>w``, ``<n>mo`` or ``<n>y`` with ``n`` a positive
integer without leading zeros: ``90d``, ``12w``, ``6mo``, ``1y``. The headless
runner's ``--window`` (``signals``), the ``ai_usage`` scan's
``history_window``, the fleet activity cutoff (``fleet_activity``) and the
churn-label normaliser (``git_churn.window_from_churn_label``) all read windows
through this module, so the grammar is defined once.

``anchored_since`` turns a window into the absolute ``git log --since=@<epoch>``
bound counted back from the assessed commit's committer time (issue #773), so a
window never resolves against the clock of whoever runs the scan.

The one history-window grammar (issue #601): `<n>d`, `<n>w`, `<n>mo`, `<n>y`.
`split_window` returns `(count, unit)` or raises `ValueError`; it matches the whole
string (`re.fullmatch`), so `"6mo\\n"` is rejected as the ECMA-262 schema pattern rejects it; `WINDOW_PATTERN` is the
unanchored regex fragment. `signals.parse_window`, `ai_usage.resolve_window`,
`fleet_activity.window_start`, `git_churn.window_from_churn_label` and the
`run.schema.json` window pattern (`fleet_schema`) all read it.
`anchored_since(window, anchor_epoch)` is the absolute `"@<epoch>"` start of a window
counted back from an epoch (days and weeks exactly, months and years by calendar,
clamped to the month end, time of day kept); None for all history, or for a start git
cannot read as an epoch (fewer than nine digits, before 1973-03-03). Tests:
`tests/test_history_window.py`, `tests/test_commit_anchor.py`.
`git_commit_info`'s `dirty` flag runs `git status` under the `ASSESS_EXCLUDE_PATHSPEC`
pathspec (issue #414), so the `.assess/` wiki the run has just rewritten - including a
scoped `.assess/<slug>/` - never counts as an uncommitted edit, while a modified tracked
file anywhere else still does. The line is whether a path moves what a scan *measures*,
not whether anything reads it back: a later run does read the wiki (`complexity-stats.prior.json`
for the cross-run diff, `first-flagged.json` for hotspot ages), but that shapes what the
report says about figures already computed. `.assess/config.toml` is the exception -
`assess_config.load_config` reads it before any scan and its excludes decide which files
get measured - so it is checked by a second status call under `ASSESS_CONFIG_PATHSPEC`
and OR-ed back in. That is the seam to keep in step if the config ever grows a second
file or a scope-local path. Both pathspecs are built from `assess_config.ASSESS_DIR` and
`CONFIG_FILE`, the same two constants `load_config` resolves its own path from, so
renaming the directory or the file cannot leave the check pointing at a path that no
longer exists (`test_assess_pathspecs_derive_from_assess_config` pins that).
`content_commit_clock` is the last-content-change clock (issue #333): one `git log` pass
over the docs' history that skips bulk mechanical commits (more than
`BULK_COMMIT_DOC_SHARE` of the docs and at least `BULK_COMMIT_MIN_DOCS` of them, such as
a licence-header sweep), so one sweep cannot reset every doc's staleness. A doc whose
every commit is bulk falls back to its oldest commit and is flagged (`creation_fallback`),
since that is a creation date rather than a content age. Historical paths are mapped
through `change_coupling.build_rename_map`, so a mass rename keeps each doc's earlier
content dates. On a git failure the clock is marked incomplete and degrades to the plain
newest-commit read; a shallow clone is also marked incomplete.
"""
from __future__ import annotations

import calendar
import re
from datetime import datetime, timedelta, timezone
from typing import Literal

WindowUnit = Literal["d", "w", "mo", "y"]

_COUNT = r"[1-9][0-9]*"
_UNIT = r"(?:d|w|mo|y)"

# The window grammar as a regex fragment (no anchors, no capture groups), for
# callers that find a window inside a longer string.
WINDOW_PATTERN = _COUNT + _UNIT

_WINDOW = re.compile(rf"({_COUNT})({_UNIT})")
_UNITS: dict[str, WindowUnit] = {"d": "d", "w": "w", "mo": "mo", "y": "y"}


def split_window(window: str) -> tuple[int, WindowUnit]:
    """``(count, unit)`` for a window such as ``"6mo"`` -> ``(6, "mo")``.

    Raises ``ValueError`` on anything outside the grammar; callers re-raise
    with their own message.
    """
    match = _WINDOW.fullmatch(window)  # "$" would accept a trailing newline
    if match is None:
        raise ValueError(f"not a <n>d|w|mo|y window: {window!r}")
    return int(match.group(1)), _UNITS[match.group(2)]


# git reads a bare number as an epoch only from nine digits (1973-03-03T09:46:40Z);
# a smaller ``@<n>`` matches nothing, so a window starting earlier is all history.
_MIN_GIT_EPOCH = 100_000_000


def anchored_since(window: str | None, anchor_epoch: int) -> str | None:
    """``"@<epoch>"``: the start of ``window`` counted back from ``anchor_epoch``.

    Days and weeks count back exactly; months and years by calendar, clamped to
    the month end (``6mo`` from 2020-08-31 starts 2020-02-29), keeping the time
    of day. None (all history) for a None window, or for a start git cannot
    read as an epoch (before 1973-03-03, or before year 1). Raises
    ``ValueError`` on a window outside the grammar.
    """
    if window is None:
        return None
    n, unit = split_window(window)
    anchor = datetime.fromtimestamp(anchor_epoch, tz=timezone.utc)
    try:
        if unit in ("d", "w"):
            start = anchor - timedelta(days=n * (7 if unit == "w" else 1))
        else:
            months = n * (12 if unit == "y" else 1)
            year, month = divmod(anchor.year * 12 + (anchor.month - 1) - months, 12)
            month += 1
            start = anchor.replace(year=year, month=month,
                                   day=min(anchor.day, calendar.monthrange(year, month)[1]))
    except (ValueError, OverflowError):  # before year 1
        return None
    epoch = int(start.timestamp())
    return f"@{epoch}" if epoch >= _MIN_GIT_EPOCH else None
