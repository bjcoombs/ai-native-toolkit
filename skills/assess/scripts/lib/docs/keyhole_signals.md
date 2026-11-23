# `lib/keyhole/keyhole_signals.py`

The full reference for `skills/assess/scripts/lib/keyhole/keyhole_signals.py`, whose module docstring holds the summary.

(co-change hotspot)
Integration barrier between the individual signal modules and `assess_core`. Derives
the per-directory containment view from the commit-file sets the orchestrator parses
once and passes in, then assembles all five run-context blocks from the upstream signal
outputs. Emits the named derived findings as a fixed-order structured array (the single
source of order is `FINDING_ORDER`, loaded from the finding entries' `order` in
`checks/core.toml`): `hidden_coupling`, `lying_map`,
`unexplained_complexity`, `untrusted_hotspot`, `self_referential_tests`,
`unactioned_intent`, `accretion_ratchet`, `orphaned_understanding`,
`candidate_dead_weight`, `override_contradicts_signals` (archetype marker disagrees with
the deterministic signals - fed the `archetype` block), and the one positive
`refactor_boundary` last. After assembly `apply_config_excludes` drops any finding path a
user config exclude (`exclude_dirs`/`exclude_patterns`) covers - the git-log-derived
findings never saw the scan-level filter - and returns the dropped paths as
`excluded_finding_paths` so `assess_core` can serialise the `excluded_by_config`
disclosure (a suppressed finding is counted and named, never silently vanished).
`exclude_archive_from_attention` then builds the attention list with any path under an
`archive/`, `archived/` or `attic/` directory left out (so it never becomes a prescribed
action) and returns those paths as `archived_finding_paths` for the `excluded_as_archive`
disclosure; the findings themselves still name them. Rows of equal score are ordered by
`attention_tie_break` (an `AttentionTieBreak` built from data the run already holds):
`top_hotspots` members first in hotspot rank order, then descending severity (the highest
`promissory_markers.top_offenders[].severity` for an `unactioned_intent` file, divided by
the run's highest so it shares the 0-1 scale of `1 - containment_ratio` for a
`hidden_coupling` directory; neither finding type outranks the other by scale alone),
then path. `is_attention_low_signal` marks the list low-signal when its top score is 1
(no row lands in two negative findings; `False` for an empty list), and `integrate` then
caps `prescribed_actions` at the rank-1 row instead of three; the flag is serialised as the
run-context `attention_low_signal`. Before either filter, the commit
sets are folded through the rename map (so a renamed directory's history lands on its
current name), and `prune_missing_finding_paths` drops any `hidden_coupling` or
`refactor_boundary` path absent from the working tree, returning them as
`pruned_finding_paths` for the run-context block of that name (`paths`, `count`). The
prune stands down when the rename map is incomplete, since an unfolded old path is not
evidence of a deletion, and `rename_map_complete` carries that state into the block so
the report can say renames were not read. Each
block build is wrapped in a catch-all so one signal's failure degrades that block to
`available: False` rather than crashing the run. `render_findings_markdown` renders at most
`MAX_FINDING_PATHS_RENDERED` paths per finding and `MAX_ATTENTION_ROWS_RENDERED`
attention rows, and discloses any cut with an italic omission row naming the
`run-context.json` key that holds the rest (#440). It also runs `structure_drift.py`'s Tier 1
grouping disagreement (fed the behaviour block's co-change pairs so no second git-log parse
happens) and folds its hidden-seam direction into the `hidden_coupling` finding, returning the
Tier 1 result for `assess_core` to serialise into the `structure_drift` run-context block - so
structure-drift findings flow through this barrier rather than being assembled in the orchestrator.
`integrate` itself only sequences named steps: `_prepare_commit_sets` (parse, rename-fold,
scope), `_integrate_blocks` (the four `_safe_block` builds), one `_*_paths` helper per finding
that needs more than a list comprehension, `_finding_path_inputs` (the `assemble_findings`
input, with the degenerate-churn gate on `_CHURN_DERIVED_FINDINGS`) and `_filter_findings`
(prune, then config excludes). A new finding adds a helper and a key there, not a branch in
`integrate`; the C901 gate keeps each step under ccn 15. The attention list and every
product rendered from the findings (`findings_markdown`, `keyhole_summary`,
`attention_low_signal`, `prescribed_actions`, `archived_finding_paths`) come from one
function, `finding_products`, which `mutation_refresh.py` calls too. After
`_filter_findings`, `integrate` calls `reviewed_findings.mark_reviewed`, and
`finding_products` reads every product from `reviewed_findings.open_findings`, so a
reviewed path stays in `derived_findings` but not in the attention list, the
prescribed actions, the summary or the rendered path lists.
