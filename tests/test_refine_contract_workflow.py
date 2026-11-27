"""The contract-draft job's pull-request handling, run for real (issue #1565).

`reusable-refine-contract.yml` drafts the run's contract on the branch
`refine/contract-draft-<run_id>` and keeps one pull request for it. These tests
run the job's own step scripts under bash against a local bare remote, with
`gh` replaced by a stub that logs its arguments:

- a push whose `gh pr create` failed, or a draft pull request closed unmerged,
  gets its pull request opened by the next run, with nothing pushed again;
- an open pull request on an unchanged draft gets no comment;
- a draft already on the default branch opens no pull request;
- an empty queue closes the open draft pull request, with a comment.

The `if:` of each step is checked as text and applied here by hand.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
import yaml  # required: the plugin contract job installs pyyaml

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / ".github" / "workflows" / "reusable-refine-contract.yml"
DRAFT = REPO / "scripts" / "contract" / "draft.py"
RUN_ID = "issues-agent-ready"
BRANCH = "refine/contract-draft-%s" % RUN_ID

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not all(shutil.which(t) for t in ("bash", "git", "jq")),
    reason="the job runs under bash with git and jq on Linux runners")

QUEUE = {"repository": "acme/widgets", "issues": [
    {"number": 11, "title": "Issue 11", "url": "u", "criteria": ["`make lint` exits 0"]},
]}
EMPTY = {"repository": "acme/widgets", "issues": []}

GH_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$GH_LOG"
if [ "$1 $2" = "pr list" ]; then printf '%s\\n' "${GH_PRS:-[]}"; fi
if [ "$1 $2" = "pr create" ] && [ "${GH_CREATE_FAILS:-}" = 1 ]; then exit 1; fi
exit 0
"""


def _steps() -> List[Dict[str, Any]]:
    doc: Dict[str, Any] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps: List[Dict[str, Any]] = doc["jobs"]["contract"]["steps"]
    return steps


def _step(pattern: str) -> Dict[str, Any]:
    return next(s for s in _steps() if re.search(pattern, str(s.get("name", ""))))


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@example.test",
                           "-c", "commit.gpgsign=false", *args], check=True, capture_output=True,
                          encoding="utf-8", errors="replace")
    return proc.stdout.strip()


class Job:
    """One bare remote with `main`, and a runner that replays the job against it."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.remote = tmp / "remote.git"
        self.url = self.remote.as_uri()
        self.runs = 0
        seed = tmp / "seed"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remote)], check=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
        _git(seed, "commit", "-q", "--allow-empty", "-m", "init")
        _git(seed, "push", "-q", self.url, "main")
        self.bin = tmp / "bin"
        self.bin.mkdir()
        (self.bin / "gh").write_text(GH_STUB, encoding="utf-8")
        (self.bin / "python3").write_text('#!/bin/sh\nexec "%s" "$@"\n' % sys.executable, encoding="utf-8")
        for tool in ("gh", "python3"):
            (self.bin / tool).chmod(0o755)
        self.log = tmp / "gh.log"

    def remote_sha(self, ref: str) -> Optional[str]:
        proc = subprocess.run(["git", "-C", str(self.remote), "rev-parse", "-q", "--verify", ref],
                              capture_output=True, encoding="utf-8", errors="replace", check=False)
        return proc.stdout.strip() or None

    def gh_calls(self) -> List[str]:
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []

    def run(self, queue: Dict[str, Any], prs: Any = (), create_fails: bool = False) -> Dict[str, str]:
        """Replay the job's steps in live mode; return the draft step's outputs."""
        self.runs += 1
        self.log.unlink(missing_ok=True)
        work = self.tmp / ("run%d" % self.runs)
        subprocess.run(["git", "clone", "-q", "--depth=1", "--branch", "main", self.url, str(work)], check=True,
                       capture_output=True)
        (work / ".refine").mkdir()
        (work / ".refine" / "queue.json").write_text(json.dumps(queue), encoding="utf-8")
        output, summary = self.tmp / ("out%d" % self.runs), self.tmp / ("summary%d" % self.runs)
        output.touch()
        env = {
            "PATH": "%s%s%s" % (self.bin, os.pathsep, os.environ["PATH"]),
            "HOME": str(self.tmp), "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
            "GH_TOKEN": "t0ken", "GH_REPO": "acme/widgets", "GH_LOG": str(self.log),
            "GH_PRS": json.dumps([{"number": n} for n in prs]), "GH_CREATE_FAILS": "1" if create_fails else "",
            "REFINE_MODE": "live", "RUN_ID": RUN_ID, "DEFAULT_BRANCH": "main", "DRAFT": str(DRAFT),
            "GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(summary),
        }
        self._bash(_step(r"^Draft the contract"), work, env, {})
        outputs = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines() if "=" in line)
        for step, applies in ((_step(r"^Push the draft"), outputs.get("branch", "") != ""),
                              (_step(r"^Close the draft"), outputs.get("empty", "") == "true")):
            if applies:
                self._bash(step, work, env, outputs, check=not create_fails)
        return outputs

    @staticmethod
    def _bash(step: Dict[str, Any], work: Path, env: Dict[str, str], outputs: Dict[str, str],
              check: bool = True) -> None:
        step_env = {k: re.sub(r"\$\{\{\s*steps\.draft\.outputs\.(\w+)\s*\}\}",
                              lambda m: outputs.get(m.group(1), ""), str(v))
                    for k, v in (step.get("env") or {}).items()}
        proc = subprocess.run(["bash", "-e", "-c", step["run"]], cwd=work, env={**env, **step_env},
                              capture_output=True, encoding="utf-8", errors="replace", check=False)
        if check:
            assert proc.returncode == 0, proc.stderr


def test_step_conditions_are_live_only() -> None:
    push, close = _step(r"^Push the draft"), _step(r"^Close the draft")
    assert push["if"] == "steps.draft.outputs.branch != '' && inputs.mode == 'live'"
    assert close["if"] == "steps.draft.outputs.empty == 'true' && inputs.mode == 'live'"
    assert "git" not in re.findall(r"\b\w+\b", close["run"]), "closing pushes nothing"


def test_pull_request_is_opened_again_after_a_failed_create(tmp_path: Path) -> None:
    job = Job(tmp_path)
    job.run(QUEUE, create_fails=True)
    pushed = job.remote_sha(BRANCH)
    assert pushed and any(c.startswith("pr create") for c in job.gh_calls())

    outputs = job.run(QUEUE)  # the branch holds the current draft; no pull request is open
    assert outputs.get("branch") == BRANCH and outputs.get("changed") == "false"
    assert job.remote_sha(BRANCH) == pushed, "nothing new to push"
    calls = job.gh_calls()
    assert any(c.startswith("pr create --base main --head %s" % BRANCH) for c in calls), calls
    assert not any(c.startswith("pr comment") for c in calls)


def test_open_pull_request_on_an_unchanged_draft_gets_no_comment(tmp_path: Path) -> None:
    job = Job(tmp_path)
    job.run(QUEUE)
    job.run(QUEUE, prs=[7])
    calls = job.gh_calls()
    assert not any(c.startswith(("pr create", "pr comment")) for c in calls), calls


def test_changed_draft_comments_on_the_open_pull_request(tmp_path: Path) -> None:
    job = Job(tmp_path)
    job.run(QUEUE)
    first = job.remote_sha(BRANCH)
    grown = dict(QUEUE, issues=QUEUE["issues"] + [
        {"number": 12, "title": "Issue 12", "url": "u", "criteria": ["`make test` exits 0"]}])
    outputs = job.run(grown, prs=[7])
    assert outputs.get("changed") == "true" and job.remote_sha(BRANCH) != first
    calls = job.gh_calls()
    assert any(c.startswith("pr comment 7") for c in calls) and not any(c.startswith("pr create") for c in calls)


def test_draft_already_on_the_default_branch_opens_no_pull_request(tmp_path: Path) -> None:
    job = Job(tmp_path)
    job.run(QUEUE)
    merged = tmp_path / "merge"
    subprocess.run(["git", "clone", "-q", job.url, str(merged)], check=True, capture_output=True)
    _git(merged, "merge", "-q", "--ff-only", "origin/%s" % BRANCH)
    _git(merged, "push", "-q", "origin", "main")
    outputs = job.run(QUEUE)
    assert outputs.get("branch", "") == ""
    assert not any(c.startswith("pr create") for c in job.gh_calls())


def test_empty_queue_closes_the_open_draft_pull_request(tmp_path: Path) -> None:
    job = Job(tmp_path)
    job.run(QUEUE)
    outputs = job.run(EMPTY, prs=[7])
    assert outputs.get("empty") == "true" and outputs.get("branch", "") == ""
    closes = [c for c in job.gh_calls() if c.startswith("pr close")]
    assert len(closes) == 1 and closes[0].startswith("pr close 7 --comment")
    assert "nothing to draft" in closes[0]


def test_empty_queue_with_no_open_pull_request_closes_nothing(tmp_path: Path) -> None:
    job = Job(tmp_path)
    job.run(EMPTY)
    assert not any(c.startswith("pr close") for c in job.gh_calls())
