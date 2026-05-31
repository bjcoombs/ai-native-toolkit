"""Write-side truth pressure: does the test suite actually pin behaviour down?

Coverage says a line *ran*; it never says an assertion would *fail* if that line
were wrong. This package gathers the signals that distinguish a suite which
holds the code to account from one that merely visits it. Three tiers, cheapest
first, each degrading to "not assessed" rather than ever blocking the
assessment:

**Mutation tier (decisive, expensive, opt-in)** - ``mutation.py``. Config
detection plus a bounded, opt-in mutation run and survivor aggregation.

**Cheap-heuristic tier (always-on, candidate signals only)** - ``heuristics.py``.
Three syntactic fingerprints of hollow tests: assertion-on-internal,
untested-boundaries, duplicate-truth.

**Aggregation + facade** - ``aggregate.py``. ``scan_test_pressure`` merges both
tiers into the ``test_pressure`` block consumed by run-context.json.

This ``__init__`` is the stable public facade: it re-exports the same names the
former single-module ``lib.test_pressure`` exposed, so importers and tests need
no change. ``shutil`` and ``subprocess`` are re-exported so existing tests can
monkeypatch ``lib.test_pressure.shutil`` / ``.subprocess`` (the same singleton
module objects the mutation tier calls through).

Boundary: every signal here is a *candidate*. None is a verdict. The output is
grist for human (or LLM) judgement, scoped and labelled as such.
"""
from __future__ import annotations

# Re-exported so tests can monkeypatch lib.test_pressure.shutil/.subprocess.
import shutil
import subprocess

from .aggregate import (
    TestPressureResult,
    _overall_coverage,
    scan_test_pressure,
)
from .common import MAX_FINDINGS
from .heuristics import (
    CHEAP_HEURISTIC_NOTE,
    compute_cheap_heuristics,
    detect_assertion_on_internal,
    detect_duplicate_truth,
    detect_untested_boundaries,
)
from .mutation import (
    CLUSTER_MIN_SURVIVORS,
    HIGH_COVERAGE,
    LOW_MUTATION_SCORE,
    MAX_FILES_TO_MUTATE,
    MUTATION_TIMEOUT,
    _parse_cargo_mutants,
    _parse_gremlins,
    _parse_mutmut,
    _parse_stryker_json,
    compute_gap_signal,
    compute_survivor_density,
    detect_mutation_config,
    identify_survivor_clusters,
    run_bounded_mutation,
)

__all__ = [
    "scan_test_pressure",
    "TestPressureResult",
    "detect_mutation_config",
    "run_bounded_mutation",
    "compute_survivor_density",
    "identify_survivor_clusters",
    "compute_gap_signal",
    "compute_cheap_heuristics",
    "detect_assertion_on_internal",
    "detect_untested_boundaries",
    "detect_duplicate_truth",
    "CHEAP_HEURISTIC_NOTE",
    "MUTATION_TIMEOUT",
    "MAX_FILES_TO_MUTATE",
    "MAX_FINDINGS",
    "CLUSTER_MIN_SURVIVORS",
    "HIGH_COVERAGE",
    "LOW_MUTATION_SCORE",
]
