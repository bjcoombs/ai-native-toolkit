"""Read-only platform access for the refinement script: one adapter per issue host (issue #1320).

Every check in the refinement script reads `Issue` and `PullRequest`
(`refine_types.py`), never a host's raw JSON. An adapter implements the
`Backlog` protocol: open issues, issues closed since a date, and open pull
requests. `GitHubBacklog` reads them through `gh`; a GitLab adapter (`glab`)
can follow by adding a class with the same three methods (#1327).

All host access goes through `run_gh_readonly`, which refuses any call that
could change state before it starts a process:

- `gh issue list` and `gh pr list` (and their `view` forms) are allowed;
- `gh api` is allowed only without a method override or a field flag, so it
  stays a GET;
- every other subcommand is refused with `ReadOnlyViolation`.

The runner is injectable (`Runner`), so the tests replay recorded `gh` output
with no network. A missing `gh` and malformed output both raise `GhError`, so
the script's host-failure exit code covers them.

Limit: `gh issue list --json comments` returns one page of comments per issue
(the first 100) and does not paginate, so on a longer thread a newer
`refine:size` marker past that page is not seen.

Python 3.8 compatible and stdlib only.
"""
from __future__ import annotations

import json
import subprocess
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence

from refine_types import Blocker, Comment, Issue, PullRequest

Runner = Callable[[Sequence[str]], str]

# The `gh` subcommands that only read. A two-word command is matched on its
# first two arguments.
READ_COMMANDS = frozenset({("issue", "list"), ("issue", "view"), ("pr", "list"), ("pr", "view")})
# Flags that turn `gh api` into a write: a method override, or a request body.
API_WRITE_FLAGS = ("-X", "--method", "-f", "--raw-field", "-F", "--field", "--input")

ISSUE_FIELDS = "number,title,body,state,stateReason,labels,createdAt,closedAt,url"
OPEN_ISSUE_FIELDS = ISSUE_FIELDS + ",blockedBy,subIssuesSummary,comments"
PR_FIELDS = "number,title,files,closingIssuesReferences"
# `gh issue list` reads through the search API when --search is set, which
# returns at most 1000 results.
LIST_LIMIT = 1000

Json = Dict[str, Any]


class ReadOnlyViolation(RuntimeError):
    """A `gh` call that could write was refused before it ran."""


class GhError(RuntimeError):
    """`gh` exited non-zero; the message carries its stderr."""


def _is_write_flag(arg: str) -> bool:
    for flag in API_WRITE_FLAGS:
        if arg == flag:
            return True
        if flag.startswith("--"):
            if arg.startswith(flag + "="):
                return True
        elif arg.startswith(flag):  # a glued value: -X<verb>, -f<key>=<value>
            return True
    return False


def check_read_only(args: Sequence[str]) -> None:
    """Raise `ReadOnlyViolation` unless `args` (the arguments after `gh`) only read."""
    if len(args) >= 2 and (args[0], args[1]) in READ_COMMANDS:
        return
    if args and args[0] == "api":
        bad = [a for a in args[1:] if _is_write_flag(a)]
        if bad:
            raise ReadOnlyViolation(f"gh api with {bad[0]} may write; refused: {' '.join(args)}")
        return
    raise ReadOnlyViolation(f"not a read-only gh call; refused: {' '.join(args)}")


def run_gh_readonly(args: Sequence[str]) -> str:
    """Run `gh <args>` after `check_read_only` and return its stdout."""
    check_read_only(args)
    try:
        proc = subprocess.run(
            ["gh", *args], capture_output=True, encoding="utf-8", errors="replace", check=False,
        )
    except OSError as exc:  # gh missing from PATH, or not executable
        raise GhError(f"cannot run gh: {exc}") from exc
    if proc.returncode != 0:
        raise GhError(f"gh {' '.join(args)} exited {proc.returncode}: {proc.stderr.strip()}")
    return proc.stdout


def _lower(value: Optional[str]) -> Optional[str]:
    return value.lower() if value else None


def _comments(raw: Json) -> List[Comment]:
    return [Comment(body=str(c.get("body") or ""), created_at=str(c.get("createdAt") or ""))
            for c in raw.get("comments") or []]


def _blockers(raw: Json) -> List[Blocker]:
    # GraphQL shape: {"nodes": [...], "totalCount": n}, not the REST flat array.
    nodes = (raw.get("blockedBy") or {}).get("nodes") or []
    return [Blocker(number=int(n["number"]), state=str(n.get("state") or "").lower()) for n in nodes]


def github_issue(raw: Json) -> Issue:
    """One `gh issue list --json` row as an `Issue`."""
    summary = raw.get("subIssuesSummary") or {}
    return Issue(
        number=int(raw["number"]),
        title=str(raw.get("title") or ""),
        body=str(raw.get("body") or ""),
        state=str(raw.get("state") or "").lower(),
        state_reason=_lower(raw.get("stateReason")),
        labels=sorted(str(lab["name"]) for lab in raw.get("labels") or []),
        created_at=str(raw.get("createdAt") or ""),
        closed_at=raw.get("closedAt") or None,
        url=str(raw.get("url") or ""),
        blocked_by=_blockers(raw),
        sub_issues_total=int(summary.get("total") or 0),
        comments=_comments(raw),
    )


def github_pull_request(raw: Json) -> PullRequest:
    """One `gh pr list --json` row as a `PullRequest`."""
    return PullRequest(
        number=int(raw["number"]),
        title=str(raw.get("title") or ""),
        files=sorted(str(f["path"]) for f in raw.get("files") or []),
        closes=sorted(int(i["number"]) for i in raw.get("closingIssuesReferences") or []),
    )


class Backlog(Protocol):
    """What the refinement script reads from an issue host."""

    platform: str
    repo: str
    open_truncated: bool
    closed_truncated: bool

    def open_issues(self) -> List[Issue]: ...

    def closed_issues_since(self, since: date) -> List[Issue]: ...

    def open_pull_requests(self) -> List[PullRequest]: ...


class GitHubBacklog:
    """The `Backlog` protocol over `gh`, for one `owner/name` repository."""

    platform = "github"

    def __init__(self, repo: str, runner: Runner = run_gh_readonly) -> None:
        self.repo = repo
        self._run = runner
        self.open_truncated = False
        self.closed_truncated = False

    def _list(self, args: List[str]) -> List[Json]:
        try:
            rows = json.loads(self._run(args) or "[]")
        except json.JSONDecodeError as exc:
            raise GhError(f"gh {' '.join(args)} returned malformed JSON: {exc}") from exc
        if not isinstance(rows, list):
            raise GhError(f"gh {' '.join(args)} did not return a JSON array")
        return rows

    def open_issues(self) -> List[Issue]:
        rows = self._list(["issue", "list", "--repo", self.repo, "--state", "open",
                           "--limit", str(LIST_LIMIT), "--json", OPEN_ISSUE_FIELDS])
        self.open_truncated = len(rows) >= LIST_LIMIT
        return [github_issue(r) for r in rows]

    def closed_issues_since(self, since: date) -> List[Issue]:
        rows = self._list(["issue", "list", "--repo", self.repo, "--state", "closed",
                           "--search", f"closed:>={since.isoformat()}",
                           "--limit", str(LIST_LIMIT), "--json", ISSUE_FIELDS])
        self.closed_truncated = len(rows) >= LIST_LIMIT
        return [github_issue(r) for r in rows]

    def open_pull_requests(self) -> List[PullRequest]:
        rows = self._list(["pr", "list", "--repo", self.repo, "--state", "open",
                           "--limit", str(LIST_LIMIT), "--json", PR_FIELDS])
        return [github_pull_request(r) for r in rows]
