# skills/assess/scripts/lib

Deterministic library modules for the `/assess` engine. No LLM calls anywhere in this
package - every function is a pure transform of filesystem, git, or pre-computed signal
data, with three bounded exceptions. Live platform reads are confined to `gh_cli.py`
(GitHub) and `gitlab_cli.py` (GitLab), which run their CLI through `platform_cli.py`;
the headless runner makes them only under `--platform`. They are optional (they need a github.com or
GitLab remote and an authenticated `gh` or `glab`) and degrade to `available: False`
with a reason, never to a clean result. The opt-in mutation pass's uv
runner (`test_pressure/mutmut3.py`) may fetch mutmut, pytest and the assessed package's
dependencies from the package index on first use, into a scratch environment; it runs
only on explicit consent and degrades to a named `reason`. The `/assess --fix` delivery
(`starter_delivery.py`, #1154) is the one network write: only with the opt-in `--pr`, a clean
tree, an authenticated `gh` or `glab` and a run from the default branch's tip, it pushes a branch and opens a pull (merge) request;
any failed precondition leaves the commit on the local branch, with a named `reason`. The LLM reads
`run-context.json` after the core finishes; it does not call into these modules.

## The assess_core.py -> lib seam

`assess_core.py` is the orchestrator. It imports from almost every module here, calls
each one in sequence, and assembles the results into `run-context.json`. When the
orchestrator grows a new signal, reorganizes the context schema, or changes what data
blocks it needs, the lib modules that supply that data move in the same commit. This
is cohesion by design: the lib modules exist to serve the orchestrator's data contract,
so their shape is coupled to it by construction.

Two modules are the identified co-change hotspots in the git history:

- **`doc_graph.py`** - the foundation for Layer 0 navigability. Both `doc_staleness.py`
  and the understanding analysis in `keyhole_signals.py` depend on its doc->code
  association edges. When the core adds a navigability signal or changes how it
  represents doc reachability, `doc_graph.py` is the first file that moves.

- **`keyhole_signals.py`** - the integration barrier between the individual signal
  modules and the orchestrator. It assembles all five run-context blocks
  (`behaviour`, `documentation`, `understanding`, `runtime`, `structure`) and emits
  the six named derived findings. Because it touches every upstream signal, it
  co-changes with the core on almost every schema or signal-set change.

The seam is narrowing. Every run-context block is declared once in `scan_registry.py`
(`SCANS`) and run by one loop, so adding a scan edits its own module, its test, that
table and this file - not `assess_core.py`. The setup steps (scope, artifact directory,
run id, stats and diff, instruction grading, first-flagged map) are entries too; their
adapters live in `run_setup.py`, the read-side adapters in `core_scans.py`. The
doc-staleness keys and the doc x complexity join joined the table last (#558), so the
core assigns no key by hand. The table only reads: `run_publish.py` writes the wiki,
badge and `run-context.json` after the scans (#557), so `build_run_context` has no
file-system side effects and `build_and_publish_run` adds the publish step (#660).

Seeing these two files in the same commit as `assess_core.py` is expected, not a
defect.

The core keeps only the orchestration: `build_run_context` (one `run_scans` call,
writing nothing), `build_and_publish_run` (the same, then the publish call, which
`main` uses), `run_opt_in_mutation` and `main`.
Its helpers live in seven modules, one per seam: `instruction_files.py`
(instruction-file grading), `diff_reliability.py` (cross-run diff trust),
`wiki_state.py` (first-flagged dates and run supersession), `run_wiki.py` (the wiki
pages, index and log for one run), `context_blocks.py` (scan results serialised into
run-context blocks), `mutation_cap.py` (the `test_pressure` shape and the Layer 6 cap)
and `run_scope.py` (`--scope` and the artifact directory), plus the
`artifact_schema.py` and `assess_method.py` constants. A change to one of these concerns edits its module and
its test; `assess_core.py` moves only when the order of the pipeline or the
run-context key order changes.

## The wider co-change seams (cohesion, not entanglement)

`/assess`'s own hotspot scan flags several directories as historically co-changing:
`skills/assess/scripts`, `skills/assess/scripts/lib`, `skills/assess/tests`,
`skills/assess/tests/fixtures`, and `scripts` (+ `scripts/tests`). That is the static
import map saying "separate directories" while git says "they move together." Here the
coupling is genuine subsystem cohesion, and naming it is the point - so a reader (or an
agent) trusts the boundary deliberately rather than being surprised by the seam:

- **`scripts/lib` modules <-> `skills/assess/tests`.** Each deterministic module is
  pinned by a test in `skills/assess/tests/`, and the contract in `CLAUDE.md` is explicit
  ("Add a test alongside any change to a deterministic module"). So a module and its test
  are *meant* to move in the same commit - the test is the module's behavioural contract,
  not a separable concern. A reviewer seeing `doc_graph.py` and `test_doc_graph.py` in one
  diff is seeing the intended unit of change.
- **`scripts/` (standalone build) <-> `skills/`.** The standalone-skill build under
  `scripts/` vendors and transforms the very skills it packages, so a change to a skill's
  shape and the build that ships it co-change by construction. That seam is documented in
  `docs/contributing/standalone-skills.md`; it is cohesion between a packager and
  the thing it packages, not a leak.
- **The `actions/checkout` pin across `.github/workflows/*`,
  `skills/assess/templates/assess-gate.yml.template` and `README.md`.** The gate the
  skill generates, the repo's own workflows and the README's copy-paste snippet pin the
  same release by commit SHA. Dependabot bumps only the workflow pins, so the template
  and README follow by hand in the same PR. `skills/assess/tests/test_ci_workflow.py`
  fails the build when the template's pin drifts from `.github/workflows/assess-gate.yml`
  or the README.
- **`fleet/fleet_method.py` <-> `checks/core.toml` and `levels.py`.** The fleet stamp's
  `rules_sha256` hashes the loaded `MaturityModel` with a denylist of naming keys
  (`UNHASHED_KEYS`), so a new model key is hashed by default; `collectors` lists each core
  collector's `revision`. A collector whose output in the fleet golden changes must bump its
  `revision`: `tests/test_collector_fingerprints.py` compares the golden against
  `tests/fixtures/fleet_golden/collector_fingerprints.json`, and `make regen` refuses to record
  a changed output under an unchanged revision (#952).
- **`fleet/fleet_rescore.py` <-> `fleet/fleet_data.py`.** `rescore` rebuilds a stored run's cells and
  roll-ups with `fleet_data`'s own `repo_cells`, `row_roll_up` and `step_fields` (with
  `fleet_data_steps.step_roll_up`), so a
  change to how `build_fleet_data` computes a cell or a unit roll-up moves both.
  `tests/test_fleet_rescore.py::test_rescore_recomputes_the_unit_roll_ups`
  fails when a re-scored run differs from a fresh `build_fleet_data` over the same rows.
- **`account_discovery.py` <-> `agent_identity.py`.** The `dependency_bot_hint` evidence
  token reads `agent_identity.is_dependency_bot`, the same test `AuthorMatcher` buckets a
  `dependency_bot` with, so widening `DEPENDENCY_BOT_RULES` moves both the `ai_usage`
  counts and the identities awaiting confirmation. `account_discovery` imports only
  public names from `agent_identity` (#1031).

None of these is a refactor task. They are recorded here so the seam is owned: if any is
ever cut, this note is where the intended boundary is written down.

---

## Check manifest whys (#939)

Every check manifest entry says why it matters to an agent. The loader is
`check_manifest.load_manifest` (`manifest/check_manifest.py`); the why rules and accessors are in `manifest/manifest_whys.py`.

| Entry | Where its why lives | Accessor |
|---|---|---|
| condition | `why` (and optional `why_source`) on the condition entry in its fragment, `checks/core/<scan key>.toml` | `manifest_whys.why_of`, `condition_whys` |
| finding | `why` (and optional `why_source`) on the finding entry in its fragment | `manifest_whys.why_of` |
| scan | the `whys` table of the collector that publishes it, keyed by scan name (the keys of its `headless` table); sources in `why_sources` | `manifest_whys.scan_why`, `scan_whys` |
| truth tag | `[tags.<id>] why` in the root manifest, `checks/core.toml` (not a fragment) | `manifest_whys.tag_why` |
| signal | none: a signal entry with `why` is rejected, and the catalog shows the why of each condition it feeds | - |

**Version rule.** `manifest_version = 1` loads with no whys (every accessor returns `""`), and a
version 1 manifest carrying any why, source or `tags` table is rejected. `manifest_version = 2`
(the core) requires every why above, whatever the manifest's `owner`; a missing one is a named
error such as `core/ci_checks.toml entries[4] (core.cond.layer-5-ci.1.enforced_check_runs).why:
required`. A why is 1 to 300 characters and passes `lib.vocabulary` (no banned word, no layer
number). A scan's why may be exactly `exempt: plumbing` only when no row or finding reads the
scan, directly or through another collector's `reads` (a collector's read of its own block does
not count); the catalog renders it "Run plumbing". A custom manifest that moves to version 2
declares the eight truth-tag whys too, under its own root `tags` table; one that stays at
version 1 needs none.

**Where whys are written.** `measures.json` carries `why` and `why_source` on criteria, scans,
findings and tags, and `docs/what-we-measure.md` renders them (`catalog_md`). In the run context,
each `level.conditions[]` entry carries `why`: its criteria's whys in criterion order, each once,
joined by one space (`level_tally.condition_why`), absent for a criterion with no manifest entry;
`/fleet` writes the same in `level.json`. `headline.next_why` holds, for each blocking gate, the
`why` of every condition of the gate's row up to its level that is not met, joined the same way
(`""` with no gate blocker). Each `gap_actions` entry carries the `why` of the scan its `signal`
names. All are static manifest text; counts-only output drops them, since a reader rejoins
them from `measures.json` by id.

---

## Modules by subpackage

Issue #917 moves `lib/` modules into subpackages, one group per pull request. A package
`__init__.py` holds only its docstring, so importing a package imports none of its modules;
`tests/test_self_architecture.py` enforces it. Three exceptions: `fleet/__init__.py` and
`counts_only/__init__.py` also hold a lazy forward until 5.0.0 (to `fleet.runner` and
`counts_only.content_free`), and `test_pressure/__init__.py`, which predates the rule, is a
facade that re-exports its package's names. The shared core (`run_context_types`,
`assess_config`, `artifact_schema`, `assess_method`, `tool_lookup`, `vocabulary`, `agent_identity` and
`fleet_config`) stays at the root of `lib/`, and so does every module whose group has not
moved yet. A moved module keeps its filename.

Each old path `lib/<stem>.py` is a shim until 5.0.0: it sets `sys.modules[__name__]` to the
moved module, so the old and new dotted paths name one module object, and a module that runs
as a script gets a `runpy` `__main__` branch. Run as `python scripts/lib/<stem>.py`, Python
puts `lib/` first on `sys.path`, not `scripts/`, so such a shim first inserts `scripts/`
(`Path(__file__).resolve().parents[1]`) and the old path keeps running. Running a shim as
`__main__` imports the moved module before `runpy` runs it, so Python prints a
`RuntimeWarning`; the moved file (`python scripts/lib/<pkg>/<stem>.py`) runs without it.
New code imports the new path.

- **`agents/`**: what a repository builds with agents and how an agent runs in it, the
  Layer 8 inputs. `agent_identity` is shared core, and `agent_consistency`, `agent_systems`
  and `agent_instructions_grader` wait for the `instructions` group, so all four stay at the
  root. Files: `__init__.py`, `agent_building.py`, `agent_building_vocab.py`,
  `agent_environment.py`, `agent_harness.py`, `agent_manifests.py`, `agent_ops.py`.
- **`complexity/`**: the complexity treemap renderer, `scc` coverage, the Dart scanner and
  the stats sidecar (its rows, cross-run diff and diff reliability), plus the generated- and
  raw-source exclusions they apply. Files: `__init__.py`, `dart_complexity.py`,
  `diff_reliability.py`, `generated_files.py`, `raw_source.py`, `scc_coverage.py`,
  `stats_diff.py`, `stats_rows.py`, `treemap_render.py`.
- **`counts_only/`**: the content-free copy of a run-context and its allow-list: the engine,
  the field rules and their schema, the reason codes it keeps and the typed block shapes a
  default criterion reads. `lib/counts_only.py` became `counts_only/content_free.py`, because
  the package now holds that name; until 5.0.0 the package `__init__.py` forwards each old
  name (`lib.counts_only.render_doc`) to `lib.counts_only.content_free` on first use, without
  importing it eagerly. Files: `__init__.py`, `block_shapes.py`, `content_free.py`,
  `counts_only_rules.py`, `counts_only_schema.py`, `reason_codes.py`.
- **`docs/`**: the doc graph and the Layer 0 doc signals: links, claims, ownership,
  provenance, staleness and its generator directives, the doc-to-complexity join and the
  vault queries. `doc_graph` imports `ownership_parser` inside a function, because
  `ownership_parser` imports `doc_graph`. The folder holds two kinds of file: the doc-scan
  modules (`.py`) and every group's long module references (`.md`, one per module, #981).
  Files: `__init__.py`, `doc_claims.py`, `doc_complexity_join.py`, `doc_directives.py`,
  `doc_graph.py`, `doc_graph_layout.py`, `doc_links.py`, `doc_ownership.py`,
  `doc_provenance.py`, `doc_staleness.py`, `ownership_parser.py`, `vault_queries.py`.
- **`fleet/`**: the `/fleet` runner, roll-ups, run history, published fleet schemas and
  diagrams. `lib/fleet.py` became `fleet/runner.py`, because the package now holds that
  name; until 5.0.0 the package `__init__.py` forwards each runner name (`lib.fleet.FleetRow`)
  to `lib.fleet.runner` on first use, without importing it eagerly. `fleet_config` and
  `fleet_activity` keep their prefix but are not fleet modules: `fleet_config` is shared core,
  and `fleet_activity` waits for the `history` group. Files: `__init__.py`, `fleet_achievements.py`, `fleet_achievements_runs.py`, `fleet_achievements_schema.py`, `fleet_agent_building.py`, `fleet_cleanup.py`, `fleet_cli_args.py`, `fleet_compare.py`, `fleet_coverage.py`, `fleet_credit.py`, `fleet_data.py`, `fleet_data_schema.py`, `fleet_data_steps.py`, `fleet_data_tags.py`, `fleet_diagrams.py`, `fleet_doc_freshness.py`, `fleet_front.py`, `fleet_group_schema.py`, `fleet_groups.py`, `fleet_hashed.py`, `fleet_heatmap.py`, `fleet_history.py`, `fleet_labels.py`, `fleet_legacy.py`, `fleet_level.py`, `fleet_level_schema.py`, `fleet_maturity.py`, `fleet_method.py`, `fleet_next_steps.py`, `fleet_opportunities.py`, `fleet_opportunities_section.py`, `fleet_platform.py`, `fleet_publish.py`, `fleet_repo_facts.py`, `fleet_rescore.py`, `fleet_scanner_diff.py`, `fleet_schema.py`, `fleet_slope.py`, `fleet_stamp.py`, `fleet_step_targets.py`, `fleet_svg.py`, `fleet_targets.py`, `fleet_tooling_map.py`, `fleet_trends.py`, `fleet_units.py`, `fleet_view.py`, `runner.py`.
- **`graph/`**: language import graphs and the static dependency structure built from
  them. The builder registry, `import_graph_builders`, stays at the root: `import_graph`
  imports it inside a function, because each builder imports `import_graph`.
  Files: `__init__.py`, `import_graph.py`, `import_graph_go.py`, `import_graph_python.py`,
  `import_graph_typescript.py`, `structure_graph.py`.
- **`hosting/`**: the `gh` and `glab` reads and the platform blocks built from them: the
  shared merged sample, delivery, review reality, GitLab notes and CI lint, and config drift.
  Named `hosting`, not `platform`, because a `lib/platform/` package would shadow the standard
  library's `platform` module for any script run by path from `lib/` (decision 9 on #917).
  `external_ci`, `external_ci_rules`, `gitlab_rules` and `unread_ci` (#884, #928) postdate the
  #917 module map and stay at the root. Files: `__init__.py`, `config_drift.py`, `delivery.py`,
  `gh_cli.py`, `gitlab_ci_lint.py`, `gitlab_cli.py`, `gitlab_notes.py`, `merged_sample.py`,
  `platform_cli.py`, `review_reality.py`.
- **`keyhole/`**: the keyhole-readiness signals and findings, and the Layer 1 liveness
  inputs: accretion, promissory markers, structure drift, hidden coupling, understanding,
  archetype and the Dart and JVM capability entries. `promissory_markers` and
  `accretion_ratchet` also run as scripts: run the moved file,
  `python scripts/lib/keyhole/<stem>.py`. Files: `__init__.py`,
  `accretion_ratchet.py`, `archetype.py`, `coupling_analysis.py`, `dart_capabilities.py`,
  `jvm_capabilities.py`, `keyhole_signals.py`, `liveness_scan.py`, `promissory_markers.py`,
  `python_regions.py`, `structure_drift.py`, `understanding_analysis.py`.
- **`manifest/`**: the check manifest loader (`checks/core.toml` and its fragments), its
  structured validation errors, and the `why` rules the loader applies to every entry
  (#939). Files: `__init__.py`, `check_manifest.py`, `check_validation.py`, `manifest_whys.py`.
- **`test_pressure/`** (predates #917): write-side truth pressure, with a facade
  `__init__.py` that re-exports the package's names. Files: `__init__.py`, `aggregate.py`, `common.py`, `heuristics.py`, `mutation.py`, `mutmut3.py`, `reason_paths.py`.
- **`testing/`**: the Layer 6 test signals outside `test_pressure/`: the coverage gate and
  the readings it combines (coverage activation, CI coverage runs, the root coverage.py
  config), the coverage report, test focus, sibling tests, import credit, the mutation cap
  and refresh, and untrusted hotspots. Files: `__init__.py`, `ci_workdir.py`, `coverage_activation.py`,
  `coverage_ci.py`, `coverage_gate.py`, `coverage_report.py`, `coverage_root_config.py`,
  `import_credit.py`, `mutation_cap.py`, `mutation_refresh.py`, `sibling_tests.py`,
  `test_focus.py`, `untrusted_hotspot.py`.

## Module reference

There is no per-module index here: each module documents itself in its own module docstring,
a one-line summary followed by the details (`tests/test_self_architecture.py` enforces it).
A module too long to hold its whole reference within its file-size limit keeps a summary
and points at its own `docs/<module>.md`.

Windows portability (#712, #752): every text-mode `subprocess.run` names
`encoding="utf-8"` (with `errors="replace"`, or `surrogateescape` on every `git ls-files -z` listing, whose paths must
round-trip), and a repo-relative path is rendered with `.as_posix()`, never `str()`.
`tests/test_windows_portability.py` guards all three across every runtime Python tree: each
`skills/*/scripts/` and the repo-level `scripts/`, leaving out `tests/`, `fixtures/` and dot directories.
The fleet run files (`repos.jsonl`, `groups.jsonl`, `run.json`, `compare.json`, `labels.jsonl`,
`doc_freshness.jsonl`, `agent_clusters.json`, `achievements.json`, `condition_effect.json`, `demotions.json`, `fleet-data.json`, the
generated fleet schemas and the next-steps pages) are written with `newline="\n"`, so Windows and
Linux produce the same bytes; the same test checks that every writer in `lib/fleet/`, the `lib/fleet_*.py` modules and
`skills/fleet/scripts/` names `newline`. `promissory_markers` asks `rg` for `--path-separator /`
so its paths are POSIX on Windows too, and `change_coupling` keys and sorts co-change pairs
as POSIX strings, so pair order is the same on every OS (#713). That is the canonical order
`structure_drift` already used, and it changed Linux pair order too: `file_a`/`file_b` now
follow POSIX string order, so `src/foo.py` sorts before `src/foo/bar.py` (`.` < `/`), where
the earlier `Path` ordering compared path parts and put `src/foo/bar.py` first. The windows-latest job
(`.github/workflows/windows.yml`) checks both through `tests/test_fleet_golden.py`.
