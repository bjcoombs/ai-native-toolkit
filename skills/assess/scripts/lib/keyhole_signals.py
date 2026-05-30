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

from collections import Counter, defaultdict
from pathlib import Path

from lib.change_coupling import (
    authorship_analysis,
    change_coupling_pairs,
    containment_ratio,
    parse_commit_file_sets,
)
from lib.coupling_analysis import detect_hidden_coupling, find_refactor_boundaries
from lib.doc_complexity_join import (
    _extract_file_ccn,
    _high_ccn_threshold,
    analyze_doc_complexity_join,
)
from lib.liveness_scan import STATIC_REACHABILITY_CAVEAT
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

# The six named derived findings, in a fixed report order (worst-first, the one
# positive last). The action strings are the deterministic recommendation the
# report leads with; the LLM elaborates but never contradicts them.
FINDING_ORDER = [
    "hidden_coupling",
    "lying_map",
    "unexplained_complexity",
    "orphaned_understanding",
    "candidate_dead_weight",
    "refactor_boundary",
]
FINDING_ACTIONS = {
    "hidden_coupling": "investigate the seam",
    "lying_map": "fix or delete the doc",
    "unexplained_complexity": "write the missing contract (do NOT auto-generate)",
    "orphaned_understanding": "assign a human anchor before further change",
    "candidate_dead_weight": "verify liveness, then delete if dead",
    "refactor_boundary": "safe to hand an agent in isolation",
}


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
) -> dict:
    """Build the five run-context blocks + derived findings + attention list.

    Pure orchestration over the lib signals. ``commit_sets`` may be passed in
    (the orchestrator parses git log once and reuses it for churn etc.);
    otherwise it is parsed here. Every block is built defensively - a failure in
    one degrades that block to ``available: False`` and leaves the rest intact.
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

    findings = assemble_findings({
        "hidden_coupling": [h["path"] for h in behaviour.get("hidden_coupling_findings", [])],
        "lying_map": [d["path"] for d in documentation.get("stale_doc_on_complexity", [])],
        "unexplained_complexity": [
            d["path"] for d in documentation.get("unexplained_complexity", [])
        ],
        "orphaned_understanding": understanding.get("orphaned_understanding", []),
        "candidate_dead_weight": dead_weight,
        "refactor_boundary": [b["path"] for b in behaviour.get("refactor_boundaries", [])],
    })

    return {
        "structure": structure,
        "behaviour": behaviour,
        "documentation": documentation,
        "understanding": understanding,
        "runtime": runtime,
        "derived_findings": findings,
        "attention": build_attention_list(findings),
    }
