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
stamp is ``run.json``, lib/fleet_stamp.py). An ``ok`` row's ``active`` says
whether its ``HEAD`` was committed inside the window (lib/fleet_activity.py). Two runs over unchanged checkouts on the same day write
byte-identical files. ``--hash-paths`` without a salt draws one salt per run,
so such a run joins within itself but is not byte-identical across runs.

Read only: every git call here and in the scan runs with
``GIT_OPTIONAL_LOCKS=0``, scans run with the scratch directory as their working
directory, and their output lands in that scratch directory, never in a target.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, TypedDict

from lib.fleet_activity import is_active
from lib.run_context_types import JsonDict

# Bumped when a row's envelope (the keys below) changes. Scan blocks carry
# their own shapes, versioned by ``signals_schema_version``.
# 2 (#569): ``active``.
FLEET_ROW_VERSION = 2
REPOS_FILE = "repos.jsonl"
SIGNALS_SCRIPT = Path(__file__).resolve().parents[1] / "assess_signals.py"
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
FAILURE_REASONS = (MISSING_CHECKOUT, NOT_A_WORK_TREE, SCAN_TIMEOUT, INVALID_OUTPUT, SCAN_ERROR)
# Plus ``scan_exit_<code>`` for a scan process that exits non-zero.

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


ScanFn = Callable[[Target, Path, ScanOptions], FleetRow]
# Called in the worker with each target's row as soon as it is in hand
# (``--delete-after``, lib/fleet_cleanup.py). It must not raise.
AfterFn = Callable[[Target, FleetRow], None]


def _read_only_env() -> dict[str, str]:
    # PYTHONUTF8: the scan child reads and writes UTF-8 whatever the host locale
    # (a Windows code page would otherwise garble or reject non-ASCII text).
    return {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "PYTHONUTF8": "1"}


def _git(path: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(path), *args], capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            check=True, timeout=30, env=_read_only_env(),
        )
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None


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
    try:
        path = Path(entry).expanduser()
    except RuntimeError as e:  # ~nosuchuser/repo: no such user's home
        raise ValueError(f"list entry {entry!r}: cannot expand {entry.split('/', 1)[0]!r} ({e})") from e
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
         active: bool | None = None) -> FleetRow:
    return {
        "fleet_row_version": FLEET_ROW_VERSION,
        "repo": target.repo,
        "group": target.group,
        "status": STATUS_FAILED if reason else STATUS_OK,
        "reason": reason,
        "signals_schema_version": document["signals_schema_version"] if document else None,
        "scans": document["scans"] if document else None,
        "active": active if document else None,
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


def signals_command(checkout: Path, out: Path, options: ScanOptions) -> list[str]:
    command = [sys.executable, str(SIGNALS_SCRIPT), str(checkout), "--out", str(out)]
    if options.counts_only:
        command.append("--counts-only")
        if options.hash_salt is not None:
            command += ["--hash-salt", options.hash_salt]
    if options.window is not None:
        command += ["--window", options.window]
    if options.scans is not None:
        command += ["--scans", options.scans]
    if options.fleet_config is not None:
        command += ["--fleet-config", str(options.fleet_config)]
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
    out = scratch / (secrets.token_hex(8) + ".json")
    try:
        done = subprocess.run(
            signals_command(target.checkout, out, options), cwd=scratch, env=_read_only_env(),
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=options.timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return _row(target, reason=SCAN_TIMEOUT)
    except OSError:
        return _row(target, reason=SCAN_ERROR)
    if done.returncode != 0:
        write_stderr_tail(options.log_dir, target, done.stderr)
        return _row(target, reason=f"scan_exit_{done.returncode}")
    document = _load_document(out)
    out.unlink(missing_ok=True)
    if document is None:
        return _row(target, reason=INVALID_OUTPUT)
    since = options.active_since
    return _row(target, document=document,
                active=None if since is None else is_active(target.checkout, since))


def write_stderr_tail(log_dir: Path | None, target: Target, stderr: str | None) -> Path | None:
    """The last ``STDERR_TAIL_CHARS`` of a failed scan's stderr, beside the rows, never in one.

    Written to ``<log_dir>/<repo>.stderr`` (the id's segments as directories)
    for diagnosis; the row keeps only its reason token. A write that fails is
    skipped: the log is a convenience, the row is the record.
    """
    if log_dir is None:
        return None
    path = (log_dir / f"{target.repo}.stderr").resolve()
    if log_dir.resolve() not in path.parents:  # an id that would climb out of the logs
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text((stderr or "")[-STDERR_TAIL_CHARS:], encoding="utf-8")
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
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
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
