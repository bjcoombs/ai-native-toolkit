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
ARTIFACT_SCHEMA_VERSION = "1.11.0"

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
# 6: a bats suite is a test file and credits its shell script (issue #593), so a
#    co-located ``<stem>.bats`` can now produce self_referential_tests (E2).
ASSESS_METHOD_VERSION = 6
