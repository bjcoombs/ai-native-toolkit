# skills/assess/tests

Test suites for the `/assess` deterministic engine. Tests live here, co-located with
the scripts they pin. When a source module changes, its test file is expected to change
in the same commit - this is intentional co-change, not accidental coupling.

## Test/source co-change seam

The suites below are expected to move with the engine. A reviewer seeing
`test_doc_graph.py` and `lib/doc_graph.py` in the same diff is looking at the normal
edit cycle, not a layering violation. Keep tests co-located.

The three highest-frequency co-change pairs in the git history are:

- **`test_assess_core.py` / `assess_core.py`** - the orchestrator and its end-to-end
  harness. Every time the core adds a signal or changes the `run-context.json` schema,
  both files move.
- **`test_complexity_treemap.py` / `complexity-treemap.py`** - the heatmap CLI and its
  filter, version-stamp and sidecar tests.
- **`test_doc_graph.py` / `lib/doc_graph.py`** - the navigability graph is the
  foundation for Layer 0 and feeds both the staleness and the understanding analysis,
  so its contract tests are touched on most doc-analysis changes.

---

## Suite / source mapping

Every `test_*.py` file in this directory has a row below.

### Orchestrator suites

| Suite | Pins |
|---|---|
| `test_assess_core.py` | `scripts/assess_core.py` - end-to-end orchestrator; drives `build_run_context` without running lizard/scc; tests of code that moved into a `lib/` module sit in that module's suite below |
| `assess_core_helpers.py` | repo-seeding helpers shared by `test_assess_core.py` and the lib suites split from it (not a test module) |
| `test_assess_finalize.py` | `scripts/assess_finalize.py` - LLM write-back; placeholder substitution in `log.md` and `hotspots/*.md` |
| `test_assess_gate.py` | `scripts/assess_gate.py` - CI regression gate; complexity and containment threshold checks and exit codes |
| `test_findings_snapshot.py` | `lib/findings_snapshot.py` + `scripts/assess_gate.py` - the committed `findings.json`, the method-version comparison and the gate note it prints |
| `test_assess_report.py` | `scripts/assess_report.py` - deterministic report renderer; template substitution, section renderers, conditional fallbacks |
| `test_emit_workflow.py` | `scripts/assess_emit_workflow.py` - CLI wrapper for the frozen-harness workflow emitter; default derivation, arg parsing, path filters and the path-filter default |
| `test_decomposition_parity.py` | `scripts/assess_core.py` + `scripts/assess_report.py` - parity harness; guards that the deterministic pipeline produces byte-for-byte identical output after the Part 3 SKILL.md decomposition |
| `test_complexity_treemap.py` | `scripts/complexity-treemap.py` - build-artifact filter, plugin version stamp, stats-sidecar enrichment, the render summary, CLI argument and scope checks, and the `plural()` count helper (heavy deps are stubbed) |

### lib/ suites

| Suite | Pins |
|---|---|
| `test_doc_graph.py` | `lib/doc_graph.py` - doc link-graph, link parsing (unit level: `test_doc_links.py`), orphan detection, connectivity, MOC validation, doc->code edges |
| `test_doc_links.py` | `lib/doc_links.py` - link pass called directly: wikilink and markdown-link edges, ghosts, directory links, machine-link counts, code-span skipping, ambiguity, fence stripping |
| `test_doc_graph_layout.py` | `lib/doc_graph_layout.py` - radial shells, node classification, broken-link ghost grouping |
| `test_keyhole_hidden_coupling.py` | `lib/keyhole_signals.py` - containment by directory, static-modularity projection, behaviour block, coupled pairs on hidden_coupling, structure-drift seams |
| `test_keyhole_blocks.py` | `lib/keyhole_signals.py` - documentation, understanding and runtime run-context blocks |
| `test_keyhole_attention.py` | `lib/keyhole_signals.py` - `assemble_findings` in `FINDING_ORDER`, candidate dead weight, attention ranking and tie-break, `FINDING_MODES` |
| `test_keyhole_render.py` | `lib/keyhole_signals.py` - findings markdown, keyhole summary, prescribed actions |
| `test_keyhole_untrusted_hotspot.py` | `lib/keyhole_signals.py` - E1 untrusted hotspots, E2 test-to-code mapping |
| `test_keyhole_accretion.py` | `lib/keyhole_signals.py` - accretion_ratchet finding |
| `test_keyhole_override_contradicts.py` | `lib/keyhole_signals.py` - override_contradicts_signals finding |
| `test_keyhole_suppression.py` | `lib/keyhole_signals.py` - degenerate churn, config excludes, archive paths, pruning of renamed or deleted paths |
| `keyhole_helpers.py` | shared `integrate()` fixtures for the `test_keyhole_*.py` files (not a test module) |
| `test_change_coupling.py` | `lib/change_coupling.py` - B1 change-coupling pairs, B2 containment ratio, B4 authorship; synthetic git histories built in tmp dirs |
| `test_coupling_analysis.py` | `lib/coupling_analysis.py` - B3 static-vs-historical disagreement; hidden-coupling, bleeding-module, and refactor-boundary classification with mocked inputs |
| `test_doc_complexity_join.py` | `lib/doc_complexity_join.py` - Signal C: doc_value formula, slop-doc guard, threshold behaviour; mocked complexity-stats and staleness inputs |
| `test_doc_ownership.py` | `lib/doc_ownership.py` - the one doc-to-code ownership rule: spec expansion per method, the README-beside-design.md tie-break through both blocks, one `subject_code_count`, the export cap, scoped-run keys |
| `test_rename_churn.py` | `lib/git_churn.py` + `lib/doc_staleness.py` - churn counts that follow renames (issue #425): parity with `git log --follow`, twin histories with and without a rename giving the same finding, and low confidence when the rename map is incomplete |
| `test_shallow_history.py` | `lib/git_churn.py` + `lib/doc_staleness.py` - shallow clones (issue #544): a lower-bound age at the boundary, the exact age on a full clone, the boundary never counted as bulk, the truncation and boundary date in the block, the stale-hub copy and the report label |
| `test_doc_staleness.py` | `lib/doc_staleness.py` - doc->code association (base-doc, parallel docs/, code links, repo-wide fallback), churn-relative staleness ratios, and the lying-map rule that counts only the subject's commits after the doc's last edit |
| `test_doc_modularity.py` | `lib/doc_staleness.py` - the `modularity` block: size-weighted coverage, the large-repo flag, and a base-doc directory ratio that counts only code directories |
| `test_structure_graph.py` | `lib/structure_graph.py` - A1 footprint additivity, A2 SCCs and Q range, A3 front-door vs burrow, A4 cut-lines, graceful degradation |
| `test_understanding_analysis.py` | `lib/understanding_analysis.py` - B4 human anchor + intent source, velocity clock (D2), orphaned-understanding classification; both pure-logic (mocked) and git-integration variants |
| `test_liveness_scan.py` | `lib/liveness_scan.py` - dead-code tool output parsers, observability rungs, graceful degradation when tools are absent |
| `test_test_pressure.py` | `lib/test_pressure/` - mutation tier output parsing, cheap heuristics (test/source ratio, assertion density, gap signal) |
| `test_ci_workflow.py` | `lib/ci_workflow.py` - template substitution (version, branch, tool steps, path filters), path-filtered workflow detection, literal-dollar escaping, YAML well-formedness, and the `actions/checkout` pin matching this repo's gate workflow and `README.md` |
| `test_stats_diff.py` | `lib/stats_diff.py` - hotspot transition classification (graduated, regressed, restructured, new, persistent; restructured when the worst function falls while summed complexity rises) and sidecar loading |
| `test_stats_rows.py` | `lib/stats_rows.py` + `scripts/complexity-treemap.py` - the top-10 lists and the uncapped `files` key, that the doc-complexity join sees a complex file outside every top list, and the one-time diff disarm from the stats schema bump |
| `test_wiki_writer.py` | `lib/wiki_writer.py` - wiki file rendering (index, log, hotspot pages) and HotspotEntry / LogEntry dataclass behaviour |
| `test_git_commit_info.py` | `lib/git_churn.py` (`git_commit_info`) - commit snapshot with SHA/timestamp for staleness warnings |
| `test_instruction_bloat.py` | `lib/agent_instructions_grader.py` - score on always-on size and share (#560): always-on lines past 200, fetch-on-need lines, neither gated on the share flag, B cap past 400 loaded lines, and the grade matching its `instruction_placement` record |
| `test_instruction_grading_robustness.py` | `lib/agent_instructions_grader.py`, `lib/instruction_placement.py` - wording and padding inside a section never lower the penalty (#619): prose padding a command fence, heading renames and `Never` lines on a size-signalled section, a section crossing `LONG_SECTION_MIN_LINES` at the relocatable cap or near the B cap, and a property sweep over always-on to fetch-on-need moves |
| `test_instruction_placement.py` | `lib/instruction_placement.py` - section splitting, the always-on and fetch-on-need signals (an absolute 60-line `long_section` in a file with two headed sections, `scoped_paths` never pointing a nested file at its own subtree), suggested homes and the `loaded_in_full` list, including root `agents/*.md` agent definitions in a repo with no plugin manifest and the documents kept out of it (#559, #560, #662) |
| `test_command_resolver.py` | `lib/command_resolver.py` - command extraction and per-runner resolution (resolved / missing / external / unknown) |
| `test_command_index.py` | `lib/command_index.py` - the per-repo target index: config parsing, tracked files, lenient families |
| `test_instruction_content.py` | `lib/instruction_content.py` - stale path and symbol references, directory trees, overview headings, README overlap |
| `test_accretion_ratchet.py` | `lib/accretion_ratchet.py` - the accretion-ratchet scanner (files that only ever grow) |
| `test_accretion_ratchet_threshold.py` | `lib/accretion_ratchet.py` - regression: the caller's `--deletion-threshold` is the cut applied |
| `test_agent_instructions_grader.py` | `lib/agent_instructions_grader.py` - heuristic grading of agent instruction files, the verified-commands vs directive-monolith fixture pair, findings |
| `test_agent_harness.py` | `lib/agent_harness.py` - the committed agent harness inventory and its secret-exposure findings: a planted fixture with one of each finding kind, a clean fixture with the near misses, and this repository |
| `test_agent_environment.py` | `lib/agent_environment.py` - the agent working-environment block: the CI-only, eligible-for-Present and malformed `copilot-setup-steps.yml` fixtures, each setup source, check-command classification, lockfile rows, and the `gh` CI-duration probe against a fake `gh` |
| `test_check_command.py` | `lib/check_command.py` - the lint classifier: linters run directly or through `uv run` / `npx` / `python -m`, lint targets of runners and scripts, and installs or bare list items that are not lint |
| `test_ci_checks.py` | `lib/ci_checks.py` - whether CI runs a test or lint command: the line classifier (checks, linters, YAML keys and echo that are not), the no-CI, no-check and check cases, and include-only CI reading unknown |
| `test_ai_usage.py` | `lib/ai_usage.py` and `lib/agent_identity.py` - exact trailer counts on a dated fixture (human-only, human plus agent, an automation `[bot]` not counted, Copilot, out-of-window and merge commits), `--scope` subtree counting, automation-bot exclusion, window resolution, the no-identity-string check, and a distinct degrade reason for no repository, a shallow clone, missing git and a timeout |
| `test_agent_building.py` | `lib/agent_building.py`, `lib/agent_building_vocab.py`, `lib/agent_manifests.py` - one fixture repository per class, the class rule table, the vocabulary and manifest readers, test-file and untracked exclusion, counts-only round trip (#689) |
| `test_fleet_agent_building.py` | `lib/fleet_agent_building.py` - per-group class counts, the cluster rule (Jaccard, pairing, connected components) and the counts-only, unnamed cluster output (#689) |
| `test_ai_usage_authors.py` | `lib/ai_usage.py` author counts and `lib/agent_identity.py`'s `AuthorMatcher` (#695) - exact agent-authored, either, dependency-bot and token-user counts on a fixture, config rules credited by position (first match), token-user promotion, dependency bots never agents, display name only under `field = "name"`, the username form and GitLab token-user recognition, `[ai_usage]` validation messages that never quote a pattern, the fleet-then-repository rule order end to end through `signals.collect`, counts-only output with no pattern or identity, and a guard that the shared matchers are unchanged |
| `test_identity_candidates.py` | Bot and agent account discovery (#765): `lib/account_discovery.py`, the `[identities]` section (`lib/fleet_config.py`, `ConfirmedIdentities` in `lib/agent_identity.py`) and `scripts/identity_candidates.py` on the issue's fixture - only `claude[bot]` counts before confirmation, the three automated-looking accounts await confirmation with their evidence, confirming moves `cursor` into the agent count and a rerun lists nothing, a confirmed class beats every pinned rule, entries are literal, weak evidence alone makes no candidate, the fleet listing aggregates and `--confirm` writes the fleet file, and no identity reaches the block or counts-only output |
| `test_review_automation.py` | `lib/review_automation.py` and `shared_tooling.step_uses` - AI review committed in CI (pinned actions and GitLab components, the `[review] ai_review_components` list, review app config files, tracked-only credit), the `review_evidence` truth table, a headless Layer 7 floor of 1 on a GitHub and a GitLab fixture, and a failed platform read keeping the offline credit |
| `test_shared_tooling.py` | `lib/shared_tooling.py` - shared CI sources and agent marketplaces: GitLab includes, GitHub reusable workflows, marketplace manifests and `extraKnownMarketplaces`, tracked-only credit, and the distinct-marketplace roll-up fixture from #550 |
| `test_agent_ops.py` | `lib/agent_ops.py` - agent-operations guardrail scan (Layer 8 evidence) |
| `test_anomaly_detector.py` | `lib/anomaly_detector.py` - anomaly detection on run output |
| `test_archetype.py` | `lib/archetype.py` - repository archetype detection |
| `test_assess_config.py` | `lib/assess_config.py` - the working-notes keys in `.assess/config.toml` |
| `test_badge.py` | `lib/badge.py` - the shields.io endpoint badge and its producers |
| `test_claude_config.py` | `lib/claude_config.py` - legacy command files and Claude Code frontmatter the runtime ignores, plugin path overrides, scope, and the unchanged run-context of an unconfigured repo |
| `test_config_drift.py` | `lib/config_drift.py` + `lib/gh_cli.py` - committed GitHub-config snapshots against the live setting, and the shared `gh` helper |
| `test_review_reality_gitlab.py` | `lib/gitlab_cli.py` + the GitLab path of `lib/review_reality.py` - provider detection, `glab` refusal mapping, merged merge request approvals |
| `test_gitlab_protected_branches.py` | `lib/gitlab_cli.branch_protection` + the GitLab paths of `lib/config_drift.py` and `lib/review_reality.py` - wildcard protected-branch matching, 403/404 as unavailable, GitLab protected-branch snapshots, rules scoped to all protected branches |
| `test_context_blocks.py` | `lib/context_blocks.py` - keyhole, stale-hub, liveness, coverage-report, accretion, structure-drift and exclusion/pruning blocks in run-context |
| `test_coverage_gate.py` | `lib/coverage_gate.py` - enforced coverage-threshold detector |
| `test_coverage_report.py` | `lib/coverage_report.py` - coverage-report parser |
| `test_dart_capabilities.py` | `lib/dart_capabilities.py` - Dart linting and liveness capability entries |
| `test_dart_complexity.py` | `lib/dart_complexity.py` - the approximate Dart per-function scanner |
| `test_decline_markers.py` | `lib/decline_markers.py` - decline-marker provenance and re-offer on a major bump |
| `test_diff_reliability.py` | `lib/diff_reliability.py` - cross-run diff reliability: version stamps, tool-version changes, schema changes |
| `test_doc_provenance.py` | `lib/doc_provenance.py` - provenance-aware staleness of generated docs |
| `test_doc_claims.py` | `lib/doc_claims.py` - claim checks across the doc set and cross-document contradictions |
| `test_evidence_check.py` | `lib/evidence_check.py` - deterministic re-check of scorer evidence |
| `test_scorer_evidence.py` | `lib/evidence_check.py` - the layer scorer's structured evidence contract |
| `test_gap_actions.py` | `lib/gap_actions.py` - the deterministic Top 3 gap candidates |
| `test_gate_cost.py` | `lib/gate_cost.py` - the CI gate's Actions cost estimate |
| `test_generated_files.py` | `lib/generated_files.py` - generated-file header sniff and long-line detector |
| `test_git_churn.py` | `lib/git_churn.py` - churn-degeneracy detector |
| `test_instruction_claims.py` | `lib/instruction_claims.py` - verifying claims in agent instruction files |
| `test_instruction_budget.py` | `lib/instruction_budget.py` - `@import` resolution and the always-loaded budget per tool |
| `test_instruction_discovery.py` | `lib/instruction_discovery.py` - nested and path-scoped instruction files, their scopes and integrity findings (fixture monorepo) |
| `test_instruction_files.py` | `lib/instruction_files.py` - instruction-file discovery and grading, alias grade inheritance, broken references, sensitive content, ancestor cascade (through `build_run_context`) |
| `test_instruction_globs.py` | `lib/instruction_globs.py` - scope frontmatter parsing and glob matching |
| `test_interactivity.py` | `lib/interactivity.py` - the non-interactive consent contract |
| `test_jvm_capabilities.py` | `lib/jvm_capabilities.py` - the capability-driven JVM offer flow |
| `test_mutation_cap.py` | `lib/mutation_cap.py` - the `test_pressure` block shape and the Layer 6 mutation-not-run cap |
| `test_mutation_cap_partial.py` | `lib/mutation_cap.py` - the Layer 6 cap holds at Partial, and finalize refuses Present, when every mutmut 3 group with records stopped at the time budget (`partial`) |
| `test_mutation_refresh.py` | `scripts/assess_core.py --opt-in-mutation` - the opt-in pass rebuilds every block derived from `test_pressure` (E1 `untrusted_hotspot`, attention, findings markdown, summary, prescribed actions, badge), not only the block and the cap |
| `test_mutmut3.py` | `lib/test_pressure/mutmut3.py` - the mutmut 3 adapter and the mutmut version probe |
| `test_mutmut3_layout.py` | `lib/test_pressure/mutmut3.py` - the pass across package layouts: grouping by package root, the package's own config against a generated one, files read outside the package, several packages in one focus set (mutmut faked) |
| `test_mutmut3_runner.py` | `lib/test_pressure/mutmut3.py` (`_resolve_runner`) - the environment the pass runs in (package venv, uv scratch environment, PATH) and the reason naming the first import error or failing test when the baseline run fails |
| `test_ownership_parser.py` | `lib/ownership_parser.py` - ownership-map parser |
| `test_promissory_markers.py` | `lib/promissory_markers.py` - stale TODOs, suppressions and skips |
| `test_promissory_markers_literals.py` | `lib/promissory_markers.py` and `lib/python_regions.py` - marker text in Python string literals, quoted directives and config files is data, not a marker; docstrings and real comments still count and age |
| `test_raw_source.py` | `lib/raw_source.py` - raw-source subtree detection |
| `test_review_reality.py` | `lib/review_reality.py` - review-automation evidence from merged PRs |
| `test_reviewed_findings.py` | `lib/reviewed_findings.py` - a done review in the committed `.assess/actions.json` clears `self_referential_tests` from attention and the prescribed actions while the source is unchanged since `completed_sha` (the path keeps a `reviewed` note in `derived_findings`), a later source change re-flags it, entries that do not clear, the mutation refresh, counts-only, and `assess_finalize` keeping done entries under `completed` (#721) |
| `test_delivery.py` | `lib/delivery.py` + `lib/merged_sample.py` - delivery figures over a recorded, scrubbed `gh pr list` response (`fixtures/platform/`), the windowed two-read GitHub sample and one-read GitLab sample |
| `test_gitlab_notes.py` | `lib/gitlab_notes.py` - GitLab merge-request note classification (markers, agent accounts, unmarked access-token notes as automation, the author's own notes), the `gitlab_note_facts` spec and the GitLab `delivery` note figures |
| `test_platform_argv_contract.py` | every `PLATFORM_SCANS` scan through recording `gh`/`glab` stubs: each argv names its target (`--repo`, `repos/<slug>`, `users/<login>`, `--hostname`), as the argv-keyed response cache needs |
| `test_run_wiki.py` | `lib/run_wiki.py` - hotspot pages, `index.md` rows and graduation records for one run |
| `test_scan_registry.py` | `lib/scan_registry.py` - the declared scan table and the loop that runs it |
| `test_sibling_tests.py` | `lib/sibling_tests.py` - the one sibling-test resolver |
| `test_import_credit.py` | `lib/import_credit.py` - the import tier of the sibling-test probe |
| `test_structure_drift.py` | `lib/structure_drift.py` - Tier 0 path-existence structure-drift signal |
| `test_test_focus.py` | `lib/test_focus.py` - `compute_test_focus` and mutation scope |
| `test_vault_queries.py` | `lib/vault_queries.py` - vault-native navigation query parser |
| `test_wiki_state.py` | `lib/wiki_state.py` - first-flagged dates and their rekeying through the rename map |
| `test_run_setup.py` | `lib/run_setup.py` + `lib/core_scans.py` + `lib/run_publish.py` - the setup-stage scan adapters and the publish step |
| `test_scan_registry_degrade.py` | `lib/scan_registry.py` - degrade by construction: every scan-table entry without a `GATE_REASONS` reason is forced to raise and the run carries the unavailable marker downstream |
| `test_scc_coverage.py` | `lib/scc_coverage.py` - the implausibly-low scc complexity check, calibrated on real scc output for an under-counted shell script |
| `test_counts_only.py` | `lib/counts_only.py` + `lib/counts_only_rules.py` + `lib/counts_only_schema.py` - the content-free run-context mode: every typed string field has a keep/drop/path rule, and the counts-only document drops copied text |
| `test_counts_only_reason.py` | `lib/counts_only.py` `reason_token` + the `REASON` rule - a full counts-only run keeps `structure.reason` as its code (`no_import_graph_rust` on a Rust repository), never the sentence (#764) |
| `test_levels.py` | `lib/levels.py` - TOML maturity model loading and deterministic whole-level evaluation |
| `test_level_tally.py` | `lib/level_tally.py`, `lib/fleet_level.py`, `lib/fleet_level_schema.py` - the verified tally, condition states, Steps and their gates, `requires` and `[[steps]]` loading, `level.json` and the run context's `level` block, and the #744 contract fixtures through `/fleet` and `/assess` |
| `test_level_signals.py` | `lib/level_signals.py` - schema-backed signal paths and the shipped default maturity model |
| `test_block_shapes.py` | `lib/block_shapes.py` - the typed shapes of the blocks a default criterion reads, against each builder's output and the fleet golden; a misspelt `--levels` sub-path under them fails at load (#793); their counts-only reduction |
| `test_signal_inputs.py` | `lib/level_signals.py` - the producer input class of every default criterion signal, its grade and the declared predicate (#783) |
| `test_check_manifest.py` | `checks/core.toml` against the scan registry, the headless tables, `ROOTS`, the default maturity model and `level_signals.SIGNAL_INPUTS`, in both directions; the manifest against `references/check-manifest.schema.json`. The agreement test: each failure names the scan, signal, condition or finding that disagrees (`test_registry_matches_manifest` for a scan). The tables generated from the manifest (#795): the finding tables, the coupling thresholds and the counts-only `RULES` follow an edited copy of the manifest; the structure reason codes match `import_graph.STRUCTURE_REASON_CODES` |
| `test_check_manifest_loader.py` | `lib/check_manifest.py` (the strict loader: allowed keys, types, ids, the registry rules and cross-references) and `lib/check_validation.py` (structured errors and the Levenshtein suggestion, with Meridian's test tables) |
| `test_hollow_conditions.py` | `levels/default.toml` - hollow cases: the cheapest committed change that meets each condition must not be credited; a case the model still credits is `xfail(strict=True)` naming its fixing issue, and that fix removes the mark (#784) |
| `test_layer_grades.py` | `lib/layer_grades.py` - deterministic layer grades as a registered signals block, with a counts-only rule |
| `test_history_window.py` | `lib/history_window.py` - the one history-window grammar shared by `lib/git_churn.py`, `lib/ai_usage.py`, `lib/fleet_activity.py` and `lib/signals.py`, and the shallow-boundary reachability check |

### Headless signals and fleet suites

| Suite | Pins |
|---|---|
| `test_signals.py` | `scripts/assess_signals.py` + `lib/signals.py` + `lib/signals_schema.py` - the headless signals entry point and its committed JSON schema |
| `test_signals_counts_only.py` | `scripts/assess_signals.py --counts-only` - the content-free headless document validates against the schema, hashes paths and shared-tooling ids, and keeps a reason on every unavailable block |
| `test_fleet.py` | `lib/fleet.py` + `lib/fleet_schema.py` - the fleet runner core: targets, per-repository rows, isolated failures |
| `test_fleet_cli.py` | `skills/fleet/scripts/fleet.py` (relative to the repo root) - end to end over real `assess_signals` runs |
| `test_fleet_groups.py` | `lib/fleet_groups.py` - the group roll-up with maturity model levels and joined group settings |
| `test_fleet_labels.py` | `lib/fleet_labels.py` + `skills/fleet/scripts/fleet_labels.py` - the closed label set, the `labels.jsonl` schema and its join to `repos.jsonl`; labels never touch a maturity model score |
| `test_fleet_next_steps.py` | `lib/fleet_next_steps.py` + `lib/next_step_guidance.py` + `lib/fleet_view.py` + `skills/fleet/scripts/fleet_next_steps.py` - per-team next-steps pages against goldens in `fixtures/fleet_next_steps/expected/` |
| `test_fleet_heatmap.py` | `lib/fleet_view.py` + `lib/fleet_heatmap.py` + `skills/fleet/scripts/heatmap.py` - the leadership heat map SVG against goldens in `fixtures/fleet_heatmap/` |
| `fleet_heatmap_fixture.py` | builds the `fixtures/fleet_heatmap/` runs from `lib/fleet_groups.roll_up` (not a test module; regenerate with `uv run --locked python tests/fleet_heatmap_fixture.py`) |
| `test_fleet_run_stamp.py` | `lib/fleet_stamp.py` + `lib/fleet_cleanup.py` - the fleet run stamp, per-repository activity and `--delete-after` |
| `test_windows_portability.py` | Windows correctness across every runtime Python tree (`skills/*/scripts/` and `scripts/`, #712, #752): every text-mode subprocess call names UTF-8 (checked under an emulated ASCII locale), every `git ls-files -z` decode uses `surrogateescape`, every fleet writer pins `newline="\n"`, repo-relative paths render with `/`, and `lib/fleet_cleanup.py` deletes read-only files under emulated Windows deletion semantics |
| `test_fleet_legacy.py` | `lib/fleet_legacy.py` - a run written before the 2.0.0 maturity-model rename (`fixtures/fleet_legacy/`) loads, compares and counts in the history and trend |
| `test_fleet_history.py` | `lib/fleet_history.py` + `skills/fleet/scripts/fleet.py` + `skills/fleet/scripts/fleet_compare.py` - the dated run history, the `--compare` wiring and the compare CLI |
| `test_fleet_compare.py` | `lib/fleet_compare.py` - comparing two fleet runs without rescanning: level movement apart from measured-set movement, and the not-comparable reasons |
| `test_fleet_scanner_diff.py` | `lib/fleet_scanner_diff.py` - attributing a fleet comparison move to a scanner change (`scanner_changed`), an unavailable scanner diff (`toolkit_changed_unknown_scope`) or a signal one run lacks (`set`), and the CLI wiring (#774) |

### Infrastructure suites

| Suite | Pins |
|---|---|
| `test_smoke.py` | `lib/__init__.py` - confirms the lib package is importable and `__version__` is set |
| `test_golden_baseline.py` | `tests/golden.py` + dogfood fixtures - guards the regression baseline scaffolding (fixture completeness, normalization idempotency, loader correctness) used by `test_decomposition_parity.py` |
| `test_golden_svg_render.py` | `scripts/complexity-treemap.py` + `scripts/doc-graph-svg.py` - runs the real renderers and locks their colour encoding |
| `test_svg_cascade.py` | `lib/treemap_render.py` - the heatmap stylesheet never overrides a `<text>` presentation attribute (the check itself is in `svg_cascade.py`) |
| `svg_cascade.py` | the CSS-cascade check shared by `test_svg_cascade.py` and `test_golden_svg_render.py` (not a test module) |
| `test_symlink_walks.py` | the committed `skills/assess/src -> scripts` symlink (relative to the repo root) - the link exists and points at `scripts`; the treemap and the shared walks count linked files once; the mutation pass's scratch copy keeps the link, never links back into the assessed tree, ignores an inherited `GIT_DIR`, and maps focus paths through it both ways |
| `test_doc_graph_svg.py` | `scripts/doc-graph-svg.py` - the SVG honours the same excludes as the scorer; node encoding, labels and layout; title and summary counts with singular/plural nouns and no em dash; CLI argument checks |
| `test_action_contract.py` | `action.yml` (repo root) - the composite AI-readiness gate action |
| `test_assess_delivery.py` | `skills/assess-pr/SKILL.md` + `skills/assess-findings/SKILL.md` (relative to the repo root) - the delivery text contract: on a read-only target the first upstream offer is issues linking the fork-hosted report, filed items are single-concern with repository-native "Done when" checks, and the report never narrates a scorer-draft correction |
| `test_no_contributions_scan.py` | `skills/assess-pr/SKILL.md` (relative to the repo root) - extracts and runs the marked no-contributions bash block |
| `test_uninstall.py` | `references/uninstall.md` + `scripts/assess_core.py` - run-context pointer, doc completeness, and the uninstall offer |
| `test_scope.py` | `/assess <path>` monorepo scoping across `lib/doc_graph.py`, `lib/git_churn.py`, `lib/badge.py` and `lib/wiki_writer.py` |
| `test_log_supersede.py` | `lib/wiki_writer.py` - `log.md` entry replacement and re-chain across core runs and finalize |
| `test_hotspot_orphan_invariant.py` | `lib/wiki_writer.py` - executable invariant for the hotspot wiki |
| `test_self_architecture.py` | `scripts/lib/` - the inward-only layering contract and the lib `README.md` entry check |
| `test_self_dogfood.py` | the whole deterministic core - it must obey the signals it computes |

---

## Fixtures

`conftest.py` provides three fixtures: `fixtures_dir` (the path to `fixtures/`),
`tmp_assess_dir` (an empty `.assess/` with a `hotspots/` subdirectory) and `git_repo`
(a throwaway repo plus a `commit_fn` that can backdate author and committer time). At
import time it points `GIT_CONFIG_GLOBAL` and `GIT_CONFIG_SYSTEM` at the null device, so
ambient commit signing or hooks cannot break the git-backed tests.

`fixtures/` holds data, not tests; `pyproject.toml` excludes it from collection:

| Fixture | Used by |
|---|---|
| `golden/` | `golden.py`, `test_decomposition_parity.py`, `test_golden_baseline.py` - the run-context and report baselines |
| `golden-doc-repo/`, `golden-svg-repo/` | `test_golden_svg_render.py` - small repos the real renderers draw |
| `hollow_test_repo/`, `honest_test_repo/` | `test_test_pressure.py` - weak versus real tests |
| `mutmut-junitxml.xml` | `test_test_pressure.py`, `test_complexity_treemap.py` - mutation output |
| `good_instructions.md`, `bad_instructions.md`, `monolithic_instructions.md`, `lean_with_skills/` | the instruction grader and bloat suites |
| `instruction_grading/` | the #512 fixture pair: `verified_commands.md` (59 lines of commands that resolve in `repo/`) and `directive_monolith.md` (400 lines of directives, overview and a directory tree) |
| `coverage.xml`, `lcov.info` | `test_coverage_report.py` |
| `prior_stats.json`, `current_stats.json` | `test_stats_diff.py` |
| `maven_project/` | `test_jvm_capabilities.py` |
| `claude_config_plugin/` | `test_claude_config.py` - a plugin repo whose manifest overrides the skills, commands and agents paths |
| `structure_drift/` | `test_structure_drift.py` |

## Markers

`no_cover` (declared in `pyproject.toml`) pauses pytest-cov tracing for one test. It
guards wall-clock budgets: the Dart scanner's 1s pathological-input test in
`test_dart_complexity.py` would otherwise miss its budget under `--cov`. Use it rather
than widening a budget to fit the tracer.

---

## Running the suite

```bash
# From skills/assess/
uv run --locked pytest tests/ -v

# With line coverage, as CI runs it (fails below fail_under in pyproject.toml)
uv run --locked pytest -v \
  --cov=scripts --cov-report=term --cov-report=xml:coverage.xml
```

From the repository root, `make test-assess` runs the coverage command and `make check`
runs every required CI check. The dev group in `pyproject.toml`, pinned by `uv.lock`,
supplies pytest, pytest-cov, `pyyaml`, ruff and mypy; `--locked` refuses a lock that
no longer matches `pyproject.toml` (run `uv lock` after editing it). `pyyaml` lets the
structural guards in `test_action_contract.py` parse YAML; without it they skip.
`conftest.py` neutralises global git config, so the suite needs no
`GIT_CONFIG_GLOBAL=/dev/null` prefix. Scripts run outside pytest, such as
`assess_core.py`, still read global config.
