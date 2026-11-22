"""The "a broken scan must never block the assessment" wrapper.

Kept apart from ``scan_registry`` so the modules the table's adapters import
(``context_blocks`` among them) can use it without importing the table.

`safe` and `unavailable`: the "a broken scan never blocks the assessment" wrapper and
its marker. Kept apart from `scan_registry.py` so the modules the table imports
(`context_blocks.py`) can use it without importing the table.
A spec sets `network=True` when it reads GitHub through `gh` (`config_drift`,
`review_reality`, `gate_cost_estimate`, `agent_environment`), and may name an
`offline_fn` - the same scan with its probe off, taking the same reads
(`agent_environment` with `probe_ci=False`). `assess_core` ignores both; the headless
runner in `signals.py` reads them. `validate` rejects an `offline_fn` on a scan that is
not `network`, or one that cannot take the spec's reads.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


def unavailable(label: str, error: Exception) -> dict[str, Any]:
    """The marker a failed scan degrades to."""
    return {"available": False, "reason": f"{label} scan failed: {error}"}


def safe(label: str, fn: Callable[[], Any]) -> Any:
    """Run a read-side scan, degrading to an unavailable marker on any failure.

    Read-side signals are additive context for the LLM, never gates - a broken
    scan must never block the assessment (PRD: "never block").
    """
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - intentional catch-all; degrade, don't crash
        return unavailable(label, e)
