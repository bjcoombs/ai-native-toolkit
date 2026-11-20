"""Fleet run history: each counts-only run kept under a dated directory (issue #552).

Layout: ``<history root>/<org>/<scan_date>/`` holding the run's
``repos.jsonl``, ``groups.jsonl`` and ``run.json``. The history root is
``--history-dir`` or ``${XDG_DATA_HOME:-~/.local/share}/ai-native-toolkit/fleet``
(a relative or empty ``XDG_DATA_HOME`` is ignored, per the XDG spec). ``<org>``
is ``--history-org`` or ``default_org``: the first segment every repository id
shares, or the distinct first segments sorted and joined by ``+``.

Only counts-only output is kept (a ``--full`` run is not), so the history can
live outside the scanned repositories.

Writes are narrow and fail closed:

- each of the three files is written through a temp file and ``os.replace``,
  ``run.json`` last; nothing in the history is ever deleted;
- ``<org>`` and ``<scan_date>`` must each be one safe path component, and no
  existing directory on the way from the root to the entry may be a symlink;
- a same-day entry is replaced only when it is this tool's (a readable
  ``run.json`` at the stamp version this code writes) and its stamp agrees on
  rubric version, window, output mode, scan selection and path hashing, and
  holds the same repository set (so a partial ``--from-list`` rerun never
  replaces the full-fleet entry); otherwise ``entry_conflict`` names why, the entry is kept, and
  ``choose_entry`` moves on to a suffixed sibling (``<date>.2``, ``<date>.3``,
  ...), taking the first that is free or holds a matching run.

``latest_matching`` finds the newest entry (by date, then suffix) whose stamp
matches a planned run, preferring one with the same repository set: the
default earlier run for ``--compare``.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from lib.fleet import REPOS_FILE
from lib.fleet_groups import GROUPS_FILE
from lib.fleet_stamp import FLEET_STAMP_VERSION, RUN_FILE, FleetStamp

HISTORY_FILES = (REPOS_FILE, GROUPS_FILE, RUN_FILE)  # written in this order: run.json last
_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,199}$")
_MATCH_KEYS = ("rubric_version", "window", "counts_only", "scans", "paths_hashed")
_ENTRY = re.compile(r"^([0-9]{4}-[0-9]{2}-[0-9]{2})(?:\.([1-9][0-9]*))?$")
MAX_SAME_DAY = 99


class HistoryError(ValueError):
    """The history location cannot be used; the message says why."""


def history_root(environ: Mapping[str, str], home: Path) -> Path:
    """``${XDG_DATA_HOME:-~/.local/share}/ai-native-toolkit/fleet``."""
    xdg = environ.get("XDG_DATA_HOME", "")
    base = Path(xdg) if xdg and Path(xdg).is_absolute() else home / ".local" / "share"
    return base / "ai-native-toolkit" / "fleet"


def safe_component(name: str, what: str) -> str:
    """``name`` when it is one safe path component, else ``HistoryError``."""
    if not _SAFE.match(name) or name in (".", ".."):
        raise HistoryError(f"{what} {name!r} is not a safe directory name "
                           "(letters, digits, '.', '_', '+', '-'; not starting with '.' or '-')")
    return name


def default_org(repo_ids: Iterable[str]) -> str:
    """The first segment every id shares, or the distinct ones sorted and joined by ``+``."""
    firsts = sorted({repo.split("/", 1)[0] for repo in repo_ids})
    if not firsts:
        raise HistoryError("no repositories to name the history directory after")
    return safe_component("+".join(firsts), "history org")


def entry_dir(root: Path, org: str, scan_date: str) -> Path:
    """``root/org/scan_date``, refusing unsafe names and symlinked directories on the way."""
    entry = root / safe_component(org, "history org") / safe_component(scan_date, "scan date")
    for directory in (root, root / org, entry):
        if directory.is_symlink():
            raise HistoryError(f"{directory} is a symlink; the history never writes through one")
    return entry


def entry_repos(entry: Path) -> frozenset[str] | None:
    """The repository ids in ``entry/repos.jsonl``; None when it cannot be read."""
    try:
        lines = (entry / REPOS_FILE).read_text(encoding="utf-8").splitlines()
        rows = [json.loads(line) for line in lines if line.strip()]
    except (OSError, ValueError):
        return None
    if not all(isinstance(row, dict) and isinstance(row.get("repo"), str) for row in rows):
        return None
    return frozenset(row["repo"] for row in rows)


def entry_conflict(entry: Path, stamp: Mapping[str, Any], repos: frozenset[str] | None = None) -> str | None:
    """Why the same-day ``entry`` must be kept rather than replaced; None when it may be.

    With ``repos``, the entry must also hold exactly that repository set.
    """
    if not entry.exists():
        return None
    if not entry.is_dir():
        return f"{entry} exists and is not a directory"
    run = entry / RUN_FILE
    if not run.exists():
        present = [name for name in HISTORY_FILES if (entry / name).exists()]
        return f"{entry} holds {', '.join(present)} without a {RUN_FILE}" if present else None
    try:
        earlier: Any = json.loads(run.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return f"{run} is not a readable run stamp"
    if not isinstance(earlier, dict) or earlier.get("fleet_stamp_version") != FLEET_STAMP_VERSION:
        return f"{run} is not a run stamp this toolkit writes"
    differs = [key for key in _MATCH_KEYS if earlier.get(key) != stamp.get(key)]
    if differs:
        return f"{entry} is a same-day run with a different {', '.join(differs)}"
    if repos is not None:
        held = entry_repos(entry)
        if held is None:
            return f"{entry / REPOS_FILE} is not readable"
        if held != repos:
            return (f"{entry} is a same-day run over a different repository set "
                    f"({len(held)} repositories, this run {len(repos)})")
    return None


def choose_entry(root: Path, org: str, scan_date: str, stamp: Mapping[str, Any],
                 repos: frozenset[str]) -> tuple[Path, list[str]]:
    """The entry this run is kept in, and why each earlier same-day candidate was passed over.

    ``<date>``, then ``<date>.2``, ``<date>.3``, ...: the first that is free or
    holds a run with a matching stamp over the same ``repos``. Raises
    ``HistoryError`` past ``MAX_SAME_DAY``.
    """
    passed: list[str] = []
    for n in range(1, MAX_SAME_DAY + 1):
        entry = entry_dir(root, org, scan_date if n == 1 else f"{scan_date}.{n}")
        conflict = entry_conflict(entry, stamp, repos)
        if conflict is None:
            return entry, passed
        passed.append(conflict)
    raise HistoryError(f"{MAX_SAME_DAY} same-day entries for {org} on {scan_date} already differ from this run")


def _entry_order(path: Path) -> tuple[str, int] | None:
    match = _ENTRY.match(path.name)
    return (match.group(1), int(match.group(2) or 1)) if match else None


def latest_matching(org_dir: Path, stamp: Mapping[str, Any], repos: frozenset[str] | None = None) -> Path | None:
    """The newest ``<date>[.<n>]`` entry under ``org_dir`` holding a run whose stamp matches ``stamp``.

    With ``repos``, the newest such entry over exactly that repository set wins
    when there is one, so a partial rerun is not taken as the baseline.
    """
    if not org_dir.is_dir() or org_dir.is_symlink():
        return None
    entries = [(order, p) for p in org_dir.iterdir()
               if (order := _entry_order(p)) is not None and p.is_dir() and not p.is_symlink()]
    matching = [entry for _order, entry in sorted(entries, reverse=True)
                if (entry / RUN_FILE).is_file() and entry_conflict(entry, stamp) is None]
    same_set = [entry for entry in matching if repos is not None and entry_repos(entry) == repos]
    if same_set:
        return same_set[0]
    return matching[0] if matching else None


def _replace(source: Path, target: Path) -> None:
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(source.read_bytes())
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def persist(out_dir: Path, entry: Path, stamp: FleetStamp, repos: frozenset[str]) -> Path:
    """Copy the run's three files from ``out_dir`` into ``entry``; raises ``HistoryError`` on a conflict."""
    conflict = entry_conflict(entry, stamp, repos)
    if conflict is not None:
        raise HistoryError(conflict)
    entry.mkdir(parents=True, exist_ok=True)
    for name in HISTORY_FILES:
        _replace(out_dir / name, entry / name)
    return entry
