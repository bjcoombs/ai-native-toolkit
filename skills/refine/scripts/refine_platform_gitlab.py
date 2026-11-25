"""Read-only GitLab access for the refinement script: the `Backlog` protocol over `glab api` (issue #1327).

`GitLabBacklog` is the GitLab twin of `refine_platform.GitHubBacklog`: it reads
one project's issues and merge requests through `glab api` and returns the same
`Issue` and `PullRequest` shapes (`refine_types.py`), so no check in
`refine_checks.py` branches on the host. `refine.py --platform gitlab` picks it.

Where a rule reads a GitHub-only field, the GitLab reading is:

| GitHub field | GitLab reading |
|---|---|
| `blockedBy` edges | linked issues of type `is_blocked_by` in the same project (`issues/:iid/links`); linked `blocks` issues are a Premium feature, so on Free every issue reads no edge |
| `authorAssociation` (trusted comment) | the author is a project member at Reporter (20) or above (`members/all`), or the refiner's bot (`REFINE_BOT_LOGIN`) |
| `subIssuesSummary` | none: GitLab child tasks are not read, so `sub_issues_total` is 0 and a large issue is sized, never "decomposed" |
| `stateReason` | none: GitLab records no close reason, so it is `None` |
| `closingIssuesReferences`, `files` | `merge_requests/:iid/closes_issues` and `merge_requests/:iid/diffs` |

GitLab system notes (label changes, links) are not comments and are dropped.
Scoped labels (`refine::ready`) are plain label names here. Timestamps are
normalised to `YYYY-MM-DDTHH:MM:SSZ`, the form the GitHub adapter returns.

Every call goes through `run_glab_readonly`, which refuses before starting a
process anything but `glab api` with no method override and no field flag,
and the `graphql` endpoint, which is always a POST. The runner is injectable,
so the tests replay recorded `glab` output (`tests/fixtures/refine_gitlab/`)
with no network. `glab` reads its host and token from `GITLAB_HOST` and
`GITLAB_TOKEN`.

Cost: one list call per state, plus one notes call and one links call per open
issue and two calls per open merge request; GitHub needs one call per list.

Python 3.8 compatible and stdlib only.
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote

from refine_platform import LIST_LIMIT, GhError, ReadOnlyViolation
from refine_types import Blocker, Comment, Issue, PullRequest

Runner = Callable[[Sequence[str]], str]
Json = Dict[str, Any]

API_WRITE_FLAGS = ("-X", "--method", "-f", "--raw-field", "-F", "--field", "--input")
# Reporter: a project member who can triage issues. Guests and Planners can
# comment but do not count, the way GitHub counts OWNER, MEMBER and COLLABORATOR.
TRUSTED_ACCESS = 20


def _is_write_flag(arg: str) -> bool:
    for flag in API_WRITE_FLAGS:
        if arg == flag or (flag.startswith("--") and arg.startswith(flag + "=")):
            return True
        if not flag.startswith("--") and arg.startswith(flag):
            return True
    return False


def check_glab_read_only(args: Sequence[str]) -> None:
    """Raise `ReadOnlyViolation` unless `args` (after `glab`) is a GET through `glab api`."""
    if not args or args[0] != "api":
        raise ReadOnlyViolation(f"not a read-only glab call; refused: {' '.join(args)}")
    bad = [a for a in args[1:] if _is_write_flag(a)]
    if bad:
        raise ReadOnlyViolation(f"glab api with {bad[0]} may write; refused: {' '.join(args)}")
    if any(a == "graphql" or a.startswith("graphql?") for a in args[1:]):
        raise ReadOnlyViolation("glab api graphql is a POST; refused")


def run_glab_readonly(args: Sequence[str]) -> str:
    """Run `glab <args>` after `check_glab_read_only` and return its stdout."""
    check_glab_read_only(args)
    try:
        proc = subprocess.run(["glab", *args], capture_output=True, encoding="utf-8", errors="replace", check=False)
    except OSError as exc:
        raise GhError(f"cannot run glab: {exc}") from exc
    if proc.returncode != 0:
        raise GhError(f"glab {' '.join(args)} exited {proc.returncode}: {proc.stderr.strip()}")
    return proc.stdout


def project_param(project: str) -> str:
    """A project path as the API takes it in a URL: `group/sub/name` becomes `group%2Fsub%2Fname`."""
    return quote(project, safe="")


def json_stream(text: str, what: str) -> List[Any]:
    """Every top-level JSON value in `text`, arrays flattened: `--paginate` prints one array per page."""
    decoder = json.JSONDecoder()
    out: List[Any] = []
    pos, text = 0, text.strip()
    while pos < len(text):
        try:
            value, pos = decoder.raw_decode(text, pos)
        except ValueError as exc:
            raise GhError(f"glab {what} returned malformed JSON: {exc}") from exc
        out.extend(value if isinstance(value, list) else [value])
        while pos < len(text) and text[pos].isspace():
            pos += 1
    return out


def iso(value: Any) -> str:
    """A GitLab timestamp (`2026-11-25T12:00:00.000Z`, or with an offset) as `2026-11-25T12:00:00Z`."""
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def username(user: Any) -> str:
    return str(user.get("username") or "") if isinstance(user, dict) else ""


def member_levels(rows: List[Any]) -> Dict[str, int]:
    """username -> the highest access level, from `members/all` rows."""
    out: Dict[str, int] = {}
    for row in rows:
        if isinstance(row, dict) and row.get("username"):
            name = str(row["username"])
            out[name] = max(out.get(name, 0), int(row.get("access_level") or 0))
    return out


def gitlab_state(value: Any) -> str:
    return "open" if value == "opened" else str(value or "").lower()


class GitLabBacklog:
    """The `Backlog` protocol over `glab api`, for one `group/project` path."""

    platform = "gitlab"

    def __init__(self, repo: str, runner: Runner = run_glab_readonly, bot_login: Optional[str] = None) -> None:
        self.repo = repo
        self._run = runner
        self._p = project_param(repo)
        self.bot_login = bot_login if bot_login is not None else os.environ.get("REFINE_BOT_LOGIN", "")
        self.open_truncated = False
        self.closed_truncated = False
        self._levels: Optional[Dict[str, int]] = None

    def get(self, path: str) -> List[Any]:
        return json_stream(self._run(["api", "--paginate", path]), path)

    def trusted(self, login: str) -> bool:
        if self._levels is None:
            self._levels = member_levels(self.get(f"projects/{self._p}/members/all?per_page=100"))
        return bool(login) and (login == self.bot_login or self._levels.get(login, 0) >= TRUSTED_ACCESS)

    def _comments(self, iid: int) -> List[Comment]:
        notes = self.get(f"projects/{self._p}/issues/{iid}/notes?per_page=100&sort=asc&order_by=created_at")
        return [Comment(body=str(n.get("body") or ""), created_at=iso(n.get("created_at")),
                        trusted=self.trusted(username(n.get("author"))))
                for n in notes if isinstance(n, dict) and not n.get("system")]

    def _blockers(self, iid: int, project_id: Any) -> List[Blocker]:
        links = self.get(f"projects/{self._p}/issues/{iid}/links")
        return [Blocker(number=int(link["iid"]), state=gitlab_state(link.get("state")))
                for link in links
                if isinstance(link, dict) and link.get("link_type") == "is_blocked_by"
                and link.get("project_id") == project_id and isinstance(link.get("iid"), int)]

    def issue(self, raw: Json, detail: bool) -> Issue:
        """One issue row as an `Issue`; `detail` reads its comments and blocked-by links too."""
        iid = int(raw["iid"])
        return Issue(
            number=iid,
            title=str(raw.get("title") or ""),
            body=str(raw.get("description") or ""),
            state=gitlab_state(raw.get("state")),
            state_reason=None,
            labels=sorted(str(lab) for lab in raw.get("labels") or []),
            created_at=iso(raw.get("created_at")),
            closed_at=iso(raw.get("closed_at")) or None,
            url=str(raw.get("web_url") or ""),
            blocked_by=self._blockers(iid, raw.get("project_id")) if detail else [],
            sub_issues_total=0,
            comments=self._comments(iid) if detail else [],
        )

    def _rows(self, path: str) -> Tuple[List[Json], bool]:
        rows = [r for r in self.get(path) if isinstance(r, dict)]
        return rows[:LIST_LIMIT], len(rows) > LIST_LIMIT

    def open_issues(self) -> List[Issue]:
        rows, self.open_truncated = self._rows(f"projects/{self._p}/issues?state=opened&per_page=100")
        return [self.issue(r, detail=True) for r in rows]

    def closed_issues_since(self, since: date) -> List[Issue]:
        # GitLab filters by update time only, and closing an issue updates it, so this is a superset.
        rows, self.closed_truncated = self._rows(
            f"projects/{self._p}/issues?state=closed&per_page=100&updated_after={since.isoformat()}T00:00:00Z")
        issues = [self.issue(r, detail=False) for r in rows]
        return [i for i in issues if (i["closed_at"] or "") >= since.isoformat()]

    def open_pull_requests(self) -> List[PullRequest]:
        rows, _ = self._rows(f"projects/{self._p}/merge_requests?state=opened&per_page=100")
        out: List[PullRequest] = []
        for mr in rows:
            iid = int(mr["iid"])
            closes = self.get(f"projects/{self._p}/merge_requests/{iid}/closes_issues")
            diffs = self.get(f"projects/{self._p}/merge_requests/{iid}/diffs?per_page=100")
            files = {str(d.get(k)) for d in diffs if isinstance(d, dict) for k in ("new_path", "old_path") if d.get(k)}
            out.append(PullRequest(
                number=iid, title=str(mr.get("title") or ""), files=sorted(files),
                closes=sorted(int(c["iid"]) for c in closes
                              if isinstance(c, dict) and isinstance(c.get("iid"), int)
                              and c.get("project_id") == mr.get("project_id"))))
        return out
