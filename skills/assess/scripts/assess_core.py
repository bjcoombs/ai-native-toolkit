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

import argparse
import json
import re
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

# Make sibling lib package importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.accretion_ratchet import scan_accretion_ratchet
from lib.agent_instructions_grader import (
    detect_alias,
    detect_skills_dir,
    grade_instructions,
    scan_sensitive_content,
)
from lib.anomaly_detector import detect_anomalies
from lib.archetype import analyze_archetype
from lib.badge import (
    concern_count_from_findings,
    fallback_badge,
    write_badge,
)
from lib.assess_config import load_excludes, load_structure_config
from lib.coverage_report import detect_coverage_report, load_coverage_data
from lib.decline_markers import build_decline_block
from lib.interactivity import build_offers_block
from lib.doc_graph import build_doc_graph, is_repo_file
from lib.doc_staleness import analyze_doc_staleness
from lib.git_churn import git_commit_info, tracked_files
from lib.keyhole_signals import integrate as integrate_keyhole_signals
from lib.liveness_scan import scan_liveness
from lib.promissory_markers import scan_promissory_markers
from lib.structure_graph import analyze_structure
from lib.stats_diff import diff_stats, hotspot_commits, load_stats
from lib.structure_drift import (
    SEAM_ALLOWLIST,
    detect_path_existence_drift,
)
from lib.test_focus import compute_test_focus
from lib.test_pressure import scan_test_pressure
from lib.wiki_writer import (
    UNFINALIZED_ACTIONS_POINTER,
    HotspotEntry,
    LogEntry,
    append_log_entry,
    prune_orphan_hotspots,
    verify_log_chain,
    write_hotspot_page,
    write_index,
)


# Artifact schema version, stamped on every artifact the run produces
# (run-context.json, the badge, the wiki pages, complexity-stats). Distinct from
# the stats-layout `schema_version` (from #244) that versions the sidecar shape
# for diff comparability: this one versions the run_id provenance envelope.
# Bumped when the cross-artifact provenance schema changes shape in a way a
# consumer must adapt to.
ARTIFACT_SCHEMA_VERSION = "1.0.0"


def _new_run_id() -> str:
    """A unique id for this run: a sortable wall-clock stamp plus random suffix.

    ``YYYYMMDDHHMMSS-<8 hex>`` - the timestamp orders runs, the uuid suffix makes
    two runs in the same second still distinct. Stamped on every artifact so
    finalize can prove the finalize-input and run-context came from one run.
    """
    return f"{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"


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


def _parse_semver(value: str | None) -> tuple[int, int, int] | None:
    """Parse ``MAJOR.MINOR.PATCH`` (leading ``v`` and a pre-release/build
    suffix tolerated) into an int triple, or ``None`` when it isn't a semver.

    A small local parse rather than a ``packaging`` dependency: the deterministic
    core is stdlib-only by convention (its only deps are the graph libs), and the
    only comparison the diff needs is the major component and equality.
    """
    if not isinstance(value, str):
        return None
    m = re.match(r"^\s*v?(\d+)\.(\d+)\.(\d+)", value)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _diff_is_reliable(
    prior_version: str | None,
    current_version: str | None,
    prior_schema: object | None,
    current_schema: object | None,
) -> tuple[bool, str | None]:
    """Decide whether a cross-run diff can be trusted, and why not if not.

    A diff is only trustworthy when both snapshots came from a comparable
    toolchain. This gates the plugin-version and stats-schema halves of that
    (tool-backend version changes are handled by the caller, which owns the
    per-tool notes). Ordering matches the failure severity:

    - prior snapshot never stamped a version -> unreliable (can't establish
      comparability at all);
    - either version unparseable -> unreliable (can't reason about the delta);
    - stats schema changed -> unreliable (the sidecar shape the diff reads moved);
    - MAJOR version changed -> unreliable AND a trend reset (breaking change to
      the deterministic core; prior history is not comparable);
    - only MINOR/PATCH moved -> reliable, trend and gate stay armed.

    Returns ``(reliable, note)``; ``note`` is ``None`` exactly when reliable.
    """
    if not prior_version:
        return False, "version not stamped in prior snapshot"
    pv = _parse_semver(prior_version)
    cv = _parse_semver(current_version)
    if pv is None or cv is None:
        return False, (
            f"unparseable plugin version (prior {prior_version!r}, "
            f"current {current_version!r})"
        )
    if prior_schema != current_schema:
        return False, f"schema version changed {prior_schema}->{current_schema}"
    if pv[0] != cv[0]:
        return False, f"major version changed {prior_version}->{current_version}"
    return True, None


def _stats_tool_versions(stats: dict | None) -> dict[str, str]:
    """Extract the ``{tool: version}`` map a stats sidecar stamped.

    Reads the flat ``lizard_version`` / ``scc_version`` keys the treemap writes
    (absent on pre-stamping snapshots, in which case the tool is simply omitted -
    an omitted tool can't be compared, so it never forces a false reset)."""
    if not isinstance(stats, dict):
        return {}
    out: dict[str, str] = {}
    for tool in ("lizard", "scc"):
        v = stats.get(f"{tool}_version")
        if isinstance(v, str) and v:
            out[tool] = v
    return out


def _compute_diff_reliability(
    prior_exists: bool, prior: dict | None, current: dict,
) -> tuple[bool, str | None, bool]:
    """Decide whether the cross-run diff is trustworthy, returning
    ``(diff_reliable, diff_version_note, diff_trend_reset)``.

    Layers the plugin/schema check (``_diff_is_reliable``) over the tool-backend
    check (``_tool_version_change_note``): a MINOR/PATCH plugin bump keeps the
    diff armed unless a complexity backend also moved. A first run (no prior) is
    trivially reliable - there is nothing to compare, so nothing to distrust.
    """
    if not prior_exists:
        return True, None, False
    reliable, note = _diff_is_reliable(
        (prior or {}).get("plugin_version"),
        current.get("plugin_version"),
        (prior or {}).get("schema_version"),
        current.get("schema_version"),
    )
    if not reliable:
        # A MAJOR plugin bump is a breaking change to the core: the prior trend
        # history is not comparable, so the report discloses a reset.
        trend_reset = bool(note and note.startswith("major version changed"))
        return False, note, trend_reset
    # Plugin/schema are comparable; a backend version change still voids the diff
    # (and names which tool moved) so the numbers aren't read as a regression.
    tool_note = _tool_version_change_note(
        _stats_tool_versions(prior), _stats_tool_versions(current),
    )
    if tool_note is not None:
        return False, tool_note, False
    return True, None, False


def _tool_version_change_note(
    prior_tools: dict[str, str], current_tools: dict[str, str]
) -> str | None:
    """Return a note naming the first complexity backend whose version changed
    between the two snapshots, or ``None`` when every shared tool matches.

    Only a tool recorded in BOTH snapshots can be compared; a tool missing from
    the prior snapshot (older, pre-stamping) is skipped rather than treated as a
    change - that case is already caught by the plugin/schema reliability check.
    A backend version change (e.g. a lizard release) can shift cyclomatic scores
    with no change in the tree, so the diff against the prior snapshot is voided.
    """
    for tool in sorted(current_tools):
        pv = prior_tools.get(tool)
        cv = current_tools.get(tool)
        if pv is not None and cv is not None and pv != cv:
            return (
                f"{tool} version changed {pv}->{cv}; complexity scores may shift, "
                "so the diff against the prior snapshot is not comparable"
            )
    return None


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


def _write_badge(
    assess_dir: Path, promissory: Any, derived_findings: list[dict],
    run_id: str | None = None,
) -> None:
    """Write the deterministic default badge, always.

    The shipped ``badge.json`` is the deterministic findings-count form: a pure
    function of measured run data, never an LLM-authored score. It is written on
    every run and is no longer overwritten by ``assess_finalize`` - the
    LLM-derived grade lives in ``assess-report.md``, and the badge's ``link``
    funnels a badge-clicker there. ``run_id`` stamps the badge with the run that
    produced it.
    """
    stale = (
        promissory.get("total_stale", 0)
        if isinstance(promissory, dict) and promissory.get("available")
        else 0
    )
    badge = fallback_badge(
        concern_count_from_findings(derived_findings), stale, run_id=run_id,
    )
    badge["link"] = "./assess-report.md"
    write_badge(assess_dir, badge)


# Cap on accretion files carried into run-context.json. The scanner measures
# every file; the run-context list keeps only the worst few that also score in
# the top complexity/size band, so a growing-but-simple file never earns a line.
MAX_ACCRETION_FILES = 12


def _top_band_paths(complexity_stats: dict) -> set[str]:
    """Paths already in the top complexity/size band of this run's stats.

    Union of the three ranked top-N lists (``top_hotspots``/``top_complex``/
    ``top_large``). Accretion is only surfaced for a file already in this set:
    a growing file that scores low on complexity *and* size isn't a hotspot, so
    flagging its growth would be noise. Mirrors lib.keyhole_signals._paths_from_stats.
    """
    paths: set[str] = set()
    for key in ("top_hotspots", "top_complex", "top_large"):
        for entry in complexity_stats.get(key) or []:
            path = entry.get("path")
            if path:
                paths.add(path)
    return paths


def _accretion_block(scan: Any, complexity_stats: dict) -> dict[str, Any]:
    """Serialize an AccretionScan into the run-context ``accretion_ratchet`` block.

    On an unavailable scan, emits the same shape with an empty file list and the
    scan's reason (graceful degradation - a failed scan is never read as "no
    accretion"). On success, the flagged files are filtered to those already in
    the top complexity/size band (the noise budget), sorted by net additions
    descending with a path tie-break for a total, deterministic order, and capped
    at MAX_ACCRETION_FILES.
    """
    block: dict[str, Any] = {
        "available": scan.available,
        "reason": scan.reason,
        "reliable": scan.reliable,
        "deletion_fraction_threshold": scan.deletion_fraction_threshold,
        "files": [],
    }
    if not scan.available:
        return block

    band = _top_band_paths(complexity_stats)
    in_band = [f for f in scan.files if f.path in band]
    # The scan already sorts by (-net_additions, path); re-sort defensively so
    # the serialized order is a total, clone-independent order regardless of the
    # filtered subset's incoming order.
    in_band.sort(key=lambda f: (-f.net_additions, f.path))
    block["total_in_band"] = len(in_band)
    block["files"] = [f.to_dict() for f in in_band[:MAX_ACCRETION_FILES]]
    return block


def _accretion_lookup(scan: Any) -> dict[str, dict]:
    """O(1) ``{path: accretion_info}`` for the hotspot pages, from one scan.

    Each entry is the serialized AccretionFile plus the scan-wide ``reliable``
    flag, so a hotspot page can name a file's growth profile and disclaim it on
    degenerate history without re-deriving anything. Returns ``{}`` when the
    scan was unavailable - the hotspot loop then writes pages with no growth
    line (graceful degradation; the scan never gates page generation).
    """
    if not scan.available:
        return {}
    lookup: dict[str, dict] = {}
    for af in scan.files:
        entry = af.to_dict()
        entry["reliable"] = scan.reliable
        lookup[af.path] = entry
    return lookup


# The six Tier 1 grouping-disagreement counts, in a fixed order so the
# run-context tier_1 sub-block is deterministic regardless of dict iteration.
_TIER1_DISAGREEMENT_KEYS = (
    "human_grouped_static_splits",
    "human_split_static_fuses",
    "human_grouped_never_cochange",
    "human_split_but_cochange",
    "human_static_agree",
    "human_cochange_agree",
)


def _structure_drift_block(
    repo_root: Path, tier_1: dict,
) -> dict | None:
    """Build the run-context ``structure_drift`` block (Tier 0 + Tier 1).

    Tier 0 (``detect_path_existence_drift``) is the zero-threshold cut: declared
    ownership patterns matching no tracked file. It runs whenever an ownership
    map exists; when none does it degrades to ``available: False`` and the whole
    block is omitted (the caller drops a ``None``), keeping non-owned repos'
    run-context byte-stable.

    ``tier_1`` is the grouping-disagreement result ``keyhole_signals.integrate``
    already computed from the behaviour block's co-change pairs (no second
    ``git log`` parse, no double computation) - either the six disagreement
    counts or an ``available: False`` marker when the static import graph was
    unavailable or no ownership map existed. The seam allowlist was applied
    inside the detector, so the counts here are already post-allowlist.

    Returns ``None`` when Tier 0 is unavailable (no ownership map) - a graceful,
    half-block-free omission. Otherwise returns a JSON-serialisable block whose
    ``tier_0`` always carries data and whose ``tier_1`` is either the six
    disagreement counts or ``{"available": False}``.
    """
    tier_0 = detect_path_existence_drift(repo_root)
    if not tier_0.get("available"):
        return None  # no ownership map - omit the block entirely

    block: dict[str, Any] = {
        "tier_0": {
            "available": True,
            "empty_ownership_patterns": tier_0["empty_ownership_patterns"],
            "total_patterns": tier_0["total_patterns"],
            "matched_patterns": tier_0["matched_patterns"],
        },
    }

    if not tier_1.get("available"):
        block["tier_1"] = {"available": False}
        return block

    tier_1_block: dict[str, Any] = {"available": True}
    for key in _TIER1_DISAGREEMENT_KEYS:
        tier_1_block[f"{key}_count"] = tier_1.get(f"{key}_count", 0)
    tier_1_block["seam_allowlist_applied"] = True
    tier_1_block["allowlist_pairs_count"] = len(SEAM_ALLOWLIST)
    block["tier_1"] = tier_1_block
    return block


def _attach_structure_drift(
    ctx: dict[str, Any], repo_root: Path, tier_1: dict,
) -> None:
    """Attach the structure_drift block to ctx, or omit it on a graceful degrade.

    Builds the block via :func:`_structure_drift_block` (Tier 0 + the supplied
    Tier 1 result) under :func:`_safe`. Attaches only a real block (one carrying
    a ``tier_0``): the no-ownership-map path returns ``None`` and a scan failure
    returns ``_safe``'s degrade dict (no ``tier_0``); both omit the block rather
    than emit a half-block, keeping the contract that absence means "nothing to
    drift against / not assessed", never "no drift".
    """
    block = _safe(
        "structure_drift",
        lambda: _structure_drift_block(repo_root, tier_1),
    )
    if isinstance(block, dict) and "tier_0" in block:
        ctx["structure_drift"] = block


def _marker_debt_sentence(debt: dict | None) -> str:
    """One briefing sentence accusing a hotspot of its own stale promises."""
    if not debt:
        return ""
    families = ", ".join(debt["families"])
    return (
        f"Carries {debt['count']} stale promissory marker(s) "
        f"({families}; oldest survived {debt['max_survived']} edits to this file). "
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


def _normalize_test_pressure(test_pressure: Any) -> dict:
    """Normalize a ``scan_test_pressure`` result into the run-context block shape.

    A failed (or malformed) scan must not read as "no mutation setup": that
    would mis-score Layer 1 just as a failed liveness scan would mis-score
    observability. On a bad result, carry an explicit unavailable marker with a
    null ``mutation_config_present`` and empty heuristic buckets so the LLM sees
    "not assessed", never a false negative. Keys mirror the real block's
    ``cheap_heuristics`` schema (assertion_on_internal / untested_boundaries /
    duplicate_truth) so the consumer's shape doesn't change on the failure path.

    Shared by the default read-only scan (``build_run_context``) and the opt-in
    mutation re-run (``run_opt_in_mutation``) so both write an identical shape.
    """
    tp_ok = (isinstance(test_pressure, dict)
             and "mutation_config_present" in test_pressure
             and "cheap_heuristics" in test_pressure)
    if tp_ok:
        return test_pressure
    return {
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


# The annotation the LLM must attach to Layer 6 when mutation testing never
# ran. Layer 6 (truth pressure) asks whether the suite *proves* behaviour, not
# merely visits it - a claim only a mutation run can substantiate. Absent that
# run, the strongest honest verdict is Partial; a Present claim would be an
# unproven self-description, exactly the guardrail-erosion failure /assess
# exists to catch. assess_finalize enforces the cap deterministically.
MUTATION_NOT_RUN_ANNOTATION = "truth-pressure unproven (mutation not run)"


def _mutation_not_run_cap(test_pressure_block: dict) -> dict:
    """The Layer 6 cap the LLM reads: does mutation evidence exist this run?

    ``mutation_run`` is True only when the (opt-in, code-executing) bounded
    mutation pass actually ran - coverage-config detection alone leaves it
    False. When it is False, Layer 6 cannot be scored above Partial and the
    ``annotation`` must be attached; assess_finalize rejects a finalize-input
    that violates this.
    """
    mutation_run = bool(
        isinstance(test_pressure_block, dict)
        and test_pressure_block.get("mutation_run", False)
    )
    return {
        "applies": not mutation_run,
        "mutation_run": mutation_run,
        "max_layer6_band": "Present" if mutation_run else "Partial",
        "annotation": None if mutation_run else MUTATION_NOT_RUN_ANNOTATION,
    }


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


def build_run_context(
    *, repo_root: Path, run_date: str, non_interactive: bool = False
) -> dict:
    """Run the deterministic pipeline and return the structured context dict.

    ``non_interactive`` is the orchestrator's explicit headless/CI signal; it
    (together with the ``CI`` / ``ASSESS_NON_INTERACTIVE`` env vars) decides the
    ``interactive`` flag and the pre-recorded ``offers``. It is never inferred
    from ``sys.stdin.isatty()`` - the core always runs as a subprocess with no
    controlling terminal, so an interactive /assess would misread as headless.

    Side effects: writes index.md, log.md, hotspots/*.md, run-context.json.
    """
    assess_dir = repo_root / ".assess"
    assess_dir.mkdir(parents=True, exist_ok=True)
    # Unique id minted once at the top of the run and stamped on every artifact
    # this build produces, so finalize can prove the finalize-input it later
    # consumes was authored against *this* run-context and not a stale one.
    run_id = _new_run_id()
    current = load_stats(assess_dir / "complexity-stats.json") or {
        "files_scored": 0, "top_hotspots": [], "top_complex": [], "top_large": [],
        "loc": {}, "ccn": {},
    }
    prior = load_stats(assess_dir / "complexity-stats.prior.json")
    prior_exists = prior is not None

    # A diff is only trustworthy when both snapshots came from a comparable
    # toolchain. Three things can void it, in descending severity, all schema-
    # and version-aware (not a blunt exact-string equality):
    #   1. the plugin's stats schema or MAJOR version changed - the sidecar shape
    #      or the deterministic core moved (major also resets the trend);
    #   2. a complexity backend (lizard/scc) version changed - scores can shift
    #      with no change in the tree, so the note names the tool;
    #   3. the prior snapshot never stamped a version - comparability can't be
    #      established, so "graduated" entries may be phantom filter transitions.
    # A mere MINOR/PATCH plugin bump keeps the diff reliable and the gate armed.
    prior_version = prior.get("plugin_version") if prior else None
    current_schema = current.get("schema_version")
    prior_schema = prior.get("schema_version") if prior else None
    current_tools = _stats_tool_versions(current)
    prior_tools = _stats_tool_versions(prior)
    diff_reliable, diff_version_note, diff_trend_reset = _compute_diff_reliability(
        prior_exists, prior, current,
    )

    diff = diff_stats(prior=prior, current=current)
    instruction_files, instructions_grade, untracked_instr, dangling_instr, skills_info, \
        sensitive_instr = _grade_instruction_files(repo_root)

    # Load (and later update) the persistent first-flagged date map
    first_flagged_map = _load_first_flagged(assess_dir)

    # User-supplied excludes (`.assess/config.toml`), loaded once and threaded
    # into every read-side scan (heatmap parity, doc graph, staleness, liveness,
    # markers) so "this is reference data, not source" is a single statement.
    extra_exclude_dirs, extra_exclude_patterns = load_excludes(repo_root)

    # Promissory markers (stale TODO/FIXME, suppressions, disabled tests),
    # scanned before the wiki pages so each hotspot page can carry its own
    # marker debt. summary() shape on success; _safe's degrade dict on failure
    # (both carry `available`).
    promissory = _safe(
        "promissory_markers",
        lambda: scan_promissory_markers(
            repo_root,
            extra_exclude_dirs=extra_exclude_dirs,
            extra_exclude_patterns=extra_exclude_patterns,
        ).summary(),
    )
    marker_debt_by_file = (
        promissory.get("stale_by_file", {}) if promissory.get("available") else {}
    )

    # Accretion ratchet (files whose line count only ever grows): the first of
    # the three write-side tendencies. scan_accretion_ratchet never raises - it
    # degrades to available=False internally - and returns an AccretionScan
    # dataclass (not a dict), so it is called directly rather than through _safe
    # (whose degrade path yields a dict). The block is serialized below, after
    # the complexity band is known, so growth is reported only for files already
    # in the top complexity/size band.
    accretion_scan = scan_accretion_ratchet(repo_root)
    # O(1) per-file lookup for the hotspot pages, built from the same scan the
    # run-context block serializes (no re-scan). Empty when the scan was
    # unavailable - graceful degradation: those files just get no growth line.
    accretion_by_file = _accretion_lookup(accretion_scan)

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
                + _marker_debt_sentence(marker_debt_by_file.get(path))
                + "(Briefing refined by LLM via assess_finalize - see Suggested actions below.)"
            ),
            actions=UNFINALIZED_ACTIONS_POINTER,
            accretion_data=accretion_by_file.get(path),
            run_id=run_id,
            schema_version=ARTIFACT_SCHEMA_VERSION,
        )

    # Prune orphan hotspot pages: any page from a prior run whose source file no
    # longer exists on disk is stamped retired (history preserved) so no active
    # page keeps describing a deleted file. Runs after the current top hotspots
    # are (re)written, so a file that is still a live hotspot has just had its
    # page refreshed and won't be touched.
    pruned_hotspots = prune_orphan_hotspots(assess_dir, repo_root)

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

    write_index(
        assess_dir, hotspot_entries, last_updated=run_date,
        run_id=run_id, schema_version=ARTIFACT_SCHEMA_VERSION,
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
        new_count=len(diff.new),
        persistent_count=len(diff.persistent),
        top_action=top_action,
        plugin_version=_read_plugin_version(),
        run_id=run_id,
        schema_version=ARTIFACT_SCHEMA_VERSION,
    )
    append_log_entry(assess_dir, log_entry)

    # log.md integrity: verify the chained checksums after the append. A break
    # (an earlier entry edited after the fact) is disclosed in log.md itself and
    # surfaced here so the report/gate can render it - a lying history is exactly
    # the self-description-under-no-pressure failure the toolkit guards against.
    log_valid, log_broken_at = verify_log_chain(assess_dir)

    # Heterogeneous run-context bus: values are dicts, lists, scalars, or the
    # degrade-gracefully bool/str/None fallbacks. Typed as dict[str, Any] so the
    # block accessors below (ctx["dead_code"] etc.) stay assignable to the
    # signal functions that consume them.
    ctx: dict[str, Any] = {
        # Run provenance: the unique id every artifact of this run carries, and
        # the artifact schema version a consumer checks before reading. finalize
        # refuses to reconcile a finalize-input whose run_id disagrees (a torn
        # write). `artifact_schema_version` is distinct from the stats-layout
        # `schema_version` set further below (from #244).
        "run_id": run_id,
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "run_date": run_date,
        # The commit the scan measured. Absolute LOC/CCN figures are a snapshot
        # of this commit; the report pins the SHA and warns when HEAD is dirty
        # or behind its upstream so the numbers aren't read as current (#59).
        "measured_commit": git_commit_info(repo_root),
        "prior_stats_exists": prior_exists,
        "stats_summary": {
            "files_scored": current.get("files_scored", 0),
            "loc": current.get("loc", {}),
            # Estimated tokens (the keyhole size unit) + the budget rollup: repo
            # total and how many files / top-level subtrees exceed one
            # context-window keyhole. The most on-thesis snapshot signal - the
            # literal "does the relevant slice fit?" measure. Empty {} on a
            # pre-token stats snapshot (back-compat).
            "est_tokens": current.get("est_tokens", {}),
            "ccn": current.get("ccn", {}),
            "top_hotspots": current.get("top_hotspots", []),
        },
        "instruction_files": instruction_files,
        "instructions_grade": instructions_grade,
        "diff": diff.summary(),
        "diff_reliable": diff_reliable,
        "diff_version_note": diff_version_note,
        # True only when a MAJOR plugin bump reset the trend baseline; the report
        # renders an explicit disclosure line so a suppressed diff isn't read as
        # "nothing changed".
        "diff_trend_reset": diff_trend_reset,
        "prior_plugin_version": prior_version,
        # Toolchain the snapshot was produced with, surfaced so the report/gate
        # can reason about comparability. The stats schema version plus the
        # complexity backends and their captured versions (this run and prior).
        "schema_version": current_schema,
        "prior_schema_version": prior_schema,
        "tool_versions": current_tools,
        "prior_tool_versions": prior_tools,
        "diff_detail": {
            "graduated": [h.__dict__ for h in diff.graduated],
            "regressed": [h.__dict__ for h in diff.regressed],
            "new": [h.__dict__ for h in diff.new],
            "persistent": [h.__dict__ for h in diff.persistent],
        },
        # Hotspot pages retired this run because their source file left the tree
        # (task 9). Empty on a run that deleted nothing - a stable baseline.
        "pruned_hotspots": pruned_hotspots,
        # log.md integrity chain state (task 11). `valid` is False when an earlier
        # log entry was edited after it was written; `broken_at_entry` is the
        # 1-based index of the first entry that fails verification (None when
        # intact). The break is also disclosed in log.md itself.
        "log_integrity": {"valid": log_valid, "broken_at_entry": log_broken_at},
    }

    # extra_exclude_dirs / extra_exclude_patterns were loaded once above
    # (before the marker scan + wiki pages) and apply uniformly to every
    # read-side scan below. The treemap CLI also honours `--exclude` on top.

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
    # Churn-measurement reliability, surfaced to the report layer so the score
    # line can carry a "snapshot / no usable history" caveat. True when the git
    # history is degenerate - every file ~1 commit (shallow clone, fresh import,
    # squashed/extracted tree) - in which case churn-derived findings are
    # discounted (confidence capped, lying_map / hidden_coupling not counted) and
    # the treemap saturation axis is inactive. Single source of truth: the
    # doc-staleness block (lib.git_churn.churn_is_degenerate).
    ctx["churn_degenerate"] = bool(
        doc_staleness.get("churn_degenerate", False)
        if isinstance(doc_staleness, dict) else False
    )
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
    # Repository archetype (issue #224): is this a software repo or a
    # knowledge/document base? A knowledge base has no code surface for the
    # write-side layers (L2-L7), so they are marked N/A and excluded from the
    # denominator rather than scored Missing. The block also carries the
    # Karpathy LLM-wiki maintenance signal (a read-side / Layer 0 quality
    # signal) and the gist pointer. Degrades to available=False on error.
    ctx["archetype"] = _safe("archetype", lambda: analyze_archetype(repo_root))
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
    # Capability-driven JVM offers (issue #113): present only when a Maven/Gradle
    # project is detected, so non-JVM repos carry no extra key (the run-context
    # baseline stays stable). Names each unserved capability + a candidate tool
    # (honest-degrade) and the liveness run/install-consent offer the Step 2
    # offer-layer turns into an AskUserQuestion.
    if liveness_ok and isinstance(liveness.get("jvm_capabilities"), dict):
        ctx["capability_offers"] = liveness["jvm_capabilities"]

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

    # Layer 1 write-side truth pressure: does the suite pin behaviour down, or
    # merely visit it? Best-effort like every other read-side scan. opt_in=False
    # keeps the (mutating, code-running) bounded mutation pass OFF by default, so
    # /assess stays read-only and fast - the cheap hollow-test heuristics and
    # mutation-config detection still run. hot_files come from the current top
    # hotspots so an opt-in mutation run would target the files that matter most.
    # Scanned before the keyhole integrate so the E1 trust axis can cross the
    # mutation survivor density with the complexity hotspots.
    hot_files = [h.get("path") for h in current.get("top_hotspots", [])
                 if h.get("path")]
    # Pull line-coverage truth from a report the project already generated (CI or
    # local) - /assess never runs the suite, so an existing coverage.xml / lcov.info
    # is the only honest source. Absent or malformed -> None, and the scan reports
    # "not assessed" rather than guessing. Provenance is recorded separately so the
    # report can distinguish a real read from "none found".
    cov_detect = detect_coverage_report(repo_root)
    coverage_data = load_coverage_data(repo_root)
    ctx["coverage_report"] = (
        {
            "available": True,
            "source": cov_detect["source"],
            "format": cov_detect["format"],
            "parsed": coverage_data is not None,
        }
        if cov_detect
        else {"available": False, "source": "none found"}
    )
    test_pressure = _safe(
        "test_pressure",
        lambda: scan_test_pressure(repo_root, hot_files=hot_files, opt_in=False,
                                   coverage_data=coverage_data),
    )
    ctx["test_pressure"] = _normalize_test_pressure(test_pressure)
    # Layer 6 cap: on the default read-only pass the mutation tier never runs, so
    # this reads "applies: true" and carries the required annotation. The LLM
    # reads it when scoring Layer 6; assess_finalize enforces it.
    ctx["mutation_not_run_cap"] = _mutation_not_run_cap(ctx["test_pressure"])

    # Cross-join the three already-collected signals - hotspot risk band, parsed
    # coverage, hollow-test heuristics - into one ranked focus list answering
    # "which risky files most need test work, and which kind?". Pure composition
    # of values already in hand (the top hotspots, the coverage_data loaded above,
    # and this block's cheap_heuristics), so it cannot fail the scan. This block is
    # the single source the report table and the mutation offer both consume.
    ctx["test_focus"] = compute_test_focus(
        current.get("top_hotspots", []),
        coverage_data,
        ctx["test_pressure"].get("cheap_heuristics"),
    )

    # Promissory markers (stale TODO/FIXME, suppressions, disabled tests):
    # the write-side erosion instrument. Family totals + stale counts feed the
    # Layer 3/5/8 scoring rules; stale_by_file feeds the unactioned_intent
    # finding and the hotspot pages (already written above with marker debt).
    ctx["promissory_markers"] = promissory

    # Accretion ratchet (write-side tendency: files that only ever grow). The
    # scan measured every file above; here it is filtered to files already in the
    # top complexity/size band, sorted worst-first, and capped - so a file earns
    # a line only by scoring high on complexity/LOC *and* growing monotonically.
    ctx["accretion_ratchet"] = _accretion_block(accretion_scan, current)

    keyhole = integrate_keyhole_signals(
        repo_root=repo_root,
        complexity_stats=current,
        doc_staleness=doc_staleness if isinstance(doc_staleness, dict) else {},
        dead_code=ctx["dead_code"],
        observability=ctx["observability"],
        structure=structure,
        test_pressure=ctx["test_pressure"],
        promissory_markers=promissory if isinstance(promissory, dict) else None,
        accretion_ratchet=ctx["accretion_ratchet"],
        archetype=ctx["archetype"] if isinstance(ctx.get("archetype"), dict) else None,
        exclude_dirs=extra_exclude_dirs,
        exclude_patterns=extra_exclude_patterns,
    )
    ctx["structure"] = keyhole["structure"]
    ctx["behaviour"] = keyhole["behaviour"]
    ctx["documentation"] = keyhole["documentation"]
    ctx["understanding"] = keyhole["understanding"]
    ctx["runtime"] = keyhole["runtime"]
    ctx["derived_findings"] = keyhole["derived_findings"]
    ctx["attention"] = keyhole["attention"]
    # Deterministic report-skeleton products (assess-dogfooded Part 1): the
    # pre-rendered findings section the LLM copies verbatim, the keyhole
    # readiness summary reported alongside (never merged into) the 0-8 score, and
    # the mandatory attention-derived Top-3 actions.
    ctx["findings_markdown"] = keyhole["findings_markdown"]
    ctx["keyhole_summary"] = keyhole["keyhole_summary"]
    ctx["prescribed_actions"] = keyhole["prescribed_actions"]
    # Config-exclusion disclosure: config excludes silently drop paths from every
    # scan, so a finding suppressed by an exclude must be counted and named rather
    # than vanish. keyhole_signals filtered the excluded finding paths; this block
    # records the active excludes alongside the paths that would otherwise have
    # been findings, so the gate and report can surface the suppression.
    excluded_finding_paths = keyhole.get("excluded_finding_paths", [])
    ctx["excluded_by_config"] = {
        "dirs": sorted(extra_exclude_dirs),
        "patterns": list(extra_exclude_patterns),
        "affected_finding_paths": excluded_finding_paths,
        "count": len(excluded_finding_paths),
    }

    # Structure drift (third write-side tendency surface: a declared ownership
    # map that no longer matches where the code lives). Tier 0 is the cheap
    # path-existence cut; Tier 1 the grouping-disagreement cut keyhole_signals
    # already computed from the behaviour block's co-change pairs (no second
    # git-log parse). The block is omitted entirely when no ownership map exists
    # - a graceful, half-block-free degrade that keeps a non-owned repo's
    # run-context byte-stable - and its Tier 1 hidden-seam already flows into the
    # report's B3 attention list via keyhole_signals; this block is the record.
    _attach_structure_drift(ctx, repo_root, keyhole["structure_drift_tier1"])

    _write_badge(
        assess_dir, promissory, ctx["derived_findings"], run_id=run_id
    )

    ctx["plugin_version"] = _read_plugin_version()

    # Decline markers (.assess/.no-<tool>): active permanent declines of the
    # optional tools (scc, dead-code linters, the bounded mutation pass). The
    # report discloses each so a silenced capability is never invisible, and a
    # marker written under an older major sets reoffer_mutation so SKILL.md can
    # re-ask once. Legacy empty/non-JSON markers are honoured without provenance.
    decline = build_decline_block(assess_dir, ctx["plugin_version"])
    ctx["decline_markers"] = decline["markers"]
    ctx["reoffer_mutation"] = decline["reoffer_mutation"]
    ctx["decline_disclosures"] = decline["disclosures"]

    # Non-interactive contract: in a headless/CI run no human can answer an
    # offer, so every offer is pre-recorded as skipped and the orchestrator must
    # make zero interactive prompts. Interactive runs leave `offers` empty for
    # the orchestrator to drive live (SKILL.md's three-phase consent flow). The
    # signal is explicit (the orchestrator's --non-interactive flag / CI env),
    # never inferred from subprocess stdin.
    offers_block = build_offers_block(non_interactive=non_interactive)
    ctx["interactive"] = offers_block["interactive"]
    ctx["offers"] = offers_block["offers"]

    # Where the end-of-run uninstall guide lives, relative to the assess skill
    # directory (resolved by the orchestrator via $SKILL_DIR). A machine-stable
    # pointer so an agent can Read the removal steps without hunting for them.
    ctx["uninstall_instructions_path"] = "references/uninstall.md"

    ctx["anomalies"] = [
        {"code": a.code, "description": a.description, "detail": a.detail}
        for a in detect_anomalies(ctx)
    ]
    (assess_dir / "run-context.json").write_text(json.dumps(ctx, indent=2), encoding="utf-8")
    return ctx


def run_opt_in_mutation(repo_root: Path) -> int:
    """Re-run the Layer-1 test-pressure scan with the bounded mutation pass ON.

    The consent-gated counterpart to the default read-only scan in
    ``build_run_context``. The orchestrator (SKILL.md Step 2d) calls this only
    after the user accepts the mutation offer - it mutates and *runs* code, so it
    is never part of the default pass. It does not recompute the whole context:
    it reads the existing ``run-context.json``, takes the focus targets from the
    ``test_focus`` block (the single source of focus files), runs
    ``scan_test_pressure(..., opt_in=True)`` scoped to them, and rewrites only the
    ``test_pressure`` block in place. ``run_bounded_mutation`` itself caps the
    scope at ``MAX_FILES_TO_MUTATE``, so passing every focus path is safe.

    The treemap overlay (regenerated separately by the orchestrator) then reads
    the refreshed ``test_pressure.per_file`` so covered-but-unpinned files get
    hatched. Never raises beyond argparse/IO; degrades to a non-zero return.
    """
    assess_dir = repo_root / ".assess"
    ctx_path = assess_dir / "run-context.json"
    if not ctx_path.exists():
        print("run-context.json not found - run assess_core.py first",
              file=sys.stderr)
        return 1
    try:
        ctx = json.loads(ctx_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"could not read run-context.json: {e}", file=sys.stderr)
        return 1

    focus = ctx.get("test_focus") or {}
    entries = focus.get("entries") or []
    focus_files = [e.get("path") for e in entries
                   if isinstance(e, dict) and e.get("path")]
    if not focus_files:
        print("no test_focus targets - nothing to mutate", file=sys.stderr)
        return 0

    coverage_data = load_coverage_data(repo_root)
    test_pressure = _safe(
        "test_pressure",
        lambda: scan_test_pressure(repo_root, hot_files=focus_files, opt_in=True,
                                   coverage_data=coverage_data),
    )
    ctx["test_pressure"] = _normalize_test_pressure(test_pressure)
    # Refresh the Layer 6 cap: the mutation tier may have run this pass, lifting
    # the ceiling to Present. Written back so a subsequent finalize sees it.
    ctx["mutation_not_run_cap"] = _mutation_not_run_cap(ctx["test_pressure"])
    ctx_path.write_text(json.dumps(ctx, indent=2), encoding="utf-8")

    tp = ctx["test_pressure"]
    print(json.dumps({
        "mutation_run": tp.get("mutation_run", False),
        "mutation_scope": tp.get("mutation_scope", []),
        "mutation_note": tp.get("mutation_note"),
    }))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic core for /assess; writes run-context.json.",
    )
    parser.add_argument("repo_root", help="Path to the repo root to assess.")
    parser.add_argument(
        "--opt-in-mutation",
        action="store_true",
        help=(
            "Re-run only the Layer-1 test-pressure scan with the bounded "
            "mutation pass enabled, scoped to the existing run-context.json "
            "test_focus targets, and rewrite the test_pressure block in place. "
            "Requires a prior default run. Mutates and runs code - consent-gated."
        ),
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help=(
            "Mark this a headless/CI run: no human can answer, so every consent "
            "offer is pre-recorded as skipped and the orchestrator makes zero "
            "prompts. Set this (or the ASSESS_NON_INTERACTIVE / CI env vars) only "
            "on a genuinely headless path; a normal interactive /assess omits it. "
            "Interactivity is never inferred from subprocess stdin."
        ),
    )
    parsed = parser.parse_args(argv)
    repo_root = Path(parsed.repo_root).resolve()
    if parsed.opt_in_mutation:
        return run_opt_in_mutation(repo_root)
    run_date = datetime.now().strftime("%Y-%m-%d")
    ctx = build_run_context(
        repo_root=repo_root, run_date=run_date,
        non_interactive=parsed.non_interactive,
    )
    print(json.dumps(ctx["diff"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
