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
import sys
from pathlib import Path
from typing import Any

from lib.run_context_types import FindingPaths, FindingsSnapshot, MethodChange, PathMove
from lib.run_scope import assess_dir_for

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


def _encodable(s: object) -> bool:
    """A string that can be written back out as UTF-8. A hand-edited JSON
    escape such as ``"\\ud800"`` loads as a lone surrogate, which would raise
    later when the gate prints it."""
    if not isinstance(s, str):
        return False
    try:
        s.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _entry_paths(entry: dict[str, Any]) -> list[str]:
    """The string paths of one finding entry; a missing or non-list ``paths``
    (a hand-edited ``"paths": 5`` or ``"paths": "a.py"``) has none."""
    paths = entry.get("paths")
    if not isinstance(paths, list):
        return []
    return [p for p in paths if _encodable(p)]


def _entry_is_clean(entry: object) -> bool:
    """Whether ``build_snapshot`` keeps every part of a loaded entry."""
    if not isinstance(entry, dict) or not _encodable(entry.get("name")):
        return False
    paths = entry.get("paths", [])
    return isinstance(paths, list) and all(_encodable(p) for p in paths)


def build_snapshot(findings: list[Any], method_version: int) -> FindingsSnapshot:
    """The snapshot for one run's ``derived_findings``: names in run order,
    paths sorted so the committed file only changes when a finding moves.
    Entries without a string name are skipped; non-string paths are dropped."""
    rows: list[FindingPaths] = []
    for f in findings if isinstance(findings, list) else []:
        if not isinstance(f, dict) or not _encodable(f.get("name")):
            continue
        rows.append({"name": f["name"], "paths": sorted(set(_entry_paths(f)))})
    return {"assess_method_version": method_version, "findings": rows}


def _parse_version(version: object) -> int | None:
    # A hand-edited file may hold the version as a numeric string ("12").
    # ``isdigit`` alone would accept "²", which ``int`` rejects.
    if isinstance(version, str):
        try:
            version = int(version.strip())
        except ValueError:
            return None
    if not isinstance(version, int) or isinstance(version, bool):
        return None
    return version


def read_snapshot(assess_dir: Path) -> tuple[FindingsSnapshot | None, str | None]:
    """Read the previous ``findings.json`` as ``(snapshot, reason)``.

    ``reason`` is a fixed token naming what was wrong with the file, or
    ``None`` when it was absent or clean. A file that cannot establish a prior
    method version gives ``(None, reason)``: ``unreadable``, ``not_utf8``,
    ``invalid_json`` (including an integer too long to parse), ``too_deep``,
    ``not_object``, ``version_missing``, ``version_invalid``,
    ``findings_missing`` or ``findings_not_list``. A file whose version and
    list are sound but with some unusable entries (not an object, no string
    name, non-list ``paths``, a non-string path) gives the snapshot of the
    rest with ``malformed_entries``. Never raises on file content.
    """
    path = assess_dir / FINDINGS_SNAPSHOT_FILE
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None, None
    except OSError:
        return None, "unreadable"
    try:
        # ``utf-8-sig`` accepts the BOM some Windows editors add.
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None, "not_utf8"
    try:
        raw = json.loads(text)
    except RecursionError:
        return None, "too_deep"
    except ValueError:  # JSONDecodeError, or an over-long integer literal
        return None, "invalid_json"
    if not isinstance(raw, dict):
        return None, "not_object"
    if "assess_method_version" not in raw:
        return None, "version_missing"
    version = _parse_version(raw["assess_method_version"])
    if version is None:
        return None, "version_invalid"
    if "findings" not in raw:
        return None, "findings_missing"
    findings = raw["findings"]
    if not isinstance(findings, list):
        return None, "findings_not_list"
    reason = None if all(_entry_is_clean(f) for f in findings) else "malformed_entries"
    return build_snapshot(findings, version), reason


def load_snapshot(assess_dir: Path) -> FindingsSnapshot | None:
    """Read the previous ``findings.json``; ``None`` when absent or malformed.

    A malformed file cannot establish a prior method version, so it is treated
    as no snapshot: the gate then has nothing to compare, never a false move.
    A malformed file prints one stderr line naming the ``read_snapshot``
    reason (an absent file is silent), in the pipeline as well as the gate,
    since in CI the pipeline overwrites the file before the gate reads it.
    """
    snapshot, reason = read_snapshot(assess_dir)
    if reason is not None:
        effect = ("unusable entries skipped" if snapshot is not None
                  else "treated as no prior snapshot")
        print(
            f"/assess: {(assess_dir / FINDINGS_SNAPSHOT_FILE).as_posix()} is "
            f"malformed ({reason}); {effect}.",
            file=sys.stderr,
        )
    return snapshot


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
    on_disk = load_snapshot(assess_dir_for(repo_root, slug if isinstance(slug, str) else ""))
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
    for f in findings if isinstance(findings, list) else []:
        if isinstance(f, dict) and isinstance(f.get("name"), str):
            out.setdefault(f["name"], set()).update(_entry_paths(f))
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
