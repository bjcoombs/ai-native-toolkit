---
name: assess-findings
description: Renders the /assess report from the deterministic run-context.json and the layer scorecard - the scorecard, the verbatim cross-layer findings, lying signals, and the mandatory Top 3 Actions. TRIGGER when the /assess orchestrator reaches the report-writing step; not a standalone user command.
---

# Assess Findings Writer

The report-writing half of `/assess`. The deterministic core has written `.assess/run-context.json` (the data bus) and the `assess-layer-scorer` agent has returned the 0-8 scorecard. Your job is to assemble `.assess/assess-report.md`: the scorecard, the snapshots, the **verbatim** cross-layer findings, the lying signals, and the Top 3 Actions.

The deterministic parts are not yours to invent - you paste them. You write the prose *around* a findings section you cannot omit or reorder. This is the deterministic-core-writes-data / LLM-writes-prose split that makes the report reproducible regardless of which model drives the run.

## Inputs

- `$REPO_ROOT/.assess/run-context.json` - the data bus (findings, attention, keyhole summary, prescribed actions, stats, diff).
- The scorecard returned by the `assess-layer-scorer` agent (the 0-8 score, per-layer verdicts, maturity label).

## Read the cross-layer findings first


The layers above each measure one axis. The deterministic core also crosses those axes against each other and emits eight named findings - the "where to look" signals no single layer surfaces. Read them once, after the per-layer scans:

```bash
jq '.derived_findings, .attention, .keyhole_summary, .prescribed_actions' "$REPO_ROOT/.assess/run-context.json"
```

`derived_findings` is a fixed-order list of eight `{name, paths, action}` objects - all eight always present, `paths` may be empty. Omit a finding from the report when its `paths` is empty. Each pairs an axis-crossing with the action it implies:

- **`hidden_coupling`** - modular statically but bleeds across boundaries historically (files that keep changing together). The static map says "isolated"; git says "coupled." Action: investigate the seam before trusting the boundary.
- **`lying_map`** - high complexity under a stale doc: the map exists but no longer matches the territory. Action: fix or delete the doc - a wrong map is worse than none.
- **`unexplained_complexity`** - high complexity with no doc and no recorded intent. Action: write the missing contract. Do **not** auto-generate it - a guessed contract is just another lying map.
- **`untrusted_hotspot`** (E1 trust axis) - a complexity hotspot whose tests are hollow: an opt-in mutation pass let a high fraction of mutants survive, so the suite *runs* the code but doesn't *pin* it. Silent without mutation data (the default read-only run never fires it). Action: strengthen tests to pin observable behaviour, not internal state.
- **`self_referential_tests`** (E2 trust axis) - the code and its co-located tests were introduced in the same commit, so the suite may verify the author's mental model rather than independently-specified behaviour. Action: request human review - the tests verify internal consistency, not truth.
- **`orphaned_understanding`** - high complexity with no human anchor and no intent: nobody owns the knowledge. Action: assign a human anchor before further change.
- **`candidate_dead_weight`** - high complexity with no runtime evidence it is live. The bias is to **keep** (static reachability can't see external callers - Layer 1's caveat applies). Action: verify liveness, then delete only if confirmed dead.
- **`refactor_boundary`** (positive) - high containment: edits stay local. A safe zone, never an attention row. Action: safe to hand an agent in isolation; cite these paths in Strengths.

`attention` ranks the few units landing in the most *negative* findings (`refactor_boundary` never counts) - the "look here first" list, each row carrying its `findings` and `score`. Lead the report's findings with the top of this list.

**Copy `findings_markdown` verbatim.** `run-context.json` carries a pre-rendered `findings_markdown` string - the deterministic findings section (the eight findings with their paths and actions, then the attention list). Paste it into the report **verbatim, in the `## Cross-Layer Findings (Keyhole Readiness)` section below the Scorecard** - do not paraphrase, summarise, reorder, or drop findings. You write prose *around* it (the "why these matter here" framing), but the section itself is the deterministic core's product, not yours. This is what makes the findings impossible to omit regardless of which LLM drives the run.

`keyhole_summary` rolls the same findings into a one-line readiness summary (`summary_text`), reported alongside the 0-8 score - see the **Keyhole Readiness** line in the report template below. `prescribed_actions` lists the attention-derived Top-3 actions the report MUST include - see the **Mandatory attention rule** in the Top 3 Actions section below.


## Score and Write the Report

Calculate the score (0-8 based on layers present, +0.5 for partial) and write the report to `$REPO_ROOT/.assess/assess-report.md`.

**Report format** (write this to disk verbatim, filling in the placeholders):

```markdown
# Codebase Assessment: <repo-name>

_Generated <YYYY-MM-DD>._

## How to read this report

**What this is ultimately measuring.** When you hand work to an AI here, does it behave like a brand-new hire or like an engineer who has been in the org eighteen months? The difference isn't capability - it's *externalized context*: knowing where things live, which contracts are load-bearing, where the minefields are. An AI contributor is structurally always the new hire - it starts every session with an empty head, seeing one narrow slice through its context window - so this report scores how much of the tenured engineer's implicit map the codebase has made explicit and navigable. The aim is not an AI that comprehends what humans no longer can and is trusted blindly (that is the dangerous case); it is a codebase legible enough that the relevant slice fits one context window and the agent's answers stay anchored to code you can still verify. **Legibility you can trust, not omniscience you can't.**

This is an improvement roadmap, not a verdict. It measures one thing: **is the codebase kept honest, not just scaffolded.** It pairs three views:

- **Where the codebase is today** — the complexity heatmap shows current complexity and churn. Vivid red = complex AND actively changing = the files most likely to bite an agent (or a human) next week.
- **Whether an agent can navigate it** — the doc graph shows the docs' link structure: how much is reachable from the entry point, and which docs are stale maps of churning code.
- **What keeps it from getting worse** — the AI Readiness score (0–8) across three bands: read-side foundation (can the agent form a true picture?), write-side enforcement (can it be trusted to produce good output?), and meta (does the system keep itself honest over time?).

A codebase can be 8/8 and still on fire (great scaffolding, legacy debt) — or 2/8 with a calm treemap (small codebase, no enforcement needed yet). The views matter together.

**Each layer is a sense the agent needs, not a box to tick — and this report is written to be understood without prior knowledge of the framework.** Every row in the scorecard below names one thing an agent (or a newly-arrived human) must be able to *see or trust* before working safely here; it's phrased as the question that row answers. A **Missing** or **Partial** is not a mark against the codebase — it locates where someone working here is partly blind, and the Gap column says what would restore the view. One row matters most for cold readers, because it's the one most often misread: **Layer 1 (Runtime Legibility) is the agent's attention-sense — "which parts are actually live, which need attention, which are dead weight creating support burden?"** A running service answers that through telemetry (latency, errors, traffic); a repo with no deployed runtime answers it through complexity, churn, and reachability — so the complexity heatmap and the dead-code scan *are* its observability. A low telemetry score on such a repo means *look through those instruments instead*, *not* "the agent is flying blind." Observability, in this report, is whatever lets the agent see where to point effort — by whichever instrument fits the codebase.

**The legacy-transition lens — this is not a new problem.** The hard part of AI-assisted coding is the same one the industry has faced for 25 years: *what happens when the people who understood the code are gone, or the code outruns anyone's ability to understand it?* AI only changes the **velocity** — a codebase can now slide from modern to legacy in months rather than years, because agents can generate change faster than a team can keep comprehending it. So read this report through the accumulated discipline of legacy-code engineering, not as a novel AI concern. The grounded references below are deliberate: a reading agent already knows them, and they carry weight that "we think you should add tests" does not.

- **"Legacy code is code without tests."** — Michael Feathers, *Working Effectively with Legacy Code* (2004). A file that is **complex, churning, and untested is already legacy**, regardless of its age. The correct response is to write **characterization tests** (pin the code's *actual current behaviour*, since that is what callers depend on — not its intended spec) and find a **seam** (a place to change behaviour without editing the code) to get it under test, *then* refactor.
- **Don't rewrite — strangle.** The instinct under AI velocity is to throw a confusing module away and regenerate it. That is the oldest trap in the book (Joel Spolsky, *Things You Should Never Do*, 2000): a rewrite discards the undocumented behaviour and bug-fixes a working system has accreted. Prefer Martin Fowler's **Strangler Fig** — replace incrementally around the edges, behind tests. An agent's default ("regenerate from intent") is precisely the wrong move; say so where a hotspot invites it.
- **Hotspots and change-coupling are the map.** The complexity-×-churn heatmap and the coupling findings in this report *are* Adam Tornhill's (CodeScene) hotspot and change-coupling analyses — the modern, version-control-aware form of legacy comprehension. Treat the vivid-red hotspots and the `hidden_coupling` seams as the prioritized worklist, not trivia.
- **Decide per component (the 7 Rs).** Not every flagged unit should be refactored: retain, retire, rehost, replatform, refactor, re-architect, or rebuild. Dead weight → *retire*; complex-and-live → *characterize then refactor*; safe `refactor_boundary` zones → hand to an agent in isolation.

When you write the findings and Top 3 Actions below, name these anchors where they apply — they tell a reading agent *how to think* about the situation, not just what the metric says.

**How it's measured.** This is an AI-readiness review run almost entirely on *traditional* tooling — static analysis, git history, and graph metrics over the docs and code. The model only writes the prose around those numbers; it does no scanning itself. That keeps a full run fast and close to zero in model tokens, and makes the structural findings reproducible run-to-run.

The "Top 3 Actions" table at the bottom names specific files. Start there.

## Snapshots

### Complexity — riskiest to change

[![Complexity hotspot](./complexity-heatmap.svg)](./complexity-heatmap.svg)

- **Measured at commit:** `<head_short>` (<committed_date>)<staleness-suffix>
- **Files scored:** <N>
- **Churn window chosen:** <last 12mo | last 24mo | last 5y | all-time>
- **Complexity profile:** per-function ccn p95 <N> (max <M>); file-aggregate ccn p95 <N> (max <M>); p95 LOC <N> (max <M>)
- **Top hotspots** (composite `sqrt(ccn) × sqrt(1 + commits)` - a sub-linear blend of complexity and recent churn, so a complex-AND-active file leads, a frozen-but-complex file ranks below it, and a churny-but-trivial file can't top the list on churn alone). `ccn` here is the **file aggregate**; the worst single function per file is in parentheses:
  1. `<path>` — <loc> LOC, aggregate ccn <N> (worst function <max_fn_ccn>), <M> commits in window
  2. ...
  3. ...

**Pin the snapshot to its commit (issue #59).** Read `measured_commit` from `run-context.json` and fill the "Measured at commit" line so every absolute LOC/CCN figure in this report reads as a snapshot of one commit, not a current truth:

```bash
jq '.measured_commit' "$REPO_ROOT/.assess/run-context.json"
```

- When `available: false`, omit the "Measured at commit" line (no git history to pin to).
- Render `head_short` and `committed_date`. Add a `<staleness-suffix>` warning when the snapshot is stale, so a reader knows the numbers may have drifted:
  - `dirty: true` → append " - **working tree had uncommitted changes; figures include un-committed edits**".
  - `behind` is a positive integer → append " - **HEAD was <behind> commit(s) behind `<upstream>`; absolute figures are a snapshot and may read low against current code**".
  - Clean and up to date (`dirty: false`, `behind` 0 or null) → no suffix.

Size encodes lines of code, colour encodes cyclomatic complexity (dark red = high), saturation encodes recent git churn (vivid = active). Vivid red blocks are the migration risk. When the treemap carries a **hatched** overlay (only when opt-in mutation results exist), those blocks are covered-but-unpinned code - tests run them without constraining them - so they stop reading as safe green; the heatmap's own legend keys the diagonal (>30% survivor density) and cross-hatch (>50%, severe).

### Doc navigability — can an agent find its way?

[![Doc map](./doc-graph.svg)](./doc-graph.svg)

Read the structured signal from `run-context.json` (`.doc_graph`, `.doc_staleness`, `.stale_hubs`) and write this section in **plain language** — explain the metrics, don't just dump numbers. Define each term the first time you use it:

- **Navigability, in words.** e.g. _"Of <N> docs, <P%> are reachable by following links from the README; the other <N> are orphans (nothing links to them) or sit in <K> disconnected islands."_ Pull `island_count`, `orphan_rate`, `reachability_pct`, and name 2–3 specific orphans/islands. **Frame it as curation, not access** — do not write "an agent can only discover <P%>": an agent can still `ls docs/` and open any file by path, so low link-reachability means the docs lack a navigable map (weak wayfinding / signal-vs-noise), not that content is hidden. State that adding an index/MOC is the fix, and present it as a wayfinding improvement, not a blocker — for a directory-organised tree, temper the priority accordingly.
- **Attribution.** Describe these checks in plain terms (orphans, broken links, connectivity, hubs). The README credits Andrej Karpathy's LLM-wiki "Lint" pattern as the influence once — **don't repeat that attribution in the generated report.**
- **Lying maps (stale docs of churning code).** Define the terms inline: **stale** = days since the doc itself last changed; **subject churn** = commits in the window to the code the doc describes; **centrality** = how many other docs point to it (a hub). A real lying map is **old AND beside genuinely churning code**. From `stale_hubs` / `doc_staleness.docs`, name the worst 2–3 — but **apply judgment, don't trust the raw composite**: when a doc's `subject_method` is `repo-baseline`, its "subject churn" is just the whole repo's churn (a coarse proxy), so a *recently-changed* doc (low stale-days) that merely happens to be a big hub is **not** a lying map — don't flag it. Prefer docs with a precise association (`nearest-ancestor` / `parallel-docs-tree` / `explicit-links`) and high stale-days.

Colour = staleness (vivid red = a frozen doc beside churning code = a lying map); structure = reachability (centre = entry, rim = unreachable, dashed ring = orphan); size = file length. Hover a node in the SVG (opened on its own) for its path and stats.

## AI Readiness

**Score: X / 8** — <maturity-label>
**Keyhole Readiness:** <paste `keyhole_summary.summary_text` from run-context.json verbatim>

The two headline metrics measure **different things and are never combined.** The 0-8 score answers _"is the scaffolding in place to catch problems?"_ (a property of the contracts and enforcement layers). The Keyhole Readiness summary answers _"where is today's structural pain?"_ - a pure count of structural concerns and safe zones rolled up from the eight cross-layer findings. A repo can score 7/8 and still carry many structural concerns (great scaffolding over churning legacy), or 3/8 with zero concerns (small, calm codebase). Report both side by side; never average, rescale, or fold one into the other.

The **What it asks** column is the question that layer answers for an agent working here — read it first; the framework name is secondary. The **Band** orders them by dependency: a read-side blind spot makes the write-side scores mean less (you can't trust enforcement of a picture you can't see), and the meta band only matters once the rest works.

| Layer | What it asks | Band | Status | Evidence | Gap |
|-------|--------------|------|--------|----------|-----|
| 0: Agent Instructions & Navigability | Can I build a true map of this codebase before I touch it? | read | Present/Partial/Missing | <what was found> | <what's missing> |
| 1: Runtime Legibility / Liveness | Can I see which parts are live, which need attention, and which are dead weight? | read | Present/Partial/Missing | <what was found - if rung 3, append: "Reachable *if* the agent has `<tools cited in runbooks>` in its execution environment"; if no deployed runtime, note that liveness is read through complexity + churn + reachability, not telemetry> | <what's missing> |
| 2: Code Design | Will the type-checker catch my mistakes? | write | Present/Partial/Missing | <what was found> | <what's missing> |
| 3: Linters | Are complexity and style bounds enforced, or will my code drift? | write | Present/Partial/Missing | <what was found> | <what's missing> |
| 4: Architecture Tests | Are the structural conventions executable, or just folklore? | write | Present/Partial/Missing | <what was found> | <what's missing> |
| 5: CI Pipeline | Does something automatically catch a bad change before it merges? | write | Present/Partial/Missing | <what was found> | <what's missing> |
| 6: Coverage Gates | Do the tests constrain behaviour, or just execute lines? | write | Present/Partial/Missing | <what was found> | <what's missing> |
| 7: Code Review Bots | Is there design-level feedback on every change? | write | Present/Partial/Missing | <what was found> | <what's missing> |
| 8: AI Project Mgmt (capstone) | Do learnings feed back into the contracts, or evaporate? | meta | Present/Partial/Missing | <what was found> | <what's missing> |

**Layer 1 evidence carries an explicit caveat by default.** The Layer 1 spec is honest about its boundary: this scores what the *repo* makes agent-reachable, not what the agent has installed at runtime. So when Layer 1 is **Present** (rung 3), the Evidence cell should not read "Reachable, full stop" - it should name the cited tools and conditionalise on their availability. Example: _"Runbook fences `kubectl logs`, `stern`, `logcli`; reachable *if* the agent has these in its execution context."_ When Partial or Missing, the caveat is moot - no rung-3 claim is being made.

### Score derivation (worked)

The layered model has 9 layers (0-8); the display caps at "X / 8" so the headline matches the maturity band table. Compute the raw sum, then apply two rules:

1. **Raw sum**: Present = 1, Partial = 0.5, Missing = 0. With 9 layers, the maximum raw sum is 9.0.
2. **Cap at 8**: the displayed score is `min(raw_sum, 8.0)`. A 9.0 (every layer Present) caps at 8.0 = ceiling. This keeps the band table (0-2 / 3-4 / 5-6 / 7-8) consistent with the headline.

Worked example: 7 layers Present + 2 layers Partial = 7×1 + 2×0.5 = 8.0 raw. Two partials below ceiling shouldn't display as "all perfect", so by the cap rule the **displayed score is 7.0 / 8** - you subtract the half-points the partials cost relative to a Present (each Partial costs 0.5 vs ceiling, two of them cost 1.0, so 8 ceiling - 1.0 = 7.0). Equivalent shortcut: `min(raw_sum, 8 - 0.5 × num_partials_when_raw_sum_would_exceed_8)`. The general case is just `min(raw_sum, 8)`.

Other examples:
- 8 Present + 1 Missing → raw 8.0 → display **8.0 / 8** (AI-Native).
- 6 Present + 2 Partial + 1 Missing → raw 7.0 → display **7.0 / 8** (AI-Native).
- 4 Present + 3 Partial + 2 Missing → raw 5.5 → display **5.5 / 8** (Solid).
- 0 Present + 0 Partial → raw 0.0 → display **0.0 / 8** (Not Ready).

### Maturity Level

| Score | Level | Description |
|-------|-------|-------------|
| 0-2 | Not Ready | Agent will produce inconsistent, unvalidated code |
| 3-4 | Basic | Norms exist but aren't enforced. Agent works but drifts |
| 5-6 | Solid | Contracts catch most issues. Agent is productive |
| 7-8 | AI-Native | System self-improves. Agents work reliably at scale |

## Lying Signals

These artefacts look true but aren't - the most dangerous failure mode for an agent navigating the codebase. Each row is something the repo presents as trustworthy that a scan flagged as hollow: a map of a place that moved, code that reads as live but is never called, a gate that reports "tested" without pinning behaviour.

| Layer | Signal Type | Instance | Why it lies |
|-------|-------------|----------|-------------|
| 0 | Stale hub doc | `<path>` (<N>d stale; subject churned <M> commits in window) | A central doc agents anchor on, frozen while its subject code moves - reads as the map, describes terrain that no longer exists |
| 1 | Dead-but-present | `<path>` - `<symbol>` (<kind>) | Compiles and reads as live, but nothing in *this* repo calls it - an agent extends or trusts a path that is never exercised |
| 6 | Green-but-hollow | `<path>` (coverage <C>% vs mutation <K>%) | Tests execute the file (green coverage) but don't constrain it (mutants survive) - the gate says "tested" while behaviour is unpinned |

**Populate each row from `run-context.json`; omit any row whose signal is absent or below threshold, and omit the entire section if all three are empty:**

- **L0 — stale hub doc:** take `stale_hubs[0]` only when its `ratio > 2.0` **and** `confidence != "low"` (a `repo-baseline` subject is `confidence: low` - its "subject churn" is the whole repo's churn, too coarse to call a lie). Fill from `path`, `last_commit_days`, `code_churn_in_window`.
- **L1 — dead-but-present:** take `dead_code.candidates[0]`; fill from `path`, `symbol`, `kind`. Keep the `dead_code.caveat` in mind - static reachability proves "nothing in this repo calls it," never "no external consumer calls it" - so frame it as a candidate, not a verdict.
- **L6 — green-but-hollow:** take `test_pressure.survivor_clusters[0]` only when `test_pressure.survivor_density.overall > 0.3`; fill the file from the cluster's `file`. State the mutation score as `1 - survivor_density.overall` and pair it with the file's line coverage when you have it. This is the hollow-gate pattern Layer 6 scores Partial for.

<paste the `findings_markdown` string from run-context.json verbatim here — it renders as a `## Cross-Layer Findings (Keyhole Readiness)` section with the eight findings, their paths, their deterministic actions, and the attention list. Do not paraphrase, summarise, reorder, or drop any finding. Add a sentence of framing before or after if it helps a reader, but the section itself is copied, not rewritten.>

## Top 3 Actions

Prioritize by leverage: agent instructions and CI first, then linters and coverage, then architecture tests and retro loops. Each action should be completable in a single session and reference **specific files** from the hotspot snapshot wherever possible — generic advice is the failure mode this report exists to prevent.

**Mandatory attention rule (hard, not a suggestion).** If `attention` in run-context.json is non-empty, its top entries MUST appear in this Top 3 Actions table. The `prescribed_actions` array lists them with their finding-derived action text, their `rank`, and their `path` (pre-rendered as table rows you can paste - see `prescribed_actions` and `render_prescribed_actions`). You MAY add context, combine an attention path with a related gap, or fill the deterministic `?` cells (layer, effort, command) with judgement - but you may NOT omit an attention-list path from the Top 3 unless that path has already been addressed in this repo. When `attention` is empty, prioritise by leverage as above. This rule exists because the attention list is the deterministic core's "look here first" ranking; letting the LLM silently drop it would reintroduce exactly the non-determinism Part 1 removes.

| # | Action | Layer | Effort | Command / First Step | Hotspot files this addresses | Issue |
|---|--------|-------|--------|---------------------|------------------------------|-------|
| 1 | <one-line action - if `prescribed_actions` is non-empty, rank 1's action and path go here> | <layer number> | <small/medium/large> | `<exact command or file to edit>` | <paths from top_hotspots / top_complex / top_large, or "—" if not file-specific> | — |
| 2 | <one-line action - rank 2 from `prescribed_actions` if present> | <layer number> | <small/medium/large> | `<exact command or file to edit>` | <paths or —> | — |
| 3 | <one-line action - rank 3 from `prescribed_actions` if present> | <layer number> | <small/medium/large> | `<exact command or file to edit>` | <paths or —> | — |

The `Issue` column is filled in later by the `assess-pr` step if the user opts to create tracking issues. Leave as `—` initially - that step mutates this table in place via the `assess-report.md` artifact, so the column is the explicit contract between the two units.

**Ground actions in the legacy-code precedent where it applies.** For a complex, churning, *untested* hotspot, the right first action is almost never "rewrite it" and rarely a bare "add tests" — it is **"add characterization tests for `<file>` to pin current behaviour, then refactor behind them"** (Feathers). For a tangled `hidden_coupling` seam, frame it as a **strangler-fig boundary** to replace incrementally, not a big-bang rewrite. For `candidate_dead_weight`, the action is **retire** (confirm liveness, then delete) — one of the 7 Rs. Naming the precedent (characterization test, seam, strangler fig, the 7 Rs) gives the action authority a reading agent recognizes and steers it away from the throw-it-away-and-regenerate instinct that AI velocity makes tempting and usually wrong.

**Frame actions positively.** "Add `cyclop` rule (threshold 15) to `.golangci.yml`" beats "Stop letting complex code through CI." Positive directives are easier for the next contributor (human or LLM) to act on - they say what to do, not what to avoid. If you find yourself writing "Don't X" or "Never Y", convert to "Use X (because Z)" instead.

**Use repo-relative paths only.** Never write absolute paths from your environment (e.g. `/Users/.../repo/src/foo.go`) into the report. They leak the author's directory layout, break shell commands for other contributors, and look unprofessional in committed artifacts. Repo-relative paths (`src/foo.go`, `.golangci.yml`) work everywhere.

**Currency-check any package you name (issue #62).** Before naming a specific package *version* in an action (`dart_code_metrics: ^5.7.6`, `some-linter@2.1.0`), prefer naming the **rule/concept plus a pointer to the language's package registry** (pub.dev, npm, crates.io, PyPI, RubyGems) over a hardcoded version - registries stay current; your training data doesn't. If you do name a specific package, verify it's still actively maintained for the repo's language/runtime version rather than trusting recall. This protects every language symmetrically: any canonical tool can be deprecated after the model's training cutoff (the `dart_code_metrics` case), so "name the rule + registry" is the safe default.

Good actions look like:

> _"Add `cyclop` rule (threshold 15) to `.golangci.yml`. Current per-function p95 is 23; immediate offenders by worst function: `internal/import/parser.go` (function ccn 67), `internal/sync/reconciler.go` (function ccn 54)."_

Cite the **per-function** value (`fn_ccn` / `max_fn_ccn`) against a per-function rule, not the file aggregate - a `cyclop:15` rule flags functions, so a file whose worst function is 12 is not an offender no matter how high its aggregate sums (issue #58).

Generic actions to avoid:

> ~~_"Improve code quality"_~~ — name the files and the threshold.
> ~~_"Add a linter"_~~ — name the linter, the rule, and the first three files it will flag.

**Read-side remediation must match the actual gap.** The diagnosis from Layers 0–1 changes the action:

- **Layer 1 Partial because observability is instrumented but not agent-reachable** (rung 1–2 — the `meridian` case): the action is _"make existing observability agent-queryable"_ — e.g. "add a `.mcp.json` log/metrics server" or "add a `view-logs` repo skill wrapping `logcli`" — **not** "add observability" (it's already there; the gap is reachability).
- **Layer 0 Partial because a hub doc is stale:** name the specific stale hub from `stale_hubs` and its churning subject — _"refresh `docs/architecture.md` (251d stale; subject `src/api/` had 47 commits in window)"_ — not "improve the docs".
- **Layer 0 Partial because the doc set is fragmented:** name the orphans / islands — _"link the 6 orphan docs into the MOC; `index.md` is named but not wired (out-degree 0)"_.
- **Layer 6 Partial because coverage is enforced but truth-pressure is unverified:** the actions are _"strengthen assertions to pin observable behaviour at the named survivors"_ and _"add mutation testing to CI (e.g. `mutmut`, Stryker, PITest)"_ — **not** "raise the coverage threshold" (higher line-coverage numbers manufacture more lying signal without improving behavioural constraint).

**Cross-check offenders against the recommendation's own scope (issue #59a).** When an action names a *scope* ("un-exclude `services/*/service/*.go`", "enforce the rule under `src/core/`"), every offender you list in that action's evidence must actually fall within that scope. Before writing the offender list, verify each path against the named pattern - a `service_modules.go` under `shared/pkg/saga/schema/` is **not** under `services/*/service/`, so it can't be cited as evidence for un-excluding `services/*/service/`. If the worst offenders sit outside the scope you're recommending, either widen the recommended scope to cover them or move them to a separate action with the right scope. The offender list and the recommended change must describe the same set of files.

**Apply the same truth-pressure to `/assess`'s own structural findings (issue #59c).** `/assess` grades other docs for being honest; its own topology/count claims are held to the same bar. Before stating any count-based finding - "the README lists 15 services but there are 19", "N modules lack a base doc" - **count the actual entries**, don't estimate or trust a single signal. For a service/component-count claim, enumerate both sides (what the doc lists vs what exists on disk) and report the real delta and the specific missing names, not a round-number guess. A wrong count ("missing 2" when it's actually missing 4, including specific named services) erodes trust exactly as a stale doc does. If you can't enumerate precisely, say "approximately" and name what you couldn't verify rather than asserting a false-precise number.

**Sub-threshold "approaching the cap" findings are not refactor tasks (issue #60).** Files in the *Watch* band but under the *High* threshold (e.g. 600-800 LOC against an 800 cap, or per-function ccn 10-14 against a 15 rule) are **not violations**. Do not default them into refactor tasks - pre-emptively decomposing files that currently work fine is churn-for-churn (review cost, merge conflicts, behaviour risk on working code). Instead:

- Keep actual violations (over the High threshold) as the standard remediation tasks.
- Surface "approaching the cap" files as an **optional, low-priority** item in *Additional Opportunities*, never the Top 3 - and frame it **annotate-first**: the recommendation is to add an acknowledgement marker (e.g. a `// large-file: tracked` comment or a lint allow-entry) so the file is consciously owned, leaving the decompose-or-not decision to the maintainer. Only escalate to a refactor task when the file is over the cap or the maintainer asks.
- Label the band explicitly in the report so a reader sees "under the cap, watch" - never present a sub-threshold file as if it failed a gate.

### Why these three?
<2-3 sentences explaining why these are highest leverage. Connect to specific gaps from the table above and to hotspot files where relevant. Be concrete about what each action prevents.>

## Additional Opportunities

<If more than 3 gaps exist, list remaining as brief bullets. Keep to one line each. These are "after you've done the top 3" items.>

## Strengths

<3-5 bullet points. What this repo already does well. Be specific — name files, tools, and patterns. Acknowledge existing infrastructure.>

**If you are an agent working in this repo, read the `.assess/` directory — it is actionable feedback written for you, not just a report you skim once.** It is a compounding, AI-readable record that grows with every run:

- `.assess/assess-report.md` — this report: the scorecard, the lying signals, and the Top 3 Actions with exact commands and file paths.
- `.assess/hotspots/<file>.md` — a per-file briefing for each hotspot, including a **Suggested actions** section. Before you change a file that appears here, read its briefing first: it tells you why the file is risky and what to do about it.
- `.assess/index.md` — the catalog of every hotspot ever flagged (current and graduated), so you can see what has and hasn't been addressed.
- `.assess/log.md` — run history, so you can see whether a hotspot is regressing, persistent, or improving over time.

Treat it as the first place to look when deciding where to point effort in this codebase — it is the durable form of "what needs attention here."

---

<!-- chat-replace:report-footer -->
_Report generated by [`/ai-native-toolkit:assess`](https://github.com/bjcoombs/ai-native-toolkit). Install in any Claude Code session: `/plugin marketplace add https://github.com/bjcoombs/ai-native-toolkit` then `/plugin install ai-native-toolkit@ai-native-toolkit`._
```

Complete the full assessment in under 2 minutes. Scan, don't deep-read.

Both SVGs are embedded as **clickable, relative links** - `[![alt](./complexity-heatmap.svg)](./complexity-heatmap.svg)` and the same for `./doc-graph.svg`. The `viewBox` lets GitHub scale them to the content column; the link lets a reader open the SVG on its own (the doc graph's hover tooltips only work when the raw SVG is opened directly - GitHub renders the inline copy as a static image). Keep the link relative, never a `raw.githubusercontent.com` URL (those are branch-specific and rot on rename). Omit a section only if its script could not generate the SVG (record the reason instead).

The plugin footer is important - it's how other engineers viewing the report in a PR discover the tool that produced it. Do not omit it.

