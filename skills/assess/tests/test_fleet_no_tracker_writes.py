"""A ``/fleet`` run writes nothing to any tracker (issue #1541, #777 success criterion 5).

``/fleet`` never files, edits, closes or comments on an issue in a member repository:
filing is a per-repository opt-in of ``/assess`` (maintainer decision on #777). The
only steps of a run that reach a tracker are the platform pass (``fleet.py
--platform``, ``lib/fleet/fleet_platform.py``), whose ``assess_signals.py`` children run
``gh`` and ``glab`` through ``lib/hosting/platform_cli.py``, and nothing else: the
offline scan and the ``--label`` step (``skills/fleet/scripts/fleet_labels.py``)
make no call at all.

The test puts a fake ``gh`` and ``glab`` first on PATH. Each one is the same Python
shim: it appends its argv to a log and answers the reads the platform pass makes
(auth probes, ``gh api`` and ``glab api`` GETs, ``gh pr list``, ``gh repo view``) with
one recent merge, so the pass goes as deep as it can (bot lookups, approvals, notes,
the merged CI configuration). The run covers a GitHub and a GitLab repository, and
the test asserts both fakes were reached before it asserts no call wrote.

The shim runs on Windows too. ``subprocess`` there finds only ``gh.exe`` (CreateProcess
appends ``.exe`` and ignores PATHEXT), so a ``.cmd`` wrapper would never be called.
On Windows each fake is ``gh.exe`` built the way pip builds a console script: the
distlib launcher (``t64.exe`` from the pip wheel ``ensurepip`` bundles), a ``#!``
line naming this interpreter, and a zip whose ``__main__.py`` is the shim. On POSIX
it is a ``/bin/sh`` wrapper that execs this interpreter on the shim.
"""
from __future__ import annotations

import ensurepip
import functools
import io
import json
import os
import platform
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from lib.fleet.fleet_platform import run_platform
from lib.fleet.runner import run_signals
from test_fleet import make_repo
from test_fleet_cli import fleet_cli
from test_fleet_labels import cli as labels_cli

LOG_ENV = "FAKE_TRACKER_LOG"

# The shim. ``NAME`` is prepended per binary. Standard library only: it runs under
# this interpreter outside the project's import path.
SHIM = r'''
import json, os, sys
from datetime import datetime, timedelta, timezone

argv = sys.argv[1:]
with open(os.environ["FAKE_TRACKER_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps([NAME, *argv]) + "\n")

now = datetime.now(timezone.utc)
created = (now - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
merged = (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")


def reply(value):
    sys.stdout.write(json.dumps(value))
    sys.exit(0)


def not_found():
    sys.stderr.write("HTTP 404: Not Found\n")
    sys.exit(1)


if argv[:1] == ["auth"]:
    sys.exit(0)
if NAME == "gh":
    if argv[:2] == ["pr", "list"]:
        reply([{"number": 1, "author": {"login": "alice"}, "mergedBy": {"login": "carol"},
                "createdAt": created, "mergedAt": merged, "reviewDecision": "APPROVED",
                "headRefOid": "0" * 40, "comments": [],
                "reviews": [{"author": {"login": "coderabbitai"}, "state": "COMMENTED",
                             "body": "", "submittedAt": merged}]}])
    if argv[:2] == ["repo", "view"]:
        reply({"defaultBranchRef": {"name": "main"}, "isPrivate": False})
    if argv[:1] == ["api"]:
        path = argv[-1]
        if path.startswith("users/"):
            reply({"login": path.split("/", 1)[1], "type": "Bot"})
        if "/rules/branches/" in path:
            reply([])
        if path.endswith("/check-runs"):
            reply({"total_count": 0, "check_runs": []})
        if path.endswith("/status"):
            reply({"state": "success", "statuses": []})
        if path.count("/") == 2:
            reply({"default_branch": "main", "private": False,
                   "security_and_analysis": {"secret_scanning": {"status": "enabled"},
                                             "secret_scanning_push_protection": {"status": "enabled"}}})
    not_found()
if argv[:1] == ["api"]:
    path = argv[-1]
    if "/merge_requests/" in path and path.split("?")[0].endswith("/approvals"):
        reply({"approved_by": [{"user": {"username": "bob"}}]})
    if "/merge_requests/" in path and "/notes" in path:
        reply([])
    if "/merge_requests?" in path:
        reply([{"iid": 1, "author": {"username": "alice"}, "merged_by": {"username": "carol"},
                "created_at": created, "merged_at": merged, "updated_at": merged}])
    if "/approval_rules" in path:
        reply([])
    if "/ci/lint" in path:
        reply({"valid": True, "merged_yaml": "test:\n  script: make test\n"})
    if path.startswith("projects/") and "/" not in path[len("projects/"):]:
        reply({"id": 7, "default_branch": "main", "path_with_namespace": "grp/app"})
not_found()
'''


def _launcher() -> bytes:
    """distlib's console launcher from the pip wheel ``ensurepip`` bundles (Windows only)."""
    member = "t64-arm.exe" if platform.machine().upper() == "ARM64" else "t64.exe"
    for wheel in sorted((Path(ensurepip.__file__).parent / "_bundled").glob("pip-*.whl")):
        with zipfile.ZipFile(wheel) as archive:
            return archive.read(f"pip/_vendor/distlib/{member}")
    pytest.fail("no pip wheel in ensurepip/_bundled to take the distlib launcher from")


def install_fake(bindir: Path, name: str) -> None:
    """A fake ``name`` in ``bindir`` that logs its argv and answers platform reads."""
    source = f"NAME = {name!r}\n{SHIM}"
    if os.name == "nt":
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("__main__.py", source)
        shebang = f'#!"{sys.executable}"\n'.encode()
        (bindir / f"{name}.exe").write_bytes(_launcher() + shebang + stream.getvalue())
        return
    shim = bindir / f"{name}_shim.py"
    shim.write_text(source, encoding="utf-8")
    wrapper = bindir / name
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{shim}" "$@"\n', encoding="utf-8")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


# --- what counts as a write ----------------------------------------------------

# Subcommands of a tracker object that change it (gh and glab share most names).
WRITE_SUBCOMMANDS = frozenset({
    "create", "edit", "close", "reopen", "comment", "note", "delete", "merge", "lock", "unlock",
    "transfer", "pin", "unpin", "review", "approve", "revoke", "update", "ready", "subscribe",
    "unsubscribe", "set", "archive", "unarchive", "rename", "fork", "clone", "sync",
})
TRACKER_OBJECTS = frozenset({
    "issue", "pr", "mr", "label", "release", "repo", "variable", "secret", "milestone",
    "project", "ruleset", "workflow", "run", "cache", "incident", "snippet",
})
FIELD_FLAGS = ("-f", "-F", "--field", "--raw-field")


def _api_method(args: list[str]) -> str | None:
    for i, arg in enumerate(args):
        if arg in ("-X", "--method") and i + 1 < len(args):
            return args[i + 1].upper()
        for prefix in ("-X", "--method="):
            if arg.startswith(prefix) and len(arg) > len(prefix):
                return arg[len(prefix):].upper()
    return None


def _payload(args: list[str]) -> tuple[list[str], bool]:
    """The ``api`` call's field values, separate (``-f x=1``) or attached (``-fx=1``,
    ``--field=x=1``), and whether it sends ``--input``, separate or attached."""
    fields: list[str] = []
    for i, arg in enumerate(args):
        if arg in FIELD_FLAGS:
            fields.append(args[i + 1] if i + 1 < len(args) else "")
        elif arg.startswith(("--field=", "--raw-field=")):
            fields.append(arg.split("=", 1)[1])
        elif arg[:2] in ("-f", "-F") and len(arg) > 2:
            fields.append(arg[2:])
    has_input = any(a == "--input" or a.startswith("--input=") for a in args)
    return fields, has_input


def is_write(call: list[str]) -> bool:
    """A ``gh`` or ``glab`` argv that writes to a tracker.

    ``issue|pr|mr|...`` with a changing subcommand; an ``api`` call with a method other
    than GET, or with a field or ``--input`` and no method (both CLIs then POST). A
    GraphQL call is a read only when every field is inline (no ``@file``), there is no
    ``--input`` and no field holds a mutation: an operation read from a file is opaque,
    so it counts as a write.
    """
    args = call[1:]
    if args[:1] == ["api"]:
        method = _api_method(args)
        if method is not None:
            return method != "GET"
        fields, has_input = _payload(args)
        if not fields and not has_input:
            return False
        if "graphql" not in args or has_input:
            return True
        return any("=@" in f or "mutation" in f for f in fields)
    return len(args) >= 2 and args[0] in TRACKER_OBJECTS and args[1] in WRITE_SUBCOMMANDS


def tracker_writes(calls: list[list[str]]) -> list[list[str]]:
    return [call for call in calls if is_write(call)]


@pytest.mark.parametrize("call", [
    ["gh", "issue", "create", "--repo", "grp/svc", "--title", "t"],
    ["gh", "issue", "edit", "4", "--repo", "grp/svc", "--add-label", "x"],
    ["gh", "issue", "close", "4"],
    ["gh", "issue", "comment", "4", "--body", "b"],
    ["glab", "issue", "note", "4", "-m", "b"],
    ["glab", "mr", "merge", "4"],
    ["gh", "label", "create", "assess-finding"],
    ["gh", "api", "-X", "POST", "repos/grp/svc/issues"],
    ["gh", "api", "--method", "PATCH", "repos/grp/svc/issues/4"],
    ["gh", "api", "-XDELETE", "repos/grp/svc/issues/4/labels/x"],
    ["gh", "api", "--method=PUT", "repos/grp/svc/issues/4/lock"],
    ["gh", "api", "repos/grp/svc/issues", "-f", "title=t"],
    ["glab", "api", "--hostname", "gitlab.com", "projects/1/issues", "--field", "title=t"],
    ["gh", "api", "graphql", "-f", "query=mutation { closeIssue(input: {}) { clientMutationId } }"],
    ["gh", "api", "repos/grp/svc/issues", "-ftitle=t"],
    ["gh", "api", "repos/grp/svc/issues", "-Ftitle=t"],
    ["gh", "api", "repos/grp/svc/issues", "--raw-field=title=t"],
    ["gh", "api", "repos/grp/svc/issues", "--input", "payload.json"],
    ["gh", "api", "repos/grp/svc/issues", "--input=payload.json"],
    ["gh", "api", "graphql", "--input", "payload.json"],
    ["gh", "api", "graphql", "--input=payload.json"],
    ["gh", "api", "graphql", "-F", "query=@operation.graphql"],
])
def test_every_write_shape_is_caught(call):
    assert is_write(call)


@pytest.mark.parametrize("call", [
    ["gh", "auth", "status", "--hostname", "github.com"],
    ["gh", "api", "repos/grp/svc"],
    ["gh", "api", "-X", "GET", "repos/grp/svc/issues", "-f", "state=open"],
    ["gh", "api", "graphql", "-f", "query={ viewer { login } }"],
    ["gh", "api", "graphql", "-fquery={ viewer { login } }", "-F", "first=10"],
    ["gh", "pr", "list", "--repo", "grp/svc", "--state", "merged", "--json", "mergedAt"],
    ["gh", "repo", "view", "grp/svc", "--json", "isPrivate"],
    ["glab", "api", "--hostname", "gitlab.com", "projects/grp%2Fapp/merge_requests/1/notes"],
])
def test_reads_are_not_writes(call):
    assert not is_write(call)


# --- the run --------------------------------------------------------------------


@pytest.fixture
def trackers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Fake ``gh`` and ``glab`` first on PATH, logging to the returned file, and a root
    with one GitHub and one GitLab repository."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("gh", "glab"):
        install_fake(bindir, name)
    log = tmp_path / "tracker-calls.jsonl"
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv(LOG_ENV, str(log))
    monkeypatch.chdir(tmp_path)
    make_repo(tmp_path / "root", "grp/svc", remote="https://github.com/grp/svc.git")
    make_repo(tmp_path / "root", "grp/app", remote="https://gitlab.com/grp/app.git")
    return log


def _calls(log: Path) -> list[list[str]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def _fleet(tmp_path: Path, name: str, *extra: str) -> Path:
    out = tmp_path / name
    assert fleet_cli.main([str(tmp_path / "root"), "--out", str(out), "--no-history",
                           "--no-diagrams", *extra]) == 0
    return out


def _assert_both_trackers_were_read(calls: list[list[str]]) -> None:
    """The fakes were on the path the pass takes: a vacuous pass would see no call."""
    gh = [" ".join(c[1:]) for c in calls if c[0] == "gh"]
    glab = [" ".join(c[1:]) for c in calls if c[0] == "glab"]
    assert any(c.startswith("pr list --repo grp/svc") for c in gh), gh
    assert any(c.startswith("api repos/grp/svc") for c in gh), gh
    assert any(c.startswith("api users/") for c in gh), gh
    assert any("merge_requests?state=merged" in c for c in glab), glab
    assert any("/merge_requests/1/approvals" in c for c in glab), glab
    assert any("/merge_requests/1/notes" in c for c in glab), glab


def test_a_fleet_run_with_the_platform_pass_and_labels_writes_nothing(trackers, tmp_path):
    _fleet(tmp_path, "offline")
    assert _calls(trackers) == []  # the offline scan makes no call at all

    out = _fleet(tmp_path, "platform", "--platform", "--platform-jobs", "1")
    calls = _calls(trackers)
    _assert_both_trackers_were_read(calls)
    assert tracker_writes(calls) == []

    # The --label step (SKILL.md "Optional: repository labels"): prompt, write, check.
    answers = tmp_path / "answers.txt"
    answers.write_text('{"path": "grp/svc", "label": "service", "confidence": 0.9}\n'
                       '{"path": "grp/app", "label": "service", "confidence": 0.9}\n', encoding="utf-8")
    assert labels_cli.main(["prompt", str(out), "--count"]) == 0
    assert labels_cli.main(["prompt", str(out), "--batch", "0"]) == 0
    assert labels_cli.main(["write", str(out), "--model", "test-model", "--answers", str(answers)]) == 0
    assert labels_cli.main(["check", str(out)]) == 0
    assert _calls(trackers) == calls  # the label step made no call


def test_an_injected_issue_write_is_caught(trackers, tmp_path, monkeypatch):
    """The guard fails when the platform pass writes: each child also files an issue."""
    def writing_run(target, scratch, options, suffix):
        document = run_signals(target, scratch, options, suffix)
        binary = "glab" if target.repo == "grp/app" else "gh"
        subprocess.run([binary, "issue", "create", "--repo", target.repo, "--title", "injected"],
                       capture_output=True, encoding="utf-8", errors="replace", check=False)
        return document

    monkeypatch.setattr(fleet_cli, "run_platform", functools.partial(run_platform, run=writing_run))
    _fleet(tmp_path, "injected", "--platform", "--platform-jobs", "1")
    calls = _calls(trackers)
    _assert_both_trackers_were_read(calls)
    assert sorted(tracker_writes(calls)) == [
        ["gh", "issue", "create", "--repo", "grp/svc", "--title", "injected"],
        ["glab", "issue", "create", "--repo", "grp/app", "--title", "injected"],
    ]
