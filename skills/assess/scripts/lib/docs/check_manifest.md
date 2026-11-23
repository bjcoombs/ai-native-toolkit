# `lib/check_manifest.py`

The full reference for `skills/assess/scripts/lib/check_manifest.py`, whose module docstring holds the summary.

(#789 step 1, #792)
The strict loader for the check manifest, `skills/assess/checks/core.toml` (owner
`core`), which declares every check once. Its published contract is
`skills/assess/references/check-manifest.schema.json` (JSON Schema 2020-12, one manifest
per owner). `load_manifest(path=CORE_MANIFEST)` reads the TOML with stdlib `tomllib`,
checks it with allowed-key sets per entry kind and the cross-reference rules below, and
returns the document itself as a `CheckManifest` TypedDict (no defaults filled, nothing
re-keyed, so `json.dumps` of it validates against the schema). Any error raises
`CheckManifestError`, whose `errors` lists every structured finding
(`check_validation.ValidationError`); `validate_manifest(data)` returns them without
raising, and `parse_manifest(data)` validates an already-parsed document. Helpers:
`collectors`, `signals`, `conditions`, `findings` (by `order`), `scan_key` (a collector
id minus its owner), `block_collectors` (run-context block to publishing collector id)
and `condition_key(row, level, signal)`.

**Whys (#939).** `MANIFEST_VERSIONS` is `(1, 2)`. Version 1 loads with no whys and rejects
any `why`, `why_source`, `whys`, `why_sources` or root `tags`; version 2 requires each, as
`lib/manifest_whys.py` and the "Check manifest whys" section of `lib/README.md` describe.
The why findings run after the cross-references, so they appear only on an otherwise valid
manifest.

**Fragments (#982).** A root manifest may list `fragments` instead of declaring
`[[entries]]`. The core does: `checks/core.toml` holds `manifest_version`, `owner` and
`fragments`, the scan keys in scan-table order, and each entry lives in
`checks/core/<scan key>.toml`. A fragment holds `[[entries]]` only: exactly one collector,
the one its file is named for, plus the signals, conditions and findings it owns (a signal
names its collector, a condition reads one signal, a finding belongs to the collector of
its first input). The loader merges the fragments by kind (collectors, signals,
conditions, findings) and, within a kind, in the listed order, so the merged document is
the one a single file declares, and an error names the fragment
(`core/ci_checks.toml entries[0] (core.ci_checks).stage`). Layout errors:
`MISSING_FRAGMENT` (listed, no file), `UNLISTED_FRAGMENT` (a file the list leaves out, which
would not load), `DUPLICATE_FRAGMENT`, `FRAGMENT_COLLECTOR` (not one collector named for
the file), `FRAGMENT_OWNER` (#994: an entry outside the fragment of its owner, naming the
entry, its fragment and the expected one; an owner that does not resolve or that two fragments claim, or an id
declared twice, is left to validation) and `FRAGMENTS_AND_ENTRIES`. An id declared in two
fragments is `DUPLICATE_ID`, naming both files. A new collector adds its fragment and one line to `fragments`; a change
to an existing check edits its own fragment only. `read_manifest(path)` returns the merged
document unvalidated (the registry-agreement tests read it), and `fragment_names(path)` the
list. A single-file manifest (any third-party owner) loads as before.

The manifest has `manifest_version = 1`, `owner`, an optional `owner_version` and
`[[entries]]` of four kinds:

- `collector`: one `scan_registry.ScanSpec`, id `core.<scan key>`, in scan-table order.
  `callable` (`module:function`), `offline_callable`, `publishes` (the run-context keys
  written: `[key]`, the spread keys with `spread = true`, or `[]` with `private = true`),
  `reads`, `stage`, `degrade`, `gate_reason` (a token for one of
  `scan_registry.GATE_REASONS`: `setup`, `bad_scope`, `self_degrading`, `serialiser`),
  `label`, `network`, `platform_pass` (`signals.PLATFORM_SCANS`), `platform_only`
  (`signals.PLATFORM_ONLY_SCANS`), `unit_scope` (`scoped`: a monorepo unit's reading is
  this scan run again on the unit; `override`: the repository-wide reading, replaced when
  the unit holds its own evidence; absent: repo-wide, inherited by every unit;
  `lib/unit_readings.py`, #863), `headless` (published block to `registered`,
  `headless_variant` or `unregistered`; an unlisted block is not reported headless),
  `headless_variants` (the `signals.HEADLESS_SCANS` spec for a block, as `block`,
  `callable`, `reads`, `stage`), `shapes` (the `counts_only_schema.ROOTS` typed roots
  under its blocks), `privacy` (its counts-only patterns and classes, from which
  `counts_only_rules.RULES` is generated; a pattern is classified by one collector only;
  `archived_finding_paths` sits with the private `keyhole` collector that produces it),
  `unknown_reasons` (`code`, `carried_in` `reason`, `basis`, `enforced_basis` or
  `unmeasured_reason` (a code carried in `enforced_basis` is a `ci_checks.enforced_basis`
  token, #1031), an optional `block` naming the published block whose reason carries it,
  `means`,
  `next_step`; a trailing `<name>` in a code stands for a family, as in
  `no_import_graph_<language>`),
  `revision` (starts at 1; bumped when a published value changes meaning, #774, as
  `ci_checks`, `review_automation` and `coverage_gate` have been) and an optional `evidence`. Collector `evidence` is left to #789 step 6; only
  signals carry it now.
- `signal`: a leaf a default criterion reads, id `core:<path>`, with `path`, `type`
  (`bool` or `number` here), `collector` (the collector publishing its first segment),
  `evidence` (the producer input class, which generates `level_signals.SIGNAL_INPUTS`,
  #783, #953) and
  `null_means`. No grade is authored: it follows from the class
  (`level_signals.INPUT_GRADES`).
- `condition`: display metadata for one default criterion, keyed
  `<row>.<level>:<signal>` (`layer-5-ci.1:ci_checks.enforced_check_runs`, unique per
  manifest), with an id starting `core.cond.` (core uses `<row>.<level>.<leaf>`) and a
  supportive `fix`. The criterion itself stays in
  `levels/default.toml`; a custom `--levels` file needs no entries.
- `finding`: a derived finding, id `core.finding.<name>`, with `order` (the report and
  severity order, `keyhole_signals.FINDING_ORDER`), `action` (`FINDING_ACTIONS`), `mode`
  (`FINDING_MODES`), `concern` (`assess_config.GATE_CONCERN_FINDINGS`), `inputs`,
  `derive`, `thresholds` (the `coupling_analysis` defaults for `hidden_coupling`),
  `suppressed_when` (the churn suppression, `keyhole_signals._CHURN_DERIVED_FINDINGS`;
  until the rule evaluator the loader accepts only `doc_staleness.churn_degenerate ==
  true`), `prune_missing_paths` (`keyhole_signals.GIT_HISTORY_FINDINGS`) and `headless`.
  `inputs`, `derive` and `headless` are documentary: no table is generated from them.
  `inputs` is checked on its typed-root fields or first segment, `derive` as a callable,
  and `headless` against the `derived_findings` block's headless status.

Loader rules beyond types: an id has its kind's form; a private collector publishes
nothing and is not spread; a plain collector publishes `[key]`; opting out of degrading
names a `gate_reason`; `offline_callable` needs `network`, `platform_only` needs
`platform_pass`; a `headless` block or `shapes` path is published by the collector, and a
block is `headless_variant` exactly when a variant names it; every read is a core input
or an earlier collector at the same or an earlier stage, and no key shadows a core
input or publishes a block another collector publishes (the `scan_registry.validate`
rules); a signal's collector exists and publishes its block; a condition key is unique and names a
declared signal; finding orders are distinct and every finding input names a block or collector; a
privacy pattern is classified by one collector; a reason code's `block` is published by
its collector; `suppressed_when` is the one supported expression.
An unknown key or reference carries a "did you mean" `suggestion` and the `available`
names. The module imports none of the tables it describes; they load from it.

**Generated at import (#795, #789 steps 3 and 4).** `core_manifest()` loads and validates
`checks/core.toml` once per process. `finding_tables` builds `FINDING_ORDER`,
`FINDING_ACTIONS`, `FINDING_MODES`, `GIT_HISTORY_FINDINGS`, `_CHURN_DERIVED_FINDINGS`
(`keyhole_signals`) and `GATE_CONCERN_FINDINGS` (`assess_config`); `finding_thresholds`
gives the `coupling_analysis` defaults (`DEFAULT_LOW_CONTAINMENT`,
`DEFAULT_HIGH_MODULARITY_Q`, `DEFAULT_HIGH_FRONT_DOOR_RATIO`); `privacy_rules` builds
`counts_only_rules.RULES` (scan key to privacy table, in scan-table order); and
`reason_codes(manifest, block)` feeds the per-block reason descriptions of
`signals_schema`. Editing an entry's `order`, `action`, `mode`, `concern`,
`prune_missing_paths`, `suppressed_when`, `thresholds` or `privacy` changes the runtime
tables; after a `privacy` change regenerate `references/counts-only.md`, and after an
`unknown_reasons` change regenerate the signals and fleet-row schemas.

**Agreement test:**
`tests/test_check_manifest.py` checks it in both directions against `scan_registry.SCANS`
(`test_registry_matches_manifest`: one collector per scan key, equal `callable`, `reads`,
`stage`, `publishes`, `private`, `spread`, `degrade`, `gate_reason`, `label`, `network`
and `offline_callable`, in table order; a failure names the scan key), the headless
tables, `ROOTS`, the default model's signals and criteria, and `SIGNAL_INPUTS`; and it
validates the loaded manifest against the schema. `SIGNAL_INPUTS` and each ScanSpec's
`reads`, `stage`, `degrade`, `gate_reason`, `label`, `private` and `network` are generated
from the manifest (`check_manifest.signal_inputs`, `scan_registry.spec_from_manifest`,
#953; `tests/test_manifest_tables.py`): a signal's evidence or a scan's inputs change in
the manifest only. Adding a scan still adds its table line (key, callable, spread keys,
offline callable), and a criterion or typed root its manifest entry. A manifest edit
keeps each callable's positional contract: a scan's `reads` are its arguments in order.
`level` zips its arguments with `level_tally.LEVEL_READS`, so `scan_registry.pinned_reads`
fails the import when `checks/core/level.toml` lists other reads or another order; change
`LEVEL_READS` with it. A
counts-only rule is added in the manifest only; a finding is added in the manifest plus
its path derivation in `keyhole_signals._finding_path_inputs`
(`test_every_finding_has_a_path_derivation` pins the two name sets). The generated tables are
pinned through their builders (an edited copy of the manifest reorders the findings or
reclassifies a field), the structure reason codes against
`import_graph.STRUCTURE_REASON_CODES`, and the thresholds as all read.
A finding `inputs` path under a typed root must name fields of its shape
(`test_finding_inputs_under_a_typed_root_name_its_fields`, #793); one under an untyped
block or a collector is checked on its first segment only. Map keys under a typed root
are not checked. `derive` is checked as a callable only: it stays documentary until a
later step generates the findings from it.
Loader tests: `tests/test_check_manifest_loader.py`.
