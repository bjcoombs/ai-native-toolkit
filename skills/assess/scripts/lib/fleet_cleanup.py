"""``--delete-after`` for the fleet runner: remove each clone once it is scanned (issue #569).

A fleet run over hundreds of repositories can clone, scan and drop each one
to keep disk use flat. ``CloneDeleter`` is the ``after`` hook ``run_fleet``
calls in the worker as soon as a target's row is in hand.

The fleet run did not create these clones, so deletion is narrow and fails
closed. Only the scanned checkout (``<repo>/<repo>-main``) is removed, and only
when nothing in it would be lost:

- the row is ``ok`` (a failed scan keeps its clone for diagnosis and a retry);
- the checkout is in the ghsync layout, not a symlink, and a main clone (a
  ``.git`` directory at the work-tree root, not a linked worktree);
- ``git status --porcelain --ignored`` is empty: no change, untracked or
  ignored file;
- there is no stash, no commit on ``HEAD`` or any ref (branch, tag, note,
  replace or custom ref) that no remote branch holds, and no linked worktree;
- it is not, and does not contain, a protected path (the output directory,
  the working directory, the toolkit itself).

Afterwards the sibling ``worktree/`` and the repository directory are removed
only if empty (``rmdir``). Any check that cannot be run keeps the clone. The
outcome is ``deleted`` or a keep-reason token; it never changes a row.

`--delete-after` (issue #569). `CloneDeleter` is a `run_fleet` `after` hook;
`delete_clone` removes only the scanned `<repo>/<repo>-main` checkout, then `rmdir`s an
empty `worktree/` and repository directory (a missing `worktree/` does not stop the
repository directory's `rmdir`). `deletion_problem` fails closed with a
keep-reason token (`KEEP_REASONS`): a non-ok row, a non-layout or symlinked path, a
checkout that is not a main clone at its work-tree root, one holding a protected path
(`--out`, the working directory, the toolkit), any `git status --porcelain --ignored`
output, a stash, a commit on `HEAD` or any ref (`rev-list --all`) that no remote branch holds, a
linked worktree, or a git call that cannot run. Outcomes never reach a row or the stamp.
The `rmtree` error hook (`_clear_read_only`, #712) makes a read-only file writable and
retries its deletion once, since Windows refuses to delete git's read-only objects; any
other failure keeps the clone as `delete_error`.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import threading
from collections.abc import Callable, Iterable
from pathlib import Path

from lib.fleet import STATUS_OK, FleetRow, Target, checkout_name

DELETED = "deleted"

# Keep-reason tokens: why a clone was left in place.
KEEP_SCAN_NOT_OK = "scan_not_ok"
KEEP_NOT_LAYOUT = "not_ghsync_layout"
KEEP_SYMLINK = "symlink"
KEEP_NOT_MAIN_CLONE = "not_a_main_clone"
KEEP_PROTECTED = "holds_protected_path"
KEEP_LOCAL_CHANGES = "local_changes"
KEEP_STASH = "stash"
KEEP_UNPUSHED = "unpushed_commits"
KEEP_WORKTREES = "linked_worktrees"
KEEP_GIT_ERROR = "git_error"
KEEP_DELETE_ERROR = "delete_error"
KEEP_REASONS = (
    KEEP_SCAN_NOT_OK, KEEP_NOT_LAYOUT, KEEP_SYMLINK, KEEP_NOT_MAIN_CLONE, KEEP_PROTECTED,
    KEEP_LOCAL_CHANGES, KEEP_STASH, KEEP_UNPUSHED, KEEP_WORKTREES, KEEP_GIT_ERROR, KEEP_DELETE_ERROR,
)


class _GitError(Exception):
    pass


def _git_out(checkout: Path, *args: str) -> str:
    """Stdout of a read-only git call; raises ``_GitError`` when it cannot run."""
    try:
        done = subprocess.run(
            ["git", "-C", str(checkout), *args], capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            check=True, timeout=60, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired) as e:
        raise _GitError(str(e)) from e
    return done.stdout


def _holds(checkout: Path, protected: Iterable[Path]) -> bool:
    return any(p == checkout or checkout in p.parents for p in protected)


def _git_problem(checkout: Path) -> str | None:
    """A keep reason from the checkout's git state, or None when nothing would be lost."""
    try:
        top = _git_out(checkout, "rev-parse", "--show-toplevel").strip()
        if not top or Path(top).resolve() != checkout:
            return KEEP_NOT_MAIN_CLONE
        if _git_out(checkout, "status", "--porcelain", "--ignored").strip():
            return KEEP_LOCAL_CHANGES
        if _git_out(checkout, "for-each-ref", "refs/stash").strip():
            return KEEP_STASH
        # Every ref (branches, tags, notes, replace, custom) and HEAD.
        unpushed = _git_out(checkout, "rev-list", "--count", "--all", "HEAD", "--not", "--remotes")
        if unpushed.strip() != "0":
            return KEEP_UNPUSHED
        listing = _git_out(checkout, "worktree", "list", "--porcelain")
        if sum(1 for line in listing.splitlines() if line.startswith("worktree ")) != 1:
            return KEEP_WORKTREES
    except _GitError:
        return KEEP_GIT_ERROR
    return None


def deletion_problem(target: Target, row: FleetRow, protected: Iterable[Path]) -> str | None:
    """Why ``target``'s checkout must be kept, or None when it may be deleted."""
    if row["status"] != STATUS_OK:
        return KEEP_SCAN_NOT_OK
    checkout = target.checkout
    if checkout.is_symlink() or checkout.parent.is_symlink():
        return KEEP_SYMLINK
    checkout = checkout.resolve()
    if checkout.name != checkout_name(checkout.parent):
        return KEEP_NOT_LAYOUT
    git_dir = checkout / ".git"
    if not git_dir.is_dir() or git_dir.is_symlink():
        return KEEP_NOT_MAIN_CLONE
    if _holds(checkout, (p.resolve() for p in protected)):
        return KEEP_PROTECTED
    return _git_problem(checkout)


def _clear_read_only(func: Callable[..., object], path: str, exc: object) -> None:
    """rmtree's error hook: make a file writable and retry its deletion once.

    Git marks pack and object files read-only, and Windows refuses to delete a
    read-only file. Any other failure, and a second failure, propagate to
    ``delete_clone`` (``exc`` is the exception, or onerror's exc_info tuple).
    """
    if func not in (os.unlink, os.remove, os.rmdir):
        raise exc if isinstance(exc, BaseException) else sys.exc_info()[1] or OSError(path)
    os.chmod(path, os.stat(path).st_mode | stat.S_IWRITE)
    func(path)


def _rmtree(path: Path) -> None:
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_clear_read_only)
    else:  # pragma: no cover - Python 3.11 has only onerror
        shutil.rmtree(path, onerror=_clear_read_only)


def delete_clone(target: Target, row: FleetRow, protected: Iterable[Path]) -> str:
    """Delete ``target``'s checkout when ``deletion_problem`` allows; ``deleted`` or a keep reason."""
    problem = deletion_problem(target, row, protected)
    if problem is not None:
        return problem
    checkout = target.checkout.resolve()
    try:
        _rmtree(checkout)
    except OSError:
        return KEEP_DELETE_ERROR
    for directory in (checkout.parent / "worktree", checkout.parent):
        try:
            directory.rmdir()  # only succeeds when empty
        except FileNotFoundError:
            continue  # no worktree/ beside the clone: the repository directory may still be empty
        except OSError:
            break
    return DELETED


class CloneDeleter:
    """``run_fleet``'s ``after`` hook: delete each scanned clone, recording the outcome by repo id."""

    def __init__(self, protected: Iterable[Path]) -> None:
        self.protected = tuple(Path(p).resolve() for p in protected)
        self.outcomes: dict[str, str] = {}
        self._lock = threading.Lock()

    def __call__(self, target: Target, row: FleetRow) -> None:
        try:
            outcome = delete_clone(target, row, self.protected)
        except Exception:  # noqa: BLE001 - a failed delete never stops the run
            outcome = KEEP_DELETE_ERROR
        with self._lock:
            self.outcomes[target.repo] = outcome

    def deleted(self) -> list[str]:
        return sorted(repo for repo, outcome in self.outcomes.items() if outcome == DELETED)

    def kept(self) -> list[tuple[str, str]]:
        return sorted((repo, outcome) for repo, outcome in self.outcomes.items() if outcome != DELETED)
