"""Wiki state carried between runs: first-flagged dates and run supersession.

``first-flagged.json`` records when each hotspot was first flagged; a rename
moves the date to the current path. A run that measures the same commit on the
same day as a still-unfinalized run supersedes it: its log entry and history
rows are removed, and files only that run flagged and now excluded by config
are retired (#355, #356, #421).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lib.assess_config import is_user_excluded
from lib.stats_diff import StatsDiff
from lib.wiki_writer import (
    last_log_entry_is_unfinalized_run,
    retire_excluded_hotspots,
    supersede_unfinalized_log_entry,
    sweep_superseded_history_rows,
)


def load_first_flagged(assess_dir: Path) -> dict[str, str]:
    """Load the first-flagged date map from .assess/first-flagged.json.

    Returns an empty dict if the file does not exist yet (first run).
    """
    state_file = assess_dir / "first-flagged.json"
    if not state_file.exists():
        return {}
    first_flagged: dict[str, str] = json.loads(state_file.read_text(encoding="utf-8"))
    return first_flagged


def rekey_first_flagged(
    first_flagged: dict[str, str], rename_map: dict[str, str],
) -> dict[str, str]:
    """Move each first-flagged entry for a renamed path onto its current path.

    The date travels with the file. When the current path already has an entry,
    the earlier known date wins, so a rename never makes a file look newer.
    """
    rekeyed = {k: v for k, v in first_flagged.items() if k not in rename_map}
    for old, date in first_flagged.items():
        new = rename_map.get(old)
        if new is None:
            continue
        known = sorted(d for d in (date, rekeyed.get(new)) if d and d != "unknown")
        rekeyed[new] = known[0] if known else "unknown"
    return rekeyed


def same_measurement_prior_run(
    assess_dir: Path, *, run_date: str, measured_commit: dict[str, Any]
) -> dict[str, Any] | None:
    """The previous run's context when this run supersedes it, else None.

    A run supersedes the previous one when both share ``run_date`` and the
    measured commit. The previous run-context.json is still on disk here (this
    run writes its own later), so it names the entry's run id and commit. The
    wiki writer only removes that run's log entry if it is the log's last and
    still carries placeholders; a finalized entry is history and stays (#355).

    A target with no git (``available`` false on both runs) has no commit to
    key on, so the date and the prior run id are the whole identity: two such
    runs on one day count as the same measurement.
    """
    try:
        prior = json.loads((assess_dir / "run-context.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(prior, dict) or not prior.get("run_id"):
        return None
    if prior.get("run_date") != run_date:
        return None
    prior_commit = prior.get("measured_commit")
    if not isinstance(prior_commit, dict):
        return None
    head_sha = measured_commit.get("head_sha")
    if head_sha:
        same = prior_commit.get("head_sha") == head_sha
    else:
        same = (
            measured_commit.get("available") is False
            and prior_commit.get("available") is False
        )
    return prior if same else None


def _inherited_provisional_paths(
    assess_dir: Path, superseded: dict[str, Any] | None, first_flagged_map: dict[str, str],
) -> set[str]:
    """Paths first flagged only by the superseded, never-finalized run (#356).

    Empty unless the superseded run's log entry is still unfinalized. Each run
    records ``provisional_first_flagged``: the paths it first flagged plus those
    it inherited this way, so a chain of unfinalized same-day runs carries a
    file forward after it stops being "new". A run-context written before the
    key existed yields nothing: its ``diff_detail.new`` cannot tell a file first
    flagged there from one first flagged by a finalized run earlier that day
    that graduated and returned, and retiring the latter would delete a
    finalized date. Only paths whose first-flagged date is still that run's
    date qualify.
    """
    if superseded is None or not last_log_entry_is_unfinalized_run(
        assess_dir, superseded["run_id"],
    ):
        return set()
    paths = superseded.get("provisional_first_flagged")
    if not isinstance(paths, list):
        return set()
    return {
        p for p in paths
        if isinstance(p, str) and first_flagged_map.get(p) == superseded.get("run_date")
    }


def excluded_after_unfinalized_run(
    assess_dir: Path, *, superseded: dict[str, Any] | None, first_flagged_map: dict[str, str],
    current: dict[str, Any], diff: StatsDiff, excludes: tuple[set[str], list[str]],
) -> tuple[set[str], list[str]]:
    """Split out the files excluded after a never-finalized run (#356).

    Returns ``(provisional, excluded)``. ``provisional`` is what this run records
    as ``provisional_first_flagged``: the inherited paths plus the ones this run
    flags for the first time, minus ``excluded``. ``excluded`` is the sorted
    inherited paths now matched by a config exclude and no longer a top hotspot;
    they are removed from ``diff.graduated`` here so the rotated prior stats do
    not carry them into index.md. Must run before the hotspot loop stamps new
    first-flagged dates.
    """
    inherited = _inherited_provisional_paths(assess_dir, superseded, first_flagged_map)
    current_hot = {h["path"] for h in current.get("top_hotspots", [])}
    excluded = sorted(
        p for p in inherited - current_hot if is_user_excluded(Path(p), *excludes)
    )
    diff.graduated = [h for h in diff.graduated if h.path not in excluded]
    fresh = {h.path for h in diff.new if h.path not in first_flagged_map}
    return (inherited | fresh) - set(excluded), excluded


def retire_excluded_unfinalized(
    assess_dir: Path, excluded: list[str], first_flagged_map: dict[str, str],
) -> tuple[list[str], list[str]]:
    """Retire the pages of ``excluded`` and drop their first-flagged entries.

    Returns ``(retired, dropped)``. A path whose page exists but has no status
    token to stamp keeps its entry, so a page that still reads live never loses
    its first-flagged date.
    """
    retired, unstamped = retire_excluded_hotspots(assess_dir, excluded)
    dropped = [p for p in excluded if p not in unstamped and p in first_flagged_map]
    for path in dropped:
        del first_flagged_map[path]
    return retired, dropped


def sweep_superseded_history(assess_dir: Path, superseded: dict[str, Any] | None) -> None:
    """Remove a never-finalized superseded run's row from every hotspot page.

    Its log entry is dropped later in the run; the history rows go with it,
    including pages of files this run does not rank (#421). Runs before the
    pages are rewritten, so a rewritten page merges onto a swept table.
    """
    if superseded is not None and last_log_entry_is_unfinalized_run(
        assess_dir, superseded["run_id"],
    ):
        sweep_superseded_history_rows(assess_dir, superseded["run_id"])


def drop_superseded_log_entry(assess_dir: Path, superseded: dict[str, Any] | None) -> None:
    """Remove the superseded run's log entry when it is still unfinalized."""
    if superseded is not None:
        supersede_unfinalized_log_entry(assess_dir, superseded["run_id"])


def save_first_flagged(assess_dir: Path, first_flagged: dict[str, str]) -> None:
    """Persist the first-flagged date map to .assess/first-flagged.json."""
    (assess_dir / "first-flagged.json").write_text(
        json.dumps(first_flagged, indent=2), encoding="utf-8"
    )
