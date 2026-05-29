"""Tests for the B3 static-vs-historical disagreement cross (coupling_analysis).

The inputs (per-directory containment ratios + an optional per-directory
static-modularity view) are **mocked directly** rather than derived from real
git histories and grimp graphs: B3 is a pure function of those two upstream
signals, which already have their own contract tests (test_change_coupling.py,
test_structure_graph.py). Mocking keeps these tests fast and makes the
disagreement logic auditable in isolation. Expected classifications are spelled
out in each test so the contract is explicit.
"""
from __future__ import annotations

from lib.coupling_analysis import (
    DEFAULT_HIGH_CONTAINMENT,
    DEFAULT_LOW_CONTAINMENT,
    detect_hidden_coupling,
    find_refactor_boundaries,
)


def _finding_for(results: list[dict], path: str):
    """The `finding` for `path` in a results list, or KeyError-free None."""
    for r in results:
        if r["path"] == path:
            return r["finding"]
    return "ABSENT"


# --------------------------------------------------------------------------
# detect_hidden_coupling
# --------------------------------------------------------------------------

def test_high_static_modularity_plus_low_containment_is_hidden_coupling():
    """Looks modular statically (high Q) but bleeds historically -> hidden_coupling."""
    containment = {"pkg/a": 0.1}
    static = {"pkg/a": {"modularity_q": 0.6, "front_door_ratio": 0.95}}
    results = detect_hidden_coupling(containment, static_modularity=static)
    assert len(results) == 1
    entry = results[0]
    assert entry["finding"] == "hidden_coupling"
    assert entry["path"] == "pkg/a"
    assert entry["containment_ratio"] == 0.1
    assert "seam" in entry["recommendation"].lower()


def test_high_front_door_alone_triggers_hidden_coupling():
    """Front-door ratio high (A3) is enough to count as 'looks modular'."""
    containment = {"pkg/a": 0.2}
    # modularity low, but a clean front door -> still looks modular statically.
    static = {"pkg/a": {"modularity_q": 0.0, "front_door_ratio": 0.9}}
    results = detect_hidden_coupling(containment, static_modularity=static)
    assert _finding_for(results, "pkg/a") == "hidden_coupling"


def test_low_containment_no_static_input_is_bleeding_module():
    """No static graph at all -> graceful historical-only fallback label."""
    containment = {"pkg/a": 0.15}
    results = detect_hidden_coupling(containment, static_modularity=None)
    assert len(results) == 1
    assert results[0]["finding"] == "bleeding_module"
    assert results[0]["path"] == "pkg/a"


def test_low_containment_dir_absent_from_static_dict_is_bleeding_module():
    """Static graph present but no evidence for THIS dir -> per-dir fallback."""
    containment = {"pkg/a": 0.15}
    static = {"pkg/other": {"modularity_q": 0.6, "front_door_ratio": 0.9}}
    results = detect_hidden_coupling(containment, static_modularity=static)
    assert _finding_for(results, "pkg/a") == "bleeding_module"


def test_low_static_and_high_containment_is_suppressed():
    """Looks coupled statically + never co-changes (high containment) -> suppressed.

    The static graph already surfaces the coupling; with no behavioural bleed
    there is nothing new to flag, so the directory is simply omitted (it does
    not bleed, so it is not this function's concern).
    """
    containment = {"pkg/a": 0.95}  # high: edits stay contained
    static = {"pkg/a": {"modularity_q": -0.1, "front_door_ratio": 0.2}}
    results = detect_hidden_coupling(containment, static_modularity=static)
    assert results == []


def test_low_static_and_low_containment_agree_coupled_finding_none():
    """Static AND history agree it's coupled -> reported but finding=None (not hidden)."""
    containment = {"pkg/a": 0.1}  # bleeds
    static = {"pkg/a": {"modularity_q": -0.2, "front_door_ratio": 0.1}}  # looks coupled
    results = detect_hidden_coupling(containment, static_modularity=static)
    assert len(results) == 1
    assert results[0]["finding"] is None
    assert results[0]["path"] == "pkg/a"


def test_non_bleeding_dirs_are_omitted():
    """Directories at or above the low threshold are not hidden-coupling concerns."""
    containment = {"safe": 0.8, "edge": DEFAULT_LOW_CONTAINMENT, "bleeds": 0.1}
    results = detect_hidden_coupling(containment, static_modularity=None)
    paths = {r["path"] for r in results}
    assert paths == {"bleeds"}  # 0.8 and the 0.3 edge are excluded


def test_low_containment_threshold_is_strict_lower_bound():
    """containment == threshold is NOT low (boundary not flagged); just below IS."""
    containment = {"at": DEFAULT_LOW_CONTAINMENT, "below": DEFAULT_LOW_CONTAINMENT - 0.01}
    results = detect_hidden_coupling(containment, static_modularity=None)
    assert _finding_for(results, "at") == "ABSENT"
    assert _finding_for(results, "below") == "bleeding_module"


def test_custom_low_threshold_is_honoured():
    containment = {"pkg/a": 0.45}
    # default 0.3 would not flag 0.45; raise the bar to 0.5 and it bleeds.
    assert detect_hidden_coupling(containment, threshold_low_containment=0.3) == []
    results = detect_hidden_coupling(containment, threshold_low_containment=0.5)
    assert _finding_for(results, "pkg/a") == "bleeding_module"


def test_hidden_coupling_results_sorted_worst_bleed_first():
    containment = {"a": 0.05, "b": 0.25, "c": 0.15}
    results = detect_hidden_coupling(containment, static_modularity=None)
    assert [r["path"] for r in results] == ["a", "c", "b"]
    assert [r["containment_ratio"] for r in results] == [0.05, 0.15, 0.25]


def test_partial_static_metrics_only_modularity():
    """A static dict carrying only modularity_q (no front_door) still classifies."""
    containment = {"pkg/a": 0.2}
    static = {"pkg/a": {"modularity_q": 0.5}}  # front_door_ratio missing
    assert _finding_for(
        detect_hidden_coupling(containment, static_modularity=static), "pkg/a"
    ) == "hidden_coupling"


def test_empty_containment_returns_empty():
    assert detect_hidden_coupling({}, static_modularity=None) == []
    assert detect_hidden_coupling({}, static_modularity={}) == []


# --------------------------------------------------------------------------
# find_refactor_boundaries
# --------------------------------------------------------------------------

def test_high_containment_is_refactor_boundary():
    """High containment (> 0.7) -> safe zone, even with no static graph."""
    containment = {"pkg/island": 0.9}
    results = find_refactor_boundaries(containment)
    assert len(results) == 1
    entry = results[0]
    assert entry["finding"] == "refactor_boundary"
    assert entry["path"] == "pkg/island"
    assert entry["containment_ratio"] == 0.9
    assert "isolation" in entry["recommendation"].lower()


def test_high_containment_threshold_is_strict_upper_bound():
    """containment == threshold is NOT high enough; just above IS."""
    containment = {"at": DEFAULT_HIGH_CONTAINMENT, "above": DEFAULT_HIGH_CONTAINMENT + 0.01}
    results = find_refactor_boundaries(containment)
    paths = {r["path"] for r in results}
    assert paths == {"above"}


def test_low_containment_is_not_a_refactor_boundary():
    containment = {"pkg/bleeds": 0.2}
    assert find_refactor_boundaries(containment) == []


def test_refactor_boundary_static_agreement_enriches_recommendation():
    """A modular static boundary that agrees gets the 'lenses agree' note."""
    containment = {"pkg/clean": 0.9, "pkg/quiet": 0.85}
    static = {
        "pkg/clean": {"modularity_q": 0.6, "front_door_ratio": 0.95},  # looks modular
        "pkg/quiet": {"modularity_q": -0.1, "front_door_ratio": 0.2},  # looks coupled
    }
    results = find_refactor_boundaries(containment, static_modularity=static)
    by_path = {r["path"]: r for r in results}
    # Both qualify on containment; static only changes the wording.
    assert by_path["pkg/clean"]["finding"] == "refactor_boundary"
    assert by_path["pkg/quiet"]["finding"] == "refactor_boundary"
    assert "agree" in by_path["pkg/clean"]["recommendation"].lower()
    assert "agree" not in by_path["pkg/quiet"]["recommendation"].lower()


def test_refactor_boundaries_sorted_safest_first():
    containment = {"a": 0.75, "b": 0.99, "c": 0.85}
    results = find_refactor_boundaries(containment)
    assert [r["path"] for r in results] == ["b", "c", "a"]


def test_custom_high_threshold_is_honoured():
    containment = {"pkg/a": 0.6}
    assert find_refactor_boundaries(containment, threshold_high_containment=0.7) == []
    results = find_refactor_boundaries(containment, threshold_high_containment=0.5)
    assert _finding_for(results, "pkg/a") == "refactor_boundary"


def test_refactor_empty_containment_returns_empty():
    assert find_refactor_boundaries({}) == []


# --------------------------------------------------------------------------
# The two functions partition the bleed/island space cleanly
# --------------------------------------------------------------------------

def test_hidden_coupling_and_refactor_boundaries_are_disjoint():
    """No directory is both a bleed concern and a safe refactor boundary."""
    containment = {"bleeds": 0.1, "island": 0.95, "middle": 0.5}
    static = {"bleeds": {"modularity_q": 0.6, "front_door_ratio": 0.9}}
    hidden = {r["path"] for r in detect_hidden_coupling(containment, static_modularity=static)}
    safe = {r["path"] for r in find_refactor_boundaries(containment, static_modularity=static)}
    assert hidden == {"bleeds"}
    assert safe == {"island"}
    assert hidden.isdisjoint(safe)
    # "middle" (0.3 <= c <= 0.7) is neither flagged nor declared safe.
