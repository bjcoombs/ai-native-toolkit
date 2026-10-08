"""TypedDict shapes for the run-context.json rows the lib modules build.

A shape lives here when a second ``lib/`` module builds or reads it (a broken
doc link, a doc-to-code edge, a dead-code candidate, a bulk commit), or when it
belongs to the same run-context block as one that does: the ``doc_graph`` and
``dead_code`` blocks keep every row shape together here, so a block is read in
one place. Any other shape, including a block read back only by the
orchestrator, stays in the module that builds it. Typing them turns the shape
from a docstring comment into a contract mypy checks at every construction
site.

These are annotations only. A TypedDict is a plain ``dict`` at runtime, so the
serialised run-context is byte-identical to the untyped version.

Consumers outside the ``disallow_any_generics`` ratchet (see ``[tool.mypy]`` in
``pyproject.toml``) still read some of these blocks as bare ``dict``; they move
to these types when their module joins the ratchet.
"""
from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

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


# --- git_churn / doc_staleness ---------------------------------------------

class BulkCommit(TypedDict):
    """A bulk mechanical commit the content clock skipped."""

    sha: str
    date: str
    docs_touched: int
    doc_count: int


# --- test_pressure: the Layer-1 write-side block ---------------------------
#
# Built by the ``test_pressure`` package (``aggregate``, ``mutation``,
# ``mutmut3``, ``heuristics``) and read by ``mutation_cap``,
# ``untrusted_hotspot`` and ``keyhole_signals``, so the whole block lives here.

class MutationFileResult(TypedDict):
    """One file's mutation figures, the row of ``test_pressure.per_file``."""

    file: str
    # The mutmut 2 survivor-only listing cannot see killed mutants, so killed
    # and total are None there; every other parser counts them.
    killed: int | None
    survived: int
    total: int | None


class SurvivorDensity(TypedDict):
    """``test_pressure.survivor_density``."""

    overall: float | None
    total_survived: int
    total_mutants: int | None
    by_file: dict[str, int]


class SurvivorCluster(TypedDict):
    """A file whose survivor count clears the cluster threshold."""

    file: str
    survived: int


class MutationGroupRecord(TypedDict):
    """A mutmut 3 package run before ``mutation_run`` is decided.

    The optional keys are filled as the run reaches them, so a record spreads
    into a finished :class:`MutationGroup` without dropping any of them.
    """

    root: str
    # "repo" (the package's own config) or "generated".
    config: str
    scope: list[str]
    # "venv", "uv" or "path"; absent when the run stopped before resolving it.
    runner: NotRequired[str]
    # Focus files the run produced no figures for.
    unmeasured: NotRequired[list[str]]
    reason: NotRequired[str]
    # Present (True) only on a run stopped at the budget with saved verdicts.
    partial: NotRequired[bool]


class MutationGroup(MutationGroupRecord):
    """One entry of ``test_pressure.mutation_groups``."""

    mutation_run: bool


class MutationConfig(TypedDict):
    """``detect_mutation_config``: mutation-testing setup the repo carries."""

    present: bool
    tools: list[str]
    ci_integrated: bool


class MutationRunResult(TypedDict):
    """``run_bounded_mutation``: the mutation tier's result before aggregation.

    Keys beyond the first two appear only on the paths that reach them; the
    mutmut 3 pass adds ``groups``.
    """

    mutation_run: bool
    available: bool
    tool: NotRequired[str]
    scope: NotRequired[list[str]]
    per_file: NotRequired[list[MutationFileResult]]
    reason: NotRequired[str]
    groups: NotRequired[list[MutationGroup]]


class AssertionOnInternalFinding(TypedDict):
    test_file: str
    subject_function: str
    internal_field: str
    confidence: str


class UntestedBoundaryFinding(TypedDict):
    file: str
    line: int
    operator: str
    covered: bool
    boundary_tested: bool


class DuplicateTruthFinding(TypedDict):
    file: str
    field_name: str
    derives_from: str
    confidence: str


class CheapHeuristics(TypedDict):
    """``test_pressure.cheap_heuristics``."""

    assertion_on_internal: list[AssertionOnInternalFinding]
    untested_boundaries: list[UntestedBoundaryFinding]
    duplicate_truth: list[DuplicateTruthFinding]
    # The unavailable marker (``mutation_cap.normalize_test_pressure``)
    # carries the three buckets only.
    confidence_note: NotRequired[str]


class TestPressureBlock(TypedDict):
    """``run-context.json`` ``test_pressure`` from a scan that ran."""

    mutation_config_present: bool
    mutation_tools_detected: list[str]
    ci_integrated: bool
    mutation_run: bool
    mutation_scope: list[str]
    per_file: list[MutationFileResult]
    survivor_density: SurvivorDensity
    survivor_clusters: list[SurvivorCluster]
    gap_signal: str
    cheap_heuristics: CheapHeuristics
    # Why the mutation pass did not run, when it did not.
    mutation_note: NotRequired[str]
    # One record per package root the mutmut 3 pass ran.
    mutation_groups: NotRequired[list[MutationGroup]]


class TestPressureUnavailable(TypedDict):
    """``test_pressure`` when the scan failed: "not assessed", never a negative."""

    available: Literal[False]
    # Whatever the failed scan reported, or a fixed message.
    reason: object
    mutation_config_present: None
    cheap_heuristics: CheapHeuristics
