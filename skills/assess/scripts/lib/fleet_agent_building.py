"""Fleet roll-up of the ``agent_building`` inventory (issue #689).

Two outputs, both read from the ``repos.jsonl`` rows (``lib/fleet.py``), so they
work on counts-only rows: every field they read is a vocabulary slug, a
number or a boolean, which counts-only keeps.

- ``group_counts``: per group, how many scanned repositories fall in each class
  (``lib/agent_building.py``), carried on each ``groups.jsonl`` row as
  ``agent_building``. ``None`` when no repository in the group has an available
  block (every scan failed, degraded or was left out by ``--scans``), never a
  false 0.
- ``clusters``: similar ``builds-agent`` repositories across the whole fleet,
  written to ``agent_clusters.json``.

Cluster rule (maintainer decision on #689). Two ``builds-agent`` repositories
are linked when all three hold:

1. they have the same class;
2. the Jaccard similarity of their ``sdks`` sets is at least 0.5 (two empty sets
   are not similar: no shared evidence links nothing);
3. they have the same ``pairing`` value (both carry the issue-tracker plus
   Git-host API-client marker, or neither does).

Clusters are the connected components of that relation. A component of one
repository is not a cluster: a cluster shows duplication, so it has at least
two members. Deterministic and offline.

Output rule (#546). A cluster carries counts and vocabulary slugs only: its
size, how many groups it spans, the union and intersection of its members'
SDK slugs, and its pairing value. The repository ids and group names of its
members appear only when the caller passes ``name=True`` (``fleet.py
--name-agent-clusters``, off by default), in either output mode. Clusters are
ordered by size (largest first), then SDK union, pairing, groups spanned and
shared SDKs, so the order of unnamed clusters never depends on a repository
name (clusters equal on all of these print identically); ids (``c1``, ``c2`` ...) follow that order.
"""
from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, NotRequired, TypedDict

from lib.agent_building import CLASSES
from lib.fleet import STATUS_OK, FleetRow

CLUSTERS_FILE = "agent_clusters.json"
AGENT_CLUSTERS_VERSION = 1
JACCARD_THRESHOLD = 0.5
CLUSTER_CLASS = "builds-agent"
RULE = ("same class; Jaccard of the sdks sets >= 0.5; same pairing marker; "
        "connected components of 2 or more builds-agent repositories")


class AgentBuildingCounts(TypedDict):
    """``groups.jsonl`` ``agent_building``: scanned repositories per class."""

    repos: int  # repositories with an available agent_building block
    builds_agent: int
    builds_mcp_server: int
    uses_agent_tooling: int
    none: int


class AgentCluster(TypedDict):
    id: str
    agent_class: str
    size: int
    groups: int  # distinct groups the members belong to
    sdks: list[str]  # union of the members' sdks
    shared_sdks: list[str]  # intersection of the members' sdks
    pairing: bool
    repo_ids: NotRequired[list[str]]  # only with --name-agent-clusters
    group_ids: NotRequired[list[str]]  # only with --name-agent-clusters


class AgentClusters(TypedDict):
    agent_clusters_version: int
    rule: str
    named: bool
    evidence_repos: int  # repositories with an available agent_building block
    builds_agent_repos: int
    clustered_repos: int
    clusters: list[AgentCluster]


class _Member(TypedDict):
    repo: str
    group: str
    agent_class: str
    sdks: frozenset[str]
    pairing: bool


def _block(row: FleetRow) -> Mapping[str, Any] | None:
    """The row's ``agent_building`` block when it is evidence, else None."""
    if row["status"] != STATUS_OK:
        return None
    block = (row["scans"] or {}).get("agent_building")
    if not isinstance(block, dict) or not block.get("available"):
        return None
    return block


def group_counts(rows: Iterable[FleetRow]) -> AgentBuildingCounts | None:
    """Scanned repositories per class in one group; None with no available block."""
    counts = dict.fromkeys(CLASSES, 0)
    evidence = 0
    for row in rows:
        block = _block(row)
        if block is None:
            continue
        evidence += 1
        cls = block.get("agent_class")
        if cls in counts:
            counts[cls] += 1
    if not evidence:
        return None
    return {
        "repos": evidence,
        "builds_agent": counts["builds-agent"],
        "builds_mcp_server": counts["builds-mcp-server"],
        "uses_agent_tooling": counts["uses-agent-tooling"],
        "none": counts["none"],
    }


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """``|a & b| / |a | b|``; 0 for two empty sets."""
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def linked(a: _Member, b: _Member) -> bool:
    """The three-part cluster rule in the module docstring."""
    return (a["agent_class"] == b["agent_class"]
            and jaccard(a["sdks"], b["sdks"]) >= JACCARD_THRESHOLD
            and a["pairing"] == b["pairing"])


def _members(rows: Iterable[FleetRow]) -> list[_Member]:
    members: list[_Member] = []
    for row in rows:
        block = _block(row)
        if block is None or block.get("agent_class") != CLUSTER_CLASS:
            continue
        sdks = block.get("sdks")
        members.append({
            "repo": row["repo"], "group": row["group"], "agent_class": CLUSTER_CLASS,
            "sdks": frozenset(s for s in sdks if isinstance(s, str)) if isinstance(sdks, list) else frozenset(),
            "pairing": bool(block.get("pairing")),
        })
    return sorted(members, key=lambda m: m["repo"])


def _components(members: Sequence[_Member]) -> list[list[_Member]]:
    parent = list(range(len(members)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(members)):
        for j in range(i + 1, len(members)):
            if linked(members[i], members[j]):
                parent[find(i)] = find(j)
    groups: dict[int, list[_Member]] = {}
    for i, member in enumerate(members):
        groups.setdefault(find(i), []).append(member)
    return [component for component in groups.values() if len(component) >= 2]


def _cluster(component: Sequence[_Member], name: bool) -> AgentCluster:
    sets = [m["sdks"] for m in component]
    cluster: AgentCluster = {
        "id": "",
        "agent_class": component[0]["agent_class"],
        "size": len(component),
        "groups": len({m["group"] for m in component}),
        "sdks": sorted(frozenset().union(*sets)),
        "shared_sdks": sorted(frozenset.intersection(*sets)),
        "pairing": component[0]["pairing"],
    }
    if name:
        cluster["repo_ids"] = sorted(m["repo"] for m in component)
        cluster["group_ids"] = sorted({m["group"] for m in component})
    return cluster


def clusters(rows: Iterable[FleetRow], name: bool = False) -> AgentClusters:
    """Clusters of similar ``builds-agent`` repositories across the fleet."""
    rows = list(rows)
    evidence = sum(1 for row in rows if _block(row) is not None)
    members = _members(rows)
    found = [_cluster(component, name) for component in _components(members)]
    found.sort(key=lambda c: (-c["size"], c["sdks"], c["pairing"], -c["groups"], c["shared_sdks"],
                              c.get("repo_ids", [])))
    for index, cluster in enumerate(found, start=1):
        cluster["id"] = f"c{index}"
    return {
        "agent_clusters_version": AGENT_CLUSTERS_VERSION,
        "rule": RULE,
        "named": name,
        "evidence_repos": evidence,
        "builds_agent_repos": len(members),
        "clustered_repos": sum(c["size"] for c in found),
        "clusters": found,
    }


def write_clusters(out_dir: Path, result: AgentClusters) -> Path:
    """Write ``<out_dir>/agent_clusters.json`` through a temp file and ``os.replace``.

    Written on every run, so a rerun without ``--name-agent-clusters`` replaces a
    named file an earlier run left.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / CLUSTERS_FILE
    text = json.dumps(result, sort_keys=True, indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(dir=out_dir, prefix=f".{CLUSTERS_FILE}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target
