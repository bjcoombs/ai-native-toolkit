#!/usr/bin/env python3
"""Post the doc-sync findings on a pull request (issue #1453).

The `post` job of `.github/workflows/doc-sync.yml` runs this with the only
write token in the workflow and no model. It reads the plan
`doc_sync_findings.py` wrote and:

    doc_sync_post.py --plan findings.json --repo OWNER/NAME --pr N --head-sha SHA

- opens one review thread per planned thread whose id no earlier doc-sync
  comment on the pull request carries, on the cited line when that line is
  in the diff and on the file otherwise, so an open thread holds the merge
  until the doc is fixed or the thread is answered;
- writes every other finding, the dropped ones and the run's scope into one
  summary comment, edited in place on each push. A run with nothing to say
  creates no comment, but still updates one an earlier run left.

Every call goes through `gh api`. Stdlib only, Python 3.8 compatible.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, cast

from doc_sync_types import Finding, Plan, Side

SUMMARY_MARKER = "<!-- doc-sync:summary -->"
FINDING_MARKER = "<!-- doc-sync:finding:%s -->"
FINDING_RE = re.compile(r"<!-- doc-sync:finding:([0-9a-f]{16}) -->")
# The login `github.token` posts as; only its comments count as earlier runs.
BOT = "github-actions[bot]"
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
Runner = Callable[[Sequence[str], Optional[str]], str]


def gh(args: Sequence[str], payload: Optional[str] = None) -> str:
    done = subprocess.run(["gh", "api", *args], input=payload, capture_output=True,
                          encoding="utf-8", errors="replace", check=False)
    if done.returncode != 0:
        raise SystemExit("gh api %s failed: %s" % (" ".join(args[:3]), done.stderr.strip()))
    return done.stdout


def _pages(text: str) -> List[Any]:
    """`gh api --paginate` output: one JSON array per page, concatenated."""
    out: List[Any] = []
    decoder = json.JSONDecoder()
    i = 0
    while i < len(text):
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text):
            break
        value, i = decoder.raw_decode(text, i)
        out.extend(value if isinstance(value, list) else [value])
    return out


def diff_lines(patch: str) -> Set[int]:
    """The new-side lines a review comment can sit on: added and context lines."""
    lines: Set[int] = set()
    current = 0
    for row in patch.splitlines():
        m = HUNK_RE.match(row)
        if m:
            current = int(m.group(1))
            continue
        if row.startswith("-") or row.startswith("\\"):
            continue
        if current:
            lines.add(current)
            current += 1
    return lines


def cite(side: Side) -> str:
    # Both in code spans: GitHub renders no mention, link or reference there.
    quote = side["quote"].replace("`", "'")
    return "`%s:%s`: `%s`" % (side["path"].replace("`", "'"), side["line"], quote)


def thread_body(f: Finding) -> str:
    return "\n".join([
        "**Doc contradiction** (%s, %s confidence): %s" % (f["kind"], f["confidence"], f["subject"]),
        "",
        "- " + cite(f["a"]),
        "- " + cite(f["b"]),
        "- Why: " + str(f["why"]),
        "",
        "Fix the document or the code, or reply with why both can stand, then resolve this thread.",
        FINDING_MARKER % f["id"],
    ])


def thread_request(f: Finding, files: Dict[str, Set[int]], head_sha: str) -> Optional[Dict[str, Any]]:
    """The review-comment payload, anchored on a side the pull request changed."""
    for side in (f["a"], f["b"]):
        if side["path"] in files and side["line"] in files[side["path"]]:
            return {"commit_id": head_sha, "path": side["path"], "line": side["line"],
                    "side": "RIGHT", "body": thread_body(f)}
    for side in (f["a"], f["b"]):
        if side["path"] in files:
            return {"commit_id": head_sha, "path": side["path"], "subject_type": "file",
                    "body": thread_body(f)}
    return None


def summary_body(plan: Plan, posted: List[Dict[str, Any]], kept: int) -> str:
    rows = [SUMMARY_MARKER, "## Doc sync", ""]
    status = plan.get("status")
    if status == "skipped":
        rows.append("Not run: %s." % plan.get("reason", "no reason given"))
        return "\n".join(rows) + "\n"
    if status == "error":
        rows.append("The check did not complete: %s. It is not a required check." % plan.get("reason"))
        return "\n".join(rows) + "\n"
    rows.append("Checked %d doc cluster%s touched by this pull request%s." % (
        plan.get("clusters", 0), "" if plan.get("clusters", 0) == 1 else "s",
        ", plus the cross-cluster pass over the hub docs" if plan.get("cross_cluster") else ""))
    rows.append("Review threads: %d new, %d already open from an earlier push." % (len(posted), kept))
    if plan.get("summary"):
        rows += ["", "### Other findings (not blocking)", ""]
        for f in plan.get("summary", []):
            rows.append("- **%s** (%s, %s): %s; %s. %s" % (
                f["subject"], f["kind"], f["confidence"], cite(f["a"]), cite(f["b"]), f["why"]))
    if not plan.get("threads") and not plan.get("summary"):
        rows += ["", "No contradictions found."]
    if plan.get("dropped"):
        dropped = plan.get("dropped", [])
        rows += ["", "<details><summary>Dropped by the citation check (%d)</summary>" % len(dropped), ""]
        rows += ["- " + d.replace("`", "'") for d in dropped]
        rows += ["", "</details>"]
    return "\n".join(rows) + "\n"


def is_quiet(plan: Plan) -> bool:
    """True when the run has nothing a reader needs: no new comment is created.

    A clean check, and a skip the workflow marks quiet (no cluster touched,
    an author without write access). An error or any other skip always
    writes the summary, so a run that could not check is never silent.
    """
    if plan.get("status") == "checked":
        return not plan.get("threads") and not plan.get("summary")
    return plan.get("status") == "skipped" and bool(plan.get("quiet"))


def _ours(item: Any) -> bool:
    user = item.get("user") if isinstance(item, dict) else None
    return isinstance(user, dict) and user.get("login") == BOT


def post(plan: Plan, repo: str, pr: int, head_sha: str, run: Runner = gh) -> List[Dict[str, Any]]:
    """Apply the plan; returns the review comments it created."""
    pulls = "repos/%s/pulls/%d" % (repo, pr)
    issue = "repos/%s/issues/%d" % (repo, pr)
    files: Dict[str, Set[int]] = {}
    for item in _pages(run(["--paginate", pulls + "/files"], None)):
        files[str(item["filename"])] = diff_lines(str(item.get("patch") or ""))
    seen: Set[str] = set()
    for item in _pages(run(["--paginate", pulls + "/comments"], None)):
        if _ours(item):
            seen |= set(FINDING_RE.findall(str(item.get("body") or "")))
    posted: List[Dict[str, Any]] = []
    kept = 0
    for f in plan.get("threads", []):
        if f["id"] in seen:
            kept += 1
            continue
        request = thread_request(f, files, head_sha)
        if request is None:
            plan.setdefault("summary", []).append(f)
            continue
        run(["--method", "POST", pulls + "/comments", "--input", "-"], json.dumps(request))
        posted.append(request)
    existing = None
    for item in _pages(run(["--paginate", issue + "/comments"], None)):
        if _ours(item) and SUMMARY_MARKER in str(item.get("body") or ""):
            existing = item["id"]
    if existing is None and is_quiet(plan):
        return posted
    body = json.dumps({"body": summary_body(plan, posted, kept)})
    if existing is None:
        run(["--method", "POST", issue + "/comments", "--input", "-"], body)
    else:
        run(["--method", "PATCH", "repos/%s/issues/comments/%s" % (repo, existing), "--input", "-"], body)
    return posted


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--plan", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--head-sha", required=True)
    args = ap.parse_args(argv)
    if not re.fullmatch(r"[0-9a-f]{40}", args.head_sha):
        raise SystemExit("doc_sync_post: --head-sha must be a full commit sha")
    plan = cast(Plan, json.loads(Path(args.plan).read_text(encoding="utf-8")))
    posted = post(plan, args.repo, args.pr, args.head_sha)
    print("doc-sync: %d new review thread(s), %d summary finding(s)" % (len(posted), len(plan.get("summary", []))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
