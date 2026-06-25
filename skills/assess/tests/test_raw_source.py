"""Tests for raw-source subtree detection (issue #225).

The pure classifier in ``lib.raw_source`` decides, from three per-doc graph
signals, whether a directory subtree is a dump of raw, machine-extracted source
documents that should be excluded from the headline read-side metrics. These
tests pin the threshold contract so the detector behaves identically regardless
of which LLM is driving the surrounding assessment.
"""
from __future__ import annotations

from lib.raw_source import (
    RAW_TREE_ISOLATION_DENSITY,
    RAW_TREE_MACHINE_DENSITY,
    RAW_TREE_MIN_FILES,
    classify_raw_trees,
)


def _signal(in_degree: int = 0, out_degree: int = 0, machine_links: int = 0) -> dict:
    return {
        "in_degree": in_degree,
        "out_degree": out_degree,
        "machine_links": machine_links,
    }


def _raw_tree(prefix: str, n: int, machine_share: float = 1.0) -> dict[str, dict]:
    """A subtree of ``n`` link-isolated docs; ``machine_share`` of them carry a
    machine-extracted (non-navigational) link."""
    signals: dict[str, dict] = {}
    machine_count = round(n * machine_share)
    for i in range(n):
        signals[f"{prefix}/doc-{i:03d}.md"] = _signal(
            machine_links=1 if i < machine_count else 0,
        )
    return signals


def test_no_docs_returns_empty() -> None:
    assert classify_raw_trees({}) == []


def test_large_isolated_machine_tree_detected() -> None:
    signals = _raw_tree("sar-export", RAW_TREE_MIN_FILES + 2)
    trees = classify_raw_trees(signals)
    assert len(trees) == 1
    assert trees[0]["path"] == "sar-export"
    assert trees[0]["file_count"] == RAW_TREE_MIN_FILES + 2
    assert len(trees[0]["docs"]) == RAW_TREE_MIN_FILES + 2


def test_below_min_files_not_detected() -> None:
    signals = _raw_tree("sar-export", RAW_TREE_MIN_FILES - 1)
    assert classify_raw_trees(signals) == []


def test_isolated_but_no_machine_links_not_detected() -> None:
    # A folder of genuinely standalone-but-curated notes: link-isolated, but
    # none carry the machine-extraction fingerprint. Must NOT be excluded.
    signals = _raw_tree("notes", RAW_TREE_MIN_FILES + 5, machine_share=0.0)
    assert classify_raw_trees(signals) == []


def test_well_linked_machine_tree_not_detected() -> None:
    # Files carry machine links but are also internally navigable (in/out edges).
    signals: dict[str, dict] = {}
    for i in range(RAW_TREE_MIN_FILES + 4):
        signals[f"corpus/doc-{i:03d}.md"] = _signal(
            in_degree=2, out_degree=2, machine_links=1,
        )
    assert classify_raw_trees(signals) == []


def test_entry_point_excluded_from_isolation_numerator() -> None:
    # An entry doc in the subtree is not counted as isolated; with enough
    # isolated machine docs around it the tree still qualifies.
    signals = _raw_tree("dump", RAW_TREE_MIN_FILES + 4)
    entry = "dump/index.md"
    signals[entry] = _signal(out_degree=5, machine_links=0)
    trees = classify_raw_trees(signals, entries={entry})
    assert len(trees) == 1
    assert trees[0]["path"] == "dump"


def test_outermost_subtree_is_kept() -> None:
    # Two qualifying batch subtrees nested under a qualifying parent: only the
    # outermost ("export") is reported, not its children.
    signals: dict[str, dict] = {}
    signals.update(_raw_tree("export/batch-1", RAW_TREE_MIN_FILES + 1))
    signals.update(_raw_tree("export/batch-2", RAW_TREE_MIN_FILES + 1))
    trees = classify_raw_trees(signals)
    assert [t["path"] for t in trees] == ["export"]
    assert trees[0]["file_count"] == 2 * (RAW_TREE_MIN_FILES + 1)


def test_two_independent_raw_trees_both_reported() -> None:
    signals: dict[str, dict] = {}
    signals.update(_raw_tree("sar-export", RAW_TREE_MIN_FILES + 1))
    signals.update(_raw_tree("disclosure-dump", RAW_TREE_MIN_FILES + 1))
    trees = classify_raw_trees(signals)
    assert sorted(t["path"] for t in trees) == ["disclosure-dump", "sar-export"]


def test_root_level_docs_never_excluded() -> None:
    # Root-level isolated machine docs (no enclosing subtree) are never excluded
    # so a whole-repo false positive can't zero out the metrics.
    signals = {
        f"doc-{i:03d}.md": _signal(machine_links=1)
        for i in range(RAW_TREE_MIN_FILES + 5)
    }
    assert classify_raw_trees(signals) == []


def test_thresholds_are_tunable() -> None:
    # A smaller tree is detected once the min-files threshold is lowered.
    signals = _raw_tree("small-dump", 4)
    assert classify_raw_trees(signals) == []
    trees = classify_raw_trees(signals, min_files=3)
    assert [t["path"] for t in trees] == ["small-dump"]


def test_machine_density_threshold_boundary() -> None:
    # Exactly at the machine-density threshold qualifies; just below does not.
    n = 20
    at = _raw_tree("a", n, machine_share=RAW_TREE_MACHINE_DENSITY)
    assert [t["path"] for t in classify_raw_trees(at)] == ["a"]
    below = _raw_tree("b", n, machine_share=RAW_TREE_MACHINE_DENSITY - 0.1)
    assert classify_raw_trees(below) == []


def test_isolation_density_threshold() -> None:
    # A subtree where too many docs are linked (below the isolation density)
    # does not qualify even with machine links everywhere.
    n = 20
    linked = round(n * (1 - RAW_TREE_ISOLATION_DENSITY) + 1)
    signals: dict[str, dict] = {}
    for i in range(n):
        is_linked = i < linked
        signals[f"mix/doc-{i:03d}.md"] = _signal(
            in_degree=1 if is_linked else 0,
            out_degree=1 if is_linked else 0,
            machine_links=1,
        )
    assert classify_raw_trees(signals) == []
