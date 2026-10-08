"""Keyhole-signal unit tests for one finding family.

Covers the accretion_ratchet finding.

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


# --- Accretion ratchet finding -----------------------------------------------

def _accreting_block(*, reliable: bool = True) -> dict:
    """A minimal well-formed accretion_ratchet run-context block."""
    return {
        "available": True,
        "reliable": reliable,
        "deletion_fraction_threshold": 0.15,
        "total_in_band": 2,
        "files": [
            {
                "path": "src/fat.py",
                "net_additions": 2400,
                "commit_count": 31,
                "deletion_fraction": 0.0,
                "time_span_months": 18.0,
            },
            {
                "path": "lib/bloat.py",
                "net_additions": 800,
                "commit_count": 12,
                "deletion_fraction": 0.05,
                "time_span_months": 6.0,
            },
        ],
    }


def test_accretion_ratchet_finding_returns_paths_worst_first() -> None:
    """Files are returned sorted by net additions descending, then path."""
    run_ctx = {"accretion_ratchet": _accreting_block()}
    paths = ks._accretion_ratchet_finding(run_ctx)
    # net_additions: fat.py=2400 > bloat.py=800
    assert paths == ["src/fat.py", "lib/bloat.py"]


def test_accretion_ratchet_finding_unavailable_returns_empty() -> None:
    """Block with available=False -> no finding paths (graceful degrade)."""
    run_ctx = {"accretion_ratchet": {"available": False, "files": []}}
    assert ks._accretion_ratchet_finding(run_ctx) == []


def test_accretion_ratchet_finding_absent_block_returns_empty() -> None:
    """Missing accretion_ratchet key -> no finding paths."""
    assert ks._accretion_ratchet_finding({}) == []


def test_accretion_ratchet_finding_no_files_returns_empty() -> None:
    """Available block but empty files list -> no finding paths."""
    run_ctx = {"accretion_ratchet": {"available": True, "reliable": True, "files": []}}
    assert ks._accretion_ratchet_finding(run_ctx) == []


def test_accretion_ratchet_finding_action_text_matches_config() -> None:
    """The action string in FINDING_ACTIONS matches the requirement."""
    assert ks.FINDING_ACTIONS["accretion_ratchet"] == (
        "refactor down: extract, delete dead code, or split the file"
    )


def test_format_accretion_items_roll_up_and_per_file_lines() -> None:
    """Roll-up sentence + one line per file in worst-first order."""
    run_ctx = {"accretion_ratchet": _accreting_block()}
    items = ks._format_accretion_items(run_ctx)
    # First item is the roll-up sentence.
    assert items[0].startswith("2 files show monotonic growth")
    assert "2 hottest" in items[0]
    # Per-file lines include path, LOC, time span, commits, deletion fraction.
    # Net physical lines from git numstat (not scored LOC), and the share of
    # churn that was deletions (not a count of net reductions).
    assert any(
        "src/fat.py" in line and "+2,400 lines net" in line for line in items
    )
    assert any(
        "lib/bloat.py" in line and "5% of churn deleted" in line for line in items
    )
    assert not any("LOC" in line or "net reductions" in line for line in items)
    # Worst offender comes first.
    fat_idx = next(i for i, line in enumerate(items) if "src/fat.py" in line)
    bloat_idx = next(i for i, line in enumerate(items) if "lib/bloat.py" in line)
    assert fat_idx < bloat_idx


def test_format_accretion_items_reliable_false_adds_disclaimer() -> None:
    """Unreliable history appends a disclaimer line."""
    run_ctx = {"accretion_ratchet": _accreting_block(reliable=False)}
    items = ks._format_accretion_items(run_ctx)
    assert any("UNRELIABLE" in line for line in items)


def test_format_accretion_items_reliable_true_no_disclaimer() -> None:
    """Reliable history: no disclaimer line."""
    run_ctx = {"accretion_ratchet": _accreting_block(reliable=True)}
    items = ks._format_accretion_items(run_ctx)
    assert not any("UNRELIABLE" in line for line in items)


def test_accretion_ratchet_in_finding_order_and_actions() -> None:
    """accretion_ratchet is present in both FINDING_ORDER and FINDING_ACTIONS."""
    assert "accretion_ratchet" in ks.FINDING_ORDER
    assert "accretion_ratchet" in ks.FINDING_ACTIONS
    # Placement: after unactioned_intent, before orphaned_understanding.
    order = ks.FINDING_ORDER
    ui_idx = order.index("unactioned_intent")
    ar_idx = order.index("accretion_ratchet")
    ou_idx = order.index("orphaned_understanding")
    assert ui_idx < ar_idx < ou_idx


def test_integrate_accretion_ratchet_wired_in() -> None:
    """When accretion_ratchet block is passed to integrate(), the finding fires.

    assemble_findings sorts paths alphabetically for determinism - the finding
    result carries both files regardless of net_additions order.
    """
    accreting = _accreting_block()
    result = ks.integrate(
        repo_root=Path("/nonexistent"),
        complexity_stats=_COMPLEXITY_STATS,
        doc_staleness=_stale_doc_staleness(churn_degenerate=False),
        dead_code={"available": False, "candidate_count": 0,
                   "candidates": [], "tools": []},
        observability={"rung": None, "reachable": {"present": False}},
        structure=_MODULAR_STRUCTURE,
        commit_sets=_BLEEDING_COMMIT_SETS,
        accretion_ratchet=accreting,
    )
    paths = _finding_paths(result, "accretion_ratchet")
    # assemble_findings sorts alphabetically: lib/ before src/
    assert paths == sorted(["src/fat.py", "lib/bloat.py"])
    assert "src/fat.py" in paths
    assert "lib/bloat.py" in paths


def test_integrate_accretion_ratchet_absent_is_silent() -> None:
    """Without accretion_ratchet arg, the finding is silent (empty paths)."""
    result = ks.integrate(
        repo_root=Path("/nonexistent"),
        complexity_stats=_COMPLEXITY_STATS,
        doc_staleness=_stale_doc_staleness(churn_degenerate=False),
        dead_code={"available": False, "candidate_count": 0,
                   "candidates": [], "tools": []},
        observability={"rung": None, "reachable": {"present": False}},
        structure=_MODULAR_STRUCTURE,
        commit_sets=_BLEEDING_COMMIT_SETS,
    )
    assert _finding_paths(result, "accretion_ratchet") == []
