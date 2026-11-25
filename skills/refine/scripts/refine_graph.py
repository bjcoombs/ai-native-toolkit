"""Ready buffer and critical path over the open issues' blocked-by graph (issue #1320).

**Ready buffer** (#1317, clarified scope item 2): how many issues a marathon
could start now. An open issue counts when it carries the ready label from the
definition of ready, has no open blocker, is not a decomposed parent (a parent
is a verification unit, not work: `skills/issues/SKILL.md`, "Size by
Judgment"), and no open PR closes it (already in flight). The target is twice
the builder slots; `meets_target` compares the two.

**Critical path**: the longest chain of open issues joined by blocked-by
edges, counted in issues, ties broken towards lower issue numbers so the
answer is the same on every run. Edges to issues outside the open set are
ignored. `points` sums the size lines along the path and `unsized` counts the
issues on it with none. Issues on a dependency cycle, or blocked behind one,
have no longest path; they are listed in `cycle_members` and left out.

Python 3.8 compatible and stdlib only.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

from refine_types import Buffer, CriticalPath, Issue, PullRequest

BUFFER_RULE = (
    "open, carries the ready label, no open blocker, no sub-issues, "
    "and no open pull request closes it"
)


def ready_buffer(issues: List[Issue], prs: List[PullRequest], ready_label: str, builder_slots: int) -> Buffer:
    in_flight: Set[int] = {n for pr in prs for n in pr["closes"]}
    members = sorted(
        i["number"] for i in issues
        if ready_label in i["labels"]
        and not any(b["state"] == "open" for b in i["blocked_by"])
        and i["sub_issues_total"] == 0
        and i["number"] not in in_flight
    )
    target = 2 * builder_slots
    return Buffer(depth=len(members), builder_slots=builder_slots, target=target,
                  meets_target=len(members) >= target, issues=members, rule=BUFFER_RULE)


def _topological(nodes: List[int], blockers: Dict[int, List[int]]) -> Tuple[List[int], List[int]]:
    """(order, leftover): Kahn's order, and the nodes a cycle keeps out of it."""
    dependants: Dict[int, List[int]] = {n: [] for n in nodes}
    indegree = {n: len(blockers[n]) for n in nodes}
    for node in nodes:
        for blocker in blockers[node]:
            dependants[blocker].append(node)
    queue = sorted(n for n in nodes if indegree[n] == 0)
    order: List[int] = []
    while queue:
        node = queue.pop(0)
        order.append(node)
        for nxt in sorted(dependants[node]):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
        queue.sort()
    done = set(order)
    return order, sorted(n for n in nodes if n not in done)


def critical_path(issues: List[Issue], points: Dict[int, Optional[int]]) -> CriticalPath:
    nodes = sorted(i["number"] for i in issues)
    present = set(nodes)
    blockers = {i["number"]: sorted({b["number"] for b in i["blocked_by"]
                                     if b["number"] in present and b["number"] != i["number"]})
                for i in issues}
    order, cycle = _topological(nodes, blockers)
    # best[n]: the longest chain ending at n, as the list of issues on it.
    best: Dict[int, List[int]] = {}
    for node in order:
        chains = [best[b] for b in blockers[node]]
        longest = min(chains, key=lambda c: (-len(c), c)) if chains else []
        best[node] = longest + [node]
    path = min(best.values(), key=lambda c: (-len(c), c)) if best else []
    sized = [points.get(n) for n in path]
    return CriticalPath(
        length=len(path),
        points=sum(p for p in sized if p is not None),
        unsized=sum(1 for p in sized if p is None),
        issues=path,
        cycle_members=cycle,
    )
