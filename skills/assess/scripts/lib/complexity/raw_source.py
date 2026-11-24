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
threshold-based signal so :func:`lib.docs.doc_graph.build_doc_graph` can exclude
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
``build_doc_graph`` excludes them the same way it excludes raw trees. One
invariant bounds what such a tree takes out of the headline: a doc leaves only
if it is itself a positional note, or an index whose links go into such notes.

Raw-source subtree detection (issue #225). Threshold-based, IO-free classifier:
given per-doc graph signals (in/out degree + a machine-extraction-link count), it
identifies maximal directory subtrees that are large, almost entirely
link-isolated, and carry the machine-extraction fingerprint - a dump of raw,
machine-extracted source documents (a disclosure / SAR export of converted
`.msg`/`.pdf` files). `doc_graph.py` gathers the signals from the link graph it
already builds and excludes the qualifying subtrees from the headline read-side
metrics (orphan rate, reachability, broken links), reporting them separately so
the curated-wiki signal isn't drowned. Pure - no `doc_graph` import - so
`doc_graph.py` owns the graph and consumes this module's verdict. Co-changes with
`doc_graph.py` (its consumer) and its test `tests/test_raw_source.py`.

Its second classifier, `classify_working_notes_trees` (issue #366), finds
working-notes trees: at least `WORKING_NOTES_MIN_FILES` docs, most named in a
small set of sequence families (a date, or a word then an integer that ends the
name: `plan_07`, `PROJ-123`; a counter followed by a title such as
`adr-0001-use-postgres`, a dotted version, or a shared word alone like
`how-to-*` is no family), most with in-degree <= 1, and one or two index docs
linking to most of the tree (coverage of the tree, not a share of whatever edges
exist, so one stray link into an unlinked pile does not qualify it) - an agent's
plans or session logs hung off a backlog index. One invariant bounds the
exclusion: a doc leaves the headline only if it is itself a positional note (a
name family, or a member of a deeper tree already accepted) or an index whose
links go into such notes, one of the top sources of their inbound links rather
than a page citing one note. The tree is those docs: a subdirectory holding no
note stays counted whole (a `docs/guides/` of curated pages beside 50 notes in
`docs/`), and any other curated doc refuses the directory, leaving its deeper
trees to stand alone (`docs/guide.md` beside `docs/notes/`). `notes/backlog.md`
over `notes/2025/` and `notes/2026/` is one tree. Subdirectories are decided
deepest first, and the tree must still pass the three legs on its own. Two
`.assess/config.toml` keys override the verdict (issue #367), passed in as the
`force` / `ignore` arguments: every doc under a `working_notes_dirs` directory
joins a tree at that path whatever its size or fingerprint, and no doc under a
`working_notes_ignore` directory joins any tree (a misclassified `chapter-01` to
`chapter-20` series stays counted); ignore wins where they overlap, and an outer
tree absorbs any tree inside it. A release-note fragment directory (#1227) is a
working-notes tree too: towncrier-style `<issue>.<kind>.md` files beside a
`README.md`, unlinked until the release folds them. At least two fragments with no
inbound link (a `.base` hub counts as one) must be present; only they leave the
headline, and the README stays counted. Only docs are classified, never
a `.base` hub. It runs on the headline graph (link and reference edges) after
the raw pass. `doc_graph.py` excludes these trees too and reports
`excluded_working_notes_trees`, `working_notes_doc_count`,
`working_notes_orphan_rate` and `working_notes_broken_links`.
"""
from __future__ import annotations

import re
from collections import Counter

from lib.run_context_types import DocSignal, SourceTree

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


def _is_isolated(signal: DocSignal, rel: str, entries: frozenset[str] | set[str]) -> bool:
    """A doc with no inbound and no outbound internal links, and not an entry."""
    if rel in entries:
        return False
    return int(signal.get("in_degree", 0)) == 0 and int(signal.get("out_degree", 0)) == 0


def classify_raw_trees(
    doc_signals: dict[str, DocSignal],
    *,
    entries: frozenset[str] | set[str] | None = None,
    min_files: int = RAW_TREE_MIN_FILES,
    isolation_density: float = RAW_TREE_ISOLATION_DENSITY,
    machine_density: float = RAW_TREE_MACHINE_DENSITY,
) -> list[SourceTree]:
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
# while missing a notes tree only keeps today's figures. All three legs must
# hold, and the tree takes out only notes and their index (see `_tree_docs`).
#
# A working-notes name family is a series whose names are positions, not
# subjects: every name is a date (2026-01-31-standup) or a word and a counter
# with nothing after it (plan_07, PROJ-123), so the name says when or which
# entry and never what the page is about. A counter followed by a title
# (adr-0001-use-postgres, rfc-042-streaming, step-1-install) names a subject,
# and a dotted version (release-2.1.0, v1.2.3) is a release, not a counter; both
# are curated, as is a shared word with no counter (how-to-deploy). The rule
# separates shape, not intent: a numbered series under its own table of
# contents (chapter-01 .. chapter-20) passes all three legs exactly as plan_NN
# does and is excluded. `.assess/config.toml` `working_notes_ignore` keeps
# such a series counted, and `working_notes_dirs` forces a tree the
# fingerprint misses (issue #367). A cross-linked wiki
# fails on in-degree (several inbound links per page) and on the index (no one
# or two pages link to most of it).
WORKING_NOTES_MIN_FILES = 20  # a pile, not a small wiki section
WORKING_NOTES_PREFIX_SET = 3  # "a small set of prefixes": plan_/spike_/retro_ at most
WORKING_NOTES_NAME_DENSITY = 0.8  # >= this fraction carry a sequence name in a shared family
WORKING_NOTES_LOW_INDEGREE_DENSITY = 0.8  # >= this fraction have in-degree <= 1
WORKING_NOTES_INDEX_FILES = 2  # "one or two index files"
WORKING_NOTES_INDEX_SHARE = 0.6  # the top index files link to >= this fraction of the tree

# 2026-01-31, 20260131, 2026_01_31 anywhere in the stem.
_DATE_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}[-_.]?(?:0[1-9]|1[0-2])[-_.]?(?:0[1-9]|[12]\d|3[01])(?!\d)")
# plan_07, plan-07, PROJ-123: a word prefix, a separator, then an integer that
# ends the stem. The stem is lowercased first, so a ticket key is the family of
# its tracker (PROJ-1 and proj-2 are both ``proj``).
_SEQUENCE_RE = re.compile(r"^([a-z]+(?:[-_ ][a-z]+)*)[-_ ]\d+$")


# Release-note fragments (#1227): towncrier-style `<issue>.<kind>.md` files (or
# `+<slug>.<kind>.md`, an orphan fragment, with an optional `.<n>` counter) in a
# directory with a README that explains them. Each is written unlinked on purpose
# and deleted when the release folds it, so an orphan fragment is a working note.
# The issue number never has a leading zero: towncrier writes none, while chapter
# series (`01.intro.md`) usually pad, and those pages are real orphans.
FRAGMENT_TREE_MIN_FILES = 2  # a series, not one numbered page
_FRAGMENT_RE = re.compile(
    r"^(?:[1-9]\d*|\+[\w-]+)\.[a-z][a-z0-9_-]*(?:\.\d+)?\.(?:md|mdx|markdown)$", re.IGNORECASE,
)


def _fragment_trees(doc_signals: dict[str, DocSignal]) -> dict[str, list[str]]:
    """Release-note fragment directories, each mapped to its unlinked fragments.

    A directory qualifies when it holds a ``README.md`` and at least
    ``FRAGMENT_TREE_MIN_FILES`` fragments nothing links to, a `.base` hub
    included. Only those fragments leave the headline: the README, any other doc
    and a linked fragment stay counted. The floor keeps one unlinked
    ``01.setup.md`` beside a README counted as the orphan it is."""
    readme_dirs: set[str] = set()
    fragments: dict[str, list[str]] = {}
    for rel in sorted(doc_signals):
        head, _, name = rel.rpartition("/")
        if not head:
            continue  # a root-level doc never anchors a tree
        if name.lower() == "readme.md":
            readme_dirs.add(head)
        elif _FRAGMENT_RE.match(name) and not _linked(doc_signals[rel]):
            fragments.setdefault(head, []).append(rel)
    return {
        d: f for d, f in fragments.items()
        if d in readme_dirs and len(f) >= FRAGMENT_TREE_MIN_FILES
    }


def _linked(signal: DocSignal) -> bool:
    """True when anything links to the doc: another doc, or a `.base` hub,
    which ``graph_in_degree`` counts and the doc-only ``in_degree`` does not."""
    return max(int(signal.get("graph_in_degree", 0)), int(signal.get("in_degree", 0))) > 0


def _name_key(rel: str) -> str | None:
    """The sequence family a doc's name belongs to, or None when it has none.

    ``plan_07.md`` and ``plan-07.md`` -> ``plan``; ``PROJ-12.md`` -> ``proj``;
    ``2026-01-31-standup.md`` -> ``<date>``; ``how-to-deploy.md``,
    ``adr-0001-use-postgres.md``, ``release-2.1.0.md`` and ``v1.2.3.md`` -> None.
    """
    stem = rel.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    if _DATE_RE.search(stem):
        return "<date>"
    seq = _SEQUENCE_RE.match(stem.lower())
    return seq.group(1) if seq else None


def _is_working_notes(docs: list[str], doc_signals: dict[str, DocSignal]) -> bool:
    """All three legs of the working-notes fingerprint over one directory."""
    n = len(docs)
    families = Counter(k for k in map(_name_key, docs) if k is not None)
    shared = sorted((c for c in families.values() if c > 1), reverse=True)
    if sum(shared[:WORKING_NOTES_PREFIX_SET]) / n < WORKING_NOTES_NAME_DENSITY:
        return False
    low = sum(1 for r in docs if int(doc_signals[r].get("in_degree", 0)) <= 1)
    if low / n < WORKING_NOTES_LOW_INDEGREE_DENSITY:
        return False
    # Coverage, not concentration: the index files must link to most of the
    # tree. A share of whatever edges exist goes vacuous on a sparse pile (one
    # stray link would be 1/1 and hide every unlinked note).
    sources = Counter(s for r in docs for s in doc_signals[r].get("inbound_sources", ()))
    held = sum(c for _, c in sources.most_common(WORKING_NOTES_INDEX_FILES))
    return held >= WORKING_NOTES_INDEX_SHARE * n


def _tree_docs(directory: str, docs: list[str], doc_signals: dict[str, DocSignal],
               trees: dict[str, list[str]]) -> list[str] | None:
    """The docs ``directory`` takes out of the headline, or None to refuse.

    Invariant: a doc leaves the headline only if it is itself a positional
    note, or an index whose links go into such notes. A note is a doc with a
    name family, or a member of a deeper tree already accepted (``trees``,
    decided deepest first). An index is one of the top
    ``WORKING_NOTES_INDEX_FILES`` sources of the notes' inbound links, each
    holding at least an equal part of ``WORKING_NOTES_INDEX_SHARE``; a curated
    page citing one note is a source, not an index. Any other doc stays
    counted: a subdirectory holding no note at all is left out of the tree
    whole, and any other curated doc refuses the directory, so its notes are
    decided by their own subdirectories instead."""
    nested = {r for o, t in trees.items() if _is_ancestor_path(directory, o) for r in t}
    notes = {r for r in docs if r in nested or _name_key(r) is not None}
    sources = Counter(s for r in notes for s in doc_signals[r].get("inbound_sources", ()))
    floor = sum(sources.values()) * WORKING_NOTES_INDEX_SHARE / WORKING_NOTES_INDEX_FILES
    indexes = {s for s, c in sources.most_common(WORKING_NOTES_INDEX_FILES) if c >= floor}

    def child(rel: str) -> str | None:
        head, sep, _ = rel[len(directory) + 1:].partition("/")
        return head if sep else None

    noted = {child(r) for r in notes}
    tree = []
    for r in docs:
        if r in notes or r in indexes:
            tree.append(r)
        elif child(r) is None or child(r) in noted:
            return None
    if len(tree) < WORKING_NOTES_MIN_FILES or not _is_working_notes(tree, doc_signals):
        return None
    return tree


def classify_working_notes_trees(
    doc_signals: dict[str, DocSignal], *,
    force: list[str] | tuple[str, ...] = (), ignore: list[str] | tuple[str, ...] = (),
) -> list[SourceTree]:
    """Identify working-notes subtrees from per-doc graph signals.

    ``doc_signals`` maps a doc's repo-relative posix path to a dict with
    ``in_degree`` and ``inbound_sources`` (the paths of the docs linking or
    referring to it). A directory qualifies when it holds at least
    ``WORKING_NOTES_MIN_FILES`` docs, most named in a small set of families,
    most with in-degree <= 1, and one or two docs link to most of the tree.
    The tree is its notes and their index (``_tree_docs``): a subdirectory
    holding no note stays counted whole, and any other curated doc refuses the
    directory, leaving its deeper trees to stand alone.

    Two overrides from ``.assess/config.toml`` (issue #367), both lists of
    repo-relative directories: every doc under a ``force`` directory joins a
    tree at that path whatever its size or fingerprint, and no doc under an
    ``ignore`` directory joins any tree, so ``ignore`` wins where they overlap.
    Ignored docs are removed from the fingerprint's trees, not from its input,
    so ignoring a directory never qualifies its parent; a fingerprint tree
    whose remaining docs no longer pass the size and fingerprint tests is
    dropped whole, so its index returns to the headline with the ignored notes.
    A forced directory holding no doc is not reported. A release-note fragment
    directory (``_fragment_trees``, #1227) joins as a tree of its unlinked
    fragments, under the same ``ignore`` rule.

    Returns ``{"path", "file_count", "docs"}`` per outermost tree, sorted by
    path; an outer tree absorbs the docs of any tree inside it.
    ``notes/backlog.md`` over ``notes/2025/`` and ``notes/2026/`` is one tree;
    ``docs/guide.md`` beside ``docs/notes/`` leaves ``docs/notes`` alone.
    """
    # Ignore only ever subtracts: the fingerprint runs on every doc, so removing
    # ignored docs can never tip a parent directory over a threshold. What a
    # fingerprint tree keeps must still be a working-notes tree on its own, or
    # the rest of it (an index whose notes were ignored) returns to the headline.
    kept_docs = {
        r for r in doc_signals if not any(_is_ancestor_path(d, r) for d in ignore)
    }
    trees = {}
    for d, docs in _fingerprint_trees(doc_signals).items():
        left = sorted(set(docs) & kept_docs)
        if len(left) >= WORKING_NOTES_MIN_FILES and _is_working_notes(left, doc_signals):
            trees[d] = set(left)
    for d, fragments in _fragment_trees(doc_signals).items():
        trees[d] = trees.get(d, set()) | (set(fragments) & kept_docs)
    for d in force:
        trees[d] = trees.get(d, set()) | {r for r in kept_docs if _is_ancestor_path(d, r)}
    trees = {d: docs for d, docs in trees.items() if docs}
    kept = [d for d in trees if not any(o != d and _is_ancestor_path(o, d) for o in trees)]
    out: list[SourceTree] = []
    for d in sorted(kept):
        docs = sorted(set().union(*(t for o, t in trees.items() if _is_ancestor_path(d, o))))
        out.append({"path": d, "file_count": len(docs), "docs": docs})
    return out


def _fingerprint_trees(doc_signals: dict[str, DocSignal]) -> dict[str, list[str]]:
    """The directories the fingerprint alone classifies, each mapped to the
    docs it takes out of the headline. Nested trees are all returned; the
    caller keeps the outermost."""
    by_dir: dict[str, list[str]] = {}
    for rel in doc_signals:
        for d in _ancestor_dirs(rel):
            by_dir.setdefault(d, []).append(rel)

    qualifying = {
        d: docs for d, docs in by_dir.items()
        if len(docs) >= WORKING_NOTES_MIN_FILES and _is_working_notes(docs, doc_signals)
    }
    trees: dict[str, list[str]] = {}
    for d in sorted(qualifying, key=lambda x: -x.count("/")):  # deepest first
        tree = _tree_docs(d, qualifying[d], doc_signals, trees)
        if tree is not None:
            trees[d] = tree
    return trees
