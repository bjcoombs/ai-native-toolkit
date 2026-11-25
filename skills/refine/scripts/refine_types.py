"""Shapes shared by the refinement script's modules (issue #1320).

The platform adapter (`refine_platform.py`) turns each host's raw JSON into
`Issue` and `PullRequest`; every check reads only these shapes, so a GitLab
adapter adds no branch to the checks. The output shapes (`IssuePlan`, `Plan`
and the blocks they hold) are described for consumers by
`refine-output.schema.json`, next to this file.

Python 3.8 compatible and stdlib only.
"""
from __future__ import annotations

from typing import Dict, List, Optional, TypedDict


class Blocker(TypedDict):
    number: int
    state: str  # "open" or "closed"


class Issue(TypedDict):
    number: int
    title: str
    body: str
    state: str  # "open" or "closed"
    state_reason: Optional[str]  # "completed", "not_planned", "duplicate" or None
    labels: List[str]
    created_at: str
    closed_at: Optional[str]
    url: str
    blocked_by: List[Blocker]
    sub_issues_total: int
    comments: List[str]


class PullRequest(TypedDict):
    number: int
    title: str
    files: List[str]
    closes: List[int]


class Labels(TypedDict):
    triage: str
    ready: str


class ChecklistEntry(TypedDict):
    id: str
    check: str  # "script" or "judgement"


class Definition(TypedDict):
    version: int
    sections: List[str]
    heading_levels: List[int]
    empty_markers: List[str]
    max_points: int
    size_pattern: str
    labels: Labels
    checklist: List[ChecklistEntry]


class ChecklistResult(TypedDict):
    id: str
    check: str
    status: str  # "pass", "fail" or "needs_judgement"
    detail: str
    hints: List[str]


class Overlap(TypedDict):
    pr: int
    kind: str  # "closes" or "files"
    files: List[str]


class DuplicatePair(TypedDict):
    kept: int
    duplicate: int
    kind: str  # "exact" or "near"
    basis: str  # "title_and_body", "title" or "body"
    similarity: float
    closable: bool


class DuplicateRef(TypedDict):
    number: int
    kind: str
    basis: str
    similarity: float
    survivor: int
    closable: bool


class Untriaged(TypedDict):
    number: int
    days: int
    reason: str  # "needs_triage_label" or "no_labels"


class IssuePlan(TypedDict):
    number: int
    title: str
    url: str
    labels: List[str]
    created_at: str
    verdict: str  # "ready", "needs_judgement" or "not_ready"
    settled: bool
    reasons: List[str]
    missing_sections: List[str]
    checklist: List[ChecklistResult]
    size_points: Optional[int]
    sub_issues: int
    blocked_by: List[Blocker]
    overlaps: List[Overlap]
    duplicates: List[DuplicateRef]
    untriaged_days: Optional[int]


class Buffer(TypedDict):
    depth: int
    builder_slots: int
    target: int
    meets_target: bool
    issues: List[int]
    rule: str


class CriticalPath(TypedDict):
    length: int
    points: int
    unsized: int
    issues: List[int]
    cycle_members: List[int]


class ClosedWindow(TypedDict):
    days: int
    count: int
    truncated: bool


class DefinitionRef(TypedDict):
    path: str
    version: int


class Summary(TypedDict):
    open_issues: int
    reported_issues: int
    verdicts: Dict[str, int]
    settled: int
    untriaged: int
    duplicate_pairs: Dict[str, int]


class Plan(TypedDict):
    schema_version: str
    generated_at: str
    repository: str
    platform: str
    dry_run: bool
    definition: DefinitionRef
    issue: Optional[int]
    summary: Summary
    issues: List[IssuePlan]
    duplicates: List[DuplicatePair]
    untriaged: List[Untriaged]
    buffer: Buffer
    critical_path: CriticalPath
    closed_window: ClosedWindow
