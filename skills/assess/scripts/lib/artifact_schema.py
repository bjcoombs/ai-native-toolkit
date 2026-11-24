"""The artifact schema and method versions every /assess run stamps on its outputs.

A leaf module so the orchestrator and the lib modules that write artifacts
(the hotspot wiki pages, the index, the log, the findings snapshot) read one
constant each.

`ARTIFACT_SCHEMA_VERSION`, the run_id provenance schema stamped on every artifact a run
writes (run-context.json, the badge, the wiki pages); the version history is in the
module comment. `complexity-treemap.py` carries a copy that
`tests/test_complexity_treemap_schema_versions.py` holds equal to it.
It re-exports `ASSESS_METHOD_VERSION`, the finding method version, from
`lib/assess_method.py`, which holds that counter and its history (moved there in the 4.0.0
release, when the two histories together passed the file-size limit).

During a version freeze a change to this counter, or to another counter
`scripts/fold_changes.py` knows, adds a note file under `skills/assess/changes/`
instead of editing the history comment; the release PR folds the notes in with
`make fold` (#1011).
"""

from lib.assess_method import ASSESS_METHOD_VERSION as ASSESS_METHOD_VERSION

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
# 1.44.0 (4.0.0 release): 36 changes since 1.43.0.
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
# 1.44.0 (4.0.0 release): one change, without a bump.
# - run-context.json gains `ci_model_jobs` (issue #1242): each CI job that calls a model API
#   (`path`, `line`, `job`, `platform`, `signs`, `triggers`, `gates`, `state` gated | ungated |
#   unknown, `reason` when unknown, `cancel_superseded`), with `files_read`, `model_jobs`,
#   `gated`, `unknown`, `unread` and `ungated` (null with `ungated_reason` `trigger_unresolved`
#   or `ci_jobs_unread` when none is ungated but one could not be settled or read).
# 1.44.0 (4.0.0 release): one change, without a bump.
# - run-context.json gains `github_secret_scanning` (platform pass): `secret_scanning`,
#   `push_protection` and `delegated_bypass`, or `available: false` (`settings_not_readable` without admin rights).
#   `secrets_guard.sources` and `enforcement.source` can read `platform`.
ARTIFACT_SCHEMA_VERSION = "1.44.0"
