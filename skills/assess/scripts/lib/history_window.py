"""The one history-window grammar (issue #601).

A window is ``<n>d``, ``<n>w``, ``<n>mo`` or ``<n>y`` with ``n`` a positive
integer without leading zeros: ``90d``, ``12w``, ``6mo``, ``1y``. The headless
runner's ``--window`` (``signals``), the ``ai_usage`` scan's
``history_window``, the fleet activity cutoff (``fleet_activity``) and the
churn-label normaliser (``git_churn.window_from_churn_label``) all read windows
through this module, so the grammar is defined once.
"""
from __future__ import annotations

import re
from typing import Literal

WindowUnit = Literal["d", "w", "mo", "y"]

# The window grammar as a regex fragment (no anchors, no capture groups), for
# callers that find a window inside a longer string.
WINDOW_PATTERN = r"[1-9][0-9]*(?:d|w|mo|y)"

_WINDOW = re.compile(r"^([1-9][0-9]*)(d|w|mo|y)$")
_UNITS: dict[str, WindowUnit] = {"d": "d", "w": "w", "mo": "mo", "y": "y"}


def split_window(window: str) -> tuple[int, WindowUnit]:
    """``(count, unit)`` for a window such as ``"6mo"`` -> ``(6, "mo")``.

    Raises ``ValueError`` on anything outside the grammar; callers re-raise
    with their own message.
    """
    match = _WINDOW.match(window)
    if match is None:
        raise ValueError(f"not a <n>d|w|mo|y window: {window!r}")
    return int(match.group(1)), _UNITS[match.group(2)]
