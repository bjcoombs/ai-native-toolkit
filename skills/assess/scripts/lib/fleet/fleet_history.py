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

- the three files are written to a hidden temp sibling of the entry first; a
  new entry is that directory renamed into place in one step, and a same-day
  entry being replaced has its ``run.json`` withdrawn before the new files
  move in, ``run.json`` last, so a failure part-way never leaves a mixed entry
  that reads as a run; nothing else in the history is ever deleted;
- ``<org>`` and ``<scan_date>`` must each be one safe path component, and no
  existing directory on the way from the root to the entry may be a symlink;
- a same-day entry is replaced only when it is this tool's (a readable
  ``run.json`` at the stamp version this code writes) and its stamp agrees on
  maturity-model version, window, output mode, scan selection, path hashing and
  ``--fleet-config`` hash and ``--platform`` (absent before #714: ``false``), and
  holds the same repository set (so a partial ``--from-list`` rerun never
  replaces the full-fleet entry); otherwise ``entry_conflict`` names why, the entry is kept, and
  ``choose_entry`` moves on to a suffixed sibling (``<date>.2``, ``<date>.3``,
  ...), taking the first that is free or holds a matching run.

``latest_matching`` finds the newest entry (by date, then suffix) whose stamp
matches a planned run, preferring one with the same repository set: the
default earlier run for ``--compare``.

The fleet run history (issue #552): each counts-only run's `repos.jsonl`, `groups.jsonl`
and `run.json` kept in `<root>/<org>/<scan_date>/`, plus `doc_freshness.jsonl` and `fleet-data.json` when the
run wrote them (`OPTIONAL_HISTORY_FILES`, #664 and #703; a same-day replacement without it removes
the old copy so an entry never mixes runs). `history_root` is
`${XDG_DATA_HOME:-~/.local/share}/ai-native-toolkit/fleet` (a relative or empty
`XDG_DATA_HOME` ignored); `default_org` is the first segment every repository id shares,
or the distinct ones sorted and joined by `+`. Fails closed: `safe_component` rejects a
name that is not one plain path component, `entry_dir` refuses a symlinked directory on
the way, and `entry_conflict` keeps a same-day entry that is not this tool's (no readable
`run.json` at the current stamp version) or whose stamp differs in maturity-model version,
window, output mode, scan selection, path hashing or `--fleet-config` hash, or that holds a different
repository set (`entry_repos`), so a partial rerun never replaces a full-fleet entry;
`choose_entry` then moves to
`<date>.2`, `<date>.3`, ... (the first free or matching, up to `MAX_SAME_DAY`).
`latest_matching` finds the newest matching entry, preferring one over the same
repository set, the default for a bare `--compare`. `dated_entries` lists the
`<date>[.<n>]` directories oldest first; `one_per_date` keeps one accepted entry per date
before a run's (the entry over its repository set, else the highest suffix), shared by
`fleet_trends` and `fleet_achievements_runs`.
`persist` writes the three files to a hidden temp sibling first: a new entry is that
directory renamed into place in one step, and a replaced same-day entry loses its
`run.json` before the new files move in (`run.json` last), so a failure part-way never
leaves a mixed entry that `latest_matching` selects. Other files in an entry are kept.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any, TypeVar

from lib.fleet.runner import REPOS_FILE
from lib.fleet.fleet_data import FLEET_DATA_FILE
from lib.fleet.fleet_doc_freshness import FRESHNESS_FILE
from lib.fleet.fleet_groups import GROUPS_FILE
from lib.fleet.fleet_legacy import stamp_value, upgrade
from lib.fleet.fleet_stamp import FLEET_STAMP_VERSION, RUN_FILE, FleetStamp

HISTORY_FILES = (REPOS_FILE, GROUPS_FILE, RUN_FILE)  # written in this order: run.json last
# Kept when the run wrote them (``doc_freshness.jsonl`` exists only when a docs
# repository declares ``[[describes]]``, #664); moved in before ``run.json``, and
# a same-day entry's stale copy is removed when the new run wrote none.
# ``fleet-data.json`` (#703) is optional so entries written before it still load.
# demotions.json: the exit rule's record with its evidence, kept with the run (#1086)
OPTIONAL_HISTORY_FILES = (FRESHNESS_FILE, FLEET_DATA_FILE, "demotions.json")
_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,199}$")
_MATCH_KEYS = ("levels_version", "window", "counts_only", "scans", "paths_hashed", "fleet_config_sha256",
               "platform", "managed_settings")
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
    earlier = upgrade(earlier)  # an entry written before the 2.0.0 rename
    differs = [key for key in _MATCH_KEYS if stamp_value(earlier, key) != stamp_value(stamp, key)]
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


def dated_entries(org_dir: Path) -> list[tuple[tuple[str, int], Path]]:
    """Every ``<date>[.<n>]`` directory under ``org_dir`` (no symlinks), oldest first, with its
    ``(date, suffix)`` order; empty when ``org_dir`` is missing or a symlink."""
    if not org_dir.is_dir() or org_dir.is_symlink():
        return []
    return sorted((order, p) for p in org_dir.iterdir()
                  if (order := _entry_order(p)) is not None and p.is_dir() and not p.is_symlink())


T = TypeVar("T")


def one_per_date(org_dir: Path, before: str, repos: frozenset[str],
                 read: Callable[[Path, str], T | None]) -> list[tuple[str, T]]:
    """One entry per scan date before ``before``, oldest first, as ``(date, read(entry, date))``.

    ``read`` returns None to pass an entry over. Between same-day entries ``read`` accepts,
    the one over ``repos`` wins, then the highest suffix; ``repos.jsonl`` is read only to
    choose between them. Shared by the trend lines and the achievements engine.
    """
    by_date: dict[str, list[tuple[int, Path, T]]] = {}
    for (date, suffix), entry in dated_entries(org_dir):
        if date >= before:
            continue
        value = read(entry, date)
        if value is not None:
            by_date.setdefault(date, []).append((suffix, entry, value))
    chosen: list[tuple[str, T]] = []
    for date in sorted(by_date):
        options = by_date[date]
        if len(options) > 1:
            options = sorted(options, key=lambda o: (entry_repos(o[1]) == repos, o[0]))
        chosen.append((date, options[-1][2]))
    return chosen


def latest_matching(org_dir: Path, stamp: Mapping[str, Any], repos: frozenset[str] | None = None) -> Path | None:
    """The newest ``<date>[.<n>]`` entry under ``org_dir`` holding a run whose stamp matches ``stamp``.

    With ``repos``, the newest such entry over exactly that repository set wins
    when there is one, so a partial rerun is not taken as the baseline.
    """
    matching = [entry for _order, entry in reversed(dated_entries(org_dir))
                if (entry / RUN_FILE).is_file() and entry_conflict(entry, stamp) is None]
    same_set = [entry for entry in matching if repos is not None and entry_repos(entry) == repos]
    if same_set:
        return same_set[0]
    return matching[0] if matching else None


def _stage(out_dir: Path, parent: Path, name: str) -> Path:
    """A hidden sibling directory of ``parent/name`` holding copies of the run's three files."""
    staging = Path(tempfile.mkdtemp(dir=parent, prefix=f".{name}.", suffix=".tmp"))
    try:
        os.chmod(staging, 0o755)
        for file in HISTORY_FILES + OPTIONAL_HISTORY_FILES:
            if file in OPTIONAL_HISTORY_FILES and not (out_dir / file).is_file():
                continue
            (staging / file).write_bytes((out_dir / file).read_bytes())
            os.chmod(staging / file, 0o644)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return staging


def _move_in(staging: Path, entry: Path, landed: list[str] | None = None) -> None:
    """Move the staged files into ``entry``, ``run.json`` last; an optional file the
    run did not write is removed from the entry first, so it never mixes runs.
    ``landed`` collects each file name once the entry has changed for it."""
    done = landed if landed is not None else []
    for file in OPTIONAL_HISTORY_FILES:
        if (staging / file).exists():
            os.replace(staging / file, entry / file)
            done.append(file)
        elif (entry / file).exists():
            (entry / file).unlink()
            done.append(file)
    for file in HISTORY_FILES:  # run.json last
        os.replace(staging / file, entry / file)
        done.append(file)


def _swap_in(staging: Path, entry: Path) -> None:
    """Move the staged files over an existing entry, its ``run.json`` withdrawn first.

    Until the new ``run.json`` lands the entry has none, so ``latest_matching``
    never selects it and ``entry_conflict`` never reuses it: a failure part-way
    leaves an entry nothing reads as a run, never a mixed one.
    """
    withdrawn = staging / f".old.{RUN_FILE}"
    os.replace(entry / RUN_FILE, withdrawn)
    landed: list[str] = []
    try:
        _move_in(staging, entry, landed)
    except BaseException:
        if not landed:  # nothing new landed: the earlier run is still whole
            os.replace(withdrawn, entry / RUN_FILE)
        raise


def persist(out_dir: Path, entry: Path, stamp: FleetStamp, repos: frozenset[str]) -> Path:
    """Copy the run's three files from ``out_dir`` into ``entry``; raises ``HistoryError`` on a conflict.

    The files are first written to a hidden sibling directory, so a failed
    write never touches the history. A new entry is that directory renamed
    into place in one step. A same-day entry being replaced loses its
    ``run.json`` before the new files move in (``_swap_in``); its other files
    are left alone.
    """
    conflict = entry_conflict(entry, stamp, repos)
    if conflict is not None:
        raise HistoryError(conflict)
    entry.parent.mkdir(parents=True, exist_ok=True)
    staging = _stage(out_dir, entry.parent, entry.name)
    try:
        if entry.exists() and (entry / RUN_FILE).exists():
            _swap_in(staging, entry)
        elif entry.exists():  # a matching entry with no run.json holds none of the three files
            _move_in(staging, entry)
        else:
            os.rename(staging, entry)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return entry
