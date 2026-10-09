"""The artifact schema version every /assess run stamps on its outputs.

A leaf module so the orchestrator and the lib modules that write artifacts
(the hotspot wiki pages, the index, the log) read one constant.
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
ARTIFACT_SCHEMA_VERSION = "1.4.0"
