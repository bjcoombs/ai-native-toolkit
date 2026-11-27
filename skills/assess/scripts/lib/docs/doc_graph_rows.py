"""Per-doc link parents and per-directory counts for the doc graph.

Split out of ``lib.docs.doc_graph`` (#840) to keep that file under its line
ceiling. :func:`link_parents` gives every node one ``{path, link_parent,
link_entry}`` record over the link-only graph, so a consumer can draw the link
path from an entry document to any doc; :func:`directory_breakdown` gives doc,
unreachable and broken-link counts per top-level directory, largest gap first.
Both are pure over the graph ``build_doc_graph`` derives; ``doc_graph`` is the
only caller; their semantics are described with the block in doc_graph's
reference. Tests: ``tests/test_doc_graph.py``.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # annotation only; the caller owns the networkx import
    import networkx as nx

    from lib.run_context_types import BrokenLink, DirectoryBreakdownRow, LinkParent


def link_parents(graph: nx.DiGraph, entry_points: list[str]) -> list[LinkParent]:
    """One ``{path, link_parent, link_entry}`` record per node in ``graph``.

    ``graph`` is the link-only subgraph, so a doc reachable only over a
    ``reference`` edge (a backticked doc path) has no link path and carries
    ``null`` for both fields - which is also what a doc nothing reaches carries.
    An entry carries ``link_parent`` ``None`` and ``link_entry`` equal to its own
    path, so the three states stay distinguishable.

    A consumer will draw a link-path strip from it, so a rebuild must be
    byte-identical. That comes from the ordering: seeds are ``entry_points`` in
    their exported ascending byte order, and within a walk the frontier and each
    node's successors are taken in ascending byte order. First write wins, and
    the recorded set is the visited set - a doc already recorded is never
    enqueued again, so an entry met as a successor is not expanded by the
    walking entry and never lends its name to another entry's children.

    Each seed's walk runs to exhaustion before the next starts. That is an
    attribution rule, not a determinism one: a doc belongs to the first entry,
    in entry order, that reaches it at all, so a later entry gaining a shorter
    link does not re-attribute it. The cost is that the recorded path is the
    shortest from its own entry, not the shortest from any entry.
    """
    nodes = set(graph.nodes())
    # (link_parent, link_entry), keyed by path. Entries land before any walk so
    # one entry linking another cannot overwrite it.
    recorded: dict[str, tuple[str | None, str | None]] = {
        e: (None, e) for e in entry_points if e in nodes
    }
    for entry in entry_points:
        if entry not in nodes:
            continue
        frontier = [entry]
        while frontier:
            reached: list[str] = []
            for node in frontier:
                for succ in sorted(graph.successors(node)):
                    if succ in recorded:
                        continue
                    recorded[succ] = (node, entry)
                    reached.append(succ)
            frontier = sorted(reached)
    return [
        {"path": n,
         "link_parent": recorded.get(n, (None, None))[0],
         "link_entry": recorded.get(n, (None, None))[1]}
        for n in sorted(nodes)
    ]


def _top_dir(rel_path: str) -> str:
    """First path segment of a doc's rel path; root-level docs key as ``.``."""
    head, sep, _ = rel_path.partition("/")
    return head if sep else "."


def directory_breakdown(
    nodes: Iterable[str], unreachable: list[str], broken: list[BrokenLink],
) -> list[DirectoryBreakdownRow]:
    """Doc, unreachable and broken-link counts per top-level directory, largest
    gap first. A broken link counts toward the directory of the doc it is
    written in (``from``)."""
    counts: dict[str, list[int]] = {}
    for n in nodes:
        counts.setdefault(_top_dir(n), [0, 0, 0])[0] += 1
    for n in unreachable:
        counts[_top_dir(n)][1] += 1
    for b in broken:
        counts.setdefault(_top_dir(b.get("from", "")), [0, 0, 0])[2] += 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1][1], -kv[1][2], -kv[1][0], kv[0]))
    return [
        {"path": d, "doc_count": c[0], "unreachable_count": c[1], "broken_link_count": c[2]}
        for d, c in ranked
    ]

