"""Run-context shape for the ``timings`` diagnostic block (#1380).

A sibling of ``lib/run_context_types.py``, which is at its line budget. Built by
``lib/run/scan_timings.py``.
"""
from __future__ import annotations

from typing import TypedDict


class TimingsBlock(TypedDict):
    """``timings``: where one run spent its wall time, in seconds to the millisecond.

    ``stages`` maps each ``scan_registry.STAGES`` stage to its wall time and ``scans``
    each scan key that ran to its own, both in run order; a scan the plan skipped
    has no entry. ``total_seconds`` is the sum of the scan stages: the publish step and
    the counts-only write that follow are not counted. A diagnostic only:
    it differs on every run, so counts-only output and the golden normalisation
    leave it out.
    """

    total_seconds: float
    stages: dict[str, float]
    scans: dict[str, float]
