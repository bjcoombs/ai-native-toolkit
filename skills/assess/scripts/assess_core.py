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
#     "grimp",
# ]
# ///
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Make sibling lib package importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.agent_instructions_grader import (
    detect_alias,
    detect_skills_dir,
    grade_instructions,
    scan_sensitive_content,
)
from lib.anomaly_detector import detect_anomalies
from lib.assess_config import load_excludes, load_structure_config
from lib.doc_graph import build_doc_graph, is_repo_file
from lib.doc_staleness import analyze_doc_staleness
from lib.git_churn import tracked_files
from lib.keyhole_signals import integrate as integrate_keyhole_signals
from lib.liveness_scan import scan_liveness
from lib.structure_graph import analyze_structure
from lib.stats_diff import diff_stats, hotspot_commits, load_stats
from lib.test_pressure import scan_test_pressure
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
) -> tuple[dict[str, dict], str | None, list[str], list[dict], dict, dict]:
    """Scan all known instruction file locations and grade each one found.

    Returns: (files_dict, best_grade, untracked, dangling_refs, skills_info, sensitive)
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
        sensitive: per-path list of REDACTED sensitive-content findings (IPs,
            SSH/host details, credentials, home-dir/PII paths) for any candidate
            on disk - tracked or untracked. Surfaced so the remediation warns
            before recommending a file be committed, especially to a public repo
            (issue #56).
    """
    repo_root = repo_root.resolve()
    tracked = tracked_files(repo_root)
    # Detect skills directories once, before grading any file. A repo that
    # factors guidance into on-demand skills uses progressive disclosure, so a
    # large instruction file is not penalized as bloat (see compute_bloat_penalty).
    skills_info = detect_skills_dir(repo_root)
    skills_present = skills_info["skills_dirs_present"]
    found: dict[str, dict] = {}
    untracked: list[str] = []
    dangling_refs: list[dict] = []
    sensitive: dict[str, list] = {}
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
        # Scan every candidate on disk for content unsafe to publish - tracked
        # OR untracked. An untracked file is exactly the one the remediation
        # might tell the user to commit, so it must be scanned before that.
        try:
            disk_text = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            disk_text = ""
        flags = scan_sensitive_content(disk_text) if disk_text else []
        if flags:
            sensitive[rel_path] = flags
        # Only grade genuine repo files. An on-disk-but-untracked instruction
        # file (a contributor's personal CLAUDE.md, or one symlinked in from
        # outside) is recorded as a finding rather than credited to the score.
        if not is_repo_file(candidate, repo_root, tracked):
            untracked.append(rel_path)
            continue
        text = disk_text
        freshness = _file_freshness_days(candidate)
        grade = grade_instructions(
            text, freshness_days=freshness, skills_present=skills_present
        )
        entry = {
            "grade": grade.grade,
            "score": grade.score,
            "subscores": grade.subscores,
            "freshness_days": freshness,
            "line_count": len(text.splitlines()),
            "present": True,
        }
        # Alias detection (issue #57): a committed AGENTS.md/GEMINI.md that is a
        # symlink to - or a thin stub pointing at - a canonical instruction file
        # is the desired single-source-of-truth shape, not a low-scoring bespoke
        # doc. Record the target so the second pass can inherit its grade.
        alias_target = _alias_target(candidate, text, repo_root)
        if alias_target:
            entry["alias_target_basename"] = alias_target
        found[rel_path] = entry

    _resolve_alias_grades(found)

    best = (max(found.values(), key=lambda v: GRADE_RANK.get(v["grade"], 0))["grade"]
            if found else None)
    return found, best, untracked, dangling_refs, skills_info, sensitive


# Basenames (lowercased) of the known instruction files, for cross-referencing
# broken doc links against the instruction surface.
_INSTRUCTION_BASENAMES = {Path(p).name.lower() for p in INSTRUCTION_FILE_PATHS}

# Canonical files an alias would point at (a single source of truth).
_CANONICAL_ALIAS_TARGETS = {"claude.md", "agents.md", "gemini.md"}


def _alias_target(candidate: Path, text: str, repo_root: Path) -> str | None:
    """Return the canonical basename this file aliases, or None.

    Two shapes count as an alias (issue #57):
      * a symlink whose target is a canonical instruction file, or
      * a thin stub whose only real content references a canonical file.
    The alias's own basename is excluded, so CLAUDE.md never aliases itself.
    """
    self_name = candidate.name.lower()
    # Symlink alias - the target is whatever the link resolves to.
    if candidate.is_symlink():
        try:
            target_name = candidate.resolve().name.lower()
        except OSError:
            target_name = ""
        if target_name in _CANONICAL_ALIAS_TARGETS and target_name != self_name:
            return candidate.resolve().name
    # Thin-stub alias - short file that just points at a canonical doc.
    alias = detect_alias(text)
    if alias["is_alias"] and alias["alias_target"]:
        if alias["alias_target"].lower() != self_name:
            return alias["alias_target"]
    return None


def _resolve_alias_grades(found: dict[str, dict]) -> None:
    """Let an alias inherit the grade of the canonical file it points at.

    A thin alias/symlink should grade as the single-source-of-truth it routes
    to, not as a low-scoring standalone doc that the remediation would tell the
    user to rewrite. Mutates ``found`` in place: marks ``is_alias`` and copies
    the target's grade/score when the target is itself graded.
    """
    by_basename = {Path(rel).name.lower(): meta for rel, meta in found.items()}
    for meta in found.values():
        target = meta.pop("alias_target_basename", None)
        if not target:
            continue
        target_meta = by_basename.get(target.lower())
        meta["is_alias"] = True
        meta["alias_target"] = target
        if target_meta is not None and target_meta is not meta:
            # Inherit the canonical grade - the alias is as good as what it
            # points at, and carries no maintenance burden of its own.
            meta["grade"] = target_meta["grade"]
            meta["score"] = target_meta["score"]


def detect_ancestor_instructions(repo_root: Path) -> list[str]:
    """Detect committed-elsewhere instruction files that cascade into this repo.

    Claude Code composes ``CLAUDE.md`` from every ancestor directory plus the
    global ``~/.claude/CLAUDE.md``. So "no instruction file at the repo root" is
    not the same as "no instructions anywhere" - a clone gets none of the
    ancestor cascade, but the maintainer working in-tree does (issue #57).

    Returns REDACTED, repo-relative / ``~``-relative descriptors (never absolute
    paths - those would leak a home directory into the committed wiki). Best
    effort: any filesystem error yields an empty list.
    """
    found: list[str] = []
    try:
        repo_root = repo_root.resolve()
        # Walk parent directories above the repo root (bounded depth).
        parent = repo_root.parent
        depth = 1
        while parent != parent.parent and depth <= 6:
            for name in ("CLAUDE.md", "AGENTS.md", "GEMINI.md"):
                if (parent / name).is_file():
                    found.append(f"{name} ({depth} level(s) above repo root)")
            parent = parent.parent
            depth += 1
        # The global user instructions, if present.
        for rel in (".claude/CLAUDE.md", ".codex/AGENTS.md", ".gemini/GEMINI.md"):
            if (Path.home() / rel).is_file():
                found.append(f"~/{rel} (global user instructions)")
    except OSError:
        return []
    return found


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


# Co-location test conventions, keyed off a source file's stem + suffix.
# Each entry is a (filename-builder) applied in the file's own directory; the
# first existing match wins. Covers the dominant per-language idioms - it is a
# cheap precision heuristic, not a build-graph analysis, so a project that keeps
# all tests in a far-away mirror tree still degrades to "unknown" rather than a
# false "no".
_TEST_SIBLING_BUILDERS = [
    lambda stem, ext: f"{stem}_test{ext}",    # Go, Python (pytest co-located)
    lambda stem, ext: f"{stem}.test{ext}",    # JS/TS (jest)
    lambda stem, ext: f"{stem}.spec{ext}",    # JS/TS/Angular (jasmine/jest)
    lambda stem, ext: f"{stem}_spec{ext}",    # Ruby (rspec), some JS
    lambda stem, ext: f"test_{stem}{ext}",    # Python (unittest)
    lambda stem, ext: f"{stem}Test{ext}",     # Java/Kotlin/C# (JUnit)
    lambda stem, ext: f"{stem}Tests{ext}",    # C#/Swift (XCTest)
]
# Adjacent directories that conventionally hold co-located tests for the
# files beside them. Checked for any of the sibling-name patterns above.
_ADJACENT_TEST_DIRS = ["__tests__", "tests", "test", "spec"]
# Suffixes/stem markers that mean the file IS itself a test - it doesn't need a
# separate test file, so it counts as covered.
_IS_TEST_RE = re.compile(r"(^test_|_test$|\.test$|\.spec$|_spec$|Tests?$)")


def _has_sibling_test(repo_root: Path, rel_path: str) -> bool | None:
    """Best-effort: does this source file have a co-located test file?

    Returns True/False from a filesystem check of common co-location idioms
    (`foo.ts` next to `foo.test.ts`, `foo_test.go`, an adjacent `__tests__/`,
    etc.). Returns None only when the file isn't on disk (e.g. scanning a stats
    snapshot for a since-deleted path), which honestly maps to "unknown".
    """
    src = (repo_root / rel_path)
    if not src.is_file():
        return None
    stem, ext = src.stem, src.suffix
    if _IS_TEST_RE.search(stem):
        return True  # the file is itself a test
    directory = src.parent
    candidate_names = [build(stem, ext) for build in _TEST_SIBLING_BUILDERS]
    for name in candidate_names:
        if (directory / name).is_file():
            return True
    for sub in _ADJACENT_TEST_DIRS:
        test_dir = directory / sub
        if not test_dir.is_dir():
            continue
        for name in candidate_names + [f"{stem}{ext}"]:
            if (test_dir / name).is_file():
                return True
    return False


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

    # A diff is only trustworthy when both snapshots came from the same plugin
    # filter. When the prior snapshot predates version stamping (seeded by hand,
    # or written by an older plugin with a looser file filter), "graduated"
    # entries can be files the new filter simply excludes - phantom transitions,
    # not real improvement. Detect the mismatch and flag the diff as unreliable
    # so the report suppresses or caveats it (see SKILL.md).
    current_version = current.get("plugin_version")
    prior_version = prior.get("plugin_version") if prior else None
    diff_reliable = True
    diff_version_note = None
    if prior_exists and prior_version != current_version:
        diff_reliable = False
        diff_version_note = (
            f"prior stats from plugin {prior_version or 'unknown'}, current "
            f"{current_version or 'unknown'}; file-filter differences across "
            "versions may surface phantom graduated/new transitions"
        )

    diff = diff_stats(prior=prior, current=current)
    instruction_files, instructions_grade, untracked_instr, dangling_instr, skills_info, \
        sensitive_instr = _grade_instruction_files(repo_root)

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
        hotspot_entries.append(HotspotEntry(
            path=path,
            first_flagged=first_flagged,
            last_seen=run_date,
            status=status,
            ccn=ccn,
            loc=loc,
        ))
        write_hotspot_page(
            assess_dir,
            path=path,
            first_flagged=first_flagged,
            last_seen=run_date,
            status=status,
            loc=loc,
            ccn=ccn,
            commits=commits,
            has_tests=_has_sibling_test(repo_root, path),
            history_rows=f"| {run_date} | {loc} | {ccn} | {commits} | {status} |",
            briefing=(
                f"Hotspot ({status}). "
                f"{loc} LOC, "
                f"max cyclomatic complexity {ccn}, "
                f"{commits} commits in churn window. "
                "(Briefing refined by LLM via assess_finalize - see Suggested actions below.)"
            ),
            actions="- Pending LLM-generated suggestions",
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

    for h in diff.graduated:
        hotspot_entries.append(HotspotEntry(
            path=h.path,
            # Graduated means it was a prior hotspot; if we have no recorded
            # first-flagged date, it predates this run - "unknown", not today.
            first_flagged=first_flagged_map.get(h.path, "unknown"),
            last_seen=run_date,
            status="graduated",
            ccn=current_ccns.get(h.path),
            loc=current_locs.get(h.path),
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
        plugin_version=_read_plugin_version(),
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
        "diff_reliable": diff_reliable,
        "diff_version_note": diff_version_note,
        "prior_plugin_version": prior_version,
        "diff_detail": {
            "graduated": [h.__dict__ for h in diff.graduated],
            "regressed": [h.__dict__ for h in diff.regressed],
            "new": [h.__dict__ for h in diff.new],
            "persistent": [h.__dict__ for h in diff.persistent],
        },
    }

    # User-supplied excludes (`.assess/config.toml`) are loaded once and
    # threaded into every read-side scan so a `regulatory-raw/` directory
    # disappears uniformly from the heatmap, the doc-navigability graph,
    # the doc-staleness pass, and the liveness scan. The treemap CLI also
    # honours `--exclude` on top of these; the read-side scans are driven
    # by the orchestrator and pick up the config-only path.
    extra_exclude_dirs, extra_exclude_patterns = load_excludes(repo_root)

    # Read-side foundation signals (Layer 0 navigability + Layer 1 liveness).
    # Each is best-effort and degrades rather than blocking the assessment.
    doc_graph = _safe("doc_graph", lambda: build_doc_graph(
        repo_root,
        extra_exclude_dirs=extra_exclude_dirs,
        extra_exclude_patterns=extra_exclude_patterns,
    ).as_dict())
    doc_to_code = (doc_graph.get("doc_to_code_edges", [])
                   if doc_graph.get("available") else [])
    doc_staleness = _safe(
        "doc_staleness",
        lambda: analyze_doc_staleness(
            repo_root, doc_to_code_edges=doc_to_code,
            extra_exclude_dirs=extra_exclude_dirs,
            extra_exclude_patterns=extra_exclude_patterns,
        ),
    )
    liveness = _safe("liveness", lambda: scan_liveness(
        repo_root,
        extra_exclude_dirs=extra_exclude_dirs,
        extra_exclude_patterns=extra_exclude_patterns,
    ))
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
    # Sensitive content found in a candidate instruction file (issue #56). A
    # non-empty map means the remediation must warn + suggest redaction BEFORE
    # recommending the file be committed - acutely so for a public repo. All
    # evidence is redacted at the source (scan_sensitive_content).
    ctx["sensitive_instruction_content"] = sensitive_instr
    # Ancestor-cascade acknowledgement (issue #57): instruction files that live
    # above the repo root (or in the global user config) and cascade into the
    # working tree locally, but reach no fresh clone. Distinguishes "no
    # instructions anywhere" from "instructions exist but aren't committed here".
    ctx["ancestor_instruction_files"] = detect_ancestor_instructions(repo_root)
    # Progressive-disclosure signals (Layer 0): does the repo factor guidance
    # into on-demand skills, and is any instruction file an oversized monolith?
    # An oversized instruction file with no skills factoring carries a bloat
    # penalty (instruction_file_size[path].bloat_penalty > 0) that LOWERS its
    # grade - it scores strictly below an equivalent lean-file-plus-skills repo.
    ctx["skills_present"] = skills_info["skills_dirs_present"]
    ctx["skills_count"] = skills_info["skills_count"]
    ctx["skill_files"] = skills_info["skill_files"]
    ctx["instruction_file_size"] = {
        path: {
            "line_count": meta["subscores"].get("line_count", 0),
            "word_count": meta["subscores"].get("word_count", 0),
            "bloat_penalty": meta["subscores"].get("bloat_penalty", 0),
        }
        for path, meta in instruction_files.items()
    }
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

    # Keyhole-readiness signals (PRD 2026-05-29): the static-structure,
    # behaviour (change-coupling / containment / static-vs-historical),
    # documentation (complexity x doc-state join), understanding (human anchor /
    # intent source / authorship class), and runtime (static reachability)
    # blocks, plus the deterministic derived findings + ranked attention list.
    # Every piece degrades independently (integrate() wraps each block in a
    # catch-all), so a git-log or grimp failure in one signal emits an
    # available:false block rather than crashing the run. The commit file-sets
    # are parsed once and shared across coupling + containment.
    structure = _safe(
        "structure",
        lambda: analyze_structure(
            repo_root,
            keyhole_budget=load_structure_config(repo_root)["keyhole_budget"],
            extra_exclude_dirs=extra_exclude_dirs,
        ).as_dict(),
    )
    keyhole = integrate_keyhole_signals(
        repo_root=repo_root,
        complexity_stats=current,
        doc_staleness=doc_staleness if isinstance(doc_staleness, dict) else {},
        dead_code=ctx["dead_code"],
        observability=ctx["observability"],
        structure=structure,
    )
    ctx["structure"] = keyhole["structure"]
    ctx["behaviour"] = keyhole["behaviour"]
    ctx["documentation"] = keyhole["documentation"]
    ctx["understanding"] = keyhole["understanding"]
    ctx["runtime"] = keyhole["runtime"]
    ctx["derived_findings"] = keyhole["derived_findings"]
    ctx["attention"] = keyhole["attention"]

    # Layer 1 write-side truth pressure: does the suite pin behaviour down, or
    # merely visit it? Best-effort like every other read-side scan. opt_in=False
    # keeps the (mutating, code-running) bounded mutation pass OFF by default, so
    # /assess stays read-only and fast - the cheap hollow-test heuristics and
    # mutation-config detection still run. hot_files come from the current top
    # hotspots so an opt-in mutation run would target the files that matter most.
    hot_files = [h.get("path") for h in current.get("top_hotspots", [])
                 if h.get("path")]
    test_pressure = _safe(
        "test_pressure",
        lambda: scan_test_pressure(repo_root, hot_files=hot_files, opt_in=False),
    )
    # A failed (or malformed) scan must not read as "no mutation setup": that
    # would mis-score Layer 1 just as a failed liveness scan would mis-score
    # observability. Carry an explicit unavailable marker with a null
    # mutation_config_present and empty heuristic buckets so the LLM sees
    # "not assessed", never a false negative. Keys mirror the real block's
    # `cheap_heuristics` schema (assertion_on_internal / untested_boundaries /
    # duplicate_truth) so the consumer's shape doesn't change on the failure path.
    tp_ok = (isinstance(test_pressure, dict)
             and "mutation_config_present" in test_pressure
             and "cheap_heuristics" in test_pressure)
    ctx["test_pressure"] = test_pressure if tp_ok else {
        "available": False,
        "reason": (test_pressure.get("reason")
                   if isinstance(test_pressure, dict)
                   else "test_pressure scan unavailable"),
        "mutation_config_present": None,
        "cheap_heuristics": {
            "assertion_on_internal": [],
            "untested_boundaries": [],
            "duplicate_truth": [],
        },
    }

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
