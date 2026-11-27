"""The GitLab half of the issue-outcome read for the backlog-ready practice, level 2 (#1323).

``lib.hosting.issue_outcomes`` documents the read; this module makes it on GitLab through
``glab api`` with ``--hostname`` and the project in every argv.

- **Issues touched in the window**: ``projects/<id>/issues?updated_after=<start>`` (newest first,
  ``GITLAB_PAGE`` a page, at most ``MAX_PAGES`` pages).
- **First response** to each issue opened in the window: its notes, oldest first, one page of
  ``GITLAB_PAGE`` (``cache=False``, so no note body reaches the response cache), read only when
  ``user_notes_count`` is above 0 and at most ``MAX_NOTE_READS`` issues a run; and its close,
  by ``closed_by`` at ``closed_at``. A full page leaves the response unread past its last note:
  page 2 may hold an earlier one than a later close (#1587). System notes are skipped. The author's own notes are no
  response. A bot is no response: ``bot: true`` on the user object, a project or group
  access-token user (``project_<id>_bot_<hash>``), or a ``users/<id>`` read that says
  ``bot: true`` (at most ``MAX_USER_PROBES`` probes; an account still unclassified leaves the
  issue's response unknown, never a person).
- **Open issues older than 7 days**: ``projects/<id>/issues?state=opened&created_before=<cutoff>``,
  labels only.

Both issue lists are read with ``cache=False`` too: GitLab returns every issue's title and
description, so neither list reaches the ``--platform`` response cache (#778).

An issue whose notes were not read (the cap), whose single page was full with no response on it
before its last note (a note on page 2 may come before a later close, #1587), or whose responder
could not be classified has ``response_read`` False, and the read is
``truncated`` when a cap was hit. A 403 on any of these reads raises ``no_access``, which the
caller reads as ``no_platform_auth``.

`gitlab_read(project, start, now)` builds the `issue_outcomes.IssueRead` for a GitLab project as
above; `GitlabBots` classifies note and close authors. Tests: `tests/test_backlog_ready_platform.py`.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from lib.hosting.gitlab_cli import GitlabProject, glab_api
from lib.hosting.gh_cli import GhUnavailable
from lib.backlog_issue_read import UNTRIAGED_AFTER, Issue, IssueRead
from lib.hosting.issue_outcomes import MAX_PAGES
from lib.hosting.merged_sample import parse_time

GITLAB_PAGE = 100
MAX_NOTE_READS = 200
MAX_USER_PROBES = 30

# GitLab's generated project and group access-token users.
_TOKEN_USER = re.compile(r"^(?:project|group)_\d+_bot(?:_[0-9a-f]+)?$")


def _stamp(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


class GitlabBots:
    """Whether a GitLab user is a bot; one ``users/<id>`` probe per unmarked account."""

    def __init__(self, project: GitlabProject) -> None:
        self._project = project
        self._known: dict[int, bool | None] = {}
        self._probes = 0
        self.capped = False

    def is_bot(self, user: Any) -> bool | None:
        if not isinstance(user, dict):
            return None
        if isinstance(user.get("bot"), bool):
            return bool(user["bot"])
        name = user.get("username")
        if isinstance(name, str) and (_TOKEN_USER.match(name) or name.lower().endswith("[bot]")):
            return True
        uid = user.get("id")
        if not isinstance(uid, int) or isinstance(uid, bool):
            return None
        if uid not in self._known:
            self._known[uid] = self._probe(uid)
        return self._known[uid]

    def _probe(self, uid: int) -> bool | None:
        if self._probes >= MAX_USER_PROBES:
            self.capped = True
            return None
        self._probes += 1
        try:
            found = glab_api(self._project, f"users/{uid}")
        except GhUnavailable:
            return None
        flag = found.get("bot") if isinstance(found, dict) else None
        return flag if isinstance(flag, bool) else None


def _user_key(user: Any) -> object:
    if not isinstance(user, dict):
        return None
    return user.get("id") if user.get("id") is not None else user.get("username")


def _pages(project: GitlabProject, query: str) -> tuple[list[dict[str, Any]], bool]:
    got: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        # cache=False: GitLab returns each issue's title and description, read in memory only (#778).
        rows = glab_api(project, f"projects/{project.encoded}/issues?{query}&per_page={GITLAB_PAGE}&page={page}",
                        cache=False)
        if not isinstance(rows, list):
            raise GhUnavailable("glab_bad_json: the issues list is not a list")
        got += [r for r in rows if isinstance(r, dict)]
        if len(rows) < GITLAB_PAGE:
            return got, False
    return got, True


def _first_response(project: GitlabProject, issue: dict[str, Any], bots: GitlabBots,
                    read_notes: bool) -> tuple[datetime | None, bool]:
    """The first response to one issue and whether it is settled (see the module doc)."""
    author = _user_key(issue.get("author"))
    events: list[tuple[datetime, Any]] = []
    closed = parse_time(issue.get("closed_at"))
    if closed is not None and issue.get("closed_by"):
        events.append((closed, issue["closed_by"]))
    # past this time an unread note may come first: a full page leaves page 2 unread, and its
    # notes (oldest first) are no earlier than the last note on page 1
    horizon: datetime | None = None
    count = issue.get("user_notes_count")
    if not isinstance(count, int) or count > 0:
        if not read_notes:  # an unread note may come before the close
            return None, False
        iid = issue.get("iid")
        notes = glab_api(project, f"projects/{project.encoded}/issues/{iid}/notes?sort=asc"
                         f"&order_by=created_at&per_page={GITLAB_PAGE}", cache=False)
        notes = notes if isinstance(notes, list) else []
        stamps = [parse_time(note.get("created_at")) if isinstance(note, dict) else None for note in notes]
        for note, when in zip(notes, stamps):
            if when is not None and note.get("system") is not True:
                events.append((when, note.get("author")))
        if len(notes) >= GITLAB_PAGE:
            read = [when for when in stamps if when is not None]
            if not read:
                return None, False
            horizon = max(read)
    for when, user in sorted(events, key=lambda pair: pair[0]):
        if horizon is not None and when > horizon:
            return None, False  # an unread note on page 2 may come first
        if _user_key(user) == author:
            continue
        verdict = bots.is_bot(user)
        if verdict is None:
            return None, False
        if verdict is False:
            return when, True
    return None, horizon is None


def gitlab_read(project: GitlabProject, start: datetime, now: datetime) -> IssueRead:
    """The issues of a GitLab project (see the module doc)."""
    rows, cut_window = _pages(project, f"updated_after={_stamp(start)}&order_by=created_at&sort=desc")
    cut = cut_window
    bots = GitlabBots(project)
    recent: list[Issue] = []
    reads = 0
    for row in rows:
        created = parse_time(row.get("created_at"))
        if created is None:
            raise GhUnavailable("glab_bad_json: an issue has no created_at")
        first: datetime | None = None
        settled = True
        if created >= start:
            count = row.get("user_notes_count")
            wants = not isinstance(count, int) or count > 0
            first, settled = _first_response(project, row, bots, reads < MAX_NOTE_READS)
            reads += 1 if wants else 0
            cut = cut or (wants and reads > MAX_NOTE_READS)
        closed = parse_time(row.get("closed_at"))
        closer = row.get("closed_by")
        key = _user_key(closer)  # an unkeyed closer is no one: never the author
        self_closed = closed is not None and key is not None and key == _user_key(row.get("author"))
        recent.append(Issue(created, closed, (), first, settled, self_closed=self_closed))
    open_rows, cut_open = _pages(project, f"state=opened&created_before={_stamp(now - UNTRIAGED_AFTER)}")
    open_old = tuple(Issue(created, None, tuple(str(x).lower() for x in r.get("labels") or []
                                                if isinstance(x, str)))
                     for r in open_rows if (created := parse_time(r.get("created_at"))) is not None)
    return IssueRead("gitlab", start, now, tuple(recent), open_old, cut or cut_open or bots.capped,
                     cut_window, cut_open)
