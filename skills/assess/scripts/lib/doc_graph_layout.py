"""Render-side helpers for the doc connectivity graph.

Pure functions over the graph and result that ``lib.doc_graph.build_doc_graph``
returns, used by ``doc-graph-svg.py`` to place and label nodes: the concentric
shells of the radial layout (link-distance from the entry points), each node's
navigability class, and the grouping of broken links into one ghost per missing
file. No layout maths and no plotting here -- graph traversal and bookkeeping
only -- so all of it is unit-testable without numpy or matplotlib.

Inward-only: stdlib only, no ``lib`` import.
"""
from __future__ import annotations

import posixpath


def radial_shells(graph, entries, ring: int = 24) -> list[list[str]]:
    """Order nodes into concentric shells by link-distance from the entry points.

    Shell 0 = the entry points; shell k = docs k hops away (following links);
    then the unreachable docs, chunked into progressively larger outer rings.
    Pure graph traversal - no layout - so it's unit-testable without numpy.
    """
    dist: dict[str, int] = {e: 0 for e in entries if e in graph}
    frontier = list(dist)
    while frontier:
        nxt = []
        for u in frontier:
            for v in graph.successors(u):
                if v not in dist:
                    dist[v] = dist[u] + 1
                    nxt.append(v)
        frontier = nxt
    all_nodes = list(graph.nodes())
    max_d = max(dist.values(), default=0)
    shells = [sorted(n for n in all_nodes if dist.get(n) == d) for d in range(max_d + 1)]
    unreachable = sorted(n for n in all_nodes if n not in dist)
    i, cap = 0, ring
    while i < len(unreachable):
        shells.append(unreachable[i:i + cap])
        i += cap
        cap += 12
    return [s for s in shells if s]


def classify_node(node: str, entries: set, unreachable: set, orphans: set) -> str:
    """Navigability status of a node: entry / reachable / orphan / island."""
    if node in entries:
        return "entry"
    if node not in unreachable:
        return "reachable"
    if node in orphans:
        return "orphan"
    return "island"


def _broken_link_key(src: str, target: str, kind: str | None) -> str:
    """Canonical grouping key for a broken link's missing target.

    Mirrors ``lib.doc_links._resolve_mdlink``'s path arithmetic so links that
    point at the same absent file share a key whatever way they're spelt:

    - A markdown link starting ``/`` is root-absolute - resolved from the repo
      root (``/CLAUDE.md`` -> ``CLAUDE.md``), matching ``lib.doc_links._resolve_mdlink``'s
      ``repo_root / target.lstrip("/")`` branch. Without this, ``/CLAUDE.md`` and
      ``CLAUDE.md`` would key apart and the duplicate ghost this function exists
      to kill would survive for the root-absolute spelling.
    - Any other markdown link resolves relative to the source file's directory
      (``../CLAUDE.md`` from a subdir collapses onto the root ``CLAUDE.md``).
    - A wikilink resolves by note name globally, so it keys on the bare name.

    Known limit (intentional, not fixed): wikilinks and markdown links live in
    different resolution domains, so ``[[CLAUDE]]`` (key ``CLAUDE``) and
    ``[x](CLAUDE.md)`` (key ``CLAUDE.md``) at the same missing file do not merge.
    """
    if kind == "wikilink":
        return target  # wikilinks resolve by note name, not by directory
    if not target:
        return target
    if target.startswith("/"):
        return posixpath.normpath(target.lstrip("/"))
    return posixpath.normpath(posixpath.join(posixpath.dirname(src), target))


def group_broken_links(broken_links: list[dict]) -> list[dict]:
    """Collapse broken links by the missing file they point at.

    Several links can name the same non-existent target - `README.md` and
    `CONTRIBUTING.md` both linking a missing `CLAUDE.md`, say. They describe one
    absent file, so the renderer should draw one ghost they both tether to, not a
    separate ghost per link.

    Targets are normalised to a canonical key (see ``_broken_link_key``) before
    grouping. Returns ``[{"target", "sources"}]`` ordered by descending source
    count then key, so the most-referenced ghost is rendered first.
    """
    groups: dict[str, list[str]] = {}
    for bl in broken_links:
        src = bl.get("from") or ""
        target = bl.get("target") or "?"
        key = _broken_link_key(src, target, bl.get("kind"))
        sources = groups.setdefault(key, [])
        if src not in sources:
            sources.append(src)
    return [
        {"target": key, "sources": sources}
        for key, sources in sorted(
            groups.items(), key=lambda kv: (-len(kv[1]), kv[0])
        )
    ]
