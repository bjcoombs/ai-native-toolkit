#!/usr/bin/env python3
"""Keep a GitHub project's Status, Size and Verdict fields in sync with the refinement verdict (issue #1327).

The sweep runs this after `refine.py`, with no model. For every open issue in
the verdict it works out three field values and sets them on the issue's item
in one GitHub project (a user's or an organization's Projects board), adding
the issue to the project first when it is missing:

    refine_project.py --verdict V --project-number N --owner O

**Status:** the first row that matches, top to bottom.

| Status | When |
|---|---|
| In review | an open pull request closes the issue (an overlap of kind `closes`) |
| In progress | the issue has the in-progress label |
| Blocked | the issue is ready (next row) but has an open blocked-by edge |
| Ready | the ready label, with a verdict that is not `not_ready` |
| Waiting on you | the issue is named in an open refiner question (below) |
| Needs refinement | none of the above |

A question is open when the refiner's bot named the issue in a questions
comment (`<!-- refine:questions -->`) on `REFINE_QUESTIONS_ISSUE` and no
later comment there by anyone else names the same `#N`. With no questions
issue set, no issue is waiting.

**Size:** the verdict's `size_points` (from the `<!-- refine:size N -->`
marker or a `Size: N points` body line), cleared when the issue has none.

**Verdict:** the verdict's `verdict`.

Only a field whose value differs is written, so a second run with nothing
changed makes no write. A project with more items than one `item-list` call
returns (2000) stops the run before any write, since an unlisted item would
read as missing. The project's fields must be named `Status`
(single select, with the six options above), `Size` (number) and `Verdict`
(single select: `ready`, `needs_judgement`, `not_ready`); a missing field or
option stops the run before any write (exit 2).

Tokens: the project calls run with `REFINE_PROJECT_TOKEN` (a token with the
`project` scope; the workflow's `GITHUB_TOKEN` cannot write a user's
project). Reading the questions issue uses the job's own `GH_TOKEN`.

Dry-run: unless `REFINE_MODE` is `live`, every field write and item add is
recorded in the actions log and none is sent, as in `refine_write.py`.
`REFINE_PROJECT_MAX_WRITES` (default 500) caps the writes in one run; the
rest are refused and logged.

Exit codes: 0 done; 2 usage or project-shape error; 3 `gh` failed; 4 the
write cap refused at least one write.

Python 3.8 compatible and stdlib only, plus `gh` on PATH.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence, Set

from refine_write import QUESTIONS_MARKER, GhError, Json, Runner, Settings, Writer, gh_runner, json_values

STATUS_IN_REVIEW = "In review"
STATUS_IN_PROGRESS = "In progress"
STATUS_BLOCKED = "Blocked"
STATUS_READY = "Ready"
STATUS_WAITING = "Waiting on you"
STATUS_NEEDS_REFINEMENT = "Needs refinement"
STATUSES = (STATUS_IN_REVIEW, STATUS_IN_PROGRESS, STATUS_BLOCKED, STATUS_READY, STATUS_WAITING,
            STATUS_NEEDS_REFINEMENT)
VERDICTS = ("ready", "needs_judgement", "not_ready")
FIELD_STATUS, FIELD_SIZE, FIELD_VERDICT = "Status", "Size", "Verdict"
DEFAULT_IN_PROGRESS = "in-progress"
DEFAULT_MAX_WRITES = 500
ITEM_LIMIT = 2000
SCRIPT = "refine_project.py"
_ISSUE_REF = re.compile(r"#(\d+)\b")
_QUESTION_LINE = re.compile(r"^- #(\d+):", re.MULTILINE)


class ProjectError(ValueError):
    """The project is missing a field or an option the sync needs."""


# ── what each issue should show ──────────────────────────────────────────

def is_ready(entry: Json, ready_label: str) -> bool:
    return ready_label in entry.get("labels", []) and entry.get("verdict") != "not_ready"


def status_for(entry: Json, ready_label: str, in_progress_label: str, waiting: Set[int]) -> str:
    """The Status row for one verdict entry: the first that matches, top to bottom."""
    if any(o.get("kind") == "closes" for o in entry.get("overlaps", [])):
        return STATUS_IN_REVIEW
    if in_progress_label in entry.get("labels", []):
        return STATUS_IN_PROGRESS
    ready = is_ready(entry, ready_label)
    if ready and any(str(b.get("state", "")).lower() == "open" for b in entry.get("blocked_by", [])):
        return STATUS_BLOCKED
    if ready:
        return STATUS_READY
    if int(entry["number"]) in waiting:
        return STATUS_WAITING
    return STATUS_NEEDS_REFINEMENT


def waiting_issues(comments: Sequence[Json], is_bot: Callable[[Any], bool]) -> Set[int]:
    """Issues named in a refiner questions comment with no later reply naming them.

    `comments` are the questions issue's comments, oldest first (the REST
    order); `is_bot` tells the refiner's own comments from a person's.
    """
    open_q: Set[int] = set()
    for c in comments:
        body = str(c.get("body") or "")
        if is_bot(c.get("user")):
            if body.startswith(QUESTIONS_MARKER):
                open_q |= {int(n) for n in _QUESTION_LINE.findall(body)}
        else:
            open_q -= {int(n) for n in _ISSUE_REF.findall(body)}
    return open_q


def wanted(entry: Json, ready_label: str, in_progress_label: str, waiting: Set[int]) -> Json:
    """The three field values one issue should show."""
    size = entry.get("size_points")
    return {FIELD_STATUS: status_for(entry, ready_label, in_progress_label, waiting),
            FIELD_SIZE: int(size) if size is not None else None,
            FIELD_VERDICT: str(entry.get("verdict"))}


# ── the project ──────────────────────────────────────────────────────────

def token_runner(token: str) -> Runner:
    """A `gh` runner that authenticates with `token` instead of the job's GH_TOKEN."""
    env = dict(os.environ)
    env["GH_TOKEN"] = token
    env.pop("GITHUB_TOKEN", None)

    def run(args: Sequence[str]) -> str:
        try:
            proc = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", check=False, env=env)
        except OSError as exc:
            raise GhError(f"gh could not start: {exc}") from exc
        if proc.returncode != 0:
            raise GhError(f"gh {' '.join(args[:3])} failed: {proc.stderr.strip()}")
        return proc.stdout
    return run


def _one(text: str) -> Json:
    values = json_values(text)
    if len(values) != 1 or not isinstance(values[0], dict):
        raise GhError("gh returned output that is not one JSON object")
    return dict(values[0])


class Project:
    """One project's fields and items, read once; writes go through `set_field` and `add`."""

    def __init__(self, number: int, owner: str, repo: str, settings: Settings, runner: Runner,
                 max_writes: int = DEFAULT_MAX_WRITES) -> None:
        self.number, self.owner, self.repo = str(number), owner, repo
        self.s = settings
        self.run = runner
        self.max_writes = max_writes
        self.writes = 0
        self.refused = 0
        where = ["--owner", owner, "--format", "json"]
        self.id = str(_one(self.run(["project", "view", self.number, *where]))["id"])
        fields = _one(self.run(["project", "field-list", self.number, *where, "--limit", "100"])).get("fields", [])
        self.fields: Dict[str, Json] = {str(f.get("name")): f for f in fields}
        self._check_shape()
        items = _one(self.run(["project", "item-list", self.number, *where, "--limit", str(ITEM_LIMIT)]))
        listed, total = len(items.get("items", [])), int(items.get("totalCount") or 0)
        if total > listed:
            # An item past the limit would read as missing and be rewritten every run.
            raise ProjectError(f"the project has {total} items and only {listed} were listed; "
                               f"the sync reads at most {ITEM_LIMIT}")
        self.items: Dict[int, Json] = {}
        for item in items.get("items", []):
            content = item.get("content") or {}
            if content.get("type") == "Issue" and content.get("repository") == repo:
                self.items[int(content["number"])] = item

    def _check_shape(self) -> None:
        for name, options in ((FIELD_STATUS, STATUSES), (FIELD_VERDICT, VERDICTS)):
            have = {str(o.get("name")) for o in self.fields.get(name, {}).get("options", [])}
            if name not in self.fields:
                raise ProjectError(f"the project has no {name} field")
            missing = [o for o in options if o not in have]
            if missing:
                raise ProjectError(f"the project's {name} field has no option(s) {missing}")
        if FIELD_SIZE not in self.fields:
            raise ProjectError(f"the project has no {FIELD_SIZE} field")

    def _option(self, field: str, name: str) -> str:
        return next(str(o["id"]) for o in self.fields[field]["options"] if o.get("name") == name)

    def _record(self, action: str, issue: int, detail: str) -> None:
        self.s.actions_log.parent.mkdir(parents=True, exist_ok=True)
        with self.s.actions_log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"mode": "live" if self.s.live else "dry-run", "script": SCRIPT,
                                 "action": action, "issue": issue, "detail": detail}, sort_keys=True) + "\n")

    def _write(self, action: str, issue: int, args: Sequence[str], detail: str) -> Optional[str]:
        if self.writes >= self.max_writes:
            self.refused += 1
            self._record("refused", issue, f"{action} {detail}: over the cap of {self.max_writes} writes")
            return None
        self.writes += 1
        self._record(action, issue, detail)
        return self.run(args) if self.s.live else None

    def add(self, issue: int, url: str) -> Optional[str]:
        """Add the issue to the project; returns the new item id (None in dry-run)."""
        out = self._write("project-add", issue,
                          ["project", "item-add", self.number, "--owner", self.owner, "--url", url,
                           "--format", "json"], "added to the project")
        return str(_one(out)["id"]) if out else None

    def set_field(self, issue: int, item_id: Optional[str], field: str, value: Any) -> None:
        args = ["project", "item-edit", "--id", item_id or "<new item>", "--project-id", self.id,
                "--field-id", str(self.fields[field]["id"])]
        if value is None:
            args.append("--clear")
        elif field == FIELD_SIZE:
            args += ["--number", str(value)]
        else:
            args += ["--single-select-option-id", self._option(field, str(value))]
        detail = f"{field}: {'(cleared)' if value is None else value}"
        if item_id is None and self.s.live:
            self._record("refused", issue, f"{detail}: the issue has no project item")
            return
        self._write("project-field", issue, args, detail)


def _current(item: Optional[Json]) -> Json:
    """The item's present values, keyed by field name; `gh` reports them lower-cased."""
    if item is None:
        return {}
    size = item.get("size")
    return {FIELD_STATUS: item.get("status"), FIELD_VERDICT: item.get("verdict"),
            FIELD_SIZE: int(size) if isinstance(size, (int, float)) else None}


def sync(project: Project, verdict: Json, ready_label: str, in_progress_label: str, waiting: Set[int]) -> int:
    """Set each open issue's three fields; returns the number of field writes."""
    before = project.writes
    for entry in verdict.get("issues", []):
        number = int(entry["number"])
        item = project.items.get(number)
        item_id: Optional[str] = str(item["id"]) if item else None
        if item is None:
            item_id = project.add(number, str(entry.get("url") or ""))
        have = _current(item)
        for field, value in wanted(entry, ready_label, in_progress_label, waiting).items():
            if item is not None and have.get(field) == value:
                continue
            if item is None and value is None:
                continue
            project.set_field(number, item_id, field, value)
    return project.writes - before


# ── command line ─────────────────────────────────────────────────────────

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=SCRIPT, description=__doc__.splitlines()[0] if __doc__ else None)
    p.add_argument("--verdict", required=True, help="refine.py's JSON output")
    p.add_argument("--project-number", type=int, required=True)
    p.add_argument("--owner", required=True, help="the project's owner: a user or an organization")
    return p


def main(argv: Optional[Sequence[str]] = None, read_runner: Runner = gh_runner,
         project_runner: Optional[Runner] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = Settings()
        token = os.environ.get("REFINE_PROJECT_TOKEN", "")
        if project_runner is None:
            if not token:
                raise ValueError("REFINE_PROJECT_TOKEN is not set")
            project_runner = token_runner(token)
        verdict = json.loads(Path(args.verdict).read_text(encoding="utf-8"))
        reader = Writer(settings, read_runner)
        waiting: Set[int] = set()
        if settings.questions_issue is not None:
            waiting = waiting_issues(reader.comments(settings.questions_issue), reader._is_bot)
        in_progress = os.environ.get("REFINE_IN_PROGRESS_LABEL") or DEFAULT_IN_PROGRESS
        max_writes = int(os.environ.get("REFINE_PROJECT_MAX_WRITES") or DEFAULT_MAX_WRITES)
        project = Project(args.project_number, args.owner, settings.repo, settings, project_runner, max_writes)
        count = sync(project, verdict, settings.ready_label, in_progress, waiting)
    except GhError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except (ValueError, OSError, KeyError) as exc:
        print(f"{SCRIPT}: {exc}", file=sys.stderr)
        return 2
    mode = "live" if settings.live else "dry-run"
    print(f"{SCRIPT}: {count} project write(s) ({mode}), {project.refused} refused", file=sys.stderr)
    return 4 if project.refused else 0


if __name__ == "__main__":
    sys.exit(main())
