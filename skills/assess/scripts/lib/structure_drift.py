"""Structure-drift signals: declared ownership vs where the code actually lives.

Ownership is *declared* in two maps an LLM contributor reads as authoritative
boundaries - a GitHub ``CODEOWNERS`` (glob -> owner) and a boundary-declaring
``ARCHITECTURE.md`` / seam ``README.md`` (prose -> "module X owns these paths").
A declaration is a self-description under no pressure to stay true: a directory
gets renamed, a module's files scatter, a pattern is typo'd - and nothing forces
the map to follow. The map is then a *lying map of ownership*, the same defect as
a stale doc (a lying map of behaviour) or an aged TODO (a lying map of intent).
This module converts that drift into a deterministic signal so the honest action
(fix the map, or the layout) becomes the cheap one.

**Tier 0** (this module, task 9) is the cheapest, zero-threshold cut: a declared
pattern or path that matches *zero* tracked files on disk. It is the
enumerate-both-sides shape ``doc_graph.py`` uses for broken links - side A is the
declared boundaries (every CODEOWNERS glob + every ARCHITECTURE.md path
reference), side B is the tracked file set, and the finding is the declared
patterns whose match set is empty. Binary, no statistics: a pattern matches or it
does not. A pattern that matches only excluded files counts as empty (the
excludes are not part of the navigable repo a contributor reasons over).

The parse half is entirely ``ownership_parser`` (task 8): the same CODEOWNERS
glob resolution, the same architecture-doc discovery and path-reference
resolution, the same ``EXCLUDE_DIRS`` / tracked-file conventions. This module only
joins the two sides and reports the empty set - it re-implements no parsing.

Determinism is a contract: the same repo must produce byte-identical output, so
``empty_ownership_patterns`` (and every list) is sorted by a stable key and no
set/dict iteration order leaks out. The module degrades to ``available: False``
with a reason when no ownership map of either kind exists - it never crashes the
assessment.

**Tier 1** (this module, task 10) is the next cut up from Tier 0's binary
existence test: not "does the declared boundary still match *any* file?" but
"do the files a boundary groups together still belong together?". Three lenses
each induce a *grouping* of the repo's files - the **declared** one (an owner /
architecture module groups the files it claims), the **static** one (an import-
graph community groups modules that depend on each other), and the **historical**
one (files that keep co-changing in the same commit). Where they disagree about
which files belong together is the signal: a declared boundary the import graph
or the commit log has quietly split or fused.

The correctness property is **label invariance**. A grouping is not its
community *names* - relabel the communities, reorder them, swap which is "A" and
which is "B", and nothing about which files belong together has changed. A
partition therefore *is* its co-membership relation ``{(a, b) | same_group(a,
b)}``; two partitions are equal iff their relations are equal. Tier 1 reports
disagreement as set operations over canonical file-pairs ``(min(a, b), max(a,
b))``, so the metric is invariant to any community relabeling by construction -
the relation never carries a label to permute. This is the whole correctness
contract of the tier, and the suite pins it directly (build communities, relabel
them, assert identical metrics).

A repo legitimately groups some directories together by design - a lib module
and its test, a packager and the thing it packages. Those known-good seams are an
allowlist subtracted from the *denominator* (correct by construction: a seam can
only shrink the disagreement set, never inflate it), so owned cohesion never
reads as drift. The tier degrades to ``available: False`` when there is no
ownership map to ground the human grouping, or when the static / historical
lenses are unavailable - it never crashes the assessment.
"""
from __future__ import annotations

from itertools import combinations
from pathlib import Path

from lib.ownership_parser import (
    _discover_arch_docs,
    _extract_path_refs,
    _FENCE_RE,
    _HEADER_RE,
    _resolve_ref,
    _tracked_rel_paths,
    find_empty_globs,
    parse_architecture_md,
    parse_codeowners,
)
from lib.git_churn import tracked_files


def _empty_codeowners_patterns(repo_root: Path) -> list[dict]:
    """CODEOWNERS globs that match zero tracked, non-excluded files.

    Pure reuse of task 8: ``parse_codeowners`` resolves each glob against the
    tracked file set (a pattern matching only excluded files resolves to an empty
    set there too), and ``find_empty_globs`` flags the empties. Returns
    ``[{pattern, declared_in, owners}]`` - ``owners`` is empty (CODEOWNERS owner
    tokens are not retained by the parser) and present only so the architecture
    and CODEOWNERS rows share one shape.
    """
    codeowners = parse_codeowners(repo_root)
    return [
        {"pattern": e["pattern"], "declared_in": e["declared_in"], "owners": []}
        for e in find_empty_globs(codeowners)
    ]


def _empty_architecture_refs(repo_root: Path) -> list[dict]:
    """Architecture-doc path references that resolve to zero tracked files.

    ``parse_architecture_md`` (task 8) drops references that resolve to nothing -
    it keeps a declared module's *real* files only - so the stale reference we
    want to flag is exactly the one it discards. Rather than re-implement that
    parse, we re-walk the same architecture docs (``_discover_arch_docs``) with
    the same building blocks (``_FENCE_RE`` strip, ``_HEADER_RE`` sectioning,
    ``_extract_path_refs``, ``_resolve_ref``) and keep the references whose
    resolution is empty - a declared boundary the filesystem has left behind (a
    renamed or deleted directory, a typo'd path).

    The reference text is the ``pattern``; ``declared_in`` is the ``<doc>::<header>``
    boundary that named it, so two docs declaring the same stale path don't merge.
    ``owners`` is empty (architecture docs carry no owner tokens). Returns one row
    per (boundary, reference); a reference repeated within a section is reported
    once.
    """
    docs = _discover_arch_docs(repo_root)
    if not docs:
        return []

    tracked = tracked_files(repo_root)
    rel_paths = set(_tracked_rel_paths(repo_root, tracked))

    out: list[dict] = []
    for doc in docs:
        try:
            text = doc.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            # Best-effort: an unreadable doc is skipped, never aborts the scan
            # (the same honest-degrade the parser applies).
            continue
        doc_rel = doc.relative_to(repo_root).as_posix()
        out.extend(_empty_refs_in_doc(text, doc_rel, repo_root, rel_paths))
    return out


def _empty_refs_in_doc(
    text: str, doc_rel: str, repo_root: Path, rel_paths: set[str],
) -> list[dict]:
    """Path references in one architecture doc that resolve to no tracked file.

    Mirrors ``ownership_parser._parse_one_arch_doc``: strip fenced code (a fence
    is a sample listing, not a boundary), section the body by markdown header, and
    attribute each reference to its section's ``<doc_rel>::<header>`` boundary.
    References before the first header belong to the doc itself
    (``<doc_rel>::<doc>``). A reference is flagged only when ``_resolve_ref``
    returns an empty set against the tracked file universe.
    """
    body = _FENCE_RE.sub("\n", text)
    current = f"{doc_rel}::{Path(doc_rel).name}"
    buffer: list[str] = []
    rows: list[dict] = []

    def flush(boundary: str, lines: list[str]) -> None:
        if not lines:
            return
        segment = "\n".join(lines)
        # Sort the references so a section's empty rows emit in a stable order
        # regardless of set iteration; the caller sorts the whole list again.
        for ref in sorted(_extract_path_refs(segment)):
            if not _resolve_ref(ref, repo_root, rel_paths):
                rows.append({
                    "pattern": ref, "declared_in": boundary, "owners": [],
                })

    for line in body.splitlines():
        m = _HEADER_RE.match(line)
        if m:
            flush(current, buffer)
            buffer = []
            current = f"{doc_rel}::{m.group(2).strip()}"
        else:
            buffer.append(line)
    flush(current, buffer)
    return rows


def _count_declared_and_matched(repo_root: Path) -> tuple[int, int]:
    """Total declared patterns/paths and how many match at least one tracked file.

    Counts both sides' declarations: every CODEOWNERS glob, and every distinct
    architecture-doc path reference (per declaring boundary). ``matched`` is the
    declarations whose resolution is non-empty. Reuses the same parse/resolve path
    as the empty-set detectors so the two never disagree on what "declared" means.
    """
    codeowners = parse_codeowners(repo_root)
    declared = len(codeowners)
    matched = sum(1 for files in codeowners.values() if files)

    docs = _discover_arch_docs(repo_root)
    if docs:
        tracked = tracked_files(repo_root)
        rel_paths = set(_tracked_rel_paths(repo_root, tracked))
        for doc in docs:
            try:
                text = doc.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            d, m = _declared_refs_in_doc(text, repo_root, rel_paths)
            declared += d
            matched += m
    return declared, matched


def _declared_refs_in_doc(
    text: str, repo_root: Path, rel_paths: set[str],
) -> tuple[int, int]:
    """(declared, matched) path-reference counts for one architecture doc.

    Same sectioning as ``_empty_refs_in_doc`` - one declaration per (boundary,
    distinct reference) - but counts resolution outcomes instead of collecting the
    empties, so totals and the empty list are derived from the identical walk. The
    boundary name is irrelevant to a count, so sectioning here only flushes the
    buffer at each header rather than tracking the current boundary key.
    """
    body = _FENCE_RE.sub("\n", text)
    buffer: list[str] = []
    declared = 0
    matched = 0

    def flush(lines: list[str]) -> None:
        nonlocal declared, matched
        if not lines:
            return
        segment = "\n".join(lines)
        for ref in _extract_path_refs(segment):
            declared += 1
            if _resolve_ref(ref, repo_root, rel_paths):
                matched += 1

    for line in body.splitlines():
        if _HEADER_RE.match(line):
            flush(buffer)
            buffer = []
        else:
            buffer.append(line)
    flush(buffer)
    return declared, matched


def detect_path_existence_drift(
    repo_root: Path, extra_exclude_dirs: set[str] | None = None,
) -> dict:
    """Tier 0 structure drift: declared ownership patterns that match zero files.

    The cheapest, zero-threshold drift cut. Enumerates both sides - side A every
    declared boundary (CODEOWNERS globs + ARCHITECTURE.md path references), side B
    the tracked, non-excluded file set - and reports the declarations whose match
    set is empty. Binary, no statistics; a pattern matching only excluded files
    counts as empty.

    Returns a JSON-serialisable run-context ``structure_drift`` block::

        {
          available, reason,
          tier_0_available,
          empty_ownership_patterns: [{pattern, declared_in, owners}],
          total_patterns, matched_patterns, coverage_ratio,
          # legacy-shape mirrors for the orchestrator's enumerate-both-sides view:
          empty_globs: [{pattern, file, owner}],
          declared_paths, matched_paths,
        }

    Degrades to ``available: False`` with reason ``"no ownership map"`` when no
    CODEOWNERS and no boundary doc exists (no map to drift against). Every list is
    sorted by ``(pattern, declared_in)`` so the same repo yields byte-identical
    output. ``extra_exclude_dirs`` is accepted for signature parity with the other
    signals; the underlying parser applies the built-in ``EXCLUDE_DIRS`` already.
    """
    repo_root = repo_root.resolve()

    codeowners = parse_codeowners(repo_root)
    arch_docs = _discover_arch_docs(repo_root)
    if not codeowners and not arch_docs:
        return {
            "available": False,
            "reason": "no ownership map",
            "tier_0_available": False,
            "empty_ownership_patterns": [],
            "total_patterns": 0,
            "matched_patterns": 0,
            "coverage_ratio": 0.0,
            "empty_globs": [],
            "declared_paths": 0,
            "matched_paths": 0,
        }

    empties = _empty_codeowners_patterns(repo_root) + _empty_architecture_refs(repo_root)
    empties.sort(key=lambda e: (e["pattern"], e["declared_in"]))

    total, matched = _count_declared_and_matched(repo_root)
    coverage = round(matched / total, 3) if total else 0.0

    return {
        "available": True,
        "reason": "",
        "tier_0_available": True,
        "empty_ownership_patterns": empties,
        "total_patterns": total,
        "matched_patterns": matched,
        "coverage_ratio": coverage,
        # The orchestrator's enumerate-both-sides view (#59c) reads an
        # ``empty_globs`` list and a declared/matched count pair; mirror the
        # canonical fields into that shape so wiring stays a rename, not a reshape.
        "empty_globs": [
            {"pattern": e["pattern"], "file": e["declared_in"],
             "owner": (e["owners"][0] if e["owners"] else "")}
            for e in empties
        ],
        "declared_paths": total,
        "matched_paths": matched,
    }


# ===========================================================================
# Tier 1 - equivalence-relation grouping disagreement
# ===========================================================================
#
# A grouping of files is reported as its co-membership relation: the set of
# canonical pairs ``(min(a, b), max(a, b))`` whose two files share a group. This
# is the label-invariant representation - relabel or reorder the groups and the
# pair set is unchanged - so all disagreement is set algebra over pairs and never
# touches a community name.

Pair = tuple[Path, Path]


def _canonical_pair(a: Path, b: Path) -> Pair:
    """Order a file pair so ``(a, b)`` and ``(b, a)`` collapse to one key.

    Canonicalising on the POSIX path string makes the pair the same object
    regardless of which side a producer happened to list first - the property
    that lets the three relations be compared as plain sets.
    """
    return (a, b) if a.as_posix() <= b.as_posix() else (b, a)


def _pairs_within(files: set[Path]) -> set[Pair]:
    """Every canonical same-group pair among a set of files.

    A group of *n* files contributes the ``n*(n-1)/2`` unordered pairs that
    declare those files co-grouped. A singleton group contributes nothing (no
    pair to disagree about).
    """
    return {_canonical_pair(a, b) for a, b in combinations(sorted(files), 2)}


def human_grouping_relation(ownership_map: dict[str, set[Path]]) -> set[Pair]:
    """The declared grouping as its co-membership relation over file pairs.

    ``ownership_map`` is the ``{declared_boundary: {file_paths}}`` shape both
    ``parse_codeowners`` and ``parse_architecture_md`` produce: an owner or an
    architecture module mapped to the tracked files it claims. Each boundary
    asserts its files belong together, so it contributes every same-group pair;
    the relation is the union across boundaries. The result is label-invariant -
    it carries the *pairs*, never the boundary keys - so two ownership maps that
    group the same files identically yield the same relation even if every
    boundary were renamed.
    """
    relation: set[Pair] = set()
    for files in ownership_map.values():
        relation |= _pairs_within(files)
    return relation


def static_grouping_relation(
    repo_root: Path, communities: list[set[str]],
) -> set[Pair]:
    """The import-graph community grouping as a relation over file pairs.

    ``communities`` is ``structure_graph._detect_communities()``'s output: a list
    of sets of *dotted module names* (e.g. ``{"lib.doc_graph", "lib.git_churn"}``).
    Each community asserts its modules belong together; we resolve every module
    name to its repo-relative source file (via the same package-root resolution
    ``structure_graph`` uses) and contribute the same-group pairs over the files.
    A module that resolves to no tracked file is dropped, so a community of one
    resolvable module contributes nothing. Label-invariant by construction: the
    relation never records which community a pair came from.
    """
    module_to_path = _build_module_path_map(repo_root)
    if not module_to_path:
        return set()
    relation: set[Pair] = set()
    for community in communities:
        files = {
            module_to_path[m] for m in community if m in module_to_path
        }
        relation |= _pairs_within(files)
    return relation


def _build_module_path_map(repo_root: Path) -> dict[str, Path]:
    """Map every importable dotted module name to its repo-relative source file.

    Reuses ``structure_graph``'s package discovery and module->file resolution so
    a community's dotted names map back to the exact tracked paths the human and
    co-change relations are expressed in. Degrades to an empty map when grimp /
    networkx is unavailable or no package is found - the caller then yields an
    empty static relation rather than crashing.
    """
    try:
        from lib.structure_graph import (
            _build_grimp_graph,
            _module_file,
            discover_packages,
        )
    except ImportError:  # pragma: no cover - exercised only on a broken env
        return {}

    repo_root = repo_root.resolve()
    package_dirs = discover_packages(repo_root)
    if not package_dirs:
        return {}
    try:
        import_graph, _names, roots = _build_grimp_graph(package_dirs)
    except Exception:  # pragma: no cover - grimp parse failure on odd trees
        return {}

    mapping: dict[str, Path] = {}
    for module in import_graph.modules:
        src = _module_file(module, roots)
        if src is None:
            continue
        try:
            rel = src.resolve().relative_to(repo_root)
        except ValueError:
            continue
        mapping[module] = rel
    return mapping


def cochange_grouping_relation(
    coupling_pairs: list[dict], threshold_pct: float = 5.0,
) -> set[Pair]:
    """The historical co-change grouping as a relation over file pairs.

    ``coupling_pairs`` is ``change_coupling.change_coupling_pairs()``'s output:
    ``[{file_a, file_b, co_change_count, support_pct}]``. Unlike the human and
    static groupings (which assert transitive membership - everything in a group
    is co-grouped), co-change is *already* a pairwise relation: a pair is grouped
    iff it co-changed in at least ``threshold_pct`` percent of commits in the
    window. So no transitive closure is taken - each surviving pair maps straight
    to a canonical relation member. The threshold filters incidental single-commit
    coincidences from genuine coupling.
    """
    relation: set[Pair] = set()
    for entry in coupling_pairs:
        if float(entry.get("support_pct", 0.0)) < threshold_pct:
            continue
        relation.add(
            _canonical_pair(Path(entry["file_a"]), Path(entry["file_b"]))
        )
    return relation


def compute_grouping_disagreement(
    human_rel: set[Pair], static_rel: set[Pair], cochange_rel: set[Pair],
) -> dict:
    """Six set-operation metrics over the three grouping relations.

    Each metric is a set difference or intersection of two relations - pure pair
    algebra, so every value is invariant to how any lens labelled its groups:

      - ``human_grouped_static_splits`` (human - static): the declared boundary
        groups these files but the import graph splits them into different
        communities - a boundary the dependency structure no longer backs.
      - ``human_split_static_fuses`` (static - human): the import graph groups
        them but no declared boundary does - cohesion the ownership map misses.
      - ``human_grouped_never_cochange`` (human - cochange): declared together but
        the commit log never couples them above threshold - a boundary history
        does not exercise as a unit.
      - ``human_split_but_cochange`` (cochange - human): they keep co-changing but
        no declared boundary groups them - a hidden seam the map omits.
      - ``human_static_agree`` (human & static): declared and dependency lenses
        agree these belong together.
      - ``human_cochange_agree`` (human & cochange): declared and historical
        lenses agree.

    Every pair list is serialised as ``[{file_a, file_b}]`` sorted by
    ``(file_a, file_b)`` so the same relations always yield byte-identical output.
    Counts accompany each list so a caller (the orchestrator) can read magnitudes
    without re-counting.
    """
    sets = {
        "human_grouped_static_splits": human_rel - static_rel,
        "human_split_static_fuses": static_rel - human_rel,
        "human_grouped_never_cochange": human_rel - cochange_rel,
        "human_split_but_cochange": cochange_rel - human_rel,
        "human_static_agree": human_rel & static_rel,
        "human_cochange_agree": human_rel & cochange_rel,
    }
    out: dict = {}
    for name, pairs in sets.items():
        out[name] = _serialize_pairs(pairs)
        out[f"{name}_count"] = len(pairs)
    return out


def _serialize_pairs(pairs: set[Pair]) -> list[dict]:
    """Pairs as a sorted ``[{file_a, file_b}]`` list (no set order leaks out)."""
    rows = [
        {"file_a": a.as_posix(), "file_b": b.as_posix()} for a, b in pairs
    ]
    rows.sort(key=lambda r: (r["file_a"], r["file_b"]))
    return rows


# Directory-prefix seam pairs whose two trees move together *by design* in this
# repo - owned cohesion, not entanglement. A canonical file-pair is on the
# allowlist when one file sits under the first prefix and the other under the
# second (in either order). These are the two seams the lib README's co-change
# seam map documents: each deterministic ``lib/`` module is pinned by a test in
# ``skills/assess/tests`` ("Add a test alongside any change to a deterministic
# module"), and the standalone build under ``scripts`` vendors and transforms the
# very ``skills`` it packages. Allowlisting them subtracts the pair from the
# disagreement *denominator* so the intended boundary never reads as drift.
SEAM_ALLOWLIST: tuple[tuple[str, str], ...] = (
    ("skills/assess/scripts/lib", "skills/assess/tests"),
    ("scripts", "skills"),
)


def _under(path_str: str, prefix: str) -> bool:
    """True if a POSIX path string is the prefix dir or sits beneath it."""
    return path_str == prefix or path_str.startswith(prefix + "/")


def _pair_on_seam(pair_row: dict, seam: tuple[str, str]) -> bool:
    """True if a serialised pair straddles a seam's two directory prefixes."""
    lo, hi = seam
    a, b = pair_row["file_a"], pair_row["file_b"]
    return (_under(a, lo) and _under(b, hi)) or (_under(a, hi) and _under(b, lo))


def apply_seam_allowlist(
    disagreement: dict, allowlist: tuple[tuple[str, str], ...] = SEAM_ALLOWLIST,
) -> dict:
    """Drop allowlisted known-good seams from every disagreement pair list.

    A seam on the allowlist names two directory trees that co-change by design.
    Subtracting its pairs from the *denominator* is correct by construction - the
    operation can only remove a pair, never add one, so an allowlist can never
    manufacture drift, only suppress an owned boundary that would otherwise read
    as one. Returns a new dict with each ``*_count`` recomputed to match the
    filtered list; the ``agree`` lists are filtered too so a seam pair is not
    double-counted as both agreement and (suppressed) disagreement.
    """
    out: dict = {}
    for key, value in disagreement.items():
        if key.endswith("_count"):
            continue  # recomputed from the filtered list below
        kept = [
            row for row in value
            if not any(_pair_on_seam(row, seam) for seam in allowlist)
        ]
        out[key] = kept
        out[f"{key}_count"] = len(kept)
    return out


def detect_grouping_disagreement(
    repo_root: Path,
    communities: list[set[str]] | None = None,
    coupling_pairs: list[dict] | None = None,
    cochange_threshold_pct: float = 5.0,
    allowlist: tuple[tuple[str, str], ...] = SEAM_ALLOWLIST,
) -> dict:
    """Tier 1 structure drift: where the three grouping lenses disagree.

    Builds the declared grouping from the repo's ownership map (CODEOWNERS +
    architecture docs), the static grouping from the import-graph communities,
    and the historical grouping from the co-change pairs; reports their pairwise
    disagreement as label-invariant set operations, then subtracts the known-good
    architectural seams.

    The static and historical inputs are accepted as arguments so the
    orchestrator (task 11) can pass the communities and coupling pairs it already
    computes for the A2/B1 signals rather than re-running grimp and ``git log``.
    When omitted they are computed here so the function is usable standalone; a
    lens that cannot be computed (no Python packages, no git history) yields an
    empty relation and simply contributes no disagreement.

    Returns a JSON-serialisable dict mirroring Tier 0's degradation contract::

        {
          available, reason, tier_1_available,
          human_grouped_static_splits: [{file_a, file_b}], ..._count,
          human_split_static_fuses, human_grouped_never_cochange,
          human_split_but_cochange, human_static_agree, human_cochange_agree,
          (each with a sibling ``*_count``),
        }

    Degrades to ``available: False`` reason ``"no ownership map"`` when neither a
    CODEOWNERS nor a boundary doc exists - with no declared grouping to ground
    against, there is nothing to disagree with. Every list is sorted so the same
    repo yields byte-identical output.
    """
    repo_root = repo_root.resolve()

    ownership_map = _human_ownership_map(repo_root)
    if not ownership_map:
        return _tier1_unavailable("no ownership map")

    human_rel = human_grouping_relation(ownership_map)

    if communities is None:
        communities = _compute_communities(repo_root)
    static_rel = static_grouping_relation(repo_root, communities)

    if coupling_pairs is None:
        coupling_pairs = _compute_coupling_pairs(repo_root)
    cochange_rel = cochange_grouping_relation(
        coupling_pairs, cochange_threshold_pct,
    )

    disagreement = compute_grouping_disagreement(
        human_rel, static_rel, cochange_rel,
    )
    filtered = apply_seam_allowlist(disagreement, allowlist)

    return {
        "available": True,
        "reason": "",
        "tier_1_available": True,
        **filtered,
    }


def _tier1_unavailable(reason: str) -> dict:
    """The Tier 1 degraded block: every pair list empty, every count zero."""
    names = (
        "human_grouped_static_splits",
        "human_split_static_fuses",
        "human_grouped_never_cochange",
        "human_split_but_cochange",
        "human_static_agree",
        "human_cochange_agree",
    )
    block: dict = {
        "available": False,
        "reason": reason,
        "tier_1_available": False,
    }
    for name in names:
        block[name] = []
        block[f"{name}_count"] = 0
    return block


def _human_ownership_map(repo_root: Path) -> dict[str, set[Path]]:
    """The combined declared grouping (CODEOWNERS + architecture docs).

    Unions the two parser outputs into one ``{boundary: {files}}`` map. A glob
    that matches nothing contributes an empty file set (so it adds no pair); the
    Tier 0 signal is the place that flags those empties.
    """
    combined: dict[str, set[Path]] = {}
    for pattern, files in parse_codeowners(repo_root).items():
        combined.setdefault(f"CODEOWNERS::{pattern}", set()).update(files)
    for module, files in parse_architecture_md(repo_root).items():
        combined.setdefault(module, set()).update(files)
    return {k: v for k, v in combined.items() if v}


def _compute_communities(repo_root: Path) -> list[set[str]]:
    """Import-graph communities for the repo, or ``[]`` when unavailable.

    Builds the same grimp digraph ``structure_graph.analyze_structure`` builds and
    runs its community detection. Returns ``[]`` (no static lens) when grimp /
    networkx is missing or no Python package is found, so the caller's static
    relation is simply empty.
    """
    try:
        from lib.structure_graph import (
            _build_grimp_graph,
            _detect_communities,
            _NETWORKX_AVAILABLE,
            discover_packages,
            nx,
        )
    except ImportError:  # pragma: no cover - exercised only on a broken env
        return []
    if not _NETWORKX_AVAILABLE:
        return []

    package_dirs = discover_packages(repo_root)
    if not package_dirs:
        return []
    try:
        import_graph, _names, _roots = _build_grimp_graph(package_dirs)
    except Exception:  # pragma: no cover - grimp parse failure on odd trees
        return []

    modules = sorted(import_graph.modules)
    graph = nx.DiGraph()
    graph.add_nodes_from(modules)
    for m in modules:
        for dep in import_graph.find_modules_directly_imported_by(m):
            if dep in graph:
                graph.add_edge(m, dep)
    return _detect_communities(graph.to_undirected())


def _compute_coupling_pairs(repo_root: Path) -> list[dict]:
    """Co-change pairs for the repo, or ``[]`` when there is no git history."""
    try:
        from lib.change_coupling import (
            change_coupling_pairs,
            parse_commit_file_sets,
        )
    except ImportError:  # pragma: no cover - exercised only on a broken env
        return []
    return change_coupling_pairs(parse_commit_file_sets(repo_root))
