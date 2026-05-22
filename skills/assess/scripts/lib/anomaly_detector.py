"""Detect anomalies in /assess run output.

Pure inspection of a run-context dict. No LLM, no file IO. Deterministic.

The detail strings on each Anomaly are SAFE-TO-SHARE: counts and grades only,
never paths or code. They form the body of self-feedback issues filed against
the toolkit repo.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Anomaly:
    code: str
    description: str
    detail: str  # sanitized - no paths, no code


def detect_anomalies(context: dict) -> list[Anomaly]:
    """Inspect a run-context dict and return any anomalies found."""
    found: list[Anomaly] = []
    stats = context.get("stats_summary", {})
    instruction_files = context.get("instruction_files", {})
    diff = context.get("diff", {})

    files_scored = stats.get("files_scored", 0)
    ccn = stats.get("ccn", {})
    hotspots = stats.get("top_hotspots", [])

    if files_scored == 0:
        found.append(Anomaly(
            code="ZERO_FILES_SCORED",
            description="Treemap reported 0 files scored.",
            detail="files_scored=0",
        ))

    if files_scored > 5 and ccn.get("p95", 0) == 0 and ccn.get("max", 0) == 0:
        found.append(Anomaly(
            code="ZERO_COMPLEXITY",
            description="All complexity metrics are zero despite files being scored.",
            detail=f"files_scored={files_scored}, ccn_p95=0, ccn_max=0",
        ))

    if files_scored > 200 and len(hotspots) == 0:
        found.append(Anomaly(
            code="EMPTY_HOTSPOTS",
            description="Large repo but no hotspots emerged.",
            detail=f"files_scored={files_scored}, hotspots_count=0",
        ))

    # Iterate over all present instruction files - the same check applies to each.
    for filename, file_info in instruction_files.items():
        if file_info.get("grade") == "F" and file_info.get("line_count", 0) > 200:
            # Use the file's basename for the detail so we don't leak any
            # repo-relative directory structure (e.g. .github/copilot-instructions.md).
            kind = filename.rsplit("/", 1)[-1]
            found.append(Anomaly(
                code="INSTRUCTION_FILE_GRADE_MISMATCH",
                description=f"{kind} is substantial but graded F.",
                detail=f"file={kind}, line_count={file_info.get('line_count')}, grade=F",
            ))

    hotspot_count = len(hotspots)
    new_count = diff.get("new", 0)
    persistent_count = diff.get("persistent", 0)
    prior_exists = context.get("prior_stats_exists", False)
    if prior_exists and hotspot_count > 5 and new_count == hotspot_count and persistent_count == 0:
        found.append(Anomaly(
            code="ALL_NEW_HOTSPOTS",
            description="All hotspots are new (none persisted). Stats rotation may have failed.",
            detail=f"hotspot_count={hotspot_count}, new_count={new_count}, persistent_count=0",
        ))

    return found
