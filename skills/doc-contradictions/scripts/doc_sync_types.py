"""The plan shapes the doc-sync jobs hand each other (issue #1453).

`doc_sync_findings.py` writes a `Plan` in the read-only `check` job and
`doc_sync_post.py` reads it in the `post` job, so both type against these
definitions and mypy catches a key one side renames. Stdlib only.
"""
from __future__ import annotations

from typing import List, TypedDict


class Side(TypedDict):
    path: str
    line: int
    quote: str


class Finding(TypedDict):
    id: str
    kind: str
    subject: str
    a: Side
    b: Side
    why: str
    confidence: str
    cluster: str


class Plan(TypedDict, total=False):
    status: str  # checked, skipped or error
    reason: str
    quiet: bool  # a skip that writes no new summary comment
    clusters: int
    cross_cluster: bool
    threads: List[Finding]
    summary: List[Finding]
    dropped: List[str]
    dropped_excluded: int
