"""Signal C: complexity x doc-state join (joins two artifacts the skill already has).

The treemap tells us *where the complexity is*; the doc-staleness metric tells
us *whether the map beside it still tracks the territory*. Crossing them turns
each into something neither has alone -- a judgement about whether a complex
unit is safely *legible through a keyhole*:

  - complex + **fresh doc**  -> the doc is the contract (good; footprint relieved).
  - complex + **no doc**     -> high load, no summary an agent can read first.
  - complex + **stale doc**  -> a *lying map* over dangerous territory. The doc
                                tells the agent the wrong thing; worse than none.

Quantified as a single signed number per unit::

    doc_value = complexity_summarised x freshness

where ``complexity_summarised`` is the **max cyclomatic complexity** of the code
the doc covers, and ``freshness`` is **signed** in ``[-1, +1]`` -- positive when
the doc keeps pace with its code, crossing to **negative** once the code has
churned past the doc by more than a staleness threshold. Two consequences fall
straight out of the multiplication, exactly as the PRD requires:

  - trivial code  -> ``complexity_summarised`` is small  -> ``doc_value`` ~= 0
    regardless of the doc's state (an out-of-date note on a one-liner is noise,
    not a finding).
  - stale doc over complex code -> ``freshness < 0`` -> ``doc_value < 0``: a doc
    in this quadrant is *worth less than no doc*, so the recommendation is
    fix-or-DELETE, never "preserve".

**Slop-doc guard (hard constraint).** Nothing here ever recommends
auto-generating a doc to clear a flag -- that manufactures lying maps at scale.
An honest *undocumented* unit (``unexplained_complexity``, ``doc_value == 0``)
must score **strictly safer** than a hollow stale summary (``lying_map``,
``doc_value < 0``), and it does: ``0 > negative``. Recommendations are advice
for a human, never an instruction to synthesise prose.

**Doc->code mapping is deliberately fuzzy** -- nearest-ancestor by path
proximity over the paths already present in the two input artifacts. It is a
*candidate* signal pointing a human at a unit, not a verdict, so no filesystem
read or symbol resolution is required (and the function stays trivially
testable from mocked dicts).

This module is **standalone**: it consumes the JSON-serialisable outputs of the
complexity treemap (``complexity-stats.json``) and ``analyze_doc_staleness`` and
returns a JSON-serialisable dict. Integration into ``assess_core`` /
``run-context.json`` is a separate task's job -- nothing here imports or edits
the core.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath

# Code that churns more than this multiple of its doc's own churn is treated as
# having outrun the map: freshness crosses zero here and reaches -1 at twice
# this ratio. 2.0 = "the code changed twice as often as anyone touched the doc".
# The doc-staleness metric already computes this ratio (code_churn / doc_churn)
# as its core decaying-map signal; we only map it onto a signed scale.
STALENESS_RATIO_THRESHOLD = 2.0

# McCabe's classic "moderate risk" line. We gate findings on the *higher* of
# this floor and the repo's own 95th-percentile CCN, so a genuinely simple repo
# never sprouts "high complexity" findings, while a complex repo self-calibrates
# to flag only its worst ~5%.
MIN_HIGH_CCN = 10.0

# Per-finding advice. Every string is guidance for a human; none instructs
# anyone (or any tool) to synthesise documentation -- see the slop-doc guard.
_RECOMMENDATIONS = {
    "lying_map": (
        "Fix or DELETE this doc. A stale summary over complex code is worse "
        "than none -- it lies to the next agent. Do not auto-generate a "
        "replacement; a hollow summary is still a lying map."
    ),
    "unexplained_complexity": (
        "Complex code with no doc and no intent source. A human should write "
        "the missing contract. Do NOT auto-generate it -- an honest gap is "
        "safer than a synthetic summary."
    ),
    "good_contract": (
        "Keep. The doc is fresh and carries real complexity -- this is the "
        "contract relieving the comprehension footprint."
    ),
}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _extract_file_ccn(complexity_stats: dict) -> dict[str, float]:
    """Build path -> max-CCN from whatever per-file lists the stats expose.

    ``complexity-stats.json`` carries per-file CCN only in its ranked lists
    (``top_complex`` / ``top_hotspots`` / ``top_large``); we union them (and an
    optional full ``files`` list, for forward-compatibility) and keep the
    highest CCN seen per path. Percentiles live elsewhere in the dict and are
    read separately for the threshold.
    """
    ccn: dict[str, float] = {}
    for key in ("files", "top_complex", "top_hotspots", "top_large"):
        for entry in complexity_stats.get(key) or []:
            path = entry.get("path")
            if path is None or entry.get("ccn") is None:
                continue
            value = float(entry["ccn"])
            if value > ccn.get(path, float("-inf")):
                ccn[path] = value
    return ccn


def _high_ccn_threshold(complexity_stats: dict) -> float:
    """The CCN at or above which a unit counts as 'high complexity'."""
    p95 = float((complexity_stats.get("ccn") or {}).get("p95", 0.0) or 0.0)
    return max(p95, MIN_HIGH_CCN)


def _signed_freshness(doc: dict) -> float:
    """Map the doc's staleness state onto a signed freshness in [-1, +1].

    **Generated docs with declared provenance** (issue #178) bypass the churn
    ratio entirely: a generated doc's freshness is determined by whether its
    declared source has moved on, not by how busy the surrounding code is. So
    when the doc-staleness block carries a ``provenance.source_newer`` verdict::

        source_newer == False -> +1.0  (doc still matches its source -> fresh,
                                         never a lying_map)
        source_newer == True  -> -1.0  (source has outrun the doc -> stale)

    A ``source_newer`` of ``None`` means provenance was declared but the
    comparison was indeterminate (no usable timestamps), so we fall back to the
    churn ratio below.

    Otherwise (the ordinary hand-written doc), ``ratio`` (code churn per unit of
    doc maintenance) is the decaying-map signal: 0 when the code is as quiet as
    the doc, large when the code churns while the doc sits frozen.
    Piecewise-linear::

        ratio = 0                       -> +1.0  (doc fully keeps pace)
        ratio = THRESHOLD               ->  0.0  (doc starts lagging)
        ratio >= 2 * THRESHOLD          -> -1.0  (doc has outrun, clamped)
    """
    provenance = doc.get("provenance")
    if isinstance(provenance, dict):
        source_newer = provenance.get("source_newer")
        if source_newer is True:
            return -1.0
        if source_newer is False:
            return 1.0
    ratio = float(doc.get("ratio", 0.0) or 0.0)
    return round(_clamp((STALENESS_RATIO_THRESHOLD - ratio) / STALENESS_RATIO_THRESHOLD,
                        -1.0, 1.0), 3)


def _doc_dir_parts(doc_path: str) -> tuple[str, ...]:
    """Directory the doc lives in, as path parts (() for a repo-root doc)."""
    return PurePosixPath(doc_path).parent.parts


def _covers(doc_dir: tuple[str, ...], code_parts: tuple[str, ...]) -> bool:
    """True if ``code`` sits in the doc's directory or any subdirectory."""
    return code_parts[: len(doc_dir)] == doc_dir


def _assign_code_to_docs(
    code_paths: list[str], doc_paths: list[str],
) -> dict[str, list[str]]:
    """Assign each code file to its *nearest-ancestor* doc by path proximity.

    A doc covers code in its own directory and below; when several docs cover
    the same file the deepest (longest directory prefix) wins -- the same
    nearest-match rule ``CODEOWNERS`` and the doc-staleness metric use. Ties
    break on doc path for determinism. Files no doc covers are simply absent
    from the result (they become ``unexplained_complexity`` candidates).
    """
    doc_dirs = {d: _doc_dir_parts(d) for d in doc_paths}
    assignment: dict[str, list[str]] = {d: [] for d in doc_paths}
    for code in code_paths:
        code_parts = PurePosixPath(code).parts
        candidates = [
            (len(dir_parts), doc)
            for doc, dir_parts in doc_dirs.items()
            if _covers(dir_parts, code_parts)
        ]
        if not candidates:
            continue
        _, nearest = max(candidates, key=lambda t: (t[0], t[1]))
        assignment[nearest].append(code)
    return assignment


def analyze_doc_complexity_join(
    complexity_stats: dict, doc_staleness: dict, repo_root: Path,
) -> dict:
    """Join complexity hotspots with doc freshness into Signal C findings.

    Args:
        complexity_stats: the ``complexity-stats.json`` sidecar (per-file CCN in
            its ranked lists; CCN percentiles under ``ccn``).
        doc_staleness: the dict returned by ``analyze_doc_staleness`` (per-doc
            ``ratio`` / ``confidence`` under ``docs``).
        repo_root: repository root. Accepted for signature parity with the rest
            of the pipeline; the join itself needs no filesystem access (the
            doc->code mapping is path-proximity over the input artifacts), which
            keeps it deterministic and testable from mocked dicts.

    Returns a JSON-serialisable dict::

        {
          "available": bool,
          "high_ccn_threshold": float,
          "docs": [ {path, complexity_summarised, freshness, doc_value,
                     finding, confidence, subject_code_count, recommendation} ],
          "findings": {"lying_maps": [...], "unexplained_complexity": [...],
                       "good_contracts": [...]},
        }

    The ``docs`` list mixes two unit kinds: real docs (a freshness-signed
    ``doc_value``) and undocumented high-complexity code surfaced as
    ``unexplained_complexity`` (``freshness == 0`` -> ``doc_value == 0``, per the
    PRD's "missing -> 0" rule). Suitable as-is for ``run-context.json``'s
    ``documentation`` block (task #5's job to place it there).
    """
    repo_root = Path(repo_root)  # parity only; unused by the deterministic join

    file_ccn = _extract_file_ccn(complexity_stats)
    threshold = _high_ccn_threshold(complexity_stats)

    docs_in = (
        doc_staleness.get("docs", [])
        if doc_staleness.get("available", False)
        else []
    )
    # Churn-measurement reliability (set once in lib.git_churn, carried on the
    # doc-staleness block). When the history is degenerate - every file ~1 commit
    # (shallow clone, fresh import, squashed/extracted tree) - the `ratio` that
    # drives `freshness` is built on a churn count that means nothing, even
    # though the doc->code association may be perfectly precise. `confidence`
    # encodes association precision, not measurement reliability, so a precise
    # map over a meaningless churn signal would otherwise stamp a high-confidence
    # lying_map. Cap every doc's confidence to "low" so the existing
    # low-confidence guard below suppresses the lying_map classification.
    churn_degenerate = bool(doc_staleness.get("churn_degenerate", False))
    doc_paths = [d["path"] for d in docs_in]
    assignment = _assign_code_to_docs(list(file_ccn), doc_paths)
    covered_code = {c for codes in assignment.values() for c in codes}

    units: list[dict] = []
    lying_maps: list[dict] = []
    unexplained: list[dict] = []
    good_contracts: list[dict] = []

    # --- Real docs: classify by (complexity it covers) x (its freshness) ---
    for doc in docs_in:
        path = doc["path"]
        subject = assignment.get(path, [])
        complexity_summarised = round(
            max((file_ccn[c] for c in subject), default=0.0), 2
        )
        freshness = _signed_freshness(doc)
        doc_value = round(complexity_summarised * freshness, 3)
        # Provenance (issue #178): when the freshness came from a definite
        # source-vs-doc comparison (a generated doc declaring its source), it is
        # a direct, high-confidence verdict - NOT the coarse churn ratio. The
        # churn-based confidence caps below (repo-baseline, degenerate history)
        # exist to discount the ratio; they must not suppress a provenance
        # verdict. So a provenance doc reports "high" confidence and bypasses the
        # low-confidence guard.
        prov = doc.get("provenance")
        has_provenance_verdict = (
            isinstance(prov, dict) and prov.get("source_newer") in (True, False)
        )
        confidence: str | None
        if has_provenance_verdict:
            confidence = "high"
        else:
            # Degenerate churn caps measurement confidence to "low" regardless of
            # how precise the association is (see churn_degenerate above). The
            # reported confidence reflects the cap so a downstream reader sees it.
            confidence = "low" if churn_degenerate else doc.get("confidence")

        finding: str | None = None
        # Trivial code never produces a finding: complexity_summarised below the
        # high bar means doc_value is already near zero, so the doc's state is
        # noise either way.
        if complexity_summarised >= threshold:
            # A low-confidence staleness signal (subject_method ==
            # "repo-baseline") measures the doc against repo-wide churn, not the
            # specific code it describes, so a doc edited today can still read as
            # "stale" purely because the repo is busy elsewhere. That is too
            # coarse to call a lying map - the same confidence guard the Layer 0
            # stale-hub reporting applies. Leave such a doc unclassified. A
            # provenance verdict is exempt (see has_provenance_verdict).
            low_confidence = confidence == "low" and not has_provenance_verdict
            if freshness < 0 and not low_confidence:
                finding = "lying_map"
            elif freshness > 0:
                finding = "good_contract"

        unit = {
            "path": path,
            "complexity_summarised": complexity_summarised,
            "freshness": freshness,
            "doc_value": doc_value,
            "finding": finding,
            "confidence": confidence,
            "subject_code_count": len(subject),
            "recommendation": _RECOMMENDATIONS.get(finding) if finding else None,
        }
        units.append(unit)
        if finding == "lying_map":
            lying_maps.append(unit)
        elif finding == "good_contract":
            good_contracts.append(unit)

    # --- Undocumented high-complexity code: unexplained_complexity ---
    # No covering doc => no intent source within this signal's reach. doc_value
    # is 0 (the "missing -> 0" branch), so an honest gap scores strictly safer
    # than a lying map -- the slop-doc guard in numbers.
    for code, ccn in file_ccn.items():
        if code in covered_code or round(ccn, 2) < threshold:
            continue
        unit = {
            "path": code,
            "complexity_summarised": round(ccn, 2),
            "freshness": 0.0,
            "doc_value": 0.0,
            "finding": "unexplained_complexity",
            "confidence": None,
            "subject_code_count": 0,
            "recommendation": _RECOMMENDATIONS["unexplained_complexity"],
        }
        units.append(unit)
        unexplained.append(unit)

    # Deterministic ordering, most-actionable first within each bucket.
    units.sort(key=lambda u: (u["doc_value"], u["path"]))
    lying_maps.sort(key=lambda u: (u["doc_value"], u["path"]))
    unexplained.sort(key=lambda u: (-u["complexity_summarised"], u["path"]))
    good_contracts.sort(key=lambda u: (-u["doc_value"], u["path"]))

    return {
        "available": True,
        "high_ccn_threshold": round(threshold, 2),
        "docs": units,
        "findings": {
            "lying_maps": lying_maps,
            "unexplained_complexity": unexplained,
            "good_contracts": good_contracts,
        },
    }
