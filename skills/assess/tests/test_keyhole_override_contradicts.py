"""Keyhole-signal unit tests for one finding family.

Covers the override_contradicts_signals finding (archetype marker vs signals).

The pure, git-free derivation core; end-to-end wiring (build_run_context)
is covered in test_assess_core.py. Split from the former
test_keyhole_signals.py by finding family; shared integrate() fixtures live
in keyhole_helpers.py.
"""
from __future__ import annotations

from pathlib import Path

from keyhole_helpers import (
    _BLEEDING_COMMIT_SETS,
    _COMPLEXITY_STATS,
    _finding_paths,
    _MODULAR_STRUCTURE,
    _stale_doc_staleness,
)
from lib import keyhole_signals as ks


# --- override_contradicts_signals finding (archetype marker vs signals) ------

def test_override_contradicts_in_finding_order_and_actions() -> None:
    """override_contradicts_signals is a named finding, positioned before the
    one positive finding (refactor_boundary stays last)."""
    assert "override_contradicts_signals" in ks.FINDING_ORDER
    assert "override_contradicts_signals" in ks.FINDING_ACTIONS
    order = ks.FINDING_ORDER
    assert order.index("override_contradicts_signals") < order.index("refactor_boundary")
    assert ks.FINDING_ACTIONS["override_contradicts_signals"] == (
        "Review archetype marker - deterministic signals suggest a different "
        "classification"
    )


def test_integrate_override_contradiction_fires_finding() -> None:
    """A contradicting archetype block makes the finding fire against its source."""
    archetype = {
        "available": True,
        "override_contradicts_signals": True,
        "override_source": "CLAUDE.md",
    }
    result = ks.integrate(
        repo_root=Path("/nonexistent"),
        complexity_stats=_COMPLEXITY_STATS,
        doc_staleness=_stale_doc_staleness(churn_degenerate=False),
        dead_code={"available": False, "candidate_count": 0,
                   "candidates": [], "tools": []},
        observability={"rung": None, "reachable": {"present": False}},
        structure=_MODULAR_STRUCTURE,
        commit_sets=_BLEEDING_COMMIT_SETS,
        archetype=archetype,
    )
    assert _finding_paths(result, "override_contradicts_signals") == ["CLAUDE.md"]


def test_integrate_override_contradiction_absent_is_silent() -> None:
    """No archetype contradiction -> the finding is silent (empty paths)."""
    result = ks.integrate(
        repo_root=Path("/nonexistent"),
        complexity_stats=_COMPLEXITY_STATS,
        doc_staleness=_stale_doc_staleness(churn_degenerate=False),
        dead_code={"available": False, "candidate_count": 0,
                   "candidates": [], "tools": []},
        observability={"rung": None, "reachable": {"present": False}},
        structure=_MODULAR_STRUCTURE,
        commit_sets=_BLEEDING_COMMIT_SETS,
        archetype={"available": True, "override_contradicts_signals": False},
    )
    assert _finding_paths(result, "override_contradicts_signals") == []
