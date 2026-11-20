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
  rubric version, window, output mode, scan selection and path hashing;
  otherwise ``entry_conflict`` names why and the entry is kept.
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


def entry_conflict(entry: Path, stamp: Mapping[str, Any]) -> str | None:
    """Why the same-day ``entry`` must be kept rather than replaced; None when it may be."""
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
    return None


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


def persist(out_dir: Path, entry: Path, stamp: FleetStamp) -> Path:
    """Copy the run's three files from ``out_dir`` into ``entry``; raises ``HistoryError`` on a conflict."""
    conflict = entry_conflict(entry, stamp)
    if conflict is not None:
        raise HistoryError(conflict)
    entry.mkdir(parents=True, exist_ok=True)
    for name in HISTORY_FILES:
        _replace(out_dir / name, entry / name)
    return entry
