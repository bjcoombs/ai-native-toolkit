"""Compose existing signals into a single ranked test-focus block.

`/assess` already surfaces three independent truths about a file: how risky it is
(complexity x churn -> the hotspot band), whether a test covers it (the parsed
coverage report), and whether the test that covers it looks hollow (the cheap
heuristics). On their own each is a separate list the reader has to cross-join in
their head. This module does that cross-join deterministically and emits one
ranked list answering the only question that matters for write-side safety:
*which risky files most need test work, and which kind?*

`compute_test_focus` is the SINGLE source the report table (the focus block) and
the mutation offer both read - the contract is here, not duplicated downstream.
It is a pure function: it takes its three inputs as parameters and returns a
plain dict. No file I/O, no orchestrator import, never raises.

Signal per file (most to least actionable):
  - ``no_covering_test``      - covered report exists but this file is absent or
                                its line rate is 0: a risky file with no test.
  - ``covered_but_hollow``    - a test covers it, but it trips a hollow-test
                                heuristic (asserts internals, untested boundary,
                                duplicate truth).
  - ``unknown_no_coverage``   - no coverage report at all: we *cannot* say it is
                                covered, so we do not pretend it is clean.
  - ``covered_clean``         - covered, no hollow hit. Not a focus target;
                                filtered out of the output.

Honest degradation is the hard contract: ``coverage_data is None`` makes every
file ``unknown_no_coverage`` (never ``covered_clean``) and records
``coverage_present: False``. A risky file we know nothing about is surfaced for
test work, not silently blessed as clean.

Inward-only imports: stdlib only; imported by the orchestrator (`assess_core.py`),
never importing one itself.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Risk bands by position in the ranked top_hotspots list. Index 0-2 are the
# sharpest hotspots, 3-6 the next tier, 7-9 the tail; anything past the top 10 is
# not a hotspot and is excluded entirely.
_HIGH_MAX = 2
_MEDIUM_MAX = 6
_LOW_MAX = 9

# Ranking weights. Risk band dominates; signal severity breaks ties within a band.
_BAND_RANK = {"high": 3, "medium": 2, "low": 1}
_SIGNAL_SEVERITY = {
    "no_covering_test": 3,
    "covered_but_hollow": 2,
    "unknown_no_coverage": 1,
    "covered_clean": 0,
}

# Which suggested action each signal implies.
_ACTION_BY_SIGNAL = {
    "no_covering_test": "add_tests",
    "unknown_no_coverage": "add_tests",
    "covered_but_hollow": "strengthen_assertions",
    "covered_clean": "none",
}

# The three hollow-test heuristic buckets, in report order. Each bucket entry
# names the file it flags under either ``file`` (boundary / duplicate-truth, a
# source file) or ``test_file`` (assertion-on-internal, a test file); we read
# whichever is present so a source hot file matches against any of them.
_HEURISTIC_BUCKETS = (
    "assertion_on_internal",
    "untested_boundaries",
    "duplicate_truth",
)


@dataclass
class TestFocusEntry:
    """One ranked focus target: a hot file, its risk, its test signal, the
    hollow-heuristic kinds it tripped, and the suggested remediation."""

    path: str
    risk_band: str  # 'high' | 'medium' | 'low'
    test_signal: str  # 'no_covering_test'|'covered_but_hollow'|'covered_clean'|'unknown_no_coverage'
    hollow_heuristic_kinds: list[str] = field(default_factory=list)
    suggested_action: str = "none"  # 'add_tests' | 'strengthen_assertions' | 'none'


def _entry_path(entry: Any) -> str | None:
    """Path of a top_hotspots entry, whether it is a dict (``{"path": ...}``) or a
    bare string. Anything else has no usable path."""
    if isinstance(entry, str):
        return entry or None
    if isinstance(entry, dict):
        path = entry.get("path")
        return path if isinstance(path, str) and path else None
    return None


def _risk_band(index: int) -> str | None:
    """Band for a file's position in the ranked hotspot list, or ``None`` if it
    falls outside the top 10 (not a hotspot)."""
    if index <= _HIGH_MAX:
        return "high"
    if index <= _MEDIUM_MAX:
        return "medium"
    if index <= _LOW_MAX:
        return "low"
    return None


def _is_covered(path: str, coverage_data: dict[str, Any]) -> bool:
    """True when the parsed coverage report carries a non-zero line rate for the
    file. Absent from the report, or a 0.0 rate, means no covering test."""
    per_file = coverage_data.get("per_file")
    if not isinstance(per_file, dict):
        return False
    rate = per_file.get(path)
    try:
        return rate is not None and float(rate) > 0.0
    except (TypeError, ValueError):
        return False


def _hollow_kinds(path: str, cheap_heuristics: dict[str, Any]) -> list[str]:
    """Heuristic buckets in which this file appears, in report order. Reads both
    the ``file`` and ``test_file`` keys so a source hot file matches whichever a
    bucket uses."""
    if not isinstance(cheap_heuristics, dict):
        return []
    kinds: list[str] = []
    for bucket in _HEURISTIC_BUCKETS:
        findings = cheap_heuristics.get(bucket)
        if not isinstance(findings, list):
            continue
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            if finding.get("file") == path or finding.get("test_file") == path:
                kinds.append(bucket)
                break
    return kinds


def _classify(
    path: str,
    coverage_present: bool,
    coverage_data: dict[str, Any] | None,
    cheap_heuristics: dict[str, Any],
) -> tuple[str, list[str]]:
    """Resolve a file's test signal and the hollow kinds it tripped.

    No coverage report at all -> ``unknown_no_coverage`` (we never claim clean).
    Covered + a hollow hit -> ``covered_but_hollow``. Covered + clean ->
    ``covered_clean``. Present report but file absent / zero rate ->
    ``no_covering_test``.
    """
    if not coverage_present or coverage_data is None:
        return "unknown_no_coverage", []
    if not _is_covered(path, coverage_data):
        return "no_covering_test", []
    kinds = _hollow_kinds(path, cheap_heuristics)
    if kinds:
        return "covered_but_hollow", kinds
    return "covered_clean", []


def compute_test_focus(
    hot_files: Any,
    coverage_data: dict[str, Any] | None,
    cheap_heuristics: dict[str, Any] | None,
) -> dict[str, Any]:
    """Cross-join the hotspot, coverage, and hollow-test signals into one ranked
    focus block.

    Args:
        hot_files: the ranked ``complexity_stats.top_hotspots`` list (entries are
            dicts with a ``path``, or bare path strings). Position sets the risk
            band; only the top 10 are considered, the rest are not hotspots.
        coverage_data: the parsed ``{_overall, per_file}`` report from
            ``load_coverage_data``, or ``None`` when no report was found.
        cheap_heuristics: the ``test_pressure`` block's ``cheap_heuristics`` dict
            (``assertion_on_internal`` / ``untested_boundaries`` /
            ``duplicate_truth`` buckets).

    Returns:
        ``{available, coverage_present, entries, total_focus_targets}`` where
        ``entries`` is the ranked list of focus targets (``covered_clean``
        filtered out), each a ``TestFocusEntry`` as a dict.
    """
    coverage_present = coverage_data is not None
    heuristics = cheap_heuristics if isinstance(cheap_heuristics, dict) else {}
    entries: list[TestFocusEntry] = []

    items = hot_files if isinstance(hot_files, list) else []
    for index, item in enumerate(items):
        band = _risk_band(index)
        if band is None:
            break  # past the top 10 - no longer a hotspot
        path = _entry_path(item)
        if path is None:
            continue
        signal, kinds = _classify(path, coverage_present, coverage_data, heuristics)
        if signal == "covered_clean":
            continue  # not a focus target
        entries.append(
            TestFocusEntry(
                path=path,
                risk_band=band,
                test_signal=signal,
                hollow_heuristic_kinds=kinds,
                suggested_action=_ACTION_BY_SIGNAL[signal],
            )
        )

    # Rank by risk band first, then signal severity within a band. Python's sort
    # is stable, so files tied on both keys keep their original hotspot order.
    entries.sort(
        key=lambda e: (_BAND_RANK[e.risk_band], _SIGNAL_SEVERITY[e.test_signal]),
        reverse=True,
    )

    return {
        "available": True,
        "coverage_present": coverage_present,
        "entries": [asdict(e) for e in entries],
        "total_focus_targets": len(entries),
    }
