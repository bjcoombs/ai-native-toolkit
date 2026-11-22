"""The artifact schema and method versions every /assess run stamps on its outputs.

A leaf module so the orchestrator and the lib modules that write artifacts
(the hotspot wiki pages, the index, the log, the findings snapshot) read one
constant each.
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
# lib.doc_ownership (issue #555). A repo-baseline row's subject_code_count is now
# 0 (it owns no subject), the same value the documentation block reports.
# complexity-stats.json is unchanged.
# 1.11.0: complexity-stats.json gains complexity_unreliable, the scc-only files
# whose ccn is an order of magnitude below their language's median density
# (lib.scc_coverage, issue #590), and run-context.json copies it into
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
# (fleet_compare_version 2). Readers map the old keys (lib/fleet_legacy.py), so
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
# 1.36.0: run-context.json ci_checks gains unread_ci_files, and basis and
# enforced_basis gain unread_ci_system (issue #787): a tracked CI file of a
# system the scan does not parse (Jenkinsfile, .circleci/config.yml, ...).
# Additive: no existing key moves, and complexity-stats.json is unchanged.
ARTIFACT_SCHEMA_VERSION = "1.36.0"

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
#    (lib.doc_ownership, issue #555): a README beside a design doc owns the
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
# 31: a repository with no readable CI check whose tree tracks a CI file of a
#     system the scan does not parse (Jenkins, CircleCI, Azure Pipelines,
#     Bitbucket Pipelines, Travis CI, Buildkite) reads
#     ``ci_checks.enforced_check_runs`` null (``basis: unread_ci_system``), so
#     its Layer 5 row moves from level 0 to unknown (issue #787).
ASSESS_METHOD_VERSION = 31
