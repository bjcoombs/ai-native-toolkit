"""Per-stage and per-scan wall time for one /assess run (#1380), a diagnostic only.

``scan_registry.run_scans`` times each scan it calls and each stage it runs with
``timed`` and records the seconds in the inputs mapping under ``TIMINGS_KEY``, never
in the run context it fills, so a caller that compares two run contexts built by
``run_scans`` sees no difference. ``assess_core`` copies the block into the run
context as ``timings`` (``lib.run_context_types_timings.TimingsBlock``) and prints
``summary_line`` to stderr at the end of a run.

The figures differ on every run, so nothing that is compared between runs carries
them: ``counts_only`` drops the block (``DIAGNOSTIC_KEYS``), the golden normalisation
masks it, and the cross-run stats diff reads the complexity sidecars, which never
hold it. They exist to show which scans to run concurrently first (#798).
Tests: ``tests/test_scan_registry.py``, ``tests/test_scan_timings.py``.
"""
from __future__ import annotations

import time
from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from typing import Any, Literal

from lib.run_context_types_timings import TimingsBlock

TIMINGS_KEY = "timings"
# Run-context keys that hold diagnostics, not measurements: counts-only output drops them.
DIAGNOSTIC_KEYS = frozenset({TIMINGS_KEY})
_PLACES = 3
SLOWEST = 3


def timings_block(inputs: MutableMapping[str, Any]) -> TimingsBlock:
    """The run's ``TimingsBlock`` in ``inputs``, created empty on first use."""
    block = inputs.get(TIMINGS_KEY)
    if block is None:
        block = TimingsBlock(total_seconds=0.0, stages={}, scans={})
        inputs[TIMINGS_KEY] = block
    return block


@contextmanager
def timed(inputs: MutableMapping[str, Any], kind: Literal["stages", "scans"], key: str) -> Iterator[None]:
    """Record the wall time of the ``with`` body under ``timings[kind][key]``, even when it raises.

    A stage's time also refreshes ``total_seconds`` as the sum of the stages.
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        block = timings_block(inputs)
        block[kind][key] = round(time.perf_counter() - start, _PLACES)
        if kind == "stages":
            block["total_seconds"] = round(sum(block["stages"].values()), _PLACES)


def summary_line(block: TimingsBlock | None, slowest: int = SLOWEST) -> str:
    """One line naming the scan stages' total and the ``slowest`` scans, slowest first, ties by key.

    The publish step and the counts-only write run after the scans and are not counted, so the
    total reads lower than the script's own wall time; the line says ``scans total`` for that.
    """
    if not block:
        return "timings: not recorded"
    ranked = sorted(block["scans"].items(), key=lambda kv: (-kv[1], kv[0]))[:slowest]
    named = ", ".join(f"{key} {seconds:.1f}s" for key, seconds in ranked) or "none"
    return f"timings: scans total {block['total_seconds']:.1f}s; slowest scans: {named}"
