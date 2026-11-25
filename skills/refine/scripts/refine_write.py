#!/usr/bin/env python3
"""Guarded writes for the refiner workflows (issue #1321).

Every change the refiner makes to an issue tracker goes through one of the
single-purpose shell scripts next to this file, and each of them runs one
subcommand of this module. The model never writes: it returns a JSON plan,
and `refine_apply.py` turns that plan into calls to these functions. The
rules below are enforced here, in code, whatever the plan says:

- **Label allow-list.** Only labels named in `REFINE_ALLOWED_LABELS` are
  added or removed. The refiner never removes the ready label, and removes
  the triage label only when it adds the ready label.
- **Never re-apply a removed label.** A label a person (not the bot) removed
  from an issue is never added back, including the triage label on
  `/reopen`. A vetoed label does not use a call from the cap.
- **The ready label** (`agent-ready` by default) is added only when the
  refinement script's verdict for the issue has no failing check, no open-PR
  overlap and no exact duplicate, and every judgement item is resolved by
  the plan with no open question. The size is read again just before the
  label goes on (`current_size`): it must still be present when the verdict
  had one, at most `REFINE_MAX_POINTS`, and equal to the plan's size when
  the plan sized the issue. An issue the verdict found `unsized` must read
  a size by then, or have been sized by this run: a marker this run posted
  (live) or recorded (dry-run) stands in when the re-read finds none, so a
  read that lags the write does not withhold the label. `refine_apply.apply_triage` also withholds the
  label when a required size write or any planned blocked-by edge was
  refused or failed. Adding it removes the triage label.
- **Duplicate closes.** Only a pair the refinement script reported as
  `exact` and `closable`, with both issues still open, their title and body
  still equal under `refine_duplicates.normalise` when read fresh, the
  duplicate never reopened by a person, and fewer than `REFINE_CLOSE_CAP`
  duplicate closes by `REFINE_BOT_LOGIN` in the last 7 days, each close
  counted once. The close comment links the surviving issue and names the
  `/reopen` route.
- **Sizes.** A size marker goes only on an issue the refinement script
  reported `unsized` and that still reads unsized now, so a size a person
  wrote, before or after the verdict, is never overridden. The current size
  is the newest trusted comment whose first line matches the definition's
  `size_marker`, else the last body line matching its `size_pattern`; both
  patterns come from the verdict's `definition` block, so the writer reads a
  size the way `refine.py` did.
- **Questions** for the maintainer go into one comment per run on the issue
  named by `REFINE_QUESTIONS_ISSUE`, skipped when the refiner already posted
  the same comment there.
- **Call caps.** `CLAUDE_CODE_SCRIPT_CAPS` (a JSON object, script name to
  count) caps how many times each write script runs in one job. When it is
  set, a script it does not name is refused.
- **Dry-run.** Unless `REFINE_MODE` is `live`, every write is recorded in the
  actions log (`REFINE_ACTIONS_LOG`, JSON lines) and none is sent. Reads and
  guardrails run the same way in both modes, so a dry-run shows what live
  mode would do.

Exit codes: 0 done (individual labels may have been dropped, listed on
stderr); 2 usage error; 3 `gh` failed; 4 a guardrail refused the call.

Python 3.8 compatible and stdlib only, plus `gh` on PATH, or `glab` when
`REFINE_PLATFORM` is `gitlab` (`refine_write_gitlab.GitLabWriter`, #1327).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from refine_duplicates import normalise

Runner = Callable[[Sequence[str]], str]
Json = Dict[str, Any]

DEFAULT_READY = "agent-ready"
DEFAULT_TRIAGE = "needs-triage"
DEFAULT_BOT = "github-actions[bot]"
DEFAULT_CLOSE_CAP = 10
DEFAULT_MAX_POINTS = 3
DEFAULT_SIZE_PATTERN = r"Size: (\d+) points?"
DEFAULT_SIZE_MARKER = r"<!-- refine:size (\d+) -->"
CLOSE_WINDOW_DAYS = 7
EVENT_PAGES = 10
FIBONACCI = (1, 2, 3, 5, 8, 13, 21)
MAX_CANDIDATES = 3
MAX_TEXT = 500
WRITE_ROLES = ("admin", "maintain", "write")
TRUSTED_ASSOCIATIONS = ("OWNER", "MEMBER", "COLLABORATOR")
TRUSTED_BOTS = ("github-actions", "github-actions[bot]")

SIZE_MARKER = "<!-- refine:size {points} -->"
DUPLICATES_MARKER = "<!-- refine:duplicates -->"
CLOSE_MARKER = "<!-- refine:closed-duplicate -->"
QUESTIONS_MARKER = "<!-- refine:questions -->"
REPORT_MARKER = "<!-- refine:ready-buffer -->"
REOPEN_MARKER = "<!-- refine:reopened -->"


class Refused(Exception):
    """A guardrail refused the write; the message says which rule."""


class GhError(RuntimeError):
    """`gh` exited non-zero or returned output that is not JSON."""


def gh_runner(args: Sequence[str]) -> str:
    """Run `gh` with `args` and return its stdout."""
    try:
        proc = subprocess.run(["gh", *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", check=False)
    except OSError as exc:
        raise GhError(f"gh could not start: {exc}") from exc
    if proc.returncode != 0:
        raise GhError(f"gh {' '.join(args[:3])} failed: {proc.stderr.strip()}")
    return proc.stdout


def json_values(text: str) -> List[Any]:
    """Every top-level JSON value in `text`, with arrays flattened.

    `gh api --paginate` prints one array per page back to back, so the output
    is a stream of values rather than one document.
    """
    decoder = json.JSONDecoder()
    out: List[Any] = []
    pos = 0
    text = text.strip()
    while pos < len(text):
        try:
            value, end = decoder.raw_decode(text, pos)
        except ValueError as exc:
            raise GhError(f"gh returned output that is not JSON: {exc}") from exc
        if isinstance(value, list):
            out.extend(value)
        else:
            out.append(value)
        pos = end
        while pos < len(text) and text[pos].isspace():
            pos += 1
    return out


def neutralise(text: str) -> str:
    """Model or issue text made safe to post: one paragraph, no mentions, bounded."""
    flat = " ".join(text.split())
    flat = flat.replace("@", "@​")
    return flat[:MAX_TEXT]


def _csv(value: str) -> List[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


class Settings:
    """The refiner's settings, read from the environment the workflow sets."""

    def __init__(self, env: Optional[Dict[str, str]] = None) -> None:
        e = dict(os.environ if env is None else env)
        self.platform = e.get("REFINE_PLATFORM") or "github"  # "gitlab": refine_write_gitlab.GitLabWriter
        self.repo = e.get("GH_REPO") or e.get("GITHUB_REPOSITORY") or e.get("CI_PROJECT_PATH") or ""
        self.live = e.get("REFINE_MODE", "") == "live"
        self.ready_label = e.get("REFINE_READY_LABEL") or DEFAULT_READY
        self.triage_label = e.get("REFINE_TRIAGE_LABEL") or DEFAULT_TRIAGE
        self.allowed_labels: Set[str] = set(_csv(e.get("REFINE_ALLOWED_LABELS", "")))
        self.allowed_labels |= {self.ready_label, self.triage_label}
        self.bot_login = e.get("REFINE_BOT_LOGIN") or DEFAULT_BOT
        self.bot_login_set = bool(e.get("REFINE_BOT_LOGIN"))
        self.close_cap = int(e.get("REFINE_CLOSE_CAP") or DEFAULT_CLOSE_CAP)
        self.max_points = int(e.get("REFINE_MAX_POINTS") or DEFAULT_MAX_POINTS)
        q = e.get("REFINE_QUESTIONS_ISSUE", "").strip().lstrip("#")
        self.questions_issue: Optional[int] = int(q) if q.isdigit() else None
        caps = e.get("CLAUDE_CODE_SCRIPT_CAPS", "").strip()
        self.caps: Optional[Dict[str, int]] = None
        if caps:
            parsed = json.loads(caps)
            if not isinstance(parsed, dict):
                raise ValueError("CLAUDE_CODE_SCRIPT_CAPS must be a JSON object")
            self.caps = {str(k): int(v) for k, v in parsed.items()}
        base = e.get("REFINE_STATE_DIR") or str(Path(e.get("RUNNER_TEMP") or tempfile.gettempdir()) / "refine")
        self.state_dir = Path(base)
        self.actions_log = Path(e.get("REFINE_ACTIONS_LOG") or str(self.state_dir / "actions.jsonl"))


class Writer:
    """The guarded write operations. `runner` is injectable for tests."""

    def __init__(self, settings: Settings, runner: Runner = gh_runner,
                 now: Optional[datetime] = None) -> None:
        if not settings.repo:
            raise ValueError("no repository: set GH_REPO or GITHUB_REPOSITORY")
        self.s = settings
        self.run = runner
        self.now = now or datetime.now(timezone.utc)
        self.closed_this_run: Set[int] = set()
        self.sized_this_run: Dict[int, int] = {}
        self.failures = 0

    # ── plumbing ─────────────────────────────────────────────────────────

    def _read(self, args: Sequence[str]) -> Any:
        values = json_values(self.run(args))
        return values[0] if len(values) == 1 else values

    def _record(self, entry: Json) -> None:
        self.s.actions_log.parent.mkdir(parents=True, exist_ok=True)
        with self.s.actions_log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")

    def _write(self, script: str, action: str, issue: int, args: Sequence[str], detail: str) -> None:
        """Send one write, or only record it in dry-run."""
        self._record({"mode": "live" if self.s.live else "dry-run", "script": script,
                      "action": action, "issue": issue, "detail": detail})
        if self.s.live:
            self.run(args)

    def refuse(self, script: str, issue: int, reason: str) -> Refused:
        self._record({"mode": "live" if self.s.live else "dry-run", "script": script,
                      "action": "refused", "issue": issue, "detail": reason})
        return Refused(f"{script}: {reason}")

    def take_cap(self, script: str) -> None:
        """Count one run of `script` against CLAUDE_CODE_SCRIPT_CAPS."""
        if self.s.caps is None:
            return
        path = self.s.state_dir / "script-calls.json"
        counts: Dict[str, int] = {}
        if path.is_file():
            counts = {str(k): int(v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()}
        limit = self.s.caps.get(script)
        if limit is None:
            raise self.refuse(script, 0, "not named in CLAUDE_CODE_SCRIPT_CAPS")
        used = counts.get(script, 0)
        if used >= limit:
            raise self.refuse(script, 0, f"call cap reached ({used} of {limit})")
        counts[script] = used + 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(counts, sort_keys=True), encoding="utf-8")

    def issue(self, number: int) -> Json:
        data = self._read(["api", f"repos/{self.s.repo}/issues/{number}"])
        if not isinstance(data, dict):
            raise GhError(f"issue #{number}: unexpected response")
        return data

    def events(self, number: int) -> List[Json]:
        data = json_values(self.run(["api", "--paginate", f"repos/{self.s.repo}/issues/{number}/events?per_page=100"]))
        return [e for e in data if isinstance(e, dict)]

    def comments(self, number: int) -> List[Json]:
        data = json_values(self.run(["api", "--paginate", f"repos/{self.s.repo}/issues/{number}/comments?per_page=100"]))
        return [c for c in data if isinstance(c, dict)]

    # ── host calls: GitHub here, GitLab in refine_write_gitlab.GitLabWriter ─
    # The reads return GitHub's REST shapes; the GitLab writer maps its host's
    # JSON onto them, so every rule above and below reads one shape.

    def repo_events_page(self, page: int) -> List[Json]:
        """One page of the repository's issue events, newest first."""
        return [e for e in json_values(self.run(["api", f"repos/{self.s.repo}/issues/events?per_page=100&page={page}"]))
                if isinstance(e, dict)]

    def blocked_by(self, number: int) -> Set[int]:
        """The issues `number` is blocked by now."""
        data = json_values(self.run(["api", f"repos/{self.s.repo}/issues/{number}/dependencies/blocked_by"]))
        return {int(i["number"]) for i in data if isinstance(i, dict) and isinstance(i.get("number"), int)}

    def is_writer(self, login: str) -> bool:
        perm = self._read(["api", f"repos/{self.s.repo}/collaborators/{login}/permission"])
        return str((perm or {}).get("role_name") or (perm or {}).get("permission") or "") in WRITE_ROLES

    def rendered(self, body: str) -> str:
        """`body` as the host stores it, for comparing with a comment read back."""
        return body

    def labels_args(self, number: int, add: Sequence[str], remove: Sequence[str]) -> List[str]:
        args = ["issue", "edit", str(number), "--repo", self.s.repo]
        if add:
            args += ["--add-label", ",".join(add)]
        if remove:
            args += ["--remove-label", ",".join(remove)]
        return args

    def comment_args(self, number: int, body: str) -> List[str]:
        return ["issue", "comment", str(number), "--repo", self.s.repo, "--body", self.rendered(body)]

    def comment_update_args(self, number: int, comment_id: int, body: str) -> List[str]:
        return ["api", "-X", "PATCH", f"repos/{self.s.repo}/issues/comments/{comment_id}", "-f",
                f"body={self.rendered(body)}"]

    def close_args(self, number: int) -> List[str]:
        return ["api", "-X", "PATCH", f"repos/{self.s.repo}/issues/{number}",
                "-f", "state=closed", "-f", "state_reason=duplicate"]

    def reopen_args(self, number: int) -> List[str]:
        return ["api", "-X", "PATCH", f"repos/{self.s.repo}/issues/{number}",
                "-f", "state=open", "-f", "state_reason=reopened"]

    def blocked_by_args(self, number: int, blocker: Json) -> List[str]:
        return ["api", "-X", "POST", f"repos/{self.s.repo}/issues/{number}/dependencies/blocked_by",
                "-F", f"issue_id={int(blocker['id'])}"]

    def _is_bot(self, actor: Any) -> bool:
        if not isinstance(actor, dict):
            return False
        return actor.get("login") == self.s.bot_login or actor.get("type") == "Bot"

    def _trusted_sizer(self, comment: Json) -> bool:
        """A size marker counts from a writer or the refiner's bot, as in `refine_platform._trusted`."""
        login = str((comment.get("user") or {}).get("login") or "")
        return comment.get("author_association") in TRUSTED_ASSOCIATIONS or login in (self.s.bot_login, *TRUSTED_BOTS)

    def removed_by_person(self, number: int) -> Set[str]:
        """Labels a person (not a bot) has removed from the issue."""
        out: Set[str] = set()
        for e in self.events(number):
            if e.get("event") == "unlabeled" and not self._is_bot(e.get("actor")):
                name = (e.get("label") or {}).get("name")
                if name:
                    out.add(str(name))
        return out

    def current_size(self, number: int, verdict: Optional[Json], info: Optional[Json] = None) -> Optional[int]:
        """The size as it reads now: the newest trusted marker, else the last body size line."""
        pattern, marker = size_rules(verdict)
        latest: Optional[int] = None
        for c in self.comments(number):
            if not self._trusted_sizer(c):
                continue
            match = marker.fullmatch(str(c.get("body", "")).split("\n", 1)[0].strip())
            if match:
                latest = int(match.group(1))
        if latest is not None:
            return latest
        body = str((info if info is not None else self.issue(number)).get("body") or "")
        found = list(pattern.finditer(body))
        return int(found[-1].group(1)) if found else None

    def size_drift(self, number: int, info: Optional[Json], verdict: Optional[Json], plan: Optional[Json]) -> str:
        """Why the size as it reads now no longer supports the ready label, or ""."""
        entry = verdict_entry(verdict, number) or {}
        current = self.current_size(number, verdict, info)
        planned = (plan or {}).get("size") if entry.get("unsized") is True else None
        if current is None:
            current = self.sized_this_run.get(number)
        if current is None:
            if entry.get("size_points") is not None or entry.get("unsized") is True:
                return "the issue reads no size"
            return ""
        if current > self.s.max_points:
            return f"the size now reads {current}, above {self.s.max_points}"
        if isinstance(planned, int) and current != planned:
            return f"the size now reads {current}, not the plan's {planned}"
        return ""

    # ── edit-issue-labels.sh ─────────────────────────────────────────────

    def _screen_labels(self, add: Sequence[str], remove: Sequence[str]) -> Tuple[List[str], List[str], List[str]]:
        """Drop labels off the allow-list, and the ready label from removals."""
        refused = [f"{label}: not on the label allow-list"
                   for label in list(dict.fromkeys(add)) + list(dict.fromkeys(remove))
                   if label not in self.s.allowed_labels]
        to_add = [a for a in dict.fromkeys(add) if a in self.s.allowed_labels]
        to_remove = [r for r in dict.fromkeys(remove) if r in self.s.allowed_labels]
        if self.s.ready_label in to_remove:
            refused.append(f"{self.s.ready_label}: only a person removes it")
            to_remove.remove(self.s.ready_label)
        if self.s.triage_label in to_remove:
            refused.append(f"{self.s.triage_label}: removed only when the ready label goes on")
            to_remove.remove(self.s.triage_label)
        return to_add, to_remove, refused

    def _apply_ready_rule(self, number: int, to_add: List[str], to_remove: List[str], info: Json,
                          verdict: Optional[Json], plan: Optional[Json]) -> Optional[str]:
        """Keep the ready label in `to_add` only when the rule allows it; return the refusal."""
        if self.s.ready_label not in to_add:
            return None
        if self.s.ready_label in _label_names(info):
            to_add.remove(self.s.ready_label)
            return None
        reason = ready_blocker(number, verdict, plan, self.s.max_points) or self.size_drift(number, info, verdict, plan)
        if reason:
            to_add.remove(self.s.ready_label)
            return f"{self.s.ready_label}: {reason}"
        if self.s.triage_label not in to_remove:
            to_remove.append(self.s.triage_label)
        if self.s.triage_label in to_add:
            to_add.remove(self.s.triage_label)
        return None

    def edit_labels(self, number: int, add: Sequence[str], remove: Sequence[str],
                    verdict: Optional[Json] = None, plan: Optional[Json] = None) -> Json:
        """Add and remove labels under the allow-list, the veto and the ready rule."""
        script = "edit-issue-labels.sh"
        to_add, to_remove, refused = self._screen_labels(add, remove)
        info = self.issue(number)
        if info.get("state") != "open":
            raise self.refuse(script, number, "the issue is not open")
        current = _label_names(info)
        vetoed = self.removed_by_person(number)
        refused += [f"{label}: a person removed it from this issue" for label in to_add if label in vetoed]
        to_add = [a for a in to_add if a not in vetoed]
        ready_refusal = self._apply_ready_rule(number, to_add, to_remove, info, verdict, plan)
        if ready_refusal:
            refused.append(ready_refusal)
        to_add = [a for a in to_add if a not in current]
        to_remove = [r for r in to_remove if r in current]
        for reason in refused:
            self._record({"mode": "live" if self.s.live else "dry-run", "script": script,
                          "action": "refused", "issue": number, "detail": reason})
        if to_add or to_remove:
            # The cap counts only a call that writes, so a vetoed entry leaves it for later ones.
            self.take_cap(script)
            self._write(script, "edit-labels", number, self.labels_args(number, to_add, to_remove),
                        f"add {to_add or '-'} remove {to_remove or '-'}")
        return {"added": to_add, "removed": to_remove, "refused": refused}

    # ── comment-on-duplicates.sh ─────────────────────────────────────────

    def comment_on_duplicates(self, number: int, candidates: Sequence[Json]) -> List[int]:
        """One comment naming likely duplicates. It never closes anything."""
        script = "comment-on-duplicates.sh"
        self.take_cap(script)
        if any(DUPLICATES_MARKER in str(c.get("body", "")) and self._is_bot(c.get("user"))
               for c in self.comments(number)):
            raise self.refuse(script, number, "the refiner already listed duplicates here")
        lines: List[str] = []
        named: List[int] = []
        for cand in list(candidates)[:MAX_CANDIDATES]:
            other = int(cand["number"])
            if other == number or other in named:
                continue
            if self.issue(other).get("state") != "open":
                continue
            named.append(other)
            lines.append(f"- #{other}: {neutralise(str(cand.get('reason', '')))}")
        if not named:
            raise self.refuse(script, number, "no open candidate to name")
        body = "\n".join([DUPLICATES_MARKER, "The refiner found issues that may cover the same work:", "",
                          *lines, "", "Nothing has been closed. A maintainer decides whether to merge them."])
        self._write(script, "comment-duplicates", number, self.comment_args(number, body),
                    f"possible duplicates {named}")
        return named

    # ── close-duplicate.sh ───────────────────────────────────────────────

    def _is_refiner_close(self, e: Json) -> bool:
        if e.get("event") != "closed" or (e.get("actor") or {}).get("login") != self.s.bot_login:
            return False
        if "pull_request" in (e.get("issue") or {}):
            return False
        return e.get("state_reason") in ("duplicate", None)

    def bot_closes_in_window(self) -> int:
        """Duplicate closes by the refiner's bot in the last 7 days, newest first from the events API.

        Counted: `closed` events whose actor is `REFINE_BOT_LOGIN`, on an issue
        (not a pull request), with `state_reason` `duplicate` or no reason
        recorded. Other bots' closes, merges and pull requests do not count,
        nor do closes of issues this run closed: `close_duplicate` adds those
        once from `closed_this_run`, so each close counts once in either mode.

        Reading stops at the first event older than the window. When the page
        limit runs out first, the count is treated as the cap: fail closed.
        """
        since = self.now - timedelta(days=CLOSE_WINDOW_DAYS)
        count = 0
        for page in range(1, EVENT_PAGES + 1):
            batch = self.repo_events_page(page)
            if not batch:
                return count
            for e in batch:
                created = parse_time(str(e.get("created_at", "")))
                if created is None or created < since:
                    return count
                if self._is_refiner_close(e) and (e.get("issue") or {}).get("number") not in self.closed_this_run:
                    count += 1
        return max(count, self.s.close_cap)

    def close_duplicate(self, number: int, survivor: int, verdict: Optional[Json]) -> None:
        """Close an exact duplicate, under the weekly cap, with the reopen route."""
        script = "close-duplicate.sh"
        self.take_cap(script)
        pairs = (verdict or {}).get("duplicates", [])
        if not any(p.get("duplicate") == number and p.get("kept") == survivor
                   and p.get("kind") == "exact" and p.get("closable") is True for p in pairs):
            raise self.refuse(script, number, f"no exact, closable pair with #{survivor} in the verdict")
        if survivor == number:
            raise self.refuse(script, number, "an issue cannot duplicate itself")
        fresh = {n: self.issue(n) for n in (number, survivor)}
        for n, info in fresh.items():
            if info.get("state") != "open":
                raise self.refuse(script, number, f"#{n} is not open")
        if not same_text(fresh[number], fresh[survivor]):
            raise self.refuse(script, number, f"the title or body no longer matches #{survivor} exactly")
        if any(e.get("event") == "reopened" for e in self.events(number)):
            raise self.refuse(script, number, "a person reopened it before; not closing again")
        used = self.bot_closes_in_window() + len(self.closed_this_run)
        if used >= self.s.close_cap:
            raise self.refuse(script, number, f"weekly close cap reached ({used} of {self.s.close_cap})")
        body = "\n".join([
            CLOSE_MARKER,
            f"Closed as an exact duplicate of #{survivor}: the title and body match. "
            "Follow that issue for progress.",
            "",
            "If this is wrong, comment `/reopen` on this issue. The issue author or anyone "
            "with write access can do that, and it reopens the issue with no model involved.",
        ])
        self._write(script, "comment-close", number, self.comment_args(number, body),
                    f"duplicate of #{survivor}")
        self._write(script, "close-duplicate", number, self.close_args(number),
                    f"closed as duplicate of #{survivor}")
        self.closed_this_run.add(number)

    # ── write-size-marker.sh ─────────────────────────────────────────────

    def write_size_marker(self, number: int, points: int, reason: str, verdict: Optional[Json]) -> bool:
        """Post a sizing comment for an unsized issue; skip when the newest marker already says `points`.

        Only an issue the refinement script reported `unsized`, and that
        still reads unsized now, may be sized, so the refiner never overrides
        a size a person wrote in the body or in a marker, before or after the
        verdict.
        """
        script = "write-size-marker.sh"
        if points not in FIBONACCI:
            raise self.refuse(script, number, f"{points} is not a Fibonacci size {FIBONACCI}")
        if not reason.strip():
            raise self.refuse(script, number, "a sizing comment needs a one-line reason")
        entry = verdict_entry(verdict, number)
        if entry is None or entry.get("unsized") is not True:
            raise self.refuse(script, number, "the issue already has a size, or no verdict says it is unsized")
        latest = self.current_size(number, verdict)
        if latest == points:
            self.sized_this_run[number] = points
            return False
        if latest is not None:
            raise self.refuse(script, number, f"the issue reads size {latest} now, written after the verdict")
        # The cap counts only a call that writes, so a marker already in place costs nothing.
        self.take_cap(script)
        body = SIZE_MARKER.format(points=points) + "\n" + neutralise(reason)
        self._write(script, "size-marker", number, self.comment_args(number, body),
                    f"size {points}")
        self.sized_this_run[number] = points
        return True

    # ── add-blocked-by.sh ────────────────────────────────────────────────

    def add_blocked_by(self, number: int, blocker: int) -> bool:
        """Add a blocked-by edge through the dependencies API."""
        script = "add-blocked-by.sh"
        if blocker == number:
            raise self.refuse(script, number, "an issue cannot block itself")
        if blocker in self.blocked_by(number):
            return False
        target = self.issue(blocker)
        if "pull_request" in target:
            raise self.refuse(script, number, f"#{blocker} is a pull request, not an issue")
        if number in self.blocked_by(blocker):
            raise self.refuse(script, number, f"#{blocker} is already blocked by #{number}; that is a cycle")
        self.take_cap(script)
        self._write(script, "blocked-by", number, self.blocked_by_args(number, target),
                    f"blocked by #{blocker}")
        return True

    # ── post-questions.sh ────────────────────────────────────────────────

    def post_questions(self, questions: Sequence[Json]) -> int:
        """Every question from this run, in one comment on the questions issue."""
        script = "post-questions.sh"
        items = [q for q in questions if str(q.get("question", "")).strip()]
        if not items:
            return 0
        self.take_cap(script)
        target = self.s.questions_issue
        if target is None:
            raise self.refuse(script, 0, "REFINE_QUESTIONS_ISSUE is not set")
        lines = [f"- #{int(q['issue'])}: {neutralise(str(q['question']))}" for q in items]
        body = "\n".join([QUESTIONS_MARKER, "Questions from the refiner that need a maintainer's call:", "", *lines])
        if any(str(c.get("body", "")) == self.rendered(body) and self._is_bot(c.get("user"))
               for c in self.comments(target)):
            return 0
        self._write(script, "questions", target, self.comment_args(target, body),
                    f"{len(items)} question(s)")
        return len(items)

    # ── post-buffer-report.sh ────────────────────────────────────────────

    def post_report(self, body: str) -> None:
        """Keep one ready-buffer report on the questions issue, edited in place."""
        script = "post-buffer-report.sh"
        self.take_cap(script)
        target = self.s.questions_issue
        if target is None:
            raise self.refuse(script, 0, "REFINE_QUESTIONS_ISSUE is not set")
        text = REPORT_MARKER + "\n" + body
        mine = [c for c in self.comments(target)
                if REPORT_MARKER in str(c.get("body", "")) and self._is_bot(c.get("user"))]
        if mine:
            cid = int(mine[-1]["id"])
            self._write(script, "report-update", target, self.comment_update_args(target, cid, text),
                        "ready-buffer report updated")
        else:
            self._write(script, "report-create", target, self.comment_args(target, text),
                        "ready-buffer report posted")

    # ── reopen-duplicate.sh ──────────────────────────────────────────────

    def reopen(self, number: int, commenter: str) -> None:
        """The `/reopen` route: undo a refiner duplicate close. No model runs."""
        script = "reopen-duplicate.sh"
        self.take_cap(script)
        info = self.issue(number)
        if info.get("state") != "closed" or info.get("state_reason") != "duplicate":
            raise self.refuse(script, number, "the issue is not closed as a duplicate")
        if (info.get("closed_by") or {}).get("login") != self.s.bot_login:
            raise self.refuse(script, number, "the refiner did not close it, so a person reopens it")
        author = str((info.get("user") or {}).get("login", ""))
        if commenter != author and not self.is_writer(commenter):
            raise self.refuse(script, number, f"{commenter} is neither the author nor a writer")
        self._write(script, "reopen", number, self.reopen_args(number),
                    f"reopened at the request of {commenter}")
        if self.s.triage_label in self.removed_by_person(number):
            self._record({"mode": "live" if self.s.live else "dry-run", "script": script, "action": "refused",
                          "issue": number, "detail": f"{self.s.triage_label}: a person removed it from this issue"})
        else:
            self._write(script, "label-reopened", number, self.labels_args(number, [self.s.triage_label], []),
                        f"add {self.s.triage_label}")
        self._write(script, "comment-reopened", number,
                    self.comment_args(number, REOPEN_MARKER + "\nReopened. The refiner will not close this issue "
                                      "as a duplicate again."),
                    "reopen acknowledged")


def parse_time(value: str) -> Optional[datetime]:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def size_rules(verdict: Optional[Json]) -> Tuple["re.Pattern[str]", "re.Pattern[str]"]:
    """(size_pattern, size_marker) from the verdict's definition block, else the defaults."""
    definition = (verdict or {}).get("definition") or {}
    return (re.compile(str(definition.get("size_pattern") or DEFAULT_SIZE_PATTERN)),
            re.compile(str(definition.get("size_marker") or DEFAULT_SIZE_MARKER)))


def _label_names(info: Json) -> Set[str]:
    return {str(lbl.get("name")) for lbl in info.get("labels", []) if isinstance(lbl, dict)}


def same_text(a: Json, b: Json) -> bool:
    """The exact-duplicate rule of `refine_duplicates`: normalised title and body both equal."""
    return (normalise(str(a.get("title") or "")) == normalise(str(b.get("title") or ""))
            and normalise(str(a.get("body") or "")) == normalise(str(b.get("body") or "")))


def verdict_entry(verdict: Optional[Json], number: int) -> Optional[Json]:
    for entry in (verdict or {}).get("issues", []):
        if isinstance(entry, dict) and entry.get("number") == number:
            return entry
    return None


def ready_blocker(number: int, verdict: Optional[Json], plan: Optional[Json], max_points: int) -> str:
    """Why the ready label may not go on, or "" when it may.

    Without a plan, the script's verdict alone must read `ready`. With a
    plan, the plan resolves the judgement items: it must say ready, ask no
    question, and size an issue the script could not size within `max_points`.
    """
    entry = verdict_entry(verdict, number)
    if entry is None:
        return "no refinement-script verdict for this issue"
    if any(i.get("status") == "fail" for i in entry.get("checklist", [])):
        return "a script-checked item fails"
    if entry.get("overlaps"):
        return "an open pull request overlaps it"
    if any(d.get("kind") == "exact" and d.get("survivor") != number for d in entry.get("duplicates", [])):
        return "it is an exact duplicate"
    pending = [i for i in entry.get("checklist", []) if i.get("status") == "needs_judgement"]
    if not pending:
        return "" if entry.get("verdict") == "ready" else f"the verdict is {entry.get('verdict')}"
    if plan is None:
        return "judgement items are open and no plan resolves them"
    if plan.get("ready") is not True:
        return "the plan does not judge it ready"
    if plan.get("questions"):
        return "the plan leaves a question for a person"
    if any(i.get("id") == "sized" for i in pending):
        size = plan.get("size")
        if not isinstance(size, int) or not 1 <= size <= max_points:
            return f"the plan's size {size} is not between 1 and {max_points}"
    return ""


def _load_json(path: Optional[str]) -> Optional[Json]:
    if not path:
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return data


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="refine_write.py", description="Guarded refiner writes (issue #1321).")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("edit-labels")
    s.add_argument("--issue", type=int, required=True)
    s.add_argument("--add", action="append", default=[])
    s.add_argument("--remove", action="append", default=[])
    s.add_argument("--verdict")
    s.add_argument("--plan")
    s = sub.add_parser("comment-duplicates")
    s.add_argument("--issue", type=int, required=True)
    s.add_argument("--candidate", type=int, action="append", default=[])
    s = sub.add_parser("close-duplicate")
    s.add_argument("--issue", type=int, required=True)
    s.add_argument("--of", type=int, required=True)
    s.add_argument("--verdict", required=True)
    s = sub.add_parser("size-marker")
    s.add_argument("--issue", type=int, required=True)
    s.add_argument("--points", type=int, required=True)
    s.add_argument("--reason", required=True)
    s.add_argument("--verdict", required=True)
    s = sub.add_parser("blocked-by")
    s.add_argument("--issue", type=int, required=True)
    s.add_argument("--blocker", type=int, required=True)
    s = sub.add_parser("questions")
    s.add_argument("--file", required=True, help='JSON list of {"issue": n, "question": "..."}')
    s = sub.add_parser("report")
    s.add_argument("--file", required=True, help="markdown body of the ready-buffer report")
    s = sub.add_parser("reopen")
    s.add_argument("--issue", type=int, required=True)
    s.add_argument("--commenter", required=True)
    return p


def dispatch(args: argparse.Namespace, w: Writer) -> None:
    cmd = args.command
    if cmd == "edit-labels":
        result = w.edit_labels(args.issue, _csv(",".join(args.add)), _csv(",".join(args.remove)),
                               _load_json(args.verdict), _load_json(args.plan))
        for reason in result["refused"]:
            print(f"refused: {reason}", file=sys.stderr)
    elif cmd == "comment-duplicates":
        w.comment_on_duplicates(args.issue, [{"number": n, "reason": "named by the refiner"} for n in args.candidate])
    elif cmd == "close-duplicate":
        w.close_duplicate(args.issue, args.of, _load_json(args.verdict))
    elif cmd == "size-marker":
        w.write_size_marker(args.issue, args.points, args.reason, _load_json(args.verdict))
    elif cmd == "blocked-by":
        w.add_blocked_by(args.issue, args.blocker)
    elif cmd == "questions":
        w.post_questions(json.loads(Path(args.file).read_text(encoding="utf-8")))
    elif cmd == "report":
        w.post_report(Path(args.file).read_text(encoding="utf-8"))
    elif cmd == "reopen":
        w.reopen(args.issue, args.commenter)


def make_writer(settings: Settings, runner: Optional[Runner] = None) -> Writer:
    """The writer for `settings.platform`: GitHub through `gh`, GitLab through `glab` (#1327)."""
    if settings.platform == "gitlab":
        from refine_write_gitlab import GitLabWriter, glab_runner
        return GitLabWriter(settings, runner or glab_runner)
    if settings.platform != "github":
        raise ValueError(f"REFINE_PLATFORM must be github or gitlab, not {settings.platform!r}")
    return Writer(settings, runner or gh_runner)


def main(argv: Optional[Sequence[str]] = None, runner: Optional[Runner] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        w = make_writer(Settings(), runner)
        dispatch(args, w)
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 4
    except GhError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except (ValueError, OSError) as exc:
        print(f"usage: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
