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
  - ``unsupported``           - no coverage report and no sibling test file
                                maps to it (``repo_root`` given): the core cannot
                                tell whether a test exists, so it says so rather
                                than claim ``no_covering_test``.
  - ``sibling_test_only``     - no coverage report, but a conventionally named
                                test file maps to it: a test file is
                                present, coverage is unmeasured. Carries any
                                hollow-heuristic kinds it tripped.
  - ``unknown_no_coverage``   - no coverage report and no ``repo_root`` to look
                                for a sibling test: we *cannot* say it is
                                covered, so we do not pretend it is clean.
  - ``covered_clean``         - covered, no hollow hit. Not a focus target;
                                filtered out of the output.

Sibling-test fallback: with no coverage report and a ``repo_root``, a hot file
with a sibling test (``<stem>.test.<ext>``, ``<stem>.spec.<ext>``,
``<stem>_test.<ext>``, ``test_<stem>.<ext>`` beside it, in an adjacent
``__tests__/`` / ``tests/`` / ``test/`` directory, in a ``tests/`` / ``test/``
tree at any ancestor up to the root mirroring the source path, or in a flat
``tests/`` / ``test/`` tree beside the source's directory or its top-level package
directory when no other hot file shares its bare name; a
hyphenated stem also matches its underscore spelling) gets ``sibling_test_only``
rather than a covered bucket, and so does a hot file that is itself a test file:
the only evidence is a file's existence, so the core never spells it as coverage.

Honest degradation is the hard contract: ``coverage_data is None`` never yields
``covered_clean`` for an untested file and records ``coverage_present: False``.
A risky file we know nothing about is surfaced, not silently blessed as clean.

Inward-only imports: stdlib only; imported by the orchestrator (`assess_core.py`),
never importing one itself. The only file I/O is the sibling-test existence check,
and only when ``repo_root`` is passed.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

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
# The mutation offer reads the head of this same list, and mutation testing a
# file with no test yields all survivors, so an ``unsupported`` head entry there
# measures the missing test rather than the strength of an existing one.
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


def _sibling_test_names(name: str) -> list[str]:
    """Conventional test-file names for a source file name (``a.ts`` ->
    ``a.test.ts``, ``a.spec.ts``, ``a_test.ts``, ``test_a.ts``). A hyphenated
    stem also yields its underscore spelling, since a Python test for the script
    ``complexity-treemap.py`` has to be importable as
    ``test_complexity_treemap.py``. A name with no extension has no conventional
    sibling."""
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        return []
    stems = [stem] + ([stem.replace("-", "_")] if "-" in stem else [])
    return [n for s in stems for n in (f"{s}.test.{ext}", f"{s}.spec.{ext}",
                                        f"{s}_test.{ext}", f"test_{s}.{ext}")]


def _is_test_file(name: str) -> bool:
    """True when the file name itself follows a test naming convention: the
    file is test evidence, not a source awaiting a sibling test."""
    stem, dot, _ = name.rpartition(".")
    if not dot or not stem:
        return False
    return stem.startswith("test_") or stem.endswith(("_test", ".test", ".spec"))


# Directory names that hold tests beside a source file, and the ones that hold a
# parallel test tree at some ancestor (``tests/test_<stem>.py`` for
# ``scripts/lib/<stem>.py``). ``__tests__`` is a JS co-location idiom, never a
# repo-level tree, so it is only probed beside the source.
_ADJACENT_TEST_DIRS = ("__tests__", "tests", "test")
_TREE_TEST_DIRS = ("tests", "test")
# Bound on how many ancestor directories the walk visits. Each level costs a
# couple of directory stats plus a few file stats only where a test dir exists.
_MAX_ANCESTOR_LEVELS = 16
# A flat ``tests/`` / ``test/`` tree (test named by bare file name, no path
# mirrored) is only trusted where it sits beside the source's own directory
# (one component below the ancestor) or beside its top-level package directory
# (two components: ``skills/assess/tests`` for ``skills/assess/scripts/lib/``).
# Further up, a bare-name match carries no path relationship: a root
# ``tests/test_mod.py`` would credit every ``mod.py`` in the tree.
_MAX_FLAT_BELOW = 2

# Match strength returned by the sibling-test probe.
_MATCH_DIRECT = "direct"  # beside, adjacent, mirrored, or the file is a test
_MATCH_FLAT = "flat"  # only a bounded flat tests/ tree matched the bare name


def _test_dirs_for(rel_dir: Path) -> list[tuple[Path, bool]]:
    """Repo-relative directories that may hold a test for a source in
    ``rel_dir``, most local first, each paired with whether it is a flat probe:
    the source's own directory, its adjacent test directories, then at each
    ancestor up to the root a ``tests/`` / ``test/`` tree mirroring the source's
    path below that ancestor, and mirroring it with the first component (a
    ``src/``-style root) dropped. The flat tree (bare name, no mirror) is probed
    only within ``_MAX_FLAT_BELOW`` components of the source directory."""
    dirs: dict[Path, bool] = {rel_dir: False}
    for d in _ADJACENT_TEST_DIRS:
        dirs[rel_dir / d] = False
    parts = rel_dir.parts
    for depth in range(len(parts), -1, -1)[:_MAX_ANCESTOR_LEVELS]:
        ancestor = Path(*parts[:depth])
        below = parts[depth:]
        for tree in _TREE_TEST_DIRS:
            base = ancestor / tree
            if below:
                dirs.setdefault(base.joinpath(*below), False)
                if len(below) > 1:
                    dirs.setdefault(base.joinpath(*below[1:]), False)
            if len(below) <= _MAX_FLAT_BELOW:
                dirs.setdefault(base, True)
    return list(dirs.items())


def _sibling_test_match(path: str, repo_root: Path) -> str | None:
    """How a conventionally named test file for ``path`` was found, or ``None``.

    ``_MATCH_DIRECT``: the test sits beside the source, in an adjacent
    ``__tests__/`` / ``tests/`` / ``test/`` directory (where ``__tests__/`` also
    accepts the bare source name), or in a ``tests/`` / ``test/`` tree at any
    ancestor up to ``repo_root`` mirroring the source path; or the hot file is
    itself a test. ``_MATCH_FLAT``: only a flat ``tests/`` / ``test/`` tree
    beside the source directory or its top-level package directory holds the
    bare name - weaker evidence the caller disambiguates across hot files. A
    source that is not on disk (a stale stats entry for a deleted file) is never
    credited. Never raises."""
    try:
        source = repo_root / path
        if not source.is_file():
            return None
        if _is_test_file(source.name) or "__tests__" in Path(path).parts:
            return _MATCH_DIRECT
        names = _sibling_test_names(source.name)
        if not names:
            return None
        rel_dir = Path(path).parent
        if ".." in rel_dir.parts or rel_dir.is_absolute():
            return None
        flat_hit = False
        for rel, is_flat in _test_dirs_for(rel_dir):
            if is_flat and flat_hit:
                continue  # already have the weak match; only a direct one helps
            directory = repo_root / rel
            if not directory.is_dir():
                continue
            candidates = list(names)
            if rel.name == "__tests__":
                candidates.append(source.name)
            if any((directory / n).is_file() for n in candidates):
                if not is_flat:
                    return _MATCH_DIRECT
                flat_hit = True
        return _MATCH_FLAT if flat_hit else None
    except (OSError, ValueError):
        return None


def _name_key(path: str) -> str:
    """Bare file name with hyphens folded to underscores: two hot files with the
    same key resolve to the same conventional test names."""
    return Path(path).name.replace("-", "_")


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

    No coverage report and a ``repo_root``: a sibling test credits the file as
    ``sibling_test_only`` (with any hollow kinds it tripped); no sibling test ->
    ``unsupported``. A flat-only match does not credit a file whose bare name
    another hot file shares (``shared_names``): the bare name cannot say which
    of them the test belongs to. No report and no ``repo_root`` ->
    ``unknown_no_coverage`` (we never claim clean).
    Covered + a hollow hit -> ``covered_but_hollow``. Covered + clean ->
    ``covered_clean``. Present report but file absent / zero rate ->
    ``no_covering_test``.
    """
    if not coverage_present or coverage_data is None:
        if repo_root is None:
            return "unknown_no_coverage", []
        match = _sibling_test_match(path, repo_root)
        if match is None or (match == _MATCH_FLAT and _name_key(path) in shared_names):
            return "unsupported", []
        return "sibling_test_only", _hollow_kinds(path, cheap_heuristics)
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
        repo_root: optional repository root. When given and no coverage report
            exists, each hot file is checked for a sibling test file instead of
            degrading straight to ``unknown_no_coverage``.

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
    name_counts: dict[str, int] = {}
    for item in items[: _LOW_MAX + 1]:
        hot_path = _entry_path(item)
        if hot_path is not None:
            key = _name_key(hot_path)
            name_counts[key] = name_counts.get(key, 0) + 1
    shared_names = frozenset(k for k, n in name_counts.items() if n > 1)

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
