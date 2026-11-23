"""The counts-only allow-list: which run-context string fields survive, and as what.

``lib.counts_only`` keeps every number, boolean and null in a run-context and
decides each string by the table here. A field pattern is the dotted run of
dict keys from the top of ``run-context.json`` down to the value, with list
levels left out and the key of a path-keyed map written ``{}``:
``behaviour.hidden_coupling_findings[3].coupled_pairs[0].file_a`` is
``behaviour.hidden_coupling_findings.coupled_pairs.file_a`` and
``instruction_files["CLAUDE.md"].grade`` is ``instruction_files.{}.grade``.

Five classes:

- ``PATH``: a repo-relative path, directory, glob or dotted module name. Kept
  as written, or replaced by a salted hash when path hashing is on.
- ``TOKEN``: a kind, grade, band, status, date or version. Kept only while it
  is a short slug (``TOKEN_RE``); anything longer or with spaces is dropped.
- ``REASON``: an unavailable block's reason, kept as its code only: the
  ``code`` of a ``code: sentence`` reason when it is a known structure code
  (``import_graph.STRUCTURE_REASON_CODES``, a fixed allow-list) or
  ``no_ownership_map`` (``reason_codes.BLOCK_REASON_CODES``), or the code a
  known sentence maps to (``REASON_SENTENCE_CODES``), else ``unavailable``. The sentence never
  survives; an empty reason (an available block) is dropped.
- ``ID``: a stable identifier of another repository or marketplace, the join
  key of a fleet roll-up (``kind:owner/repo``). Kept while it has no
  whitespace or e-mail address; hashed with the paths when hashing is on.
- ``DROP``: free text, file contents, commands, commit subjects, program names
  and identities. Removed (a dropped list of strings becomes its length).

The per-field rules (``RULES``) are generated from the check manifest: each
collector's ``privacy`` table in ``checks/core.toml`` (#795). The global rules, the
keyed-map sets and the fleet-data rules stay here.

A string field with no rule is dropped too: the table is an allow-list, so a
new scan field carrying text cannot leak before someone classifies it. The
tests walk the TypedDicts in ``lib/run_context_types.py`` (and the block
shapes beside their builders) and fail on any string field missing here, so a
new field is classified on purpose rather than dropped by accident.

Lookup order for a pattern: the exact pattern, then ``<parent>.*``, then a
global leaf rule (``**.<leaf>``): path field names are paths, and a ``reason``
is dropped text wherever no rule says otherwise.

The counts-only allow-list: `RULES` maps each collector's field patterns (generated from
the collector's `privacy` table in `checks/core.toml`, #795; dotted keys, list
levels left out, `{}` for a path-keyed map key) to path, token or dropped text, with
an `ID` class for external join keys such as `shared_tooling` source ids (kept, hashed
with the paths), a `REASON` class for an unavailable block's reason kept as its code
only, `REASON_SENTENCE_CODES` (the structure reason sentences that predate the
`code: sentence` form, shared with `headless_layers.structure`), `PATH_KEYED_MAPS`, `IDENTIFIER_KEYED_MAPS` (kind- or tool-keyed maps), the global path
field names and `classify`. Every other object is a record whose keys survive only when
they are known field names (`EXTRA_FIELD_NAMES` for dict-literal blocks, plus the typed
fields and rule segments), so a map keyed by a login or a function name is dropped
rather than kept unhashed. Adding a run-context string field or block means adding its
rule to its collector's `privacy` table in `checks/core.toml` (or a field name here) and
regenerating `references/counts-only.md`. The global leaves, the keyed-map sets,
`EXTRA_FIELD_NAMES` and `FLEET_DATA_RULES` stay in this module.
`FLEET_DATA_RULES` and `classify_fleet_data` classify the fleet's `fleet-data.json`
fields (#703), kept apart from `RULES` so their names never widen the run-context field
names. Tests: `tests/test_counts_only.py`, `tests/test_fleet_data.py`.
"""
from __future__ import annotations

import re
from typing import Final, Literal, cast

from lib.manifest.check_manifest import core_manifest, privacy_rules

FieldClass = Literal["path", "token", "reason", "id", "drop"]
PATH: Final = "path"
TOKEN: Final = "token"
REASON: Final = "reason"
ID: Final = "id"
DROP: Final = "drop"

# A kept token: a slug of at most 40 characters with no spaces, slashes or "@"
# (so no sentence, path or e-mail address passes as a kind).
TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:+-]{0,39}$")

# Reason sentences written before the ``code: sentence`` convention, as
# (substring, code). The headless structure marker and the REASON class both
# read this table, so a full run and a signals run name the same code (#764).
REASON_SENTENCE_CODES: Final[tuple[tuple[str, str], ...]] = (
    ("grimp not installed", "no_grimp"),
    ("networkx not installed", "no_networkx"),
    ("grimp failed", "grimp_failed"),
    ("no importable Python", "no_python_packages"),
)

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
    "unit_readings.units.blocks",
    "unit_readings.units.blocks.promissory_markers.families",
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
    # stats_summary: the size split by path class (#820; its fields are lib.run_context_types_size)
    "loc_by_class",
    # excludes and pruned paths
    "affected_finding_paths", "dirs", "patterns", "rename_map_complete",
    # promissory markers
    "aging_reliable", "families", "stale_by_file", "stale_touches_threshold",
    "todo_bare", "todo_linked", "total_markers", "total_stale",
    "stale_agent_introduced", "agent_introduced", "justified", "linked", "stale",
    "family", "text", "commit", "survived_touches", "severity",
    # structure_drift tier 1, test_focus, instruction files
    "tier_1", "allowlist_pairs_count", "seam_allowlist_applied", "matched_patterns",
    "total_patterns", "boundary_patterns",
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

# Collector -> {field pattern: class}, generated from each collector's ``privacy``
# table in the check manifest (``checks/core.toml``, #795), in scan-table order, so the
# document (``render_doc``) lists each collector's fields together. A pattern is
# classified by one collector only (the manifest loader rejects a second). The rules
# below (``FLEET_DATA_RULES``, the global leaves and the keyed-map sets) stay in code.
RULES: Final[dict[str, dict[str, FieldClass]]] = {
    collector: cast(dict[str, FieldClass], table)
    for collector, table in privacy_rules(core_manifest()).items()
}


# The fleet's ``fleet-data.json`` (lib/fleet/fleet_data.py, issue #703): field pattern -> class,
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
    # #746: the repositories an opportunity lifts, and the shared CI source id (kept
    # readable in counts-only like shared_tooling ids; under a salt the scan's own hash)
    "opportunities.biggest_lift.repos": PATH,
    "opportunities.shared_templates.repos": PATH,
    "opportunities.shared_templates.source": PATH,
    "opportunities.shared_templates.kind": TOKEN,
    # #944: the owner's exclusions (.assess/config.toml); the reason is the owner's own words
    "repos.exclusions.kind": TOKEN,
    "repos.exclusions.source": TOKEN,
    "repos.exclusions.reason": DROP,
    # #945: each Step gate that does not hold, as tokens (gate id, state, disposition)
    "repos.gate_blockers.gate": TOKEN,
    "repos.gate_blockers.state": TOKEN,
    "repos.gate_blockers.disposition": TOKEN,
    # #1073: the per-tag tally names a tag from the fixed truth-tag list; every other field is a count
    "repos.tags.tag": TOKEN,
    "views.units.tags.cells.tag": TOKEN,
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
    # #746: maturity-model ids and the run's own wording (counts and model titles only)
    "opportunities.in_place.row", "opportunities.biggest_lift.row", "opportunities.biggest_lift.signal",
    "opportunities.biggest_lift.gate", "opportunities.biggest_lift.change",
    "opportunities.biggest_lift.done_when", "opportunities.biggest_lift.text",
    "opportunities.shared_templates.row", "opportunities.shared_templates.signal",
    "opportunities.shared_templates.text",
    # #944: the level result's ids, states and reason codes (lib/level_tally.py), copied as
    # level.json writes them, and the model's Step gates
    "repos.next_step.blockers.kind", "repos.next_step.blockers.gate", "repos.next_step.blockers.condition",
    "repos.next_step.blockers.state", "repos.next_step.blockers.reason",
    "repos.next_step.blockers.disposition", "repos.next_step.blockers.exclusion_kind",
    "repos.exclusions.id", "views.units.step.next_step.gates.gate", "views.units.step.next_step.gates.row",
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
