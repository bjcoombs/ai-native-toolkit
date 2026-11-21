"""Static dependency-structure analysis for the keyhole-readiness signals.

The keyhole is the binding constraint once a codebase outgrows a single
context window: every actor -- the agent's window and the human reviewing a
diff -- sees a narrow slice by construction. A change is safe only when the
*unit being changed plus the contracts at its boundary* fit inside that slice.
This module measures the static, language-specific (Python-first) half of that
question off the import graph:

  - **A1 comprehension footprint** -> for a unit X, ``size(X) +
    public_surface(direct deps of X) + surface X exposes to dependents``.
    DIRECT dependencies only -- transitive closure would explode the metric
    for anything depending on common utilities and flag the whole repo. A unit
    whose footprint exceeds the **keyhole budget** is one no agent can change
    completely from inside the window.
  - **A2 blob vs modular** -> strongly-connected components (a cycle of length
    > 1 is a definitional blob) plus a Newman modularity score *Q*. High Q =
    cohesive clusters with sparse cross-talk; low / negative Q = either a blob
    (everything coupled) or confetti (a hundred tiny packages cross-talking).
  - **A3 contracts** -> the fraction of cross-package inbound edges that land
    on a package's *front door* (its ``__init__`` / public API) versus
    **burrow** into internals. Deep-reaching imports mean there is no real
    contract and refactors leak.
  - **A4 breakup candidates** -> a package whose internal modules fall into
    well-separated sub-clusters is several packages wearing one coat; the
    sub-clusters *are* the proposed cut-lines.

The graph comes from a language builder (``lib.import_graph``): each
registered builder (``lib.import_graph_builders``) returns one language's
:class:`~lib.import_graph.ModuleGraph` -- modules, edges, front doors, module
files and package boundaries -- and the largest is measured, its language in the
block's ``language`` field. Nothing here assumes a language: a package boundary,
a front door and a public surface are whatever the builder says they are. With no
graph the reason names the repository's dominant language
(``no_import_graph_<language>``), ``no importable Python packages found`` for a
Python repository, or ``no_source_files``. The Python builder
(``lib.import_graph_python``) reads packages with ``grimp``.

``networkx`` (community detection / SCCs) is the core dependency. Mirroring
``doc_graph``, the module degrades to an ``available=False`` result rather than
crashing when it or a builder's tool is missing -- the assessment never blocks.
The analysis is purely static (no code execution) and deterministic, so it is
reproducible run to run.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict

try:  # networkx is the core dep; degrade rather than crash if absent.
    import networkx as nx

    _NETWORKX_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only on a broken env
    nx = None  # type: ignore[assignment, unused-ignore]  # stubs absent: nx is Any; installed: None mismatches the module
    _NETWORKX_AVAILABLE = False

from lib.assess_config import DEFAULT_KEYHOLE_BUDGET
from lib.import_graph import (
    EXCLUDE_DIRS,
    ImportGraphBuilder,
    ModuleGraph,
    NameCollision,
    select_graph,
)
from lib.import_graph_builders import BUILDERS

__all__ = ["EXCLUDE_DIRS", "NameCollision"]

# Below this many internal modules a package is too small to be worth proposing
# a split for -- two or three modules are a unit, not a hidden multi-package.
MIN_PACKAGE_MODULES_FOR_BREAKUP = 4

# A package's internal community split only counts as a real seam (a breakup
# candidate) when the sub-clusters are this well separated. Below it the
# package is cohesive and the "clusters" are an artefact of sparse edges.
BREAKUP_MODULARITY_THRESHOLD = 0.25

# networkx's greedy_modularity_communities is good for small graphs; louvain
# scales better. Switch over at this node count (matches the PRD guidance).
GREEDY_MAX_NODES = 500

# modularity_reason when the module graph has no import edge: Q is undefined,
# so modularity_q is null rather than a 0.0 that reads as tangled (issue #682).
NO_IMPORT_EDGES = "no_import_edges"

# Caps so a pathological repo can't bloat run-context.json.
MAX_FOOTPRINTS = 200
MAX_BURROW_EDGES = 100
MAX_SCCS = 50


class Footprint(TypedDict):
    """A1: a module's comprehension footprint against the keyhole budget."""

    module: str
    size: int
    dep_surface: int
    exposed_surface: int
    total: int
    over_budget: bool


class BurrowEdge(TypedDict):
    """A3: an import that reaches into another package's internals."""

    importer: str
    imported: str


class BreakupCandidate(TypedDict):
    """A4: a package whose modules split into well-separated clusters."""

    package: str
    clusters: list[list[str]]
    num_clusters: int
    modularity_q: float


class StructureBlock(TypedDict):
    """``run-context.json`` ``structure``: :meth:`StructureGraphResult.as_dict`."""

    available: bool
    reason: str
    language: str | None
    keyhole_budget: int
    footprints: list[Footprint]
    sccs: list[list[str]]
    modularity_q: float | None
    modularity_reason: str | None
    front_door_ratio: float | None
    cross_package_edges: int
    internal_burrow_edges: list[BurrowEdge]
    breakup_candidates: list[BreakupCandidate]
    module_count: int
    edge_count: int
    name_collisions: list[NameCollision]


@dataclass
class StructureGraphResult:
    available: bool = True
    reason: str = ""
    # The measured graph's language (the dominant language's in a polyglot repo);
    # None when no graph was measured.
    language: str | None = None
    keyhole_budget: int = DEFAULT_KEYHOLE_BUDGET
    # A1: one footprint per module, largest total first.
    footprints: list[Footprint] = field(default_factory=list)
    # A2: strongly-connected components of length > 1 (import cycles = blobs).
    sccs: list[list[str]] = field(default_factory=list)
    # A2: Newman modularity Q in [-0.5, 1] over the module graph. None when
    # nothing was measured, with the code why in modularity_reason (#682).
    modularity_q: float | None = None
    modularity_reason: str | None = None
    # A3: fraction of cross-package inbound edges landing on a front door.
    # None when no package boundary is crossed (cross_package_edges 0): there
    # is no contract edge to measure, so the ratio is unknown, not 1.0 (#760).
    front_door_ratio: float | None = None
    # A3: the edges front_door_ratio is measured over.
    cross_package_edges: int = 0
    # A3: the burrowing edges (imports that reach into another package's
    # internals instead of its public API).
    internal_burrow_edges: list[BurrowEdge] = field(default_factory=list)
    # A4: packages proposed for splitting, with their clusters as cut-lines.
    breakup_candidates: list[BreakupCandidate] = field(default_factory=list)
    module_count: int = 0
    edge_count: int = 0
    # Package names held by more than one discovered package; each copy is
    # graphed under its repo-relative path instead of one silently winning.
    name_collisions: list[NameCollision] = field(default_factory=list)

    def as_dict(self) -> StructureBlock:
        return {
            "available": self.available,
            "reason": self.reason,
            "language": self.language,
            "keyhole_budget": self.keyhole_budget,
            "footprints": self.footprints,
            "sccs": self.sccs,
            "modularity_q": None if self.modularity_q is None else round(self.modularity_q, 4),
            "modularity_reason": self.modularity_reason,
            "front_door_ratio": (
                None if self.front_door_ratio is None else round(self.front_door_ratio, 4)),
            "cross_package_edges": self.cross_package_edges,
            "internal_burrow_edges": self.internal_burrow_edges,
            "breakup_candidates": self.breakup_candidates,
            "module_count": self.module_count,
            "edge_count": self.edge_count,
            "name_collisions": self.name_collisions,
        }


# --------------------------------------------------------------------------
# Source-surface measurement (size + public API)
# --------------------------------------------------------------------------

def _count_loc(path: Path | None) -> int:
    """Non-blank source lines in a module file (0 if unreadable / absent)."""
    if path is None:
        return 0
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return 0
    return sum(1 for line in text.splitlines() if line.strip())


# --------------------------------------------------------------------------
# A1 -- comprehension footprint
# --------------------------------------------------------------------------

def compute_footprint(
    module: str,
    direct_deps: list[str],
    surfaces: dict[str, int],
    sizes: dict[str, int],
    keyhole_budget: int,
) -> Footprint:
    """A1 footprint for one module: size + direct-dep surface + own surface.

    ``direct_deps`` is the module's DIRECT imports only -- no transitive
    closure. A depends on B depends on C must NOT pull C's surface into A's
    footprint, or every module transitively touching a common utility would
    blow the budget and the whole repo would be flagged.
    """
    size = sizes.get(module, 0)
    dep_surface = sum(surfaces.get(dep, 0) for dep in direct_deps)
    exposed_surface = surfaces.get(module, 0)
    total = size + dep_surface + exposed_surface
    return {
        "module": module,
        "size": size,
        "dep_surface": dep_surface,
        "exposed_surface": exposed_surface,
        "total": total,
        "over_budget": total > keyhole_budget,
    }


# --------------------------------------------------------------------------
# A2 -- blob vs modular
# --------------------------------------------------------------------------

def _detect_communities(undirected: nx.Graph) -> list[set[str]]:
    """Community partition of an undirected graph (greedy / louvain by size)."""
    if undirected.number_of_nodes() == 0:
        return []
    from networkx.algorithms.community import (
        greedy_modularity_communities,
        louvain_communities,
    )
    if undirected.number_of_edges() == 0:
        # No edges: every node is its own community.
        return [{n} for n in undirected.nodes()]
    if undirected.number_of_nodes() < GREEDY_MAX_NODES:
        return [set(c) for c in greedy_modularity_communities(undirected)]
    # louvain needs a deterministic seed to stay reproducible run to run.
    return [set(c) for c in louvain_communities(undirected, seed=1)]


def _modularity_q(undirected: nx.Graph, communities: list[set[str]]) -> float:
    """Newman Q for a partition, clamped to the theoretical [-0.5, 1] range."""
    if not communities or undirected.number_of_edges() == 0:
        return 0.0
    from networkx.algorithms.community import modularity
    try:
        q = modularity(undirected, communities)
    except (ZeroDivisionError, KeyError):  # pragma: no cover - defensive
        return 0.0
    return max(-0.5, min(1.0, float(q)))


def compute_modularity(graph: nx.DiGraph) -> tuple[list[list[str]], float]:
    """A2: import cycles (SCCs len > 1) and the Newman modularity Q.

    Returns ``(sccs, q)`` where ``sccs`` is the list of strongly-connected
    components of length > 1 (each a definitional blob -- a cycle through which
    a change in any member can reach every other), and ``q`` is the modularity
    of the best community partition of the undirected projection.
    """
    sccs = sorted(
        (sorted(c) for c in nx.strongly_connected_components(graph) if len(c) > 1),
        key=lambda c: (-len(c), c),
    )
    undirected = graph.to_undirected()
    communities = _detect_communities(undirected)
    q = _modularity_q(undirected, communities)
    return sccs, q


# --------------------------------------------------------------------------
# A3 -- contracts (front door vs burrow)
# --------------------------------------------------------------------------

def compute_front_door_ratio(
    graph: nx.DiGraph, front_doors: set[str], package_of: Mapping[str, str],
) -> tuple[float, list[BurrowEdge]]:
    """A3: fraction of cross-package edges landing on a front door.

    Only *cross-package* edges carry a contract -- intra-package edges are
    internal cohesion. ``package_of`` maps a module to its package. An edge
    into another package is a **front door** when its target is one of
    ``front_doors`` (the package's public API, a Python ``__init__``) and a
    **burrow** when it reaches a plain submodule inside that package.

    A vacuous graph (no cross-package edges) returns 1.0 -- no boundary is
    violated -- but that is no evidence of contracts, so ``analyze_structure``
    reports the ratio as None there (issue #760).
    """
    front, burrow = 0, 0
    burrow_edges: list[BurrowEdge] = []
    for importer, imported in graph.edges():
        if package_of[importer] == package_of[imported]:
            continue  # intra-package: not a contract edge
        if imported in front_doors:
            front += 1
        else:
            burrow += 1
            burrow_edges.append({"importer": importer, "imported": imported})
    total = front + burrow
    ratio = 1.0 if total == 0 else front / total
    burrow_edges.sort(key=lambda e: (e["importer"], e["imported"]))
    return ratio, burrow_edges


def count_cross_package_edges(graph: nx.DiGraph, package_of: Mapping[str, str]) -> int:
    """A3: how many import edges cross a package boundary.

    The denominator of ``compute_front_door_ratio``. Zero means that ratio is
    the vacuous 1.0, so the result reports it as None: a reader can tell "every
    boundary import uses the front door" from "no boundary is crossed" (issues
    #663, #760).
    """
    return sum(1 for a, b in graph.edges() if package_of[a] != package_of[b])


# --------------------------------------------------------------------------
# A4 -- breakup candidates
# --------------------------------------------------------------------------

def find_breakup_candidates(
    package: str, internal_graph: nx.DiGraph,
) -> BreakupCandidate | None:
    """A4: propose cut-lines for a package that is several packages in one.

    ``internal_graph`` holds only the package's own modules and the edges
    between them. A package is a breakup candidate when its internal modules
    fall into two or more well-separated communities -- the sub-clusters are
    the proposed cut-lines. Returns ``None`` when the package is too small to
    bother splitting, or when its modules form one cohesive cluster (the
    community split is weak: Q below the threshold).
    """
    if internal_graph.number_of_nodes() < MIN_PACKAGE_MODULES_FOR_BREAKUP:
        return None
    undirected = internal_graph.to_undirected()
    communities = _detect_communities(undirected)
    communities = [c for c in communities if c]
    if len(communities) < 2:
        return None
    q = _modularity_q(undirected, communities)
    if q < BREAKUP_MODULARITY_THRESHOLD:
        return None  # cohesive enough: one coat, one package
    clusters = sorted(
        (sorted(c) for c in communities), key=lambda c: (-len(c), c),
    )
    return {
        "package": package,
        "clusters": clusters,
        "num_clusters": len(clusters),
        "modularity_q": round(q, 4),
    }


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def import_digraph(mg: ModuleGraph) -> nx.DiGraph:
    """The networkx digraph of ``mg``: nodes = modules, edges = direct imports."""
    graph = nx.DiGraph()
    graph.add_nodes_from(mg.modules)
    for m in mg.modules:
        for dep in sorted(mg.edges.get(m, ())):
            if dep in graph:  # ignore imports of external / unknown modules
                graph.add_edge(m, dep)
    return graph


def _all_breakup_candidates(
    graph: nx.DiGraph, mg: ModuleGraph,
) -> list[BreakupCandidate]:
    """A4 over every package, each judged on its own subgraph."""
    breakup: list[BreakupCandidate] = []
    for pkg in mg.packages:
        members = [m for m in mg.modules if mg.package_of[m] == pkg]
        candidate = find_breakup_candidates(pkg, graph.subgraph(members))
        if candidate is not None:
            breakup.append(candidate)
    return breakup


def analyze_structure(
    repo_root: Path,
    keyhole_budget: int | None = None,
    extra_exclude_dirs: set[str] | None = None,
    scope: Path | None = None,
    builders: Sequence[ImportGraphBuilder] = BUILDERS,
) -> StructureGraphResult:
    """Run the A1-A4 static structure analysis over the repo's import graph.

    Every builder in ``builders`` graphs its language; the graph
    ``import_graph.select_graph`` picks (the dominant language's) is measured and
    its language reported. Degrades gracefully
    (``available=False``) when networkx is missing, when a builder's tool is
    missing or fails and no other graph was built, or when nothing could be
    graphed (the reason names the dominant language). The result is
    JSON-serialisable via ``as_dict()`` and deterministic for a given source tree.

    ``scope`` (an absolute path under ``repo_root``) confines the analysis to
    modules within a subtree for ``/assess <path>`` monorepo scoping, so a
    scoped run carries no structure signal from a sibling directory. Omit it for
    a whole-repo run.
    """
    repo_root = Path(repo_root).resolve()
    budget = keyhole_budget if keyhole_budget is not None else DEFAULT_KEYHOLE_BUDGET

    if not _NETWORKX_AVAILABLE:
        return StructureGraphResult(
            available=False,
            reason="networkx not installed; static structure not assessed",
            keyhole_budget=budget,
        )

    selection = select_graph(repo_root, builders, extra_exclude_dirs, scope)
    mg = selection.graph
    if mg is None:
        # Nothing measured, not a measured empty graph: modularity 0.0 would
        # read as tangled code in a maturity model (issue #663).
        return StructureGraphResult(
            available=False, reason=selection.reason, keyhole_budget=budget,
        )

    modules = mg.modules
    sizes = {m: _count_loc(mg.module_file.get(m)) for m in modules}
    graph = import_digraph(mg)

    # A1 footprints (direct deps only).
    footprints = [
        compute_footprint(
            m, sorted(graph.successors(m)), mg.surfaces, sizes, budget,
        )
        for m in modules
    ]
    footprints.sort(key=lambda fp: (-fp["total"], fp["module"]))

    # A2 SCCs + modularity. With no import edge there is no community structure
    # to measure: Q would read 0.0, tangled, from nothing (issue #682).
    sccs, q = compute_modularity(graph)
    edgeless = graph.number_of_edges() == 0

    # A3 front-door ratio. With no cross-package edge it is unknown (#760).
    front_door_ratio, burrow_edges = compute_front_door_ratio(
        graph, mg.front_doors, mg.package_of,
    )
    cross_package_edges = count_cross_package_edges(graph, mg.package_of)

    return StructureGraphResult(
        available=True,
        language=mg.language,
        keyhole_budget=budget,
        footprints=footprints[:MAX_FOOTPRINTS],
        sccs=sccs[:MAX_SCCS],
        modularity_q=None if edgeless else q,
        modularity_reason=NO_IMPORT_EDGES if edgeless else None,
        front_door_ratio=front_door_ratio if cross_package_edges else None,
        cross_package_edges=cross_package_edges,
        internal_burrow_edges=burrow_edges[:MAX_BURROW_EDGES],
        breakup_candidates=_all_breakup_candidates(graph, mg),
        module_count=graph.number_of_nodes(),
        edge_count=graph.number_of_edges(),
        name_collisions=mg.name_collisions,
    )
