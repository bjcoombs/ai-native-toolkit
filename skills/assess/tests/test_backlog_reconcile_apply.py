"""The scheduled reconcile's writer (#814): ``scripts/backlog_reconcile_apply.py``."""
from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

import backlog_reconcile_apply as ap
from lib.report import backlog_schedule as bs


class FakeGh:
    def __init__(self, labels: Sequence[str] = ()) -> None:
        self.calls: list[list[str]] = []
        self.bodies: list[str] = []
        self.labels = labels

    def __call__(self, args: Sequence[str]) -> str:
        self.calls.append(list(args))
        for i, a in enumerate(args):
            if a == "--body-file":
                self.bodies.append(Path(args[i + 1]).read_text(encoding="utf-8"))
            if a.startswith("body=@"):
                self.bodies.append(Path(a[len("body=@"):]).read_text(encoding="utf-8"))
        if args[:2] == ["label", "list"]:
            return json.dumps([{"name": x} for x in self.labels])
        if args[:2] == ["issue", "create"]:
            return "Creating issue\nhttps://github.com/o/r/issues/31\n"
        return ""


def _plan(tmp_path: Path, mode: str, writes: list[dict[str, object]]) -> Path:
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({"mode": mode, "writes": writes}), encoding="utf-8")
    return path


def test_the_allowed_writes_and_caps_match_the_planner() -> None:
    assert ap.ALLOWED == bs.WRITE_OPS
    assert (ap.MAX_CLOSES, ap.MAX_CREATES) == (bs.MAX_CLOSES, bs.MAX_CREATES)


def test_each_write_runs_its_gh_call(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    writes: list[dict[str, object]] = [
        {"op": "edit_comment", "number": 1, "comment_id": 11, "body": "edited"},
        {"op": "add_comment", "number": 2, "body": "added"},
        {"op": "close", "number": 3},
        {"op": "create", "title": "Meet x.1", "body": "new", "labels": ["assess-finding"], "status": "status"},
    ]
    gh = FakeGh()
    assert ap.main(["--plan", str(_plan(tmp_path, "file-and-close", writes)), "--repo", "o/r"], run=gh) == 0
    assert gh.calls[0][:4] == ["api", "--method", "PATCH", "repos/o/r/issues/comments/11"]
    assert gh.calls[1][:3] == ["issue", "comment", "2"]
    assert gh.calls[2] == ["issue", "close", "3", "--repo", "o/r", "--reason", "completed"]
    assert gh.calls[4] == ["label", "create", "assess-finding", "--repo", "o/r"]
    assert gh.calls[5][:2] == ["issue", "create"] and gh.calls[5][-2:] == ["--label", "assess-finding"]
    assert gh.calls[6][:3] == ["issue", "comment", "31"]
    assert gh.bodies == ["edited", "added", "new", "status"]
    assert "filed #31: Meet x.1" in capsys.readouterr().out
    known = FakeGh(labels=["assess-finding"])
    ap.apply(writes[3:], "o/r", known, tmp_path)
    assert not any(c[:2] == ["label", "create"] for c in known.calls)


@pytest.mark.parametrize(("mode", "writes", "error"), [
    ("propose", [{"op": "close", "number": 1}], "does not allow the write 'close'"),
    ("file", [{"op": "close", "number": 1}], "does not allow"),
    ("propose", [{"op": "add_comment", "number": 1, "body": "x"}], "does not allow"),
    ("chaos", [], "no known mode"),
    ("file-and-close", [{"op": "close", "number": n} for n in range(1, 12)], "more than 10 close"),
    ("file-and-close", [{"op": "close", "number": "1"}], "no valid number"),
    ("propose", [{"op": "edit_comment", "number": 1, "comment_id": 2}], "has no body"),
])
def test_a_write_the_mode_does_not_allow_fails_before_any_write(
        tmp_path: Path, capsys: pytest.CaptureFixture[str], mode: str, writes: list[dict[str, object]],
        error: str) -> None:
    gh = FakeGh()
    assert ap.main(["--plan", str(_plan(tmp_path, mode, writes)), "--repo", "o/r"], run=gh) == 1
    assert error in capsys.readouterr().err
    assert gh.calls == []


def test_dry_run_and_unreadable_plans(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    gh = FakeGh()
    plan = _plan(tmp_path, "propose", [{"op": "edit_comment", "number": 1, "comment_id": 2, "body": "b"}])
    assert ap.main(["--plan", str(plan), "--repo", "o/r", "--dry-run"], run=gh) == 0
    assert gh.calls == [] and "dry run: edit_comment #1" in capsys.readouterr().out
    assert ap.main(["--plan", str(tmp_path / "missing.json"), "--repo", "o/r"], run=gh) == 1


def test_a_create_without_an_issue_url_fails(tmp_path: Path) -> None:
    with pytest.raises(ap.WriteError, match="no issue URL"):
        ap.apply([{"op": "create", "title": "t", "body": "b", "status": "s"}], "o/r", lambda args: "", tmp_path)


def test_a_failed_gh_call_prints_its_stderr_and_the_writes_already_made(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def run(args: Sequence[str]) -> str:
        if args[:2] == ["issue", "close"]:
            raise subprocess.CalledProcessError(1, ["gh", *args], stderr="HTTP 403: Resource not accessible\n")
        return ""
    writes: list[dict[str, object]] = [
        {"op": "edit_comment", "number": 1, "comment_id": 11, "body": "edited"},
        {"op": "close", "number": 3},
        {"op": "close", "number": 4},
    ]
    assert ap.main(["--plan", str(_plan(tmp_path, "file-and-close", writes)), "--repo", "o/r"], run=run) == 1
    out = capsys.readouterr()
    assert "edited the status comment on #1" in out.out and "closed" not in out.out
    assert "gh issue close failed (exit 1): HTTP 403: Resource not accessible" in out.err
    assert "1 write(s) were made before it" in out.err


def test_a_failed_status_comment_after_a_create_logs_the_new_item(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def run(args: Sequence[str]) -> str:
        if args[:2] == ["issue", "create"]:
            return "https://github.com/o/r/issues/31\n"
        if args[:3] == ["issue", "comment", "31"]:
            raise subprocess.CalledProcessError(1, ["gh", *args], stderr="HTTP 502\n")
        return ""
    writes: list[dict[str, object]] = [{"op": "create", "title": "Meet x.1", "body": "new", "status": "s"}]
    assert ap.main(["--plan", str(_plan(tmp_path, "file", writes)), "--repo", "o/r"], run=run) == 1
    out = capsys.readouterr()
    assert "filed #31: Meet x.1" in out.out
    assert "gh issue comment failed (exit 1): HTTP 502" in out.err and "1 write(s) were made before it" in out.err


def test_a_create_without_a_url_after_earlier_writes_lists_them(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    writes: list[dict[str, object]] = [
        {"op": "add_comment", "number": 2, "body": "added"},
        {"op": "create", "title": "t", "body": "b", "status": "s"},
    ]
    assert ap.main(["--plan", str(_plan(tmp_path, "file", writes)), "--repo", "o/r"], run=lambda args: "") == 1
    out = capsys.readouterr()
    assert "added a status comment to #2" in out.out
    assert "no issue URL" in out.err and "1 write(s) were made before it" in out.err
