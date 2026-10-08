"""E1 trust axis: complexity hotspots whose tests are hollow.

A hotspot is untrusted when its tests let a high fraction of mutants survive:
the suite visits the code but does not pin it. The signal needs per-file
mutation data, which only the consent-gated opt-in mutation pass produces, so
the default read-only run reports no untrusted hotspot. ``keyhole_signals``
folds the result into ``derived_findings``; ``mutation_refresh`` recomputes it
after the opt-in pass. A leaf module: it imports no other ``lib`` module.
"""

from __future__ import annotations

from typing import Any

# A hotspot is "untrusted" when at least this fraction of its mutants survive -
# the suite runs the code but doesn't pin it. Asymmetric like dead-weight: this
# fires only on positive mutation evidence, so a read-only /assess (no opt-in
# mutation pass) reports no untrusted hotspots rather than guessing.
DEFAULT_SURVIVOR_DENSITY_THRESHOLD = 0.3


def find_untrusted_hotspots(
    complexity_stats: dict[str, Any],
    test_pressure: dict[str, Any],
    threshold_survivor_density: float = DEFAULT_SURVIVOR_DENSITY_THRESHOLD,
) -> list[str]:
    """E1: complexity hotspots whose tests are hollow (mutants survive).

    Crosses the complexity hotspot list with the per-file mutation survivor
    density. A hotspot whose tests let a high fraction of mutants survive is a
    trust failure: the suite *visits* the code but doesn't *pin* it. Returns the
    sorted hotspot paths over the density threshold.

    Degrades to ``[]`` whenever there is no per-file mutation data - the default
    read-only /assess run never mutates, so E1 stays silent rather than
    manufacturing a finding from the always-on cheap heuristics. It speaks only
    when an opt-in mutation pass populated ``test_pressure.per_file``.
    """
    if not isinstance(test_pressure, dict):
        return []
    per_file = test_pressure.get("per_file") or []
    if not per_file:
        return []
    hotspot_paths = {
        h.get("path")
        for h in complexity_stats.get("top_hotspots", [])
        if h.get("path")
    }
    density_by_file: dict[str, float] = {}
    for entry in per_file:
        total = entry.get("total")
        survived = entry.get("survived") or 0
        if total:
            density_by_file[entry.get("file")] = survived / total
    return sorted(
        p for p in hotspot_paths
        if density_by_file.get(p, 0.0) >= threshold_survivor_density
    )


def untrusted_hotspot_paths(
    complexity_stats: dict[str, Any], test_pressure: dict[str, Any] | None,
) -> list[str]:
    """E1 trust axis: complexity hotspots whose tests are hollow.

    Silent without opt-in mutation data, so it degrades cleanly on the default
    read-only run.
    """
    try:
        return find_untrusted_hotspots(complexity_stats, test_pressure or {})
    except Exception:  # noqa: BLE001 - degrade, never crash
        return []
