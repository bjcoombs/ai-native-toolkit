#!/usr/bin/env python3
"""Read-only GitLab reads for the refiner's model and its CI jobs (issue #1327).

The model's tool on GitLab is the same `gh.sh` as on GitHub: with
`REFINE_PLATFORM=gitlab`, `gh.sh` keeps its own checks (four read
subcommands, no `--web`, no other repository) and then runs this script in
place of `gh`. The four subcommands answer in plain text from `glab api`
GETs, through `refine_platform_gitlab.run_glab_readonly`, which refuses
anything that could write before a process starts:

    gh.sh issue view <n> [--comments]
    gh.sh issue list [--search <q>] [--state open|closed|all] [--label <l>] [--limit <n>]
    gh.sh search issues <query> [--state open|closed|all] [--limit <n>]
    gh.sh label list

Flags these do not use are ignored, so a GitHub-style call still reads. A
`--repo` other than the job's project (`GH_REPO`, else `CI_PROJECT_PATH`) is
refused, as is an issue given as anything but a number.

Two more subcommands serve the GitLab template (`templates/refine.yml`) and
are not reachable through `gh.sh`:

- `new-issues [--payload <file>] [--max <n>] [--fallback-hours <h>]` prints
  the issues to triage this run, one number per line. With a webhook payload
  (`TRIGGER_PAYLOAD`), only the issue it opened; otherwise every issue opened
  since the newest earlier `refine-apply` job that succeeded on the default
  branch (`CI_DEFAULT_BRANCH`) outside a webhook pipeline, or in the last
  `--fallback-hours` when there is none, oldest first, at most `--max`. A
  failed run therefore leaves its issues for the next one.
- `trusted-author --issue <n>` prints `true` when the issue's author is a
  project member at Reporter or above, else `false`: the model runs only on
  such an issue, as the GitHub workflows run it only for an OWNER, MEMBER or
  COLLABORATOR.

Exit codes: 0 done; 2 usage; 3 `glab` failed; 4 refused.

Python 3.8 compatible and stdlib only, plus `glab` on PATH.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import quote

from refine_platform import GhError, ReadOnlyViolation
from refine_platform_gitlab import (
    TRUSTED_ACCESS, Runner, gitlab_state, iso, json_stream, member_levels, project_param, run_glab_readonly, username,
)

STATES = {"open": "opened", "opened": "opened", "closed": "closed", "all": "all"}
MAX_LIST = 100
APPLY_JOB = "refine-apply"
JOB_PAGES = 5
# gh flags that take a value and change only the output format or ordering.
IGNORED_VALUE_FLAGS = frozenset({"--json", "--jq", "-q", "--template", "-t", "--sort", "--order", "--author", "-A",
                                 "--assignee", "-a", "--milestone", "-m", "--app", "--mention", "--match"})


class Refused(Exception):
    """The call asks for something this reader does not do."""


class Reader:
    def __init__(self, project: str, runner: Runner, env: Dict[str, str]) -> None:
        self.project = project
        self.p = project_param(project)
        self.run = runner
        self.env = env

    def get(self, path: str, paginate: bool = False) -> List[Any]:
        args = ["api", "--paginate", path] if paginate else ["api", path]
        return json_stream(self.run(args), path)

    def one(self, path: str) -> Dict[str, Any]:
        values = self.get(path)
        if len(values) != 1 or not isinstance(values[0], dict):
            raise GhError(f"glab api {path}: unexpected response")
        return dict(values[0])

    def issue_view(self, number: int, comments: bool) -> str:
        raw = self.one(f"projects/{self.p}/issues/{number}")
        lines = [f"title:\t{raw.get('title') or ''}", f"state:\t{gitlab_state(raw.get('state'))}",
                 f"author:\t{username(raw.get('author'))}", f"labels:\t{', '.join(raw.get('labels') or [])}",
                 f"number:\t{raw.get('iid')}", f"url:\t{raw.get('web_url') or ''}", "--", str(raw.get("description") or "")]
        if comments:
            notes = self.get(f"projects/{self.p}/issues/{number}/notes?per_page=100&sort=asc&order_by=created_at",
                             paginate=True)
            for n in notes:
                if isinstance(n, dict) and not n.get("system"):
                    lines += ["", f"-- comment by {username(n.get('author'))} at {iso(n.get('created_at'))}",
                              str(n.get("body") or "")]
        return "\n".join(lines)

    def issue_list(self, search: str, state: str, labels: List[str], limit: int) -> str:
        query = f"state={STATES[state]}&per_page={min(limit, MAX_LIST)}&order_by=created_at&sort=desc"
        if search:
            query += "&in=title,description&search=" + quote(search, safe="")
        if labels:
            query += "&labels=" + quote(",".join(labels), safe="")
        rows = [r for r in self.get(f"projects/{self.p}/issues?{query}") if isinstance(r, dict)][:limit]
        return "\n".join(f"{r.get('iid')}\t{gitlab_state(r.get('state'))}\t{r.get('title') or ''}\t"
                         f"{', '.join(r.get('labels') or [])}" for r in rows)

    def label_list(self) -> str:
        rows = self.get(f"projects/{self.p}/labels?per_page=100", paginate=True)
        return "\n".join(f"{r.get('name')}\t{r.get('description') or ''}" for r in rows if isinstance(r, dict))

    def new_issues(self, payload: Optional[str], limit: int, fallback_hours: int, now: datetime) -> List[int]:
        if payload:
            data = json.loads(Path(payload).read_text(encoding="utf-8") or "{}")
            attrs = data.get("object_attributes") or {}
            if data.get("object_kind") == "issue" and attrs.get("action") == "open" and isinstance(attrs.get("iid"), int):
                return [int(attrs["iid"])]
            return []
        since = self.last_run() or (now - timedelta(hours=fallback_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = self.get(f"projects/{self.p}/issues?state=opened&created_after={since}"
                        "&order_by=created_at&sort=asc&per_page=100", paginate=True)
        numbers = [int(r["iid"]) for r in rows if isinstance(r, dict) and isinstance(r.get("iid"), int)]
        if len(numbers) > limit:
            print(f"refine: {len(numbers)} new issues since {since}; triaging the oldest {limit}, "
                  "the sweep covers the rest", file=sys.stderr)
        return numbers[:limit]

    def last_run(self) -> str:
        """When the newest earlier `refine-apply` job that succeeded on the default branch was created, or "".

        A webhook pipeline's run handles one issue, so it does not count; nor
        does this pipeline (`CI_PIPELINE_ID`). Only the newest JOB_PAGES pages
        of successful jobs are read.
        """
        current = str(self.env.get("CI_PIPELINE_ID") or "")
        branch = str(self.env.get("CI_DEFAULT_BRANCH") or "")
        for page in range(1, JOB_PAGES + 1):
            jobs = [j for j in self.get(f"projects/{self.p}/jobs?scope[]=success&per_page=100&page={page}")
                    if isinstance(j, dict)]
            for j in jobs:
                pipeline = j.get("pipeline") or {}
                if (j.get("name") == APPLY_JOB and j.get("created_at") and str(pipeline.get("id")) != current
                        and (not branch or j.get("ref") == branch) and pipeline.get("source") != "trigger"):
                    return iso(j["created_at"])
            if len(jobs) < 100:
                break
        return ""

    def trusted_author(self, number: int) -> bool:
        login = username(self.one(f"projects/{self.p}/issues/{number}").get("author"))
        levels = member_levels(self.get(f"projects/{self.p}/members/all?per_page=100", paginate=True))
        return bool(login) and levels.get(login, 0) >= TRUSTED_ACCESS


def _number(value: str) -> int:
    text = value.lstrip("#")
    if not text.isdigit():
        raise Refused(f"an issue is named by its number, not {value!r}")
    return int(text)


def _drop_ignored(argv: List[str]) -> List[str]:
    """`argv` without the gh flags this reader ignores, and their values."""
    out: List[str] = []
    skip = False
    for arg in argv:
        if skip:
            skip = False
        elif arg in IGNORED_VALUE_FLAGS:
            skip = True
        else:
            out.append(arg)
    return out


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="refine_gitlab_read.py", add_help=False)
    p.add_argument("group")
    p.add_argument("command")
    p.add_argument("words", nargs="*")
    p.add_argument("--repo", "-R")
    p.add_argument("--comments", "-c", action="store_true")
    p.add_argument("--search", "-S", default="")
    p.add_argument("--state", "-s", default="open", choices=sorted(STATES))
    p.add_argument("--label", "-l", action="append", default=[])
    p.add_argument("--limit", "-L", type=int, default=30)
    p.add_argument("--issue", type=int)
    p.add_argument("--payload")
    p.add_argument("--max", type=int, default=20)
    p.add_argument("--fallback-hours", type=int, default=24)
    return p


def run(argv: Sequence[str], runner: Runner, env: Dict[str, str], now: datetime) -> str:
    args, _ = _parser().parse_known_args(list(argv))
    project = env.get("GH_REPO") or env.get("CI_PROJECT_PATH") or ""
    if not project:
        raise Refused("no project: set GH_REPO or CI_PROJECT_PATH")
    if args.repo is not None and args.repo != project:
        raise Refused(f"only {project} may be read")
    r = Reader(project, runner, env)
    cmd = f"{args.group} {args.command}"
    if cmd == "issue view":
        if len(args.words) != 1:
            raise Refused("issue view takes one issue number")
        return r.issue_view(_number(args.words[0]), args.comments)
    if cmd == "issue list":
        return r.issue_list(args.search, args.state, args.label, args.limit)
    if cmd == "search issues":
        return r.issue_list(" ".join(args.words), args.state, args.label, args.limit)
    if cmd == "label list":
        return r.label_list()
    if args.group == "new-issues":
        return "\n".join(str(n) for n in r.new_issues(args.payload, args.max, args.fallback_hours, now))
    if args.group == "trusted-author" and args.issue is not None:
        return "true" if r.trusted_author(args.issue) else "false"
    raise Refused(f"'{' '.join(argv[:2])}' is not a read this script serves")


def main(argv: Optional[Sequence[str]] = None, runner: Runner = run_glab_readonly,
         env: Optional[Dict[str, str]] = None, now: Optional[datetime] = None) -> int:
    argv = _drop_ignored(list(sys.argv[1:] if argv is None else argv))
    if argv and argv[0] in ("new-issues", "trusted-author"):  # no second word
        argv.insert(1, "-")
    try:
        out = run(argv, runner, dict(os.environ if env is None else env), now or datetime.now(timezone.utc))
    except (Refused, ReadOnlyViolation) as exc:
        print(f"refine_gitlab_read: refused: {exc}", file=sys.stderr)
        return 4
    except GhError as exc:
        print(f"refine_gitlab_read: {exc}", file=sys.stderr)
        return 3
    except (ValueError, OSError) as exc:
        print(f"refine_gitlab_read: {exc}", file=sys.stderr)
        return 2
    except SystemExit as exc:  # argparse usage error
        return 2 if exc.code else 0
    if out:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
