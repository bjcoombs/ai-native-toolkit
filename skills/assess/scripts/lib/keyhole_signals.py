"""Keyhole-signal integration: turn the five lib modules into run-context blocks.

Task #5's deterministic core. The individual signals (change-coupling B1/B2/B4,
the doc x complexity join C, understanding B4+D2, static-vs-historical B3, static
structure A1-A4) each live in their own module with their own contract tests.
This module is the *integration barrier*: it derives the per-directory
containment view, assembles the five new ``run-context.json`` blocks
(``behaviour`` / ``documentation`` / ``understanding`` / ``runtime`` /
``structure``), and crucially emits the **derived findings** - the deterministic
array of the six named findings that is the assessment's primary product.

Every function here is a pure transform of already-computed signal outputs (plus,
for containment, the git-log commit-file-sets the orchestrator parses once and
reuses). No function writes files; ``assess_core.build_run_context`` calls
:func:`integrate` and merges the result into the context dict.

**Defensive by construction.** :func:`integrate` wraps each block build in a
catch-all so one signal's failure or timeout degrades that block to
``available: False`` rather than crashing the whole ``/assess`` run. The
deterministic core stays ignorant of LLM-derived prose - this module emits only
structured data + the named findings; the LLM write-back fills judgement later.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

from lib.change_coupling import (
    authorship_analysis,
    change_coupling_pairs,
    containment_ratio,
    find_self_referential_tests,
    parse_commit_file_sets,
)
from lib.coupling_analysis import detect_hidden_coupling, find_refactor_boundaries
from lib.doc_complexity_join import (
    _extract_file_ccn,
    _high_ccn_threshold,
    analyze_doc_complexity_join,
)
from lib.liveness_scan import STATIC_REACHABILITY_CAVEAT
from lib.structure_drift import detect_grouping_disagreement
from lib.understanding_analysis import analyze_understanding

# Caps so a pathological repo can't bloat run-context.json. The treemap and
# liveness blocks already cap their own lists; these bound the new ones.
MAX_COUPLING_PAIRS = 100
MAX_CONTAINMENT_DIRS = 50
MAX_AUTHORSHIP_PATHS = 40
MAX_ATTENTION_UNITS = 10

# A directory must be touched by at least this many commits before its
# containment ratio is meaningful - below it the ratio is noise (one or two
# commits can't establish whether edits "stay contained").
MIN_DIR_COMMITS_FOR_CONTAINMENT = 5

# The named derived findings, in a fixed report order (worst-first, the one
# positive last). The action strings are the deterministic recommendation the
# report leads with; the LLM elaborates but never contradicts them.
FINDING_ORDER = [
    "hidden_coupling",
    "lying_map",
    "unexplained_complexity",
    "untrusted_hotspot",  # E1: complex churning code with hollow tests
    "self_referential_tests",  # E2: tests authored with the code they cover
    "unactioned_intent",  # stale promissory markers that survived many edits
    "accretion_ratchet",  # files that only ever grow - never meaningfully cut back
    "orphaned_understanding",
    "candidate_dead_weight",
    "refactor_boundary",
]
FINDING_ACTIONS = {
    "hidden_coupling": "investigate the seam",
    "lying_map": "fix or delete the doc",
    "unexplained_complexity": "write the missing contract (do NOT auto-generate)",
    "untrusted_hotspot": "strengthen tests to pin observable behaviour (not internal state)",
    "self_referential_tests": "request human review - tests verify internal consistency, not truth",
    "unactioned_intent": "action the promise: fix it, ticket it, or delete the marker/skip",
    "accretion_ratchet": "refactor down: extract, delete dead code, or split the file",
    "orphaned_understanding": "assign a human anchor before further change",
    "candidate_dead_weight": "verify liveness, then delete if dead",
    "refactor_boundary": "safe to hand an agent in isolation",
}

# Source-file suffix/stem markers that mean a file IS itself a test (so it never
# needs - and never maps to - a separate sibling test). Mirrors the same idiom
# list assess_core uses for its co-location check, kept self-contained here so
# the lib layer never imports back up into the orchestrator.
_TEST_SIBLING_BUILDERS = [
    lambda stem, ext: f"{stem}_test{ext}",    # Go, Python (pytest co-located)
    lambda stem, ext: f"{stem}.test{ext}",    # JS/TS (jest)
    lambda stem, ext: f"{stem}.spec{ext}",    # JS/TS/Angular (jasmine/jest)
    lambda stem, ext: f"{stem}_spec{ext}",    # Ruby (rspec), some JS
    lambda stem, ext: f"test_{stem}{ext}",    # Python (unittest)
    lambda stem, ext: f"{stem}Test{ext}",     # Java/Kotlin/C# (JUnit)
    lambda stem, ext: f"{stem}Tests{ext}",    # C#/Swift (XCTest)
]
_ADJACENT_TEST_DIRS = ["__tests__", "tests", "test", "spec"]
_IS_TEST_RE = re.compile(r"(^test_|_test$|\.test$|\.spec$|_spec$|Tests?$)")


# --------------------------------------------------------------------------
# B2 - per-directory containment
# --------------------------------------------------------------------------

def _candidate_dirs(
    commit_sets: list[set[Path]], min_commits: int, max_dirs: int,
) -> list[str]:
    """Directories worth computing a containment ratio for.

    Every ancestor directory of every touched file is a candidate (a "module"
    can live at any level), except the repo root ``.`` - its containment is
    vacuously high (everything is under it) and tells us nothing. A directory
    qualifies only when at least ``min_commits`` commits touch *something* under
    it; the busiest ``max_dirs`` win. Counting is per-commit (a commit touching
    three files in one dir counts once for that dir), so the threshold means
    "this many distinct changes," matching the containment denominator.
    """
    dir_commits: Counter[str] = Counter()
    for files in commit_sets:
        dirs: set[str] = set()
        for f in files:
            for parent in Path(f).parents:
                s = parent.as_posix()
                if s != ".":
                    dirs.add(s)
        for d in dirs:
            dir_commits[d] += 1
    eligible = [(d, n) for d, n in dir_commits.items() if n >= min_commits]
    eligible.sort(key=lambda t: (-t[1], t[0]))
    return [d for d, _ in eligible[:max_dirs]]


def containment_by_dir(
    repo_root: Path,
    commit_sets: list[set[Path]],
    min_commits: int = MIN_DIR_COMMITS_FOR_CONTAINMENT,
    max_dirs: int = MAX_CONTAINMENT_DIRS,
) -> dict[str, float]:
    """B2: ``{directory: containment_ratio}`` for the active directories.

    Reuses :func:`change_coupling.containment_ratio` (one pass over the commit
    file-sets per directory). Returns repo-relative posix directory keys mapped
    to a ratio in ``[0, 1]`` (rounded), highest = safest island.
    """
    dirs = _candidate_dirs(commit_sets, min_commits, max_dirs)
    return {
        d: round(containment_ratio(repo_root, d, commit_sets), 4) for d in dirs
    }


def project_static_modularity(
    structure: dict | None, dirs: list[str],
) -> dict | None:
    """Project the repo-level static-modularity view onto per-directory keys.

    ``structure_graph`` currently emits a single repo-level ``modularity_q`` /
    ``front_door_ratio`` (not per-directory). The B3 cross
    (``detect_hidden_coupling`` / ``find_refactor_boundaries``) consumes a
    per-directory view, so - per ``coupling_analysis``'s documented contract -
    the caller is responsible for the projection. This is the v1 **coarse**
    projection: every directory in ``dirs`` inherits the repo-level metrics. It
    means "the repo looks modular overall, yet this directory bleeds
    historically" -> a hidden-coupling *candidate* worth a human's eye, never a
    verdict.

    The caller must pass only directories the static graph has evidence about
    (Python-bearing ones - the import graph is silent on a ``docs/`` or
    ``.github/`` tree). A directory absent from the returned dict has no static
    evidence and correctly degrades to ``bleeding_module`` (historical-only)
    rather than a false ``hidden_coupling``. Returns ``None`` (the fully
    graceful historical-only path) when no static graph is available at all.
    """
    if not structure or not structure.get("available"):
        return None
    metrics = {
        "modularity_q": structure.get("modularity_q"),
        "front_door_ratio": structure.get("front_door_ratio"),
    }
    return {d: dict(metrics) for d in dirs}


def _python_bearing_dirs(commit_sets: list[set[Path]]) -> set[str]:
    """Repo-relative dirs (and ancestors) that contain at least one .py file.

    Derived from the commit file-sets (git-consistent, no extra filesystem
    walk) so it agrees with the containment view. These are the only
    directories the Python import graph could have evidence about; projecting
    the static-modularity metrics onto anything else manufactures false
    hidden-coupling findings on doc / config trees.
    """
    out: set[str] = set()
    for files in commit_sets:
        for f in files:
            if f.suffix != ".py":
                continue
            for parent in Path(f).parents:
                s = parent.as_posix()
                if s != ".":
                    out.add(s)
    return out


# --------------------------------------------------------------------------
# Block builders (pure transforms of upstream signal outputs)
# --------------------------------------------------------------------------

def build_behaviour_block(
    repo_root: Path, commit_sets: list[set[Path]], structure: dict | None,
) -> dict:
    """The ``behaviour`` block: B1 coupling, B2 containment, B3 disagreement."""
    if not commit_sets:
        return {
            "available": False,
            "reason": "no git history (commit file-sets empty)",
            "containment_by_dir": {},
            "change_coupling_pairs": [],
            "static_history_disagreement": [],
            "hidden_coupling_findings": [],
            "refactor_boundaries": [],
        }
    containment = containment_by_dir(repo_root, commit_sets)
    pairs = change_coupling_pairs(commit_sets)[:MAX_COUPLING_PAIRS]
    # Only project the (Python import-graph) static metrics onto Python-bearing
    # directories; a bleeding doc/config tree has no static evidence and
    # degrades to bleeding_module rather than a false hidden_coupling.
    python_dirs = _python_bearing_dirs(commit_sets)
    static_dirs = [d for d in containment if d in python_dirs]
    static_mod = project_static_modularity(structure, static_dirs)
    disagreement = detect_hidden_coupling(containment, static_modularity=static_mod)
    boundaries = find_refactor_boundaries(containment, static_modularity=static_mod)
    return {
        "available": True,
        "containment_by_dir": containment,
        "change_coupling_pairs": pairs,
        "static_history_disagreement": disagreement,
        "hidden_coupling_findings": [
            d for d in disagreement if d["finding"] == "hidden_coupling"
        ],
        "refactor_boundaries": boundaries,
        "static_modularity_projection": (
            "repo-level (coarse)" if static_mod is not None else "none"
        ),
    }


def build_documentation_block(doc_join: dict) -> dict:
    """The ``documentation`` block: freshness, complexity coverage, Signal C."""
    if not doc_join.get("available"):
        return {
            "available": False,
            "reason": doc_join.get("reason", "doc x complexity join unavailable"),
            "freshness_by_doc": {},
            "complexity_coverage": {},
            "stale_doc_on_complexity": [],
            "unexplained_complexity": [],
        }
    # The doc_join "docs" list mixes real docs with undocumented high-complexity
    # code surfaced as unexplained_complexity (subject_code_count 0, freshness 0).
    # freshness/coverage are meaningful only for real docs.
    real_docs = [d for d in doc_join["docs"] if d["finding"] != "unexplained_complexity"]
    findings = doc_join.get("findings", {})
    return {
        "available": True,
        "high_ccn_threshold": doc_join.get("high_ccn_threshold"),
        "freshness_by_doc": {d["path"]: d["freshness"] for d in real_docs},
        "complexity_coverage": {
            d["path"]: {
                "complexity_summarised": d["complexity_summarised"],
                "subject_code_count": d["subject_code_count"],
                "doc_value": d["doc_value"],
            }
            for d in real_docs
        },
        "stale_doc_on_complexity": findings.get("lying_maps", []),
        "unexplained_complexity": findings.get("unexplained_complexity", []),
        "good_contracts": findings.get("good_contracts", []),
    }


def build_understanding_block(understanding: dict) -> dict:
    """The ``understanding`` block: human anchor, intent source, authorship class."""
    if not understanding.get("available"):
        return {
            "available": False,
            "reason": understanding.get("reason", "no authorship data to analyse"),
            "human_anchor_by_path": {},
            "intent_source_by_path": {},
            "authorship_class_by_path": {},
            "orphaned_understanding": [],
        }
    modules = understanding["modules"]
    return {
        "available": True,
        "high_ccn_threshold": understanding.get("high_ccn_threshold"),
        "human_anchor_by_path": {m["path"]: m["human_anchor"] for m in modules},
        "intent_source_by_path": {m["path"]: m["intent_source"] for m in modules},
        "authorship_class_by_path": {m["path"]: m["authorship_class"] for m in modules},
        "orphaned_understanding": understanding.get("orphaned_understanding", []),
        "modules": modules,
    }


def build_runtime_block(dead_code: dict, observability: dict) -> dict:
    """The ``runtime`` block: D1 static reachability + the observability rung.

    Reuses the existing ``liveness_scan`` outputs rather than re-deriving:
    ``static_reachability`` is the dead-code candidate set (what nothing in this
    repo references), carrying its own cross-boundary caveat. The observability
    rung is the runtime-evidence axis - rung 3 (reachable) is the only one that
    lets an agent actually verify liveness.
    """
    return {
        "available": True,
        "static_reachability": {
            "available": dead_code.get("available", False),
            "candidate_count": dead_code.get("candidate_count", 0),
            "candidates": dead_code.get("candidates", []),
            "tools": dead_code.get("tools", []),
            "caveat": dead_code.get("caveat", STATIC_REACHABILITY_CAVEAT),
        },
        "observability_rung": observability.get("rung"),
        "runtime_evidence_available": bool(
            observability.get("reachable", {}).get("present")
        ),
    }


# --------------------------------------------------------------------------
# Derived findings (the primary output)
# --------------------------------------------------------------------------

def _high_complexity_paths(complexity_stats: dict) -> list[str]:
    """Paths at or above the high-CCN threshold (same gate the joins use)."""
    ccn = _extract_file_ccn(complexity_stats)
    threshold = _high_ccn_threshold(complexity_stats)
    return sorted(p for p, c in ccn.items() if c >= threshold)


def candidate_dead_weight_paths(
    complexity_stats: dict,
    dead_code: dict,
    intent_source_by_path: dict[str, bool],
) -> list[str]:
    """Derive *candidate dead weight* with asymmetric delete caution (PRD 5).

    A false "dead" is far worse than a false "alive", so this fires only on
    **positive** static-reachability evidence: a high-complexity path that the
    dead-code scan flagged (nothing in the repo references it) *and* that has no
    intent source explaining why it should exist. Mere absence of runtime
    evidence is never enough - that would flag every undocumented complex file
    in a repo without observability. The action stays "verify liveness, then
    delete if dead", never "delete".
    """
    high = set(_high_complexity_paths(complexity_stats))
    flagged = {c.get("path") for c in dead_code.get("candidates", [])}
    return sorted(
        p for p in high
        if p in flagged and not intent_source_by_path.get(p, False)
    )


def assemble_findings(paths_by_name: dict[str, list[str]]) -> list[dict]:
    """Assemble the six named findings in fixed order.

    Each finding is ``{name, paths, action}``; ``paths`` is deduped and sorted
    for determinism. All six are always present (paths may be empty) so the
    run-context shape is stable and the report can rely on it.
    """
    return [
        {
            "name": name,
            "paths": sorted(set(paths_by_name.get(name, []))),
            "action": FINDING_ACTIONS[name],
        }
        for name in FINDING_ORDER
    ]


def build_attention_list(
    findings: list[dict], max_units: int = MAX_ATTENTION_UNITS,
) -> list[dict]:
    """Rank the few units worst across axes - the "where to look" list.

    A unit's score is how many *negative* findings name it (the one positive
    finding, ``refactor_boundary``, is a safe zone, never an attention row).
    Higher score = worse across more axes = look here first.
    """
    reasons: dict[str, list[str]] = defaultdict(list)
    for f in findings:
        if f["name"] == "refactor_boundary":
            continue
        for path in f["paths"]:
            reasons[path].append(f["name"])
    units = [
        {"path": path, "findings": sorted(set(names)), "score": len(set(names))}
        for path, names in reasons.items()
    ]
    # dict values are heterogeneous (str | list | int), so mypy types the
    # lookup as ``object``; the negation is valid at runtime (score is int).
    units.sort(key=lambda u: (-u["score"], u["path"]))  # type: ignore[operator]
    return units[:max_units]


# --------------------------------------------------------------------------
# E1 - untrusted hotspots (complexity x hollow tests)
# --------------------------------------------------------------------------

# A hotspot is "untrusted" when at least this fraction of its mutants survive -
# the suite runs the code but doesn't pin it. Asymmetric like dead-weight: this
# fires only on positive mutation evidence, so a read-only /assess (no opt-in
# mutation pass) reports no untrusted hotspots rather than guessing.
DEFAULT_SURVIVOR_DENSITY_THRESHOLD = 0.3


def find_untrusted_hotspots(
    complexity_stats: dict,
    test_pressure: dict,
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


# --------------------------------------------------------------------------
# E2 - self-referential test authorship (test+code co-located AND co-committed)
# --------------------------------------------------------------------------

def _find_sibling_test(repo_root: Path, rel_path: str) -> Path | None:
    """Return the co-located test file for a source path, or ``None``.

    Mirrors assess_core's ``_has_sibling_test`` co-location idioms but returns
    the test *path* (not a bool) so the E2 map can pair a test with its source.
    A file that is itself a test maps to ``None`` - it is not a source needing a
    sibling. Returns ``None`` when the source isn't on disk or no sibling is
    found.
    """
    src = repo_root / rel_path
    if not src.is_file():
        return None
    stem, ext = src.stem, src.suffix
    if _IS_TEST_RE.search(stem):
        return None  # the file IS a test, not a source needing a sibling
    directory = src.parent
    candidate_names = [build(stem, ext) for build in _TEST_SIBLING_BUILDERS]
    for name in candidate_names:
        cand = directory / name
        if cand.is_file():
            return cand
    for sub in _ADJACENT_TEST_DIRS:
        test_dir = directory / sub
        if not test_dir.is_dir():
            continue
        for name in candidate_names + [f"{stem}{ext}"]:
            cand = test_dir / name
            if cand.is_file():
                return cand
    return None


def build_test_to_code_map(
    repo_root: Path, source_paths: list[str],
) -> dict[str, str]:
    """Map ``test_file -> source_file`` via co-location conventions.

    Best-effort and filesystem-only: each source path that has a co-located test
    contributes one ``{repo-relative test path: source path}`` entry. Paths the
    co-location idioms miss (far-away mirror test trees) simply don't appear,
    which correctly degrades E2 to "no evidence" rather than a false negative.
    """
    repo_root = Path(repo_root)
    mapping: dict[str, str] = {}
    for src in source_paths:
        test = _find_sibling_test(repo_root, src)
        if test is None:
            continue
        try:
            rel = test.relative_to(repo_root).as_posix()
        except ValueError:
            rel = test.as_posix()
        mapping[rel] = src
    return mapping


# --------------------------------------------------------------------------
# Accretion ratchet finding: files that only ever grow
# --------------------------------------------------------------------------

# Maximum number of per-file detail lines emitted in the finding items list.
# The full list is available in the run-context accretion_ratchet block; this
# cap keeps the finding items readable and bounded.
MAX_ACCRETION_ITEMS = 10

# Unreliable-history disclaimer, mirroring the pattern used by other
# reliability-flagged signals (e.g. unactioned_intent aging_reliable).
_ACCRETION_UNRELIABLE_DISCLAIMER = (
    "History reliability: UNRELIABLE (degenerate history - shallow clone or "
    "squashed import). Results shown but should not be acted on without "
    "verifying against a full clone."
)


def _accretion_ratchet_finding(run_context: dict) -> list[str]:
    """Derive the accretion_ratchet finding paths from the run-context block.

    Reads ``run_context["accretion_ratchet"]``; returns an empty list when the
    block is absent, unavailable, or carries no flagged files - so the finding
    degrades to silent rather than manufacturing paths. The returned list is
    sorted by net additions descending (worst first), then by path for a stable
    tie-break, matching the serialization order in the block. Determinism is
    non-negotiable: no set iteration, no dict-order dependency.
    """
    block = run_context.get("accretion_ratchet") or {}
    if not block.get("available"):
        return []
    files = block.get("files") or []
    if not files:
        return []
    # The block already sorts by (-net_additions, path); re-sort defensively so
    # the finding list is a total, deterministic order regardless of upstream.
    ordered = sorted(files, key=lambda f: (-f["net_additions"], f["path"]))
    return [f["path"] for f in ordered]


def _format_accretion_items(run_context: dict) -> list[str]:
    """Build the human-readable detail items for the accretion_ratchet finding.

    Returns a roll-up sentence followed by one line per file (capped at
    MAX_ACCRETION_ITEMS), then a reliability disclaimer when the history is
    degenerate. The format mirrors the sibling per-file lines used in the
    report: path, net LOC added, time span, commit count, and deletion
    fraction, with plain-English time span phrasing.
    """
    block = run_context.get("accretion_ratchet") or {}
    files = block.get("files") or []
    if not files:
        return []

    total = block.get("total_in_band", len(files))
    top = sorted(files, key=lambda f: (-f["net_additions"], f["path"]))
    hottest = top[:MAX_ACCRETION_ITEMS]

    def _months_str(months: float) -> str:
        """Human-readable time span: whole months, or '<1mo' for short spans."""
        if months < 1.0:
            return "<1mo"
        return f"{round(months)}mo"

    items: list[str] = [
        f"{total} file{'s' if total != 1 else ''} show monotonic growth; "
        f"the {len(hottest)} hottest {'are' if len(hottest) != 1 else 'is'} below"
    ]
    for f in hottest:
        net = f["net_additions"]
        months = _months_str(f["time_span_months"])
        commits = f["commit_count"]
        del_frac = f["deletion_fraction"]
        items.append(
            f"{f['path']} — +{net:,} LOC over {months} across {commits} commit"
            f"{'s' if commits != 1 else ''}, {del_frac:.0%} net reductions"
            " — only ever grows"
        )

    if not block.get("reliable", True):
        items.append(_ACCRETION_UNRELIABLE_DISCLAIMER)

    return items


# --------------------------------------------------------------------------
# Structure drift (Tier 1) as a B3 hidden-coupling signal
# --------------------------------------------------------------------------
#
# Tier 1's ``human_split_but_cochange`` is, by construction, a B3 signal: file
# pairs the commit log keeps coupling that *no* declared ownership boundary
# groups - a hidden seam the map omits, exactly the question B3
# (static-vs-historical disagreement) already asks. So it folds into the
# *existing* ``hidden_coupling`` finding rather than a new finding type, after
# the seam allowlist (applied inside ``detect_grouping_disagreement``) has
# absorbed the known-good seams.
#
# The aggregation tests directory *pairs*, not single directories: a genuine
# hidden seam is two trees that keep co-changing across several distinct file
# pairs, not one hub file that touches everything. The version hot-file
# ``plugin.json`` co-changes with a file in every tree on each PR - inflating its
# own directory's single-dir count - but each of those couplings is a *different*
# counterpart directory through the *same* hub file, so no directory *pair*
# recurs. Requiring a directory pair to recur (``min_pairs`` distinct file pairs
# straddling the same two trees) is the recurrence test that distinguishes a
# mutual entanglement from a repo-wide hub, and yields no false positive from the
# version-bump ritual.

MIN_DRIFT_PAIRS_FOR_HIDDEN_COUPLING = 2


def _parent_dir(path_str: str) -> str:
    """The repo-relative parent directory of a file path (``.`` for repo root)."""
    return Path(path_str).parent.as_posix()


def structure_drift_hidden_coupling_dirs(
    tier1: dict, min_pairs: int = MIN_DRIFT_PAIRS_FOR_HIDDEN_COUPLING,
) -> list[str]:
    """Directories entangled by a recurring Tier 1 hidden seam.

    Aggregates the post-allowlist ``human_split_but_cochange`` pairs (co-change
    with no declared boundary) to *directory pairs* and keeps the directories of
    any directory pair straddled by at least ``min_pairs`` distinct file pairs.
    Testing directory pairs (not single directories) is what filters a repo-wide
    hub - the version hot-file couples with every tree, inflating its own
    directory's appearances, but always through a *different* counterpart
    directory, so no directory pair recurs and it never reads as a seam. A pair
    where both files share a directory is ignored (intra-directory cohesion is
    not a cross-tree seam), as is any pair touching the repo root ``.`` (vacuous
    containment, never an attention unit). Returns a sorted, deduped path list
    matching the dir granularity of the existing ``hidden_coupling`` finding so
    the attention list stays deterministic.
    """
    if not tier1.get("available"):
        return []
    dir_pair_counts: Counter[tuple[str, str]] = Counter()
    for row in tier1.get("human_split_but_cochange", []):
        da, db = _parent_dir(row["file_a"]), _parent_dir(row["file_b"])
        if da == db or "." in (da, db):
            continue
        key: tuple[str, str] = (da, db) if da <= db else (db, da)
        dir_pair_counts[key] += 1
    out: set[str] = set()
    for (da, db), n in dir_pair_counts.items():
        if n >= min_pairs:
            out.add(da)
            out.add(db)
    return sorted(out)


# --------------------------------------------------------------------------
# Deterministic markdown / summary renderers (the report-skeleton products)
# --------------------------------------------------------------------------

# Cap on paths-per-finding and attention rows in the rendered markdown so a
# pathological repo can't bloat the report skeleton. The structured arrays in
# run-context.json keep the full (already-capped) lists.
MAX_FINDING_PATHS_RENDERED = 10
MAX_ATTENTION_ROWS_RENDERED = 5


def render_findings_markdown(
    findings: list[dict], attention: list[dict],
) -> str:
    """Render the derived findings + attention list as a markdown section.

    This is the deterministic report skeleton: the LLM writes prose *around* it
    but cannot omit, rename, or reorder the findings. Findings with no paths are
    skipped (nothing to point at); when none have paths the section says so
    explicitly rather than rendering an empty heading. Always ends with a single
    trailing newline so it concatenates cleanly into the report.
    """
    lines = ["## Cross-Layer Findings (Keyhole Readiness)", ""]
    rendered_any = False
    for f in findings:
        paths = f.get("paths") or []
        if not paths:
            continue
        rendered_any = True
        lines.append(f"### {f['name']}")
        lines.append("")
        lines.append(f"Action: {f['action']}")
        lines.append("")
        lines.append("Paths:")
        for p in paths[:MAX_FINDING_PATHS_RENDERED]:
            lines.append(f"- {p}")
        lines.append("")
    if not rendered_any:
        lines.append(
            "_No cross-layer findings surfaced - no path crossed an axis boundary._"
        )
        lines.append("")
    if attention:
        lines.append("### Attention List (Priority Order)")
        lines.append("")
        for a in attention[:MAX_ATTENTION_ROWS_RENDERED]:
            names = ", ".join(a.get("findings", []))
            lines.append(f"- {a['path']} (score {a['score']}): {names}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# Display-name overrides for finding identifiers whose naive underscore->space
# form reads wrong (compound adjectives need a hyphen). Names not listed here
# fall back to a plain underscore->space replace in ``finding_display_name``.
FINDING_DISPLAY_NAMES = {
    "self_referential_tests": "self-referential tests",
}


def finding_display_name(name: str) -> str:
    """Human-readable form of a finding identifier for summary text."""
    return FINDING_DISPLAY_NAMES.get(name, name.replace("_", " "))


def _format_summary(concerns: list[dict], safe_zones: int) -> str:
    """One-line human-readable keyhole-readiness summary.

    Pure count with a positive/negative split (PRD: never imply commensurability
    with the 0-8 score). Singular/plural handled for the headline count and the
    safe-zone count; the per-finding labels use ``finding_display_name`` (plain
    underscore->space, e.g. ``2 hidden coupling``, with explicit overrides for
    compound adjectives, e.g. ``self-referential tests``).
    """
    def plural(n: int, word: str) -> str:
        return f"{n} {word}" if n == 1 else f"{n} {word}s"

    zones = plural(safe_zones, "safe zone")
    if not concerns:
        return f"No structural concerns, {zones}."
    total = sum(c["count"] for c in concerns)
    detail = ", ".join(
        f"{c['count']} {finding_display_name(c['name'])}" for c in concerns
    )
    headline = plural(total, "structural concern")
    return f"{headline} ({detail}), {zones}."


def build_keyhole_summary(findings: list[dict]) -> dict:
    """Roll the derived findings into a count/severity readiness summary.

    Reported *alongside* the 0-8 layered score, never merged into it: the score
    asks "is the scaffolding in place to catch problems?", this asks "where is
    today's structural pain?". Returns ``{concerns, safe_zones, total_concerns,
    summary_text}`` where ``concerns`` is the per-finding ``{name, count}`` for
    every negative finding with paths and ``safe_zones`` is the
    ``refactor_boundary`` path count (the one positive finding).
    """
    concerns: list[dict] = []
    safe_zones = 0
    for f in findings:
        if f["name"] == "refactor_boundary":
            safe_zones = len(f["paths"])
        elif f["paths"]:
            concerns.append({"name": f["name"], "count": len(f["paths"])})
    return {
        "concerns": concerns,
        "safe_zones": safe_zones,
        "total_concerns": sum(c["count"] for c in concerns),
        "summary_text": _format_summary(concerns, safe_zones),
    }


# How many attention units are promoted into the mandatory prescribed-actions
# set. The report's Top 3 Actions must include these.
MAX_PRESCRIBED_ACTIONS = 3


def build_prescribed_actions(
    attention: list[dict],
    findings: list[dict],
    max_actions: int = MAX_PRESCRIBED_ACTIONS,
) -> list[dict]:
    """Map the top attention units to their finding-derived prescribed actions.

    The attention list already ranks units by negative-finding count; this picks
    the action for each unit's *worst* finding (severity = ``FINDING_ORDER``
    minus the positive ``refactor_boundary``, so the deterministic worst-first
    order is the single source of truth). Returns ``{path, action, findings,
    rank}`` for up to ``max_actions`` units - the Top-3 the report MUST include.
    """
    finding_actions = {f["name"]: f["action"] for f in findings}
    severity = [n for n in FINDING_ORDER if n != "refactor_boundary"]
    prescribed: list[dict] = []
    for i, unit in enumerate(attention[:max_actions]):
        for name in severity:
            if name in unit["findings"]:
                prescribed.append({
                    "path": unit["path"],
                    "action": finding_actions[name],
                    "findings": unit["findings"],
                    "rank": i + 1,
                })
                break
    return prescribed


def render_prescribed_actions(prescribed: list[dict]) -> str:
    """Render the mandatory attention-derived actions as Top-3 table rows.

    Pre-fills the rank, action, hotspot path, and issue columns of the report's
    Top 3 Actions table; the LLM fills the ``?`` layer/effort/command cells with
    judgement. Returns ``""`` when there is nothing to prescribe (empty
    attention), so the LLM falls back to its own prioritisation.
    """
    if not prescribed:
        return ""
    lines = []
    for p in prescribed:
        # Columns: # | Action | Layer | Effort | Command / First Step | Hotspot
        # files this addresses | Issue
        lines.append(
            f"| {p['rank']} | {p['action']} | ? | ? | ? | `{p['path']}` | — |"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Orchestration entry point
# --------------------------------------------------------------------------

def _safe_block(label: str, fn, fallback: dict) -> dict:
    """Run a block builder, degrading to ``fallback`` on any failure.

    Each new signal does git-log / static-graph work; a hang or parse failure in
    one must not crash the whole assessment. The fallback always carries
    ``available: False`` + a reason so the report can say "this signal was
    skipped" rather than silently dropping it.
    """
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - intentional: degrade, never crash
        return {**fallback, "available": False, "reason": f"{label} failed: {e}"}


def _structure_drift_tier1(
    repo_root: Path, structure: dict | None, behaviour: dict,
) -> dict:
    """Tier 1 grouping disagreement, fed the behaviour block's co-change pairs.

    Returns ``{"available": False}`` (no disagreement to surface) whenever the
    static import graph is unavailable - with no static lens there is nothing to
    disagree with. Otherwise calls ``detect_grouping_disagreement`` with the
    co-change pairs the behaviour block already computed (no second git-log
    parse); the static communities are recomputed inside the detector from the
    same grimp packages ``structure`` was built from, since the structure block
    keeps only modularity_q, not the partition. Any failure degrades to an empty
    available:False result - this is additive B3 context, never a gate.
    """
    if not structure or not structure.get("available"):
        return {"available": False}
    coupling_pairs = (
        behaviour.get("change_coupling_pairs", [])
        if behaviour.get("available") else []
    )
    try:
        return detect_grouping_disagreement(repo_root, coupling_pairs=coupling_pairs)
    except Exception:  # noqa: BLE001 - degrade, never crash
        return {"available": False}


def _paths_from_stats(complexity_stats: dict, cap: int = MAX_AUTHORSHIP_PATHS) -> list[str]:
    """The ranked-list paths to run authorship analysis over (capped).

    Union of the three top-N lists; these are the high-complexity / high-churn
    units the understanding + dead-weight findings care about. Capped so a huge
    repo doesn't trigger dozens of per-path git calls.
    """
    seen: list[str] = []
    s: set[str] = set()
    for key in ("top_hotspots", "top_complex", "top_large"):
        for entry in complexity_stats.get(key) or []:
            p = entry.get("path")
            if p and p not in s:
                s.add(p)
                seen.append(p)
    return sorted(seen)[:cap]


def integrate(
    *,
    repo_root: Path,
    complexity_stats: dict,
    doc_staleness: dict,
    dead_code: dict,
    observability: dict,
    structure: dict,
    commit_sets: list[set[Path]] | None = None,
    test_pressure: dict | None = None,
    promissory_markers: dict | None = None,
    accretion_ratchet: dict | None = None,
) -> dict:
    """Build the five run-context blocks + derived findings + attention list.

    Pure orchestration over the lib signals. ``commit_sets`` may be passed in
    (the orchestrator parses git log once and reuses it for churn etc.);
    otherwise it is parsed here. ``test_pressure`` is the Layer-1 write-side scan
    (the E1 trust axis crosses it with the complexity hotspots); when absent E1
    degrades to silent. ``promissory_markers`` is the marker-scan summary
    (``promissory_markers.MarkerScan.summary()``); when absent or unreliable the
    ``unactioned_intent`` finding degrades to silent. ``accretion_ratchet`` is the
    serialized ``AccretionScan`` block from ``assess_core._accretion_block``; when
    absent or unavailable the ``accretion_ratchet`` finding degrades to silent.
    Every block is built defensively - a failure in one degrades that block to
    ``available: False`` and leaves the rest intact.
    """
    repo_root = Path(repo_root)
    if commit_sets is None:
        try:
            commit_sets = parse_commit_file_sets(repo_root)
        except Exception:  # noqa: BLE001 - degrade to no-history
            commit_sets = []

    behaviour = _safe_block(
        "behaviour",
        lambda: build_behaviour_block(repo_root, commit_sets, structure),
        {"containment_by_dir": {}, "change_coupling_pairs": [],
         "static_history_disagreement": [], "hidden_coupling_findings": [],
         "refactor_boundaries": []},
    )

    documentation = _safe_block(
        "documentation",
        lambda: build_documentation_block(
            analyze_doc_complexity_join(complexity_stats, doc_staleness, repo_root)
        ),
        {"freshness_by_doc": {}, "complexity_coverage": {},
         "stale_doc_on_complexity": [], "unexplained_complexity": []},
    )

    def _understanding() -> dict:
        paths = _paths_from_stats(complexity_stats)
        authorship_by_path = {p: authorship_analysis(repo_root, p) for p in paths}
        return build_understanding_block(
            analyze_understanding(
                repo_root, authorship_by_path, doc_staleness, complexity_stats
            )
        )

    understanding = _safe_block(
        "understanding",
        _understanding,
        {"human_anchor_by_path": {}, "intent_source_by_path": {},
         "authorship_class_by_path": {}, "orphaned_understanding": []},
    )

    runtime = _safe_block(
        "runtime",
        lambda: build_runtime_block(dead_code, observability),
        {"static_reachability": {"available": False, "candidate_count": 0,
                                 "candidates": []},
         "observability_rung": None, "runtime_evidence_available": False},
    )

    dead_weight = candidate_dead_weight_paths(
        complexity_stats, dead_code, understanding.get("intent_source_by_path", {})
    )

    # E1 trust axis: complexity hotspots whose tests are hollow. Silent without
    # opt-in mutation data, so it degrades cleanly on the default read-only run.
    try:
        untrusted = find_untrusted_hotspots(complexity_stats, test_pressure or {})
    except Exception:  # noqa: BLE001 - degrade, never crash
        untrusted = []

    # E2 trust axis: tests co-located AND co-committed with the code they cover -
    # the suite may verify internal consistency, not truth. Filesystem + git
    # work, wrapped so a parse failure degrades to no finding.
    try:
        source_paths = _paths_from_stats(complexity_stats)
        test_to_code = build_test_to_code_map(repo_root, source_paths)
        self_ref = find_self_referential_tests(repo_root, test_to_code, commit_sets)
        self_ref_paths = sorted({sr["source_file"] for sr in self_ref})
    except Exception:  # noqa: BLE001 - degrade, never crash
        self_ref_paths = []

    # Churn-measurement reliability (single source of truth: lib.git_churn, set
    # on the doc-staleness block). When the history is degenerate - every file ~1
    # commit (shallow clone, fresh import, squashed/extracted tree) - the churn
    # signal carries no information, so the two findings derived from it must not
    # be counted: `lying_map` (built on the doc-staleness ratio) and
    # `hidden_coupling` (built on co-commit change coupling, which a single bulk
    # import maximally inflates). lying_map is already suppressed upstream by the
    # confidence cap in the join; hidden_coupling is dropped here. Both blocks
    # keep their raw data (honest); only the *counted findings* degrade.
    churn_degenerate = bool(doc_staleness.get("churn_degenerate", False))
    churn_derived_findings = {"lying_map", "hidden_coupling"}

    def _churn_paths(name: str, paths: list[str]) -> list[str]:
        return [] if (churn_degenerate and name in churn_derived_findings) else paths

    # Unactioned intent: files carrying stale promissory markers (markers that
    # survived >= threshold edits to their own file). Silent when the scan was
    # unavailable or the history is too thin to age markers (aging_reliable
    # False) - thin history must read "not assessed", never "clean".
    pm = promissory_markers or {}
    unactioned = (
        sorted(pm.get("stale_by_file", {}))
        if pm.get("available") and pm.get("aging_reliable", True)
        else []
    )

    # Accretion ratchet: files in the top complexity/size band that only ever
    # grew - monotonic net additions, almost no deletion pressure. Silent when
    # the block is absent or unavailable (the caller hasn't passed it yet, or
    # the scan failed). Paths are extracted in worst-first order (descending
    # net additions) by the helper so the finding list is deterministic.
    ratchet_run_ctx: dict = {"accretion_ratchet": accretion_ratchet or {}}
    accreting_paths = _accretion_ratchet_finding(ratchet_run_ctx)

    # Structure drift (Tier 1): grouping disagreement between the declared
    # ownership map, the static import graph, and the commit-log co-change. Run
    # only when the static lens exists (no graph -> nothing to disagree with);
    # fed the behaviour block's already-computed co-change pairs so no second
    # git-log parse happens. Its hidden-seam direction folds into the existing
    # hidden_coupling finding below. Degrades to an empty (available:False) result
    # when no ownership map exists or the detector fails - never crashes the run.
    structure_drift_tier1 = _structure_drift_tier1(repo_root, structure, behaviour)
    # The hidden-seam dirs are co-change-derived, so a degenerate history (which
    # maximally inflates co-change) must suppress them exactly as it suppresses
    # the containment-derived hidden_coupling - via the same _churn_paths gate.
    drift_hidden_dirs = structure_drift_hidden_coupling_dirs(structure_drift_tier1)

    findings = assemble_findings({
        "hidden_coupling": _churn_paths(
            "hidden_coupling",
            [h["path"] for h in behaviour.get("hidden_coupling_findings", [])]
            + drift_hidden_dirs,
        ),
        "lying_map": _churn_paths(
            "lying_map",
            [d["path"] for d in documentation.get("stale_doc_on_complexity", [])],
        ),
        "unexplained_complexity": [
            d["path"] for d in documentation.get("unexplained_complexity", [])
        ],
        "untrusted_hotspot": untrusted,
        "self_referential_tests": self_ref_paths,
        "unactioned_intent": unactioned,
        "accretion_ratchet": accreting_paths,
        "orphaned_understanding": understanding.get("orphaned_understanding", []),
        "candidate_dead_weight": dead_weight,
        "refactor_boundary": [b["path"] for b in behaviour.get("refactor_boundaries", [])],
    })
    attention = build_attention_list(findings)

    return {
        "structure": structure,
        "behaviour": behaviour,
        "documentation": documentation,
        "understanding": understanding,
        "runtime": runtime,
        "derived_findings": findings,
        "attention": attention,
        "findings_markdown": render_findings_markdown(findings, attention),
        "keyhole_summary": build_keyhole_summary(findings),
        "prescribed_actions": build_prescribed_actions(attention, findings),
        # The Tier 1 grouping disagreement, computed once here from the behaviour
        # block's co-change pairs, so the orchestrator can build the run-context
        # structure_drift tier_1 sub-block from it without a second computation.
        "structure_drift_tier1": structure_drift_tier1,
    }
