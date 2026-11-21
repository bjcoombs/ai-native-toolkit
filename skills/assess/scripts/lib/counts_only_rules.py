"""The counts-only allow-list: which run-context string fields survive, and as what.

``lib.counts_only`` keeps every number, boolean and null in a run-context and
decides each string by the table here. A field pattern is the dotted run of
dict keys from the top of ``run-context.json`` down to the value, with list
levels left out and the key of a path-keyed map written ``{}``:
``behaviour.hidden_coupling_findings[3].coupled_pairs[0].file_a`` is
``behaviour.hidden_coupling_findings.coupled_pairs.file_a`` and
``instruction_files["CLAUDE.md"].grade`` is ``instruction_files.{}.grade``.

Four classes:

- ``PATH``: a repo-relative path, directory, glob or dotted module name. Kept
  as written, or replaced by a salted hash when path hashing is on.
- ``TOKEN``: a kind, grade, band, status, date or version. Kept only while it
  is a short slug (``TOKEN_RE``); anything longer or with spaces is dropped.
- ``ID``: a stable identifier of another repository or marketplace, the join
  key of a fleet roll-up (``kind:owner/repo``). Kept while it has no
  whitespace or e-mail address; hashed with the paths when hashing is on.
- ``DROP``: free text, file contents, commands, commit subjects, program names
  and identities. Removed (a dropped list of strings becomes its length).

A string field with no rule is dropped too: the table is an allow-list, so a
new scan field carrying text cannot leak before someone classifies it. The
tests walk the TypedDicts in ``lib/run_context_types.py`` (and the block
shapes beside their builders) and fail on any string field missing here, so a
new field is classified on purpose rather than dropped by accident.

Lookup order for a pattern: the exact pattern, then ``<parent>.*``, then a
global leaf rule (``**.<leaf>``): path field names are paths, and a ``reason``
is dropped text wherever no rule says otherwise.
"""
from __future__ import annotations

import re
from typing import Final, Literal

FieldClass = Literal["path", "token", "id", "drop"]
PATH: Final = "path"
TOKEN: Final = "token"
ID: Final = "id"
DROP: Final = "drop"

# A kept token: a slug of at most 40 characters with no spaces, slashes or "@"
# (so no sentence, path or e-mail address passes as a kind).
TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:+-]{0,39}$")

# A key of an IDENTIFIER_KEYED_MAPS map is kept only when it is an
# identifier-like name (a kind, a tool, a subscore).
KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")

# Maps whose keys are kinds, tools or subscore names, kept while they match
# KEY_RE. Every other dict is a record: a key survives there only when it is a
# known field name (``EXTRA_FIELD_NAMES``, a rule segment, or a field of a
# typed block shape), so a map keyed by a login, a function name or a
# one-segment directory is dropped rather than kept unhashed.
IDENTIFIER_KEYED_MAPS: Final = frozenset({
    "tool_versions",
    "prior_tool_versions",
    "agent_harness.executes_by_kind",
    "agent_harness.counts_by_kind",
    "claude_config.counts_by_kind",
    "claude_config.files_by_kind",
    "doc_staleness.association.methods",
    "promissory_markers.families",
    "instruction_files.{}.subscores",
    "nested_instructions.files.subscores",
    "stats_summary.fn_ccn.backend_by_language",
})

# Record field names of blocks with no TypedDict (built as dict literals). The
# typed blocks' field names come from ``counts_only_schema``.
EXTRA_FIELD_NAMES: Final = frozenset({
    # top-level keys
    "agent_ops", "attention_low_signal", "diff", "diff_detail", "diff_reliable",
    "diff_trend_reset", "instruction_file_size", "interactive", "log_integrity",
    "prior_stats_exists", "skills_count", "skills_present", "churn_degenerate",
    "reoffer_mutation", "capability_offers", "available", "assess_method_version",
    # accretion_ratchet
    "reliable", "deletion_fraction_threshold", "total_in_band", "net_additions",
    "commit_count", "deletion_fraction", "time_span_months",
    # agent_ops
    "settings", "hooks_dir", "routine_dirs", "summary", "tracked_count",
    "permissions_encoded", "hooks_present", "routines_present", "parse_ok",
    "allow_count", "deny_count", "ask_count", "hook_events", "sandbox_configured",
    "tracked", "present", "file_count",
    # archetype
    "applicable_layers", "contradiction_details", "denominator", "na_layers",
    "override", "override_contradicts_signals", "code_file_ratio", "doc_file_count",
    "other_file_count", "has_runtime_surface", "documented", "gist_cited",
    "signals_found", "code_file_count", "kb_maintenance", "signals",
    # coverage, claims, config drift, review reality, gate cost
    "enforced", "failed", "verified", "dropped", "count", "removed", "added",
    "sample", "merged_count", "oldest_merged_days_ago", "reviewed_share",
    "approved_share", "bot_review_share", "self_merged_share", "review_required",
    "hollow_required_review", "required_approval_bypassed", "runs_per_month",
    "minutes_per_run", "minutes_per_month", "capped", "private", "branch", "doc",
    "format", "build_system", "build_files", "actual", "claimed", "parsed",
    # diff and hotspot wiki
    "graduated", "new", "persistent", "regressed", "restructured", "ccn_delta",
    "commits_delta", "loc_delta", "max_fn_ccn_delta", "files_scored", "behind",
    "dirty", "valid", "broken_at_entry", "priority",
    # excludes and pruned paths
    "affected_finding_paths", "dirs", "patterns", "rename_map_complete",
    # promissory markers
    "aging_reliable", "families", "stale_by_file", "stale_touches_threshold",
    "todo_bare", "todo_linked", "total_markers", "total_stale",
    "stale_agent_introduced", "agent_introduced", "justified", "linked", "stale",
    "family", "text", "commit", "survived_touches", "severity",
    # structure_drift tier 1, test_focus, instruction files
    "tier_1", "allowlist_pairs_count", "seam_allowlist_applied", "matched_patterns",
    "total_patterns",
    "human_cochange_agree_count", "human_grouped_never_cochange_count",
    "human_grouped_static_splits_count", "human_split_but_cochange_count",
    "human_split_static_fuses_count", "human_static_agree_count",
    "coverage_present", "total_focus_targets", "entries", "word_count",
    "bloat_penalty", "markers", "declined_by", "declined_at", "version", "reoffer",
})

# Field names that hold a repo-relative path wherever they appear.
GLOBAL_PATH_LEAVES: Final = frozenset({
    "path", "file", "file_a", "file_b", "test_file", "source_file",
})

# Field names that hold free text wherever they appear, unless a rule says so.
GLOBAL_DROP_LEAVES: Final = frozenset({"reason"})

# Maps whose keys are repo-relative paths (hashed like a path value).
PATH_KEYED_MAPS: Final = frozenset({
    "instruction_files",
    "instruction_file_size",
    "sensitive_instruction_content",
    "behaviour.containment_by_dir",
    "documentation.freshness_by_doc",
    "documentation.complexity_coverage",
    "understanding.human_anchor_by_path",
    "understanding.intent_source_by_path",
    "understanding.authorship_class_by_path",
    "test_pressure.survivor_density.by_file",
    "promissory_markers.stale_by_file",
})

# Block -> {field pattern: class}. Grouped by top-level block so the document
# (``render_doc``) lists each block's fields together.
RULES: Final[dict[str, dict[str, FieldClass]]] = {
    "run identity": {
        "run_id": TOKEN,
        "artifact_schema_version": TOKEN,
        "schema_version": TOKEN,
        "prior_schema_version": TOKEN,
        "plugin_version": TOKEN,
        "prior_plugin_version": TOKEN,
        "run_date": TOKEN,
        "scope": PATH,
        "scope_slug": PATH,
        "tool_versions.*": TOKEN,
        "prior_tool_versions.*": TOKEN,
        "diff_version_note": DROP,
        "uninstall_instructions_path": DROP,
    },
    "measured_commit": {
        "measured_commit.committed_date": TOKEN,
        "measured_commit.head_sha": DROP,
        "measured_commit.head_short": DROP,
        "measured_commit.subject": DROP,
        "measured_commit.upstream": DROP,
    },
    "stats_summary": {
        "stats_summary.ccn.basis": TOKEN,
        "stats_summary.fn_ccn.basis": TOKEN,
        "stats_summary.fn_ccn.source.name": TOKEN,
        "stats_summary.fn_ccn.backend_by_language.*": TOKEN,
        "stats_summary.est_tokens.budget.budget_basis": DROP,
        "stats_summary.top_hotspots.ccn_basis": TOKEN,
        "stats_summary.top_hotspots.source": TOKEN,
        "stats_summary.top_hotspots.max_fn_name": DROP,
        "stats_summary.top_complex.ccn_basis": TOKEN,
        "stats_summary.top_complex.source": TOKEN,
        "stats_summary.top_complex.max_fn_name": DROP,
        "stats_summary.top_large.ccn_basis": TOKEN,
        "stats_summary.top_large.source": TOKEN,
        "stats_summary.top_large.max_fn_name": DROP,
        "stats_summary.complexity_unreliable.path": PATH,
        "stats_summary.complexity_unreliable.language": TOKEN,
    },
    "hotspot wiki": {
        "pruned_hotspots": PATH,
        "retired_excluded_hotspots": PATH,
        "dropped_first_flagged": PATH,
        "provisional_first_flagged": PATH,
        "excluded_generated.reason": DROP,
    },
    "instruction files": {
        "instructions_grade": TOKEN,
        "instruction_files.{}.grade": TOKEN,
        "instruction_files.{}.findings.kind": TOKEN,
        "instruction_files.{}.findings.reference": DROP,
        "instruction_files.{}.findings.reason": DROP,
        "skill_files": PATH,
        "untracked_instruction_files": PATH,
        "ancestor_instruction_files": DROP,
        "broken_instruction_refs": PATH,
        "sensitive_instruction_content.{}.category": TOKEN,
        "sensitive_instruction_content.{}.evidence": DROP,
    },
    "instruction_placement": {
        "instruction_placement.flagged": PATH,
        "instruction_placement.files.kind": TOKEN,
        "instruction_placement.files.relocatable.heading": DROP,
        "instruction_placement.files.relocatable.signals": TOKEN,
        "instruction_placement.files.relocatable.home.kind": TOKEN,
        "instruction_placement.files.relocatable.home.target": PATH,
        "instruction_placement.loaded_in_full.kind": TOKEN,
        "instruction_placement.loaded_in_full.relocatable.heading": DROP,
        "instruction_placement.loaded_in_full.relocatable.signals": TOKEN,
        "instruction_placement.loaded_in_full.relocatable.home.kind": TOKEN,
        "instruction_placement.loaded_in_full.relocatable.home.target": PATH,
    },
    "nested_instructions": {
        "nested_instructions.files.tool": TOKEN,
        "nested_instructions.files.kind": TOKEN,
        "nested_instructions.files.grade": TOKEN,
        "nested_instructions.files.scope.directory": PATH,
        "nested_instructions.files.scope.globs": PATH,
        "nested_instructions.files.findings.kind": TOKEN,
        "nested_instructions.files.findings.reference": DROP,
        "nested_instructions.files.findings.reason": DROP,
        "nested_instructions.findings.kind": TOKEN,
        "nested_instructions.findings.detail": DROP,
        "nested_instructions.findings.pattern": PATH,
        "nested_instructions.findings.parent": PATH,
        "nested_instructions.budget.claude_code.files": PATH,
        "nested_instructions.budget.claude_code.skipped_imports.source": PATH,
        "nested_instructions.budget.claude_code.skipped_imports.target": PATH,
        "nested_instructions.budget.claude_code.skipped_imports.reason": DROP,
        "nested_instructions.budget.codex.files": PATH,
        "nested_instructions.budget.codex.deepest_chain.files": PATH,
        "nested_instructions.budget.codex.deepest_chain.directory": PATH,
    },
    "instruction_claims": {
        "instruction_claims.failures.kind": TOKEN,
        "instruction_claims.failures.version": DROP,
        "instruction_claims.failures.reason": DROP,
    },
    "doc_claims": {
        "doc_claims.excluded_trees": PATH,
        "doc_claims.failures.kind": TOKEN,
        "doc_claims.failures.version": DROP,
        "doc_claims.failures.reason": DROP,
        "doc_claims.contradictions.kind": TOKEN,
        "doc_claims.contradictions.sites.value": DROP,
        "doc_claims.contradictions.conflict_pair.value": DROP,
    },
    "doc_graph": {
        "doc_graph.reason": DROP,
        "doc_graph.orphans": PATH,
        "doc_graph.entry_points": PATH,
        "doc_graph.unreachable": PATH,
        "doc_graph.moc_named_but_not_wired": PATH,
        "doc_graph.doc_to_code_edges.doc": PATH,
        "doc_graph.doc_to_code_edges.code": PATH,
        "doc_graph.broken_links.from": PATH,
        "doc_graph.broken_links.target": PATH,
        "doc_graph.broken_links.kind": TOKEN,
        "doc_graph.missing_xrefs.from": PATH,
        "doc_graph.missing_xrefs.to": PATH,
        "doc_graph.link_parents.link_parent": PATH,
        "doc_graph.link_parents.link_entry": PATH,
    },
    "doc_staleness": {
        "doc_staleness.churn_window": DROP,
        "doc_staleness.docs.subject_method": TOKEN,
        "doc_staleness.docs.subject_roots": PATH,
        "doc_staleness.docs.subject_paths": PATH,
        "doc_staleness.docs.base_doc_dir": PATH,
        "doc_staleness.docs.confidence": TOKEN,
        "doc_staleness.docs.last_change_basis": TOKEN,
        "doc_staleness.docs.last_commit_days_bound": TOKEN,
        "doc_staleness.history_boundary_date": TOKEN,
        "doc_staleness.docs.provenance.method": TOKEN,
        "doc_staleness.docs.provenance.sources": PATH,
        "doc_staleness.docs.provenance.generated_by": DROP,
        "doc_staleness.bulk_commits_skipped.sha": DROP,
        "doc_staleness.bulk_commits_skipped.date": TOKEN,
        "doc_staleness.declared_cross_repo.path": PATH,
        "doc_staleness.declared_cross_repo.repo": ID,
        "doc_staleness.declared_cross_repo.scope": PATH,
        "doc_staleness.declared_cross_repo.status": TOKEN,
        "doc_staleness.declared_cross_repo.reason": TOKEN,
        "stale_hubs.subject_method": TOKEN,
        "stale_hubs.subject_roots": PATH,
        "stale_hubs.subject_paths": PATH,
        "stale_hubs.base_doc_dir": PATH,
        "stale_hubs.confidence": TOKEN,
        "stale_hubs.last_change_basis": TOKEN,
        "stale_hubs.last_commit_days_bound": TOKEN,
        "stale_hubs.provenance.method": TOKEN,
        "stale_hubs.provenance.sources": PATH,
        "stale_hubs.provenance.generated_by": DROP,
    },
    "archetype": {
        "archetype.archetype": TOKEN,
        "archetype.detected_via": TOKEN,
        "archetype.reason": DROP,
        "archetype.kb_maintenance.gist": DROP,
    },
    "dead_code / runtime": {
        "dead_code.caveat": DROP,
        "dead_code.candidates.kind": TOKEN,
        "dead_code.candidates.symbol": DROP,
        "dead_code.tools.language": TOKEN,
        "dead_code.tools.tool": TOKEN,
        "dead_code.tools.status": TOKEN,
        "dead_code.tools.reason": DROP,
        "dead_code.tools.consent": DROP,
        "runtime.reason": DROP,
        "runtime.static_reachability.caveat": DROP,
        "runtime.static_reachability.candidates.kind": TOKEN,
        "runtime.static_reachability.candidates.symbol": DROP,
        "runtime.static_reachability.tools.language": TOKEN,
        "runtime.static_reachability.tools.tool": TOKEN,
        "runtime.static_reachability.tools.status": TOKEN,
        "runtime.static_reachability.tools.reason": DROP,
        "runtime.static_reachability.tools.consent": DROP,
    },
    "observability": {
        "observability.boundary": DROP,
        "observability.instrumented.signals": DROP,
        "observability.discoverable.signals": DROP,
        "observability.reachable.signals": DROP,
    },
    "coverage": {
        "coverage_report.source": PATH,
        "coverage_report.format": TOKEN,
        "coverage_gate.gates.tool": TOKEN,
        "coverage_gate.gates.form": DROP,
        "coverage_gate.gates.unit": TOKEN,
        "coverage_gate.gates.metric": TOKEN,
        "coverage_gate.not_detected": DROP,
    },
    "test_pressure": {
        "test_pressure.reason": DROP,
        "test_pressure.gap_signal": TOKEN,
        "test_pressure.mutation_note": DROP,
        "test_pressure.mutation_tools_detected": TOKEN,
        "test_pressure.mutation_scope": PATH,
        "test_pressure.mutation_groups.root": PATH,
        "test_pressure.mutation_groups.config": TOKEN,
        "test_pressure.mutation_groups.scope": PATH,
        "test_pressure.mutation_groups.runner": TOKEN,
        "test_pressure.mutation_groups.unmeasured": PATH,
        "test_pressure.mutation_groups.reason": DROP,
        "test_pressure.cheap_heuristics.confidence_note": DROP,
        "test_pressure.cheap_heuristics.assertion_on_internal.subject_function": DROP,
        "test_pressure.cheap_heuristics.assertion_on_internal.internal_field": DROP,
        "test_pressure.cheap_heuristics.assertion_on_internal.confidence": TOKEN,
        "test_pressure.cheap_heuristics.untested_boundaries.operator": TOKEN,
        "test_pressure.cheap_heuristics.duplicate_truth.field_name": DROP,
        "test_pressure.cheap_heuristics.duplicate_truth.derives_from": DROP,
        "test_pressure.cheap_heuristics.duplicate_truth.confidence": TOKEN,
        "mutation_not_run_cap.max_layer6_band": TOKEN,
        "mutation_not_run_cap.annotation": DROP,
        "test_focus.entries.risk_band": TOKEN,
        "test_focus.entries.test_signal": TOKEN,
        "test_focus.entries.suggested_action": TOKEN,
        "test_focus.entries.hollow_heuristic_kinds": TOKEN,
    },
    "promissory_markers": {
        "promissory_markers.reason": DROP,
        "promissory_markers.top_offenders.family": TOKEN,
        "promissory_markers.top_offenders.text": DROP,
        "promissory_markers.top_offenders.commit": DROP,
    },
    "agent_ops / config_drift / review": {
        "config_drift.reason": DROP,
        "config_drift.entries.key": TOKEN,
        "config_drift.entries.tracked": DROP,
        "config_drift.entries.live": DROP,
        "config_drift.snapshots.kind": TOKEN,
        "config_drift.snapshots.doc": DROP,
        "config_drift.snapshots.branch": DROP,
        "review_reality.reason": DROP,
        "gate_cost_estimate.reason": DROP,
        "gate_cost_estimate.assumption": DROP,
        "capability_offers.build_system": TOKEN,
        "capability_offers.build_files": PATH,
        "accretion_ratchet.reason": DROP,
    },
    "shared_tooling": {
        # Source and marketplace ids name another repository or a marketplace,
        # not this one's content; they are the join key for a fleet roll-up
        # (#550), so they are kept and hashed with --hash-paths.
        "shared_tooling.reason": DROP,
        "shared_tooling.ci_includes.kind": TOKEN,
        "shared_tooling.ci_includes.source": DROP,
        "shared_tooling.ci_includes.id": ID,
        # A pinned version (v4, 1.2.0, main, a SHA), compared for equality across
        # the fleet (#674): kept when it is a short token. A dropped ref reads as
        # "pin unknown" in the shared-tooling map, never as in step.
        "shared_tooling.ci_includes.ref": TOKEN,
        "shared_tooling.marketplaces.kind": TOKEN,
        "shared_tooling.marketplaces.name": DROP,
        "shared_tooling.marketplaces.source": DROP,
        "shared_tooling.marketplaces.id": ID,
        "shared_tooling.ci_source_ids": ID,
        "shared_tooling.marketplace_ids": ID,
    },
    "ci_checks": {
        # Counts, a tri-state boolean and the basis token (issue #698).
        "ci_checks.reason": DROP,
        "ci_checks.basis": TOKEN,
    },
    "layer_grades": {
        # A letter grade (A-F) or null; the presence flag is a boolean.
        "layer_grades.reason": DROP,
        "layer_grades.instructions_grade": TOKEN,
    },
    "agent_building": {
        # Every string is a slug from lib/agent_building_vocab.py or a class.
        "agent_building.reason": DROP,
        "agent_building.agent_class": TOKEN,
        "agent_building.sdks": TOKEN,
        "agent_building.config": TOKEN,
        "agent_building.ci_agents": TOKEN,
        "agent_building.issue_trackers": TOKEN,
        "agent_building.git_hosts": TOKEN,
    },
    "ai_usage": {
        # window is the <n>d|w|mo|y grammar or "all-time"; since is the git
        # expression behind it ("12 months ago"), redundant with window.
        # covered_agents, covered_author_agents and limits are fixed sentences:
        # kept as their length. agent_identity_rule_commits is a positional list
        # of counts (#695), kept as numbers; no rule pattern ever reaches it.
        "ai_usage.reason": DROP,
        "ai_usage.window": TOKEN,
        "ai_usage.since": DROP,
        "ai_usage.merge_rule": TOKEN,
        "ai_usage.covered_agents": DROP,
        "ai_usage.covered_author_agents": DROP,
        "ai_usage.limits": DROP,
    },
    "agent_harness": {
        "agent_harness.reason": DROP,
        "agent_harness.executes.kind": TOKEN,
        "agent_harness.executes.name": DROP,
        "agent_harness.executes.detail": DROP,
        "agent_harness.findings.kind": TOKEN,
        "agent_harness.findings.detail": DROP,
        "agent_harness.findings.fix": DROP,
    },
    "agent_environment": {
        "agent_environment.reason": DROP,
        "agent_environment.setup.sources.kind": TOKEN,
        "agent_environment.setup.sources.detail": DROP,
        "agent_environment.check.entry_points.command": DROP,
        "agent_environment.check.entry_points.source": DROP,
        "agent_environment.check.named.command": DROP,
        "agent_environment.check.named.verdict": TOKEN,
        "agent_environment.check.named.reason": DROP,
        "agent_environment.check.unresolved.command": DROP,
        "agent_environment.check.unresolved.verdict": TOKEN,
        "agent_environment.check.unresolved.reason": DROP,
        "agent_environment.pinning.manifests.ecosystem": TOKEN,
        "agent_environment.pinning.manifests.manifest": PATH,
        "agent_environment.pinning.manifests.lockfile": PATH,
        "agent_environment.ci_duration.reason": DROP,
        "agent_environment.ci_duration.branch": DROP,
        "agent_environment.ci_duration.workflows.workflow": DROP,
        "agent_environment.findings.kind": TOKEN,
        "agent_environment.findings.detail": DROP,
        "agent_environment.findings.fix": DROP,
        "agent_environment.layer5_cap.ceiling": TOKEN,
        "agent_environment.layer5_cap.unmet": TOKEN,
        "agent_environment.layer5_cap.reason": DROP,
    },
    "claude_config": {
        "claude_config.reason": DROP,
        "claude_config.snapshot_date": TOKEN,
        "claude_config.source_urls": DROP,
        "claude_config.manifest_error": DROP,
        "claude_config.findings.kind": TOKEN,
        # The key is copied from the file (an unknown frontmatter key can be a name).
        "claude_config.findings.key": DROP,
        "claude_config.findings.value": DROP,
        "claude_config.findings.fix": DROP,
    },
    "structure": {
        "structure.reason": DROP,
        "structure.language": TOKEN,
        "structure.modularity_reason": TOKEN,
        "structure.footprints.module": PATH,
        "structure.sccs": PATH,
        "structure.breakup_candidates.package": PATH,
        "structure.breakup_candidates.clusters": PATH,
        "structure.internal_burrow_edges.importer": PATH,
        "structure.internal_burrow_edges.imported": PATH,
        "structure.name_collisions.name": PATH,
        "structure.name_collisions.paths": PATH,
    },
    "structure_drift": {
        # Backticked text from architecture docs and CODEOWNERS, not paths from
        # the file tree: a package scope or an internal hostname can sit here.
        "structure_drift.tier_0.empty_ownership_patterns.pattern": DROP,
        "structure_drift.tier_0.empty_ownership_patterns.declared_in": DROP,
        "structure_drift.tier_0.empty_ownership_patterns.owners": DROP,
    },
    "behaviour": {
        "behaviour.reason": DROP,
        "behaviour.static_modularity_projection": DROP,
        "behaviour.static_history_disagreement.finding": TOKEN,
        "behaviour.static_history_disagreement.recommendation": DROP,
        "behaviour.hidden_coupling_findings.finding": TOKEN,
        "behaviour.hidden_coupling_findings.recommendation": DROP,
        "behaviour.refactor_boundaries.finding": TOKEN,
        "behaviour.refactor_boundaries.recommendation": DROP,
    },
    "documentation": {
        "documentation.reason": DROP,
        "documentation.stale_doc_on_complexity.finding": TOKEN,
        "documentation.stale_doc_on_complexity.confidence": TOKEN,
        "documentation.stale_doc_on_complexity.recommendation": DROP,
        "documentation.unexplained_complexity.finding": TOKEN,
        "documentation.unexplained_complexity.confidence": TOKEN,
        "documentation.unexplained_complexity.recommendation": DROP,
        "documentation.good_contracts.finding": TOKEN,
        "documentation.good_contracts.confidence": TOKEN,
        "documentation.good_contracts.recommendation": DROP,
    },
    "understanding": {
        "understanding.reason": DROP,
        "understanding.authorship_class_by_path.{}": TOKEN,
        "understanding.orphaned_understanding": PATH,
        "understanding.modules.authorship_class": TOKEN,
        "understanding.modules.finding": TOKEN,
        "understanding.modules.recommendation": DROP,
    },
    "findings and attention": {
        "prior_findings_snapshot.findings.name": TOKEN,
        "prior_findings_snapshot.findings.paths": PATH,
        "derived_findings.name": TOKEN,
        "derived_findings.paths": PATH,
        "derived_findings.action": DROP,
        "attention.findings": TOKEN,
        "prescribed_actions.findings": TOKEN,
        "prescribed_actions.action": DROP,
        "findings_markdown": DROP,
        "keyhole_summary.concerns.name": TOKEN,
        "keyhole_summary.summary_text": DROP,
        "gap_actions.signal": TOKEN,
        "gap_actions.action": DROP,
        "gap_actions.paths": PATH,
        "excluded_by_config.affected_finding_paths": PATH,
        "excluded_by_config.dirs": PATH,
        "excluded_by_config.patterns": PATH,
        "excluded_as_archive.affected_finding_paths": PATH,
        "pruned_finding_paths.paths": PATH,
        "archived_finding_paths": PATH,
    },
    "decline markers and offers": {
        "decline_markers.tool": TOKEN,
        "decline_markers.declined_by": DROP,
        "decline_markers.declined_at": TOKEN,
        "decline_markers.version": TOKEN,
        "decline_markers.reason": DROP,
        "decline_disclosures": DROP,
        "offers.type": TOKEN,
        "offers.status": TOKEN,
        "offers.reason": TOKEN,
        "offers.tool": TOKEN,
        "anomalies.code": TOKEN,
        "anomalies.description": DROP,
        "anomalies.detail": DROP,
    },
}

# The fleet's ``fleet-data.json`` (lib/fleet_data.py, issue #703): field pattern -> class,
# the dotted keys from the top of that document, list levels left out. Kept apart from
# RULES so its field names never widen the run-context record keys ``field_names`` keeps.
# ``key`` fields (unit and repository keys, and the references to them) are PATH: kept in
# clear, or hashed under the run's salt with spaces allowed, so they always join; under
# hashing a name, owner and web URL never appear. The run stamp and maturity-model
# fields (``FLEET_DATA_KEPT``) come from the fleet run and the caller's --levels file,
# not from a repository, and are kept as written, as the team next-steps pages print them.
FLEET_DATA_RULES: Final[dict[str, FieldClass]] = {
    "views.view": TOKEN,
    "views.units.key": PATH,
    "views.units.parent": PATH,
    "views.units.view": TOKEN,
    "views.units.kind": TOKEN,
    "views.units.source": TOKEN,
    "views.units.name": DROP,
    "views.units.owners": DROP,
    "views.units.levels.weakest.repo": PATH,
    "views.units.overall.weakest.repo": PATH,
    "repos.key": PATH,
    "repos.status": TOKEN,
    "repos.reason": TOKEN,
    "repos.web_url": ID,
    "repos.owners": DROP,
    "repos.units.view": TOKEN,
    "repos.units.unit": PATH,
    "repos.rows.status": TOKEN,
    "repos.rows.value": TOKEN,
    "repos.rows.reason": TOKEN,
}


# fleet-data.json string fields written as they are: generated by the fleet run or
# taken from the caller's maturity model, never from a repository's content.
# ``tests/test_fleet_data.py`` walks the FleetData TypedDicts and fails on a string
# field in neither this set nor FLEET_DATA_RULES.
FLEET_DATA_KEPT: Final = frozenset({
    "run.toolkit_commit", "run.plugin_version", "run.scan_date", "run.window",
    "run.levels_version", "run.scans", "run.fleet_config_sha256",
    "model.id", "model.title", "model.scope",
    "repos.rows.row", "repos.rows.signal", "views.units.levels.row",
})


def classify_fleet_data(pattern: str) -> FieldClass:
    """The class of a ``fleet-data.json`` string field; an unlisted one is dropped."""
    return FLEET_DATA_RULES.get(pattern, DROP)


# Every rule, flattened. A pattern listed under two blocks is a table error
# (the tests check the flat count equals the sum of the blocks).
FLAT_RULES: Final[dict[str, FieldClass]] = {
    pattern: cls for block in RULES.values() for pattern, cls in block.items()
}


def classify(pattern: str) -> FieldClass | None:
    """The class for a field pattern, or None when it is unclassified."""
    if pattern in FLAT_RULES:
        return FLAT_RULES[pattern]
    parent, _, leaf = pattern.rpartition(".")
    if parent and f"{parent}.*" in FLAT_RULES:
        return FLAT_RULES[f"{parent}.*"]
    if leaf in GLOBAL_PATH_LEAVES:
        return PATH
    if leaf in GLOBAL_DROP_LEAVES:
        return DROP
    return None
