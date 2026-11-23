"""Fleet runner core: headless signals over many checkouts, one JSONL row each (issue #567).

``skills/fleet/scripts/fleet.py`` is the command; this module does the work so
it sits under the lib gates (mypy strict, coverage, the README seam map).

Targets come from a ghsync root (``discover_root``) or a list file
(``targets_from_list``). Each target runs ``assess_signals.py`` (#545) in its
own process, counts-only by default (#546), from a worker pool
(``run_fleet``). A target that cannot be scanned becomes a ``failed`` row with
a reason token; it never stops the run.

Layout read (``skills/ghsync``): ``<root>/<path>/<repo>-main`` with a sibling
``worktree/``, where ``<path>`` is the repo name (GitHub) or the
``path_with_namespace`` (GitLab, any depth). A directory ``D`` is a repository
when it holds ``<D.name>-main``; discovery never descends into one, nor into
``worktree/`` or hidden directories. Discovery goes by layout, not by a valid
``.git``, so a broken checkout is reported failed rather than skipped.

Row identity (``repo``, ``group``) is the same whichever input named the
checkout: the layout path under the root, prefixed with the root's own name
when that path is a single segment (a GitHub org root gives ``org/repo``).
A listed path outside any ``--root`` falls back to the ``origin`` remote's
path, then to the last two directory names. ``group`` is ``repo`` without its
last segment.

Determinism: rows are sorted by ``repo`` and serialised with sorted keys, and
carry no absolute path, duration, stderr text or per-run stamp field (the run
stamp is ``run.json``, lib/fleet/fleet_stamp.py). An ``ok`` row's ``active`` says
whether its ``HEAD`` was committed inside the window (lib/fleet_activity.py). Two runs over unchanged checkouts on the same day write
byte-identical files. ``--hash-paths`` without a salt draws one salt per run,
so such a run joins within itself but is not byte-identical across runs. Under a
salt the output files name each repository by its hashed id (``lib/fleet/fleet_hashed.py``).

The committed tree (#907): an ``ok`` row records the ``HEAD`` it measured
(``head_sha``) and whether tracked files differed from it (``dirty``, the
``/assess`` definition in ``lib/git_churn.py``: untracked files and the
tool's own ``.assess/`` outputs never count, ``.assess/config.toml`` does).
A dirty checkout is scanned from a ``git clone --shared`` of it checked out
at that sha in the scratch directory (``committed_tree``), with the
``origin`` URL copied over, so the row is a function of the commit, the same
as a clean checkout of it. A clean checkout is scanned in place.

``dirty`` is null on an ok row only when the state is unknown, and ``dirty_reason``
then names why (#932, ``DIRTY_UNKNOWN_REASONS``): no commit (scanned in place, there
is no sha to check out), ``git status`` or ``git ls-files`` failing, or index entries
``git status`` never compares (a sparse checkout, skip-worktree or assume-unchanged
entries). Unknown is never read as clean: with a ``HEAD`` such a checkout is scanned
from its committed tree too. A submodule's own modified content never marks the
checkout dirty (``--ignore-submodules=dirty``), so a checkout whose only edit is
inside a submodule is clean and scanned in place, edit included; its row says so
with ``dirty_reason`` ``submodule_dirty`` (#1017), so a run's history can tell that
local edit from a committed change. A moved submodule commit still marks it dirty.

Read only: every git call here and in the scan runs with
``GIT_OPTIONAL_LOCKS=0``, scans run with the scratch directory as their working
directory, and their output lands in that scratch directory, never in a target.

The core of `skills/fleet/scripts/fleet.py` (issue #567): headless signals across a
ghsync root or a list, one JSONL row per repository. `discover_root` walks
`<root>/<path>/<repo>-main` at any depth (by layout, not by a valid `.git`, so a broken
checkout is reported rather than skipped), never descending into a found repository,
`worktree/` or a hidden directory. `targets_from_list` takes a checkout, its repository
directory, a plain checkout or a root-relative path (an entry whose `~user` cannot be
expanded is a `ValueError`, a usage error, and so is one whose expanded home directory
does not exist, since Windows expands `~name` without checking the account, #965); `layout_id` gives both inputs the
same `repo` id (the path under the root, prefixed with the root's name when one
segment), a listed path outside the root falling back to `remote_path` (the `origin`
URL's path) and then to its last two directory names. `scan_target` checks
`checkout_problem` first (`assess_signals` exits 0 on a broken `.git`), then runs
`assess_signals.py` as a subprocess with `GIT_OPTIONAL_LOCKS=0` and `PYTHONUTF8=1` (#712), counts-only unless
told otherwise, from a scratch directory, with a timeout; every failure becomes a
`failed` row with a reason token (`FAILURE_REASONS` or `scan_exit_<code>`), never
message text. `ScanOptions.units` (`fleet.py --monorepo-units`, #866) adds `--units` to the offline
child only (the platform pass reads no unit), so the row's scans carry `units` and
`unit_readings`. A `scan_exit_<code>` failure's stderr tail goes to `write_stderr_tail`
(`<out>/logs/<repo>.stderr`, when `ScanOptions.log_dir` is set), never into the row;
`run_fleet` first removes the `*.stderr` files there (`clear_stderr_logs`, nothing else), and a path resolving outside it is not written.
`remote_path` drops `.`, `..` and empty segments, so a remote-derived id cannot climb out,
and treats a host-less URL (`file:///...`) as no remote, so no local path becomes an id.
A list entry repeating an earlier id is skipped with a note on stderr. `run_fleet` maps targets over a thread pool of those processes, isolates
a raising scan as `scan_error` and sorts rows by `repo`; `write_rows` writes
`repos.jsonl` atomically with sorted keys. Rows keep only the signals `scans` map and
its schema version - no stamp field, path or timing - so reruns are byte-identical.
`run_salt` draws one salt per run for `--hash-paths`; under a salt a failed scan's
stderr tail is not written, since free text can name the checkout (#909). `run_fleet`'s optional `after`
hook (`AfterFn`) runs in the worker on each row as soon as it is in hand
(`fleet_cleanup.CloneDeleter`). Row version 2 adds `active` (`ScanOptions.active_since`,
`fleet_activity.is_active`); `null` when failed or not asked. `head_sha` and `dirty` (#907,
no version bump: readers use `.get`) come from `commit_state` (`git rev-parse HEAD` and
`git_churn`'s two `/assess` dirty pathspecs, read with optional locks off), both `null`
when failed. On an ok row `dirty` is `null` only when unknown, with `dirty_reason` naming why
(#932, `DIRTY_UNKNOWN_REASONS`): `no_commit`, `status_error`, `index_read_error`, or, after a
clean status, index entries `git status` never compares (`_hidden_entries` reads `git ls-files -v`:
`sparse_checkout`, `skip_worktree`, `assume_unchanged`). `commit_state` returns `CommitState`
(`RepoCommit` plus `dirty_reason`). Status runs with `--ignore-submodules=dirty`, so a submodule's
own edits never mark the checkout dirty (unlike `/assess`); a moved submodule commit still does.
A clean checkout whose submodules hold modified tracked content (a second status with
`--ignore-submodules=untracked`) keeps `dirty: false`, is scanned in place, and carries
`dirty_reason: submodule_dirty` (#1017, `SUBMODULE_DIRTY`), the one reason on a non-null `dirty`.
`_read_only_env` drops the repository-local `GIT_*` variables (`REPO_LOCAL_GIT_ENV`, git's
`--local-env-vars`), so a run from a hook or CI step reads each target. `run_signals` scans `committed_tree`: the checkout itself when known clean or without a commit, else (dirty or unknown) a
`git clone --shared --no-checkout` into the scratch directory checked out at `head_sha`
(no hooks, `GIT_LFS_SKIP_SMUDGE=1`, `origin` set to the checkout's), removed afterwards; a
copy that cannot be made is the `committed_tree_error` row. The platform pass goes through
`run_signals` too, so it reads the same tree. The row shape is the `FleetRow`
TypedDict here (kept beside its builder; group roll-up #568, run stamp #569 and later
readers consume it). Subprocess, not import, keeps it off the orchestrator layer.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, TypedDict

from lib.fleet_activity import is_active
from lib.git_churn import ASSESS_CONFIG_PATHSPEC, ASSESS_EXCLUDE_PATHSPEC
from lib.platform_cli import CACHE_ENV
from lib.run_context_types import JsonDict
from lib.signals import offline_scans

# Bumped when a row's envelope (the keys below) changes. Scan blocks carry
# their own shapes, versioned by ``signals_schema_version``.
# 2 (#569): ``active``. ``head_sha`` and ``dirty`` (#907) were added without a
# bump: the history readers reject any other version, and a row written before
# them reads both as None through ``.get``.
# 2 (3.0.0 release): two changes, without a bump.
# - ``dirty_reason`` (#932): a row written before it reads it as None through
#   ``.get``.
# - ``dirty_reason`` gains ``submodule_dirty`` (#1017), the one reason on a non-null
#   ``dirty``: the row reads ``dirty: false`` and was scanned in place, but a
#   submodule held uncommitted tracked edits. Additive: a row written before it
#   reads that case as ``dirty_reason: null``.
FLEET_ROW_VERSION = 2
REPOS_FILE = "repos.jsonl"
SIGNALS_SCRIPT = Path(__file__).resolve().parents[2] / "assess_signals.py"
DEFAULT_TIMEOUT = 600
LOGS_DIR = "logs"
STDERR_TAIL_CHARS = 4000

STATUS_OK = "ok"
STATUS_FAILED = "failed"

# Reason tokens a failed row carries. Never a git or scanner message: rows
# hold no free text.
MISSING_CHECKOUT = "missing_checkout"
NOT_A_WORK_TREE = "not_a_git_work_tree"
SCAN_TIMEOUT = "scan_timeout"
INVALID_OUTPUT = "invalid_output"
SCAN_ERROR = "scan_error"
COMMITTED_TREE_ERROR = "committed_tree_error"  # a dirty checkout's HEAD could not be checked out
FAILURE_REASONS = (MISSING_CHECKOUT, NOT_A_WORK_TREE, SCAN_TIMEOUT, INVALID_OUTPUT, SCAN_ERROR,
                   COMMITTED_TREE_ERROR)
# Plus ``scan_exit_<code>`` for a scan process that exits non-zero.

# Why an ok row's ``dirty`` is null (#932): its dirty state is unknown, never clean.
NO_COMMIT = "no_commit"  # the checkout has no HEAD; scanned in place
STATUS_ERROR = "status_error"  # ``git status`` failed
INDEX_READ_ERROR = "index_read_error"  # ``git ls-files -v`` failed
SPARSE_CHECKOUT = "sparse_checkout"  # skip-worktree entries under core.sparseCheckout
SKIP_WORKTREE = "skip_worktree"  # skip-worktree entries without a sparse checkout
ASSUME_UNCHANGED = "assume_unchanged"  # entries marked assume-unchanged
DIRTY_UNKNOWN_REASONS = (NO_COMMIT, STATUS_ERROR, INDEX_READ_ERROR, SPARSE_CHECKOUT, SKIP_WORKTREE,
                         ASSUME_UNCHANGED)
# On a clean row (#1017): the only uncommitted edit is a submodule's own tracked content,
# which ``dirty`` never counts, so the checkout was scanned in place with that edit.
SUBMODULE_DIRTY = "submodule_dirty"
DIRTY_REASONS = (*DIRTY_UNKNOWN_REASONS, SUBMODULE_DIRTY)

_SKIP_DIRS = frozenset({"worktree"})
_HOSTLESS_URL = re.compile(r"^[a-z][a-z0-9+.-]*://(?:/|$)", re.IGNORECASE)
_REMOTE_PATH = re.compile(r"^(?:[a-z+]+://[^/]+/|[^@/:]+@[^:/]+:|[^@/:]+:)(?P<path>.+?)(?:\.git)?/?$")


class FleetRow(TypedDict):
    fleet_row_version: int
    repo: str  # layout path, e.g. "grpa/svc1"
    group: str  # repo without its last segment
    status: str  # "ok" | "failed"
    reason: str | None  # failure token; None when ok
    signals_schema_version: int | None  # None when failed
    scans: dict[str, JsonDict] | None  # assess_signals ``scans``; None when failed
    active: bool | None  # HEAD committed inside the window; None when failed or not checked
    head_sha: str | None  # the commit measured (#907); None when failed or no commit
    dirty: bool | None  # tracked files differed from HEAD, so HEAD's tree was scanned; None when unknown
    dirty_reason: str | None  # why ``dirty`` is None (DIRTY_UNKNOWN_REASONS), or submodule_dirty; else None


class RepoCommit(TypedDict):
    """The commit a row measured: ``head_sha`` and ``dirty`` as on ``FleetRow``."""

    head_sha: str | None
    dirty: bool | None


class CommitState(RepoCommit):
    """``RepoCommit`` plus ``dirty_reason``: the token for a None ``dirty`` (#932), or
    ``submodule_dirty`` on a clean one (#1017)."""

    dirty_reason: str | None


@dataclass(frozen=True)
class Target:
    repo: str
    checkout: Path

    @property
    def group(self) -> str:
        return self.repo.rsplit("/", 1)[0] if "/" in self.repo else ""


@dataclass(frozen=True)
class ScanOptions:
    counts_only: bool = True
    hash_salt: str | None = None  # set: counts-only paths hashed under it
    window: str | None = None
    scans: str | None = None  # comma-separated, passed through
    timeout: int = DEFAULT_TIMEOUT
    log_dir: Path | None = None  # set: a scan_exit_* row's stderr tail lands in <log_dir>/<repo>.stderr
    active_since: date | None = None  # set: an ok row's ``active`` is HEAD committed on or after it
    fleet_config: Path | None = None  # absolute: scans run with the scratch directory as cwd
    platform: bool = False  # set: assess_signals --platform (the platform pass, lib/fleet/fleet_platform.py)
    cache_dir: Path | None = None  # set: the scan's gh/glab response cache (lib/platform_cli.py)
    ghsync_root: Path | None = None  # set: assess_signals --ghsync-root (GitLab includes, #766)
    units: bool = False  # set: assess_signals --units on the offline scan (monorepo unit rows, #866)


ScanFn = Callable[[Target, Path, ScanOptions], FleetRow]
# Called in the worker with each target's row as soon as it is in hand
# (``--delete-after``, lib/fleet/fleet_cleanup.py). It must not raise.
AfterFn = Callable[[Target, FleetRow], None]


# The repository-local variables git itself clears for a subprocess on another repository
# (``git rev-parse --local-env-vars``). Inherited from a hook or CI step, any of them would
# point every git call, the scratch clone and the scan child at that repository, not the
# target (#907).
REPO_LOCAL_GIT_ENV = frozenset({
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
    "GIT_OBJECT_DIRECTORY", "GIT_DIR", "GIT_WORK_TREE", "GIT_IMPLICIT_WORK_TREE", "GIT_GRAFT_FILE",
    "GIT_INDEX_FILE", "GIT_NO_REPLACE_OBJECTS", "GIT_REPLACE_REF_BASE", "GIT_PREFIX",
    "GIT_INTERNAL_SUPER_PREFIX", "GIT_SHALLOW_FILE", "GIT_COMMON_DIR",
})


def _read_only_env(cache_dir: Path | None = None) -> dict[str, str]:
    # PYTHONUTF8: the scan child reads and writes UTF-8 whatever the host locale
    # (a Windows code page would otherwise garble or reject non-ASCII text).
    env = {key: value for key, value in os.environ.items()
           if key not in REPO_LOCAL_GIT_ENV and not key.startswith("GIT_CONFIG_KEY_")
           and not key.startswith("GIT_CONFIG_VALUE_")}
    env.update({"GIT_OPTIONAL_LOCKS": "0", "PYTHONUTF8": "1"})
    env.pop(CACHE_ENV, None)  # only the platform pass caches, and only in its own directory
    if cache_dir is not None:
        env[CACHE_ENV] = str(cache_dir)
    return env


def _git_output(path: Path, *args: str) -> str | None:
    """Stripped stdout (empty when git printed nothing), or None when git failed."""
    try:
        out = subprocess.run(
            ["git", "-C", str(path), *args], capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            check=True, timeout=30, env=_read_only_env(),
        )
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip()


def _git(path: Path, *args: str) -> str | None:
    return _git_output(path, *args) or None


# --- target discovery ------------------------------------------------------


def checkout_name(repo_dir: Path) -> str:
    return f"{repo_dir.name}-main"


def _is_repo_dir(path: Path) -> bool:
    return (path / checkout_name(path)).is_dir()


def layout_id(repo_dir: Path, root: Path) -> str:
    """``repo_dir`` relative to ``root``, prefixed with ``root``'s name when one segment."""
    rel = repo_dir.relative_to(root).as_posix()
    return rel if "/" in rel else f"{root.name}/{rel}"


def discover_root(root: Path) -> list[Target]:
    """Every ghsync-layout repository under ``root``, sorted by id."""
    root = root.resolve()
    found: list[Target] = []
    for current, dirs, _files in os.walk(root):
        here = Path(current)
        if here != root and _is_repo_dir(here):
            found.append(Target(layout_id(here, root), here / checkout_name(here)))
            dirs[:] = []
            continue
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in _SKIP_DIRS)
    return sorted(found, key=lambda t: t.repo)


def remote_path(checkout: Path) -> str | None:
    """The path part of the checkout's ``origin`` URL (no host, user or ``.git``).

    ``.``, ``..`` and empty segments are dropped, so an id built from it never
    climbs out of a directory it names (the stderr logs); None when nothing is left.
    """
    url = _git(checkout, "config", "--get", "remote.origin.url")
    if url is None or _HOSTLESS_URL.match(url):
        return None  # file:///abs/path: a local path, never an id
    match = _REMOTE_PATH.match(url)
    if not match:
        return None
    segments = [s for s in match.group("path").split("/") if s not in ("", ".", "..")]
    return "/".join(segments) or None


def _under(path: Path, root: Path) -> bool:
    return path != root and root in path.parents


def _locate(entry: str, root: Path | None, base: Path) -> tuple[Path, Path]:
    """``(repo_dir, checkout)`` for one list entry."""
    head = entry.replace("\\", "/").split("/", 1)[0]
    try:
        path = Path(entry).expanduser()
        # POSIX raises for an unknown user; Windows builds <users dir>\<name> unchecked
        if head.startswith("~") and head != "~" and not Path(head).expanduser().is_dir():
            raise RuntimeError(f"no home directory at {Path(head).expanduser()}")
    except RuntimeError as e:  # ~nosuchuser/repo: no such user's home
        raise ValueError(f"list entry {entry!r}: cannot expand {head!r} ({e})") from e
    if not path.is_absolute():
        # With a root, a relative entry is a root path (ghsync --porcelain)
        # unless it exists only from the working directory; an uncloned one
        # still resolves under the root, so its failed row keeps the layout id.
        from_base = base / path
        only_from_base = from_base.exists() and not (root is not None and (root / path).exists())
        path = root / path if root is not None and not only_from_base else from_base
    path = path.resolve()
    if path.name == checkout_name(path.parent):  # by name: a missing checkout keeps its id
        return path.parent, path
    if _is_repo_dir(path) or not (path / ".git").exists():
        return path, path / checkout_name(path)
    return path, path  # a plain checkout outside the ghsync layout


def _list_id(repo_dir: Path, checkout: Path, root: Path | None) -> str:
    if root is not None and _under(repo_dir, root):
        return layout_id(repo_dir, root)
    remote = remote_path(checkout) if checkout.exists() else None
    if remote and "/" in remote:
        return remote
    return f"{repo_dir.parent.name}/{repo_dir.name}"


def parse_list(text: str) -> list[str]:
    """Entries of a list file: one per line, blank lines and ``#`` comments ignored."""
    lines = (line.strip() for line in text.splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def targets_from_list(entries: Iterable[str], root: Path | None, base: Path) -> list[Target]:
    """Targets for list entries, sorted by id; a repeated id keeps its first entry.

    An entry is a checkout (``<repo>-main``), its repository directory, a plain
    git checkout, or (with ``root``) a path relative to the root such as
    ghsync's ``--porcelain`` output.
    """
    resolved_root = root.resolve() if root is not None else None
    targets: dict[str, Target] = {}
    for entry in entries:
        repo_dir, checkout = _locate(entry, resolved_root, base)
        repo = _list_id(repo_dir, checkout, resolved_root)
        if repo in targets:
            print(f"fleet: skipped list entry {entry}: same id {repo} as an earlier entry", file=sys.stderr)
            continue
        targets[repo] = Target(repo, checkout)
    return sorted(targets.values(), key=lambda t: t.repo)


# --- scanning --------------------------------------------------------------


def _row(target: Target, *, reason: str | None = None, document: JsonDict | None = None,
         active: bool | None = None, commit: CommitState | None = None) -> FleetRow:
    return {
        "fleet_row_version": FLEET_ROW_VERSION,
        "repo": target.repo,
        "group": target.group,
        "status": STATUS_FAILED if reason else STATUS_OK,
        "reason": reason,
        "signals_schema_version": document["signals_schema_version"] if document else None,
        "scans": document["scans"] if document else None,
        "active": active if document else None,
        "head_sha": commit["head_sha"] if document and commit else None,
        "dirty": commit["dirty"] if document and commit else None,
        "dirty_reason": commit["dirty_reason"] if document and commit else None,
    }


def checkout_problem(checkout: Path) -> str | None:
    """A failure token when ``checkout`` cannot be scanned, else None.

    ``assess_signals`` degrades a broken ``.git`` to unavailable blocks and
    exits 0, so the runner checks for a work-tree root itself.
    """
    if not checkout.is_dir():
        return MISSING_CHECKOUT
    top = _git(checkout, "rev-parse", "--show-toplevel")
    if top is None or Path(top).resolve() != checkout.resolve():
        return NOT_A_WORK_TREE
    return None


def _commit(head: str | None, dirty: bool | None, reason: str | None = None) -> CommitState:
    return {"head_sha": head, "dirty": dirty, "dirty_reason": reason}


def _hidden_entries(checkout: Path) -> str | None:
    """The token for index entries ``git status`` never compares, ``""`` when there are
    none, or None when the index cannot be read.

    ``git ls-files -v`` tags a skip-worktree entry ``S`` (a sparse checkout sets it on
    every path outside the sparse set) and an assume-unchanged one in lower case.
    """
    listing = _git_output(checkout, "ls-files", "-v")
    if listing is None:
        return None
    tags = {line[0] for line in listing.splitlines() if line}
    if "S" in tags or "s" in tags:
        sparse = _git(checkout, "config", "--bool", "core.sparseCheckout") == "true"
        return SPARSE_CHECKOUT if sparse else SKIP_WORKTREE
    return ASSUME_UNCHANGED if any(tag.islower() for tag in tags) else ""


def commit_state(checkout: Path) -> CommitState:
    """``HEAD``, whether tracked files differ from it, and why that is unknown when it is.

    ``dirty`` is ``/assess``'s ``measured_commit.dirty`` (``lib/git_churn.py``): untracked
    files and ``.assess/`` outputs are left out, ``.assess/config.toml`` is not. Unlike
    ``/assess``, a submodule's own modified content is left out too
    (``--ignore-submodules=dirty``, #932). Read with optional locks off, so ``git
    status`` never rewrites the target's index. A clean status with entries git never
    compares (``_hidden_entries``) reads as unknown: None, with a ``dirty_reason``. A
    clean one whose submodules hold modified tracked content stays False, with
    ``submodule_dirty`` (#1017); untracked files inside a submodule never count.
    """
    head = _git(checkout, "rev-parse", "--verify", "--quiet", "HEAD")
    if head is None:
        return _commit(None, None, NO_COMMIT)
    states = _status(checkout, "dirty")
    if states is None:
        return _commit(head, None, STATUS_ERROR)
    if any(states):
        return _commit(head, True)
    hidden = _hidden_entries(checkout)
    if hidden is None:
        return _commit(head, None, INDEX_READ_ERROR)
    if hidden:
        return _commit(head, None, hidden)
    with_submodules = _status(checkout, "untracked")
    if with_submodules is None:
        return _commit(head, None, STATUS_ERROR)
    return _commit(head, False, SUBMODULE_DIRTY if any(with_submodules) else None)


def _status(checkout: Path, ignore_submodules: str) -> list[str] | None:
    """``git status --porcelain`` of tracked files under both ``/assess`` dirty pathspecs,
    one output each, or None when either call failed."""
    states = [_git_output(checkout, "status", "--porcelain", "--untracked-files=no",
                          f"--ignore-submodules={ignore_submodules}", "--", spec)
              for spec in (ASSESS_EXCLUDE_PATHSPEC, ASSESS_CONFIG_PATHSPEC)]
    return None if None in states else [state for state in states if state is not None]


def _clone_at(checkout: Path, copy: Path, sha: str, timeout: int) -> bool:
    """``copy``: a shared clone of ``checkout`` with ``sha`` checked out and the same ``origin``.

    Shared (``--shared``): the copy borrows the checkout's objects and writes none
    there. No hooks run and no LFS content is fetched.
    """
    origin = _git(checkout, "config", "--get", "remote.origin.url")
    no_hooks = ["-c", f"core.hooksPath={os.devnull}"]
    steps = [
        ["git", *no_hooks, "clone", "--quiet", "--shared", "--no-checkout", str(checkout), str(copy)],
        ["git", "-C", str(copy), *no_hooks, "-c", "advice.detachedHead=false",
         "checkout", "--quiet", "--detach", sha],
        ["git", "-C", str(copy), "remote", *(("set-url", "origin", origin) if origin else ("remove", "origin"))],
    ]
    env = {**_read_only_env(), "GIT_LFS_SKIP_SMUDGE": "1"}
    try:
        for step in steps:
            subprocess.run(step, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           check=True, timeout=timeout, env=env)
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return False
    return True


@contextmanager
def committed_tree(checkout: Path, scratch: Path, commit: RepoCommit,
                   timeout: int = DEFAULT_TIMEOUT) -> Iterator[Path | None]:
    """The tree to scan for ``commit``: ``checkout`` itself when it is known clean or has
    no commit, else (dirty or unknown, #932) a scratch clone at ``head_sha`` (same
    directory name), removed afterwards; None when that clone cannot be made."""
    sha = commit["head_sha"]
    if commit["dirty"] is False or sha is None:
        yield checkout
        return
    holder = Path(tempfile.mkdtemp(prefix="committed-", dir=scratch))
    try:
        copy = holder / checkout.name
        yield copy if _clone_at(checkout, copy, sha, timeout) else None
    finally:
        shutil.rmtree(holder, ignore_errors=True)


def signals_command(checkout: Path, out: Path, options: ScanOptions) -> list[str]:
    command = [sys.executable, str(SIGNALS_SCRIPT), str(checkout), "--out", str(out)]
    if options.counts_only:
        command.append("--counts-only")
        if options.hash_salt is not None:
            command += ["--hash-salt", options.hash_salt]
    if options.window is not None:
        command += ["--window", options.window]
    scans = options.scans if options.platform else offline_scans(options.scans)
    if scans is not None:
        command += ["--scans", scans]
    if options.fleet_config is not None:
        command += ["--fleet-config", str(options.fleet_config)]
    if options.ghsync_root is not None:
        command += ["--ghsync-root", str(options.ghsync_root)]
    if options.platform:
        command.append("--platform")
    elif options.units:  # the platform pass reads no unit; the offline scan does
        command.append("--units")
    return command


def _load_document(path: Path) -> JsonDict | None:
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not (isinstance(data, dict) and isinstance(data.get("scans"), dict)
            and isinstance(data.get("signals_schema_version"), int)):
        return None
    return data


def scan_target(target: Target, scratch: Path, options: ScanOptions) -> FleetRow:
    """One target through ``assess_signals.py`` in its own process."""
    problem = checkout_problem(target.checkout)
    if problem:
        return _row(target, reason=problem)
    commit = commit_state(target.checkout)
    document = run_signals(target, scratch, options, commit=commit)
    if isinstance(document, str):
        return _row(target, reason=document)
    since = options.active_since
    return _row(target, document=document, commit=commit,
                active=None if since is None else is_active(target.checkout, since))


def run_signals(target: Target, scratch: Path, options: ScanOptions, log_suffix: str = ".stderr",
                commit: RepoCommit | None = None) -> JsonDict | str:
    """``assess_signals.py`` on the committed tree of ``target.checkout`` (``committed_tree``;
    ``commit`` defaults to ``commit_state``) in its own process: the document, or a failure
    token (``scan_timeout``, ``scan_error``, ``scan_exit_<code>``, ``invalid_output``,
    ``committed_tree_error``).

    A non-zero exit leaves its stderr tail in ``<log_dir>/<repo><log_suffix>``.
    """
    state = commit_state(target.checkout) if commit is None else commit
    with committed_tree(target.checkout, scratch, state, options.timeout) as tree:
        if tree is None:
            return COMMITTED_TREE_ERROR
        return _run_signals_on(tree, target, scratch, options, log_suffix)


def _run_signals_on(tree: Path, target: Target, scratch: Path, options: ScanOptions,
                    log_suffix: str) -> JsonDict | str:
    out = scratch / (secrets.token_hex(8) + ".json")
    try:
        done = subprocess.run(
            signals_command(tree, out, options), cwd=scratch,
            env=_read_only_env(options.cache_dir), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=options.timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return SCAN_TIMEOUT
    except OSError:
        return SCAN_ERROR
    if done.returncode != 0:
        if options.hash_salt is None:  # free text can name the checkout: none under hashing (#909)
            write_stderr_tail(options.log_dir, target, done.stderr, log_suffix)
        return f"scan_exit_{done.returncode}"
    document = _load_document(out)
    out.unlink(missing_ok=True)
    return INVALID_OUTPUT if document is None else document


def write_stderr_tail(log_dir: Path | None, target: Target, stderr: str | None,
                      suffix: str = ".stderr") -> Path | None:
    """The last ``STDERR_TAIL_CHARS`` of a failed scan's stderr, beside the rows, never in one.

    Written to ``<log_dir>/<repo><suffix>`` (the id's segments as directories; the
    platform pass uses ``.platform.stderr``)
    for diagnosis; the row keeps only its reason token. A write that fails is
    skipped: the log is a convenience, the row is the record.
    """
    if log_dir is None:
        return None
    path = (log_dir / f"{target.repo}{suffix}").resolve()
    if log_dir.resolve() not in path.parents:  # an id that would climb out of the logs
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text((stderr or "")[-STDERR_TAIL_CHARS:], encoding="utf-8", newline="\n")
    except OSError:
        return None
    return path


def clear_stderr_logs(log_dir: Path) -> None:
    """Delete the ``*.stderr`` files under ``log_dir`` and the directories that empties.

    Only files this tool writes go; any other file (and its directories) stays.
    """
    if not log_dir.is_dir():
        return
    for path in log_dir.rglob("*.stderr"):
        if path.is_file() and not path.is_symlink():
            path.unlink(missing_ok=True)
    for directory in sorted((d for d in log_dir.rglob("*") if d.is_dir() and not d.is_symlink()),
                            key=lambda d: len(d.parts), reverse=True):
        try:
            directory.rmdir()  # only succeeds when empty
        except OSError:
            pass


def _guarded(scan: ScanFn, target: Target, scratch: Path, options: ScanOptions) -> FleetRow:
    try:
        return scan(target, scratch, options)
    except Exception:  # noqa: BLE001 - one target's failure never stops the run
        return _row(target, reason=SCAN_ERROR)


def _scan_then(scan: ScanFn, after: AfterFn | None, target: Target, scratch: Path,
               options: ScanOptions) -> FleetRow:
    row = _guarded(scan, target, scratch, options)
    if after is not None:
        after(target, row)
    return row


def run_fleet(
    targets: Sequence[Target], options: ScanOptions, jobs: int, scan: ScanFn = scan_target,
    after: AfterFn | None = None,
) -> list[FleetRow]:
    """Scan every target with ``jobs`` workers; rows sorted by ``repo``.

    ``after`` (when given) runs in the worker on each target's row right after
    its scan, before the next target starts on that worker.

    The ``*.stderr`` logs a previous run left in ``options.log_dir`` are removed
    first, so it holds only this run's; nothing else there is touched.
    """
    if options.log_dir is not None:
        clear_stderr_logs(options.log_dir)
    with tempfile.TemporaryDirectory(prefix="fleet-") as scratch, \
            ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        rows = list(pool.map(lambda t: _scan_then(scan, after, t, Path(scratch), options), targets))
    return sorted(rows, key=lambda r: r["repo"])


def run_salt(hash_paths: bool, salt: str | None) -> str | None:
    """The salt every target hashes under: the given one, one drawn per run, or None."""
    if salt is not None:
        return salt
    return secrets.token_hex(16) if hash_paths else None


# --- output ----------------------------------------------------------------


def dumps_rows(rows: Iterable[FleetRow]) -> str:
    return "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows)


def write_rows(out_dir: Path, rows: Iterable[FleetRow]) -> Path:
    """Write ``<out_dir>/repos.jsonl`` through a temp file and ``os.replace``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / REPOS_FILE
    fd, tmp = tempfile.mkstemp(dir=out_dir, prefix=f".{REPOS_FILE}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(dumps_rows(rows))
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target


def out_inside_target(out_dir: Path, targets: Iterable[Target]) -> Target | None:
    """The target whose repository directory (checkout, ``worktree/`` and all) would hold ``out_dir``, if any."""
    out = out_dir.resolve()
    for target in targets:
        checkout = target.checkout
        repo_dir = checkout.parent if checkout.name == checkout_name(checkout.parent) else checkout
        if out in (target.checkout, repo_dir) or target.checkout in out.parents or repo_dir in out.parents:
            return target
    return None
