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
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

# Make sibling lib package importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.accretion_ratchet import scan_accretion_ratchet
from lib.anomaly_detector import detect_anomalies
from lib.archetype import analyze_archetype
from lib.artifact_schema import ARTIFACT_SCHEMA_VERSION
from lib.badge import write_findings_badge
from lib.assess_config import load_excludes, load_structure_config, load_working_notes_config
from lib.change_coupling import build_rename_map
from lib.context_blocks import (
    accretion_block,
    accretion_lookup,
    attach_exclusion_disclosures,
    attach_keyhole_blocks,
    attach_liveness_blocks,
    attach_structure_drift,
    build_stale_hubs,
    coverage_report_block,
    dict_or,
    doc_to_code_edges,
    excluded_generated,
    generated_exclusion,
)
from lib.coverage_gate import detect_coverage_gate
from lib.coverage_report import detect_coverage_report, load_coverage_data
from lib.decline_markers import build_decline_block
from lib.diff_reliability import compute_diff_reliability, prior_stamps, stats_tool_versions
from lib.interactivity import build_offers_block
from lib.doc_graph import build_doc_graph
from lib.gap_actions import build_gap_actions
from lib.doc_staleness import analyze_doc_staleness
from lib.git_churn import git_commit_info
from lib.instruction_files import (
    broken_instruction_refs,
    detect_ancestor_instructions,
    grade_instruction_files,
    instruction_file_size,
)
from lib.keyhole_signals import integrate as integrate_keyhole_signals
from lib.liveness_scan import scan_liveness
from lib.mutation_cap import mutation_not_run_cap, normalize_test_pressure
from lib.mutation_refresh import recorded_excludes, refresh_mutation_findings
from lib.promissory_markers import scan_promissory_markers
from lib.run_scope import assess_dir_for, load_current_stats, resolve_scope
from lib.run_wiki import write_run_wiki
from lib.scan_registry import STAGE_POST_OFFERS, STAGE_READ_SIDE, run_scans
from lib.scan_registry import safe as _safe
from lib.structure_graph import analyze_structure
from lib.stats_diff import diff_stats, load_stats
from lib.test_focus import compute_test_focus, mutation_scope
from lib.test_pressure import scan_test_pressure
from lib.wiki_state import (
    excluded_after_unfinalized_run,
    load_first_flagged,
    rekey_first_flagged,
    same_measurement_prior_run,
)


def _new_run_id() -> str:
    """A unique id for this run: a sortable wall-clock stamp plus random suffix.

    ``YYYYMMDDHHMMSS-<8 hex>`` - the timestamp orders runs, the uuid suffix makes
    two runs in the same second still distinct. Stamped on every artifact so
    finalize can prove the finalize-input and run-context came from one run.
    """
    return f"{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"


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




def build_run_context(
    *, repo_root: Path, run_date: str, non_interactive: bool = False,
    scope: Path | None = None,
) -> dict[str, Any]:
    """Run the deterministic pipeline and return the structured context dict.

    ``non_interactive`` is the orchestrator's explicit headless/CI signal; it
    (together with the ``CI`` / ``ASSESS_NON_INTERACTIVE`` env vars) decides the
    ``interactive`` flag and the pre-recorded ``offers``. It is never inferred
    from ``sys.stdin.isatty()`` - the core always runs as a subprocess with no
    controlling terminal, so an interactive /assess would misread as headless.

    ``scope`` restricts the assessment to a subtree (``/assess <path>`` monorepo
    scoping): artifacts land under ``.assess/<slug>/`` and the file-enumerating
    scans (complexity stats read from the scoped sidecar, doc graph, doc
    staleness) see only the subtree, so the score/badge/wiki carry no signal
    from a sibling directory. None (the default) is a whole-repo run, unchanged.

    Side effects: writes index.md, log.md, hotspots/*.md, run-context.json.
    """
    scope_abs, scope_rel, scope_slug = resolve_scope(repo_root, scope)
    assess_dir = assess_dir_for(repo_root, scope_slug)
    assess_dir.mkdir(parents=True, exist_ok=True)
    # Unique id minted once at the top of the run and stamped on every artifact
    # this build produces, so finalize can prove the finalize-input it later
    # consumes was authored against *this* run-context and not a stale one.
    run_id = _new_run_id()
    current = load_current_stats(assess_dir)
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
    prior_version, prior_schema = prior_stamps(prior)
    current_schema = current.get("schema_version")
    current_tools = stats_tool_versions(current)
    prior_tools = stats_tool_versions(prior)
    diff_reliable, diff_version_note, diff_trend_reset = compute_diff_reliability(
        prior_exists, prior, current,
    )

    diff = diff_stats(prior=prior, current=current)
    # A hotspot that left the ranking because this run excluded it as generated
    # did not graduate: the filter changed, not the file. Drop it from the
    # graduated list so the append-only log and index never record it as one.
    # Content excludes are named in excluded_generated; the generated-name
    # globs are silent, so they are matched here directly.
    excluded_as_generated = generated_exclusion(current)
    diff.graduated = [h for h in diff.graduated if not excluded_as_generated(h.path)]
    instruction_files, instructions_grade, untracked_instr, dangling_instr, skills_info, \
        sensitive_instr = grade_instruction_files(repo_root)

    # Historical path -> current path, from git's rename detection. Built once:
    # it re-keys the first-flagged map here and folds co-change history onto
    # current paths in the keyhole integrate below.
    rename_map = build_rename_map(repo_root)

    # Load (and later update) the persistent first-flagged date map, with any
    # entry for a renamed file moved to its current path.
    first_flagged_map = rekey_first_flagged(
        load_first_flagged(assess_dir), rename_map.paths)

    # User-supplied excludes (`.assess/config.toml`), loaded once and threaded
    # into every read-side scan (heatmap parity, doc graph, staleness, liveness,
    # markers) so "this is reference data, not source" is a single statement.
    extra_exclude_dirs, extra_exclude_patterns = load_excludes(repo_root)

    # Excluded after an unfinalized run (#356): a file first flagged only by the
    # never-finalized run this one supersedes, and now excluded by config, was
    # never part of a finished assessment. Its page is retired, its first-flagged
    # entry dropped, and it is kept out of "graduated" so the rotated prior stats
    # do not carry it into index.md. Files first flagged by a finalized run are
    # outside the rule, as are files still in the current top hotspots.
    measured_commit = git_commit_info(repo_root)
    superseded = same_measurement_prior_run(
        assess_dir, run_date=run_date, measured_commit=measured_commit,
    )
    provisional, excluded_unfinalized = excluded_after_unfinalized_run(
        assess_dir, superseded=superseded, first_flagged_map=first_flagged_map,
        current=current, diff=diff,
        excludes=(extra_exclude_dirs, extra_exclude_patterns),
    )

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
            scope=scope_abs,
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
    accretion_by_file = accretion_lookup(accretion_scan)

    # Wiki: hotspot pages for the current top hotspots, index.md and this run's
    # log.md entry, with the log's checksum chain verified after the append.
    wiki = write_run_wiki(
        current, assess_dir=assess_dir, repo_root=repo_root, run_date=run_date,
        run_id=run_id, diff=diff, first_flagged_map=first_flagged_map,
        superseded=superseded, marker_debt_by_file=marker_debt_by_file,
        accretion_by_file=accretion_by_file,
        excluded_unfinalized=excluded_unfinalized,
        excluded_as_generated=excluded_as_generated, scope_rel=scope_rel,
        instructions_grade=instructions_grade,
        plugin_version=_read_plugin_version(),
    )

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
        # Monorepo scope (`/assess <path>`): the repo-relative subtree this run
        # covers, and its artifact-directory slug (.assess/<slug>/). Both null /
        # "" for a whole-repo run, so a consumer that ignores them sees the
        # pre-scope shape. The report, badge, and wiki label the run with these.
        "scope": scope_rel,
        "scope_slug": scope_slug or None,
        # The commit the scan measured. Absolute LOC/CCN figures are a snapshot
        # of this commit; the report pins the SHA and warns when HEAD is dirty
        # or behind its upstream so the numbers aren't read as current (#59).
        "measured_commit": measured_commit,
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
            "restructured": [h.__dict__ for h in diff.restructured],
            "new": [h.__dict__ for h in diff.new],
            "persistent": [h.__dict__ for h in diff.persistent],
        },
        # Hotspot pages retired this run because their source file left the tree
        # (task 9). Empty on a run that deleted nothing - a stable baseline.
        "pruned_hotspots": wiki.pruned_hotspots,
        # Pages retired this run because the file was first flagged only by a
        # never-finalized run and is now excluded by config (#356); the
        # first-flagged.json entries dropped for the same reason (a superset
        # when such a file has no page); and the paths whose first-flagged date
        # still rests on an unfinalized run (this one until it is finalized),
        # read back by the next superseding run.
        "retired_excluded_hotspots": wiki.retired_excluded,
        "dropped_first_flagged": wiki.dropped_first_flagged,
        "provisional_first_flagged": sorted(provisional),
        # log.md integrity chain state (task 11). `valid` is False when an earlier
        # log entry was edited after it was written; `broken_at_entry` is the
        # 1-based index of the first entry that fails verification (None when
        # intact). The break is also disclosed in log.md itself.
        "log_integrity": {
            "valid": wiki.log_valid, "broken_at_entry": wiki.log_broken_at,
        },
    }

    # extra_exclude_dirs / extra_exclude_patterns were loaded once above
    # (before the marker scan + wiki pages) and apply uniformly to every
    # read-side scan below. The treemap CLI also honours `--exclude` on top.

    # Read-side foundation signals (Layer 0 navigability + Layer 1 liveness).
    # Each is best-effort and degrades rather than blocking the assessment.
    working_notes = load_working_notes_config(repo_root)
    doc_graph = _safe("doc_graph", lambda: build_doc_graph(
        repo_root,
        extra_exclude_dirs=extra_exclude_dirs,
        extra_exclude_patterns=extra_exclude_patterns,
        scope=scope_abs,
        working_notes_dirs=working_notes.dirs,
        working_notes_ignore=working_notes.ignore,
    ).as_dict())
    doc_to_code = doc_to_code_edges(doc_graph)
    doc_staleness = _safe(
        "doc_staleness",
        lambda: analyze_doc_staleness(
            repo_root, doc_to_code_edges=doc_to_code,
            extra_exclude_dirs=extra_exclude_dirs,
            extra_exclude_patterns=extra_exclude_patterns,
            scope=scope_abs,
        ),
    )
    liveness = _safe("liveness", lambda: scan_liveness(
        repo_root,
        extra_exclude_dirs=extra_exclude_dirs,
        extra_exclude_patterns=extra_exclude_patterns,
        scope=scope_abs,
    ))
    ctx["doc_graph"] = doc_graph
    ctx["doc_staleness"] = doc_staleness
    ctx["stale_hubs"] = build_stale_hubs(doc_graph, doc_staleness)
    # Churn-measurement reliability, surfaced to the report layer so the score
    # line can carry a "snapshot / no usable history" caveat. True when the git
    # history is degenerate - every file ~1 commit (shallow clone, fresh import,
    # squashed/extracted tree) - in which case churn-derived findings are
    # discounted (confidence capped, lying_map / hidden_coupling not counted) and
    # the treemap saturation axis is inactive. Single source of truth: the
    # doc-staleness block (lib.git_churn.churn_is_degenerate).
    ctx["churn_degenerate"] = bool(
        dict_or(doc_staleness, {}).get("churn_degenerate", False))
    # Instruction-surface integrity (Layer 0): files present on disk but not
    # committed, and advertised-but-broken instruction references (dangling
    # symlinks + entry docs linking a missing instruction file). A broken
    # instruction reference must penalise L0 even when one unrelated file grades
    # well - see the Layer 0 scoring rule in SKILL.md.
    ctx["untracked_instruction_files"] = untracked_instr
    ctx["broken_instruction_refs"] = broken_instruction_refs(doc_graph, dangling_instr)
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
    # An instruction file past the size curve carries a bloat penalty
    # (instruction_file_size[path].bloat_penalty > 0) that LOWERS its grade;
    # skills factoring halves it and never waives it.
    ctx["skills_present"] = skills_info["skills_dirs_present"]
    ctx["skills_count"] = skills_info["skills_count"]
    ctx["skill_files"] = skills_info["skill_files"]
    ctx["instruction_file_size"] = instruction_file_size(instruction_files)
    attach_liveness_blocks(ctx, liveness)

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
            scope=scope_abs,
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
    ctx["coverage_report"] = coverage_report_block(cov_detect, coverage_data)
    # Layer 6: is a coverage threshold *enforced*, not just measured? Each gate
    # names its file, line and threshold as written; none found reads
    # enforced: false, never inferred from the report above.
    ctx["coverage_gate"] = _safe(
        "coverage_gate", lambda: detect_coverage_gate(repo_root))
    test_pressure = _safe(
        "test_pressure",
        lambda: scan_test_pressure(repo_root, hot_files=hot_files, opt_in=False,
                                   coverage_data=coverage_data),
    )
    ctx["test_pressure"] = normalize_test_pressure(test_pressure)
    # Layer 6 cap: on the default read-only pass the mutation tier never runs, so
    # this reads "applies: true" and carries the required annotation. The LLM
    # reads it when scoring Layer 6; assess_finalize enforces it.
    ctx["mutation_not_run_cap"] = mutation_not_run_cap(ctx["test_pressure"])

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
        repo_root=repo_root,
        index=wiki.hot_test_index,
    )

    # Promissory markers (stale TODO/FIXME, suppressions, disabled tests):
    # the write-side erosion instrument. Family totals + stale counts feed the
    # Layer 3/5/8 scoring rules; stale_by_file feeds the unactioned_intent
    # finding and the hotspot pages (already written above with marker debt).
    ctx["promissory_markers"] = promissory

    # Scans that read only the repository and feed no later block run from the
    # declared table in lib/scan_registry.py, each through the degrade wrapper.
    scan_inputs = {
        "repo_root": repo_root, "instruction_files": instruction_files,
        "scope": scope_abs, "excludes": (extra_exclude_dirs, extra_exclude_patterns),
    }
    run_scans(ctx, scan_inputs, STAGE_READ_SIDE)

    # Accretion ratchet (write-side tendency: files that only ever grow). The
    # scan measured every file above; here it is filtered to files already in the
    # top complexity/size band, sorted worst-first, and capped - so a file earns
    # a line only by scoring high on complexity/LOC *and* growing monotonically.
    ctx["accretion_ratchet"] = accretion_block(accretion_scan, current)

    keyhole = integrate_keyhole_signals(
        repo_root=repo_root,
        complexity_stats=current,
        doc_staleness=dict_or(doc_staleness, {}),
        dead_code=ctx["dead_code"],
        observability=ctx["observability"],
        structure=structure,
        test_pressure=ctx["test_pressure"],
        promissory_markers=dict_or(promissory, None),
        accretion_ratchet=ctx["accretion_ratchet"],
        archetype=dict_or(ctx.get("archetype"), None),
        exclude_dirs=extra_exclude_dirs,
        exclude_patterns=extra_exclude_patterns,
        scope=scope_abs,
        rename_map=rename_map,
    )
    attach_keyhole_blocks(ctx, keyhole)
    # Gap actions: Top 3 candidates read from the coverage and doc-graph
    # signals, which the report writer uses for free slots before judgement.
    ctx["gap_actions"] = build_gap_actions(
        ctx["coverage_report"], doc_graph, current.get("top_hotspots"),
        dict_or(ctx.get("archetype"), None),
    )
    attach_exclusion_disclosures(
        ctx, keyhole, extra_exclude_dirs, extra_exclude_patterns)
    # Generated-file disclosure: the treemap drops files that declare
    # themselves generated (header marker) or carry payload-length lines, and
    # lists them in the stats file. Copied through so the report and gate name
    # each one with its reason rather than letting it vanish from the ranking.
    ctx["excluded_generated"] = excluded_generated(current)

    # Structure drift (third write-side tendency surface: a declared ownership
    # map that no longer matches where the code lives). Tier 0 is the cheap
    # path-existence cut; Tier 1 the grouping-disagreement cut keyhole_signals
    # already computed from the behaviour block's co-change pairs (no second
    # git-log parse). The block is omitted entirely when no ownership map exists
    # - a graceful, half-block-free degrade that keeps a non-owned repo's
    # run-context byte-stable - and its Tier 1 hidden-seam already flows into the
    # report's B3 attention list via keyhole_signals; this block is the record.
    attach_structure_drift(ctx, repo_root, keyhole["structure_drift_tier1"])

    write_findings_badge(
        assess_dir, promissory, ctx["derived_findings"], run_id=run_id,
        scope=scope_rel,
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
    # directory (which the harness substitutes for ${CLAUDE_SKILL_DIR}). A machine-stable
    # pointer so an agent can Read the removal steps without hunting for them.
    ctx["uninstall_instructions_path"] = "references/uninstall.md"

    # Table scans whose blocks sit after the offers block in run-context.json;
    # lib/scan_registry.py lists them.
    run_scans(ctx, scan_inputs, STAGE_POST_OFFERS)

    ctx["anomalies"] = [
        {"code": a.code, "description": a.description, "detail": a.detail}
        for a in detect_anomalies(ctx)
    ]
    (assess_dir / "run-context.json").write_text(json.dumps(ctx, indent=2), encoding="utf-8")
    return ctx


def run_opt_in_mutation(repo_root: Path, scope: Path | None = None) -> int:
    """Re-run the Layer-1 test-pressure scan with the bounded mutation pass ON.

    The consent-gated counterpart to the default read-only scan in
    ``build_run_context``. The orchestrator (SKILL.md Step 2d) calls this only
    after the user accepts the mutation offer - it mutates and *runs* code, so it
    is never part of the default pass. It does not recompute the whole context:
    it reads the existing ``run-context.json``, takes the focus targets that carry
    test evidence from the ``test_focus`` block (``lib.test_focus.mutation_scope``),
    runs ``scan_test_pressure(..., opt_in=True)`` scoped to them, and rewrites the
    ``test_pressure`` block in place along with every block derived from it: the
    Layer 6 ``mutation_not_run_cap``, the E1 ``untrusted_hotspot`` finding in
    ``derived_findings``, and the products ranked or rendered from the findings
    (``attention``, ``attention_low_signal``, ``findings_markdown``,
    ``keyhole_summary``, ``prescribed_actions``, the archive and config-exclude
    disclosures) and ``badge.json``, through ``lib.mutation_refresh``. Blocks that
    do not read ``test_pressure`` (``test_focus``, ``gap_actions``) are kept.
    ``run_bounded_mutation`` itself caps the scope at ``MAX_FILES_TO_MUTATE``, so
    passing every focus path is safe.

    The treemap overlay (regenerated separately by the orchestrator) then reads
    the refreshed ``test_pressure.per_file`` so covered-but-unpinned files get
    hatched. Never raises beyond argparse/IO; degrades to a non-zero return.
    """
    try:
        _scope_abs, _scope_rel, scope_slug = resolve_scope(repo_root, scope)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    assess_dir = repo_root / ".assess" / scope_slug if scope_slug else repo_root / ".assess"
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

    # Only focus entries with test evidence: mutating a file with no test yields
    # all survivors and would flag it for strengthening tests that do not exist.
    focus_files = mutation_scope(ctx.get("test_focus"))
    if not focus_files:
        print("no test_focus targets with test evidence - nothing to mutate",
              file=sys.stderr)
        return 0

    coverage_data = load_coverage_data(repo_root)
    test_pressure = _safe(
        "test_pressure",
        lambda: scan_test_pressure(repo_root, hot_files=focus_files, opt_in=True,
                                   coverage_data=coverage_data),
    )
    ctx["test_pressure"] = normalize_test_pressure(test_pressure)
    # Refresh the Layer 6 cap: the mutation tier may have run this pass, lifting
    # the ceiling to Present. Written back so a subsequent finalize sees it.
    ctx["mutation_not_run_cap"] = mutation_not_run_cap(ctx["test_pressure"])
    # E1 crosses the refreshed survivor density with this run's hotspots; the
    # findings, attention, report products and badge are rebuilt from it.
    if refresh_mutation_findings(
        ctx, load_current_stats(assess_dir), *recorded_excludes(ctx, repo_root),
    ):
        write_findings_badge(
            assess_dir, ctx.get("promissory_markers"), ctx["derived_findings"],
            run_id=ctx.get("run_id"), scope=ctx.get("scope"),
        )
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
            "test_focus targets, and rewrite the test_pressure block and the "
            "findings, attention and report products derived from it in place. "
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
    parser.add_argument(
        "--scope", type=Path, metavar="SUBDIR",
        help=(
            "Scope the assessment to a subtree (`/assess <path>` monorepo "
            "scoping). A path under the repo root; the metrics, score, badge, "
            "wiki, and artifacts are all computed for and labelled with the "
            "scope, and land under `.assess/<scope-slug>/`. The scoped scans "
            "carry no signal from a sibling directory. Omit for a whole-repo "
            "run (unchanged). Pass the same `--scope` to `--opt-in-mutation`."
        ),
    )
    parsed = parser.parse_args(argv)
    repo_root = Path(parsed.repo_root).resolve()
    if parsed.opt_in_mutation:
        return run_opt_in_mutation(repo_root, scope=parsed.scope)
    run_date = datetime.now().strftime("%Y-%m-%d")
    try:
        ctx = build_run_context(
            repo_root=repo_root, run_date=run_date,
            non_interactive=parsed.non_interactive,
            scope=parsed.scope,
        )
    except ValueError as e:
        # Invalid --scope (missing path or outside the repo). Report cleanly
        # with a non-zero exit rather than a traceback.
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(json.dumps(ctx["diff"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
