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
analysis layer. Obsidian-vault detection (`_vault_detected`) walks the repo subtree,
pruning `EXCLUDE_DIRS`, to find a `.obsidian/` directory anywhere under `repo_root`,
not just at the root - a vault kept as a subdirectory (`repo/notes/.obsidian/`) sits
below the `git rev-parse --show-toplevel` scan target and was previously reported as no
vault, silently disabling downstream vault accommodations (#179). Pruning `EXCLUDE_DIRS`
keeps a vendored or build-artifact `.obsidian/` from tripping a false positive.

**`doc_staleness.py`**
Doc-staleness metric for Layer 0. Associates each doc with the code it describes via
nearest-ancestor base-doc rules, computes code churn relative to doc maintenance, and
emits a signed ratio (high = decaying map). The association logic reuses `doc_graph`'s
code-link edges.

**`doc_complexity_join.py`**
Signal C: crosses the complexity treemap against doc staleness to produce a signed
`doc_value` per unit. Positive = the doc relieves load on the agent's context window;
negative = the doc is a lying map over complex code (worse than no doc). The join
multiplies complexity by a signed freshness score so trivial code generates near-zero
signal regardless of doc state.

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
`available: False` rather than crashing the run.

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
Reads the optional per-repo `.assess/config.toml` for `exclude_dirs` and
`exclude_patterns`. The same two lists feed every scan (heatmap, doc graph, staleness,
liveness) - consistency is the point. Degrades silently on missing or malformed config
rather than blocking the run.

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

**`anomaly_detector.py`**
Inspects a run-context dict for suspicious results (e.g. zero files scored, implausible
CCN) and returns typed `Anomaly` records. Detail strings are sanitised (counts and
grades only, no paths or code) so they are safe to include in self-feedback issues.

**`test_pressure/`**
Layer 1 write-side truth pressure. Two tiers:
- Mutation tier: runs a mutation-testing tool (mutmut for Python) over a sample of the
  codebase to measure whether the test suite actually catches changes.
- Cheap heuristics: test/source ratio, assertion density, and the coverage gap signal -
  fast proxies that run without a mutation tool.
