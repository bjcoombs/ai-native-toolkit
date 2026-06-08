"""Tests for Signal C: the complexity x doc-state join.

Inputs are MOCKED dicts shaped like the real artifacts (``complexity-stats.json``
and ``analyze_doc_staleness``'s return) -- no git, no filesystem -- so every
``doc_value`` below is hand-computable from the documented formula:

    freshness  = clamp((T - ratio) / T, -1, +1),   T = STALENESS_RATIO_THRESHOLD = 2.0
    doc_value  = complexity_summarised x freshness
    threshold  = max(ccn.p95, MIN_HIGH_CCN=10)
"""
from __future__ import annotations

import json
from pathlib import Path

from lib.doc_complexity_join import (
    MIN_HIGH_CCN,
    STALENESS_RATIO_THRESHOLD,
    analyze_doc_complexity_join,
)

# A repo whose 95th-percentile CCN (8) sits below the McCabe floor, so the
# high-complexity gate is the floor, 10. core.py (CCN 30) is "complex";
# helper.py (CCN 2) is trivial.
COMPLEXITY_STATS = {
    "ccn": {"p50": 3.0, "p95": 8.0, "max": 30.0},
    "files_scored": 2,
    "top_complex": [
        {"path": "pkg/engine/core.py", "loc": 400, "ccn": 30.0, "commits": 50},
        {"path": "pkg/util/helper.py", "loc": 20, "ccn": 2.0, "commits": 1},
    ],
    "top_hotspots": [],
    "top_large": [],
}


def _staleness(docs: list[dict]) -> dict:
    return {"available": True, "churn_window": "12mo", "docs": docs}


def _doc(path: str, ratio: float, confidence: str = "high") -> dict:
    return {
        "path": path,
        "ratio": ratio,
        "last_commit_days": 10,
        "doc_churn_in_window": 4,
        "code_churn_in_window": int(ratio * 4),
        "subject_code_count": 1,
        "subject_method": "nearest-ancestor",
        "confidence": confidence,
    }


def _by_path(result: dict) -> dict[str, dict]:
    return {u["path"]: u for u in result["docs"]}


def test_complex_plus_fresh_is_good_contract() -> None:
    """Fresh doc (ratio 0.5) over CCN-30 code -> good_contract, positive value."""
    staleness = _staleness([_doc("pkg/engine/README.md", ratio=0.5)])
    result = analyze_doc_complexity_join(COMPLEXITY_STATS, staleness, Path("/repo"))

    doc = _by_path(result)["pkg/engine/README.md"]
    # freshness = (2.0 - 0.5) / 2.0 = 0.75 ; doc_value = 30 * 0.75 = 22.5
    assert doc["complexity_summarised"] == 30.0
    assert doc["freshness"] == 0.75
    assert doc["doc_value"] == 22.5
    assert doc["doc_value"] > 0
    assert doc["finding"] == "good_contract"
    assert [u["path"] for u in result["findings"]["good_contracts"]] == [
        "pkg/engine/README.md"
    ]


def test_complex_plus_stale_is_lying_map_negative_value() -> None:
    """Stale doc (ratio 6.0) over CCN-30 code -> lying_map, NEGATIVE value."""
    staleness = _staleness([_doc("pkg/engine/README.md", ratio=6.0)])
    result = analyze_doc_complexity_join(COMPLEXITY_STATS, staleness, Path("/repo"))

    doc = _by_path(result)["pkg/engine/README.md"]
    # freshness = clamp((2 - 6) / 2, -1, 1) = -1.0 ; doc_value = 30 * -1 = -30
    assert doc["freshness"] == -1.0
    assert doc["doc_value"] == -30.0
    assert doc["doc_value"] < 0
    assert doc["finding"] == "lying_map"
    # Slop-doc guard: the recommendation never says "auto-generate".
    rec = doc["recommendation"].lower()
    assert "delete" in rec
    assert "auto-generate" in rec  # ...prefixed by "do not"
    assert "do not auto-generate" in rec


def test_degenerate_churn_caps_confidence_and_suppresses_lying_map() -> None:
    """Issue #172: a precise (nearest-ancestor, high-confidence) association over
    a DEGENERATE churn history must not stamp a lying_map. The churn count means
    nothing - confidence encodes association precision, not measurement
    reliability - so the join caps confidence to 'low' (the existing guard then
    suppresses the finding). Same stale ratio and high confidence as the
    lying_map case; only ``churn_degenerate`` differs."""
    staleness = _staleness([_doc("pkg/engine/README.md", ratio=6.0)])
    staleness["churn_degenerate"] = True
    result = analyze_doc_complexity_join(COMPLEXITY_STATS, staleness, Path("/repo"))

    doc = _by_path(result)["pkg/engine/README.md"]
    # The ratio still computes negative freshness from the inflated churn...
    assert doc["freshness"] == -1.0
    # ...but the measurement is unreliable: confidence is capped and no lie called.
    assert doc["confidence"] == "low"
    assert doc["finding"] is None
    assert result["findings"]["lying_maps"] == []


def test_non_degenerate_churn_preserves_high_confidence_lying_map() -> None:
    """Regression guard: with genuine churn variance (churn_degenerate False, the
    default) the same precise association still produces a high-confidence
    lying_map - the fix must not blunt real findings."""
    staleness = _staleness([_doc("pkg/engine/README.md", ratio=6.0)])
    staleness["churn_degenerate"] = False
    result = analyze_doc_complexity_join(COMPLEXITY_STATS, staleness, Path("/repo"))

    doc = _by_path(result)["pkg/engine/README.md"]
    assert doc["confidence"] == "high"
    assert doc["finding"] == "lying_map"
    assert [u["path"] for u in result["findings"]["lying_maps"]] == [
        "pkg/engine/README.md"
    ]


def test_low_confidence_stale_doc_is_not_a_lying_map() -> None:
    """A stale-by-ratio doc whose staleness is low-confidence (subject_method ==
    'repo-baseline') must NOT be classified a lying_map: the ratio is measured
    against repo-wide churn, not the code the doc describes, so a doc edited
    today reads as 'stale' purely because the repo is busy. Mirrors the Layer 0
    stale-hub confidence guard. Same ratio as the lying_map case above, only the
    confidence differs."""
    staleness = _staleness(
        [_doc("pkg/engine/README.md", ratio=6.0, confidence="low")])
    result = analyze_doc_complexity_join(COMPLEXITY_STATS, staleness, Path("/repo"))

    doc = _by_path(result)["pkg/engine/README.md"]
    # freshness still computes negative from the coarse ratio...
    assert doc["freshness"] == -1.0
    # ...but the low-confidence signal is too coarse to call a lie.
    assert doc["finding"] is None
    assert result["findings"]["lying_maps"] == []


def test_complex_plus_no_doc_is_unexplained_complexity() -> None:
    """CCN-30 code with no doc covering it -> unexplained_complexity, value 0."""
    # Only a doc far away that covers nothing complex.
    staleness = _staleness([_doc("docs/unrelated/notes.md", ratio=0.5)])
    result = analyze_doc_complexity_join(COMPLEXITY_STATS, staleness, Path("/repo"))

    core = _by_path(result)["pkg/engine/core.py"]
    assert core["finding"] == "unexplained_complexity"
    assert core["freshness"] == 0.0
    assert core["doc_value"] == 0.0  # missing -> 0
    assert [u["path"] for u in result["findings"]["unexplained_complexity"]] == [
        "pkg/engine/core.py"
    ]
    # The recommendation forbids auto-generation (slop-doc guard).
    assert "do not auto-generate" in core["recommendation"].lower()


def test_trivial_file_has_near_zero_doc_value_and_no_finding() -> None:
    """A doc over CCN-2 code scores ~0 and raises no finding, fresh or stale."""
    staleness = _staleness([_doc("pkg/util/README.md", ratio=0.5)])
    result = analyze_doc_complexity_join(COMPLEXITY_STATS, staleness, Path("/repo"))

    doc = _by_path(result)["pkg/util/README.md"]
    # complexity_summarised = 2 (helper.py) ; freshness 0.75 -> doc_value 1.5
    assert doc["complexity_summarised"] == 2.0
    assert doc["doc_value"] == 1.5
    assert abs(doc["doc_value"]) < MIN_HIGH_CCN  # negligible vs a real hotspot
    assert doc["finding"] is None
    # The trivial doc appears in no findings bucket. (core.py, which this doc
    # does not cover, is correctly surfaced as unexplained_complexity elsewhere.)
    flagged = {
        u["path"]
        for bucket in result["findings"].values()
        for u in bucket
    }
    assert "pkg/util/README.md" not in flagged
    assert result["findings"]["lying_maps"] == []
    assert result["findings"]["good_contracts"] == []


def test_slop_doc_guard_honest_gap_beats_lying_map() -> None:
    """An undocumented unit must score strictly safer than a hollow stale doc."""
    lying = analyze_doc_complexity_join(
        COMPLEXITY_STATS, _staleness([_doc("pkg/engine/README.md", ratio=6.0)]),
        Path("/repo"),
    )
    honest = analyze_doc_complexity_join(
        COMPLEXITY_STATS, _staleness([_doc("docs/unrelated/notes.md", ratio=0.5)]),
        Path("/repo"),
    )
    lying_value = lying["findings"]["lying_maps"][0]["doc_value"]
    honest_value = honest["findings"]["unexplained_complexity"][0]["doc_value"]
    assert honest_value > lying_value  # 0 > -30


def test_result_is_json_serialisable() -> None:
    staleness = _staleness([
        _doc("pkg/engine/README.md", ratio=6.0),
        _doc("pkg/util/README.md", ratio=0.5),
    ])
    result = analyze_doc_complexity_join(COMPLEXITY_STATS, staleness, Path("/repo"))
    # Round-trips without error and preserves the threshold contract.
    reloaded = json.loads(json.dumps(result))
    assert reloaded["available"] is True
    assert reloaded["high_ccn_threshold"] == max(8.0, MIN_HIGH_CCN)
    assert STALENESS_RATIO_THRESHOLD == 2.0
