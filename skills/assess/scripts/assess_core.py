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
# dependencies = [
#     "networkx",
# ]
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
from lib.anomaly_detector import detect_anomalies
from lib.doc_graph import build_doc_graph, is_repo_file
from lib.doc_staleness import analyze_doc_staleness
from lib.git_churn import tracked_files
from lib.liveness_scan import scan_liveness
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
    # Canonical repo-root locations
    "CLAUDE.md",
    "AGENTS.md",
    "GEMINI.md",
    ".cursorrules",
    # Tool-specific locations under .github/
    ".github/copilot-instructions.md",
    ".github/claude-instructions.md",
    ".github/claude-review-instructions.md",
    # docs/ subdirectory variants used by some projects
    "docs/CLAUDE.md",
    "docs/AGENTS.md",
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


def _grade_instruction_files(
    repo_root: Path,
) -> tuple[dict[str, dict], str | None, list[str], list[dict]]:
    """Scan all known instruction file locations and grade each one found.

    Returns: (files_dict, best_grade, untracked, dangling_refs)
        files_dict: keyed by filename, e.g. {"CLAUDE.md": {grade, score, ...}}.
        best_grade: best letter grade across all *tracked, present* files; None
            if none found. None is distinct from "F": None means no committed
            file exists ("create the file"), "F" means one exists but scored
            poorly ("fix the file").
        untracked: instruction files that exist on disk but aren't part of the
            repo (untracked / git-ignored / symlinked from outside). Surfaced as
            a finding so a personal CLAUDE.md isn't silently credited *or*
            silently ignored - the agent should note it isn't committed.
        dangling_refs: instruction files that are dangling symlinks (committed
            `.cursorrules -> missing-target`) - an advertised-but-broken
            instruction surface.
    """
    repo_root = repo_root.resolve()
    tracked = tracked_files(repo_root)
    found: dict[str, dict] = {}
    untracked: list[str] = []
    dangling_refs: list[dict] = []
    for rel_path in INSTRUCTION_FILE_PATHS:
        candidate = repo_root / rel_path
        # A dangling symlink (an instruction file pointing at a missing target)
        # exists() == False but is_symlink() == True - an advertised, broken
        # instruction reference, not "no file".
        if candidate.is_symlink() and not candidate.exists():
            dangling_refs.append({"path": rel_path, "reason": "symlink target missing"})
            continue
        if not candidate.exists():
            continue
        # Only grade genuine repo files. An on-disk-but-untracked instruction
        # file (a contributor's personal CLAUDE.md, or one symlinked in from
        # outside) is recorded as a finding rather than credited to the score.
        if not is_repo_file(candidate, repo_root, tracked):
            untracked.append(rel_path)
            continue
        text = candidate.read_text(encoding="utf-8")
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

    best = (max(found.values(), key=lambda v: GRADE_RANK.get(v["grade"], 0))["grade"]
            if found else None)
    return found, best, untracked, dangling_refs


# Basenames (lowercased) of the known instruction files, for cross-referencing
# broken doc links against the instruction surface.
_INSTRUCTION_BASENAMES = {Path(p).name.lower() for p in INSTRUCTION_FILE_PATHS}


def _broken_instruction_refs(doc_graph: dict, dangling_refs: list[dict]) -> list[dict]:
    """Combine dangling-symlink instruction files with broken doc links whose
    target is an instruction file (an entry doc linking a missing CLAUDE.md).
    These are advertised-but-broken instruction references."""
    refs = list(dangling_refs)
    if doc_graph.get("available"):
        for bl in doc_graph.get("broken_links", []):
            target = bl.get("target", "")
            if Path(target).name.lower() in _INSTRUCTION_BASENAMES:
                refs.append({
                    "from": bl.get("from"), "target": target,
                    "reason": "link to missing instruction file",
                })
    return refs


def _read_plugin_version() -> str:
    """Read the plugin version from .claude-plugin/plugin.json.

    The plugin.json lives three directories up from this script:
        scripts/assess_core.py -> scripts/ -> skills/assess/ -> skills/ -> repo root
    """
    plugin_json = Path(__file__).resolve().parents[3] / ".claude-plugin" / "plugin.json"
    try:
        data = json.loads(plugin_json.read_text(encoding="utf-8"))
        return str(data.get("version", "unknown"))
    except (FileNotFoundError, json.JSONDecodeError):
        return "unknown"


def _load_first_flagged(assess_dir: Path) -> dict[str, str]:
    """Load the first-flagged date map from .assess/first-flagged.json.

    Returns an empty dict if the file does not exist yet (first run).
    """
    state_file = assess_dir / "first-flagged.json"
    if not state_file.exists():
        return {}
    return json.loads(state_file.read_text(encoding="utf-8"))


def _save_first_flagged(assess_dir: Path, first_flagged: dict[str, str]) -> None:
    """Persist the first-flagged date map to .assess/first-flagged.json."""
    (assess_dir / "first-flagged.json").write_text(
        json.dumps(first_flagged, indent=2), encoding="utf-8"
    )


def _safe(label: str, fn):
    """Run a read-side scan, degrading to an unavailable marker on any failure.

    Read-side signals are additive context for the LLM, never gates - a broken
    scan must never block the assessment (PRD: "never block").
    """
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - intentional catch-all; degrade, don't crash
        return {"available": False, "reason": f"{label} scan failed: {e}"}


def _build_stale_hubs(doc_graph: dict, doc_staleness: dict) -> list[dict]:
    """Centrality x staleness - the priority Layer 0 signal.

    A stale *hub* (high PageRank) is the most dangerous lying map: everything
    routes through it. We join the doc graph's central docs with their
    staleness ratio so a stale hub surfaces as a top finding.

    Each hub carries the underlying `subject_method` + a `confidence` flag.
    `subject_method == "repo-baseline"` means the ratio is computed against
    repo-wide churn (no derivable subject), so the priority composite shares
    a denominator across every baseline entry - confidence on those is "low".
    The sort halves the priority of low-confidence entries so a precise-subject
    hub at half the raw priority of a baseline one still outranks it.
    """
    if not doc_graph.get("available") or not doc_staleness.get("available"):
        return []
    staleness_by_path = {d["path"]: d for d in doc_staleness.get("docs", [])}
    hubs: list[dict] = []
    for hub in doc_graph.get("hubs", []):
        s = staleness_by_path.get(hub["path"])
        if s is None:
            continue
        priority = round(hub["pagerank"] * s["ratio"], 3)
        confidence = s.get("confidence", "high")
        hubs.append({
            "path": hub["path"],
            "pagerank": hub["pagerank"],
            "last_commit_days": s["last_commit_days"],
            "code_churn_in_window": s["code_churn_in_window"],
            "ratio": s["ratio"],
            "subject_method": s.get("subject_method"),
            "confidence": confidence,
            "priority": priority,
        })
    return sorted(
        hubs,
        key=lambda h: -(h["priority"] * (0.5 if h["confidence"] == "low" else 1.0)),
    )


def build_run_context(*, repo_root: Path, run_date: str) -> dict:
    """Run the deterministic pipeline and return the structured context dict.

    Side effects: writes index.md, log.md, hotspots/*.md, run-context.json.
    """
    assess_dir = repo_root / ".assess"
    assess_dir.mkdir(parents=True, exist_ok=True)
    current = load_stats(assess_dir / "complexity-stats.json") or {
        "files_scored": 0, "top_hotspots": [], "top_complex": [], "top_large": [],
        "loc": {}, "ccn": {},
    }
    prior = load_stats(assess_dir / "complexity-stats.prior.json")
    prior_exists = prior is not None

    diff = diff_stats(prior=prior, current=current)
    instruction_files, instructions_grade, untracked_instr, dangling_instr = \
        _grade_instruction_files(repo_root)

    # Load (and later update) the persistent first-flagged date map
    first_flagged_map = _load_first_flagged(assess_dir)

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
        # Preserve the original first_flagged date; only set it to run_date on first appearance.
        if h["path"] not in first_flagged_map:
            first_flagged_map[h["path"]] = run_date
        first_flagged = first_flagged_map[h["path"]]
        status = status_map.get(h["path"], "active")
        hotspot_entries.append(HotspotEntry(
            path=h["path"],
            first_flagged=first_flagged,
            last_seen=run_date,
            status=status,
            ccn=h.get("ccn", 0),
            loc=h.get("loc", 0),
        ))
        write_hotspot_page(
            assess_dir,
            path=h["path"],
            first_flagged=first_flagged,
            last_seen=run_date,
            status=status,
            loc=h.get("loc", 0),
            ccn=h.get("ccn", 0),
            commits=h.get("commits", 0),
            has_tests=None,  # unknown until test pairing feature lands (deferred)
            history_rows=f"| {run_date} | {h.get('loc', 0)} | {h.get('ccn', 0)} | {h.get('commits', 0)} | {status} |",
            briefing=(
                f"Hotspot ({status}). "
                f"{h.get('loc', 0)} LOC, "
                f"max cyclomatic complexity {h.get('ccn', 0)}, "
                f"{h.get('commits', 0)} commits in churn window. "
                "(Briefing refined by LLM via assess_finalize - see Suggested actions below.)"
            ),
            actions="- Pending LLM-generated suggestions",
        )

    # Also surface graduated hotspots in the index
    for h in diff.graduated:
        hotspot_entries.append(HotspotEntry(
            path=h.path,
            first_flagged=first_flagged_map.get(h.path, run_date),
            last_seen=run_date,
            status="graduated",
            ccn=0,
            loc=0,
        ))

    # Persist the updated first-flagged map for future runs
    _save_first_flagged(assess_dir, first_flagged_map)

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
        "prior_stats_exists": prior_exists,
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

    # Read-side foundation signals (Layer 0 navigability + Layer 1 liveness).
    # Each is best-effort and degrades rather than blocking the assessment.
    doc_graph = _safe("doc_graph", lambda: build_doc_graph(repo_root).as_dict())
    doc_to_code = (doc_graph.get("doc_to_code_edges", [])
                   if doc_graph.get("available") else [])
    doc_staleness = _safe(
        "doc_staleness",
        lambda: analyze_doc_staleness(repo_root, doc_to_code_edges=doc_to_code),
    )
    liveness = _safe("liveness", lambda: scan_liveness(repo_root))
    ctx["doc_graph"] = doc_graph
    ctx["doc_staleness"] = doc_staleness
    ctx["stale_hubs"] = _build_stale_hubs(doc_graph, doc_staleness)
    # Instruction-surface integrity (Layer 0): files present on disk but not
    # committed, and advertised-but-broken instruction references (dangling
    # symlinks + entry docs linking a missing instruction file). A broken
    # instruction reference must penalise L0 even when one unrelated file grades
    # well - see the Layer 0 scoring rule in SKILL.md.
    ctx["untracked_instruction_files"] = untracked_instr
    ctx["broken_instruction_refs"] = _broken_instruction_refs(doc_graph, dangling_instr)
    # When the scan failed, preserve failure semantics: a failed scan must not be
    # read as "no observability" (rung 0) - that would mis-score Layer 1 Missing
    # when the truth is "not assessed". Carry the reason instead.
    liveness_ok = isinstance(liveness, dict) and "dead_code" in liveness
    reason = (liveness.get("reason", "liveness scan unavailable")
              if isinstance(liveness, dict) else "liveness scan unavailable")
    ctx["dead_code"] = (
        liveness["dead_code"] if liveness_ok
        else {"available": False, "candidate_count": 0, "candidates": [],
              "tools": [], "reason": reason}
    )
    ctx["observability"] = (
        liveness["observability"] if liveness_ok
        else {"rung": None, "available": False, "reason": reason,
              "instrumented": {"present": False, "signals": []},
              "discoverable": {"present": False, "signals": []},
              "reachable": {"present": False, "signals": []}}
    )

    ctx["plugin_version"] = _read_plugin_version()
    ctx["anomalies"] = [
        {"code": a.code, "description": a.description, "detail": a.detail}
        for a in detect_anomalies(ctx)
    ]
    (assess_dir / "run-context.json").write_text(json.dumps(ctx, indent=2), encoding="utf-8")
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
