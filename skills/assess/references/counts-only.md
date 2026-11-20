# Counts-only output: fields kept per block

Generated from `scripts/lib/counts_only_rules.py` (in the assess skill) by
`lib.counts_only.render_doc`; `test_counts_only.py` fails when this file
and the table disagree. Regenerate with:

```bash
# from the assess skill directory
uv run python -c "import sys; sys.path.insert(0, 'scripts'); from lib.counts_only import render_doc; print(render_doc(), end='')" > references/counts-only.md
```

`assess_core.py --counts-only` writes `run-context.counts-only.json` beside
`run-context.json`. `assess_signals.py --counts-only` filters each block
under `scans` in its `--out` document the same way, keeps only the code
of an unavailable block's `reason` (`no_remote`, `not_registered`, or
`unavailable`), and writes `stamp.target_head` as null.

Every number, boolean and null in every block is kept.
A string is kept only when its field is listed below as a path or a
token; every other string, listed or not, is dropped. A dropped list of
strings is replaced by its length. A token is kept only while it is a
slug of at most 40 characters (no spaces, `/` or `@`).

Paths are kept as written unless `--hash-paths` (or `--hash-salt VALUE`)
is given; then each path value and each key of a path-keyed map becomes
`h:` plus 16 hex digits of an HMAC-SHA256. The salt is random per run
unless `--hash-salt` fixes it, which makes hashes join across runs. The
salt is never written to the output.

A field pattern is the dotted run of keys from the top of
`run-context.json`, list levels left out, `{}` for the key of a
path-keyed map and `*` for any key. A path value with whitespace is
dropped (a path field can be fed a sentence), but a path-keyed map's
keys come from the file tree, so a key like `docs/My Notes.md` is kept
(hashed when hashing is on). A field named `path`, `file`,
`file_a`, `file_b`, `test_file` or `source_file` is a path in every block,
and a field named `reason` is dropped in every block without its own rule.

Not filtered: the wiki pages, hotspot pages, badge and report the run
also writes stay in the target repository's `.assess/` directory and
carry full text. Only `run-context.counts-only.json` (and an
`assess_signals.py --counts-only` document) is content-free.

## Path-keyed maps

- `behaviour.containment_by_dir`
- `documentation.complexity_coverage`
- `documentation.freshness_by_doc`
- `instruction_file_size`
- `instruction_files`
- `promissory_markers.stale_by_file`
- `sensitive_instruction_content`
- `test_pressure.survivor_density.by_file`
- `understanding.authorship_class_by_path`
- `understanding.human_anchor_by_path`
- `understanding.intent_source_by_path`

## Identifier-keyed maps

Keys are kinds, tools or subscore names, kept while they are identifiers.
Every other object is a record: a key survives only when it is a known
field name, so a map keyed by a login, a function or a directory is dropped.

- `agent_harness.counts_by_kind`
- `agent_harness.executes_by_kind`
- `claude_config.counts_by_kind`
- `claude_config.files_by_kind`
- `doc_staleness.association.methods`
- `instruction_files.{}.subscores`
- `nested_instructions.files.subscores`
- `prior_tool_versions`
- `promissory_markers.families`
- `stats_summary.fn_ccn.backend_by_language`
- `tool_versions`

## run identity

| Field | Kept as |
|---|---|
| `run_id` | token |
| `artifact_schema_version` | token |
| `schema_version` | token |
| `prior_schema_version` | token |
| `plugin_version` | token |
| `prior_plugin_version` | token |
| `run_date` | token |
| `scope` | path (hashed when hashing is on) |
| `scope_slug` | path (hashed when hashing is on) |
| `tool_versions.*` | token |
| `prior_tool_versions.*` | token |
| `diff_version_note` | dropped |
| `uninstall_instructions_path` | dropped |

## measured_commit

| Field | Kept as |
|---|---|
| `measured_commit.committed_date` | token |
| `measured_commit.head_sha` | dropped |
| `measured_commit.head_short` | dropped |
| `measured_commit.subject` | dropped |
| `measured_commit.upstream` | dropped |

## stats_summary

| Field | Kept as |
|---|---|
| `stats_summary.ccn.basis` | token |
| `stats_summary.fn_ccn.basis` | token |
| `stats_summary.fn_ccn.source.name` | token |
| `stats_summary.fn_ccn.backend_by_language.*` | token |
| `stats_summary.est_tokens.budget.budget_basis` | dropped |
| `stats_summary.top_hotspots.ccn_basis` | token |
| `stats_summary.top_hotspots.source` | token |
| `stats_summary.top_hotspots.max_fn_name` | dropped |
| `stats_summary.top_complex.ccn_basis` | token |
| `stats_summary.top_complex.source` | token |
| `stats_summary.top_complex.max_fn_name` | dropped |
| `stats_summary.top_large.ccn_basis` | token |
| `stats_summary.top_large.source` | token |
| `stats_summary.top_large.max_fn_name` | dropped |

## hotspot wiki

| Field | Kept as |
|---|---|
| `pruned_hotspots` | path (hashed when hashing is on) |
| `retired_excluded_hotspots` | path (hashed when hashing is on) |
| `dropped_first_flagged` | path (hashed when hashing is on) |
| `provisional_first_flagged` | path (hashed when hashing is on) |
| `excluded_generated.reason` | dropped |

## instruction files

| Field | Kept as |
|---|---|
| `instructions_grade` | token |
| `instruction_files.{}.grade` | token |
| `instruction_files.{}.findings.kind` | token |
| `instruction_files.{}.findings.reference` | dropped |
| `instruction_files.{}.findings.reason` | dropped |
| `skill_files` | path (hashed when hashing is on) |
| `untracked_instruction_files` | path (hashed when hashing is on) |
| `ancestor_instruction_files` | dropped |
| `broken_instruction_refs` | path (hashed when hashing is on) |
| `sensitive_instruction_content.{}.category` | token |
| `sensitive_instruction_content.{}.evidence` | dropped |

## instruction_placement

| Field | Kept as |
|---|---|
| `instruction_placement.flagged` | path (hashed when hashing is on) |
| `instruction_placement.files.kind` | token |
| `instruction_placement.files.relocatable.heading` | dropped |
| `instruction_placement.files.relocatable.signals` | token |
| `instruction_placement.files.relocatable.home.kind` | token |
| `instruction_placement.files.relocatable.home.target` | path (hashed when hashing is on) |
| `instruction_placement.loaded_in_full.kind` | token |
| `instruction_placement.loaded_in_full.relocatable.heading` | dropped |
| `instruction_placement.loaded_in_full.relocatable.signals` | token |
| `instruction_placement.loaded_in_full.relocatable.home.kind` | token |
| `instruction_placement.loaded_in_full.relocatable.home.target` | path (hashed when hashing is on) |

## nested_instructions

| Field | Kept as |
|---|---|
| `nested_instructions.files.tool` | token |
| `nested_instructions.files.kind` | token |
| `nested_instructions.files.grade` | token |
| `nested_instructions.files.scope.directory` | path (hashed when hashing is on) |
| `nested_instructions.files.scope.globs` | path (hashed when hashing is on) |
| `nested_instructions.files.findings.kind` | token |
| `nested_instructions.files.findings.reference` | dropped |
| `nested_instructions.files.findings.reason` | dropped |
| `nested_instructions.findings.kind` | token |
| `nested_instructions.findings.detail` | dropped |
| `nested_instructions.findings.pattern` | path (hashed when hashing is on) |
| `nested_instructions.findings.parent` | path (hashed when hashing is on) |
| `nested_instructions.budget.claude_code.files` | path (hashed when hashing is on) |
| `nested_instructions.budget.claude_code.skipped_imports.source` | path (hashed when hashing is on) |
| `nested_instructions.budget.claude_code.skipped_imports.target` | path (hashed when hashing is on) |
| `nested_instructions.budget.claude_code.skipped_imports.reason` | dropped |
| `nested_instructions.budget.codex.files` | path (hashed when hashing is on) |
| `nested_instructions.budget.codex.deepest_chain.files` | path (hashed when hashing is on) |
| `nested_instructions.budget.codex.deepest_chain.directory` | path (hashed when hashing is on) |

## instruction_claims

| Field | Kept as |
|---|---|
| `instruction_claims.failures.kind` | token |
| `instruction_claims.failures.version` | dropped |
| `instruction_claims.failures.reason` | dropped |

## doc_graph

| Field | Kept as |
|---|---|
| `doc_graph.reason` | dropped |
| `doc_graph.orphans` | path (hashed when hashing is on) |
| `doc_graph.entry_points` | path (hashed when hashing is on) |
| `doc_graph.unreachable` | path (hashed when hashing is on) |
| `doc_graph.moc_named_but_not_wired` | path (hashed when hashing is on) |
| `doc_graph.doc_to_code_edges.doc` | path (hashed when hashing is on) |
| `doc_graph.doc_to_code_edges.code` | path (hashed when hashing is on) |
| `doc_graph.broken_links.from` | path (hashed when hashing is on) |
| `doc_graph.broken_links.target` | path (hashed when hashing is on) |
| `doc_graph.broken_links.kind` | token |
| `doc_graph.missing_xrefs.from` | path (hashed when hashing is on) |
| `doc_graph.missing_xrefs.to` | path (hashed when hashing is on) |
| `doc_graph.link_parents.link_parent` | path (hashed when hashing is on) |
| `doc_graph.link_parents.link_entry` | path (hashed when hashing is on) |

## doc_staleness

| Field | Kept as |
|---|---|
| `doc_staleness.churn_window` | dropped |
| `doc_staleness.docs.subject_method` | token |
| `doc_staleness.docs.confidence` | token |
| `doc_staleness.docs.last_change_basis` | token |
| `doc_staleness.docs.provenance.method` | token |
| `doc_staleness.docs.provenance.sources` | path (hashed when hashing is on) |
| `doc_staleness.docs.provenance.generated_by` | dropped |
| `doc_staleness.bulk_commits_skipped.sha` | dropped |
| `doc_staleness.bulk_commits_skipped.date` | token |
| `stale_hubs.subject_method` | token |
| `stale_hubs.confidence` | token |
| `stale_hubs.last_change_basis` | token |
| `stale_hubs.provenance.method` | token |
| `stale_hubs.provenance.sources` | path (hashed when hashing is on) |
| `stale_hubs.provenance.generated_by` | dropped |

## archetype

| Field | Kept as |
|---|---|
| `archetype.archetype` | token |
| `archetype.detected_via` | token |
| `archetype.reason` | dropped |
| `archetype.kb_maintenance.gist` | dropped |

## dead_code / runtime

| Field | Kept as |
|---|---|
| `dead_code.caveat` | dropped |
| `dead_code.candidates.kind` | token |
| `dead_code.candidates.symbol` | dropped |
| `dead_code.tools.language` | token |
| `dead_code.tools.tool` | token |
| `dead_code.tools.status` | token |
| `dead_code.tools.reason` | dropped |
| `dead_code.tools.consent` | dropped |
| `runtime.reason` | dropped |
| `runtime.static_reachability.caveat` | dropped |
| `runtime.static_reachability.candidates.kind` | token |
| `runtime.static_reachability.candidates.symbol` | dropped |
| `runtime.static_reachability.tools.language` | token |
| `runtime.static_reachability.tools.tool` | token |
| `runtime.static_reachability.tools.status` | token |
| `runtime.static_reachability.tools.reason` | dropped |
| `runtime.static_reachability.tools.consent` | dropped |

## observability

| Field | Kept as |
|---|---|
| `observability.boundary` | dropped |
| `observability.instrumented.signals` | dropped |
| `observability.discoverable.signals` | dropped |
| `observability.reachable.signals` | dropped |

## coverage

| Field | Kept as |
|---|---|
| `coverage_report.source` | path (hashed when hashing is on) |
| `coverage_report.format` | token |
| `coverage_gate.gates.tool` | token |
| `coverage_gate.gates.form` | dropped |
| `coverage_gate.gates.unit` | token |
| `coverage_gate.gates.metric` | token |
| `coverage_gate.not_detected` | dropped |

## test_pressure

| Field | Kept as |
|---|---|
| `test_pressure.reason` | dropped |
| `test_pressure.gap_signal` | token |
| `test_pressure.mutation_note` | dropped |
| `test_pressure.mutation_tools_detected` | token |
| `test_pressure.mutation_scope` | path (hashed when hashing is on) |
| `test_pressure.mutation_groups.root` | path (hashed when hashing is on) |
| `test_pressure.mutation_groups.config` | token |
| `test_pressure.mutation_groups.scope` | path (hashed when hashing is on) |
| `test_pressure.mutation_groups.runner` | token |
| `test_pressure.mutation_groups.unmeasured` | path (hashed when hashing is on) |
| `test_pressure.mutation_groups.reason` | dropped |
| `test_pressure.cheap_heuristics.confidence_note` | dropped |
| `test_pressure.cheap_heuristics.assertion_on_internal.subject_function` | dropped |
| `test_pressure.cheap_heuristics.assertion_on_internal.internal_field` | dropped |
| `test_pressure.cheap_heuristics.assertion_on_internal.confidence` | token |
| `test_pressure.cheap_heuristics.untested_boundaries.operator` | token |
| `test_pressure.cheap_heuristics.duplicate_truth.field_name` | dropped |
| `test_pressure.cheap_heuristics.duplicate_truth.derives_from` | dropped |
| `test_pressure.cheap_heuristics.duplicate_truth.confidence` | token |
| `mutation_not_run_cap.max_layer6_band` | token |
| `mutation_not_run_cap.annotation` | dropped |
| `test_focus.entries.risk_band` | token |
| `test_focus.entries.test_signal` | token |
| `test_focus.entries.suggested_action` | token |
| `test_focus.entries.hollow_heuristic_kinds` | token |

## promissory_markers

| Field | Kept as |
|---|---|
| `promissory_markers.reason` | dropped |
| `promissory_markers.top_offenders.family` | token |
| `promissory_markers.top_offenders.text` | dropped |
| `promissory_markers.top_offenders.commit` | dropped |

## agent_ops / config_drift / review

| Field | Kept as |
|---|---|
| `config_drift.reason` | dropped |
| `config_drift.entries.key` | token |
| `config_drift.entries.tracked` | dropped |
| `config_drift.entries.live` | dropped |
| `config_drift.snapshots.kind` | token |
| `config_drift.snapshots.doc` | dropped |
| `config_drift.snapshots.branch` | dropped |
| `review_reality.reason` | dropped |
| `gate_cost_estimate.reason` | dropped |
| `gate_cost_estimate.assumption` | dropped |
| `capability_offers.build_system` | token |
| `capability_offers.build_files` | path (hashed when hashing is on) |
| `accretion_ratchet.reason` | dropped |

## shared_tooling

| Field | Kept as |
|---|---|
| `shared_tooling.reason` | dropped |
| `shared_tooling.ci_includes.kind` | token |
| `shared_tooling.ci_includes.source` | dropped |
| `shared_tooling.ci_includes.id` | external id (hashed when hashing is on) |
| `shared_tooling.marketplaces.kind` | token |
| `shared_tooling.marketplaces.name` | dropped |
| `shared_tooling.marketplaces.source` | dropped |
| `shared_tooling.marketplaces.id` | external id (hashed when hashing is on) |
| `shared_tooling.ci_source_ids` | external id (hashed when hashing is on) |
| `shared_tooling.marketplace_ids` | external id (hashed when hashing is on) |

## agent_harness

| Field | Kept as |
|---|---|
| `agent_harness.reason` | dropped |
| `agent_harness.executes.kind` | token |
| `agent_harness.executes.name` | dropped |
| `agent_harness.executes.detail` | dropped |
| `agent_harness.findings.kind` | token |
| `agent_harness.findings.detail` | dropped |
| `agent_harness.findings.fix` | dropped |

## agent_environment

| Field | Kept as |
|---|---|
| `agent_environment.reason` | dropped |
| `agent_environment.setup.sources.kind` | token |
| `agent_environment.setup.sources.detail` | dropped |
| `agent_environment.check.entry_points.command` | dropped |
| `agent_environment.check.entry_points.source` | dropped |
| `agent_environment.check.named.command` | dropped |
| `agent_environment.check.named.verdict` | token |
| `agent_environment.check.named.reason` | dropped |
| `agent_environment.check.unresolved.command` | dropped |
| `agent_environment.check.unresolved.verdict` | token |
| `agent_environment.check.unresolved.reason` | dropped |
| `agent_environment.pinning.manifests.ecosystem` | token |
| `agent_environment.pinning.manifests.manifest` | path (hashed when hashing is on) |
| `agent_environment.pinning.manifests.lockfile` | path (hashed when hashing is on) |
| `agent_environment.ci_duration.reason` | dropped |
| `agent_environment.ci_duration.branch` | dropped |
| `agent_environment.ci_duration.workflows.workflow` | dropped |
| `agent_environment.findings.kind` | token |
| `agent_environment.findings.detail` | dropped |
| `agent_environment.findings.fix` | dropped |
| `agent_environment.layer5_cap.ceiling` | token |
| `agent_environment.layer5_cap.unmet` | token |
| `agent_environment.layer5_cap.reason` | dropped |

## claude_config

| Field | Kept as |
|---|---|
| `claude_config.reason` | dropped |
| `claude_config.snapshot_date` | token |
| `claude_config.source_urls` | dropped |
| `claude_config.manifest_error` | dropped |
| `claude_config.findings.kind` | token |
| `claude_config.findings.key` | dropped |
| `claude_config.findings.value` | dropped |
| `claude_config.findings.fix` | dropped |

## structure

| Field | Kept as |
|---|---|
| `structure.reason` | dropped |
| `structure.footprints.module` | path (hashed when hashing is on) |
| `structure.sccs` | path (hashed when hashing is on) |
| `structure.breakup_candidates.package` | path (hashed when hashing is on) |
| `structure.breakup_candidates.clusters` | path (hashed when hashing is on) |
| `structure.internal_burrow_edges.importer` | path (hashed when hashing is on) |
| `structure.internal_burrow_edges.imported` | path (hashed when hashing is on) |
| `structure.name_collisions.name` | path (hashed when hashing is on) |
| `structure.name_collisions.paths` | path (hashed when hashing is on) |

## structure_drift

| Field | Kept as |
|---|---|
| `structure_drift.tier_0.empty_ownership_patterns.pattern` | dropped |
| `structure_drift.tier_0.empty_ownership_patterns.declared_in` | dropped |
| `structure_drift.tier_0.empty_ownership_patterns.owners` | dropped |

## behaviour

| Field | Kept as |
|---|---|
| `behaviour.reason` | dropped |
| `behaviour.static_modularity_projection` | dropped |
| `behaviour.static_history_disagreement.finding` | token |
| `behaviour.static_history_disagreement.recommendation` | dropped |
| `behaviour.hidden_coupling_findings.finding` | token |
| `behaviour.hidden_coupling_findings.recommendation` | dropped |
| `behaviour.refactor_boundaries.finding` | token |
| `behaviour.refactor_boundaries.recommendation` | dropped |

## documentation

| Field | Kept as |
|---|---|
| `documentation.reason` | dropped |
| `documentation.stale_doc_on_complexity.finding` | token |
| `documentation.stale_doc_on_complexity.confidence` | token |
| `documentation.stale_doc_on_complexity.recommendation` | dropped |
| `documentation.unexplained_complexity.finding` | token |
| `documentation.unexplained_complexity.confidence` | token |
| `documentation.unexplained_complexity.recommendation` | dropped |
| `documentation.good_contracts.finding` | token |
| `documentation.good_contracts.confidence` | token |
| `documentation.good_contracts.recommendation` | dropped |

## understanding

| Field | Kept as |
|---|---|
| `understanding.reason` | dropped |
| `understanding.authorship_class_by_path.{}` | token |
| `understanding.orphaned_understanding` | path (hashed when hashing is on) |
| `understanding.modules.authorship_class` | token |
| `understanding.modules.finding` | token |
| `understanding.modules.recommendation` | dropped |

## findings and attention

| Field | Kept as |
|---|---|
| `prior_findings_snapshot.findings.name` | token |
| `prior_findings_snapshot.findings.paths` | path (hashed when hashing is on) |
| `derived_findings.name` | token |
| `derived_findings.paths` | path (hashed when hashing is on) |
| `derived_findings.action` | dropped |
| `attention.findings` | token |
| `prescribed_actions.findings` | token |
| `prescribed_actions.action` | dropped |
| `findings_markdown` | dropped |
| `keyhole_summary.concerns.name` | token |
| `keyhole_summary.summary_text` | dropped |
| `gap_actions.signal` | token |
| `gap_actions.action` | dropped |
| `gap_actions.paths` | path (hashed when hashing is on) |
| `excluded_by_config.affected_finding_paths` | path (hashed when hashing is on) |
| `excluded_by_config.dirs` | path (hashed when hashing is on) |
| `excluded_by_config.patterns` | path (hashed when hashing is on) |
| `excluded_as_archive.affected_finding_paths` | path (hashed when hashing is on) |
| `pruned_finding_paths.paths` | path (hashed when hashing is on) |
| `archived_finding_paths` | path (hashed when hashing is on) |

## decline markers and offers

| Field | Kept as |
|---|---|
| `decline_markers.tool` | token |
| `decline_markers.declined_by` | dropped |
| `decline_markers.declined_at` | token |
| `decline_markers.version` | token |
| `decline_markers.reason` | dropped |
| `decline_disclosures` | dropped |
| `offers.type` | token |
| `offers.status` | token |
| `offers.reason` | token |
| `offers.tool` | token |
| `anomalies.code` | token |
| `anomalies.description` | dropped |
| `anomalies.detail` | dropped |
