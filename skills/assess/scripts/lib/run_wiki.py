"""Write this run's wiki: the hotspot pages, index.md and the log.md entry.

The deterministic half of the compounding ``.assess/`` wiki. Pages carry
placeholders the LLM fills later through ``assess_finalize``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.artifact_schema import ARTIFACT_SCHEMA_VERSION
from lib.sibling_tests import TestIndex, build_test_index, has_sibling_test, shared_name_keys
from lib.stats_diff import StatsDiff, hotspot_commits
from lib.wiki_state import (
    drop_superseded_log_entry,
    retire_excluded_unfinalized,
    save_first_flagged,
    sweep_superseded_history,
)
from lib.wiki_writer import (
    UNFINALIZED_ACTIONS_POINTER,
    HotspotEntry,
    LogEntry,
    append_log_entry,
    format_ccn,
    prune_orphan_hotspots,
    write_hotspot_page,
    write_index,
    verify_log_chain,
)


def _has_sibling_test(
    repo_root: Path, rel_path: str, shared_names: frozenset[str] = frozenset(),
    index: TestIndex | None = None,
) -> bool | None:
    """Best-effort: does this file have a test file?

    Delegates to ``lib.sibling_tests.has_sibling_test``, the one resolver the
    ``test_focus`` signal also reads, so the hotspot page's ``Has test file`` row
    and the focus table agree. ``True``/``False`` from a filesystem check of the
    naming idioms (``foo.ts`` next to ``foo.test.ts``, ``FooTest.java``, an
    adjacent ``__tests__/``, a mirrored ``tests/`` tree, ...); ``None`` only when
    the file isn't on disk (a since-deleted path in a stats snapshot).
    """
    return has_sibling_test(repo_root, rel_path, shared_names, index)


def _marker_debt_sentence(debt: dict[str, Any] | None) -> str:
    """One briefing sentence accusing a hotspot of its own stale promises."""
    if not debt:
        return ""
    families = ", ".join(debt["families"])
    return (
        f"Carries {debt['count']} stale promissory marker(s) "
        f"({families}; oldest survived {debt['max_survived']} edits to this file). "
    )


def _hotspot_status_map(diff: StatsDiff) -> dict[str, str]:
    """Which paths are graduated, new, regressed, restructured, persistent."""
    status_map: dict[str, str] = {}
    for h in diff.graduated:
        status_map[h.path] = "graduated"
    for h in diff.new:
        status_map[h.path] = "new"
    for h in diff.regressed:
        status_map[h.path] = "regressed"
    for h in diff.restructured:
        status_map[h.path] = "restructured"
    for h in diff.persistent:
        status_map[h.path] = "persistent"
    return status_map


def _write_current_hotspot_page(
    h: dict[str, Any], *, assess_dir: Path, repo_root: Path, run_date: str, run_id: str,
    status_map: dict[str, str], first_flagged_map: dict[str, str],
    hot_shared_names: frozenset[str], hot_test_index: TestIndex | None,
    marker_debt_by_file: dict[str, Any], accretion_by_file: dict[str, Any],
) -> HotspotEntry:
    """Write one current top hotspot's wiki page and return its index entry.

    Mutates ``first_flagged_map`` when the path has no recorded date yet.
    """
    path = h["path"]
    # Preserve the original first_flagged date across runs. A path missing
    # from the map is either genuinely new this run (stamp today) or it was
    # present in the prior snapshot but we have no recorded date - e.g. the
    # prior stats were seeded without first-flagged.json. In the latter case
    # it predates this run, so an honest "unknown" beats a wrong today.
    if path not in first_flagged_map:
        first_flagged_map[path] = (
            run_date if status_map.get(path) == "new" else "unknown"
        )
    first_flagged = first_flagged_map[path]
    status = status_map.get(path, "active")
    commits = hotspot_commits(h)
    loc = h.get("loc", 0)
    ccn = h.get("ccn", 0)
    max_fn_ccn = h.get("max_fn_ccn")
    max_fn_name = h.get("max_fn_name")
    worst_fn = (
        f" (worst function `{max_fn_name}` {format_ccn(max_fn_ccn)})"
        if max_fn_ccn is not None and max_fn_name else ""
    )
    entry = HotspotEntry(
        path=path,
        first_flagged=first_flagged,
        last_seen=run_date,
        status=status,
        ccn=ccn,
        loc=loc,
    )
    write_hotspot_page(
        assess_dir,
        path=path,
        first_flagged=first_flagged,
        last_seen=run_date,
        status=status,
        loc=loc,
        ccn=ccn,
        commits=commits,
        has_tests=_has_sibling_test(repo_root, path, hot_shared_names,
                                    hot_test_index),
        briefing=(
            f"Hotspot ({status}). "
            f"{loc} LOC, "
            f"aggregate cyclomatic complexity {format_ccn(ccn)}{worst_fn}, "
            f"{commits} commits in churn window. "
            + _marker_debt_sentence(marker_debt_by_file.get(path))
            + "(Briefing refined by LLM via assess_finalize - see Suggested actions below.)"
        ),
        actions=UNFINALIZED_ACTIONS_POINTER,
        accretion_data=accretion_by_file.get(path),
        run_id=run_id,
        schema_version=ARTIFACT_SCHEMA_VERSION,
        max_fn_ccn=max_fn_ccn,
        max_fn_name=max_fn_name,
    )
    return entry


def _write_current_hotspot_pages(
    current: dict[str, Any], *, assess_dir: Path, repo_root: Path, run_date: str,
    run_id: str, status_map: dict[str, str], first_flagged_map: dict[str, str],
    superseded: dict[str, Any] | None, marker_debt_by_file: dict[str, Any],
    accretion_by_file: dict[str, Any],
) -> tuple[list[HotspotEntry], TestIndex | None]:
    """Write a page per current top hotspot; return the entries and test index.

    Sweeps the superseded run's history before the first page is written.
    """
    hotspot_entries: list[HotspotEntry] = []
    # Same flat-tree disambiguation the test_focus block applies to these files.
    hot_shared_names = shared_name_keys(
        h["path"] for h in current.get("top_hotspots", []))
    # One repository index for every hot file's parallel-tree (basename) probe.
    hot_test_index = build_test_index(repo_root) if current.get("top_hotspots") else None
    sweep_superseded_history(assess_dir, superseded)
    for h in current.get("top_hotspots", []):
        hotspot_entries.append(_write_current_hotspot_page(
            h, assess_dir=assess_dir, repo_root=repo_root, run_date=run_date,
            run_id=run_id, status_map=status_map,
            first_flagged_map=first_flagged_map,
            hot_shared_names=hot_shared_names, hot_test_index=hot_test_index,
            marker_debt_by_file=marker_debt_by_file,
            accretion_by_file=accretion_by_file,
        ))
    return hotspot_entries, hot_test_index


def _graduated_hotspot_entries(
    diff: StatsDiff, current: dict[str, Any], first_flagged_map: dict[str, str], run_date: str,
) -> list[HotspotEntry]:
    """Index entries for this run's graduated hotspots, with current metrics."""
    current_locs, current_ccns = _current_metrics_by_path(current)
    return [
        HotspotEntry(
            path=h.path,
            # Graduated means it was a prior hotspot; if we have no recorded
            # first-flagged date, it predates this run - "unknown", not today.
            first_flagged=first_flagged_map.get(h.path, "unknown"),
            last_seen=run_date,
            status="graduated",
            ccn=current_ccns.get(h.path),
            loc=current_locs.get(h.path),
        )
        for h in diff.graduated
    ]


def _current_metrics_by_path(current: dict[str, Any]) -> tuple[dict[str, int], dict[str, int]]:
    """Per-path LOC and CCN merged across the three top-N lists in ``current``.

    The first list that carries a metric for a path wins; a metric absent from
    every list stays absent (the wiki renders it as "-", never a zero).
    """
    current_locs: dict[str, int] = {}
    current_ccns: dict[str, int] = {}
    for src_key in ("top_hotspots", "top_complex", "top_large"):
        for entry in current.get(src_key, []):
            path = entry.get("path")
            if not path:
                continue
            loc = entry.get("loc")
            ccn = entry.get("ccn")
            if loc is not None and path not in current_locs:
                current_locs[path] = int(loc)
            if ccn is not None and path not in current_ccns:
                current_ccns[path] = int(ccn)
    return current_locs, current_ccns


@dataclass(frozen=True)
class RunWiki:
    """What writing the wiki hands back to the run-context assembly."""

    hot_test_index: TestIndex | None
    pruned_hotspots: list[str]
    retired_excluded: list[str]
    dropped_first_flagged: list[str]
    log_valid: bool
    log_broken_at: int | None


def write_run_wiki(
    current: dict[str, Any], *, assess_dir: Path, repo_root: Path, run_date: str,
    run_id: str, diff: StatsDiff, first_flagged_map: dict[str, str],
    superseded: dict[str, Any] | None, marker_debt_by_file: dict[str, Any],
    accretion_by_file: dict[str, dict[str, Any]], excluded_unfinalized: list[str],
    excluded_as_generated: Callable[[str], bool], scope_rel: str | None,
    instructions_grade: str | None, plugin_version: str,
) -> RunWiki:
    """Write the hotspot pages, index.md and this run's log.md entry.

    Mutates ``first_flagged_map`` (new dates stamped, retired entries dropped)
    and persists it to ``first-flagged.json``.
    """
    status_map = _hotspot_status_map(diff)

    # Wiki: hotspot pages for current top hotspots
    hotspot_entries, hot_test_index = _write_current_hotspot_pages(
        current, assess_dir=assess_dir, repo_root=repo_root, run_date=run_date,
        run_id=run_id, status_map=status_map,
        first_flagged_map=first_flagged_map, superseded=superseded,
        marker_debt_by_file=marker_debt_by_file,
        accretion_by_file=accretion_by_file,
    )

    # Prune orphan hotspot pages: any page from a prior run whose source file no
    # longer exists on disk is stamped retired (history preserved) so no active
    # page keeps describing a deleted file. Runs after the current top hotspots
    # are (re)written, so a file that is still a live hotspot has just had its
    # page refreshed and won't be touched.
    pruned_hotspots = prune_orphan_hotspots(assess_dir, repo_root)
    retired_excluded, dropped_first_flagged = retire_excluded_unfinalized(
        assess_dir, excluded_unfinalized, first_flagged_map,
    )

    # Also surface graduated hotspots in the index. Carry the file's actual
    # current metrics across the three top-N lists in `current` - graduating
    # off `top_hotspots[:10]` means the file fell out of the composite
    # ranking, NOT that its LOC or CCN dropped to zero. A graduated file
    # almost always still appears in `top_complex` (top 10 by raw CCN) or
    # `top_large` (top 10 by raw LOC) since those are wider views, so we
    # can recover real numbers in the common case. Merge metrics per-path
    # because top_complex entries carry only `ccn` and top_large entries
    # carry only `loc` - only top_hotspots carries both. When a metric
    # genuinely isn't present in any list, leave it as None - the wiki
    # renders None as "-" rather than misleading zeros (issue #52 Bug 1).
    hotspot_entries.extend(
        _graduated_hotspot_entries(diff, current, first_flagged_map, run_date))

    # Persist the updated first-flagged map for future runs
    save_first_flagged(assess_dir, first_flagged_map)

    write_index(
        assess_dir, hotspot_entries, last_updated=run_date,
        run_id=run_id, schema_version=ARTIFACT_SCHEMA_VERSION,
        scope=scope_rel, never_assessed=excluded_unfinalized,
        excluded_as_generated=excluded_as_generated,
    )

    top_action = "Deterministic ranker not yet wired (LLM picks Top 3)"
    log_entry = LogEntry(
        run_date=run_date,
        files_scored=current.get("files_scored", 0),
        readiness_score=0.0,  # LLM produces the layered score
        maturity_label="(LLM fills in)",
        instructions_grade=instructions_grade,
        graduated_count=len(diff.graduated),
        regressed_count=len(diff.regressed),
        restructured_count=len(diff.restructured),
        new_count=len(diff.new),
        persistent_count=len(diff.persistent),
        top_action=top_action,
        plugin_version=plugin_version,
        run_id=run_id,
        schema_version=ARTIFACT_SCHEMA_VERSION,
    )
    drop_superseded_log_entry(assess_dir, superseded)
    append_log_entry(assess_dir, log_entry)

    # log.md integrity: verify the chained checksums after the append. A break
    # (an earlier entry edited after the fact) is disclosed in log.md itself and
    # surfaced here so the report/gate can render it - a lying history is exactly
    # the self-description-under-no-pressure failure the toolkit guards against.
    log_valid, log_broken_at = verify_log_chain(assess_dir)

    return RunWiki(
        hot_test_index=hot_test_index,
        pruned_hotspots=pruned_hotspots,
        retired_excluded=retired_excluded,
        dropped_first_flagged=dropped_first_flagged,
        log_valid=log_valid,
        log_broken_at=log_broken_at,
    )
