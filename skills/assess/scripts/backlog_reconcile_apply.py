"""Run the scheduled backlog reconcile's writes on GitHub (#814): the writer job of ``reusable-backlog-reconcile.yml``.

Reads the plan ``assess_actions_render.py reconcile --scheduled`` printed (``{mode, writes}``,
``lib/report/backlog_schedule.py``) and runs each write with ``gh``:

- ``edit_comment``: ``PATCH repos/<repo>/issues/comments/<comment_id>`` with the new body;
- ``add_comment``: ``gh issue comment <number> --body-file``;
- ``close``: ``gh issue close <number> --reason completed``, with no comment;
- ``create``: ``gh issue create`` with the title, body and labels, then the new item's
  status comment. The labels are created first when missing, never edited when present.

A write the plan's mode does not allow, or one missing a field its op needs, (``ALLOWED``, the same table as
``backlog_schedule.WRITE_OPS``) fails the run before any write is made, and so does a plan
over ``MAX_CLOSES`` closes or ``MAX_CREATES`` new items. ``--dry-run`` prints the writes and
makes none. A failed ``gh`` call ends the run with exit 1, gh's stderr and the writes made
before it; a filed item is logged as soon as its number is known, so a status comment that
fails after ``gh issue create`` still names the new item. Any failure once writing has begun,
a ``gh`` error or a create with no issue URL, is a ``WriteError`` with that list. Stdlib only, so the writer job checks out this file and nothing else.
Tests: ``tests/test_backlog_reconcile_apply.py``.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

ALLOWED: dict[str, tuple[str, ...]] = {
    "propose": ("edit_comment",),
    "file": ("edit_comment", "add_comment", "create"),
    "file-and-close": ("edit_comment", "add_comment", "create", "close"),
}
MAX_CLOSES = 10
MAX_CREATES = 10

Runner = Callable[[Sequence[str]], str]


class PlanError(Exception):
    """The plan is unreadable or asks for a write its mode does not allow."""


def gh(args: Sequence[str]) -> str:
    """Run ``gh`` and return its stdout; a failed call raises."""
    out = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                         check=True)
    return out.stdout


def load_plan(path: Path) -> tuple[str, list[Mapping[str, object]]]:
    """``(mode, writes)`` from the plan file, checked against ``ALLOWED`` and the caps."""
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PlanError(f"cannot read the plan {path}: {exc}") from exc
    mode = plan.get("mode") if isinstance(plan, dict) else None
    writes = plan.get("writes") if isinstance(plan, dict) else None
    if mode not in ALLOWED or not isinstance(writes, list):
        raise PlanError(f"the plan has no known mode and writes list (mode {mode!r})")
    for w in writes:
        op = w.get("op") if isinstance(w, dict) else None
        if op not in ALLOWED[mode]:
            raise PlanError(f"mode {mode!r} does not allow the write {op!r}")
    for op, cap in (("close", MAX_CLOSES), ("create", MAX_CREATES)):
        if sum(1 for w in writes if w["op"] == op) > cap:
            raise PlanError(f"the plan holds more than {cap} {op} writes; nothing was written")
    for w in writes:
        _check(w)
    return mode, writes


_FIELDS = {"edit_comment": (("number", "comment_id"), ("body",)), "add_comment": (("number",), ("body",)),
           "close": (("number",), ()), "create": ((), ("title", "body", "status"))}


def _check(w: Mapping[str, object]) -> None:
    """Every field the write's op needs, so a malformed plan fails before the first write."""
    numbers, texts = _FIELDS[str(w["op"])]
    for key in numbers:
        _number(w, key)
    for key in texts:
        _text(w, key)


def _number(w: Mapping[str, object], key: str = "number") -> str:
    value = w.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise PlanError(f"write {w.get('op')!r} has no valid {key}")
    return str(value)


def _text(w: Mapping[str, object], key: str) -> str:
    value = w.get(key)
    if not isinstance(value, str) or not value:
        raise PlanError(f"write {w.get('op')!r} has no {key}")
    return value


class WriteError(Exception):
    """A ``gh`` call failed part way through the plan; ``done`` holds the writes already made."""

    def __init__(self, message: str, done: Sequence[str]) -> None:
        super().__init__(message)
        self.done = list(done)


def apply(writes: Sequence[Mapping[str, object]], repo: str, run: Runner, workdir: Path) -> list[str]:
    """Run each write in order; returns one log line per write.

    A failed ``gh`` call raises ``WriteError`` with gh's stderr and the writes made before it, as does
    a create with no issue URL (``load_plan`` has already checked every field, so nothing else fails here).
    """
    log: list[str] = []
    try:
        _apply(writes, repo, run, workdir, log)
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip() if isinstance(exc.stderr, str) else ""
        raise WriteError(f"gh {' '.join(str(a) for a in exc.cmd[1:3])} failed (exit {exc.returncode})"
                         + (f": {stderr}" if stderr else ""), log) from exc
    except PlanError as exc:
        raise WriteError(str(exc), log) from exc
    return log


def _apply(writes: Sequence[Mapping[str, object]], repo: str, run: Runner, workdir: Path, log: list[str]) -> None:
    body_file = workdir / "body.md"
    labels_done: set[str] = set()
    for w in writes:
        op = w["op"]
        if op == "edit_comment":
            body_file.write_text(_text(w, "body"), encoding="utf-8")
            run(["api", "--method", "PATCH", f"repos/{repo}/issues/comments/{_number(w, 'comment_id')}",
                 "-F", f"body=@{body_file}"])
            log.append(f"edited the status comment on #{_number(w)}")
        elif op == "add_comment":
            body_file.write_text(_text(w, "body"), encoding="utf-8")
            run(["issue", "comment", _number(w), "--repo", repo, "--body-file", str(body_file)])
            log.append(f"added a status comment to #{_number(w)}")
        elif op == "close":
            run(["issue", "close", _number(w), "--repo", repo, "--reason", "completed"])
            log.append(f"closed #{_number(w)}")
        elif op == "create":
            _create(w, repo, run, body_file, labels_done, log)


def _create(w: Mapping[str, object], repo: str, run: Runner, body_file: Path, labels_done: set[str],
            log: list[str]) -> None:
    raw = w.get("labels")
    labels = [x for x in raw if isinstance(x, str) and x] if isinstance(raw, list) else []
    for label in labels:
        if label not in labels_done:
            existing = json.loads(run(["label", "list", "--repo", repo, "--search", label, "--json", "name"]) or "[]")
            if not any(isinstance(x, dict) and x.get("name") == label for x in existing):
                run(["label", "create", label, "--repo", repo])
            labels_done.add(label)
    body_file.write_text(_text(w, "body"), encoding="utf-8")
    args = ["issue", "create", "--repo", repo, "--title", _text(w, "title"), "--body-file", str(body_file)]
    for label in labels:
        args += ["--label", label]
    lines = run(args).strip().splitlines()
    url = lines[-1] if lines else ""
    number = url.rstrip("/").rpartition("/")[2]
    if not number.isdigit():
        raise PlanError(f"gh issue create gave no issue URL: {url!r}")
    log.append(f"filed #{number}: {w.get('title')}")
    body_file.write_text(_text(w, "status"), encoding="utf-8")
    run(["issue", "comment", number, "--repo", repo, "--body-file", str(body_file)])


def main(argv: Sequence[str] | None = None, run: Runner = gh) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    p.add_argument("--plan", type=Path, required=True, help="the reconcile --scheduled output")
    p.add_argument("--repo", required=True, help="owner/name")
    p.add_argument("--dry-run", action="store_true", help="print the writes and make none")
    args = p.parse_args(argv)
    try:
        mode, writes = load_plan(args.plan)
        if args.dry_run:
            for w in writes:
                print(f"dry run: {w['op']} #{w.get('number', 'new')}")
            return 0
        with tempfile.TemporaryDirectory() as tmp:
            for line in apply(writes, args.repo, run, Path(tmp)):
                print(line)
    except PlanError as exc:
        print(f"backlog_reconcile_apply: {exc}", file=sys.stderr)
        return 1
    except WriteError as exc:
        for line in exc.done:
            print(line)
        print(f"backlog_reconcile_apply: {exc}; {len(exc.done)} write(s) were made before it", file=sys.stderr)
        return 1
    print(f"{len(writes)} write(s) made in mode {mode}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
