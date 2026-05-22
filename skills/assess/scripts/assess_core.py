"""Orchestrator for the deterministic core of /assess.

Reads:
    {repo_root}/.assess/complexity-stats.json       (current run)
    {repo_root}/.assess/complexity-stats.prior.json (if it exists)
    {repo_root}/CLAUDE.md, AGENTS.md, GEMINI.md, .cursorrules, .github/copilot-instructions.md (any that exist)

Writes:
    {repo_root}/.assess/run-context.json   (everything the LLM needs)
    {repo_root}/.assess/index.md           (regenerated each run)
    {repo_root}/.assess/log.md             (appended each run)
    {repo_root}/.assess/hotspots/*.md      (one per top hotspot)

Run:
    uv run assess_core.py <repo_root>

The LLM still writes assess-report.md (the prose-heavy summary).
The LLM reads run-context.json to ground that prose in deterministic data.
"""
# /// script
# requires-python = ">=3.11"
# ///
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Make sibling lib package importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.agent_instructions_grader import grade_instructions
from lib.stats_diff import diff_stats, load_stats
from lib.wiki_writer import (
    HotspotEntry,
    LogEntry,
    append_log_entry,
    write_hotspot_page,
    write_index,
)


# Known agent instruction file locations (relative to repo root).
# The same heuristic grader applies to all of them.
INSTRUCTION_FILE_PATHS = [
    "CLAUDE.md",
    "AGENTS.md",
    "GEMINI.md",
    ".cursorrules",
    ".github/copilot-instructions.md",
]

# Grade ranking (best -> worst) for picking a top-level grade across multiple files.
GRADE_RANK = {"A": 7, "A-": 6, "B+": 5, "B": 4, "C": 3, "D": 2, "F": 1}


def _file_freshness_days(file_path: Path) -> int:
    """Days since file_path was last touched in git. Returns 0 if not in git."""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%ct", "--", str(file_path)],
            cwd=file_path.parent if file_path.parent.exists() else Path.cwd(),
            capture_output=True,
            text=True,
            check=False,
        )
        ts = int(out.stdout.strip()) if out.stdout.strip() else 0
        if ts == 0:
            return 0
        delta = datetime.now().timestamp() - ts
        return max(0, int(delta // 86400))
    except (ValueError, FileNotFoundError):
        return 0


def _grade_instruction_files(repo_root: Path) -> tuple[dict[str, dict], str]:
    """Scan all known instruction file locations and grade each one found.

    Returns: (files_dict, best_grade)
        files_dict: keyed by filename, e.g. {"CLAUDE.md": {grade, score, ...}, "AGENTS.md": {...}}
        best_grade: best letter grade across all present files; "F" if none found.
    """
    found: dict[str, dict] = {}
    for rel_path in INSTRUCTION_FILE_PATHS:
        candidate = repo_root / rel_path
        if not candidate.exists():
            continue
        text = candidate.read_text()
        freshness = _file_freshness_days(candidate)
        grade = grade_instructions(text, freshness_days=freshness)
        found[rel_path] = {
            "grade": grade.grade,
            "score": grade.score,
            "subscores": grade.subscores,
            "freshness_days": freshness,
            "line_count": len(text.splitlines()),
            "present": True,
        }

    if not found:
        return {}, "F"
    best = max(found.values(), key=lambda v: GRADE_RANK.get(v["grade"], 0))
    return found, best["grade"]


def build_run_context(*, repo_root: Path, run_date: str) -> dict:
    """Run the deterministic pipeline and return the structured context dict.

    Side effects: writes index.md, log.md, hotspots/*.md, run-context.json.
    """
    assess_dir = repo_root / ".assess"
    current = load_stats(assess_dir / "complexity-stats.json") or {
        "files_scored": 0, "top_hotspots": [], "top_complex": [], "top_large": [],
        "loc": {}, "ccn": {},
    }
    prior = load_stats(assess_dir / "complexity-stats.prior.json")

    diff = diff_stats(prior=prior, current=current)
    instruction_files, instructions_grade = _grade_instruction_files(repo_root)

    # Build status map: which paths are graduated, new, regressed, persistent
    status_map: dict[str, str] = {}
    for h in diff.graduated:
        status_map[h.path] = "graduated"
    for h in diff.new:
        status_map[h.path] = "new"
    for h in diff.regressed:
        status_map[h.path] = "regressed"
    for h in diff.persistent:
        status_map[h.path] = "persistent"

    # Wiki: hotspot pages for current top hotspots
    hotspot_entries: list[HotspotEntry] = []
    for h in current.get("top_hotspots", []):
        status = status_map.get(h["path"], "active")
        hotspot_entries.append(HotspotEntry(
            path=h["path"],
            first_flagged=run_date,    # refined in a later plan via log scan
            last_seen=run_date,
            status=status,
            ccn=h.get("ccn", 0),
            loc=h.get("loc", 0),
        ))
        write_hotspot_page(
            assess_dir,
            path=h["path"],
            first_flagged=run_date,
            last_seen=run_date,
            status=status,
            loc=h.get("loc", 0),
            ccn=h.get("ccn", 0),
            commits=h.get("commits", 0),
            has_tests=False,  # filled in by a follow-up plan (test pairing)
            history_rows=f"| {run_date} | {h.get('loc', 0)} | {h.get('ccn', 0)} | {h.get('commits', 0)} | {status} |",
            briefing=f"Hot file in this repo. CCN {h.get('ccn', 0)}, {h.get('loc', 0)} LOC.",
            actions="- Pending LLM-generated suggestions",
        )

    # Also surface graduated hotspots in the index
    for h in diff.graduated:
        hotspot_entries.append(HotspotEntry(
            path=h.path,
            first_flagged=run_date,
            last_seen=run_date,
            status="graduated",
            ccn=0,
            loc=0,
        ))

    write_index(assess_dir, hotspot_entries, last_updated=run_date)

    top_action = "Deterministic ranker not yet wired (LLM picks Top 3)"
    log_entry = LogEntry(
        run_date=run_date,
        files_scored=current.get("files_scored", 0),
        readiness_score=0.0,  # LLM produces the layered score
        maturity_label="(LLM fills in)",
        instructions_grade=instructions_grade,
        graduated_count=len(diff.graduated),
        regressed_count=len(diff.regressed),
        new_count=len(diff.new),
        persistent_count=len(diff.persistent),
        top_action=top_action,
    )
    append_log_entry(assess_dir, log_entry)

    ctx = {
        "run_date": run_date,
        "repo_root": str(repo_root),
        "stats_summary": {
            "files_scored": current.get("files_scored", 0),
            "loc": current.get("loc", {}),
            "ccn": current.get("ccn", {}),
            "top_hotspots": current.get("top_hotspots", []),
        },
        "instruction_files": instruction_files,
        "instructions_grade": instructions_grade,
        "diff": diff.summary(),
        "diff_detail": {
            "graduated": [h.__dict__ for h in diff.graduated],
            "regressed": [h.__dict__ for h in diff.regressed],
            "new": [h.__dict__ for h in diff.new],
            "persistent": [h.__dict__ for h in diff.persistent],
        },
    }
    (assess_dir / "run-context.json").write_text(json.dumps(ctx, indent=2))
    return ctx


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: assess_core.py <repo_root>", file=sys.stderr)
        return 2
    repo_root = Path(sys.argv[1]).resolve()
    run_date = datetime.now().strftime("%Y-%m-%d")
    ctx = build_run_context(repo_root=repo_root, run_date=run_date)
    print(json.dumps(ctx["diff"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
