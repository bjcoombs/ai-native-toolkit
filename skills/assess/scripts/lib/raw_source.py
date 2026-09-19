"""Raw-source subtree detection for /assess read-side metrics (issue #225).

Read-side navigability metrics - orphan rate, reachability, broken links - are a
property of the *curated* wiki: the navigable layer an agent traverses. A repo
can also legitimately track trees of raw, machine-extracted source documents - a
subject-access / disclosure export of hundreds of ``.msg`` / ``.pdf`` / ``.docx``
files converted to markdown, say. Those files are immutable raw sources: they
legitimately have no inbound wiki links and carry machine-extracted,
non-navigational links (``mailto:`` / ``tel:`` / footer URLs lifted from the
original document). Counting them as orphans and their links as broken inflates
the figures and masks the actionable curated-wiki signal - the read most likely
to drive a fix.

This module turns "is this subtree a raw-source dump?" into a deterministic,
threshold-based signal so :func:`lib.doc_graph.build_doc_graph` can exclude
qualifying subtrees from the headline read-side metrics and report them
separately, while leaving a repo with no such tree completely unaffected.

Which contributor tendency does this guard? **Accretion of raw inputs.** An
agent told to "ingest these documents" lands hundreds of converted files in the
tree; nothing in that loop wires them into the wiki, so the orphan count
ratchets up and the curated-layer signal drowns. The exclusion keeps the
read-side number honest about the layer a human actually curates, and surfaces
the raw tree by name + count so the exclusion stays legible rather than hidden.

Detection is **graph-derived** - it reuses the link graph ``build_doc_graph``
already computes, so there is no second parse - and operates on three per-doc
signals:

- ``in_degree``  - inbound wiki / markdown links (``0`` means nothing links to it)
- ``out_degree`` - outbound links to *other docs* (``0`` means no internal
  navigation out of the file)
- ``machine_links`` - count of non-navigational URI-scheme links (``mailto:``,
  ``tel:``, external ``http(s)``) - the machine-extraction fingerprint of a
  converted document

A doc is *link-isolated* when it has no inbound and no outbound internal links
(and is not an entry point). A subtree qualifies as raw-source when it is large
enough, almost entirely link-isolated, **and** a meaningful share of its docs
carry the machine-extraction fingerprint - the two conditions the triage
decision named: "high density of files with zero inbound wiki links combined
with machine-extracted, non-navigational content". Requiring the
machine-extraction share keeps a folder of genuinely standalone *curated* notes
(isolated, but written by hand with no machine links) from being mistaken for a
raw dump.

A second fingerprint, **working notes** (issue #366), catches the other tree
that drowns the curated signal: an agent's plans, session logs or tickets,
hundreds of pattern-named files hung off one backlog index. Each file has one
inbound link (from the index), so it is not an orphan and the raw-source test
never fires, yet the tree swamps the curated wiki's doc count and hub ranking.
The tendency is the same accretion: every task leaves a note, nothing ever
consolidates them. :func:`classify_working_notes_trees` names such trees so
``build_doc_graph`` excludes them the same way it excludes raw trees.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

# Conservative, precision-first thresholds. A false positive (excluding a
# curated folder) is the costly error - it hides real navigability gaps - so the
# bar is set high: a large, almost-entirely-isolated subtree, half of whose docs
# carry the machine-extraction fingerprint. A false negative (a borderline raw
# tree left counted) merely preserves today's behaviour and is recoverable via
# `.assess/config.toml` `exclude_dirs`.
RAW_TREE_MIN_FILES = 10  # a tree, not a couple of stray files
RAW_TREE_ISOLATION_DENSITY = 0.9  # >= this fraction must be link-isolated
RAW_TREE_MACHINE_DENSITY = 0.5  # >= this fraction must carry a machine link


def _ancestor_dirs(rel: str) -> list[str]:
    """Return every ancestor directory of a posix rel path, root excluded.

    ``"a/b/c.md"`` -> ``["a", "a/b"]``; a root-level file ``"c.md"`` -> ``[]``
    (it has no enclosing subtree, so it can never anchor a raw-tree exclusion).
    """
    parts = rel.split("/")
    return ["/".join(parts[:i]) for i in range(1, len(parts))]


def _is_ancestor_path(ancestor: str, path: str) -> bool:
    """True when ``path`` is ``ancestor`` itself or nested beneath it."""
    return path == ancestor or path.startswith(ancestor + "/")


def _is_isolated(signal: dict[str, Any], rel: str, entries: frozenset[str] | set[str]) -> bool:
    """A doc with no inbound and no outbound internal links, and not an entry."""
    if rel in entries:
        return False
    return int(signal.get("in_degree", 0)) == 0 and int(signal.get("out_degree", 0)) == 0


def classify_raw_trees(
    doc_signals: dict[str, dict],
    *,
    entries: frozenset[str] | set[str] | None = None,
    min_files: int = RAW_TREE_MIN_FILES,
    isolation_density: float = RAW_TREE_ISOLATION_DENSITY,
    machine_density: float = RAW_TREE_MACHINE_DENSITY,
) -> list[dict]:
    """Identify maximal raw-source subtrees from per-doc graph signals.

    ``doc_signals`` maps a doc's repo-relative posix path to a dict with
    ``in_degree`` / ``out_degree`` / ``machine_links``. ``entries`` is the set of
    entry-point doc paths (README / MOC / base hubs), which never count toward a
    subtree's link-isolation. Returns a list of ``{"path", "file_count",
    "docs"}`` for each *outermost* qualifying subtree, sorted by path. An empty
    list means no raw-source tree was detected (the common case - the repo is
    unaffected).

    Pure and IO-free: the unit the tests pin. ``build_doc_graph`` gathers the
    signals and acts on the verdict.
    """
    entries = entries or frozenset()

    # Group every doc under each of its ancestor directories so a subtree's
    # stats include all descendants, not just direct children.
    by_dir: dict[str, list[str]] = {}
    for rel in doc_signals:
        for d in _ancestor_dirs(rel):
            by_dir.setdefault(d, []).append(rel)

    qualifying: dict[str, list[str]] = {}
    for directory, docs in by_dir.items():
        n = len(docs)
        if n < min_files:
            continue
        isolated = sum(1 for r in docs if _is_isolated(doc_signals[r], r, entries))
        machine = sum(1 for r in docs if int(doc_signals[r].get("machine_links", 0)) > 0)
        if isolated / n >= isolation_density and machine / n >= machine_density:
            qualifying[directory] = docs

    # Keep only the outermost qualifying subtrees: a qualifying child nested
    # under a qualifying parent is subsumed by the parent's exclusion.
    kept: list[str] = []
    for directory in sorted(qualifying, key=lambda x: (x.count("/"), x)):
        if any(_is_ancestor_path(anc, directory) for anc in kept):
            continue
        kept.append(directory)

    return [
        {
            "path": directory,
            "file_count": len(qualifying[directory]),
            "docs": sorted(qualifying[directory]),
        }
        for directory in sorted(kept)
    ]


# Working-notes thresholds (issue #366), precision-first for the same reason as
# the raw-tree ones: excluding a curated folder hides real navigability gaps,
# while missing a notes tree only keeps today's figures (and `.assess/config.toml`
# can name the tree). All three legs must hold, so a curated wiki fails on names
# (varied), on in-degree (cross-linked pages have several inbound links) or on
# the index (its inbound links are spread across many pages).
WORKING_NOTES_MIN_FILES = 20  # a pile, not a small wiki section; no fixture pins a lower bound
WORKING_NOTES_PREFIX_SET = 3  # "a small set of prefixes": plan_/spike_/retro_ at most
WORKING_NOTES_NAME_DENSITY = 0.8  # >= this fraction share a prefix, date or ticket name
WORKING_NOTES_LOW_INDEGREE_DENSITY = 0.8  # >= this fraction have in-degree <= 1
WORKING_NOTES_INDEX_FILES = 2  # "one or two index files"
WORKING_NOTES_INDEX_SHARE = 0.6  # the top index files hold >= this share of inbound links

# 2026-01-31, 20260131, 2026_01_31 anywhere in the stem.
_DATE_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}[-_.]?(?:0[1-9]|1[0-2])[-_.]?(?:0[1-9]|[12]\d|3[01])(?!\d)")
# PROJ-123, gh-42: a tracker key and a number.
_TICKET_RE = re.compile(r"(?<![A-Za-z])[A-Za-z]{2,10}-\d+(?!\d)")
_WORD_RE = re.compile(r"[a-z]+")


def _name_key(rel: str) -> str:
    """The naming family a doc belongs to: a date, a ticket, or its first word.

    ``plan_07.md`` -> ``plan``; ``2026-01-31-standup.md`` -> ``<date>``;
    ``PROJ-12.md`` -> ``<ticket>``; ``0001-use-postgres.md`` -> ``use``.
    """
    stem = rel.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    if _DATE_RE.search(stem):
        return "<date>"
    if _TICKET_RE.search(stem):
        return "<ticket>"
    word = _WORD_RE.search(stem.lower())
    return word.group(0) if word else "<numeric>"


def _is_working_notes(docs: list[str], doc_signals: dict[str, dict]) -> bool:
    """All three legs of the working-notes fingerprint over one directory."""
    n = len(docs)
    families = Counter(_name_key(r) for r in docs)
    shared = sorted((c for c in families.values() if c > 1), reverse=True)
    if sum(shared[:WORKING_NOTES_PREFIX_SET]) / n < WORKING_NOTES_NAME_DENSITY:
        return False
    low = sum(1 for r in docs if int(doc_signals[r].get("in_degree", 0)) <= 1)
    if low / n < WORKING_NOTES_LOW_INDEGREE_DENSITY:
        return False
    sources = Counter(s for r in docs for s in doc_signals[r].get("inbound_sources", ()))
    total = sum(sources.values())
    held = sum(c for _, c in sources.most_common(WORKING_NOTES_INDEX_FILES))
    return total > 0 and held / total >= WORKING_NOTES_INDEX_SHARE


def classify_working_notes_trees(doc_signals: dict[str, dict]) -> list[dict]:
    """Identify working-notes subtrees from per-doc graph signals.

    ``doc_signals`` maps a doc's repo-relative posix path to a dict with
    ``in_degree`` and ``inbound_sources`` (the paths of the docs linking or
    referring to it). A directory qualifies when it holds at least
    ``WORKING_NOTES_MIN_FILES`` docs, most named in a small set of families,
    most with in-degree <= 1, and one or two docs hold most of their inbound
    links. The whole directory is the tree, its index file included.

    Returns ``{"path", "file_count", "docs"}`` per tree, sorted by path. Unlike
    raw trees, the *innermost* qualifying directory wins: a parent that
    qualifies only because a notes tree dominates it would otherwise take its
    curated siblings out of the headline with it.
    """
    by_dir: dict[str, list[str]] = {}
    for rel in doc_signals:
        for d in _ancestor_dirs(rel):
            by_dir.setdefault(d, []).append(rel)

    qualifying = {
        d: docs for d, docs in by_dir.items()
        if len(docs) >= WORKING_NOTES_MIN_FILES and _is_working_notes(docs, doc_signals)
    }
    kept = [
        d for d in qualifying
        if not any(o != d and _is_ancestor_path(d, o) for o in qualifying)
    ]
    return [
        {"path": d, "file_count": len(qualifying[d]), "docs": sorted(qualifying[d])}
        for d in sorted(kept)
    ]
