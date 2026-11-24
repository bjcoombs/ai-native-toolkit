"""The artifact schema and method versions every /assess run stamps on its outputs.

A leaf module so the orchestrator and the lib modules that write artifacts
(the hotspot wiki pages, the index, the log, the findings snapshot) read one
constant each.

`ARTIFACT_SCHEMA_VERSION`, the run_id provenance schema stamped on every artifact a run
writes (run-context.json, the badge, the wiki pages); the version history is in the
module comment. `complexity-treemap.py` carries a copy that
`tests/test_complexity_treemap_schema_versions.py` holds equal to it.
It also holds `ASSESS_METHOD_VERSION`, the finding method version: an integer,
independent of plugin releases, bumped by one in any change that moves which findings
a run produces on unchanged code. `assess_core` stamps it in run-context.json and in
`findings.json`.

During a version freeze a change to either counter, or to another counter
`scripts/fold_changes.py` knows, adds a note file under `skills/assess/changes/`
instead of editing the history comment; the release PR folds the notes in with
`make fold` (#1011).
"""

# Artifact schema version, stamped on every artifact the run produces
# (run-context.json, the badge, the wiki pages, complexity-stats). Distinct from
# the stats-layout `schema_version` (from #244) that versions the sidecar shape
# for diff comparability: this one versions the run_id provenance envelope.
# Bumped when the cross-artifact provenance schema changes shape in a way a
# consumer must adapt to.
# 1.1.0: run-context.json doc_graph gains link_only_orphan_rate /
# link_only_reachability_pct, and its orphan_rate / reachability_pct now count
# reference edges (#353). complexity-stats.json is unchanged, so its layout
# STATS_SCHEMA_VERSION stays put and the cross-run diff stays armed.
# 1.2.0: run-context.json doc_graph gains link_parents, one
# {path, link_parent, link_entry} record per document naming the document one
# step up its link path and the entry document that path starts from, so a
# consumer can draw the strip without re-walking the graph. Additive: no
# existing key moves, and complexity-stats.json is unchanged.
# 1.3.0: run-context.json behaviour gains coupled_pairs and coupled_pairs_total
# on every hidden_coupling finding, and change_coupling_pairs_total on the block
# so a list cut by the repository-wide cap never reads as complete.
# complexity-stats.json is unchanged, so STATS_SCHEMA_VERSION stays put.
# 1.4.0: run-context.json gains nested_instructions (issue #511): nested and
# path-scoped instruction files graded with their scope, the always-loaded
# budget per tool, and the scope-integrity findings. Additive: no existing key
# moves, and complexity-stats.json is unchanged.
# 1.4.0 (also): run-context.json gains the agent_harness block (#513): the committed
# hooks, env names, apiKeyHelper and MCP servers that run on every agent
# session, and the findings that expose a secret or widen that surface.
# Additive; complexity-stats.json is unchanged.
# 1.4.0 (also): instruction files are graded on verified commands and size
# (issue #512). Each instruction_files entry and nested_instructions.files entry
# gains findings (unresolved commands, stale paths and symbols) and the
# subscores verified_commands, external_commands, unresolved_commands,
# unknown_commands, path_references_existing, stale_references,
# readme_overlap_pct, directory_trees, overview_sections, content_penalty,
# score_before_penalties and size_grade_capped. Additive: no existing key moves
# or changes type, and complexity-stats.json is unchanged.
# 1.4.0 (also): run-context.json gains agent_environment (issue #514): committed
# agent setup, the check command an instruction file names, lockfiles per
# manifest, copilot-setup-steps.yml findings, the Layer 5 Partial cap and the
# optional gh CI-duration probe. Additive; complexity-stats.json is unchanged.
# 1.5.0: run-context.json gains shared_tooling (issue #566): per repository, the
# shared CI sources its pipelines include and the agent marketplaces it hosts or
# names, each with a stable id a fleet roll-up counts distinct. Additive; no
# existing key moves, and complexity-stats.json is unchanged.
# 1.6.0: run-context.json gains assess_method_version and
# prior_findings_snapshot, and each run writes .assess/findings.json, the
# committed snapshot of finding names and paths (issue #554). Additive: no
# existing key moves, and complexity-stats.json is unchanged.
# 1.7.0: run-context.json gains instruction_placement (issue #559): each
# instruction file split on its headings, its always-on lines and share, and the
# relocatable sections with line ranges, signals and a suggested home; agent and
# SKILL.md files over 200 lines in a separate loaded_in_full list. Additive;
# complexity-stats.json is unchanged.
# 1.7.0 (also): run-context.json gains ai_usage (issue #547): the share of
# commits in the history window with an AI-assistant Co-authored-by trailer, as
# a lower bound with its window, merge rule and limits. Additive;
# complexity-stats.json is unchanged.
# 1.8.0: complexity-stats.json gains `files`, every scored file as {path, ccn},
# uncapped and sorted by path, read by the doc-complexity join (issue #556).
# Additive; run-context.json is unchanged. STATS_SCHEMA_VERSION moves 5->6, so
# the cross-run diff is disarmed once against a pre-#556 snapshot.
# 1.9.0: run-context.json gains doc_claims (issue #491): the instruction-claim
# kinds verified across the doc set, and cross-document contradictions (two
# files giving one checkable claim different values, every site cited).
# Additive; complexity-stats.json is unchanged.
# 1.10.0: each run-context.json doc_staleness.docs row gains subject_roots (the
# ownership rule's roots), base_doc_dir (the directory the doc is the base doc
# of, or null) and subject_paths (up to 20 sorted subject paths), from
# lib.docs.doc_ownership (issue #555). A repo-baseline row's subject_code_count is now
# 0 (it owns no subject), the same value the documentation block reports.
# complexity-stats.json is unchanged.
# 1.11.0: complexity-stats.json gains complexity_unreliable, the scc-only files
# whose ccn is an order of magnitude below their language's median density
# (lib.complexity.scc_coverage, issue #590), and run-context.json copies it into
# stats_summary and reports a COMPLEXITY_UNRELIABLE anomaly when it is non-empty.
# Additive. STATS_SCHEMA_VERSION moves 6->7. No ranking or finding moves.
# 1.11.0 (also): doc_staleness gains history_truncated and
# history_boundary_date, and its rows (and stale_hubs) an optional
# last_commit_days_bound: "lower" (issue #544). Additive; complexity-stats.json
# is unchanged.
# 1.12.0: instruction files are scored on always-on size and share (issue #560).
# Each instruction_files and nested_instructions.files entry gains the
# subscores always_on_lines, always_on_pct, fetch_on_need_lines,
# always_on_flagged, always_on_penalty and relocatable_penalty, and
# instruction_file_size gains always_on_lines (and the subscores and each
# instruction_placement record always_on_words). bloat_penalty is now their
# penalties' sum and size_grade_capped reads always-on lines. Additive: no key
# moves or changes type, and complexity-stats.json is unchanged.
# 1.13.0: run-context.json gains layer_grades (issue #620): instructions_grade
# and instruction_file_present as one registered block, so the headless signals
# document carries them and a maturity model can name them. Additive; the top-level
# instructions_grade key stays, and complexity-stats.json is unchanged.
# 1.14.0: each run-context.json nested_instructions claude_shadows_agents finding
# gains diff_lines, the lines that differ between the two files' comparable
# lines (body, plus any header comment only one file carries), and the
# informational finding kind mirrored_instruction_files reports a CLAUDE.md kept
# identical to its AGENTS.md below the header (issue #589; entry added by #632).
# Additive: no existing key moves, and complexity-stats.json is unchanged.
# 1.15.0: run-context.json structure gains cross_package_edges (issue #663), the
# import edges front_door_ratio is measured over; 0 marks that ratio's 1.0 as
# vacuous. Additive: no existing key moves, and complexity-stats.json is
# unchanged.
# 1.16.0: run-context.json structure gains modularity_reason (issue #682), and
# modularity_q is null when the import graph has no edges (modularity_reason
# "no_import_edges"; null when Q was measured). A reader comparing modularity_q
# as a number must skip null. complexity-stats.json is unchanged.
# 1.17.0: each run-context.json shared_tooling ci_includes entry gains
# ref_variable (issue #684): true when the ref holds a CI variable ($TPL_REF), so
# a fleet map draws that include as "pin unknown" rather than unpinned. Additive:
# ref stays null for it, and complexity-stats.json is unchanged.
# 1.18.0: 2.0.0 maturity-model rename (issue #670), a breaking field rename in
# the /fleet outputs: run.json and each groups.jsonl line carry levels_version
# (was rubric_version), and a group line holds its rows under levels (was
# rubric); compare.json earlier/later summaries carry levels_version
# (fleet_compare_version 2). Readers map the old keys (lib/fleet/fleet_legacy.py), so
# runs written before the rename still load and match in the history. Also
# documented: a run-context.json structure block reports modularity_q null in two
# cases, an import graph with no edges (modularity_reason "no_import_edges") and
# an unavailable block (available false, cause in structure.reason; the headless
# marker drops modularity_q and opens its reason with a code: no_grimp,
# no_networkx, grimp_failed or no_python_packages). run-context.json and
# complexity-stats.json are otherwise unchanged.
# 1.19.0: run-context.json gains ci_checks (issue #698): ci_files,
# check_commands, included_files, test_or_lint_runs (null when no CI line runs
# a check but a CI file pulls jobs from elsewhere) and basis. Additive, and
# complexity-stats.json is unchanged.
# 1.20.0: run-context.json gains agent_building (issue #689), an unscored
# inventory of what the repository builds with agents (SDK slugs, agent config
# kinds, counts-only code heuristic, API-client pairing, one class); /fleet
# groups.jsonl lines gain agent_building class counts and the run writes
# agent_clusters.json. Additive: no existing key moves, and complexity-stats.json
# is unchanged.
# 1.21.0: run-context.json ai_usage gains author counts (issue #695):
# agent_authored_commits/share, agent_either_commits/share,
# dependency_bot_commits, token_user_commits, agent_identity_rule_commits
# (positional, per [ai_usage] config rule) and covered_author_agents; limits
# reworded. Additive; the trailer fields are unchanged.
# 1.22.0: run-context.json structure gains language (issues #707, #708), the
# language of the measured import graph (the largest, one graph per language),
# null when none was measured. An unavailable block's reason names the dominant
# source language: no_import_graph_<language>, no_python_packages for a Python
# repository, no_source_files with no source. Additive: no existing key moves, a
# Python repository's values are unchanged, and complexity-stats.json is
# unchanged.
# 1.23.0: run-context.json doc_staleness gains summary (issue #699): integer
# measured_docs, stale_docs and stale_pct (null on a truncated or degenerate
# history), read by the default maturity model's doc-freshness row. Row paths
# are repo-relative POSIX on every platform. Additive; no finding moves, and
# complexity-stats.json is unchanged.
# 1.24.0: /fleet groups.jsonl level rows gain optional floor and full_run_only
# (issue #697): an unknown row's confirmed low bound when at least 1, and the
# missing signals only a full /assess measures (block reason not_registered or
# network). compare.json level states carry floor too. Additive: status stays
# level or unknown, fleet_group_version is unchanged, readers tolerate both keys
# being absent, and run-context.json and complexity-stats.json are unchanged.
# 1.25.0: run-context.json structure_drift.tier_1 gains optional
# static_lens_reason (issue #710), and its three static-derived counts
# (human_grouped_static_splits_count, human_split_static_fuses_count,
# human_static_agree_count) are null when set: an import graph that maps no
# module to a file (a Go graph) has no file-pair static lens. The co-change
# counts are unchanged. Readers treat a null count as unknown, never 0.
# 1.26.0: run-context.json gains review_automation and review_evidence (issue
# #696): AI review committed in CI (present, kinds, matches with kind, rule,
# reviewer, path, line, tracked) and the derived Layer 7 level 1 signal
# (present, ai_review_in_ci, reviewed_changes). Additive: no existing key moves,
# and complexity-stats.json is unchanged.
# 1.27.0: run-context.json structure.front_door_ratio is null when
# cross_package_edges is 0 (issue #760), and on an unavailable structure block:
# with no package boundary crossed there is no contract edge to measure, so the
# former 1.0 was vacuous. A reader comparing front_door_ratio as a number must
# skip null. complexity-stats.json is unchanged.
# 1.28.0: run-context.json ci_checks gains included_check_commands,
# resolved_includes, unresolved_includes and includes[] (kind, id, status,
# ref_basis, depth: GitLab includes resolved offline, issue #766), and each
# review_automation match gains depth. Additive: no existing key moves.
# 1.29.0: run-context.json structure_drift.tier_0 gains boundary_patterns (issue
# #785): the matched declarations that name a module path - every matched
# architecture-doc reference, and every CODEOWNERS glob with a literal path
# segment that owns source code under a non-dot directory (not `*`, `*.py`,
# `/.github/`, `README.md` or a docs-only path). Read by the default
# maturity model's Layer 4 level 1. Additive: total_patterns and matched_patterns
# keep their meaning, and complexity-stats.json is unchanged.
# 1.30.0: run-context.json ci_checks gains merged_config, merged_config_reason and
# merged_check_commands (issue #796): GitLab's merged CI configuration, read through
# the CI lint API where an include did not resolve offline, and basis gains
# merged_config. A review_automation match read from that configuration has line
# null and depth 1. Additive: no existing key moves.
# 1.31.0: run-context.json coverage_gate gains max_threshold_percent and
# threshold_meets_minimum, and each gate gains threshold_percent (issue #781): the
# threshold as a percentage (a JaCoCo ratio 0.80 is 80) and the Layer 6 level 1
# signal (true at 50 percent or more, null when a counted gate's percentage is not
# read). Additive: threshold and unit stay as written, and complexity-stats.json is
# unchanged.
# 1.32.0: run-context.json ci_checks gains enforced_check_runs,
# enforced_basis, masked_check_commands and off_trigger_check_commands
# (issue #775): the CI check test with masked checks (|| true,
# continue-on-error: true, allow_failure: true, set +e) and checks off the
# ordinary triggers left out. Additive: no existing key moves, and
# complexity-stats.json is unchanged.
# 1.33.0: run-context.json coverage_gate gains unmeasured_reason (issue #788):
# go_rust_gate_form_not_read when no gate is found in a repository holding a
# go.mod or Cargo.toml, which also makes threshold_meets_minimum null. Gates gain
# the tools go-test-coverage, cargo-tarpaulin and cargo-llvm-cov. Additive.
# 1.34.0: run-context.json gains the level block (issue #744): the default maturity
# model's conditions with state, grade and reason, the verified tally (met, measured,
# not_measured), the autonomy Step and the next Step's blockers
# (lib/level_tally.py). Additive: no existing key moves, and complexity-stats.json is
# unchanged.
# 1.35.0: run-context.json derived_findings rows gain an optional reviewed list of
# {path, completed_sha, note} (issue #721): the paths a done review in the
# committed .assess/actions.json clears. actions.json gains an optional completed
# list for done entries that left the Top 3. Additive.
# 1.36.0: run-context.json ai_usage gains identities_awaiting_confirmation (issue
# #765): the window's accounts that look automated and are not yet classified in
# the [identities] config section, a count with no identity. Additive.
# 1.37.0: run-context.json ci_checks gains lint_runs and lint_basis (issue
# #782): whether an unmasked CI line on ordinary triggers runs a linter (null
# when a check target or script may lint inside it). Additive: no existing
# key moves, and complexity-stats.json is unchanged.
# 1.38.0: run-context.json ci_checks gains unread_ci_files, and basis,
# enforced_basis and lint_basis gain unread_ci_system (issue #787): a tracked CI
# file of a system the scan does not parse (Jenkinsfile, .circleci/config.yml, ...)
# makes Layer 5 level 1 and Layer 3 level 1 unknown, not 0.
# Additive: no existing key moves, and complexity-stats.json is unchanged.
# 1.39.0: run-context.json gains the headline block (issue #747): the Step, its next
# unlock and "Verified practices: N of M measured" worded from the level block
# (lib/headline.py); badge.json carries its summary (the Step and the tally) instead
# of the findings count, and the log.md entry's readiness line is that summary, with
# no layered score. finalize-input.json drops score, maturity_label and denominator,
# and an action may carry a condition id. Additive to run-context.json.
# 1.40.0: run-context.json gains the guardrails block (issue #767): the gates
# the repository configures (complexity limit, coverage floor, pre-commit hooks,
# mypy strict), whether an instruction file states each, a snippet for each
# unstated one, and stated_pct. Additive: no existing key moves, and
# complexity-stats.json is unchanged.
# 1.41.0: run-context.json gains the agent_consistency block (issue #768): the root
# instruction files of every agent system present, the check commands and
# thresholds they disagree on, each file's link to the canonical source and the
# pointer line per unlinked system. nested_instructions also discovers the
# Windsurf, Cline, Aider, Junie, Amazon Q, Kiro, Continue and Zed files.
# Additive: no existing key moves, and complexity-stats.json is unchanged.
# 1.42.0 (2.2.0 release): four changes since 1.41.0.
# - two run-context.json fields that read configuration text are renamed so
#   their names no longer claim a run (issue #783): ci_checks.test_or_lint_runs is
#   test_or_lint_configured and coverage_gate.enforced is configured. Values and
#   meaning are unchanged; the fleet re-score reads a stored scan's old path under the
#   new name (lib/fleet/fleet_rescore.SIGNAL_ALIASES). complexity-stats.json is unchanged.
# - ci_checks.enforced_basis gains check_target (issue #920): no CI line runs a
#   check, but one runs a target whose recipe is not read (make foo, npm run foo,
#   ./scripts/foo.sh, docker run img), so enforced_check_runs reads null, never false. Common test
#   runners (dotnet test, swift test, mix test, bundle exec rspec, deno test,
#   phpunit, ctest, bazel test, turbo, nx, task) and wrappers (docker compose run,
#   pnpm exec, hatch run, bundle exec) now count as checks.
# - run-context.json structure_drift is the unavailable marker
#   {"available": false, "reason": "no_ownership_map: ..."} when the repository
#   commits no ownership or boundary map, where it was omitted (issue #908), so the
#   level block reads the same reason as fleet. A scan failure still omits it.
# - run-context.json coverage_gate gains enforced_meets_minimum and enforced_basis
#   (issue #921): the threshold reading with an enforced CI run of the gate
#   required. A gate's unit gains missed_ratio, and a JaCoCo MISSEDCOUNT /
#   MISSEDRATIO maximum is reported as a gate. Additive: no existing key moves.
# 1.43.0 (3.0.0 release): 17 changes since 1.42.0.
# - run-context.json layer_grades gains instruction_presence (issue #821): root,
#   nested_only or none. instruction_file_present is true for nested_only, and
#   instructions_grade there is the best nested grade, where both read as no
#   instruction file before; both are null when there is no root file and the
#   nested_instructions scan did not run. gap_actions gains a layer_grades entry for
#   nested_only and now sits after layer_grades in run-context.json.
# - run-context.json headline gains verified (the met, counted conditions graded
#   verified) and tags, one cell per truth tag with met, measured, verified,
#   not_measured, hazards and declined counts (issue #844). Additive: no existing
#   key moves.
# - run-context.json gains units (issue #862), after owner_exclusions: the monorepo
#   units (shallowest build-manifest folders, workspace members in place of their
#   aggregator, or the [[units]] of .assess/config.toml), the aggregators and the
#   candidates skipped with a code. Additive.
# - run-context.json gains `unit_readings` (issue #863, core-only): for each unit of the
#   `units` block, its own readings of the scope-sensitive scans (`promissory_markers`,
#   `doc_staleness`, `structure`, `stats_summary`, and `coverage_gate` or
#   `layer_grades` only when the unit holds its own gate or instruction file), projected
#   to the maturity model's signal paths, plus every model condition with `origin` `unit`
#   or `inherited`. `unit_count` is copied from `units`, null included. Manifest
#   collectors gain an optional `unit_scope` (`scoped` or `override`).
# - run-context.json gains `unit_levels` (issue #864, core-only): for each unit of
#   `unit_readings`, the default maturity model's Step, `next_step` and tally (`met`,
#   `measured`, `not_measured`, `verified`) with the unit's own readings in place, every
#   condition's `state`, `counted` and `origin` (`unit` or `inherited`), and `inherited`, the
#   met conditions that rest on a repository-wide reading. `unit_count` is copied from
#   `unit_readings`, null included. `unit_readings` no longer lets a unit inherit a coverage
#   gate or nested instruction file that sits only in other units: that unit reads its own
#   scoped coverage reading, or `instruction_file_present` false, origin `unit`. The root
#   unit (`.`) follows the same rule: it reads a no-gate coverage reading, or a nested file
#   only when one sits under no member unit.
# - ci_checks.lint_basis gains analysers_in_build and compiler_gate_only (issue
#   #926), both with lint_runs null: a CI .NET build whose committed project files
#   turn on the analysers, and a compiler run with warnings as errors as the only
#   static gate. The lint and analyser commands of .NET, Gradle, PHP, Elixir, Dart,
#   Swift and C++ count as lint runs, a rewriting formatter followed by git diff
#   --exit-code in its job counts, and a target whose recipe is not read reads
#   check_target (null) at Layer 3 as it does at Layer 5. Additive: no key moves.
# - run-context.json agent_environment.pinning gains manifests_total (issue #927):
#   the number of dependency manifests the lockfile check reads. Additive: no
#   existing key moves, and complexity-stats.json is unchanged.
# - agent_environment.pinning.manifests[] rows gain note (issue #927 part B):
#   skipped_library, direct_pins_only or null. A skipped_library row is listed
#   but left out of manifests_total and unlocked_total. Additive.
# - ci_checks basis, enforced_basis and lint_basis gain external_ci and
#   external_ci_marker (issue #928): under --platform, more than half of the sampled
#   merged pull requests carry a check of a known external CI system; offline, a root
#   Prow OWNERS_ALIASES with no CI file. Either reads null in place of a false case.
#   unread_ci_files counts more paths (LUCI .ci.yaml and infra/config/, every Azure
#   Pipelines layout, Prow, Zuul, Drone, Woodpecker and others; lib/unread_ci.py).
#   Additive: no existing key moves.
# - ci_checks.enforced_basis and ci_checks.lint_basis gain script_not_found
#   (issue #937); coverage_gate.unmeasured_reason go_rust_gate_form_not_read now
#   also carries a low gate beside an unread Go or Rust module.
# - the run context gains manifest whys (#939): `why` on each `level.conditions[]`
#   entry and each `gap_actions` entry, and `headline.next_why`, the whys of the
#   conditions the Next line's blocking gates wait on. Static text; counts-only output
#   drops them. Additive: no key moves.
# - structure gains packages (id, loc, modules; largest first, capped at 200),
#   package_count and package_edges (from, to, imports, bypass), the package map's
#   data (issue #942), and burrow_edge_count, the uncapped number of front-door
#   bypasses (internal_burrow_edges lists at most 100). Below both list caps the
#   bypass total equals burrow_edge_count. Package ids are path class under
#   counts-only. Additive: no key moves.
# - structure.package_edges is heaviest first (most imports, ties by from then to)
#   and capped at 400 pairs; package_edge_count is the number of pairs among the
#   kept packages before that cap (issue #942).
# - run-context.json review_reality gains sequence and bot_sequence (issue #943): one
#   token per sampled merged change, oldest merge first. sequence reads approved,
#   reviewed, unreviewed or unknown with the predicates behind approved_share and
#   reviewed_share; bot_sequence reads bot, none or unknown. Tokens only, kept by
#   counts-only. They feed the review strip (review-strip.svg). Additive: no
#   existing key moves.
# - ci_checks.includes records an unparsed GitLab include as kind unknown, where it
#   read local (issue #959), and an unquoted YAML tag, anchor or alias entry
#   (!reference [x], *shared) is unparsed, where it read a local glob.
#   run-context.json stats_summary with no complexity-stats.json is
#   {available: false, reason: no_complexity_stats} with files_scored null, where
#   files_scored read 0. review_reality and delivery on a remote host no platform
#   read supports (Bitbucket, Azure DevOps, a host glab does not reach) read
#   unsupported_host, where they read network.
# - run-context.json delivery gains sample_page_size (issue #995): the changes per
#   heavy read the merged sample finished at, or null when none was made. Additive:
#   no existing key moves.
# - units.skipped gains the problem code `unparseable_leaf` (issue #1030): an unparseable
#   build manifest that cannot change the unit set (not at the repository root, a
#   workspace member or beside a readable workspace declaration, and not named
#   pnpm-workspace.yaml, go.work or settings.gradle(.kts)) is skipped with it, and
#   units.unit_count stays known where it read null with `unreadable_manifest`.
#   Additive.
# 1.44.0 (3.1.0 release): 36 changes since 1.43.0.
# - ci_checks.basis gains check_target (issue #799): test_or_lint_configured reads null
#   when the only check-shaped CI line is a Maven run with its tests skipped
#   (mvn -DskipTests verify), whose verify phase may still run a lint plugin.
# - run-context.json doc_staleness rows gain directive_only (issue #816): true on a page of
#   only mkdocstrings `:::` generator directives with no declared provenance, whose ratio
#   is then 0. summary gains generator_directive_docs, the count of those pages. Additive:
#   no existing key moves, and complexity-stats.json is unchanged.
# - run-context.json doc_staleness rows gain subject_match (issue #817) on a parallel-docs-tree
#   row: the key and tie-break that picked its root, `<key>:<rule>` with key stem or
#   parent-name and rule only-match, longest-suffix, shallowest or path-order. Its
#   subject_roots may now name one module file as well as a directory. Additive: no
#   existing key moves.
# - run-context.json stats_summary gains loc_by_class (issue #820): per path class (source,
#   test, fixture, example, vendored, generated), files_scored and the p50, p95 and max of
#   its line counts, null with no file. A stats sidecar from before it reads loc_by_class
#   unavailable (no_class_split). Additive: no existing key moves.
# - run-context.json observability.instrumented gains `kinds` (issue #823): the kinds of
#   evidence behind rung 1, sorted tokens from `code` (a standard-library logger or logging
#   facade in use in production source), `config` (a committed collector or scrape config)
#   and `dependency` (a telemetry or logging dependency in a build manifest); empty when
#   nothing matched. Additive: no existing key moves.
# - run-context.json promissory_markers gains fixture_files_excluded (issue #824): the distinct
#   fixture files in scope that held a candidate marker line, counted before the family
#   filters. Additive: no existing key moves.
# - run-context.json gains `unit_rollup` (issue #865, core-only, after `unit_levels`): the
#   lowest unit's Step (`lowest_step`), `units` and `measured` (units with a Step), the Step
#   above (`next_step`) and the measured units at or above it (`at_next_step`), and
#   `unlocks`, the Step gates that hold units back ranked by the units one change lifts to
#   their next Step: `gate`, `scope` (`repo_wide` when every held unit inherits the gate's
#   reading, else `unit`), `changes`, `held`, `lifted` and `lift_per_change`. `lines` and
#   `markdown` hold the roll-up text for two or more units and are empty otherwise, so the
#   repository's Step and headline are unchanged. Additive.
# - coverage_gate.enforced_basis gains status_check_unconfirmed, and
#   coverage_gate.unmeasured_reason gains codecov_target_not_read and codecov_patch_only
#   (issue #882); gates may
#   carry tool codecov with metric project or patch.
# - run-context.json delivery gains unconfirmed_ai_accounts (issue #883): the distinct sampled
#   accounts that are name-only AI matches awaiting confirmation in [identities] (hinted
#   change authors, and GitLab note authors). Additive: no existing key moves. A share such an
#   account would decide (agent_authored_share, and on GitLab ai_review_share and the human
#   comment figures) now reads null where it read a number.
# - the run context's `level` block gains `tags` and the run header gains `prior_level` (#940):
#   the earlier run's `prior-level.json` (its maturity-model version and tag rows), read before
#   this run writes its own; null when there is no such file, and
#   `{"available": false, "reason": "unreadable"}` when it exists but cannot be read (counts-only
#   keeps both: `reason` is a fixed token). Each run writes `prior-level.json` beside
#   `run-context.json` and draws `tag-profile.svg`, the tag profile strip. Additive.
# - ci_checks.enforced_basis, ci_checks.lint_basis and coverage_gate.enforced_basis
#   gain check_may_pass (issue #1008): a check whose failure the step may hide
#   (lib/ci_unsure.py) reads null with it.
# - `instruction_placement` file records gain `est_tokens` (issue #1045), the whole file at
#   `instruction_budget.CHARS_PER_TOKEN`, and `thresholds` gains `loaded_in_full_min_tokens`
#   (5000). Additive.
# - run-context.json gains `skill_disclosure` (issue #1045): each SKILL.md and agent definition
#   with its body lines, estimated tokens, name and description, description length,
#   references (resolved, depth, lines, table of contents) and the rules it breaks;
#   `files_considered` and `files_breaking`. Read per skill unit: `unit_readings` gives a
#   `skill` unit its own `skill_disclosure`, and the check manifest gains `unit_kinds`.
# - run-context.json gains `contributor_files` (issue #1045): `ready` (null while the policy
#   is not measured), `guide` (`present`, `path`), `policy` (`present`, `path`, `kind`
#   `file` | `private_reporting` | `org_file`, `source` `repo` | `org`, or `available: false`
#   with `org_policy_not_read`), and `issue_template` and `change_template` as evidence;
#   `available: false` with `knowledge_base` for a knowledge base.
# - run-context.json gains `org_security_policy` (issue #1045, network): whether the owner's
#   public `.github` repository holds a SECURITY file, which GitHub shows on a repository with
#   none. `contributor_files.policy` reads it when the tree holds no policy, and is
#   `available: false` (`org_policy_not_read`) when it was not read.
# - run-context.json gains `skill_script_tests` (issue #1045): per skill folder its script and
#   test counts, `untested` (null with no script) and `tested_elsewhere` (outside test files
#   that reference its scripts, information only); `skills_with_scripts` and
#   `skills_untested`. Read per skill unit.
# - run-context.json gains `skill_personal_data` (issue #1045): the skill and agent files, and
#   their skills' `references/` files, that hold a detected kind of personal data, each with
#   its `kinds` only (never the data); `files_considered`, `files_with_personal_data` and
#   `kinds_detected`. Read per skill unit.
# - `skill_personal_data.files_unread`: files that could not be read; with none of the files read
#   holding a kind, the block reads `available: false` (`skill_files_unread`), not a clean pass.
# - `skill_personal_data.credential_candidates`: files with a credential-shaped assignment,
#   information only until a precision check passes (#1196); `kinds_detected` leaves out
#   `credential`.
# - Steps read gates only (issue #1083). Breaking: run-context.json loses
#   `level.next_step.threshold` and the same key under `unit_levels[].next_step`, and the
#   `threshold` blocker kind is gone with its `threshold` and `met` fields, so every blocker is
#   `kind: "gate"`. The `headline` block keeps its keys; its `summary` and lines change wording.
# - A run-context block `since_last_run` (issue #1083): `first_run`, `previous_run_id`,
#   `previous_commit`, `closed` (each `action`, `condition`, `finding`, `completed_sha`),
#   `closed_reason`, `closed_count`, `unresolved`, `gates_gained`, `gates_lost`, `next_blocker`, `lines`, `markdown`.
#   Additive. Counts-only keeps the counts, ids and shas and drops the text.
# - `gap_actions` entries may carry `finding`, `mode` and `cost` (issue #1084): an
#   `instruction_placement` removal proposal has `finding: "instruction_always_on"`,
#   `mode: "remove"` and `cost: {metric, path, value}`. `actions.json` gains the `remove`
#   mode and an optional `cost` on an action. Additive.
# - `since_last_run` gains `pruned` and `pruning`, and `prior-level.json` gains `pruning`
#   (issue #1084): the pruning ledger's removals, each `{path, completed_sha, cost_before,
#   runs_held, credited, saved, gates, credited_at}`. Additive.
# - run-context.json gains agent_outcome (issue #1085): of the agent commits ai_usage
#   counts (trailer or agent author) in the run window, the share later reverted (a
#   "This reverts commit" message naming the commit, or a later inverse diff by patch-id)
#   and the share with an added line rewritten within rework_days (14) by git blame,
#   with the counts and the window. Informational: no Step, count or headline reads it.
#   Unknown reads null with a token reason; a shallow clone or a git failure is
#   available: false with a code counts-only output keeps.
# - `units.units[]` gains `kind` (issue #1098): `skill` for a folder holding a `SKILL.md`,
#   `agents` and `commands` for a plugin root's components, `package` for a build-manifest
#   unit; `origin` gains `component` for the first three. Skill and plugin-component
#   folders are units, found before the build manifests, so a skills repository lists one
#   unit per skill. Additive: a reader that ignores `kind` sees more units.
# - `unit_readings.units[]` gains `kind` (issue #1098), copied from the unit's `units` entry
#   (`package` when the entry has none), kept as a token in counts-only output. Additive.
# - The `headline` block gains `summary_lines`, `call_to_action` and `summary_markdown`
#   (issue #1102): the three-line executive summary (the Step and what an agent can do there,
#   the next gate as a task and what the next Step allows, what changed since the last run)
#   and one linked call to action, from `lib/exec_summary.py`. Additive. The `headline` scan
#   now reads `since_last_run`, so it runs after it. Counts-only drops the text and keeps
#   `summary_lines` as its length. `HEADLINE_VERSION` moves to 4 in the release PR.
# - `prior-level.json` version 2 and the run context's `prior_level` gain an optional
#   `baseline` (issue #1122): `{run_id, head_sha, held_gates}`, the run's measured commit and
#   the Step gates it held. Version-1 files still read, without one. "Since last run" reads
#   this committed baseline first, so a fresh clone or CI gets its credit.
# - run-context.json ci_checks.includes[].status gains `toolkit_gate` (issue #1152): the
#   toolkit's own regression gate template, known to run no project tests. It counts in
#   `resolved_includes`, not `unresolved_includes`. Additive: no existing key moves.
# - run-context.json agent_ops (issue #1153) gains `managed` (`state` not_read, found, absent
#   or unreadable; `sources[]` by kind token with allow, deny and ask counts and hook events;
#   `reason`), each settings entry gains `default_mode` (a documented mode token or `other`,
#   information only), and the summary gains `permissions_source` (`repository` or `managed`).
#   `managed.state` is `not_read` unless the run opted in (`--managed-settings`, or
#   `[agent_ops] managed_settings = true` in `.assess/config.toml`), so it also records whether
#   the machine was read. Under the option `summary.permissions_encoded` may be null, with
#   `permissions_encoded_reason` `managed_settings_unreadable`, which the tally reports as not
#   measured. Additive.
# - `unit_rollup` gains `learn_from` (issue #1155) and reads the `units` block: per unit held
#   back by an unmet gate the owner did not exclude, the sibling units that already hold it:
#   `unit`, `gate`, `holders` (every sibling holding it) and `names` (up to three, the same
#   ecosystem and kind first, then by folder). Unit folders are written only hashed in
#   counts-only output, under `--hash-paths`. Additive.
# - A pruning-ledger entry may carry `merged` (issue #1168): the commits of earlier removals
#   of the same file folded into it. Additive.
# - run-context.json gains `secrets_guard` (issue #1173): the pre-commit config's secrets
#   hooks and the CI files' secrets scan steps (file and tool tokens), `configured`,
#   `enforcement` (present from CI, or `available: false` with `secret_scanning_not_read` on
#   a GitHub repository with no CI scan) and `sources`, each with its own evidence grade.
# - `secrets_guard.ci.includes_unresolved`: GitLab includes that could hold a scan and were
#   not read; level 2 reads not measured (`ci_includes_unresolved`) when it is above 0 and no
#   scan was found.
# - `secrets_guard.ci.rules_unevaluated`: GitLab scans in a job with `rules:`, `only:`/`except:` or
#   an uncertain `when:`; level 2 reads not measured (`gitlab_rules_unevaluated`) with any and no
#   counted scan.
# - `prior-level.json` version 3 and the run context's `prior_level` gain an optional `models`
#   map (issue #1184): one record per shipped profile, keyed by its name, each
#   `{levels_version, tags, baseline}`. The top-level record stays the default model's and may
#   be absent when only profiles have run. Version-1 and version-2 files still read.
ARTIFACT_SCHEMA_VERSION = "1.44.0"

# The finding method version: which rules decide the findings a run produces.
# Independent of plugin releases and of ARTIFACT_SCHEMA_VERSION (which versions
# artifact shape, not finding semantics). Bump it, by one, in any change that
# moves which findings a run produces on unchanged code; the CI gate compares it
# with the version in the committed .assess/findings.json and explains the
# finding moves instead of failing on them unexplained.
# 1: the baseline (issue #554).
# 2: churn counts follow renames, moving staleness ratios, lying_map and hotspot
# ranks for files renamed inside the churn window (issue #425).
# 3: the doc-complexity join reads complexity-stats.json's uncapped `files` list,
#    so a complex file outside every top-10 list can now produce lying_map,
#    unexplained_complexity or good_contract (issue #556). The same list reaches
#    candidate_dead_weight (keyhole_signals._high_complexity_paths) and
#    orphaned_understanding (understanding_analysis._extract_file_ccn), which
#    now also see every scored file.
# 4: the complexity join takes doc ownership from the doc-staleness rule
#    (lib.docs.doc_ownership, issue #555): a README beside a design doc owns the
#    directory's code, and a non-base doc owns none by directory.
# 5: a CLAUDE.md identical to its AGENTS.md below the header (H1 and leading
#    HTML comments) is the informational mirrored_instruction_files, not the
#    capping claude_shadows_agents (issue #589).
# 6: on a shallow clone every doc-staleness row is confidence "low", which keeps
#    it out of lying_map and halves its stale-hub priority (issue #544). A full
#    clone's findings do not move.
# 7: instruction files are graded on always-on size and share instead of the
#    whole-file size curve (issue #560), which moves instruction grades.
# 8: a bats suite is a test file and credits its shell script (issue #593), so a
#    co-located ``<stem>.bats`` can now produce self_referential_tests (E2).
# 9: a parallel-tree ``run.bats`` equally close to a ``run.sh`` and an
#    extensionless ``run`` credits neither (issue #635), so a test_focus signal or a
#    hotspot "Has test file" row can move.
# 10: a skill's SKILL.md is a base doc ranked after README.md (issue #614), so it
#     owns its skill directory's code in doc staleness and the complexity join,
#     and can move doc_staleness.modularity (base_doc_dir_ratio).
# 11: a leading HTML comment only one of a CLAUDE.md / AGENTS.md pair carries is
#     compared, not stripped, so it turns a mirrored_instruction_files into a
#     claude_shadows_agents; text after a header comment's ``-->`` (an H1
#     included) is body (issue #632).
# 12: wording and padding inside an instruction-file section no longer lower its
#     penalty (issue #619): command_reference is an absolute fenced count, a size
#     signal beats always-on wording, fetch lines past the relocatable cap count
#     as always-on, and the B cap reads the whole loaded file. Instruction grades
#     can move.
# 13: doc_claims compares pins per (pinned file, tool) (issue #610), so a Node pin
#     no longer contradicts a Python pin in one ``.tool-versions``; the L0
#     "conflicting doc claims" row and scorer evidence can move.
# 14: a tracked source's rival shell spelling no longer hides an untracked
#     ``run.sh`` beside it, so the pair ties for ``run.bats`` (issue #648); and a
#     GitLab exact protected-branch rule combines with every matching wildcard,
#     so a config_drift snapshot of the exact rule can report the wildcards'
#     looser levels as drift; and a rename-only commit (no content change) is
#     no longer churn or a content change (contract I425-2), so doc and code
#     churn, doc ages, staleness ratios and lying_map can move.
# 15: a repository with no importable Python package reports ``structure``
#     ``available: false`` instead of an empty graph (issue #663), so keyhole
#     static metrics and structure_drift tier 1 skip it; findings that read the
#     static graph can move on such repositories.
# 16: a root ``agents/*.md`` shaped as an agent definition is reported in
#     ``instruction_placement.loaded_in_full`` even without a plugin manifest
#     (issue #662), so that list and the scorer evidence drawn from it can move.
# 17: an import graph with no edges reports ``structure.modularity_q`` null with
#     ``modularity_reason: no_import_edges`` instead of 0.0 (issue #682), so the
#     scorer's Layer 2 evidence on such repositories can move.
# 18: a Go module (a go.mod with packages) is graphed from its import blocks
#     (issue #710) instead of reporting no_import_graph_go, so structure, the
#     Layer 2 evidence, and hidden_coupling / refactor_boundary on Go directories
#     can move on Go repositories; structure_drift tier 1 empties its static-derived
#     lists for a graph that maps no module to a file, and the behaviour block projects the
#     static metrics only onto directories in the graphed language.
# 19: TypeScript and JavaScript sources get an import graph (issue #709), and
#     ``select_graph`` measures the dominant source language's graph: a builder
#     that covers it decides with its graph or its failure reason, and another
#     language's graph stands in when no builder covers it or that builder
#     found nothing to graph. Files a builder
#     skips (minified bundles, declarations, tests) do not count toward the
#     dominant language. A TypeScript- or JavaScript-dominant repository now
#     reports ``structure`` available instead of ``no_import_graph_typescript``,
#     so keyhole static metrics, structure_drift tier 1, hidden_coupling and the
#     scorer's Layer 2 evidence can move there.
# 20: on a GitLab remote ``review_reality`` reads each sampled merge request's notes
#     (issue #716), so ``reviewed_share`` and ``bot_review_share`` are measured
#     instead of null and the L7 "hollow required review" row can now fire there.
# 21: every git-history window and day count is counted back from HEAD's committer
#     time instead of today (issue #773): churn windows, ``doc_staleness`` ages and
#     window ratios, instruction ``freshness_days`` (and so the grader's freshness
#     penalty), ``ai_usage`` counts and the keyhole velocity clock. On a repository
#     whose HEAD is old, ages shrink and windows cover the history before HEAD.
# 22: ``structure.front_door_ratio`` is null when no import crosses a package
#     boundary (issue #760), so a single-package repository no longer reads as
#     modular through a vacuous 1.0: ``hidden_coupling`` there rests on
#     modularity alone, and a ``structure.front_door_ratio`` criterion is unknown.
# 23: ``ci_checks`` no longer counts a bare list item that only names an
#     always-check program (``- pytest``, ``- tox``) unless its parent key is a
#     GitLab ``script:`` / ``before_script:`` / ``after_script:`` (issue #725), so
#     a workflow whose only mention is ``needs: - pytest`` or a ``matrix:`` axis
#     ``- tox`` reads ``check_commands`` 0 and Layer 5 level 1 can drop to 0.
# 24: GitLab includes resolve offline (issue #766): ``ci_checks.test_or_lint_runs``
#     reads a check in an included local file or a template cloned in the fleet root
#     (true), or false when every include resolved without one, where it was null;
#     ``review_automation`` credits a review component inside a resolved template;
#     a workflow whose ``on`` key reads ``true:`` (YAML 1.1) has its events read, so
#     an issue_comment-only review action is no longer credited.
# 25: ``agent_environment`` adds ``setup_ready`` (the setup half of ``layer5_cap``)
#     and ``pinning.unlocked_total`` (issue #786). Layer 5 level 2 reads
#     ``unlocked_total eq 0`` instead of the cap: a repository whose instruction
#     file names no check can now read level 2, and one with committed setup
#     but an unlocked manifest no longer can. ``layer5_cap`` is unchanged.
# 26: Layer 4 level 1 reads structure_drift.tier_0.boundary_patterns instead of
#     matched_patterns (issue #785), so a CODEOWNERS whose lines own no module
#     source (`*`, `*.py`, `/.github/`, `README.md`, `docs/`) no longer credits a
#     declared boundary.
# 27: under --platform (and in a full /assess run) GitLab's CI lint API gives the
#     merged CI configuration of a repository whose includes did not all resolve
#     offline (issue #796): ``ci_checks.test_or_lint_runs`` reads true or false
#     where it was null, and ``review_automation`` credits a review component the
#     merge pulled in. Offline headless runs are unchanged.
# 28: a masked CI check (``|| true``, ``continue-on-error: true``,
#     ``allow_failure: true``, ``set +e`` without a re-raise) or one that does
#     not run on ordinary changes (a ``workflow_dispatch``-only workflow, a
#     manual GitLab job) no longer meets Layer 5 level 1 (issue #775): it reads
#     ``ci_checks.enforced_check_runs``, so the Layer 5 row and the scorer's
#     Layer 5 evidence can move.
# 29: coverage_gate reads go-test-coverage's .testcoverage.yml, cargo tarpaulin
#     --fail-under and cargo llvm-cov --fail-under-lines (issue #788), and a
#     repository holding a go.mod or Cargo.toml with no gate found reads Layer 6
#     level 1 as not measured (threshold_meets_minimum null) where it read 0.
# 30: a self_referential_tests path whose done review is recorded in the committed
#     .assess/actions.json (status done, completed_sha an ancestor of HEAD, the path
#     in files) and untouched since that commit leaves the attention list,
#     prescribed_actions and the keyhole summary (issue #721), so the Top 3 can move.
#     It stays in derived_findings with a reviewed note; a later change re-flags it.
# 31: ``ai_usage`` buckets an account confirmed in the ``[identities]`` section of
#     ``.assess/config.toml`` or ``--fleet-config`` (issue #765) before any pinned
#     rule: a confirmed agent counts in ``agent_authored_commits``, a confirmed bot
#     or person never does. The same matcher serves ``delivery`` and the GitLab
#     note classifier (``gitlab_notes``) under --platform, so a confirmed account
#     moves their agent counts too. Without the section nothing changes.
# 32: Layer 3 level 1 reads ``ci_checks.lint_runs`` (issue #782): a linter run
#     in CI, not a suppression comment. One suppression comment no longer earns the row,
#     and a clean repository that lints in CI reads at least 1 where it was
#     unknown, so the Layer 3 row can move.
# 33: a repository with no readable CI check whose tree tracks a CI file of a
#     system the scan does not parse (Jenkins, CircleCI, Azure Pipelines,
#     Bitbucket Pipelines, Travis CI, Buildkite) reads
#     ``ci_checks.enforced_check_runs`` null (``basis: unread_ci_system``), so
#     its Layer 5 row moves from level 0 to unknown (issue #787).
# 34: the displayed headline changes (issue #747): the layered "X / 8" score and
#     its band names are retired for the deterministic Step, its next unlock and
#     "Verified practices: N of M measured"; the badge and the log.md entry carry
#     the Step and the tally, and the scorer returns per-layer verdicts only.
# 35: the guardrails block and its stated_pct signal (issue #767): the default
#     maturity model gains the guardrails-stated row (half of the configured
#     gates stated is level 1, all of them level 2, none found is unknown).
#     Existing rows are unchanged.
# 36: the agent_consistency block and its conflicts and unlinked signals (issue
#     #768): the default maturity model gains the agent-consistency row (no
#     conflict is level 1, every system linked to the canonical file level 2,
#     fewer than two systems unknown). Existing rows are unchanged.
# 37 (2.2.0 release): four rule changes since 36.
# - the empty-set rule and two requires (issue #906): a file-size condition
#   reads not measured (no_scored_files) when no file is scored, layer-2-design.2
#   requires layer-2-design.1 and layer-4-architecture.2 requires
#   layer-4-architecture.1. Fewer conditions count on docs-only repositories
#   and tangled import graphs; no shape changes. The default maturity model's
#   version moves with it.
# - ci_checks closes five false-credit holes (issue #919). A version or usage
#   query (`ruff --version`) runs no check; an exit-zero flag (`--exit-zero`,
#   `--issues-exit-code=0`), a later top-level `exit 0` in a GitHub run: body
#   without errexit (pwsh, a Windows runner, set +e) and a literal `if: false`
#   step or job are masked; a workflow in a subfolder of .github/workflows/ is
#   not a CI file. Layer 3 level 1 and Layer 5 level 1 can move from met to unmet.
# - Layer 6 level 1, and so the Step 4 coverage_gate gate, reads
#   coverage_gate.enforced_meets_minimum (issue #921): a threshold of at least 50
#   percent that no enforced CI line runs is level 0, where it read 1, and CI the
#   scan cannot read is unknown. A JaCoCo MISSEDCOUNT maximum reads unknown, not 0.
# - ci_checks recognises common test runners and reads unread targets as unknown
#   (issue #920): dotnet test, swift test, mix test, deno test, bazel test, ctest,
#   phpunit, pest, rspec, Gradle test tasks, xcodebuild test, php artisan test and
#   check-named make, just, task, rake, turbo and nx targets run a check, and a
#   wrapper (docker compose run, bundle exec, uv run, poetry run, npx, pnpm exec,
#   hatch run) is read by the program it runs, for Layer 3 lint runs as well as
#   Layer 5. A target whose recipe is not read (make foo, npm run foo,
#   ./scripts/foo.sh, docker run img) reads enforced_check_runs null
#   (check_target) where it read false; setup-named targets (make build) still
#   read false. Layer 5 level 1 can move from 0 to met or unknown.
# 38 (3.0.0 release): 22 rule changes since 37.
# - the displayed headline changes (issue #844): the tally line reads "Practices in
#   place: N of M measured, V verified" instead of "Verified practices", the Next
#   line's shortfall reads "N more practices are in place", a per-tag strip line
#   follows the tally with NOT MEASURED, HAZARD and DECLINED badges, and the badge
#   and log.md summary read "Step S · N in place of M measured".
# - structure_drift.tier_0.boundary_patterns counts an architecture-doc reference
#   only when its resolved files hold module source under a non-dot directory, the
#   CODEOWNERS predicate ownership_parser.is_boundary_pattern (issue #874). An
#   ARCHITECTURE.md naming only `CONTRIBUTING.md`, `docs/` or `.github/workflows/ci.yml`
#   no longer meets Layer 4 level 1, which can move from met to unmet.
# - GitLab `rules:` are read as GitLab runs them (issue #884): the first matching rule
#   decides, `if:` expressions are evaluated per merge request and branch push pipeline,
#   and a top-level `workflow: rules:` gates every job of its file. A job gated by
#   `$CI_PIPELINE_SOURCE == "schedule" && $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH` or
#   `$CI_COMMIT_TAG && ...` no longer runs a check (enforced_check_runs can move from
#   true to false); a custom variable, inline, anchored or multi-line rules and `exists:`
#   read unknown. Layer 5 level 1 can move from met to unmet or unknown.
# - the empty lockfile set (issue #927): layer-5-ci.2 reads not measured
#   (no_manifests) when agent_environment.pinning.manifests_total is 0, where it
#   read met. Fewer conditions count on repositories with no dependency manifest.
# - the lockfile check reads Mix, Pub, SwiftPM, .NET, Gradle, vcpkg and Conan
#   manifests (issue #927 part B). Libraries that by convention commit no lock
#   (Composer type library, a published Pub package, a SwiftPM library) are
#   left out of the count; a Gradle version catalog or NuGet central package
#   management reads unlocked. layer-5-ci.2 can move from met to unmet on
#   repositories with those manifests, and from unmet to not measured on
#   library-only ones.
# - ci_checks masks a check in an errexit-exempt position before a top-level `exit 0`
#   under the default `bash -e` (issue #936): `pytest && echo ok; exit 0`, `pytest | tee
#   log; exit 0` without pipefail, an `if` / `while` condition or a `!` pipeline.
#   `ruff check . --exit-zero || pytest` drops `pytest`, a folded `run: >` block is read
#   folded, a quoted `{` or `<<EOF` no longer hides a later `exit 0`, and `hatch run
#   pytest -V` / `--co` are info calls. Layer 3 and Layer 5 level 1 can move from met to
#   unmet; a folded pwsh `pytest` / `exit 0` can move from unmet to met.
# - issue #937, two false readings. A coverage gate below 50 percent beside a
#   go.mod or Cargo.toml with no gate read for that language reads
#   threshold_meets_minimum null (go_rust_gate_form_not_read) where it read
#   false, so Layer 6 level 1 and the Step 4 coverage_gate gate read unknown,
#   not unmet. A CI check or lint step that runs a committed script no tracked
#   file matches (run: ./ci.sh with no ci.sh) reads enforced_check_runs and
#   lint_runs null (script_not_found) where it read true: Layer 3 and Layer 5
#   level 1 can move from met to unknown.
# - `ci` is no longer a trusted check word (issue #951): `make ci`, `npm run ci`, `pnpm ci`,
#   `yarn ci` and `./ci.sh` can run a build, a deploy or a test suite, and the recipe is not
#   read, so a CI line naming only one reads null with basis `check_target`, like `make foo`;
#   so does a `ci` name behind `python`, `source`, `cargo`, Gradle, Maven or `go`
#   (`python ci.py`, `cargo ci`, `./gradlew ci`), never a false 0.
#   Layer 5 level 1 (`enforced_check_runs`, the Step 3 `ci_runs_tests` gate) and Layer 3
#   level 1 (`lint_runs`) can move from met to not measured. `bun ci` and `npm ci` stay
#   installs, and a name that also carries a check word (`make ci-test`, `npm run test:ci`)
#   still counts. Instruction files keep `ci` (`lib.named_check`): a CLAUDE.md that names
#   `make ci` still counts at Layer 0 (`agent_environment.check.named_total`), unchanged.
#   A committed `./ci.sh` was credited by its name (`lib.ci_scripts` checks that the file
#   is there and never reads its body), so it now reads null `check_target` too; a missing
#   one keeps `script_not_found` (#937), and a masked one keeps `not_enforced`.
# - issue #954, coverage-gate CI credit edge cases. enforced_meets_minimum (Layer 6
#   level 1, the Step 4 coverage_gate gate) no longer credits a CI run that does not
#   enforce the gate: a threshold named only in echo or printf text beside a run with
#   another threshold, pytest with --no-cov (on the line or in addopts), a Gradle run
#   that excludes jacocoTestCoverageVerification (or test, leaving it no execution data)
#   with -x or --exclude-task, and a Gradle dry run (-m, also in a cluster such as -qm,
#   --dry-run, --task-graph) now read false. pytest -c FILE reads FILE's addopts as the
#   run's own, and reads null when FILE was not read. A pytest addopts --cov or Jest
#   collectCoverage: true switches on only the gates in its own directory or below, so a
#   sibling package's config gate reads false or null where it read true. A threshold
#   outside a pytest section of pyproject.toml or setup.cfg (a hatch, pdm or poe script, a
#   tox command) is a task-runner gate: indirect_run when CI runs hatch run, pdm, poe (also
#   through uv run or poetry run) or tox, and not_run_in_ci when it runs none, where it
#   read unread_ci_system. pdm run is read through to the program it runs, so pdm run
#   pytest --cov credits a coverage.py gate where it read not_run_in_ci. A second
#   --cov-fail-under on one CI line is read as its own gate.
# - guardrails reads the run scope (issue #956): a scoped run walks the scope
#   only, as coverage_gate does, and a scope root with no ruff, flake8 or mypy
#   config is gated by the tool's nearest config at or above it, as each tool
#   resolves it (ruff from each file, mypy and flake8 from the directory they
#   run in). Ruff's C901 selection resolves select and ignore by specificity (C9
#   ranks with C90, an ignore wins a tie), so `select = ["C901"]` beside
#   `ignore = ["C"]` is a complexity gate. A threshold states a gate only as a
#   standalone number (no letter, digit or underscore on either side, no dot
#   before it, so "80th" and "15x" state nothing), and a
#   coverage floor's ratio form needs a decimal point, so "step 1" and "v1.0" no
#   longer state a floor of 100.
# - CI reader edge cases (issue #958): a GitHub workflow whose `on:` block list sits at
#   the key's own column (`on:` then `- push`) is read, so ci_checks.enforced_check_runs
#   can move from null (`unknown_trigger`) to true or false. A top-level GitLab job named
#   `trigger` after a blank line is no longer read as a child pipeline, so a merged
#   configuration with no check can move test_or_lint_configured from null
#   (`merged_config_partial`) to false. A lint-API include source with surrounding
#   whitespace is recorded once.
# - agent_consistency drops a leading heading or list ordinal before reading
#   threshold numbers (issue #960): `## 2. Coverage` beside a stated 90 is no
#   conflict, so agent-consistency level 1 can move from unmet to met. A
#   next step for an unlinked system names its primary file (CLAUDE.md) before
#   a supplementary rule file.
# - The two 2.1.0 signal paths renamed in 2.2.0 load again (issue #970):
#   a `--levels` model naming `ci_checks.test_or_lint_runs` or
#   `coverage_gate.enforced` reads `ci_checks.test_or_lint_configured` or
#   `coverage_gate.configured` and scores the same, with one deprecation line
#   on stderr per old path, where it exited 2. Fleet compare reads both sides
#   under the new paths, so a 2.1.0 run against a 2.2.0 run lists no added or
#   removed leaf for them. `SIGNAL_ALIASES` moves to `lib/signal_aliases.py`.
# - issue #1008, a check inside a ( ) subshell that ends in a no-op is masked. A
#   || true, || :, || /bin/true or || exit 0 inside a subshell masks the segments
#   before it back to the subshell's start, as at top level, so (pytest || true) and
#   (cd sub && pytest || true) read as masked, not enforced: enforced_check_runs and
#   lint_runs (Layer 5 and Layer 3 level 1) and the coverage-gate CI run can move from
#   met to unmet. (pytest && echo ok) and pytest && (echo x || true) keep their check.
# - ci_checks.enforced_check_runs resolves a GitLab job's same-file `extends:` chain
#   (a name or a list, multi-level, the last parent winning and the job's own keys
#   overriding) before it reads `rules:`, `when:`, `only:` and `except:`, and reads
#   `except:` with `only:` semantics (issue #1021). A job inheriting schedule-only or
#   tag-only rules from a hidden template no longer counts as running on ordinary
#   changes, and a job whose `extends:` target is not in the file (an `include:`),
#   a cycle, or a job given `rules:` beside `only:` / `except:` reads null. Layer 5
#   can move from met to unmet or unknown; no reading moves to met.
# - A flow-map `only:` or `except:` (`only: {refs: [tags]}`) reads null instead of
#   having its map keys read as ref names (issue #1021); an `only: {refs: [tags]}` job
#   moves from running on ordinary changes to unknown.
# - three rare lockfile readings (issue #1025): a SwiftPM package with a
#   `.library(` product and no `.executable(` product reads skipped_library even
#   when it declares an `.executableTarget(` (as a library that ships a build-tool
#   or command plugin does), where it read an unlocked app; a Kotlin
#   Multiplatform `commonMain.dependencies { ... }` block counts the build, where
#   it gave no row; a `package.json`, `composer.json` or `vcpkg.json` with a
#   byte-order mark parses, where it gave no row. layer-5-ci.2 can move from unmet
#   to not measured on such Swift packages, and from met or not measured to
#   unmet on repositories with the other two.
# - On a CI line the `.` builtin runs a script like `source` (issue #1033); instruction files
#   (Layer 0) read `.` as before. `. ./test.sh` read as no check, a
#   false 0; it now reads as `source ./test.sh` does, at Layer 5 (`enforced_check_runs`) and
#   Layer 3 (`lint_runs`). A sourced script the tree does not hold reads `script_not_found`
#   (null), as `./x` does since #937, where `source ./test.sh` with no file was credited.
#   Any other sourced script whose body is not shown reads null (`check_target`), not false
#   (`source ./scripts/common.sh`). Sourcing an environment (`. venv/bin/activate`, `source
#   .env`, `. ./env.sh`, `. ~/.nvm/nvm.sh`) runs no check and stays false.
# - A committed CI script's body is read where it is tracked at the exact path the line runs,
#   and for a `./x` run only when git records it executable (mode 100755) (`lib.script_body`): a body that provably fails when its check fails (`set -e` before the
#   check, the check as the last top-level statement, or `|| exit 1` after it) reads true,
#   so an executable `./ci.sh` ending in `pytest` moves from not measured to met. A body the reader does not
#   model, or one whose check cannot fail the script, keeps the name's reading. The only
#   reading that moves down is the missing sourced script above.
# - A target whose name pairs `ci` with a setup or build word (`make deploy-ci`, `npm run
#   build:ci`, `make ci-deploy`, `./scripts/deploy-ci.sh`) reads false (no check), not null,
#   unless its body shows a check.
# - One `on:` reader for GitHub workflows (issue #1036): shared_tooling.workflow_events
#   now reads through ci_triggers.workflow_events. A column-0 `on:` list (`on:` then
#   `- issue_comment`) was unread and so credited as running on changes; it is now
#   read, so review_automation can move from credited to not credited for a review
#   action wired only to `issue_comment`, `issues` or `workflow_dispatch`. Nested list
#   items (`branches: - main`, `types: - created`) are no longer read as events. A flow
#   list or mapping that runs onto later lines (`on: [workflow_dispatch,` then
#   `push]`) is joined in both readers, so ci_checks.enforced_check_runs can move from
#   false to true and review_automation from not credited to credited.
# - ci_triggers moves on flow collections (issue #1036): a flow list or mapping opened on
#   the `on:` line and continued below (`on: {` then `issue_comment: {}` then `}`) reads
#   its events where it read `unknown`, so ci_checks.enforced_check_runs can move from
#   null to true or false; one that never closes (invalid YAML) reads `unknown`, where
#   it read its first line's events, so enforced_check_runs can move to null. Such an
#   unclosed `on:` is never credited by review_automation (`ci_triggers.unclosed_on`).
# 39 (3.1.0 release): 53 rule changes since 38.
# - issue #799, a CI command continued with a trailing backslash is joined onto its first
#   line before it is read, so mvn ${MVN_ARGS} -T8 \ ... verify reads verify and credits
#   test_or_lint_configured and enforced_check_runs (Layer 5 level 1), where it read
#   no_check_command. A GitHub ${{ ... }} expression reads as one word, and a shell
#   expansion before a make, mvn, gradle, go or cargo goal is not read as the goal.
# - issue #799, a Maven run with -DskipTests or -Dmaven.test.skip and only lifecycle phases
#   (mvn -DskipTests verify) reads enforced_basis check_target (null) on a CI line, where
#   it read true. With no other check, test_or_lint_configured reads null with the new basis
#   value check_target, since verify may still run a lint plugin. A plugin goal beside it
#   (checkstyle:check) is read as before.
# - issue #799, a run, script or command input under a uses: step's with: reads as a shell
#   run only when the action runs it: a local action whose committed action.yml has a run:
#   step of exactly ${{ inputs.<name> }}, or nick-fields/retry, reactivecircus/
#   android-emulator-runner, coactions/setup-xvfb, gabrielbb/xvfb-action,
#   uraimo/run-on-arch-action and the vmactions/<os>-vm family. An
#   actions/github-script script is not read. Any other action's input still counts in
#   test_or_lint_configured but reads check_target (null) for enforced_check_runs and
#   lint_runs, where it read true.
# - issue #799, xvfb-run [flags] and timeout [flags] DURATION are read through to the
#   program they run, and a committed CI script reached through a tracked symlink that
#   leaves the checkout is never read.
# - Doc freshness leaves generator-directive pages out of the staleness share (issue #816):
#   a mkdocstrings `::: pkg.mod` stub renders from code at build time, so it leaves
#   doc_staleness.summary.measured_docs and no longer reads as a stale hand-written doc.
#   A hand-written page that embeds one directive is still measured.
# - A parallel docs/ page maps to one production module (issue #817): never to test or
#   fixture code, by its stem (a module file or directory) before its parent directory's
#   name, with ties broken by the longest shared path suffix, then depth, then path. A page
#   that once mapped to a mirrored tests/ directory or to a whole parent-named sibling
#   directory is now measured against the module it documents, or falls back to its
#   explicit links or the repo baseline when only test code matches.
#   A stem common to many modules (setup, config, main) matches only a directory, and a
#   top-level docs/ page never maps to the docs/ directory itself.
#   A module file that shares only its stem with the page yields to the page's explicit
#   code links.
# - Layer 4 level 2 measures file size over production source, not over every scored file
#   (issue #820): stats_summary.loc_by_class.source.p95 replaces stats_summary.loc.p95, and
#   the test set is reported apart in stats_summary.loc_by_class.test.
# - observability.rung reads instrumentation from code as well as manifests, and the
#   runbook name pattern no longer matches ordinary words (issue #823). A Python
#   `logging.getLogger`, Go `log/slog`, `java.util.logging`, SLF4J, Android `Log`, Apple
#   `OSLog`, Erlang/Elixir `:telemetry` or `ActiveSupport::Notifications` call in
#   production source counts as instrumented, as do the Rust `tracing` and `log` crates in
#   a Cargo.toml runtime dependency table and `mix.exs`, `Package.swift`, `pubspec.yaml`
#   and `gradle/libs.versions.toml` manifests. Manifest names match whole (no letter or
#   digit either side), so `hotel-booking` no longer reads as OpenTelemetry. A manifest,
#   logger or config under a test, fixture, example, vendored or generated path
#   (lib/path_classes.py) is not credited, and a doc under a test or fixture path is not a
#   runbook. On-call in a doc name needs a word or path-segment boundary, so
#   `function-call-in-default-argument.md` is not a runbook. The rung can move either way.
# - Promissory markers set aside unambiguous fixture trees (issue #824): testdata/, test_data/,
#   test-data/, __fixtures__/, __snapshots__/, mdtest/ and resources/test/ at any depth, and
#   fixture/, fixtures/ or snapshots/ below a test directory, not only tests/fixtures/. A
#   production tree named like one (core/snapshots/, a packages/fixtures package) still counts,
#   and a scoped run reads the path relative to the unit. A linter's fixture noqa no longer
#   counts toward promissory_markers.families.suppression.total or .stale (Layer 3 levels 1
#   and 2), so both can fall on a repository with fixtures outside tests/fixtures/.
# - issue #859, GitLab `project:` and `component:` includes match the ghsync clone ignoring
#   case, as GitLab paths do. `project: Grp/CI-Templates` reads the clone `grp/ci-templates`
#   on Linux too, where it read `not_cloned` (and `test_or_lint_configured` null); macOS and
#   Windows already found it. Two clones differing only in case are ambiguous and read
#   `not_cloned`.
# - A monorepo run with two or more units shows the unit roll-up under the headline (issue
#   #865): the lowest unit's Step with the measured units at the next Step, then the change
#   that moves the most units to their next Step, a change at the root ranked first on a tie.
#   The repository's Step and headline are unchanged; a single-unit run renders as before.
# - coverage_gate reads Codecov status targets (issue #882, lib/testing/coverage_codecov.py): each
#   coverage.status.project / .patch status in the first codecov.yml Codecov reads is a gate
#   with tool codecov. A project `target: auto` (or no target) makes
#   threshold_meets_minimum null with unmeasured_reason `codecov_target_not_read` where it
#   read false; a numeric project target counts (80% is a gate at 80) and
#   enforced_meets_minimum reads null with enforced_basis `status_check_unconfirmed` where
#   it read false, since whether branch protection requires the status is not read. A
#   patch status is left out of threshold_meets_minimum and of the CI-run reading (an
#   `auto` target is left out of the CI-run reading too, so neither nulls an unrun floor's
#   false), so the
#   other gates decide (project 20% beside patch 100% reads false); when patch statuses
#   are the only gates, the reading is null with `codecov_patch_only` where it read false.
#   Repositories with no Codecov status section are unchanged.
# - Name-only pinned agent matches are hints that need confirmation (issue #883, decision on
#   #883 of 2026-11-22). A pinned match with no `[bot]` suffix or verified app identity
#   (`noreply@anthropic.com`, `copilot@` addresses, the `copilot` login, `cursoragent@cursor.com`,
#   OpenHands' address and `openhands-agent`) no longer counts as an agent author until it is
#   confirmed in `[identities]` or matched by an `[[ai_usage.agent_identities]]` rule:
#   - `ai_usage.agent_authored_*` and `agent_either_*` can fall, and
#     `identities_awaiting_confirmation` can rise (new `pinned_agent_hint` evidence);
#     `covered_author_agents` names only the `[bot]` apps, and `limits` gains one line.
#   - `delivery.agent_authored_share` reads null while a sampled change author is an
#     unconfirmed name-only login (such as `copilot`); `delivery.unconfirmed_ai_accounts`
#     counts those accounts.
#   - GitLab notes: a note by a `delivery.AI_REVIEWERS` username or a hinted login leaves AI
#     review undecided (and a human comment it precedes), so `delivery.ai_review_share`,
#     the human comment figures and `review_reality`'s GitLab shares read null until the
#     account is confirmed, where they read a number. A note by an account confirmed as a
#     bot is no longer a human comment (#765 semantics).
#   - Unchanged: `[bot]`-suffix apps, AI co-author trailers, GitHub AI review (a bot-gated
#     `AI_REVIEWERS` login) and the Copilot coding agent's commit address
#     `198982749+Copilot@users.noreply.github.com`, now pinned by its GitHub id in
#     `AGENT_RULES` (it counted before through the `copilot` login rule).
# - Under a caller's own maturity model whose Step 1 has a gate the repository does not meet,
#   the headline now carries the Next line naming Step 1 and its gates (#889). The default model
#   is unchanged: its Step 1 has no gate.
# - Below the first Step of a caller's own model whose Step 1 has a gate, the executive summary
#   reads `Not yet at Step 1 of M.` with a Next line and a call to action, instead of nothing (#889).
# - Under a caller's own model, a Step whose name differs from the default model's now reads
#   the model's own Step name in the executive summary, not the default ladder sentence (#889).
# - The badge of a measured repository below Step 1 takes Step 1's colour, not the not-measured
#   grey (#889).
# - issue #893, a known lint action step whose literal with: input makes it install only is
#   no lint run: astral-sh/ruff-action and chartboost/ruff-action with args: --version (or
#   -V, --help, version, help), and golangci/golangci-lint-action at v9 or later with
#   install-only: true, read lint_runs false (no_lint_command) where they read true. A ruff
#   args or golangci install-only value set but not settled (a ${{ }} expression, an empty
#   args, an install-only value other than true or false, install-only: true on a SHA or
#   branch ref) reads check_target (null). A value whose only expression is one
#   ${{ matrix.<key> }} is read per value of the job's literal matrix axis: any leg that
#   lints keeps the credit, all legs install only read false. install-only: false,
#   install-mode, only-new-issues, a golangci ref before v9 (where install-only is not an
#   input) and a with: the reader cannot read (a merge key, a quoted key, an alias) keep the
#   credit. Layer 5 and test_or_lint_configured do not move.
# - issue #893, a reviewdog/action-* step reads lint_runs null (check_may_pass) where it read
#   true: every listed action defaults to fail_level none and fail_on_error false, so the step
#   never fails its job. Only a literal fail_level of any, info, warning or error keeps the
#   credit; fail_on_error: true alone reads null (reviewdog ignores it when a fail level is
#   passed), apart from action-tfsec, which passes only -fail-on-error. An expression or
#   another casing (Error) reads null.
# - issue #893, a folded (- >, run: >) or plain multi-line CI scalar is read as the one
#   command YAML makes of it: a GitLab item - > / ruff / --version runs ruff --version and
#   is no lint run. A literal block (run: |) still reads one command per line.
# - issue #923, a GitHub step that uses a local composite action (uses: ./.github/actions/x)
#   is read through the action's run: steps, one level and repo-local only, never executed:
#   a composite that runs ruff check . and pytest credits test_or_lint_configured,
#   enforced_check_runs and lint_runs, where it read no_check_command. The caller's
#   continue-on-error and if: false mask every copied step, and each step keeps its own
#   shell:, continue-on-error and || true masking. ${{ inputs.<name> }} in a copied run: takes
#   the caller's literal with: value or the action's literal default; one left in command
#   position, and a nested local action (not followed), read check_target (null). A run: whose command is
#   ${{ matrix.<key> }} is read once per literal matrix value (include and exclude applied),
#   so cmd: ['ruff check .', 'pytest'] is credited; a matrix command that cannot be expanded
#   (a fromJSON matrix, an unset key) reads check_target (null) on all three signals where
#   it read false. A block-list matrix value such as - ruff check . that a step runs is no
#   longer counted again outside that step.
# - Shell, make and hatch commands are parsed per program (issue #955, `lib.command_flags`), at
#   Layer 3 (`lint_runs`) and Layer 5 (`enforced_check_runs`):
#   - A shell's `-e` takes no value, so `sh -e ./ci/run.sh` and `bash -e scripts/test.sh` run
#     their script (false before; now null and true). `-o`, `--rcfile` and `--init-file` take one.
#     `--norc` is not `-c`.
#   - `bash -c "<cmd>"` reads its string as the command when it is one plain command (`bash -c
#     "make test"` reads true). A list, pipeline, redirection or substitution in the string, or
#     a string that cannot be split, reads null rather than false.
#   - make's `-j N`, `-l N`, `-o` and `-W` values are no longer targets, for checks and lint
#     alike, so `make -j 4 build` reads false (was null) and `make -j 4 lint` is a lint run.
#   - A make run whose recipes cannot fail it runs no check and no linter: a dry run (`-n`,
#     `--dry-run`, `--just-print`, `--recon`, `-t`, `-q`, or a cluster such as `-kn`) or
#     `-i` / `--ignore-errors`. Real make exits 0 with a failing recipe, so `make -n test` moves
#     from met to false. `-k` still exits 2 and still counts.
#   - `hatch run lint` has the name trust of `make lint`: it is a lint command and a lint run
#     (Layer 3 true, was null).
# - Layer 0: instruction files are read by the same classifier, so a CLAUDE.md that names
#   `bash -e scripts/test.sh`, `bash -c "make test"` or `bash --rcfile rc ./test.sh` now names
#   a check (`agent_environment.check.named_total` can move from 0 to 1), as CI reads it.
# - issue #1008, a check whose failure the step may hide reads not measured. On a CI
#   line the scan reads as a check, a || fallback that can pass (pytest || echo failed),
#   a check before && with more of the step after it, and a check inside a multi-line
#   { } group or function body called before && or in a condition read
#   enforced_check_runs and lint_runs (Layer 5 and Layer 3 level 1) null with the new
#   basis check_may_pass, where they read true, and the coverage gate's CI run reads
#   enforced_meets_minimum null with enforced_basis check_may_pass. || false, || exit 1,
#   a check as the last statement and a plain group or function call keep their credit.
#   The masking rules also cover if, while and until conditions and one-line groups and
#   functions, but the scan reads no check on a line that opens with if, while, until,
#   { or f(), so those read as before.
# - issue #1008, a GitHub ${{ ... }} expression on a run line holds no shell operator:
#   pytest -n ${{ matrix.workers || 'auto' }} is no longer split at its ||, so the new
#   fallback rule does not read it as pytest || 'auto' (it stays credited).
# - A scoped coverage_gate run reads the root coverage.py config (issue #1032): a Python
#   scope with no coverage.py config of its own counts the root `fail_under` when CI runs
#   coverage from the repository root (or names the root config with `--rcfile` /
#   `--cov-config`), so coverage_gate.threshold_meets_minimum and enforced_meets_minimum
#   can move from false to true. When where CI runs coverage is not resolved and the root
#   floor would change the reading, threshold_meets_minimum reads null with
#   unmeasured_reason `root_coverage_config_unresolved`, where it read false. Whole-repo
#   runs are unchanged.
# - `instruction_placement.loaded_in_full` lists an agent or `SKILL.md` file over 200 lines
#   or over 5000 estimated tokens, most tokens first (issue #1045); it was over 200 lines,
#   longest first, so a long-line file under 200 lines (about 20k tokens in this repository)
#   was left out. Informational: no condition or Step reads it.
# - a new counted condition, `skill-disclosure.1` (issue #1045): every SKILL.md and agent
#   definition within 500 body lines and 5000 estimated tokens, with a name, a description
#   of 1024 characters or fewer, and references that resolve one level deep. Not measured
#   (`no_skill_files`) with none. It gates no Step, so no Step moves; "Practices in place"
#   can.
# - a new counted condition, `contributor-files.1` (issue #1045, tag `docs`): a CONTRIBUTING
#   guide and a SECURITY policy (or a link to GitHub private vulnerability reporting) at the
#   root, in `.github/` or in `docs/`, or the owner's shared policy in its `.github` repository
#   (read with gh); with none in the tree and the owner's not read, the policy is not measured
#   (`org_policy_not_read`), never missing. Not measured (`knowledge_base`) for a knowledge base.
#   It gates no Step.
# - a new counted condition, `skill-script-tests.1` (issue #1045, tag `behaviour`): every skill
#   that holds scripts has tests beside them in its own folder (presence, not coverage; tests
#   only outside the folder are named but do not count). Not measured (`no_skill_scripts`)
#   with no skill holding a script. It gates no Step.
# - a new counted condition, `skill-personal-data.1` (issue #1045, tag `governance`): no skill
#   or agent file holds an email, phone number, home path, IP address, SSH login or key (names
#   are not detected; a credential-shaped assignment is counted for information only). Not measured (`no_skill_files`) with none. It gates no
#   Step.
# - issue #1046, Gradle dry runs and exclusions and pdm / poe read-through on the CI reader.
#   ./gradlew -m check (and -qm, --dry-run, --task-graph) runs nothing: it reads no check and
#   no lint run where it read enforced_check_runs true and may_lint. -Pm and -Dmode=x are no
#   dry run. A task named after -x / --exclude-task is not run (check -x check, test -x test
#   and ktlintCheck -x ktlintCheck read false), and check -x test reads null (check_target),
#   as mvn -DskipTests verify does. pdm run <program>, pdm [run] <script> and poe <task> are
#   read like hatch run: pdm run ruff check . is a lint run and pdm run pytest a check, where
#   both read nothing. A [tool.pdm.scripts] entry that shadows the program reads null in
#   ci_checks and indirect_run in coverage_gate, and a bare pdm <word> with no such script
#   reads false (pdm exits 2). The Gradle and pdm parsing is one module, lib.runner_args,
#   shared with the coverage gate.
#   pdm scripts are read from every tracked pyproject.toml; with none tracked, pdm run <word>
#   and poe <word> read null (check_target), since pdm run test falls back to the shell's test.
# - issue #1061, the CI-line reader reads five forms by what the shell runs. A line that
#   opens a compound command is read from the command it runs: (pytest), if ! pytest; then
#   exit 1; fi, { pytest; } and f() { pytest; }; f read enforced_check_runs true where they
#   read no check, and if pytest; then echo ok; fi, while and until loops that break and
#   groups called before && read null (check_may_pass), as the #1008 masking rules say.
#   In a GitHub bash or sh run: body with pipefail off (the default bash -e {0}, shell: sh),
#   a check piped into another command (pytest | tee log), or inside a { } or ( ) group piped
#   on ({ pytest; } 2>&1 | tee log), is masked, since the step takes
#   the last command's status: it reads false (not_enforced) where it read true, or null
#   (check_may_pass) when a later statement reads PIPESTATUS or re-raises a status.
#   shell: bash, set -o pipefail and GitLab keep the credit. A function no statement of
#   its run: body calls (f() { pytest; } alone, or named only in echo f) is masked too;
#   one named only as another command's argument (retry 3 f) reads null. dash ci.sh runs a script
#   as sh ci.sh does. A || fallback is judged by the commands that run, not its words:
#   pytest || echo false, pytest || echo exit and pytest || echo failed || exit 1 read
#   null (check_may_pass) where they read true.
# - The headline and badge name gates, never a count (issue #1083): `headline.summary`, the
#   badge message and the log.md readiness line read "Step N · next: <gate>" or "Step N · all
#   gates held"; the Next line names blocking gates only; the tally line is relabelled
#   "Catalogue coverage". `HEADLINE_VERSION` is 3.
# - The report and the log.md entry open with "Since last run" (issue #1083): the actions
#   closed since the previous run, each with its commit (a done action in the committed
#   actions.json whose completed_sha is on HEAD's history and not an ancestor of the previous
#   run's measured commit),
#   the gates gained or lost, and the next blocker; a first run says it is the first run.
# - /assess proposes removals (issue #1084): an instruction file whose always-on text
#   `instruction_placement` flags (over 200 lines and over half the file, the scan's own
#   thresholds) gets a `remove` gap action with its measured always-on line count. It is
#   advice only; the number of practices is never scored.
# - The pruning ledger (issue #1084): "since last run" credits a done `remove` action as
#   "Pruned: always-on text in <file>, saved N always-on lines" (the measured drop) once the
#   file has stayed unflagged and no gate lost for two runs on new commits; a flag or a
#   lost gate resets the count. Nothing is scored.
# - Each skill folder and each plugin `agents/` and `commands/` folder is a unit (issue
#   #1098), with its own readings, Step and place in the unit roll-up. A repository without
#   `SKILL.md` files or a `.claude-plugin/plugin.json` finds the same units as before.
# - The report opens with a three-line executive summary and one call to action (issue
#   #1102): "Step N of M: <what an agent can do here>.", "Next: <the blocking gate as a
#   task>. Then you reach Step N+1: <what it allows>.", "Since last run: ..." (or "First
#   run." / "not known"), then "Start here:" with the gate's task linked to its catalog
#   entry. Plain words: no layer, no condition id and no count of practices.
# - issues #1124 and #1126, Gradle option values, build and project-path tasks.
#   A Gradle option that takes a separate value (--warning-mode all, --console plain,
#   --max-workers 2, -I init.gradle, -F off, -M sha256 and the rest of Gradle 9.4's list,
#   plus -b, -c and the --tests filter) no longer has its value read as the task, so
#   ./gradlew --warning-mode all check credits Layer 5 where it read false. Every task a
#   Gradle line names counts (./gradlew clean check), not only the first.
#   ./gradlew build reads as a check where a tracked build file applies a JVM or Android
#   plugin (java, java-library, application, Kotlin JVM, Android), and null (check_target)
#   where none is read; build -x check, -m build and --dry-run build read false.
#   In the coverage gate, a project-path task (./gradlew :app:jacocoTestCoverageVerification,
#   :app:check) credits only a gate in that project's build file (app/build.gradle[.kts]);
#   a gate in another project's or the root build file reads null (indirect_run). The project
#   path is read under the Gradle root, the nearest tracked settings.gradle[.kts] at or above
#   the -p start or the gate (cd android && ./gradlew :app:... credits android/app/build.gradle);
#   -b / -c read null.
# - `assess_core.py` takes `--profile NAME` or `--levels FILE` (issue #1150): the `level` and
#   `headline` blocks evaluate that maturity model instead of the default. Two profiles ship
#   under `levels/profiles/`: `handover` (every gate plus linters in CI, review evidence and
#   documentation freshness at level 1) and `citizen` (instructions and CI running the
#   tests); each row is a default row, unchanged. Under a profile or a custom model the
#   executive summary opens with the model's name and "Ready for handover (profile:
#   handover)" or the practices still missing, and marks any practice the default model does
#   not define. The Step ladder and the evidence waffle draw the run's model (neither is
#   drawn for a custom file), the since-last-run section compares no gate across two models
#   ("Model changed since last run (default → handover); gates not compared"), and the unit
#   roll-up line says it reads the default model. A default run is unchanged.
#   `/fleet --profile NAME` passes the profile's file as `--levels`.
# - ci_checks reads the toolkit's own regression gate include as running no project tests
#   (issue #1152): a `remote:` of exactly
#   `https://raw.githubusercontent.com/bjcoombs/ai-native-toolkit/<ref>/templates/assess-gate.yml`,
#   or a `component:` ending `/ai-native-toolkit/assess-gate@<version>`, is the include status
#   `toolkit_gate` and counts as read, so a GitLab project whose only include is the gate reads
#   `test_or_lint_configured` false when no job runs a check, where it read null. Every other
#   `remote:` include stays `not_offline`.
# - issue #1153: under the opt-in `--managed-settings` (`assess_core.py`, `assess_signals.py`,
#   `fleet.py`) or a single repository's `[agent_ops] managed_settings = true`,
#   `agent_ops.summary.permissions_encoded` also reads true when a managed settings source on
#   the machine running the check (managed-settings.json, managed-settings.d/, the macOS
#   com.anthropic.claudecode plist, the Windows Policies\ClaudeCode registry value) lists a
#   permission entry, and null (not measured, `managed_settings_unreadable`) when the
#   repository has none and a managed source exists but cannot be read. Without the option the
#   reading is unchanged: the repository alone.
# - A monorepo run with two or more units adds a "Learn from:" line to the unit roll-up for
#   each unit whose next gate a sibling unit already holds (issue #1155), naming up to three
#   such siblings. Counts-only output drops the lines. The Steps and the headline are unchanged.
# - A run whose level result is unavailable leaves `prior-level.json` untouched (issue #1158),
#   where it used to delete it: the previous good run's baseline and pruning ledger survive,
#   and "since last run" says the baseline was kept, naming that run.
# - issues #1162 and #1163, a Gradle run's working directory selects the project it runs in.
#   In the coverage gate, an unqualified task run from a known directory (-p app,
#   --project-dir app, cd app && ../gradlew ..., or the step's working-directory: app)
#   credits only a gate at or under app (app/build.gradle, app/sub/build.gradle). A gate in
#   another project (lib/build.gradle) reads null (indirect_run) where it read true, since
#   allprojects {} or a projectDir override can still reach it. A -p value that is a shell
#   expansion, an absolute path or above the root, or a -p in a directory that is not
#   resolved (a defaults: working-directory), also reads null. With no -p and no
#   cd, at the repository root, the reading is unchanged.
#   The Gradle root is now the nearest tracked settings.gradle[.kts] at or above that
#   working directory, falling back to the gate's own directory only when the directory is
#   not resolved. In a repository with two Gradle builds (a root settings.gradle and
#   android/settings.gradle), ./gradlew :app:jacocoTestCoverageVerification at the root no
#   longer credits a gate only in android/app/build.gradle: it reads null (indirect_run).
#   cd android && ./gradlew :app:... and ./gradlew -p android :app:... keep their credit.
#   The working directory is left unresolved (today's reading) where a cd sits on a line
#   that runs Gradle twice, or after a quote (bash -c "cd app && ../gradlew ...").
# - issue #1164, a GitHub step whose command is an expression or a shell variable
#   (run: ${{ inputs.cmd }} in a reusable workflow, ${{ env.CMD }}, $CMD, ${CMD}) reads
#   check_target (null) on test_or_lint_configured, enforced_check_runs and lint_runs where it
#   read no_check_command (false), unless another line settles them; masked, it reads false.
#   A literal env: value of the step, job or workflow is read in its place, so env CMD set to
#   ruff check . with run: $CMD is credited. A variable only as an argument (pytest $ARGS) or
#   part of a word ($GITHUB_WORKSPACE/ci.sh) reads as before. A shell $NAME is not read
#   from env: when the run value assigns NAME itself, when an earlier step writes NAME to
#   $GITHUB_ENV (either form), or when its value holds ;&|()<> (word splitting runs no
#   operator). A composite action's env: values
#   take the caller's literal with: inputs too.
# - issue #1165, a job- or step-level continue-on-error given as an expression reads
#   check_may_pass (null) for the checks it covers where it read enforced_check (true).
#   ${{ matrix.<key> }} is resolved from a literal matrix: on a step copied per value each copy
#   takes its own value; on the job, or a step not copied, any true value masks (not_enforced)
#   and all false keeps the credit. ${{ true }} and ${{ false }} read as the literals.
# - The pruning ledger credits a file trimmed in several removal rounds once, for the whole
#   drop from the first round's starting size (issue #1168), where it credited each round from
#   its own starting size and double-counted the overlap.
# - a new `secrets-guard` row (issue #1173, tag `governance`): level 1, a secrets hook or CI
#   scan (graded config_only, listed, not counted); level 2, a CI scan an agent cannot skip
#   (graded measured, counted), not measured (`secret_scanning_not_read`) on a GitHub
#   repository with no CI scan. It gates no Step.
# - The finding actions read in plain English (issue #1174): "investigate the seam" reads
#   "check why these files change together", and so on for nine of the eleven actions. An
#   action matched to an earlier run only by its directive text (no `finding`) starts again as
#   pending once; one with a `finding` and files keeps its status.
# - A `--profile` run keeps its own committed since-last-run baseline in `prior-level.json`
#   (issue #1184) and compares gates on its own Steps, so `citizen` and `handover` show gates
#   gained and lost on a fresh clone or in CI. A profile run writes only its own record; a
#   default run keeps every profile record; a custom `--levels` run keeps none. The CI gate's
#   `fail_on_gate_loss` reads the run's own record, so a profile run with no record yet reads
#   "no baseline yet" instead of failing.
# - Manifest text reads in plain English (issue #1186): 33 condition fixes, reason-code
#   meanings and next steps, and scan summaries are split into sentences of 25 words or fewer,
#   and the plain-English lint now runs over every manifest prose field except the whys. The
#   whys are unchanged; shorter versions await the maintainer's approval.
ASSESS_METHOD_VERSION = 39
