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

Core dependencies are ``grimp`` (Python import-graph) and ``networkx``
(community detection / SCCs). Mirroring ``doc_graph``, the module degrades to
an ``available=False`` result rather than crashing when either is missing --
the assessment never blocks. The analysis is purely static (AST-level import
parsing via grimp; no code execution) and deterministic, so it is reproducible
run to run.
"""
from __future__ import annotations

import ast
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

try:  # grimp + networkx are the core deps; degrade rather than crash if absent.
    import grimp

    _GRIMP_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only on a broken env
    grimp = None  # type: ignore[assignment]
    _GRIMP_AVAILABLE = False

try:
    import networkx as nx

    _NETWORKX_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only on a broken env
    nx = None  # type: ignore[assignment]
    _NETWORKX_AVAILABLE = False

from lib.assess_config import DEFAULT_KEYHOLE_BUDGET

# Directories never worth walking for packages. Mirrors doc_graph's EXCLUDE_DIRS
# (kept local so this module pulls in no heavy deps) -- build artefacts, vendor
# trees, virtualenvs and /assess's own output are not the repo's source.
EXCLUDE_DIRS = {
    ".git", "node_modules", "dist", "build", "target", "vendor",
    ".venv", "venv", "__pycache__", ".gradle", ".idea", ".mvn",
    "worktree", ".understand-anything", ".obsidian", ".taskmaster",
    ".claude", ".next", ".nuxt", ".output", ".svelte-kit", ".astro",
    "out", "coverage", "htmlcov", "Pods", "DerivedData", "flutter_assets",
    ".assess", "tests", "test",
}

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

# Caps so a pathological repo can't bloat run-context.json.
MAX_FOOTPRINTS = 200
MAX_BURROW_EDGES = 100
MAX_SCCS = 50


@dataclass
class StructureGraphResult:
    available: bool = True
    reason: str = ""
    keyhole_budget: int = DEFAULT_KEYHOLE_BUDGET
    # A1: [{module, size, dep_surface, exposed_surface, total, over_budget}]
    footprints: list[dict] = field(default_factory=list)
    # A2: strongly-connected components of length > 1 (import cycles = blobs).
    sccs: list[list[str]] = field(default_factory=list)
    # A2: Newman modularity Q in [-0.5, 1] over the module graph.
    modularity_q: float = 0.0
    # A3: fraction of cross-package inbound edges landing on a front door.
    front_door_ratio: float = 1.0
    # A3: the burrowing edges (imports that reach into another package's
    # internals instead of its public API). [{importer, imported}].
    internal_burrow_edges: list[dict] = field(default_factory=list)
    # A4: [{package, clusters: [[mod, ...], ...], num_clusters, modularity_q}].
    breakup_candidates: list[dict] = field(default_factory=list)
    module_count: int = 0
    edge_count: int = 0

    def as_dict(self) -> dict:
        return {
            "available": self.available,
            "reason": self.reason,
            "keyhole_budget": self.keyhole_budget,
            "footprints": self.footprints,
            "sccs": self.sccs,
            "modularity_q": round(self.modularity_q, 4),
            "front_door_ratio": round(self.front_door_ratio, 4),
            "internal_burrow_edges": self.internal_burrow_edges,
            "breakup_candidates": self.breakup_candidates,
            "module_count": self.module_count,
            "edge_count": self.edge_count,
        }


# --------------------------------------------------------------------------
# Package discovery + grimp graph construction
# --------------------------------------------------------------------------

def _is_excluded(path: Path, repo_root: Path, extra: set[str]) -> bool:
    try:
        rel = path.relative_to(repo_root)
    except ValueError:
        return True
    return any(part in EXCLUDE_DIRS or part in extra for part in rel.parts)


def discover_packages(
    repo_root: Path, extra_exclude_dirs: set[str] | None = None,
) -> list[Path]:
    """Return the top-level importable package directories under repo_root.

    A package directory contains an ``__init__.py``. We keep only *top-level*
    packages -- a directory whose parent is not itself a package -- because
    grimp is given the package root and walks down from there. ``repo_root``
    itself counts if it is a package (the integration case: pointing the
    analysis straight at ``scripts/lib``).
    """
    repo_root = repo_root.resolve()
    extra = extra_exclude_dirs or set()
    init_dirs: set[Path] = set()
    # repo_root itself may be a package.
    if (repo_root / "__init__.py").is_file():
        init_dirs.add(repo_root)
    for init in repo_root.rglob("__init__.py"):
        if not init.is_file():
            continue
        if _is_excluded(init.parent, repo_root, extra):
            continue
        init_dirs.add(init.parent.resolve())
    # Keep only roots: a package whose parent is also a package is a subpackage.
    return sorted(d for d in init_dirs if d.parent not in init_dirs)


@contextmanager
def _syspath_prepended(paths: list[Path]):
    """Temporarily prepend `paths` to sys.path, restoring it afterwards.

    grimp locates a package by importable name via sys.path; we add each
    package's parent so ``grimp.build_graph("lib")`` resolves. Restored in a
    finally so a scan never leaves the interpreter's import state mutated.
    """
    added = [str(p) for p in paths]
    original = list(sys.path)
    for p in added:
        if p not in sys.path:
            sys.path.insert(0, p)
    try:
        yield
    finally:
        sys.path[:] = original


def _module_file(module: str, roots: dict[str, Path]) -> Path | None:
    """Resolve a dotted module name to its source file via its package root.

    ``roots`` maps a top-level package name to its directory; the module's
    file lives under that directory's *parent* (the sys.path root), since the
    dotted name already includes the package as its first component.
    """
    top = module.split(".", 1)[0]
    root = roots.get(top)
    if root is None:
        return None
    base = root.parent
    rel = module.replace(".", "/")
    candidate = base / f"{rel}.py"
    if candidate.is_file():
        return candidate
    pkg_init = base / rel / "__init__.py"
    if pkg_init.is_file():
        return pkg_init
    return None


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


def _public_surface(path: Path | None) -> int:
    """Count public top-level definitions (the API a module exposes).

    Public = a module-level ``def`` / ``async def`` / ``class`` whose name does
    not start with ``_``. This is the surface a dependent must comprehend to
    use the module -- the contract, not the implementation.
    """
    if path is None:
        return 0
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, SyntaxError):
        return 0
    count = 0
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                count += 1
    return count


# --------------------------------------------------------------------------
# A1 -- comprehension footprint
# --------------------------------------------------------------------------

def compute_footprint(
    module: str,
    direct_deps: list[str],
    surfaces: dict[str, int],
    sizes: dict[str, int],
    keyhole_budget: int,
) -> dict:
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

def _detect_communities(undirected) -> list[set]:
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


def _modularity_q(undirected, communities: list[set]) -> float:
    """Newman Q for a partition, clamped to the theoretical [-0.5, 1] range."""
    if not communities or undirected.number_of_edges() == 0:
        return 0.0
    from networkx.algorithms.community import modularity
    try:
        q = modularity(undirected, communities)
    except (ZeroDivisionError, KeyError):  # pragma: no cover - defensive
        return 0.0
    return max(-0.5, min(1.0, q))


def compute_modularity(graph) -> tuple[list[list[str]], float]:
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

def _top_package(module: str) -> str:
    return module.split(".", 1)[0]


def compute_front_door_ratio(
    graph, packages: set[str],
) -> tuple[float, list[dict]]:
    """A3: fraction of cross-package edges landing on a front door.

    Only *cross-package* edges carry a contract -- intra-package edges are
    internal cohesion. An edge into another package is a **front door** when
    its target is a package module itself (importing the ``__init__`` / public
    API) and a **burrow** when it reaches a plain submodule inside that package.

    A vacuous graph (no cross-package edges) is treated as fully contracted
    (ratio 1.0) -- there are no boundaries being violated.
    """
    front, burrow = 0, 0
    burrow_edges: list[dict] = []
    for importer, imported in graph.edges():
        if _top_package(importer) == _top_package(imported):
            continue  # intra-package: not a contract edge
        if imported in packages:
            front += 1
        else:
            burrow += 1
            burrow_edges.append({"importer": importer, "imported": imported})
    total = front + burrow
    ratio = 1.0 if total == 0 else front / total
    burrow_edges.sort(key=lambda e: (e["importer"], e["imported"]))
    return ratio, burrow_edges


# --------------------------------------------------------------------------
# A4 -- breakup candidates
# --------------------------------------------------------------------------

def find_breakup_candidates(
    package: str, internal_graph,
) -> dict | None:
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

def _build_grimp_graph(package_dirs: list[Path]):
    """Build the grimp import graph for the discovered packages.

    Returns ``(import_graph, package_names, roots)`` or raises on failure.
    ``roots`` maps each top-level package name to its source directory, used to
    resolve module names back to files for surface measurement.
    """
    names = [d.name for d in package_dirs]
    roots = {d.name: d for d in package_dirs}
    parents = list({d.parent for d in package_dirs})
    with _syspath_prepended(parents):
        # cache_dir=None: never write grimp's cache into the target repo.
        import_graph = grimp.build_graph(*names, cache_dir=None)
    return import_graph, names, roots


def analyze_structure(
    repo_root: Path,
    keyhole_budget: int | None = None,
    extra_exclude_dirs: set[str] | None = None,
    scope: Path | None = None,
) -> StructureGraphResult:
    """Run the A1-A4 static structure analysis over the repo's Python packages.

    Degrades gracefully (``available=False``) when grimp or networkx is missing
    or no importable Python package is found. The result is JSON-serialisable
    via ``as_dict()`` and deterministic for a given source tree.

    ``scope`` (an absolute path under ``repo_root``) confines the analysis to
    packages within a subtree for ``/assess <path>`` monorepo scoping, so a
    scoped run carries no structure signal from a sibling directory. Omit it for
    a whole-repo run.
    """
    repo_root = Path(repo_root).resolve()
    budget = keyhole_budget if keyhole_budget is not None else DEFAULT_KEYHOLE_BUDGET

    if not _GRIMP_AVAILABLE or not _NETWORKX_AVAILABLE:
        missing = "grimp" if not _GRIMP_AVAILABLE else "networkx"
        return StructureGraphResult(
            available=False,
            reason=f"{missing} not installed; static structure not assessed",
            keyhole_budget=budget,
        )

    package_dirs = discover_packages(repo_root, extra_exclude_dirs)
    if scope is not None:
        scope_abs = scope.resolve()
        package_dirs = [
            p for p in package_dirs if p.resolve().is_relative_to(scope_abs)
        ]
    if not package_dirs:
        return StructureGraphResult(
            available=True,
            reason="no importable Python packages found",
            keyhole_budget=budget,
        )

    try:
        import_graph, package_names, roots = _build_grimp_graph(package_dirs)
    except Exception as e:  # pragma: no cover - grimp parse failure on odd trees
        return StructureGraphResult(
            available=False,
            reason=f"grimp failed to build import graph ({e})",
            keyhole_budget=budget,
        )

    modules = sorted(import_graph.modules)
    package_set = set(package_names) | {
        m for m in modules
        if (mf := _module_file(m, roots)) is not None and mf.name == "__init__.py"
    }

    # Measure each module's size and public surface once.
    sizes: dict[str, int] = {}
    surfaces: dict[str, int] = {}
    for m in modules:
        f = _module_file(m, roots)
        sizes[m] = _count_loc(f)
        surfaces[m] = _public_surface(f)

    # Build the networkx digraph (nodes = modules, edges = direct imports).
    graph = nx.DiGraph()
    graph.add_nodes_from(modules)
    for m in modules:
        for dep in import_graph.find_modules_directly_imported_by(m):
            if dep in graph:  # ignore imports of external / unknown modules
                graph.add_edge(m, dep)

    # A1 footprints (direct deps only).
    footprints = [
        compute_footprint(
            m, sorted(graph.successors(m)), surfaces, sizes, budget,
        )
        for m in modules
    ]
    footprints.sort(key=lambda fp: (-fp["total"], fp["module"]))

    # A2 SCCs + modularity.
    sccs, q = compute_modularity(graph)

    # A3 front-door ratio.
    front_door_ratio, burrow_edges = compute_front_door_ratio(graph, package_set)

    # A4 breakup candidates -- one analysis per top-level package.
    breakup: list[dict] = []
    for pkg in sorted(package_names):
        members = [
            m for m in modules if m == pkg or m.startswith(pkg + ".")
        ]
        internal = graph.subgraph(members)
        candidate = find_breakup_candidates(pkg, internal)
        if candidate is not None:
            breakup.append(candidate)

    return StructureGraphResult(
        available=True,
        keyhole_budget=budget,
        footprints=footprints[:MAX_FOOTPRINTS],
        sccs=sccs[:MAX_SCCS],
        modularity_q=q,
        front_door_ratio=front_door_ratio,
        internal_burrow_edges=burrow_edges[:MAX_BURROW_EDGES],
        breakup_candidates=breakup,
        module_count=graph.number_of_nodes(),
        edge_count=graph.number_of_edges(),
    )
