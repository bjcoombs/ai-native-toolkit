"""Guarded refiner writes on GitLab: `refine_write.Writer` over `glab api` (issue #1327).

`GitLabWriter` overrides only the host calls of `refine_write.Writer`; every
rule (the label allow-list, the person's veto, the ready-label rule, the
weekly close cap, the call caps and dry-run) is the GitHub writer's own code,
run unchanged. `refine_write.make_writer` picks this class when
`REFINE_PLATFORM` is `gitlab`, so the same single-purpose write scripts
(`edit-issue-labels.sh` and the rest) serve both hosts.

The reads map GitLab's JSON onto the GitHub REST shapes the rules read:

| Writer read | GitLab source | Mapped to |
|---|---|---|
| `issue` | `issues/:iid` | `number`, `state` `open`/`closed`, `body`, `labels` as `{"name"}`, `user`, `closed_by`; `state_reason` is `duplicate` when the refiner's bot closed it and left its close-marker note |
| `events` | `resource_label_events`, `resource_state_events` | `unlabeled`/`labeled` with the label, `closed`/`reopened` |
| `comments` | `notes`, system notes dropped | `user`, `body`, `author_association` `MEMBER` for a Reporter or above |
| `repo_events_page` | project `events?action=closed&target_type=issue` | `closed` events with the issue number, for the weekly close cap |
| `blocked_by` | `issues/:iid/links` of type `is_blocked_by` in the same project | issue numbers |

The writes: labels by `PUT issues/:iid` with `add_labels` and
`remove_labels`; comments by `POST issues/:iid/notes`; the duplicate close and
reopen by `state_event`; a blocked-by edge by `POST issues/:iid/links` with
`link_type=is_blocked_by` (linked `blocks` issues need GitLab Premium).

**Quick actions.** GitLab runs a quick action (`/close`, `/label`, ...) on
any note line that starts with `/`, including a note posted through the API.
`rendered` puts a zero-width space before such a slash, so model or issue text
inside a refiner comment can never run one.

**The bot.** `REFINE_BOT_LOGIN` names the refiner's user; unset, it is read
from the write token (`GET user`), so the close cap and the veto always know
which closes and label removals are the refiner's own.

**`/reopen`.** GitLab runs `/reopen` itself as a quick action, so there is no
reopen job on GitLab; a reopened issue still carries the `reopened` state
event that stops the refiner closing it again.

Python 3.8 compatible and stdlib only, plus `glab` on PATH.
"""
from __future__ import annotations

import re
import subprocess
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Set

from refine_platform_gitlab import TRUSTED_ACCESS, gitlab_state, iso, member_levels, project_param, username
from refine_write import CLOSE_MARKER, GhError, Json, Runner, Settings, Writer, json_values

# A line whose first non-blank character is `/` is a quick action on GitLab.
_QUICK_ACTION = re.compile(r"^(\s*)/", re.MULTILINE)
_TOKEN_BOT = re.compile(r"^(?:project|group)_\d+_bot(?:_\w+)?$")


def glab_runner(args: Sequence[str]) -> str:
    """Run `glab` with `args` and return its stdout."""
    try:
        proc = subprocess.run(["glab", *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", check=False)
    except OSError as exc:
        raise GhError(f"glab could not start: {exc}") from exc
    if proc.returncode != 0:
        raise GhError(f"glab {' '.join(args[:3])} failed: {proc.stderr.strip()}")
    return proc.stdout


def neutralise_quick_actions(body: str) -> str:
    return _QUICK_ACTION.sub("\\1\u200b/", body)


class GitLabWriter(Writer):
    """`Writer` with GitLab host calls. `runner` is injectable for tests."""

    def __init__(self, settings: Settings, runner: Runner = glab_runner, now: Optional[datetime] = None) -> None:
        super().__init__(settings, runner, now)
        self.p = project_param(settings.repo)
        self._levels: Optional[Dict[str, int]] = None
        self._project_id: Optional[int] = None
        if not settings.bot_login_set:
            me = self._read(["api", "user"])
            login = username(me)
            if not login:
                raise GhError("glab api user returned no username; set REFINE_BOT_LOGIN")
            self.s.bot_login = login

    # ── mapping ──────────────────────────────────────────────────────────

    def _user(self, user: Any) -> Json:
        login = username(user)
        bot = login == self.s.bot_login or bool(_TOKEN_BOT.match(login)) or (isinstance(user, dict) and user.get("bot") is True)
        return {"login": login, "type": "Bot" if bot else "User"}

    def _level(self, login: str) -> int:
        if self._levels is None:
            rows = json_values(self.run(["api", "--paginate", f"projects/{self.p}/members/all?per_page=100"]))
            self._levels = member_levels(rows)
        return self._levels.get(login, 0)

    def project_id(self) -> int:
        if self._project_id is None:
            raw = self._read(["api", f"projects/{self.p}"])
            if not isinstance(raw, dict) or not isinstance(raw.get("id"), int):
                raise GhError(f"project {self.s.repo}: unexpected response")
            self._project_id = int(raw["id"])
        return self._project_id

    def _get_list(self, path: str) -> List[Json]:
        return [v for v in json_values(self.run(["api", "--paginate", path])) if isinstance(v, dict)]

    # ── reads ────────────────────────────────────────────────────────────

    def issue(self, number: int) -> Json:
        raw = self._read(["api", f"projects/{self.p}/issues/{number}"])
        if not isinstance(raw, dict) or "iid" not in raw:
            raise GhError(f"issue #{number}: unexpected response")
        state = gitlab_state(raw.get("state"))
        closed_by = self._user(raw.get("closed_by")) if raw.get("closed_by") else None
        reason = None
        if state == "closed" and closed_by is not None and closed_by["login"] == self.s.bot_login:
            if any(CLOSE_MARKER in str(c.get("body", "")) and self._is_bot(c.get("user")) for c in self.comments(number)):
                reason = "duplicate"
        return {"number": int(raw["iid"]), "id": raw.get("id"), "project_id": raw.get("project_id"),
                "title": str(raw.get("title") or ""), "body": str(raw.get("description") or ""),
                "state": state, "state_reason": reason, "closed_by": closed_by, "user": self._user(raw.get("author")),
                "labels": [{"name": str(name)} for name in raw.get("labels") or []]}

    def events(self, number: int) -> List[Json]:
        out: List[Json] = []
        for e in self._get_list(f"projects/{self.p}/issues/{number}/resource_label_events?per_page=100"):
            action = {"add": "labeled", "remove": "unlabeled"}.get(str(e.get("action")))
            if action and isinstance(e.get("label"), dict):
                out.append({"event": action, "actor": self._user(e.get("user")),
                            "label": {"name": str(e["label"].get("name") or "")}, "created_at": iso(e.get("created_at"))})
        for e in self._get_list(f"projects/{self.p}/issues/{number}/resource_state_events?per_page=100"):
            if e.get("state") in ("closed", "reopened"):
                out.append({"event": str(e["state"]), "actor": self._user(e.get("user")),
                            "created_at": iso(e.get("created_at"))})
        return out

    def comments(self, number: int) -> List[Json]:
        notes = self._get_list(f"projects/{self.p}/issues/{number}/notes?per_page=100&sort=asc&order_by=created_at")
        out: List[Json] = []
        for n in notes:
            if n.get("system"):
                continue
            login = username(n.get("author"))
            trusted = self._level(login) >= TRUSTED_ACCESS
            out.append({"id": n.get("id"), "body": str(n.get("body") or ""), "user": self._user(n.get("author")),
                        "author_association": "MEMBER" if trusted else "NONE", "created_at": iso(n.get("created_at"))})
        return out

    def repo_events_page(self, page: int) -> List[Json]:
        rows = self._get_list_page(f"projects/{self.p}/events?action=closed&target_type=issue&per_page=100&page={page}")
        return [{"event": "closed", "actor": {"login": str(e.get("author_username") or username(e.get("author")))},
                 "issue": {"number": e.get("target_iid")}, "state_reason": None, "created_at": iso(e.get("created_at"))}
                for e in rows]

    def _get_list_page(self, path: str) -> List[Json]:
        return [v for v in json_values(self.run(["api", path])) if isinstance(v, dict)]

    def blocked_by(self, number: int) -> Set[int]:
        return {int(link["iid"]) for link in self._get_list(f"projects/{self.p}/issues/{number}/links")
                if link.get("link_type") == "is_blocked_by" and link.get("project_id") == self.project_id()
                and isinstance(link.get("iid"), int)}

    def is_writer(self, login: str) -> bool:
        return self._level(login) >= TRUSTED_ACCESS

    # ── writes ───────────────────────────────────────────────────────────

    def rendered(self, body: str) -> str:
        return neutralise_quick_actions(body)

    def labels_args(self, number: int, add: Sequence[str], remove: Sequence[str]) -> List[str]:
        args = ["api", "-X", "PUT", f"projects/{self.p}/issues/{number}"]
        if add:
            args += ["-f", "add_labels=" + ",".join(add)]
        if remove:
            args += ["-f", "remove_labels=" + ",".join(remove)]
        return args

    def comment_args(self, number: int, body: str) -> List[str]:
        return ["api", "-X", "POST", f"projects/{self.p}/issues/{number}/notes", "-f", f"body={self.rendered(body)}"]

    def comment_update_args(self, number: int, comment_id: int, body: str) -> List[str]:
        return ["api", "-X", "PUT", f"projects/{self.p}/issues/{number}/notes/{comment_id}",
                "-f", f"body={self.rendered(body)}"]

    def close_args(self, number: int) -> List[str]:
        return ["api", "-X", "PUT", f"projects/{self.p}/issues/{number}", "-f", "state_event=close"]

    def reopen_args(self, number: int) -> List[str]:
        return ["api", "-X", "PUT", f"projects/{self.p}/issues/{number}", "-f", "state_event=reopen"]

    def blocked_by_args(self, number: int, blocker: Json) -> List[str]:
        return ["api", "-X", "POST", f"projects/{self.p}/issues/{number}/links",
                "-f", f"target_project_id={self.project_id()}", "-f", f"target_issue_iid={int(blocker['number'])}",
                "-f", "link_type=is_blocked_by"]
