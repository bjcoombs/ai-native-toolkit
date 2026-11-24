"""Deliver the ``/assess --fix`` files as one reviewable change (#1154).

``ignored`` names the written files ``.gitignore`` ignores; they stay out of the index and the
commit, and the report names the manual step. ``stage`` adds the other written files to the git index, so the re-run that proves the gate reads
them (several scans read ``git ls-files``) and the user reviews them as one staged change.

``deliver`` commits the staged files to a new local ``assess/fix-<gate>`` branch when the tree
was clean before the run outside ``.assess/`` (the assessment's own output, which the commit
leaves out by naming only the written files). It never pushes unless asked: without
``open_pr`` the result carries ``next_command``, the exact push and pull-request command. With
``open_pr`` (``assess_fix.py --pr``) it also pushes and opens a pull request (GitHub, through
``gh``) or merge request (GitLab, through ``glab``), only when the ``origin`` host has a CLI, that
CLI is authenticated, and the run started on the default branch's tip, so no other branch's
commits ride into the request (``not_on_default_branch`` otherwise). A dirty tree commits
nothing: the files stay staged. A failed step stops there with its reason; nothing is retried.
Commands run through an injectable ``Runner`` so tests make no network call.
"""
from __future__ import annotations

import shlex
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from lib.run_context_types_fix import Delivery
from lib.starter_files import remote_system

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


# The run's own output: the assessment that precedes ``--fix`` writes it, so it never makes the
# tree dirty, and the commit names only the starter files, so none of it is committed.
ASSESS_DIR = ".assess"


def tree_clean(root: Path, runner: Runner = run) -> bool:
    """No change outside ``.assess/`` (the run's own output)."""
    code, out = runner(["git", "status", "--porcelain", "--", ".", f":(exclude){ASSESS_DIR}"], root)
    return code == 0 and not out


def ignored(root: Path, paths: Sequence[str], runner: Runner = run) -> list[str]:
    """The paths ``.gitignore`` ignores (``git check-ignore``), which ``git add`` would refuse."""
    if not paths:
        return []
    code, out = runner(["git", "check-ignore", "--", *paths], root)
    return [line for line in out.splitlines() if line] if code == 0 else []


def stage(root: Path, paths: Sequence[str], runner: Runner = run) -> bool:
    if not paths:
        return True
    return runner(["git", "add", "--", *paths], root)[0] == 0


def _working(reason: str) -> Delivery:
    return {"mode": "working_tree", "branch": None, "url": None, "reason": reason, "next_command": None}


def _host_cli(url: str) -> str | None:
    system = remote_system(url)
    return "gh" if system == "github" else "glab" if system == "gitlab" else None


def push_command(cli: str | None, branch: str, pushed: bool = False) -> str:
    """The command that pushes ``branch`` (unless ``pushed``) and opens its request by hand."""
    quoted = shlex.quote(branch)
    parts = [] if pushed else [f"git push -u origin {quoted}"]
    if cli == "gh":
        parts.append(f"gh pr create --fill --head {quoted}")
    elif cli == "glab":
        parts.append(f"glab mr create --fill --source-branch {quoted} --yes")
    return " && ".join(parts)


def on_default_tip(root: Path, default_branch: str, runner: Runner = run) -> bool:
    """The checkout is ``default_branch`` and, when ``origin/<default>`` exists, at its tip: a fix
    branch cut here carries no commit of another branch into the pull request."""
    code, current = runner(["git", "branch", "--show-current"], root)
    if code != 0 or current != default_branch:
        return False
    code, remote = runner(["git", "rev-parse", "--verify", "-q", f"refs/remotes/origin/{default_branch}"], root)
    if code != 0:
        return True
    code, head = runner(["git", "rev-parse", "HEAD"], root)
    return code == 0 and head == remote


def deliver(root: Path, branch: str, title: str, body: str, paths: Sequence[str], *, was_clean: bool,
            default_branch: str, open_pr: bool = False, runner: Runner = run) -> Delivery:
    """Commit ``paths`` to a new local ``branch``; with ``open_pr``, push it and open the request.

    Without ``open_pr`` nothing leaves the machine: the result's ``next_command`` pushes and opens
    the request by hand. A dirty tree commits nothing."""
    if not was_clean:
        return _working("dirty_tree")
    code, url = runner(["git", "remote", "get-url", "origin"], root)
    cli = _host_cli(url) if code == 0 else None
    tip = on_default_tip(root, default_branch, runner)
    if runner(["git", "switch", "-c", branch], root)[0] != 0:
        return _working("branch_exists")
    if runner(["git", "commit", "-q", "-m", title, "--", *paths], root)[0] != 0:
        return {"mode": "working_tree", "branch": branch, "url": None, "reason": "commit_failed",
                "next_command": None}

    def local(reason: str) -> Delivery:
        return {"mode": "local_branch", "branch": branch, "url": None, "reason": reason,
                "next_command": push_command(cli, branch)}

    if not open_pr:
        return local("committed_locally")
    if cli is None:
        return local("no_supported_remote")
    if not tip:
        return local("not_on_default_branch")
    if runner([cli, "auth", "status"], root)[0] != 0:
        return local(f"{cli}_not_authenticated")
    if runner(["git", "push", "-q", "-u", "origin", branch], root)[0] != 0:
        return local("push_failed")
    create = ([cli, "pr", "create", "--title", title, "--body", body, "--head", branch] if cli == "gh" else
              [cli, "mr", "create", "--title", title, "--description", body, "--source-branch", branch, "--yes"])
    code, out = runner(create, root)
    if code != 0:
        failed = local("pull_request_failed")
        failed["next_command"] = push_command(cli, branch, pushed=True)
        return failed
    link = next((w for w in reversed(out.split()) if w.startswith("http")), None)
    return {"mode": "pull_request", "branch": branch, "url": link, "reason": "opened", "next_command": None}
