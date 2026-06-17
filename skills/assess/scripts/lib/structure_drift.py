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

Tier 1 (grouping declared boundaries against where their files have scattered,
task 10) is deliberately *not* here yet; it will ADD a second function to this
same module, so the Tier 0 surface is kept small and the run-context block schema
leaves room for a ``tier_1`` sibling alongside ``tier_0_available``.
"""
from __future__ import annotations

from pathlib import Path

from lib.ownership_parser import (
    _discover_arch_docs,
    _extract_path_refs,
    _FENCE_RE,
    _HEADER_RE,
    _resolve_ref,
    _tracked_rel_paths,
    find_empty_globs,
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
