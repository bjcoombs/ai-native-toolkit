#!/usr/bin/env python3
"""Backlog refinement plan: check open issues against the definition of ready and write JSON (issue #1320).

Deterministic and read-only. It reads the definition of ready from its
document, reads the backlog through one platform adapter, settles every check
a script can settle, and writes one JSON plan. It never writes to the issue
host and makes no model call; the refiner workflow acts on the plan and sends
only unsettled issues to a model (#1327).

    python3 refine.py --repo <owner/name> [--dry-run] [--issue <n>] [--out <path>]
        [--definition <path>] [--builder-slots <n>] [--now <iso8601>]

- Without `--issue`, every open issue is checked: the scheduled
  `refine-sweep` path. With it, only that issue is reported (the
  `issues: opened` path); duplicates, the buffer and the critical path are
  still computed over the whole backlog.
- `--dry-run` sets `"dry_run": true` in the plan so the workflow carries the
  mode to its own steps. The script writes nothing to the host either way.
- `--definition` defaults to `docs/contributing/definition-of-ready.md` under
  the current directory, then under this toolkit checkout.
- `--builder-slots` sets the ready-buffer target to twice the slots.
- `--now` fixes the clock, for replaying recorded input.

The plan's shape is `refine-output.schema.json`, next to this file. The checks
live in `refine_checks.py`, duplicates in `refine_duplicates.py`, the buffer
and critical path in `refine_graph.py`, and host access in
`refine_platform.py` (`gh`, behind a read-only wrapper).

Exit codes: 0 plan written; 2 usage or definition error; 3 the host call
failed or was refused.

Python 3.8 compatible and stdlib only, plus `gh` on PATH.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Sequence, Set

import refine_checks as checks
from refine_definition import DEFAULT_PATH, DefinitionError, load_definition
from refine_duplicates import by_issue, find_duplicates
from refine_graph import critical_path, ready_buffer
from refine_platform import Backlog, GhError, GitHubBacklog, ReadOnlyViolation
from refine_types import (
    ClosedWindow, Definition, DefinitionRef, DuplicatePair, DuplicateRef, Issue, IssuePlan,
    Overlap, Plan, PullRequest, Summary, Untriaged,
)

SCHEMA_VERSION = "1"
CLOSED_WINDOW_DAYS = 90
DEFAULT_BUILDER_SLOTS = 5
VERDICTS = ("ready", "needs_judgement", "not_ready")
TOOLKIT_ROOT = Path(__file__).resolve().parents[3]


def _duplicate_refs(number: int, pairs: List[DuplicatePair]) -> List[DuplicateRef]:
    refs: List[DuplicateRef] = []
    for p in pairs:
        other = p["duplicate"] if p["kept"] == number else p["kept"]
        refs.append(DuplicateRef(number=other, kind=p["kind"], basis=p["basis"],
                                 similarity=p["similarity"], survivor=p["kept"], closable=p["closable"]))
    return refs


def _overlap_reason(o: Overlap) -> str:
    if o["kind"] == "closes":
        return f"open PR #{o['pr']} closes this issue"
    return f"open PR #{o['pr']} changes files this issue names: {', '.join(o['files'])}"


def issue_plan(issue: Issue, definition: Definition, repo: str, prs: List[PullRequest],
               pairs: List[DuplicatePair], now: datetime, open_numbers: Optional[Set[int]] = None) -> IssuePlan:
    """One issue's verdict, the reasons for it and the check results behind it."""
    items = checks.checklist(issue, definition, repo, open_numbers)
    overlaps = checks.overlaps(issue, prs)
    dups = _duplicate_refs(issue["number"], pairs)
    reasons = [f"{r['id']}: {r['detail']}" for r in items if r["status"] == "fail"]
    reasons += [_overlap_reason(o) for o in overlaps]
    reasons += [f"exact duplicate of #{d['survivor']}" for d in dups
                if d["kind"] == "exact" and d["survivor"] != issue["number"]]
    points, source = checks.size_reading(issue, definition)
    unsized = points is None and issue["sub_issues_total"] == 0
    blocked = bool(reasons)
    reasons += [f"{r['id']}: needs judgement ({r['detail']})" for r in items if r["status"] == "needs_judgement"]
    if blocked:
        verdict = "not_ready"
    elif any(r["status"] == "needs_judgement" for r in items):
        verdict = "needs_judgement"
    else:
        verdict = "ready"
    return IssuePlan(
        number=issue["number"], title=issue["title"], url=issue["url"], labels=issue["labels"],
        created_at=issue["created_at"], verdict=verdict, settled=verdict != "needs_judgement",
        reasons=reasons, missing_sections=checks.missing_sections(issue, definition), checklist=items,
        size_points=points, size_source=source, unsized=unsized, sub_issues=issue["sub_issues_total"],
        blocked_by=issue["blocked_by"], overlaps=overlaps, duplicates=dups,
        untriaged_days=checks.untriaged_days(issue, definition, now),
    )


def _untriaged(issues: List[Issue], definition: Definition, now: datetime) -> List[Untriaged]:
    out: List[Untriaged] = []
    for issue in issues:
        days = checks.untriaged_days(issue, definition, now)
        if days is not None:
            reason = "no_labels" if not issue["labels"] else "needs_triage_label"
            out.append(Untriaged(number=issue["number"], days=days, reason=reason))
    return out


def build_plan(backlog: Backlog, definition: Definition, definition_path: str, now: datetime,
               dry_run: bool, only: Optional[int], builder_slots: int) -> Plan:
    """Read the backlog once and assemble the plan."""
    open_issues = sorted(backlog.open_issues(), key=lambda i: i["number"])
    if only is not None and only not in {i["number"] for i in open_issues}:
        raise ValueError(f"issue #{only} is not an open issue in {backlog.repo}")
    closed = backlog.closed_issues_since((now - timedelta(days=CLOSED_WINDOW_DAYS)).date())
    prs = backlog.open_pull_requests()
    pairs = find_duplicates(open_issues, closed)
    pairs_by = by_issue(pairs)
    reported = [i for i in open_issues if only is None or i["number"] == only]
    open_numbers = {i["number"] for i in open_issues}
    plans = [issue_plan(i, definition, backlog.repo, prs, pairs_by.get(i["number"], []), now, open_numbers)
             for i in reported]
    points = {i["number"]: checks.size_points(i, definition) for i in open_issues}
    untriaged = _untriaged(open_issues, definition, now)
    summary = Summary(
        open_issues=len(open_issues), reported_issues=len(plans),
        verdicts={v: sum(1 for p in plans if p["verdict"] == v) for v in VERDICTS},
        settled=sum(1 for p in plans if p["settled"]), unsized=sum(1 for p in plans if p["unsized"]),
        untriaged=len(untriaged),
        duplicate_pairs={k: sum(1 for p in pairs if p["kind"] == k) for k in ("exact", "near")},
    )
    return Plan(
        schema_version=SCHEMA_VERSION,
        generated_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        repository=backlog.repo,
        platform=backlog.platform,
        dry_run=dry_run,
        definition=DefinitionRef(path=definition_path, version=definition["version"]),
        issue=only,
        summary=summary,
        issues=plans,
        duplicates=pairs,
        untriaged=untriaged,
        buffer=ready_buffer(open_issues, prs, definition["labels"]["ready"], builder_slots),
        critical_path=critical_path(open_issues, points),
        open_truncated=backlog.open_truncated,
        closed_window=ClosedWindow(days=CLOSED_WINDOW_DAYS, count=len(closed), truncated=backlog.closed_truncated),
    )


def resolve_definition(arg: Optional[str]) -> Path:
    if arg:
        return Path(arg)
    # Relative when found under the current directory, so the plan names it
    # the same way on every machine.
    return DEFAULT_PATH if DEFAULT_PATH.is_file() else TOOLKIT_ROOT / DEFAULT_PATH


def _parse_now(value: Optional[str]) -> datetime:
    if not value:
        return datetime.now(timezone.utc).replace(microsecond=0)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--dry-run", action="store_true", help='mark the plan "dry_run": true')
    parser.add_argument("--issue", type=int, help="report only this open issue")
    parser.add_argument("--out", help="write the plan here instead of stdout")
    parser.add_argument("--definition", help="path to definition-of-ready.md")
    parser.add_argument("--builder-slots", type=int, default=DEFAULT_BUILDER_SLOTS)
    parser.add_argument("--now", help="ISO 8601 time to use as now")
    args = parser.parse_args(argv)
    if args.builder_slots < 1:
        parser.error("--builder-slots must be at least 1")
    return args


def main(argv: Optional[Sequence[str]] = None, backlog: Optional[Backlog] = None) -> int:
    args = parse_args(argv)
    path = resolve_definition(args.definition)
    try:
        definition = load_definition(path)
        now = _parse_now(args.now)
    except (DefinitionError, ValueError) as exc:
        print(f"refine: {exc}", file=sys.stderr)
        return 2
    source = backlog if backlog is not None else GitHubBacklog(args.repo)
    try:
        plan = build_plan(source, definition, path.as_posix(), now, args.dry_run, args.issue, args.builder_slots)
    except (GhError, ReadOnlyViolation) as exc:
        print(f"refine: {exc}", file=sys.stderr)
        return 3
    except ValueError as exc:
        print(f"refine: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(plan, indent=2, sort_keys=True) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    s = plan["summary"]
    print(f"refine: {s['reported_issues']} issue(s): " + ", ".join(f"{k} {v}" for k, v in s["verdicts"].items())
          + f"; buffer {plan['buffer']['depth']}/{plan['buffer']['target']}"
          + f"; critical path {plan['critical_path']['length']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
