"""Deliver the ``/assess --fix`` files as one reviewable change (#1154).

``stage`` adds the written files to the git index, so the re-run that proves the gate reads
them (several scans read ``git ls-files``) and the user reviews them as one staged change.

``open_change`` turns that staged change into a branch and a pull request (GitHub, through
``gh``) or merge request (GitLab, through ``glab``) when every precondition holds: a git
checkout whose tree was clean before the run (so nothing of the user's rides along), an
``origin`` remote on a host the CLI serves, and that CLI authenticated. Otherwise the files stay
staged in the working tree and ``Delivery.reason`` names why. A failed step stops there with
its reason; nothing is retried. Commands run through an injectable ``Runner`` so tests make no
network call.
"""
from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from lib.run_context_types_fix import Delivery

Runner = Callable[[Sequence[str], Path], tuple[int, str]]


def run(cmd: Sequence[str], cwd: Path) -> tuple[int, str]:
    """Run ``cmd`` in ``cwd``: its exit code and stdout (stripped); 127 when it cannot start."""
    try:
        out = subprocess.run(list(cmd), cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""
    return out.returncode, out.stdout.strip()


def is_git_repo(root: Path, runner: Runner = run) -> bool:
    code, out = runner(["git", "rev-parse", "--is-inside-work-tree"], root)
    return code == 0 and out == "true"


def tree_clean(root: Path, runner: Runner = run) -> bool:
    code, out = runner(["git", "status", "--porcelain"], root)
    return code == 0 and not out


def stage(root: Path, paths: Sequence[str], runner: Runner = run) -> bool:
    if not paths:
        return True
    return runner(["git", "add", "--", *paths], root)[0] == 0


def _working(reason: str) -> Delivery:
    return {"mode": "working_tree", "branch": None, "url": None, "reason": reason}


def _host_cli(url: str) -> str | None:
    low = url.lower()
    return "glab" if "gitlab" in low else "gh" if "github" in low else None


def open_change(root: Path, branch: str, title: str, body: str, *, was_clean: bool,
                runner: Runner = run) -> Delivery:
    """A branch, commit, push and pull (merge) request, or the reason the files stay staged."""
    if not was_clean:
        return _working("dirty_tree")
    code, url = runner(["git", "remote", "get-url", "origin"], root)
    cli = _host_cli(url) if code == 0 else None
    if cli is None:
        return _working("no_supported_remote")
    if runner([cli, "auth", "status"], root)[0] != 0:
        return _working(f"{cli}_not_authenticated")
    if runner(["git", "switch", "-c", branch], root)[0] != 0:
        return _working("branch_exists")
    steps: list[tuple[list[str], str]] = [
        (["git", "commit", "-q", "-m", title], "commit_failed"),
        (["git", "push", "-q", "-u", "origin", branch], "push_failed"),
    ]
    for cmd, reason in steps:
        if runner(cmd, root)[0] != 0:
            return {"mode": "working_tree", "branch": branch, "url": None, "reason": reason}
    create = ([cli, "pr", "create", "--title", title, "--body", body, "--head", branch] if cli == "gh" else
              [cli, "mr", "create", "--title", title, "--description", body, "--source-branch", branch, "--yes"])
    code, out = runner(create, root)
    if code != 0:
        return {"mode": "working_tree", "branch": branch, "url": None, "reason": "pull_request_failed"}
    link = next((w for w in reversed(out.split()) if w.startswith("http")), None)
    return {"mode": "pull_request", "branch": branch, "url": link, "reason": "opened"}
