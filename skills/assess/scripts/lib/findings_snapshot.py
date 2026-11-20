"""The committed findings snapshot and the method-version comparison the gate reads.

Each run writes ``findings.json`` into its artifact directory (``.assess/`` or
``.assess/<scope-slug>/``): the finding method version (``ASSESS_METHOD_VERSION``)
and every ``derived_findings`` name with its sorted paths. The file is committed,
so it holds the finding set the repository last accepted. Before overwriting it,
the run reads the previous snapshot back and records it in ``run-context.json``
as ``prior_findings_snapshot``; in CI that is the committed file, because the
checkout is fresh.

When the prior snapshot's method version differs from this run's, the method
moved findings on unchanged code. ``method_change`` compares the two and lists,
per finding, the paths that appeared and cleared (which also carry any code
changed since the snapshot was committed), so ``assess_gate.py`` can name the
method change and print the re-freeze command instead of leaving the moves
with no stated cause.

The file carries no run id or date: a run whose findings did not move leaves it
byte-identical, so it only shows in a diff when a finding moved.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lib.run_context_types import FindingPaths, FindingsSnapshot, MethodChange, PathMove

FINDINGS_SNAPSHOT_FILE = "findings.json"

# Printed by the gate on a method-version mismatch. ``{snapshot}`` is the
# snapshot's repo-relative path, ``{artifact_dir}`` the run's artifact directory
# (``.assess`` or ``.assess/<scope-slug>``) and ``{scope_args}`` the
# ``--scope <path>`` a scoped run passes to both steps (empty for a whole-repo
# run). The headless form runs the same two steps, in the same order, as the
# gate action's render step (action.yml): the treemap refreshes
# complexity-stats.json, then the core computes the findings from it.
REFREEZE_COMMAND = (
    "re-run /assess{scope_arg}, or headless from the assess skill directory: "
    "`uv run scripts/complexity-treemap.py <repo_root>{scope_args} "
    "-o <repo_root>/{artifact_dir}/complexity-heatmap.svg "
    "--stats <repo_root>/{artifact_dir}/complexity-stats.json && "
    "uv run scripts/assess_core.py <repo_root>{scope_args}`; then commit {snapshot}"
)

# Printed instead when this run's method version is older than the snapshot's:
# CI runs a toolkit release behind the one that wrote the snapshot (the gate
# workflow pins a published tag), so re-freezing with it would only undo the
# newer method, and the note would come back on the next upgrade.
RAISE_PIN_COMMAND = (
    "raise the toolkit version on the gate workflow's "
    "`uses: bjcoombs/ai-native-toolkit@v<version>` line to a release at finding "
    "method version {prior} or later; leave {snapshot} as committed"
)


def build_snapshot(findings: list[Any], method_version: int) -> FindingsSnapshot:
    """The snapshot for one run's ``derived_findings``: names in run order,
    paths sorted so the committed file only changes when a finding moves."""
    rows: list[FindingPaths] = []
    for f in findings:
        if not isinstance(f, dict) or not isinstance(f.get("name"), str):
            continue
        paths = f.get("paths") or []
        rows.append({
            "name": f["name"],
            "paths": sorted({p for p in paths if isinstance(p, str)}),
        })
    return {"assess_method_version": method_version, "findings": rows}


def load_snapshot(assess_dir: Path) -> FindingsSnapshot | None:
    """Read the previous ``findings.json``; ``None`` when absent or malformed.

    A malformed file cannot establish a prior method version, so it is treated
    as no snapshot: the gate then has nothing to compare, never a false move.
    """
    path = assess_dir / FINDINGS_SNAPSHOT_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    version = raw.get("assess_method_version")
    findings = raw.get("findings")
    # A hand-edited file may hold the version as a numeric string ("12").
    if isinstance(version, str) and version.strip().isdigit():
        version = int(version)
    if not isinstance(version, int) or isinstance(version, bool):
        return None
    if not isinstance(findings, list):
        return None
    return build_snapshot(findings, version)


def committed_prior(ctx: dict[str, Any], repo_root: Path) -> FindingsSnapshot | None:
    """The snapshot the gate compares this run against.

    The run recorded ``prior_findings_snapshot`` before overwriting
    ``findings.json``; in CI, where the pipeline runs just before the gate, that
    is the committed file. When the gate is run directly against a committed
    snapshot (no fresh pipeline run since it was edited or checked out), the
    file on disk is the one the repository accepted and the recorded value is
    stale. So the on-disk snapshot wins whenever its method version differs from
    this run's; otherwise (the run just rewrote it at the current version, or it
    is absent) the recorded prior stands.
    """
    slug = ctx.get("scope_slug")
    artifact_dir = repo_root / ".assess" / slug if isinstance(slug, str) and slug else repo_root / ".assess"
    on_disk = load_snapshot(artifact_dir)
    if on_disk is not None and on_disk["assess_method_version"] != ctx.get("assess_method_version"):
        return on_disk
    recorded = ctx.get("prior_findings_snapshot")
    if not isinstance(recorded, dict):
        return None
    version = recorded.get("assess_method_version")
    if not isinstance(version, int) or isinstance(version, bool):
        return None
    return build_snapshot(recorded.get("findings") or [], version)


def write_snapshot(assess_dir: Path, snapshot: FindingsSnapshot) -> None:
    """Write ``findings.json`` with a trailing newline, keys in a fixed order."""
    (assess_dir / FINDINGS_SNAPSHOT_FILE).write_text(
        json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")


def _paths_by_name(findings: list[Any]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for f in findings:
        if isinstance(f, dict) and isinstance(f.get("name"), str):
            paths = f.get("paths") or []
            out.setdefault(f["name"], set()).update(
                p for p in paths if isinstance(p, str))
    return out


def method_change(ctx: dict[str, Any]) -> MethodChange | None:
    """The finding moves a method-version change explains, or ``None``.

    ``None`` when there is no prior snapshot (a first run, or the snapshot was
    never committed) or when the versions match. Otherwise every
    ``(finding, path)`` present now and not in the snapshot is ``appeared``, and
    every one in the snapshot and gone now is ``cleared``; both are sorted by
    finding, then path. ``downgrade`` is set when this run's method version
    is older than the snapshot's (CI pinned to an older toolkit release); the
    remedy is then raising that pin, not re-freezing.
    """
    prior = ctx.get("prior_findings_snapshot")
    current_version = ctx.get("assess_method_version")
    if not isinstance(prior, dict) or not isinstance(current_version, int):
        return None
    prior_version = prior.get("assess_method_version")
    if not isinstance(prior_version, int) or prior_version == current_version:
        return None
    before = _paths_by_name(prior.get("findings") or [])
    after = _paths_by_name(ctx.get("derived_findings") or [])
    appeared: list[PathMove] = []
    cleared: list[PathMove] = []
    for name in sorted(set(before) | set(after)):
        old, new = before.get(name, set()), after.get(name, set())
        appeared.extend({"finding": name, "path": p} for p in sorted(new - old))
        cleared.extend({"finding": name, "path": p} for p in sorted(old - new))
    slug = ctx.get("scope_slug")
    scope = ctx.get("scope")
    artifact_dir = f".assess/{slug}" if slug else ".assess"
    snapshot = f"{artifact_dir}/{FINDINGS_SNAPSHOT_FILE}"
    scoped = bool(slug) and isinstance(scope, str) and bool(scope)
    downgrade = prior_version > current_version
    refreeze = (
        RAISE_PIN_COMMAND.format(prior=prior_version, snapshot=snapshot)
        if downgrade else REFREEZE_COMMAND.format(
            snapshot=snapshot, artifact_dir=artifact_dir,
            scope_arg=f" {scope}" if scoped else "",
            scope_args=f" --scope {scope}" if scoped else "",
        )
    )
    return {
        "prior_method_version": prior_version,
        "method_version": current_version,
        "downgrade": downgrade,
        "appeared": appeared,
        "cleared": cleared,
        "snapshot": snapshot,
        "refreeze": refreeze,
    }
