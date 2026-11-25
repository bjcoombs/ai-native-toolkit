"""Setup-stage adapters for the scan table: the run's own state and header.

``build_run_context`` used to open with about a hundred lines of locals - the
scope, the artifact directory, the run id, the complexity stats and their diff,
the instruction grading, the first-flagged map - before assembling the
25-key run-context header by hand. Each of those is now a ``STAGE_SETUP``
entry in ``lib/scan_registry.py`` whose callable lives here, so the order they
depend on each other is declared rather than implied by statement order.

Every function is a read: the wiki, the badge and ``run-context.json`` are
written after the scans by ``lib/run_publish.py``.

Setup-stage adapters for the scan table (#557): `run_scope`, `run_assess_dir`,
`new_run_id`, `current_stats` / `prior_stats`, `stats_diff` (dropping a graduation the
generated-file filter caused), `instruction_grading`, `rename_map` and
`measured_commit`, `config_excludes`, `scan_plan_stamp` (the `scan_plan` key a `--profile` or
`--levels` run records, #1268),
`first_flagged`, `superseded_run`, `unfinalized_exclusions`, `read_plugin_version`, and
`run_header`, which returns the first 28 run-context keys (`RUN_HEADER_KEYS`) with the
four `WIKI_KEYS`, `prior_findings_snapshot` and `prior_level` (#940) reserved as None for the publish step. Reads only. Tests:
`tests/test_run_setup.py`.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from collections.abc import Mapping
from typing import Any, Protocol

from lib.artifact_schema import ARTIFACT_SCHEMA_VERSION, ASSESS_METHOD_VERSION
from lib.assess_config import load_excludes
from lib.fleet_config import FleetConfig, repo_fleet_config
from lib.change_coupling import RenameMap, build_rename_map
from lib.context_blocks import generated_exclusion
from lib.complexity.diff_reliability import compute_diff_reliability, prior_stamps, stats_tool_versions
from lib.git_churn import git_commit_info, window_from_churn_label
from lib.instructions.instruction_files import grade_instruction_files
from lib.plugin_version import read_version
from lib.run_scope import assess_dir_for, load_current_stats, resolve_scope
from lib.complexity.stats_diff import StatsDiff, diff_stats, load_stats
from lib.wiki_state import (
    excluded_after_unfinalized_run,
    load_first_flagged,
    rekey_first_flagged,
    same_measurement_prior_run,
)


class _Plan(Protocol):
    """``lib.scan_plan.ScanPlan``, by shape: importing it would reach the level engine and the
    platform clients, and the setup adapters stay offline (``tests/test_signals.py``)."""

    def stamp(self) -> Mapping[str, object]: ...

# The run-context keys a wiki write fills. The header reserves them (``None``)
# so they keep their place in run-context.json; lib/run_publish.py assigns them
# after the wiki is written.
WIKI_KEYS = (
    "pruned_hotspots", "retired_excluded_hotspots", "dropped_first_flagged",
    "log_integrity",
)

# The run header's keys, in their published order.
RUN_HEADER_KEYS = (
    "run_id", "artifact_schema_version", "assess_method_version", "run_date", "scope",
    "scope_slug", "measured_commit", "prior_stats_exists", "stats_summary", "instruction_files",
    "instructions_grade", "diff", "diff_reliable", "diff_version_note", "diff_trend_reset",
    "prior_plugin_version", "schema_version", "prior_schema_version", "tool_versions",
    "prior_tool_versions", "diff_detail", "pruned_hotspots", "retired_excluded_hotspots",
    "dropped_first_flagged", "provisional_first_flagged", "log_integrity",
    "prior_findings_snapshot", "prior_level",
)

PLUGIN_JSON = Path(__file__).resolve().parents[4] / ".claude-plugin" / "plugin.json"


def new_run_id() -> str:
    """A unique id for this run: a sortable wall-clock stamp plus random suffix.

    ``YYYYMMDDHHMMSS-<8 hex>`` - the timestamp orders runs, the uuid suffix makes
    two runs in the same second still distinct. Stamped on every artifact so
    finalize can prove the finalize-input and run-context came from one run.
    """
    return f"{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"


def scan_plan_stamp(plan: _Plan | None) -> dict[str, Mapping[str, object]]:
    """The ``scan_plan`` key of a narrowed run (#1268): the model, the scans it ran and the
    scans it left out; nothing for a full run, so its run context is unchanged."""
    return {} if plan is None else {"scan_plan": plan.stamp()}


def read_plugin_version(plugin_json: Path = PLUGIN_JSON) -> str:
    """The plugin version from ``.claude-plugin/plugin.json``, else the standalone skill's
    ``plugin-version.json`` (``lib.plugin_version``, #1166), or ``unknown``.

    Never raises: an unreadable, malformed or non-object manifest reads as
    ``unknown``, the string every reader (decline markers, the wiki log, the
    report) already treats as no version, so the scan table's entry needs no
    degrade marker in place of a string.
    """
    return read_version(plugin_json)


def run_scope(repo_root: Path, requested_scope: Path | None) -> tuple[Path | None, str | None, str]:
    """``(scope_abs, scope_rel, scope_slug)``; raises ValueError for a bad scope."""
    return resolve_scope(repo_root, requested_scope)


def scope_root(scope: tuple[Path | None, str | None, str]) -> Path | None:
    """The absolute subtree the file-enumerating scans see (None: whole repo)."""
    return scope[0]


def run_assess_dir(repo_root: Path, scope: tuple[Path | None, str | None, str]) -> Path:
    """``.assess/`` or ``.assess/<slug>/``. Created by the publish step, not here."""
    return assess_dir_for(repo_root, scope[2])


def current_stats(assess_dir: Path) -> dict[str, Any]:
    """This run's complexity stats sidecar, or an empty snapshot."""
    return load_current_stats(assess_dir)


def prior_stats(assess_dir: Path) -> dict[str, Any] | None:
    """The previous run's rotated stats sidecar, or None on a first run."""
    return load_stats(assess_dir / "complexity-stats.prior.json")


def stats_diff(prior: dict[str, Any] | None, current: dict[str, Any], excluded_as_generated: Any) -> StatsDiff:
    """The hotspot diff against the prior stats.

    A hotspot that left the ranking because this run excluded it as generated
    did not graduate: the filter changed, not the file. It is dropped from the
    graduated list so the append-only log and index never record it as one.
    """
    diff = diff_stats(prior=prior, current=current)
    diff.graduated = [h for h in diff.graduated if not excluded_as_generated(h.path)]
    return diff


def generated_paths(current: dict[str, Any]) -> Any:
    """Predicate: is a path excluded as generated by this run's stats?"""
    return generated_exclusion(current)


def instruction_grading(repo_root: Path) -> tuple[Any, ...]:
    """``grade_instruction_files``: files, grade, untracked, dangling, skills, sensitive."""
    return grade_instruction_files(repo_root)


def graded_instruction_files(grading: tuple[Any, ...]) -> dict[str, Any]:
    """The per-file grades the instruction scans read."""
    files: dict[str, Any] = grading[0]
    return files


def rename_map(repo_root: Path) -> RenameMap:
    """Historical path -> current path, from git's rename detection."""
    return build_rename_map(repo_root)


def measured_commit(repo_root: Path) -> dict[str, Any]:
    """The commit the scan measured (HEAD, dirty state, upstream lag)."""
    return git_commit_info(repo_root)


def history_window(current: dict[str, Any]) -> str | None:
    """The stats sidecar's churn window as ``<n>d|w|mo|y``, or None for all history."""
    return window_from_churn_label(current.get("churn_window"))


def config_excludes(repo_root: Path) -> tuple[set[str], list[str]]:
    """``.assess/config.toml`` excludes, read by every scan."""
    return load_excludes(repo_root)


def fleet_config(repo_root: Path) -> FleetConfig:
    """The ``[ai_usage]``, ``[review]`` and ``[platform]`` sections of ``.assess/config.toml``
    (no fleet file in an interactive run; ``lib/fleet_config.py``). Never raises on repo input."""
    return repo_fleet_config(repo_root)


def ghsync_root(repo_root: Path) -> Path | None:
    """The ghsync root GitLab ``project:`` includes resolve against (#766): none in an
    interactive run (only ``local:`` includes resolve); the headless signals run provides
    ``--ghsync-root``."""
    return None


def first_flagged(assess_dir: Path, rename_map: RenameMap) -> dict[str, str]:
    """The persisted first-flagged dates, with renamed files moved to their current path."""
    return rekey_first_flagged(load_first_flagged(assess_dir), rename_map.paths)


def superseded_run(assess_dir: Path, run_date: str, measured_commit: dict[str, Any]) -> dict[str, Any] | None:
    """The previous run's context when this run supersedes it (same date and commit)."""
    return same_measurement_prior_run(assess_dir, run_date=run_date, measured_commit=measured_commit)


def unfinalized_exclusions(
    assess_dir: Path, superseded: dict[str, Any] | None, first_flagged_map: dict[str, str],
    current: dict[str, Any], diff: StatsDiff, excludes: tuple[set[str], list[str]],
) -> tuple[set[str], list[str]]:
    """``(provisional, excluded)``: files excluded after a never-finalized run (#356)."""
    return excluded_after_unfinalized_run(
        assess_dir, superseded=superseded, first_flagged_map=first_flagged_map,
        current=current, diff=diff, excludes=excludes,
    )


def run_header(
    run_id: str, run_date: str, scope: tuple[Path | None, str | None, str],
    measured_commit: dict[str, Any], current: dict[str, Any], prior: dict[str, Any] | None,
    grading: tuple[Any, ...], diff: StatsDiff, unfinalized: tuple[set[str], list[str]],
) -> dict[str, Any]:
    """The first 27 run-context keys, in their published order.

    Run provenance (``run_id``; ``artifact_schema_version``, distinct from the
    stats-layout ``schema_version``; ``assess_method_version``, the finding
    method the gate compares with the committed findings.json), the monorepo scope (both null / "" for a
    whole-repo run), the measured commit, the stats summary, the instruction
    grades, the hotspot diff and its comparability (a MAJOR plugin bump resets
    the trend; a backend version change or an unstamped prior voids the diff),
    and the wiki-state keys. The four ``WIKI_KEYS`` are reserved here and filled
    by the publish step after the wiki is written, and ``prior_findings_snapshot``
by the same step just before it rewrites findings.json, and ``prior_level`` just before
    it draws the diagrams and rewrites prior-level.json (#940).
    """
    _, scope_rel, scope_slug = scope
    prior_version, prior_schema = prior_stamps(prior)
    diff_reliable, diff_version_note, diff_trend_reset = compute_diff_reliability(
        prior is not None, prior, current,
    )
    provisional, _ = unfinalized
    header: dict[str, Any] = {
        "run_id": run_id,
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "assess_method_version": ASSESS_METHOD_VERSION,
        "run_date": run_date,
        "scope": scope_rel,
        "scope_slug": scope_slug or None,
        "measured_commit": measured_commit,
        "prior_stats_exists": prior is not None,
        "stats_summary": {
            # No sidecar: unavailable with its reason and a null count, never a measured 0 (#959).
            **({"available": False, "reason": current.get("reason")}
               if current.get("available") is False else {}),
            "files_scored": None if current.get("available") is False else current.get("files_scored", 0),
            "loc": current.get("loc", {}),
            # Lines per path class (#820). A sidecar from before it reads unavailable, never 0;
            # with no sidecar at all the outer marker already names the cause.
            **({} if current.get("available") is False else {"loc_by_class": current.get("loc_by_class") or {
                "available": False, "reason": "no_class_split: the stats sidecar predates loc_by_class"}}),
            "est_tokens": current.get("est_tokens", {}),
            "ccn": current.get("ccn", {}),
            "top_hotspots": current.get("top_hotspots", []),
            # scc-only files with implausibly low ccn (issue #590); [] for an
            # older sidecar that predates the check.
            "complexity_unreliable": current.get("complexity_unreliable", []),
        },
        "instruction_files": grading[0],
        "instructions_grade": grading[1],
        "diff": diff.summary(),
        "diff_reliable": diff_reliable,
        "diff_version_note": diff_version_note,
        "diff_trend_reset": diff_trend_reset,
        "prior_plugin_version": prior_version,
        "schema_version": current.get("schema_version"),
        "prior_schema_version": prior_schema,
        "tool_versions": stats_tool_versions(current),
        "prior_tool_versions": stats_tool_versions(prior),
        "diff_detail": {
            "graduated": [h.__dict__ for h in diff.graduated],
            "regressed": [h.__dict__ for h in diff.regressed],
            "restructured": [h.__dict__ for h in diff.restructured],
            "new": [h.__dict__ for h in diff.new],
            "persistent": [h.__dict__ for h in diff.persistent],
        },
        "pruned_hotspots": None,
        "retired_excluded_hotspots": None,
        "dropped_first_flagged": None,
        "provisional_first_flagged": sorted(provisional),
        "log_integrity": None,
        # Reserved like the WIKI_KEYS: lib/run_publish.py reads the previous
        # findings.json into it just before rewriting that file (#554).
        "prior_findings_snapshot": None,
        # Reserved the same way: run_publish reads the previous prior-level.json into it (#940).
        "prior_level": None,
    }
    return header
