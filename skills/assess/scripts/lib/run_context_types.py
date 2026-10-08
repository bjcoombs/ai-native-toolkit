"""TypedDict shapes for the run-context.json rows the lib modules build.

Each shape here is a record a ``lib/`` module constructs and another module, or
the orchestrator, reads back: a dead-code candidate, a broken doc link, a gap
action. Typing them turns the shape from a docstring comment into a contract
mypy checks at every construction site. A shape used by one module only stays
in that module; it moves here once a second module reads it.

These are annotations only. A TypedDict is a plain ``dict`` at runtime, so the
serialised run-context is byte-identical to the untyped version.

Consumers outside the ``disallow_any_generics`` ratchet (see ``[tool.mypy]`` in
``pyproject.toml``) still read some of these blocks as bare ``dict``; they move
to these types when their module joins the ratchet.
"""
from __future__ import annotations

from typing import Any, NotRequired, TypedDict

# A run-context block read back from JSON (or about to be written to it) whose
# keys vary with the run: config sections, another module's block consumed
# with ``.get``. ``dict[str, Any]`` names the dynamism instead of hiding it in
# a bare ``dict``.
JsonDict = dict[str, Any]


# --- liveness_scan: the dead_code block -----------------------------------

class DeadCodeCandidate(TypedDict):
    """One symbol a dead-code tool reports unused."""

    path: str
    line: int
    kind: str
    # knip reports an export as a name or an object; the object's ``name`` is
    # taken when present, otherwise the raw value passes through.
    symbol: Any


class DeadCodeTool(TypedDict):
    """One per-language tool status row in ``dead_code.tools``."""

    language: str
    # Dart's entry carries the capability's ``candidate_tool``, read with
    # ``.get`` from a JSON-shaped block, so it may be absent (None).
    tool: str | None
    status: str
    reason: str | None
    # Only the JVM ``offer`` row records the consent gate it waits on.
    consent: NotRequired[Any]


class DeadCodeBlock(TypedDict):
    """``run-context.json`` ``liveness.dead_code``."""

    available: bool
    candidate_count: int
    # Maven liveness folds its unused-declared-dependency rows in here with the
    # same shape (``path`` is the pom, ``symbol`` the coordinate).
    candidates: list[DeadCodeCandidate]
    tools: list[DeadCodeTool]
    caveat: str


# --- doc_links / doc_graph: doc-graph rows ---------------------------------

# ``from`` is a keyword, so the shapes that carry it use the functional form.
BrokenLink = TypedDict("BrokenLink", {"from": str, "target": str, "kind": str})
MissingXref = TypedDict("MissingXref", {"from": str, "to": str})


class DocToCodeEdge(TypedDict):
    doc: str
    code: str


class DocHub(TypedDict):
    path: str
    pagerank: float
    out_degree: int
    in_degree: int


class DeclaredMoc(TypedDict):
    path: str
    out_degree: int
    is_structural_hub: bool


class LinkParent(TypedDict):
    path: str
    link_parent: str | None
    link_entry: str | None


class DirectoryBreakdownRow(TypedDict):
    path: str
    doc_count: int
    unreachable_count: int
    broken_link_count: int


class ExcludedTree(TypedDict):
    """An excluded raw-source or working-notes tree as the doc graph reports it."""

    path: str
    file_count: int


class SourceTree(TypedDict):
    """A detected raw-source or working-notes tree with the docs it removes."""

    path: str
    file_count: int
    docs: list[str]


class DocSignal(TypedDict, total=False):
    """Per-doc graph signals the raw-source and working-notes classifiers read.

    The raw pass fills the degrees and ``machine_links``; the working-notes
    pass fills ``in_degree`` and ``inbound_sources``.
    """

    in_degree: int
    out_degree: int
    machine_links: int
    inbound_sources: list[str]


class DocGraphBlock(TypedDict):
    """``run-context.json`` ``doc_graph``: :meth:`lib.doc_graph.DocGraphResult.as_dict`."""

    available: bool
    reason: str
    doc_count: int
    edge_count: int
    hubs: list[DocHub]
    orphans: list[str]
    orphan_rate: float
    island_count: int
    reachability_pct: float
    entry_points: list[str]
    unreachable: list[str]
    declared_mocs: list[DeclaredMoc]
    moc_named_but_not_wired: list[str]
    doc_to_code_edges: list[DocToCodeEdge]
    dangling_links: int
    broken_links: list[BrokenLink]
    missing_xrefs: list[MissingXref]
    ambiguous_wikilinks: int
    vault_detected: bool
    obsidiantools_available: bool
    excluded_raw_trees: list[ExcludedTree]
    raw_source_doc_count: int
    curated_doc_count: int
    raw_source_orphan_rate: float
    raw_source_broken_links: int
    excluded_working_notes_trees: list[ExcludedTree]
    working_notes_doc_count: int
    working_notes_orphan_rate: float
    working_notes_broken_links: int
    link_only_orphan_rate: float
    link_only_reachability_pct: float
    directory_breakdown: list[DirectoryBreakdownRow]
    directory_count: int
    link_parents: list[LinkParent]


# --- gap_actions ------------------------------------------------------------

class GapAction(TypedDict):
    """A Top 3 candidate read from a measured gap: ``{signal, action, paths}``."""

    signal: str
    action: str
    paths: list[str]


# --- git_churn / doc_staleness ---------------------------------------------

class BulkCommit(TypedDict):
    """A bulk mechanical commit the content clock skipped."""

    sha: str
    date: str
    docs_touched: int
    doc_count: int
