# skills/assess/scripts/lib

Deterministic library modules for the `/assess` engine. No LLM calls anywhere in this
package - every function is a pure transform of filesystem, git, or pre-computed signal
data. The LLM reads `run-context.json` after the core finishes; it does not call into
these modules.

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

Seeing these two files in the same commit as `assess_core.py` is expected, not a
defect. If the core is later decomposed, treat this seam as the natural boundary -
`doc_graph` and `keyhole_signals` are where the cut-line already lives.

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
  `CLAUDE.md`'s "Standalone skill pipeline" section; it is cohesion between a packager and
  the thing it packages, not a leak.

None of these is a refactor task. They are recorded here so the seam is owned: if any is
ever cut, this note is where the intended boundary is written down.

---

## Module reference

### Data collection

**`git_churn.py`**
Shared git-churn machinery: per-file commit counts over a configurable window, plus
`git_commit_info` for snapshotting the exact SHA and timestamp at run time. Used by the
code heatmap, the doc-staleness heatmap, and `doc_staleness.py` - churn is computed
one way, not three. Pure subprocess + stdlib, no heavy dependencies.

**`change_coupling.py`**
Three signals derived from `git log`:
- B1 change-coupling pairs: file pairs that co-change across commits (hidden edges
  the import graph cannot see).
- B2 containment ratio: fraction of commits to a module that touch only that module
  (high = an island safe for keyhole edits).
- B4 authorship: human/agent/mixed/unknown classification, e-mail-based and
  deliberately conservative (never labels a human's work "agent" on weak evidence).

All results are JSON-serialisable so `assess_core` can drop them straight into
`run-context.json`.

### Static analysis

**`structure_graph.py`**
Python import-graph analysis (Signals A1-A4) via `grimp` + `networkx`:
- A1 comprehension footprint: direct-dependency surface area for a unit.
- A2 blob vs modular: strongly-connected components and Newman modularity Q.
- A3 contracts: front-door vs burrow (internals-reaching) inbound edges.
- A4 breakup candidates: packages whose sub-modules form separable clusters.

Degrades to `available: False` when `grimp`/`networkx` are absent rather than
blocking the run.

### Document analysis

**`doc_graph.py`** *(co-change hotspot)*
Doc link-graph for Layer 0 navigability. Parses `[[wikilinks]]` and
`[text](relative/path)` links, resolves them to real files, and builds a directed
graph. Derives PageRank centrality, orphan rate, connectivity, MOC validation, and
doc->code association edges. The doc->code edges are reused by `doc_staleness.py` and
`understanding_analysis.py`, making this module a shared dependency for the document
analysis layer. Folds in vault-native navigation edges from `vault_queries.py` (`.base`
hubs become entry nodes; `dataview` query blocks emit edges from their declaring note),
so a vault navigated by dynamic queries doesn't score as orphaned. `doc-graph-svg.py`
renders this exact graph, so the two artifacts always agree. Obsidian-vault detection
(`_vault_detected`) walks the repo subtree, pruning `EXCLUDE_DIRS`, to find a `.obsidian/`
directory anywhere under `repo_root`, not just at the root - a vault kept as a subdirectory
(`repo/notes/.obsidian/`) sits below the `git rev-parse --show-toplevel` scan target and was
previously reported as no vault, silently disabling downstream vault accommodations (#179).
Pruning `EXCLUDE_DIRS` keeps a vendored or build-artifact `.obsidian/` from tripping a false
positive.

**`vault_queries.py`**
Static parser for Obsidian dynamic-navigation hubs: `.base` view files and
`dataview` query blocks. Resolves the folder / tag / frontmatter-field
predicates it can evaluate from the committed files alone (no running Obsidian) into the
set of notes a hub surfaces. Pure - no filesystem walk, no `doc_graph` import - so
`doc_graph.py` owns discovery/excludes and consumes this module's parse + selection. Add
a fixture-backed test here alongside any predicate change.

**`doc_staleness.py`**
Doc-staleness metric for Layer 0. Associates each doc with the code it describes via
nearest-ancestor base-doc rules, computes code churn relative to doc maintenance, and
emits a signed ratio (high = decaying map). The association logic reuses `doc_graph`'s
code-link edges. For *generated* docs it reads `doc_provenance` (per-doc and via the
`[[generated]]` config map) and replaces the churn ratio with a source-vs-doc verdict.

**`doc_provenance.py`**
Provenance-aware staleness for generated docs (issue #178). Parses a doc's YAML
frontmatter `source:` / `generated_by:` (no YAML dependency - a minimal stdlib parser),
resolves the `[[generated]]` folder->source config mapping, and computes `source_newer`
(is any declared source's last change - git commit time, else mtime - more recent than
the doc?). `doc_staleness.py` carries that verdict and `doc_complexity_join.py` signs
freshness from it, so an accurate generated doc is never a `lying_map`. Co-changes with
`doc_staleness.py` (its consumer) and its test `tests/test_doc_provenance.py`.

**`doc_complexity_join.py`**
Signal C: crosses the complexity treemap against doc staleness to produce a signed
`doc_value` per unit. Positive = the doc relieves load on the agent's context window;
negative = the doc is a lying map over complex code (worse than no doc). The join
multiplies complexity by a signed freshness score so trivial code generates near-zero
signal regardless of doc state. When a doc carries a `doc_provenance` verdict, freshness
comes from the source-vs-doc comparison (a direct, high-confidence signal that bypasses
the churn-ratio confidence guards) instead of the ratio.

### Ownership and structure drift

**`ownership_parser.py`**
Parse half for Layer 0 structure-drift. Ownership is *declared* in two places an LLM
contributor reads as authoritative boundaries: a GitHub `CODEOWNERS` (glob -> owner,
honoured at the root, `.github/`, or `docs/` by GitHub's precedence) and a
boundary-declaring `ARCHITECTURE.md` / `DESIGN.md` - or a seam-mapping `README.md`
admitted only when its prose carries the ownership/seam vocabulary, so a generic project
README is skipped. `parse_codeowners` resolves each glob against the git-tracked,
non-excluded file set into `{glob_pattern: {matched_files}}`; `parse_architecture_md`
sections each doc by markdown header and attributes the path references in a section's
body (read from inline code, wikilinks, and bare prose paths) to the module the header
names, keyed `<doc>::<header>` so two docs declaring the same section don't collide, and
keeping only references that resolve to real files. `find_empty_globs` flags the CODEOWNERS
patterns that match zero tracked files - the cheapest drift, a boundary the filesystem has
left behind - and `parse_ownership` runs both with the module-shape degradation contract
(`available: False`, reason `"no ownership map"` when neither map exists). It mirrors
`doc_graph.py` deliberately: the same `EXCLUDE_DIRS` / `is_excluded_path` resolution and
`tracked_files` filtering, the same honest-degrade-over-crash shape, every collection
sorted at the boundary for byte-identical output. The one inversion is that it *reads*
inline-code path spans (a path written as code is the boundary declaration we want) where
the doc graph strips them. This is the parsing foundation the structure-drift signals
consume; it owns no drift logic itself.

**`structure_drift.py`**
The Layer 0 structure-drift signal, built entirely on `ownership_parser`'s parse + resolve
primitives (it re-implements no parsing). A declaration is a self-description under no
pressure to stay true: a directory is renamed, a module's files scatter, a pattern is
typo'd - and the map becomes a *lying map of ownership*, the same defect as a stale doc
(behaviour) or an aged TODO (intent). **Tier 0** - `detect_path_existence_drift()` - is the
zero-threshold cut: the enumerate-both-sides shape `doc_graph.py` uses for broken links,
where side A is every declared boundary (CODEOWNERS globs + architecture-doc path
references) and side B the tracked, non-excluded file set, and the finding is the
declarations whose match set is empty. Binary, no statistics; a pattern matching only
excluded files counts as empty (the excludes are not part of the navigable repo). It emits
a JSON-serialisable `structure_drift` run-context block (`empty_ownership_patterns`, a
coverage ratio, and legacy-shape `empty_globs` mirrors for the orchestrator's
enumerate-both-sides view), degrades to `available: False` reason `"no ownership map"`, and
sorts every list by `(pattern, declared_in)`. **Tier 1** - `detect_grouping_disagreement()` -
is the next cut up: not "does the boundary still match any file?" but "do the files a
boundary groups still belong together?". Three lenses each induce a grouping - the declared
one (an owner / architecture module groups its files), the static one (an import-graph
community groups co-dependent modules), and the historical one (files that keep co-changing).
A grouping is reported as its **co-membership equivalence relation** over canonical file-pairs
`(min(a, b), max(a, b))`, so disagreement is set algebra over pairs and **invariant to
community relabeling by construction** - relabel or reorder the groups and nothing changes,
because the relation carries pairs, never labels. The six metrics are set differences /
intersections of the three relations (human-vs-static splits/fuses, human-vs-cochange, and
the two agreement sets). Known-good architectural seams (the lib<->tests and standalone-build
<->skills seams documented above) are an allowlist subtracted from the *denominator* - correct
by construction, a seam can only suppress an owned boundary, never manufacture drift. Tier 1
degrades to `available: False` reason `"no ownership map"` and emits its own `tier_1_available`
field. `keyhole_signals.py` orchestrates Tier 1 (feeding it the behaviour block's already-parsed
co-change pairs) and folds the hidden-seam direction (`human_split_but_cochange`) into the
existing `hidden_coupling` finding - a recurring directory pair, not the version hot-file's
repo-wide couplings; `assess_core.py` serialises the Tier 0 + Tier 1 result into the
`structure_drift` run-context block. Co-changes with `ownership_parser.py` (its parse foundation),
`structure_graph.py` and `change_coupling.py` (the static and historical lenses it consumes),
`keyhole_signals.py` / `assess_core.py` (its orchestrator and serialiser), and its test
`tests/test_structure_drift.py`.

### Signal integration

**`keyhole_signals.py`** *(co-change hotspot)*
Integration barrier between the individual signal modules and `assess_core`. Derives
the per-directory containment view from the commit-file sets the orchestrator parses
once and passes in, then assembles all five run-context blocks from the upstream signal
outputs. Emits the eight named derived findings as a fixed-order structured array:
`hidden_coupling`, `lying_map`, `unexplained_complexity`, `untrusted_hotspot`,
`self_referential_tests`, `orphaned_understanding`, `candidate_dead_weight`, and
`refactor_boundary` (the two trust-axis findings, `untrusted_hotspot` and
`self_referential_tests`, were added after this module's first cut). Each block build
is wrapped in a catch-all so one signal's failure degrades that block to
`available: False` rather than crashing the run. It also runs `structure_drift.py`'s Tier 1
grouping disagreement (fed the behaviour block's co-change pairs so no second git-log parse
happens) and folds its hidden-seam direction into the `hidden_coupling` finding, returning the
Tier 1 result for `assess_core` to serialise into the `structure_drift` run-context block - so
structure-drift findings flow through this barrier rather than being assembled in the orchestrator.

**`coupling_analysis.py`**
B3 static-vs-historical disagreement cross: compares the import-graph view
(`structure_graph`) against the commit-history view (`change_coupling`) to surface
hidden coupling (looks modular, bleeds historically), bleeding modules (no static graph
available), and refactor boundaries (high containment + low external coupling, a safe
zone for keyhole edits). Looks-coupled-but-never-co-changes is suppressed - the static
graph already surfaces it.

**`understanding_analysis.py`**
Signals B4 + D2. Per module: human anchor (has a confirmed human authored it?), intent
source (is there an externalised spec/doc?), authorship class, and the velocity clock
(days since the last comprehension event - a human-authored commit - rather than
calendar age). Primary finding: orphaned understanding - high complexity with no human
anchor and no externalised spec. Reuses `change_coupling.authorship_analysis` so the
conservative agent/human classification is defined one way.

### Configuration

**`assess_config.py`**
Reads the optional per-repo `.assess/config.toml`: `exclude_dirs` / `exclude_patterns`
(the same two lists feed every scan - heatmap, doc graph, staleness, liveness - so
exclusion is consistent), the `[gate]` and `[structure]` sections, and the `[[generated]]`
folder->source provenance map (issue #178) consumed by `doc_provenance.py`. `resolve_excludes`
is the single shared path that combines config excludes with CLI `--exclude`; both the treemap
CLI and `doc-graph-svg.py` call it, so every artifact computes over the identical doc/code set.
Degrades silently on missing or malformed config rather than blocking the run.

### Output and formatting

**`wiki_writer.py`**
Renders and writes the `.assess/` wiki files (`index.md`, `log.md`,
`hotspots/*.md`) from string templates. No LLM calls. Pure string formatting + file IO.

**`treemap_render.py`**
Shared treemap layout and SVG primitives for the code heatmap and the doc-staleness
heatmap. Pulls `matplotlib`, `squarify`, and `numpy`. Must not be imported by the
deterministic core (which runs with `networkx` alone) - only by the treemap scripts.

**`ci_workflow.py`**
Renders the frozen-harness GitHub Action from `templates/assess-gate.yml.template`
using `string.Template`. Bakes in the toolchain discovered during the current run so
the workflow is a reproducible contract, not a norm. The emitted workflow pins its
supply chain (actions to commit SHAs, tools to exact releases) and degrades infra
failures - toolkit fetch, tool installs, uv setup - to a skip notice so the gate's
warn-only contract survives a flaky network or a missing tag.

**`stats_diff.py`**
Compares current complexity stats against a prior run and classifies hotspot
transitions: graduated (was in top list, now absent), regressed (worsened), new, and
persistent. Pure set operations + arithmetic, no LLM.

### Scoring

**`agent_instructions_grader.py`**
Heuristic scoring of agent instruction files (CLAUDE.md, AGENTS.md, GEMINI.md,
.cursorrules, .github/copilot-instructions.md) on signals that correlate with LLM
usefulness: positive directives, tradeoff phrases, path references, verifiable
outcomes, and freshness. Pure regex + arithmetic, filename-agnostic.

**`liveness_scan.py`**
Layer 1 liveness inputs, three tiers:
- Dead-code tier: runs a language-appropriate static dead-code tool (vulture, ts-prune,
  staticcheck, etc.) to flag candidate-dead exports within the repo boundary.
- Observability tier: scores three rungs - instrumented (telemetry emitted), discoverable
  (runbook present), reachable (agent has an invokable path to runtime state). The
  reachability rung decides the Layer 1 score.
- Capability-offer tier: delegates to `jvm_capabilities.py` to detect JVM/Maven build
  systems and report, per analysis capability, whether a serving tool is already
  configured, could be run/installed in-session, or honest-degrades with a named
  candidate. Surfaced so a non-enumerated ecosystem proposes a tool rather than
  silently reading "absent".

**`jvm_capabilities.py`**
JVM/Maven capability-driven analysis offers (issue #113, v1 bounded). Generalises
`/assess`'s tool mapping from a hardcoded per-language allowlist (vulture for Python,
ts-prune for TS, staticcheck for Go) to a *capability-driven detect-or-propose* model,
proven on one capability (liveness) in one build system (Maven). Reports each capability
in one of four states - `served`, `offer` (with a run-or-install `consent` shape),
`credited` (a configured pom.xml plugin already serves it), or `honest_degrade` (nothing
serves it yet; the report names the capability and a candidate tool). Imported by
`liveness_scan.py`, never by the orchestrator - it is an inward dependency of the
liveness tier.

**`promissory_markers.py`**
Write-side erosion instrument: detects the four families of promissory markers
(TODO/FIXME, deprecations, lint suppressions, disabled tests) via one rg pass per
family, then ages each marker by *survived touches* - the number of commits to its
file since the marker's introducing commit (batched `git blame` + one git-log
pass). A marker that survived many edits to an actively-maintained file is
unactioned intent; calendar age alone can't tell that from dormancy. Classifies
markers as tracked (issue/ticket/URL/date reference, or a justified suppression)
vs bare, and each introducing commit as agent/human (reusing `change_coupling`'s
conservative B4 identity rules). Honours the shared excludes and the generated-file
filter (codegen `ignore_for_file` boilerplate is not debt), and degrades aging to
`aging_reliable: False` on degenerate history (same verdict as `git_churn`).
Feeds the `unactioned_intent` derived finding, the hotspot pages' marker-debt
sentence, and the Layer 3/5/8 erosion rules. New ecosystem marker syntaxes need a
fixture in `tests/test_promissory_markers.py` - absence is a silent miss.

**`accretion_ratchet.py`**
Write-side accretion instrument: detects files that only ever grow. Walks each
file's full numstat history in author-time order (one `git log --no-merges
--no-renames --numstat` pass, sorted explicitly by `%at` then SHA so the
accumulation order is clone-independent) and flags a file when its running
net-delta is non-decreasing *and* its deletion fraction (deletions over total
churn) stays below a threshold - growth with almost no deletion pressure, the
fingerprint of pure append-only accretion rather than ordinary maintenance. A
multi-commit gate drops single-touch rename artifacts; binary files (numstat
`-`) are skipped. Compensates the *Accretion* contributor tendency named in the
repo north star. Degrades to `available: False` on git failure and
`reliable: False` on degenerate history (same verdict as `git_churn`). Reuses
`git_churn`'s `GIT_TIMEOUT_SECONDS` and `churn_is_degenerate`; imports no
orchestrator. Add a fixture-backed test in `tests/test_accretion_ratchet.py`
alongside any change to the flagging rule.

**`badge.py`**
Shields.io endpoint badge for the wiki (`.assess/badge.json`). Two producers on an
honest-degrade ladder: `assess_finalize` always writes the LLM-scored form
("7.0/8 · AI-Native", colour banded from the score), `assess_core` writes the
deterministic findings-count fallback only when no badge exists - so a gate-only
repo gets a truthful badge and a scored badge is never downgraded by a
deterministic-only rerun. Pure threshold functions, fixture-tested.

**`anomaly_detector.py`**
Inspects a run-context dict for suspicious results (e.g. zero files scored, implausible
CCN) and returns typed `Anomaly` records. Detail strings are sanitised (counts and
grades only, no paths or code) so they are safe to include in self-feedback issues.

**`coverage_report.py`**
Parses an *existing* coverage report into the shape the `test_pressure` scan's
`coverage_data=` param consumes (`{_overall, per_file: {relpath: line_rate}}`).
Two formats: Cobertura `coverage.xml` (`_overall` from the root `line-rate`,
per-file from each `<class>` element's `filename`/`line-rate`; one `iter("class")`
walk handles both the flat and nested `<packages>` schemas) and `lcov.info`
(per-file `LH/LF`, overall `sum(LH)/sum(LF)`). `/assess` never runs the suite, so a
report the project already generated is the only honest line-coverage source - the
parser reads it without taking a coverage.py runtime dependency. `detect_coverage_report`
searches the repo root, `./coverage/`, and `./.coverage/` (a `.coverage` SQLite *file*
is out of scope - reading it needs the coverage.py lib). Honest-degrade is the hard
contract: a missing or malformed report returns `None`, never raises, never blocks the
assessment; `assess_core.py` records provenance ("none found" vs. the file/format read)
separately. Stdlib only, imports no orchestrator. Add fixtures + cases in
`tests/test_coverage_report.py` alongside any change to a parse rule.

**`test_focus.py`**
Cross-joins three already-collected signals - the hotspot risk band (position in
`complexity_stats.top_hotspots`), the parsed coverage report, and the
`test_pressure` cheap heuristics - into one ranked focus list. `compute_test_focus`
classifies each top-10 hot file (`no_covering_test` / `covered_but_hollow` /
`covered_clean` / `unknown_no_coverage`), filters out `covered_clean`, and ranks by
risk band then signal severity. Honest-degrade is the contract: `coverage_data is
None` makes every file `unknown_no_coverage` (never `covered_clean`) and records
`coverage_present: False`; it never raises. Pure data - inputs are passed in
(`hot_files`, `coverage_data`, `cheap_heuristics`), no file I/O, stdlib only,
imports no orchestrator. This block is the SINGLE source both the report focus
table and the mutation offer consume - it is the contract, not duplicated
downstream. Add cases in `tests/test_test_focus.py` alongside any change to a
classification or ranking rule.

**`test_pressure/`**
Layer 1 write-side truth pressure. Two tiers:
- Mutation tier: runs a mutation-testing tool (mutmut for Python) over a sample of the
  codebase to measure whether the test suite actually catches changes.
- Cheap heuristics: test/source ratio, assertion density, and the coverage gap signal -
  fast proxies that run without a mutation tool.
