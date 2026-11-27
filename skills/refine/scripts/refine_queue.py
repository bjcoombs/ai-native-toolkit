#!/usr/bin/env python3
"""The ready queue as a contract-draft input: each queued issue's Success Criteria items (issue #1328).

The queue is the ready buffer (`refine_graph.ready_buffer`): open, the ready
label, no open blocker, no sub-issues and no open pull request closing it. For
each queued issue the Success Criteria section is read the way the readiness
checks read it (`refine_checks.all_sections`: the body, replaced section by
section by the newest trusted `<!-- triage:clarified-scope -->` comment) and
split into its list items (`refine_checks.criteria_items`).

The output feeds `scripts/contract/draft.py write --queue`:

    {"repository": str, "issues": [{"number": int, "title": str, "url": str, "criteria": [str]}]}

Deterministic and read-only, like `refine.py`:

    python3 refine_queue.py --repo <owner/name> [--out <path>] [--definition <path>]

Exit codes: 0 queue written; 2 usage or definition error; 3 the host call
failed or was refused.

Python 3.8 compatible and stdlib only, plus `gh` on PATH.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Sequence, TypedDict

import refine_checks as checks
from refine_definition import DefinitionError, load_definition, resolve_path
from refine_graph import ready_buffer
from refine_platform import Backlog, GhError, GitHubBacklog, ReadOnlyViolation
from refine_types import Definition, Issue, PullRequest

SUCCESS_CRITERIA = "Success Criteria"


class QueuedIssue(TypedDict):
    number: int
    title: str
    url: str
    criteria: List[str]


class Queue(TypedDict):
    repository: str
    issues: List[QueuedIssue]


def build_queue(repo: str, issues: List[Issue], prs: List[PullRequest], definition: Definition) -> Queue:
    """The ready buffer's issues, in number order, each with its Success Criteria items."""
    members = set(ready_buffer(issues, prs, definition["labels"]["ready"], 1)["issues"])
    queued: List[QueuedIssue] = []
    for issue in sorted(issues, key=lambda i: i["number"]):
        if issue["number"] not in members:
            continue
        section = checks.all_sections(issue, definition).get(SUCCESS_CRITERIA, "")
        queued.append(QueuedIssue(number=issue["number"], title=issue["title"], url=issue["url"],
                                  criteria=checks.criteria_items(section)))
    return Queue(repository=repo, issues=queued)


def main(argv: Optional[Sequence[str]] = None, backlog: Optional[Backlog] = None) -> int:
    parser = argparse.ArgumentParser(description="Write the ready queue's Success Criteria as JSON.")
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--out", help="write the queue here instead of stdout")
    parser.add_argument("--definition", help="path to definition-of-ready.md")
    args = parser.parse_args(argv)
    try:
        definition = load_definition(resolve_path(args.definition))
    except (DefinitionError, ValueError, OSError) as exc:
        print(f"refine_queue: {exc}", file=sys.stderr)
        return 2
    source = backlog if backlog is not None else GitHubBacklog(args.repo)
    try:
        queue = build_queue(source.repo, source.open_issues(), source.open_pull_requests(), definition)
    except (GhError, ReadOnlyViolation) as exc:
        print(f"refine_queue: {exc}", file=sys.stderr)
        return 3
    text = json.dumps(queue, indent=2, sort_keys=True) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    print(f"refine_queue: {len(queue['issues'])} issue(s) queued", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
