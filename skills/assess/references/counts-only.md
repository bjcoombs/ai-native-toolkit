# Counts-only output: fields kept per block

Generated from each collector's `privacy` table in `checks/core.toml` and
the global rules in `scripts/lib/counts_only/counts_only_rules.py` (in the assess skill)
by `lib.counts_only.content_free.render_doc`; `test_counts_only.py` fails when this file
and the table disagree. Each section below is one collector, sorted by block name. Regenerate with:

```bash
# from the assess skill directory
uv run python -c "import sys; sys.path.insert(0, 'scripts'); from lib.counts_only.content_free import render_doc; print(render_doc(), end='')" > references/counts-only.md
```

`assess_core.py --counts-only` writes `run-context.counts-only.json` beside
`run-context.json`. `assess_signals.py --counts-only` filters each block
under `scans` in its `--out` document the same way, keeps only the code
of an unavailable block's `reason` (`no_remote`, `not_registered`, or
`unavailable`), and writes `stamp.target_head` as null.

Every number, boolean and null in every block is kept. The `timings`
diagnostic block is left out whole: it differs on every run.
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
A field kept as a reason code keeps only the code of a `code: sentence`
reason when it is a known structure code (`no_builder:rust`, `no_import_graph_go`) or `no_ownership_map`,
or the code a known sentence maps to (`no_grimp`), else `unavailable`,
so a path or name before the colon is never kept; an empty reason is dropped.

Not filtered: the wiki pages, hotspot pages, badge and report the run
also writes stay in the target repository's `.assess/` directory and
carry full text. Only `run-context.counts-only.json` (and an
`assess_signals.py --counts-only` document) is content-free, with the
`package-map.counts-only.svg`, `doc-drift.counts-only.svg` and
`review-strip.counts-only.svg` drawn from it. Like the JSON, those copies keep
path-class values (package ids, doc paths) in clear under `--counts-only` alone;
only `--hash-paths` or `--hash-salt` hashes them, so share them from a hashed run.
The Step ladder and the evidence waffle have no copy: they name only the model's Steps,
gates and rows, and counts.

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
- `pack`
- `pack.*`
- `prior_level.models`
- `prior_tool_versions`
- `promissory_markers.families`
- `stats_summary.fn_ccn.backend_by_language`
- `stats_summary.fn_ccn.file_level_by_language`
- `tool_versions`
- `unit_readings.units.blocks`
- `unit_readings.units.blocks.promissory_markers.families`

## accretion_ratchet

| Field | Kept as |
|---|---|
| `accretion_ratchet.reason` | dropped |

## agent_building

| Field | Kept as |
|---|---|
| `agent_building.reason` | dropped |
| `agent_building.agent_class` | token |
| `agent_building.sdks` | token |
| `agent_building.config` | token |
| `agent_building.ci_agents` | token |
| `agent_building.issue_trackers` | token |
| `agent_building.git_hosts` | token |

## agent_consistency

| Field | Kept as |
|---|---|
| `agent_consistency.systems` | token |
| `agent_consistency.conflicts_reason` | token |
| `agent_consistency.unlinked_reason` | token |
| `agent_consistency.canonical` | path (hashed when hashing is on) |
| `agent_consistency.files.path` | path (hashed when hashing is on) |
| `agent_consistency.files.system` | token |
| `agent_consistency.files.link` | token |
| `agent_consistency.divergences.kind` | token |
| `agent_consistency.divergences.value` | dropped |
| `agent_consistency.divergences.named_in` | token |
| `agent_consistency.divergences.missing_from` | token |
| `agent_consistency.divergences.stated.system` | token |
| `agent_consistency.next_steps.system` | token |
| `agent_consistency.next_steps.path` | path (hashed when hashing is on) |
| `agent_consistency.next_steps.pointer` | dropped |
| `agent_consistency.next_steps.symlink` | dropped |
| `agent_consistency.not_read` | dropped |

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
| `agent_environment.pinning.manifests.note` | token |
| `agent_environment.ci_duration.reason` | dropped |
| `agent_environment.ci_duration.branch` | dropped |
| `agent_environment.ci_duration.workflows.workflow` | dropped |
| `agent_environment.findings.kind` | token |
| `agent_environment.findings.detail` | dropped |
| `agent_environment.findings.fix` | dropped |
| `agent_environment.layer5_cap.ceiling` | token |
| `agent_environment.layer5_cap.unmet` | token |
| `agent_environment.layer5_cap.reason` | dropped |

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

## agent_ops

| Field | Kept as |
|---|---|
| `agent_ops.settings.default_mode` | token |
| `agent_ops.managed.state` | token |
| `agent_ops.managed.reason` | token |
| `agent_ops.managed.sources.kind` | token |
| `agent_ops.summary.permissions_source` | token |
| `agent_ops.summary.permissions_encoded_reason` | token |
| `agent_ops.summary.agent_work_present_reason` | token |
| `agent_ops.agent_workflows.path` | path (hashed when hashing is on) |
| `agent_ops.agent_workflows.triggers` | token |
| `agent_ops.agent_workflows.signs` | token |
| `agent_ops.agent_workflows.via` | path (hashed when hashing is on) |
| `agent_ops.agent_jobs.path` | path (hashed when hashing is on) |
| `agent_ops.agent_jobs.job` | dropped |
| `agent_ops.agent_jobs.scope` | token |
| `agent_ops.agent_jobs.write_scopes` | token |
| `agent_ops.summary.agent_jobs_least_privilege_reason` | token |
| `agent_ops.summary.agent_job_write_access_reason` | token |

## agent_outcome

| Field | Kept as |
|---|---|
| `agent_outcome.reason` | reason code |
| `agent_outcome.window` | token |
| `agent_outcome.since` | dropped |
| `agent_outcome.agent_rule` | token |
| `agent_outcome.revert_null_reason` | token |
| `agent_outcome.rework_null_reason` | token |
| `agent_outcome.limits` | dropped |

## ai_usage

| Field | Kept as |
|---|---|
| `ai_usage.reason` | dropped |
| `ai_usage.window` | token |
| `ai_usage.since` | dropped |
| `ai_usage.merge_rule` | token |
| `ai_usage.covered_agents` | dropped |
| `ai_usage.covered_author_agents` | dropped |
| `ai_usage.limits` | dropped |

## ancestor_instruction_files

| Field | Kept as |
|---|---|
| `ancestor_instruction_files` | dropped |

## anomalies

| Field | Kept as |
|---|---|
| `anomalies.code` | token |
| `anomalies.description` | dropped |
| `anomalies.detail` | dropped |

## archetype

| Field | Kept as |
|---|---|
| `archetype.archetype` | token |
| `archetype.detected_via` | token |
| `archetype.reason` | dropped |
| `archetype.kb_maintenance.gist` | dropped |

## backlog_ready

| Field | Kept as |
|---|---|
| `backlog_ready.required_form_field_reason` | token |
| `backlog_ready.untriaged_on_open_reason` | token |
| `backlog_ready.stale_job_safe_reason` | token |

## broken_instruction_refs

| Field | Kept as |
|---|---|
| `broken_instruction_refs` | path (hashed when hashing is on) |

## ci_checks

| Field | Kept as |
|---|---|
| `ci_checks.reason` | dropped |
| `ci_checks.basis` | token |
| `ci_checks.enforced_basis` | token |
| `ci_checks.lint_basis` | token |
| `ci_checks.includes.kind` | token |
| `ci_checks.includes.id` | external id (hashed when hashing is on) |
| `ci_checks.includes.status` | token |
| `ci_checks.includes.ref_basis` | token |
| `ci_checks.includes.rename` | token |
| `ci_checks.merged_config` | token |
| `ci_checks.merged_config_reason` | token |

## ci_model_jobs

| Field | Kept as |
|---|---|
| `ci_model_jobs.ungated_reason` | token |
| `ci_model_jobs.jobs.path` | path (hashed when hashing is on) |
| `ci_model_jobs.jobs.job` | dropped |
| `ci_model_jobs.jobs.platform` | token |
| `ci_model_jobs.jobs.signs` | token |
| `ci_model_jobs.jobs.triggers` | token |
| `ci_model_jobs.jobs.gates` | token |
| `ci_model_jobs.jobs.state` | token |
| `ci_model_jobs.jobs.reason` | token |

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

## concurrency

| Field | Kept as |
|---|---|
| `concurrency.reason` | reason code |
| `concurrency.window` | token |
| `concurrency.since` | dropped |
| `concurrency.fan_in_null_reason` | token |
| `concurrency.hot_files` | dropped |
| `concurrency.limits` | dropped |

## config_drift

| Field | Kept as |
|---|---|
| `config_drift.reason` | dropped |
| `config_drift.entries.key` | token |
| `config_drift.entries.tracked` | dropped |
| `config_drift.entries.live` | dropped |
| `config_drift.snapshots.kind` | token |
| `config_drift.snapshots.doc` | dropped |
| `config_drift.snapshots.branch` | dropped |

## contributor_files

| Field | Kept as |
|---|---|
| `contributor_files.reason` | reason code |
| `contributor_files.guide.path` | path (hashed when hashing is on) |
| `contributor_files.policy.path` | path (hashed when hashing is on) |
| `contributor_files.policy.kind` | token |
| `contributor_files.policy.source` | token |
| `contributor_files.policy.reason` | reason code |

## coverage_gate

| Field | Kept as |
|---|---|
| `coverage_gate.gates.tool` | token |
| `coverage_gate.gates.form` | dropped |
| `coverage_gate.gates.unit` | token |
| `coverage_gate.gates.metric` | token |
| `coverage_gate.unmeasured_reason` | token |
| `coverage_gate.enforced_basis` | token |
| `coverage_gate.not_detected` | dropped |

## coverage_report

| Field | Kept as |
|---|---|
| `coverage_report.source` | path (hashed when hashing is on) |
| `coverage_report.format` | token |

## decline

| Field | Kept as |
|---|---|
| `decline_markers.tool` | token |
| `decline_markers.declined_by` | dropped |
| `decline_markers.declined_at` | token |
| `decline_markers.version` | token |
| `decline_markers.reason` | dropped |
| `decline_disclosures` | dropped |

## delivery

| Field | Kept as |
|---|---|
| `delivery.reason` | dropped |

## doc_claims

| Field | Kept as |
|---|---|
| `doc_claims.excluded_trees` | path (hashed when hashing is on) |
| `doc_claims.failures.kind` | token |
| `doc_claims.failures.version` | dropped |
| `doc_claims.failures.reason` | dropped |
| `doc_claims.contradictions.kind` | token |
| `doc_claims.contradictions.sites.value` | dropped |
| `doc_claims.contradictions.conflict_pair.value` | dropped |

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
| `doc_graph.unparsed_doc_formats` | token |

## doc_staleness

| Field | Kept as |
|---|---|
| `doc_staleness.churn_window` | dropped |
| `doc_staleness.docs.subject_method` | token |
| `doc_staleness.docs.subject_roots` | path (hashed when hashing is on) |
| `doc_staleness.docs.subject_match` | token |
| `doc_staleness.docs.subject_paths` | path (hashed when hashing is on) |
| `doc_staleness.docs.base_doc_dir` | path (hashed when hashing is on) |
| `doc_staleness.docs.confidence` | token |
| `doc_staleness.docs.last_change_basis` | token |
| `doc_staleness.docs.last_commit_days_bound` | token |
| `doc_staleness.history_boundary_date` | token |
| `doc_staleness.docs.provenance.method` | token |
| `doc_staleness.docs.provenance.sources` | path (hashed when hashing is on) |
| `doc_staleness.docs.provenance.generated_by` | dropped |
| `doc_staleness.bulk_commits_skipped.sha` | dropped |
| `doc_staleness.bulk_commits_skipped.date` | token |
| `doc_staleness.declared_cross_repo.path` | path (hashed when hashing is on) |
| `doc_staleness.declared_cross_repo.repo` | external id (hashed when hashing is on) |
| `doc_staleness.declared_cross_repo.scope` | path (hashed when hashing is on) |
| `doc_staleness.declared_cross_repo.status` | token |
| `doc_staleness.declared_cross_repo.reason` | token |
| `doc_staleness.summary.stale_pct_reason` | token |
| `doc_staleness.normalisation.base_doc_subject` | token |
| `doc_staleness.normalisation.rename_scope` | token |

## excluded_generated

| Field | Kept as |
|---|---|
| `excluded_generated.reason` | dropped |

## exclusion_disclosures

| Field | Kept as |
|---|---|
| `excluded_by_config.affected_finding_paths` | path (hashed when hashing is on) |
| `excluded_by_config.dirs` | path (hashed when hashing is on) |
| `excluded_by_config.patterns` | path (hashed when hashing is on) |
| `excluded_as_archive.affected_finding_paths` | path (hashed when hashing is on) |
| `pruned_finding_paths.paths` | path (hashed when hashing is on) |

## gap_actions

| Field | Kept as |
|---|---|
| `gap_actions.signal` | token |
| `gap_actions.action` | dropped |
| `gap_actions.why` | dropped |
| `gap_actions.paths` | path (hashed when hashing is on) |
| `gap_actions.finding` | token |
| `gap_actions.mode` | token |
| `gap_actions.cost.metric` | token |
| `gap_actions.cost.path` | path (hashed when hashing is on) |

## gate_cost_estimate

| Field | Kept as |
|---|---|
| `gate_cost_estimate.reason` | dropped |
| `gate_cost_estimate.assumption` | dropped |

## github_secret_scanning

| Field | Kept as |
|---|---|
| `github_secret_scanning.reason` | reason code |

## guardrails

| Field | Kept as |
|---|---|
| `guardrails.gates.kind` | token |
| `guardrails.stated_pct_reason` | token |
| `guardrails.gates.tool` | token |
| `guardrails.gates.rule` | token |
| `guardrails.gates.stated_in` | path (hashed when hashing is on) |
| `guardrails.gates.snippet` | dropped |
| `guardrails.not_read` | dropped |

## headline

| Field | Kept as |
|---|---|
| `headline.levels_version` | token |
| `headline.lines` | dropped |
| `headline.markdown` | dropped |
| `headline.summary` | dropped |
| `headline.next_why` | dropped |
| `headline.summary_lines` | dropped |
| `headline.call_to_action` | dropped |
| `headline.summary_markdown` | dropped |
| `headline.reason` | token |
| `headline.tags.tag` | token |

## instruction_claims

| Field | Kept as |
|---|---|
| `instruction_claims.failures.kind` | token |
| `instruction_claims.failures.version` | dropped |
| `instruction_claims.failures.reason` | dropped |

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

## keyhole

| Field | Kept as |
|---|---|
| `archived_finding_paths` | path (hashed when hashing is on) |

## keyhole_blocks

| Field | Kept as |
|---|---|
| `runtime.reason` | dropped |
| `runtime.static_reachability.caveat` | dropped |
| `runtime.static_reachability.candidates.kind` | token |
| `runtime.static_reachability.candidates.symbol` | dropped |
| `runtime.static_reachability.tools.language` | token |
| `runtime.static_reachability.tools.tool` | token |
| `runtime.static_reachability.tools.status` | token |
| `runtime.static_reachability.tools.reason` | dropped |
| `runtime.static_reachability.tools.consent` | dropped |
| `structure.reason` | reason code |
| `structure.language` | token |
| `structure.modularity_reason` | token |
| `structure.footprints.module` | path (hashed when hashing is on) |
| `structure.sccs` | path (hashed when hashing is on) |
| `structure.breakup_candidates.package` | path (hashed when hashing is on) |
| `structure.breakup_candidates.clusters` | path (hashed when hashing is on) |
| `structure.internal_burrow_edges.importer` | path (hashed when hashing is on) |
| `structure.internal_burrow_edges.imported` | path (hashed when hashing is on) |
| `structure.name_collisions.name` | path (hashed when hashing is on) |
| `structure.name_collisions.paths` | path (hashed when hashing is on) |
| `structure.packages.id` | path (hashed when hashing is on) |
| `structure.package_edges.from` | path (hashed when hashing is on) |
| `structure.package_edges.to` | path (hashed when hashing is on) |
| `behaviour.reason` | dropped |
| `behaviour.static_modularity_projection` | dropped |
| `behaviour.static_history_disagreement.finding` | token |
| `behaviour.static_history_disagreement.recommendation` | dropped |
| `behaviour.hidden_coupling_findings.finding` | token |
| `behaviour.hidden_coupling_findings.recommendation` | dropped |
| `behaviour.refactor_boundaries.finding` | token |
| `behaviour.refactor_boundaries.recommendation` | dropped |
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
| `understanding.reason` | dropped |
| `understanding.authorship_class_by_path.{}` | token |
| `understanding.orphaned_understanding` | path (hashed when hashing is on) |
| `understanding.modules.authorship_class` | token |
| `understanding.modules.finding` | token |
| `understanding.modules.recommendation` | dropped |
| `derived_findings.name` | token |
| `derived_findings.paths` | path (hashed when hashing is on) |
| `derived_findings.action` | dropped |
| `derived_findings.reviewed.path` | path (hashed when hashing is on) |
| `derived_findings.reviewed.completed_sha` | dropped |
| `derived_findings.reviewed.note` | dropped |
| `attention.findings` | token |
| `prescribed_actions.findings` | token |
| `prescribed_actions.action` | dropped |
| `findings_markdown` | dropped |
| `keyhole_summary.concerns.name` | token |
| `keyhole_summary.summary_text` | dropped |

## layer_grades

| Field | Kept as |
|---|---|
| `layer_grades.reason` | dropped |
| `layer_grades.instructions_grade` | token |
| `layer_grades.instruction_presence` | token |

## level

| Field | Kept as |
|---|---|
| `level.levels_version` | token |
| `level.counted` | token |
| `level.listed` | token |
| `level.not_measured.id` | token |
| `level.not_measured.reason` | token |
| `level.conditions.id` | token |
| `level.conditions.row` | token |
| `level.conditions.grade` | token |
| `level.conditions.state` | token |
| `level.conditions.reason` | token |
| `level.conditions.polarity` | token |
| `level.conditions.source` | token |
| `level.conditions.why` | dropped |
| `level.next_step.blockers.kind` | token |
| `level.next_step.blockers.gate` | token |
| `level.next_step.blockers.condition` | token |
| `level.next_step.blockers.state` | token |
| `level.next_step.blockers.reason` | token |
| `level.conditions.disposition` | token |
| `level.conditions.exclusion_kind` | token |
| `level.next_step.blockers.disposition` | token |
| `level.next_step.blockers.exclusion_kind` | token |
| `level.held_above` | token |
| `level.in_place.row` | token |
| `level.in_place.gates` | token |
| `level.in_place.why` | dropped |
| `level.excluded.id` | token |
| `level.excluded.kind` | token |
| `level.excluded.source` | token |
| `level.tags.tag` | token |
| `level.tags.not_measured.id` | token |
| `level.tags.not_measured.reason` | token |
| `level.tags.countable_not_measured.id` | token |
| `level.tags.countable_not_measured.reason` | token |

## liveness_blocks

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
| `observability.boundary` | dropped |
| `observability.instrumented.signals` | dropped |
| `observability.instrumented.kinds` | token |
| `observability.discoverable.signals` | dropped |
| `observability.reachable.signals` | dropped |
| `capability_offers.build_system` | token |
| `capability_offers.build_files` | path (hashed when hashing is on) |

## mutation_not_run_cap

| Field | Kept as |
|---|---|
| `mutation_not_run_cap.max_layer6_band` | token |
| `mutation_not_run_cap.annotation` | dropped |

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

## offers_block

| Field | Kept as |
|---|---|
| `offers.type` | token |
| `offers.status` | token |
| `offers.reason` | token |
| `offers.tool` | token |

## org_security_policy

| Field | Kept as |
|---|---|
| `org_security_policy.reason` | reason code |
| `org_security_policy.path` | dropped |

## owner_exclusions

| Field | Kept as |
|---|---|
| `owner_exclusions.exclusions.id` | token |
| `owner_exclusions.exclusions.kind` | token |
| `owner_exclusions.exclusions.reason` | dropped |
| `owner_exclusions.exclusions.source` | token |
| `owner_exclusions.exclusions.excluded_by` | dropped |
| `owner_exclusions.exclusions.excluded_at` | token |
| `owner_exclusions.exclusions.version` | token |
| `owner_exclusions.skipped.id` | dropped |
| `owner_exclusions.skipped.problem` | token |
| `owner_exclusions.disclosures` | dropped |

## pack_checks

| Field | Kept as |
|---|---|
| `packs.name` | token |
| `packs.version` | token |
| `packs.sha256` | external id (hashed when hashing is on) |

## plugin_version

| Field | Kept as |
|---|---|
| `plugin_version` | token |

## promissory_markers

| Field | Kept as |
|---|---|
| `promissory_markers.reason` | dropped |
| `promissory_markers.top_offenders.family` | token |
| `promissory_markers.top_offenders.text` | dropped |
| `promissory_markers.top_offenders.commit` | dropped |
| `promissory_markers.stale_by_file.{}.families` | dropped |

## review_automation

| Field | Kept as |
|---|---|
| `review_automation.reason` | dropped |
| `review_automation.kinds` | token |
| `review_automation.matches.kind` | token |
| `review_automation.matches.rule` | token |
| `review_automation.matches.reviewer` | token |
| `review_automation.matches.path` | path (hashed when hashing is on) |

## review_evidence

| Field | Kept as |
|---|---|
| `review_evidence.reason` | dropped |

## review_reality

| Field | Kept as |
|---|---|
| `review_reality.reason` | dropped |
| `review_reality.sequence` | token |
| `review_reality.bot_sequence` | token |

## run_header

| Field | Kept as |
|---|---|
| `run_id` | token |
| `artifact_schema_version` | token |
| `schema_version` | token |
| `prior_schema_version` | token |
| `prior_plugin_version` | token |
| `run_date` | token |
| `scope` | path (hashed when hashing is on) |
| `scope_slug` | path (hashed when hashing is on) |
| `tool_versions.*` | token |
| `prior_tool_versions.*` | token |
| `diff_version_note` | dropped |
| `measured_commit.committed_date` | token |
| `measured_commit.head_sha` | dropped |
| `measured_commit.head_short` | dropped |
| `measured_commit.subject` | dropped |
| `measured_commit.upstream` | dropped |
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
| `stats_summary.complexity_unreliable.path` | path (hashed when hashing is on) |
| `stats_summary.complexity_unreliable.language` | token |
| `pruned_hotspots` | path (hashed when hashing is on) |
| `retired_excluded_hotspots` | path (hashed when hashing is on) |
| `dropped_first_flagged` | path (hashed when hashing is on) |
| `provisional_first_flagged` | path (hashed when hashing is on) |
| `instructions_grade` | token |
| `instruction_files.{}.grade` | token |
| `instruction_files.{}.findings.kind` | token |
| `instruction_files.{}.findings.reference` | dropped |
| `instruction_files.{}.findings.reason` | dropped |
| `prior_findings_snapshot.findings.name` | token |
| `prior_findings_snapshot.findings.paths` | path (hashed when hashing is on) |
| `prior_level.levels_version` | token |
| `prior_level.tags.tag` | token |
| `prior_level.tags.not_measured.id` | token |
| `prior_level.tags.not_measured.reason` | token |
| `prior_level.tags.countable_not_measured.id` | token |
| `prior_level.tags.countable_not_measured.reason` | token |
| `prior_level.reason` | token |
| `prior_level.baseline.run_id` | token |
| `prior_level.baseline.head_sha` | token |
| `prior_level.baseline.held_gates` | token |
| `prior_level.pruning.path` | path (hashed when hashing is on) |
| `prior_level.pruning.completed_sha` | token |
| `prior_level.pruning.gates` | token |
| `prior_level.pruning.credited_at` | token |
| `prior_level.pruning.merged` | token |
| `prior_level.models.*.levels_version` | token |
| `prior_level.models.*.tags.tag` | token |
| `prior_level.models.*.tags.not_measured.id` | token |
| `prior_level.models.*.tags.not_measured.reason` | token |
| `prior_level.models.*.tags.countable_not_measured.id` | token |
| `prior_level.models.*.tags.countable_not_measured.reason` | token |
| `prior_level.models.*.baseline.run_id` | token |
| `prior_level.models.*.baseline.head_sha` | token |
| `prior_level.models.*.baseline.held_gates` | token |
| `prior_level.models.*.packs.name` | token |
| `prior_level.models.*.packs.version` | token |
| `prior_level.models.*.packs.sha256` | external id (hashed when hashing is on) |

## scan_plan_stamp

| Field | Kept as |
|---|---|
| `scan_plan.model` | dropped |
| `scan_plan.ran` | token |
| `scan_plan.not_run` | token |

## secrets_guard

| Field | Kept as |
|---|---|
| `secrets_guard.pre_commit.file` | path (hashed when hashing is on) |
| `secrets_guard.pre_commit.tools` | token |
| `secrets_guard.ci.files` | path (hashed when hashing is on) |
| `secrets_guard.ci.rules_unevaluated` | token |
| `secrets_guard.ci.tools` | token |
| `secrets_guard.enforcement.reason` | reason code |
| `secrets_guard.enforcement.source` | token |
| `secrets_guard.sources.source` | token |
| `secrets_guard.sources.grade` | token |

## sensitive_instruction_content

| Field | Kept as |
|---|---|
| `sensitive_instruction_content.{}.category` | token |
| `sensitive_instruction_content.{}.evidence` | dropped |

## shared_tooling

| Field | Kept as |
|---|---|
| `shared_tooling.reason` | dropped |
| `shared_tooling.ci_includes.kind` | token |
| `shared_tooling.ci_includes.source` | dropped |
| `shared_tooling.ci_includes.id` | external id (hashed when hashing is on) |
| `shared_tooling.ci_includes.ref` | token |
| `shared_tooling.marketplaces.kind` | token |
| `shared_tooling.marketplaces.name` | dropped |
| `shared_tooling.marketplaces.source` | dropped |
| `shared_tooling.marketplaces.id` | external id (hashed when hashing is on) |
| `shared_tooling.ci_source_ids` | external id (hashed when hashing is on) |
| `shared_tooling.marketplace_ids` | external id (hashed when hashing is on) |

## since_last_run

| Field | Kept as |
|---|---|
| `since_last_run.previous_run_id` | token |
| `since_last_run.previous_commit` | token |
| `since_last_run.closed.action` | dropped |
| `since_last_run.closed.condition` | token |
| `since_last_run.closed.finding` | token |
| `since_last_run.closed.completed_sha` | token |
| `since_last_run.gates_gained` | token |
| `since_last_run.gates_lost` | token |
| `since_last_run.next_blocker` | token |
| `since_last_run.lines` | dropped |
| `since_last_run.markdown` | dropped |
| `since_last_run.reason` | token |
| `since_last_run.closed_reason` | token |
| `since_last_run.pruned.path` | path (hashed when hashing is on) |
| `since_last_run.pruned.completed_sha` | token |
| `since_last_run.pruned.gates` | token |
| `since_last_run.pruned.credited_at` | token |
| `since_last_run.pruned.merged` | token |
| `since_last_run.pruning.path` | path (hashed when hashing is on) |
| `since_last_run.pruning.completed_sha` | token |
| `since_last_run.pruning.gates` | token |
| `since_last_run.pruning.credited_at` | token |
| `since_last_run.pruning.merged` | token |

## skill_disclosure

| Field | Kept as |
|---|---|
| `skill_disclosure.files.path` | path (hashed when hashing is on) |
| `skill_disclosure.files.kind` | token |
| `skill_disclosure.files.breaks` | token |
| `skill_disclosure.files.references.target` | path (hashed when hashing is on) |

## skill_library

| Field | Kept as |
|---|---|
| `skill_library.reason` | reason code |
| `skill_library.skills.path` | path (hashed when hashing is on) |
| `skill_library.skills.location` | path (hashed when hashing is on) |
| `skill_library.invalid_files` | path (hashed when hashing is on) |
| `skill_library.shared_by_symlink.path` | path (hashed when hashing is on) |
| `skill_library.shared_by_symlink.target` | path (hashed when hashing is on) |
| `skill_library.lock_files` | path (hashed when hashing is on) |
| `skill_library.hashed_manifests` | path (hashed when hashing is on) |

## skill_personal_data

| Field | Kept as |
|---|---|
| `skill_personal_data.files.path` | path (hashed when hashing is on) |
| `skill_personal_data.files.kinds` | token |
| `skill_personal_data.kinds_detected` | token |

## skill_script_tests

| Field | Kept as |
|---|---|
| `skill_script_tests.skills.path` | path (hashed when hashing is on) |
| `skill_script_tests.skills.tested_elsewhere` | path (hashed when hashing is on) |

## skills

| Field | Kept as |
|---|---|
| `skill_files` | path (hashed when hashing is on) |

## stale_hubs

| Field | Kept as |
|---|---|
| `stale_hubs.subject_method` | token |
| `stale_hubs.subject_roots` | path (hashed when hashing is on) |
| `stale_hubs.subject_match` | token |
| `stale_hubs.subject_paths` | path (hashed when hashing is on) |
| `stale_hubs.base_doc_dir` | path (hashed when hashing is on) |
| `stale_hubs.confidence` | token |
| `stale_hubs.last_change_basis` | token |
| `stale_hubs.last_commit_days_bound` | token |
| `stale_hubs.provenance.method` | token |
| `stale_hubs.provenance.sources` | path (hashed when hashing is on) |
| `stale_hubs.provenance.generated_by` | dropped |

## structure_drift_block

| Field | Kept as |
|---|---|
| `structure_drift.reason` | reason code |
| `structure_drift.tier_0.empty_ownership_patterns.pattern` | dropped |
| `structure_drift.tier_0.empty_ownership_patterns.declared_in` | dropped |
| `structure_drift.tier_0.empty_ownership_patterns.owners` | dropped |
| `structure_drift.tier_1.static_lens_reason` | token |

## test_focus

| Field | Kept as |
|---|---|
| `test_focus.entries.risk_band` | token |
| `test_focus.entries.test_signal` | token |
| `test_focus.entries.suggested_action` | token |
| `test_focus.entries.hollow_heuristic_kinds` | token |

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

## uninstall_instructions_path

| Field | Kept as |
|---|---|
| `uninstall_instructions_path` | dropped |

## unit_levels

| Field | Kept as |
|---|---|
| `unit_levels.unit_count_reason` | token |
| `unit_levels.levels_version` | token |
| `unit_levels.units.path` | path, dropped unless hashing is on (then hashed) |
| `unit_levels.units.inherited` | token |
| `unit_levels.units.conditions.id` | token |
| `unit_levels.units.conditions.state` | token |
| `unit_levels.units.conditions.origin` | token |
| `unit_levels.units.conditions.reason` | token |
| `unit_levels.units.conditions.why` | dropped |
| `unit_levels.units.next_step.blockers.kind` | token |
| `unit_levels.units.next_step.blockers.gate` | token |
| `unit_levels.units.next_step.blockers.condition` | token |
| `unit_levels.units.next_step.blockers.state` | token |
| `unit_levels.units.next_step.blockers.reason` | token |
| `unit_levels.units.next_step.blockers.disposition` | token |
| `unit_levels.units.next_step.blockers.exclusion_kind` | token |

## unit_readings

| Field | Kept as |
|---|---|
| `unit_readings.unit_count_reason` | token |
| `unit_readings.unit_blocks` | token |
| `unit_readings.units.path` | path, dropped unless hashing is on (then hashed) |
| `unit_readings.units.kind` | token |
| `unit_readings.units.conditions.id` | token |
| `unit_readings.units.conditions.origin` | token |

## unit_rollup

| Field | Kept as |
|---|---|
| `unit_rollup.unit_count_reason` | token |
| `unit_rollup.unlocks.gate` | token |
| `unit_rollup.unlocks.scope` | token |
| `unit_rollup.learn_from.unit` | path, dropped unless hashing is on (then hashed) |
| `unit_rollup.learn_from.gate` | token |
| `unit_rollup.learn_from.names` | path, dropped unless hashing is on (then hashed) |
| `unit_rollup.lines` | dropped |
| `unit_rollup.markdown` | dropped |

## units

| Field | Kept as |
|---|---|
| `units.source` | token |
| `units.unit_count_reason` | token |
| `units.units.path` | path, dropped unless hashing is on (then hashed) |
| `units.units.kind` | token |
| `units.units.origin` | token |
| `units.units.ecosystem` | token |
| `units.units.workspace` | dropped |
| `units.units.name` | dropped |
| `units.aggregators.path` | dropped |
| `units.aggregators.ecosystem` | token |
| `units.skipped.path` | dropped |
| `units.skipped.problem` | token |
| `units.unreadable_manifests` | dropped |

## untracked_instruction_files

| Field | Kept as |
|---|---|
| `untracked_instruction_files` | path (hashed when hashing is on) |

## fleet-data.json (the fleet skill)

Patterns from the top of `fleet-data.json`, not of a run-context. A
unit or repository key is always kept (hashed, spaces allowed, under
path hashing); under hashing no name, owner or web URL is written.
The run stamp (`run.*`) and maturity-model fields (`model.*`, the row ids,
`repos.rows.signal`) come from the run and the `--levels` file and are
kept as written, as is a criterion's `why` (`repos.rows.why`,
`opportunities.biggest_lift.why`), the check manifest's own text. A string
`repos.rows.value` that is not a token is written `set`.

| Field | Kept as |
|---|---|
| `views.view` | token |
| `views.units.key` | path (hashed when hashing is on) |
| `views.units.parent` | path (hashed when hashing is on) |
| `views.units.view` | token |
| `views.units.kind` | token |
| `views.units.source` | token |
| `views.units.name` | dropped |
| `views.units.owners` | dropped |
| `repos.key` | path (hashed when hashing is on) |
| `repos.status` | token |
| `repos.reason` | token |
| `repos.web_url` | external id (hashed when hashing is on) |
| `repos.owners` | dropped |
| `repos.units.view` | token |
| `repos.units.unit` | path (hashed when hashing is on) |
| `repos.rows.status` | token |
| `repos.rows.value` | token |
| `repos.rows.reason` | token |
| `opportunities.biggest_lift.repos` | path (hashed when hashing is on) |
| `opportunities.shared_templates.repos` | path (hashed when hashing is on) |
| `opportunities.shared_templates.source` | path (hashed when hashing is on) |
| `opportunities.shared_templates.kind` | token |
| `repos.exclusions.kind` | token |
| `repos.exclusions.source` | token |
| `repos.exclusions.reason` | dropped |
| `repos.gate_blockers.gate` | token |
| `repos.gate_blockers.state` | token |
| `repos.gate_blockers.disposition` | token |
| `repos.tags.tag` | token |
| `tag_profile.tag` | token |
| `views.units.tags.cells.tag` | token |
| `repos.unit_rows.key` | path, dropped unless hashing is on (then hashed) |
| `repos.unit_rows.next_blocker.gate` | token |
| `repos.unit_rows.next_blocker.state` | token |
| `repos.unit_rows.next_blocker.disposition` | token |
| `repos.unit_rows.tags.tag` | token |
| `repos.unit_rows.kind` | token |
| `repos.unit_rows.learn_from` | path, dropped unless hashing is on (then hashed) |
| `repos.learn_from` | path, dropped unless hashing is on (then hashed) |
