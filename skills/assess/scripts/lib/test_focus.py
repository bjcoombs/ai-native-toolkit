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
It takes four inputs as parameters (the ranked hot files, the parsed coverage
report, the hollow-test heuristics, and an optional ``repo_root``) and returns a
plain dict. It imports no orchestrator and never raises. Its one file-system
probe is the sibling-test existence check in `lib/sibling_tests.py`, run only
when ``repo_root`` is passed and bounded to at most ten hot files and a fixed
ancestor depth; without ``repo_root`` it does no file I/O at all.

Signal per file (most to least actionable):
  - ``no_covering_test``      - a coverage report exists and it records this file
                                at a 0 line rate, or omits it with no test file
                                found: a risky file with no test.
  - ``covered_but_hollow``    - a test covers it, but it trips a hollow-test
                                heuristic (asserts internals, untested boundary,
                                duplicate truth).
  - ``unsupported``           - no coverage report and no test file found
                                (``repo_root`` given): the core cannot tell
                                whether a test exists, so it says so rather
                                than claim ``no_covering_test``.
  - ``sibling_test_only``     - a test file maps to it but no coverage record
                                does (no report, or a partial report that omits
                                the file): a test file is present, coverage is
                                unmeasured. Carries any hollow kinds it tripped.
  - ``unknown_no_coverage``   - no coverage report and no ``repo_root`` to look
                                for a test file: we *cannot* say it is covered,
                                so we do not pretend it is clean.
  - ``covered_clean``         - covered, no hollow hit. Not a focus target;
                                filtered out of the output.

Test-file evidence comes from `lib/sibling_tests.has_sibling_test`, the same
resolver behind the hotspot page's ``Has test file`` row, so the two never
disagree in one run. The evidence is a file's existence, so the core never
spells it as coverage.

Mutation scope: `mutation_scope` takes the paths of the entries that carry test
evidence (``covered_but_hollow``, ``sibling_test_only``). Mutating a file with no
test yields all survivors and measures the missing test, not an existing one's
strength, so ``unsupported`` / ``no_covering_test`` / ``unknown_no_coverage``
entries stay in the table but out of the mutation pass. A hot file that is
itself a test (`sibling_tests.is_test_path`) keeps its ``sibling_test_only`` row
but never enters the scope: nothing tests a test file, so mutating it measures
nothing and would come back as an ``untrusted_hotspot``.

Honest degradation is the hard contract: ``coverage_data is None`` never yields
``covered_clean`` for an untested file and records ``coverage_present: False``.
A risky file we know nothing about is surfaced, not silently blessed as clean.

Inward-only imports: stdlib and `lib.sibling_tests`; imported by the orchestrator
(`assess_core.py`), never importing one itself.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from lib.sibling_tests import has_sibling_test, is_test_path, shared_name_keys

# Risk bands by position in the ranked top_hotspots list. Index 0-2 are the
# sharpest hotspots, 3-6 the next tier, 7-9 the tail; anything past the top 10 is
# not a hotspot and is excluded entirely.
_HIGH_MAX = 2
_MEDIUM_MAX = 6
_LOW_MAX = 9

# Ranking weights. Risk band dominates; signal severity breaks ties within a band.
_BAND_RANK = {"high": 3, "medium": 2, "low": 1}
#
# The scale ranks "less tested" higher: the list answers which risky files most
# need test work. ``no_covering_test`` (no test) outranks ``covered_but_hollow``
# (a weak test), and by the same rule ``unsupported`` (no test file found in any
# conventional location) outranks ``sibling_test_only`` (a test file exists).
# ``sibling_test_only`` and ``unknown_no_coverage`` share the bottom rank; they
# never appear in the same block (one needs ``repo_root``, the other its absence).
# The mutation pass does not read this order raw: `mutation_scope` keeps only
# entries with test evidence, since mutating a file with no test measures the
# missing test rather than the strength of an existing one.
_SIGNAL_SEVERITY = {
    "no_covering_test": 4,
    "covered_but_hollow": 3,
    "unsupported": 2,
    "sibling_test_only": 1,
    "unknown_no_coverage": 1,
    "covered_clean": 0,
}

# Which suggested action each signal implies.
_ACTION_BY_SIGNAL = {
    "no_covering_test": "add_tests",
    "unknown_no_coverage": "add_tests",
    "unsupported": "measure_coverage",
    "sibling_test_only": "measure_coverage",
    "covered_but_hollow": "strengthen_assertions",
    "covered_clean": "none",
}

# Signals whose file has test evidence - the only entries a mutation pass can
# say anything about. Kept in ranked order by `mutation_scope`, which also drops
# any hot file that is itself a test: it counts as its own test (so it reads
# ``sibling_test_only``, never ``unsupported``), but no test exercises it, so
# mutating it measures nothing.
MUTATION_SCOPE_SIGNALS = frozenset({"covered_but_hollow", "sibling_test_only"})

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
    # 'no_covering_test'|'covered_but_hollow'|'covered_clean'|'unknown_no_coverage'
    # |'unsupported'|'sibling_test_only'
    test_signal: str
    hollow_heuristic_kinds: list[str] = field(default_factory=list)
    # 'add_tests' | 'strengthen_assertions' | 'measure_coverage' | 'none'
    suggested_action: str = "none"


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


def _has_record(path: str, coverage_data: dict[str, Any]) -> bool:
    """True when the parsed coverage report carries any entry for the file,
    even a zero rate: the report measured it."""
    per_file = coverage_data.get("per_file")
    return isinstance(per_file, dict) and path in per_file


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
    repo_root: Path | None = None,
    shared_names: frozenset[str] = frozenset(),
) -> tuple[str, list[str]]:
    """Resolve a file's test signal and the hollow kinds it tripped.

    No coverage report and a ``repo_root``: a test file credits the file as
    ``sibling_test_only`` (with any hollow kinds it tripped); none ->
    ``unsupported``. No report and no ``repo_root`` -> ``unknown_no_coverage``
    (we never claim clean). A report present: covered + a hollow hit ->
    ``covered_but_hollow``; covered + clean -> ``covered_clean``; a 0 rate ->
    ``no_covering_test``; absent from the report -> ``sibling_test_only`` when a
    test file exists (a partial report is not evidence of no test), otherwise
    ``no_covering_test``. A flat-tree-only test match does not credit a file
    whose bare name another hot file shares (``shared_names``).
    """
    def has_test() -> bool:
        return repo_root is not None and bool(
            has_sibling_test(repo_root, path, shared_names))

    if not coverage_present or coverage_data is None:
        if repo_root is None:
            return "unknown_no_coverage", []
        if not has_test():
            return "unsupported", []
        return "sibling_test_only", _hollow_kinds(path, cheap_heuristics)
    if not _is_covered(path, coverage_data):
        if not _has_record(path, coverage_data) and has_test():
            return "sibling_test_only", _hollow_kinds(path, cheap_heuristics)
        return "no_covering_test", []
    kinds = _hollow_kinds(path, cheap_heuristics)
    if kinds:
        return "covered_but_hollow", kinds
    return "covered_clean", []


def compute_test_focus(
    hot_files: Any,
    coverage_data: dict[str, Any] | None,
    cheap_heuristics: dict[str, Any] | None,
    *,
    repo_root: Path | None = None,
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
        repo_root: optional repository root. When given, a hot file with no
            coverage record is checked for a test file instead of degrading
            straight to ``unknown_no_coverage`` / ``no_covering_test``.

    Returns:
        ``{available, coverage_present, entries, total_focus_targets}`` where
        ``entries`` is the ranked list of focus targets (``covered_clean``
        filtered out), each a ``TestFocusEntry`` as a dict.
    """
    coverage_present = coverage_data is not None
    heuristics = cheap_heuristics if isinstance(cheap_heuristics, dict) else {}
    entries: list[TestFocusEntry] = []

    items = hot_files if isinstance(hot_files, list) else []
    # Bare names carried by more than one considered hot file: a flat tests/
    # match on such a name is ambiguous and credits none of them.
    shared_names = shared_name_keys(
        p for p in (_entry_path(i) for i in items[: _LOW_MAX + 1]) if p is not None)

    for index, item in enumerate(items):
        band = _risk_band(index)
        if band is None:
            break  # past the top 10 - no longer a hotspot
        path = _entry_path(item)
        if path is None:
            continue
        signal, kinds = _classify(
            path, coverage_present, coverage_data, heuristics,
            Path(repo_root) if repo_root is not None else None,
            shared_names,
        )
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


def mutation_scope(test_focus: Any) -> list[str]:
    """Paths the bounded mutation pass should mutate, in ranked order: the
    ``test_focus`` entries whose signal is in ``MUTATION_SCOPE_SIGNALS`` (the
    file has test evidence), minus any path that is itself a test file. Accepts
    the block dict or its ``entries`` list; anything malformed yields ``[]``."""
    entries = test_focus.get("entries") if isinstance(test_focus, dict) else test_focus
    if not isinstance(entries, list):
        return []
    return [
        e["path"] for e in entries
        if isinstance(e, dict) and isinstance(e.get("path"), str) and e["path"]
        and e.get("test_signal") in MUTATION_SCOPE_SIGNALS
        and not is_test_path(e["path"])
    ]
