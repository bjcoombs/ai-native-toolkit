"""The "a broken scan must never block the assessment" wrapper.

Kept apart from ``scan_registry`` so the modules the table's adapters import
(``context_blocks`` among them) can use it without importing the table.
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
