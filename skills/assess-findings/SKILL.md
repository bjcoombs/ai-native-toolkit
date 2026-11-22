---
name: assess-findings
description: Renders the /assess report from the deterministic run-context.json and the layer scorecard - the scorecard, the verbatim cross-layer findings, lying signals, and the mandatory Top 3 Actions. TRIGGER when the /assess orchestrator reaches the report-writing step; not a standalone user command.
---

# Assess Findings Writer

The report-writing half of `/assess`. The deterministic core has written `.assess/run-context.json` (the data bus) and the `assess-layer-scorer` agent has returned the per-layer scorecard. Your job is to assemble `.assess/assess-report.md`: the deterministic headline, the scorecard, the snapshots, the **verbatim** cross-layer findings, the lying signals, and the Top 3 Actions.

**Windows:** run the bash blocks below in Git Bash, which ships with Git for Windows. Installs, PATH notes and the make-free commands are in the [Windows setup guide](../../docs/windows.md).

The deterministic parts are not yours to invent - you paste them. You write the prose *around* a findings section you cannot omit or reorder. This is the deterministic-core-writes-data / LLM-writes-prose split that makes the report reproducible regardless of which model drives the run.

## Inputs

- `$REPO_ROOT/.assess/run-context.json` - the data bus (the `headline` and `level` blocks, findings, attention, keyhole summary, prescribed actions, stats, diff).
- The scorecard returned by the `assess-layer-scorer` agent (the per-layer verdicts and their evidence), with its `evidence` list already re-checked by `lib/evidence_check.py`: only entries that held on disk remain there; the rejected ones arrive separately under `evidence_rejected`, each with the `reason` the check gave.

**Existence and wiring claims cite only verified evidence.** A report sentence saying a file or directory exists or is missing, or that something is or is not wired in (a workflow runs the linter, no CI job calls the script), must rest on an entry in the scorecard's `evidence` list and name the path that entry cites. When no verified entry backs such a claim, leave it out rather than restating it from the scorer's prose. The scorecard's per-layer Evidence cell is exempt only in its two empty states (no entries offered, every entry rejected), set beside the scorecard table below; a cell whose layer kept some verified entries cites only those, as the partial state there says. The deterministic findings you paste verbatim are already grounded and need no entry.

## Read the cross-layer findings first


The layers above each measure one axis. The deterministic core also crosses those axes against each other and emits ten named findings - the "where to look" signals no single layer surfaces. Read them once, after the per-layer scans:

```bash
jq '.derived_findings, .attention, .attention_low_signal, .keyhole_summary, .prescribed_actions, .gap_actions, .stats_summary.complexity_unreliable' "$REPO_ROOT/.assess/run-context.json"
```

`derived_findings` is a fixed-order list of ten `{name, paths, action}` objects - all ten always present, `paths` may be empty. Omit a finding from the report when its `paths` is empty. Each pairs an axis-crossing with the action it implies:

- **`hidden_coupling`** - modular statically but bleeds across boundaries historically (files that keep changing together). The static map says "isolated"; git says "coupled." Action: investigate the seam before trusting the boundary.
- **`lying_map`** - high complexity under a stale doc: the map exists but no longer matches the territory. Staleness is the decaying-map churn ratio for a hand-written doc, counted only over distinct subject commits after the doc's last content change (a doc corrected after its subject last moved is never flagged, however busy the window was), but for a *generated* doc that declares provenance (frontmatter `source:` or a `[[generated]]` config mapping) it is measured against the source - the doc is stale only when its source is newer than it, so an accurate generated doc is never flagged. Action: fix or delete the doc - a wrong map is worse than none.
- **`unexplained_complexity`** - high complexity with no doc and no recorded intent. Action: write the contract the code needs. Do **not** auto-generate it - a guessed contract is just another lying map.
- **`untrusted_hotspot`** (E1 trust axis) - a complexity hotspot whose tests are hollow: an opt-in mutation pass let a high fraction of mutants survive, so the suite *runs* the code but doesn't *pin* it. Silent without mutation data (the default read-only run never fires it). Action: strengthen tests to pin observable behaviour, not internal state.
- **`self_referential_tests`** (E2 trust axis) - the code and its co-located tests were introduced in the same commit, so the suite may verify the author's mental model rather than independently-specified behaviour. Action: request human review - the tests verify internal consistency, not truth.
- **`unactioned_intent`** (write-side tendency) - files carrying stale promissory markers (`TODO` / `FIXME` / "remove after migration") that survived at least `stale_touches_threshold` edits to their own file without being kept: a lying map of intent. A justified suppression (`eslint-disable-line x -- reason`, `# noqa: X  # reason`) is never stale; a marker citing an issue or a date still ages. Silent when the marker scan is unavailable or the history is too thin to age markers. Action: action the promise - fix it, ticket it, or delete the marker/skip.
- **`accretion_ratchet`** (write-side tendency) - a top-band hotspot whose line count only ever ratcheted upward: monotonic net growth across multiple commits with almost no deletion pressure (deletions below ~15% of total churn). The fingerprint of a file appended to rather than reworked. Only surfaced for files already in the top complexity/size band, so a growing-but-simple file is never flagged. Silent when the scan is unavailable, and disclaimed (not dropped) when the git history is degenerate (shallow clone or squashed import). Action: refactor down - extract, delete dead code, or split the file.
- **`orphaned_understanding`** - high complexity with no human anchor and no intent: nobody owns the knowledge. Action: assign a human anchor before further change.
- **`candidate_dead_weight`** - high complexity with no runtime evidence it is live. The bias is to **keep** (static reachability can't see external callers - Layer 1's caveat applies). Action: verify liveness, then delete only if confirmed dead.
- **`refactor_boundary`** (positive) - high containment: edits stay local. A safe zone, never an attention row. Action: safe to hand an agent in isolation; cite these paths in Strengths.

**Partial import graph (`structure.name_collisions`).** Read it with `jq '.structure.name_collisions'`. Each entry is `{name, paths}`: one import name held by two or more discovered packages (`paths` are their repo-relative directories, with `.` written `%2E` and `%` written `%25` so each stays one module name; decode those two escapes before naming a path, so `v1%2E2/lib` reads `v1.2/lib`), which the core graphs in separate builds. Every import between those builds is dropped, not only imports of the colliding name, so the import-graph signals - the per-module import footprints, the front-door ratio, modularity, and the static containment behind `hidden_coupling` and `refactor_boundary` - cover only part of the tree and can be understated. When the list is non-empty, say so in one or two sentences directly under the `## Cross-Layer Findings (Keyhole Readiness)` heading, naming each colliding package by `name` with its `paths`, so a reader does not take the structure figures as whole-repo. Omit the note when the list is empty or `structure.available` is false (the import graph did not run; there is nothing to qualify).

`attention` ranks the few units landing in the most *negative* findings (`refactor_boundary` never counts) - the "look here first" list, each row carrying its `findings` and `score`. Equal scores order by `stats_summary.top_hotspots` rank (members first), then finding severity (stale-marker severity, or lower `containment_ratio` for `hidden_coupling`), then path, so the order is already the priority. Lead the report's findings with the top of this list. The sibling boolean `attention_low_signal` is true when the top `score` is 1; the ranking is then weak, and only its first row is prescribed.

**Copy `findings_markdown` verbatim.** `run-context.json` carries a pre-rendered `findings_markdown` string - the deterministic findings section (the ten findings with their paths and actions, then the attention list). Paste it into the report **verbatim, inside the `🔎 Cross-layer findings` fold below** - do not paraphrase, summarise, reorder, or drop findings. You write framing prose (the "why these matter here") *inside* the block, directly under its `## Cross-Layer Findings (Keyhole Readiness)` heading - the block already opens with that heading, so **never add your own heading above or around it**; the heading must appear exactly once in the report. The section itself is the deterministic core's product, not yours. This is what makes the findings impossible to omit regardless of which LLM drives the run.

`keyhole_summary` rolls the same findings into a one-line readiness summary (`summary_text`), reported beside the deterministic headline - see the **headline** in the report template below. `prescribed_actions` lists the actions the report MUST include, one per top `attention` row (up to three; one when `attention_low_signal` is true), and `gap_actions` lists `{signal, action, paths}` candidates for the slots left free: instruction files that are all nested (`signal` `layer_grades`, advising a root `AGENTS.md` or `CLAUDE.md` that names the check command, naming the nested files), a missing coverage report (`signal` `coverage_report`, naming the top hotspots to measure) and doc reachability below the 0.50 floor (`signal` `doc_graph`, naming the unreachable docs), `[]` when none fires, or `{available: false, reason}` when the gap scan failed (then fill free slots by judgement) - see the **Mandatory attention rule** in the Top 3 Actions section below.


## Write the Report

Write the report to `$REPO_ROOT/.assess/assess-report.md`. **The headline is not yours to compute.** It is the run context's `headline` block (`lib/headline.py`), a pure function of the commit and the maturity-model version: the autonomy Step with its next unlock, then "Verified practices: N of M measured" with the not-measured count and the model version. Paste `headline.markdown` verbatim. Never write a layered score, a fraction such as "X / 8" or a band name; the per-layer verdicts sit underneath as detail, and they never set the Step.

**Two audiences, one file.** The report serves a human (wants a short, picture-led skim) and an AI agent (wants the full structured detail). The structure below satisfies both: the **default-visible surface is short** - the headline, the Top 3 Actions, and the two SVG images, plus one caveat line - and **everything verbose is relocated into collapsible `<details>` folds**. A collapsed `<details>` keeps its content fully present in the raw markdown, so an agent reading the file sees everything inside the folds; the fold only hides it from a human's *rendered* view. **Folding is presentation, not removal - never delete content to shorten, relocate it into a fold.** Two things never fold: the **headline** and the **Top 3 Actions**. A section-ablation A/B showed that removing the Top 3 made a fresh agent confidently act on the *wrong* (lower-priority) item, so they stay visible and complete.

Use the CodeRabbit fold style: `<details>`, then `<summary>emoji label</summary>`, a blank line, the content, a blank line, then `</details>`. The blank lines are required for tables and lists to render inside the fold. Nesting is allowed.

**Report format** (write this to disk verbatim, filling in the placeholders; the bolded paragraphs - inside the folds and under the Top 3 Actions table - are instructions for *how* to fill that section, not literal report text):

```markdown
# Codebase Assessment: <repo-name>

_Generated <YYYY-MM-DD>. Generated by `/assess` v<version>._

<paste `headline.markdown` from run-context.json verbatim>

_A readiness snapshot, not a verdict._ · Keyhole: <paste `keyhole_summary.summary_text` from run-context.json verbatim>
<!-- The headline lines come from the deterministic core: the Step as the contracts in place, the Next line naming the next Step and the one change that reaches it, then "Verified practices: N of M measured" with its not-measured count and model version. Paste them unchanged; never add a score, a fraction or a band name. -->
<!-- The "A readiness snapshot, not a verdict." line is a FIXED string: paste it verbatim under the headline. It is the SHORT not-a-verdict frame that sits with the headline so the reader never mistakes a Step for a pass/fail gate. Do NOT expand it into the full "This is an improvement roadmap, not a verdict" sentence here - that longer framing stays in the "How to read" fold, and duplicating it on the surface regresses the bespoke-opening contract. -->

<Bespoke summary - the report's emotional headline and the first thing a human reads. Write 2-3 sentences specific to **this** repo and **this** run; it must be **unrepeatable** - a sentence that would print identically under a different headline is the boilerplate tell to avoid. Hit recognition before remediation:
> 1. **Lead with the genuine standout strengths**, named from the actual findings (the enforced gates, the complexity ratchet, a navigability jump, the closing feedback loop) - so the maintainer feels *seen* and a skeptic sees the tool read *this* code, not a template.
> 2. **Then** name the single most important next step in one clause, and - when prior-run data exists - a one-beat trajectory note (what moved since last run: the Step, the verified tally, a layer that flipped, reachability).
>
> Keep the non-verdict reassurance alive in the *tone* (a Missing or Partial locates where someone is partly blind, it is not a mark against the code) but do not lead with it or restate it as a standalone caveat - the full framing lives in the "How to read" fold. Celebrate what is working before pointing at what to improve.>

<MUTATION_CAVEAT - render this line VERBATIM and above the fold only when mutation testing did not run; omit it entirely when mutation ran. Read `mutation_not_run_cap.applies` from run-context.json (equivalently `mutation_run` is false; task 8 lands this data - read it, do not recompute). When it applies, paste exactly:>
_Note: Mutation testing was not run. Layer 6 (Coverage) is capped at Partial and truth-pressure remains unproven._

<MUTATION_PARTIAL_CAVEAT - render this line VERBATIM and above the fold only when `mutation_not_run_cap.partial` is true (mutation ran, but every group with records stopped at the time budget); omit it otherwise. It never appears together with MUTATION_CAVEAT. When it applies, paste exactly:>
_Note: Mutation testing stopped at its time budget in every package it ran in. Layer 6 (Coverage) is held at Partial: the figures cover only the fastest mutants, a biased sample._

> **Agents start here.** The prioritized Top 3 actions below are also machine-readable in `.assess/actions.json` (schema v2: every entry carries `rank`, `action`, `done_when`, and `scope_fence`, plus the lifecycle fields `status` / `claimed_by` / `completed_sha` and a derived execution `mode`). Read that file to pick up the work - even with a smaller model - without parsing this report's prose.
<!-- Paste this "Agents start here" pointer VERBATIM (it is a deterministic fixed string keyed to the actions.json v2 schema, not prose to rewrite or embellish). It must sit above the first fold, pointing the reader at the durable machine-readable Top-3 contract before the human-readable table below. -->

## Top 3 Actions

<one orientation sentence: which attention paths Actions 1-2 cover and which scorecard gap Action 3 closes.>

| # | Action | Layer | Effort | Command / First Step | Done when | Scope fence | Hotspot files this addresses | Issue |
|---|--------|-------|--------|---------------------|-----------|-------------|------------------------------|-------|
| 1 | <one-line action - if `prescribed_actions` is non-empty, rank 1's action and path go here> | <layer number> | <small/medium/large> | `<exact command or file to edit>` | <verifiable exit criterion> | <what NOT to touch> | <paths from top_hotspots / top_complex / top_large, or "-" if not file-specific> | - |
| 2 | <one-line action - rank 2 from `prescribed_actions` if present, else the next unused `gap_actions` entry, else judgement> | <layer number> | <small/medium/large> | `<exact command or file to edit>` | <verifiable exit criterion> | <what NOT to touch> | <paths or -> | - |
| 3 | <one-line action - rank 3 from `prescribed_actions` if present, else the next unused `gap_actions` entry, else judgement> | <layer number> | <small/medium/large> | `<exact command or file to edit>` | <verifiable exit criterion> | <what NOT to touch> | <paths or -> | - |

**Done when** is the action's exit criterion and it must be *checkable, not aspirational*. State it in the repository's own terms first, so a maintainer without the plugin can check it: a command whose pass state defines done (`golangci-lint run` reports 0 new issues; `bats tests/` passes), a file state (`AGENTS.md` names the check command), or a CI result (the `lint` job runs `actionlint` and is green). A re-run criterion (`max_fn_ccn` of the named function drops below the threshold in a re-run's stats sidecar; the finding no longer lists the path) may follow, but never as the only one. An executing agent - especially a smaller model - uses this to know when to stop; an action without a verifiable exit invites either premature "done" claims or runaway scope.

**One concern per action.** Each row carries one change a maintainer could accept or decline on its own. When a gap needs several independent changes (an instruction-file stub, a lockfile, a CI step), the row takes the highest-leverage one, and each other change goes directly under the table as a lettered sub-item (`3a. <change> - Done when: <criterion>`), so the issue-tracking step can file each separately. Sub-items are not extra rows: the table and `actions.json` keep one entry per rank.

**Name the condition an action moves.** When an action changes one maturity-model condition, put that condition's id in the action's `condition` field in `finalize-input.json` (an id `level.conditions` lists, e.g. `layer-5-ci.1`); `assess_finalize.py` refuses an id the run's level result does not list. When the headline's Next line names an unmet gate and no prescribed action holds the slot, the change that holds that gate is the strongest candidate for a free slot.

**Scope fence** names what the action must NOT touch: the files, behaviours, or conventions out of bounds ("only `.golangci.yml`; no source edits", "extract the one function; no drive-by reformatting of the rest of the file"). Smaller models over-extend without an explicit fence.

**Keyhole-size the action.** When an action targets code (not config), prefer the *function* over the *file*: the stats sidecar carries per-function data on each ranked row (`max_fn_ccn` and `max_fn_name`, the worst function's name, filled for files a per-function backend scored - lizard, and the approximate `dart-scanner` for Dart; both are null for files scc scored at file level only), so "extract the dispatch branch of `parseLine` (fn ccn 41), characterization test first" fits one context window where "refactor parser.go (ccn 67)" does not. An action a fresh agent cannot complete inside one keyhole is two actions.

**scc-only ccn is not a priority figure (issue #590).** A row with `source` `scc` and null `max_fn_ccn` carries scc's file-level keyword count, not a parsed complexity, and a path listed in `stats_summary.complexity_unreliable` is one whose count is implausibly low for its size (scc lost its place, often on shell quoting). Never justify an action's rank or order with such a figure: argue from churn, size and the finding instead, and when the figure appears at all write it as "ccn ~N (approximate)" - or "ccn ~N (unreliable)" for a `complexity_unreliable` path (the report table's `~N (approximate)` and `~N (unreliable)` cells, with the column's "ccn" in front). The Top 3 table is pasted into PR and issue bodies, so this caveat travels with it.

### Why these three?

<2-3 sentences explaining why these are highest leverage. Connect to specific gaps from the scorecard and to hotspot files where relevant. Be concrete about what each action prevents.>

## Snapshots

### Complexity - riskiest to change

[![Complexity hotspot](./complexity-heatmap.svg)](./complexity-heatmap.svg)

<one-line takeaway: where the risk concentrates and whether the per-function complexity is fenced - e.g. "Every hotspot is in `skills/assess/`: the engine auditing itself; per-function ccn p95 9 sits under the 15 gate.">

### Doc navigability - can an agent find its way?

[![Doc map](./doc-graph.svg)](./doc-graph.svg)

<one-line takeaway: reachability plus whether it is a wayfinding or a truth gap - e.g. "~10% link-reachable: a curation/wayfinding gap, not hidden content; no lying maps.">

### Evidence behind the level

[![Evidence behind the level](./evidence-waffle.svg)](./evidence-waffle.svg)

<the waffle's caption and its "In place:" line, copied verbatim from the `## Evidence behind the level` section that `assess_report.py <repo> --stdout` prints; when that section says "Not drawn:", write its reason here instead of the image>

<details>
<summary>📈 Snapshot detail (commit, hotspots, navigability, lying maps)</summary>

#### Complexity profile

- **Measured at commit:** `<head_short>` (<committed_date>)<staleness-suffix>
- **Files scored:** <N>
- **Churn window chosen:** <last 12mo | last 24mo | last 5y | all-time>
- **Complexity profile:** per-function ccn p95 <N> (max <M>); file-aggregate ccn p95 <N> (max <M>); p95 est. tokens <N> (max <M>; code <C>, data <D>); p95 NLOC <N> (max <M>; code <C>, data <D>). The code and data maxima come from `max_code` / `max_data` in the `est_tokens` and `loc` blocks (data is scc's JSON, YAML and JSONL); omit a split its snapshot lacks
- **Top hotspots** (composite `sqrt(effective_ccn) × sqrt(1 + commits) × sqrt(est_tokens)`, with `effective_ccn` the file aggregate weighted toward its worst function: a sub-linear blend of complexity, recent churn, and context-window size, so a file high on *multiple* axes - big AND complex AND churning - is the worst keyhole and leads; a frozen-but-complex file ranks below an equally-sized active one; a churny-but-trivial file can't top on churn alone; and a big-but-simple-stable file can't top on size alone; a co-change breadth term was tested and left out, see the assess skill's `references/hotspot-cochange-experiment.md`). `est_tokens` is the char-based estimate (~chars/4), `ccn` here is the **file aggregate**; the worst single function per file is in parentheses:
  1. `<path>` - <est_tokens> est. tokens (<loc> NLOC), aggregate ccn <N> (worst function `<max_fn_name>` <max_fn_ccn>), <M> commits in window
     For a row scc scored at file level only (`source` `scc`, `max_fn_ccn` null) write the figure as "ccn ~N (approximate)", and as "ccn ~N (unreliable)" when its path is in `stats_summary.complexity_unreliable`.
  2. ...
  3. ...
- **Size units:** `loc` in the stats sidecar, and every `LOC` label the core generates (the treemap tooltip, the hotspot pages' `LOC` row, the `index.md` column), is NLOC: code lines with blank and comment lines left out (lizard's count; scc's code count for data files). A file-size ratchet or lint cap, `wc -l`, and the hotspot pages' "lines net" accretion figure count physical lines. Write "<N> NLOC" for the first and "<N> lines" for the second, and never set the two against each other as one file's size: the same file can be 394 NLOC and 723 lines.
- **Per-function coverage:** list every language that `fn_ccn.backend_by_language` maps to `null` in one line, once per run: _"no per-function data for <Language>, <Language> (scored at file level only)."_ A row whose `max_fn_ccn` is null then drops the "worst function" parenthesis rather than printing null. Omit the line when no language maps to `null`. For each backend `fn_ccn.source` marks `approximate: true` (`dart-scanner` today), say so once beside the figures it produced, naming the languages `backend_by_language` maps to it: _"Dart per-function figures are approximate (brace-and-keyword scan, `dart-scanner`)."_, and write each such row's figure as "worst function `<max_fn_name>` ~M".

- **Keyhole budget** (`stats_summary.est_tokens.budget`): state the repo total estimated tokens and how many files / top-level subtrees exceed one context-window keyhole (the documented `budget`, an estimate). This is the literal "does the relevant slice fit one keyhole?" measure. e.g. _"Repo is ~<total> est. tokens; <N> subtree(s) exceed the ~200k keyhole budget: `<name>` (~<tokens>)."_ When `est_tokens` is absent (pre-token snapshot), omit this line.

Size encodes estimated tokens (~chars/4 - the keyhole size unit; the familiar LOC is one hover away in the tooltip), colour encodes cyclomatic complexity (dark red = high), saturation encodes recent git churn (vivid = active). Tokens track what fills an agent's context window better than LOC, which undercounts dense/wide files and overcounts sparse code. Vivid red blocks are the migration risk. When the treemap carries a **hatched** overlay (only when opt-in mutation results exist), those blocks are covered-but-unpinned code - tests run them without constraining them - so they stop reading as safe green; the heatmap's own legend keys the diagonal (>30% survivor density) and cross-hatch (>50%, severe).

**Always explain the hatching - even when it is absent.** The hatched overlay only appears when an opt-in mutation pass ran, but its *absence* must never pass silently: a green-but-unverified treemap reading as safe is exactly the lie the overlay exists to prevent. Read `test_pressure.mutation_config_present` (and whether `test_pressure.survivor_clusters` carry data) and write one of these two readings right after the treemap, always:

- **Mutation ran and hatches exist:** explain the overlay - a diagonal hatch marks >30% survivor density, a cross-hatch >50% (severe); both flag covered-but-unpinned code, tests that execute the lines without constraining behaviour, so the action is to strengthen assertions to pin observable behaviour, not internal state.
- **Mutation did NOT run:** write it verbatim - _"No hatching visible - mutation analysis was not run. The hatching would mark covered-but-unpinned code (tests that execute without constraining). Run `/assess` and accept the mutation offer to enable it."_

**Pin the snapshot to its commit (issue #59).** Read `measured_commit` from `run-context.json` and fill the "Measured at commit" line so every absolute LOC/CCN figure in this report reads as a snapshot of one commit, not a current truth:

```bash
jq '.measured_commit' "$REPO_ROOT/.assess/run-context.json"
```

- When `available: false`, omit the "Measured at commit" line (no git history to pin to).
- Render `head_short` and `committed_date`. Add a `<staleness-suffix>` warning when the snapshot is stale, so a reader knows the numbers may have drifted:
  - `dirty: true` → append " - **working tree had uncommitted changes; figures include un-committed edits**".
  - `behind` is a positive integer → append " - **HEAD was <behind> commit(s) behind `<upstream>`; absolute figures are a snapshot and may read low against current code**".
  - Clean and up to date (`dirty: false`, `behind` 0 or null) → no suffix.

#### Where to focus testing

The cheap, always-on read of *which risky files most need test work, and which kind* - the hotspot risk band crossed against the parsed coverage report and the hollow-test heuristics, pre-joined into one ranked list by the deterministic core. Render it from `run-context.json .test_focus.entries` (the top 5-10). **Omit the whole subsection** when `.test_focus.available` is false or `entries` is empty.

Open with the **coverage provenance line** from `.coverage_report`, so a reader knows whether the signals rest on a real coverage report or on heuristics alone:

- `available: true` → _"Coverage data: `<source>` (Cobertura)"_ (name the report file from `.coverage_report.source`; the format is `cobertura` or `lcov`).
- `available: false` with `source: "none found"` → _"Coverage data: none found - test signals are heuristic-only."_
- `available: false` with a `reason` (the scan failed) → _"Coverage data: not read (`<reason>`) - test signals are heuristic-only."_ Never render a failed read as "none found".

Follow it with the **coverage gate line** from `.coverage_gate`:

- `available: false` (the scan failed) → _"Coverage gate: scan failed (`<reason>`)"_. Never render a failed scan as "none detected".
- `configured: true` → _"Coverage gate: `<file>:<line>` (`<form>` `<threshold>`)"_ for each gate: the threshold as written, with `ratio` appended for a JaCoCo ratio and `uncovered lines` for a negative Jest value (`unit: uncovered_count`, so `-10` reads "at most 10 uncovered lines", never minus ten percent). A `threshold: null` reads "tool default" for an nyc/c8 `check-coverage` and "threshold not read" for any other tool.
- `configured: false` with gates listed (`fail_under = 0`, or a Jest/Vitest threshold not read) → _"Coverage gate: `<file>:<line>` configured at 0 / threshold not read, gates nothing"_ for each.
- `configured: false` with no gates → _"Coverage gate: none detected"_.
- Close the line with the configured 50 percent minimum (`.coverage_gate.threshold_meets_minimum`): `true` → _"meets the 50% minimum"_ (name `max_threshold_percent`, so a JaCoCo `0.80` reads 80%); `false` with a counted gate → _"below the 50% minimum (highest `<max_threshold_percent>`%)"_; `null` with `unmeasured_reason: go_rust_gate_form_not_read`, with or without gates → _"50% minimum not measured: Go and Rust gates written in a script are not read"_; any other `null` with gates → _"50% minimum not measured: a threshold is not a readable percentage"_. Otherwise omit it: with no gates, or when gates are listed but none is counted (`configured: false`). Use `threshold_percent` only for this comparison; each gate still shows its threshold as written. Then add whether CI runs it (`.coverage_gate.enforced_meets_minimum`, the signal Layer 6 level 1 reads): `true` → _"run in CI"_; `false` with `enforced_basis: not_run_in_ci` or `no_ci` → _"no CI step runs it"_; `null` with `enforced_basis` `unknown_trigger`, `indirect_run`, `included_jobs` or `unread_ci_system` → _"CI run not measured: <enforced_basis>"_. Omit it when the threshold line is omitted or `threshold_meets_minimum` is not `true`.

Then the table, one row per entry, mapping the raw `.test_focus.entries` values to human-readable labels:

| File | Risk | Test Signal | Suggested Action |
|------|------|-------------|------------------|
| `<path>` | High/Medium/Low | <test_signal label> | <suggested_action label> |

- **Risk** ← `risk_band`: `high` → High, `medium` → Medium, `low` → Low.
- **Test Signal** ← `test_signal`: `no_covering_test` → "No covering test", `covered_but_hollow` → "Covered but hollow", `unknown_no_coverage` → "Unknown (no coverage)", `unsupported` → "Unsupported (no report; no sibling or parallel-tree test found)", `sibling_test_only` → "Test file present, coverage unmeasured".
- **Suggested Action** ← `suggested_action`: `add_tests` → "Add tests", `strengthen_assertions` → "Strengthen assertions", `measure_coverage` → "Measure coverage".

**Cross-reference, don't duplicate.** This table surfaces the **cheap** signals - risk band, coverage presence, and the hollow-test heuristics. The **expensive** confirmation lives elsewhere and is referenced, not repeated: the `untrusted_hotspot` finding confirms which files mutation testing proved hollow, and the Layer 6 green-but-hollow row in the Lying Signals table pairs a file's coverage against its mutation score. A file flagged `covered_but_hollow` here and confirmed by `untrusted_hotspot` there is the same file seen through the cheap then the expensive lens - so point at the finding, don't restate it.

**Disclose active declines.** When `run-context.json .decline_disclosures` is non-empty, the user has permanently declined one or more optional tools for this repo, so a capability was deliberately silenced. Render each line verbatim under a short **"Declined tools"** note so the read stays honest about its own depth - e.g. _"Mutation testing permanently declined by ben on 2026-07-07"_ tells the reader the Layer 1/6 depth was capped by choice, not by a gap. Omit the note entirely when `decline_disclosures` is empty (no markers, the common case).

**Disclose owner exclusions.** When `run-context.json .owner_exclusions.disclosures` is non-empty, the owner excluded one or more maturity-model conditions in `.assess/config.toml` `[checks]`, each with a reason. Render each line verbatim under a short **"Excluded by owner"** note, beside the declined tools. When `.owner_exclusions.skipped` is non-empty, list each entry under the same note as "exclusion not applied (`[checks] exclude[<index>]`): <problem, underscores as spaces>; the condition stays in force", so an owner who mistyped an id or left out a reason sees that the condition still counts. An excluded condition neither earns nor costs, and an excluded gate condition keeps its Step out of reach; the headline already says so, so do not restate the Step. Omit the note when both `disclosures` and `skipped` are empty.

#### Doc navigability

Read the structured signal from `run-context.json` (`.doc_graph` without its per-document `link_parents` list - `jq '.doc_graph | del(.link_parents)'` - plus `.doc_staleness` and `.stale_hubs`) and write this in **plain language** - explain the metrics, don't just dump numbers. Define each term the first time you use it:

- **Navigability, in words.** e.g. _"Of <N> docs, <P%> are reachable from the README by following links and backticked doc paths; the other <N> are orphans (nothing links to or cites them) or sit in <K> disconnected islands."_ Pull `island_count`, `orphan_rate`, `reachability_pct`, and name 2-3 specific orphans/islands. Reachability counts both links and **reference edges** (a backticked path to an existing doc, such as `docs/arch.md` in `CLAUDE.md`, which an agent follows as readily as a link); `link_only_reachability_pct` / `link_only_orphan_rate` give the link-only view, so when they differ say how much of the wayfinding rests on backticked citations rather than links. These headline figures are the **curated wiki layer** (see the raw-source split below), so `doc_count` is `curated_doc_count`. **Frame it as curation, not access** - do not write "an agent can only discover <P%>": an agent can still `ls docs/` and open any file by path, so low reachability means the docs lack a navigable map (weak wayfinding / signal-vs-noise), not that content is hidden. State that adding an index/MOC is the fix, and present it as a wayfinding improvement, not a blocker - for a directory-organised tree, temper the priority accordingly.
- **Curated wiki vs raw sources.** When `doc_graph.excluded_raw_trees` is non-empty, a subtree of raw, machine-extracted source documents (a disclosure / SAR export of converted `.msg`/`.pdf`/`.docx` files, say) was auto-detected and **excluded** from the orphan/reachability/broken-link figures above - those files are immutable raw sources that legitimately have no inbound links and carry machine-extracted non-navigational links, so counting them would inflate the figures and mask the curated signal. **Name each excluded tree and its file count** (e.g. _"excluded raw-source tree `sar-export/`: 142 files"_) so the exclusion stays legible, and report the raw layer's own figures separately (`raw_source_doc_count`, `raw_source_orphan_rate`, `raw_source_broken_links`) as context, not as a gap to fix. The headline read-side signal is the curated layer. When `excluded_raw_trees` is empty, say nothing about this - the repo has no raw tree and is unaffected. **Working notes** (`doc_graph.excluded_working_notes_trees`) are the same move for a second kind of tree: pattern-named notes (plans, session logs, tickets) hung off one or two index files. Name each tree and its file count (e.g. _"excluded working-notes tree `notes/`: 51 files"_) and give `working_notes_doc_count` / `working_notes_orphan_rate` / `working_notes_broken_links` as context; a notes tree that dwarfs the curated wiki is itself worth one line (consolidate or archive it), never a navigability gap. A misclassified tree is fixed in `.assess/config.toml`: `working_notes_ignore` keeps a directory counted, `working_notes_dirs` excludes one from the headline. Empty list: say nothing.
- **Attribution.** Describe these checks in plain terms (orphans, broken links, connectivity, hubs). The README credits Andrej Karpathy's LLM-wiki "Lint" pattern as the influence once - **don't repeat that attribution in the generated report.**
- **Lying maps (stale docs of churning code).** Define the terms inline: **stale** = days since the doc's last content change (bulk mechanical commits such as a licence-header sweep are skipped; when `doc_staleness.bulk_commits_skipped` is non-empty, name the newest skipped commit's short SHA, date and `docs_touched` of `doc_count` in one line so the reader knows the clock looked past it; when `bulk_commit_scan_complete` is false the history was unreadable or shallow, so say the ages may include bulk commits; a doc with `last_change_basis: "creation"` was only ever touched in bulk, so its age is a creation date - do not call it stale); **subject churn** = file-commits in the window to the code the doc describes (`code_churn_in_window`); `code_churn_since_doc_change` counts the distinct commits to that code after the doc's last content change, and `ratio` is capped by it, so a doc that is not behind its subject has `ratio` 0; **centrality** = how many other docs point to it (a hub). A real lying map is **old AND beside genuinely churning code**. From `stale_hubs` / `doc_staleness.docs`, name the worst 2-3 - but **apply judgment, don't trust the raw composite**: when a doc's `subject_method` is `repo-baseline`, its "subject churn" is just the whole repo's churn (a coarse proxy), so a *recently-changed* doc (low stale-days) that merely happens to be a big hub is **not** a lying map - don't flag it. Prefer docs with a precise association (`nearest-ancestor` / `parallel-docs-tree` / `explicit-links`) and high stale-days.
- **Cross-repo mappings.** When `doc_staleness.declared_cross_repo` is present, say in one line how many `[[describes]]` mappings the repo declares and that they are not measured outside fleet mode (`/fleet` measures them against the described repositories); name any `status: "invalid"` entries as rejected config. Never guess their freshness or call those docs stale or current from this run.

Colour = staleness (vivid red = a frozen doc beside churning code = a lying map); structure = reachability (centre = entry, rim = unreachable, dashed ring = orphan; a solid edge is a link, a dotted edge a reference - a backticked path to an existing doc); size = file length. Hover a node in the SVG (opened on its own) for its path and stats.

#### What changed since last run

<the diff summary (graduated / new / regressed / restructured / persistent), OR "_Diff suppressed: <note>._" when the prior snapshot was a different plugin version, OR "_No prior run to compare against - this is the first recorded snapshot._" on a first run.>

</details>

<details>
<summary>📊 Full scorecard (per-layer evidence & gaps)</summary>

The headline and the keyhole summary measure **different things and are never combined.** The Step and the verified tally answer _"which contracts are in place to catch problems?"_ (a property of the contracts and enforcement). The Keyhole Readiness summary answers _"where is today's structural pain?"_ - a pure count of structural concerns and safe zones rolled up from the ten cross-layer findings. A repo can hold Step 3 and still carry many structural concerns (strong contracts over churning legacy), or Step 1 with zero concerns (small, calm codebase). Report both side by side; never average, rescale, or fold one into the other. The per-layer rows below are detail under the headline: they explain it and never set it.

The **What it asks** column is the question that layer answers for an agent working here - read it first; the framework name is secondary. The **Band** orders them by dependency: a read-side blind spot makes the write-side scores mean less (you can't trust enforcement of a picture you can't see), and the meta band only matters once the rest works.

**Status labels (display only).** The scorer returns `Present`, `Partial`, `Missing` or `N/A`; render the Status cell as **In place**, **Partly in place**, **Next up** or **N/A**, and keep the scorer's token everywhere it is data (`layer_scores`, the finalize input). Write the Status, Evidence and Gap cells of this scorecard in the supportive vocabulary: the banned words of the deterministic vocabulary list (tangled, penalty, weakest, weak, lying, missing, fail, fails, failed, failing, failure, and the retired band labels Not Ready, Basic, Solid, AI-Native) stay out of those cells, and a gap reads as the next goal, never a verdict on the people who built the repository. This rule covers the scorecard cells only; the fixed headings and lines the rest of this template prescribes are unchanged here.

| Layer | What it asks | Band | Status | Evidence | Gap |
|-------|--------------|------|--------|----------|-----|
| 0: Agent Instructions & Navigability | Can I build a true map of this codebase before I touch it? | read | In place/Partly in place/Next up | <what was found> | <what's next> |
| 1: Runtime Legibility / Liveness | Can I see which parts are live, which need attention, and which are dead weight? | read | In place/Partly in place/Next up | <what was found - if rung 3, append: "Reachable *if* the agent has `<tools cited in runbooks>` in its execution environment"; if no deployed runtime, note that liveness is read through complexity + churn + reachability, not telemetry> | <what's next> |
| 2: Code Design | Will the type-checker catch my mistakes? | write | In place/Partly in place/Next up | <what was found> | <what's next> |
| 3: Linters | Are complexity and style bounds enforced, or will my code drift? | write | In place/Partly in place/Next up | <what was found> | <what's next> |
| 4: Architecture Tests | Are the structural conventions executable, or just folklore? | write | In place/Partly in place/Next up | <what was found> | <what's next> |
| 5: CI Pipeline | Does something automatically catch a bad change before it merges? | write | In place/Partly in place/Next up | <what was found> | <what's next> |
| 6: Coverage Gates | Do the tests constrain behaviour, or just execute lines? | write | In place/Partly in place/Next up | <what was found> | <what's next> |
| 7: Code Review Bots | Is there design-level feedback on every change? | write | In place/Partly in place/Next up | <what was found> | <what's next> |
| 8: AI Project Mgmt (capstone) | Do learnings feed back into the contracts, or evaporate? | meta | In place/Partly in place/Next up | <what was found> | <what's next> |

**Archetype-aware Status (`N/A` ≠ `Missing`).** When the scorecard marks a layer **N/A** (the write-side layers L2-L7 on a detected knowledge base - see the headline section below), render its Status cell as `N/A`, its Evidence as "not applicable - <archetype> repo has no code surface for this layer", and its Gap as "-". **Never render an N/A layer as Missing** and never propose a remediation action for it: Missing is a real gap (the contributor is asked to be careful where a guardrail could exist); N/A means the layer cannot apply here. An N/A layer names no gap, so a knowledge base strong on its applicable layers reads as ready, not as failing the layers it has no surface for.

**The Evidence cell is never blank, and a scored layer has two empty states and one partial state.** When the scorer offered no entries for a scored layer (or returned no `evidence` list), keep its one-line note there and end it with `(unverified)`, so the gap reads as a limit of this run rather than as a fact. An N/A layer is neither state: it keeps the archetype rule's text above and takes no marker, because nothing about a layer that cannot apply awaits verification. When a layer's entries were offered and every one was rejected, drop the scorer's note, since repeating it restates a claim the check refuted, and render the gap instead: `no verified evidence - N claim(s) rejected`, followed by each rejected entry's `reason` from `evidence_rejected`. When only some of a layer's entries were rejected, strike the refuted claims from the scorer's note (or rebuild the cell from the entries that held) and end it with `- N of M claim(s) rejected`, so the cell cites only verified entries and still shows that the check refuted part of the note.

**Layer 1 evidence carries an explicit caveat by default.** The Layer 1 spec is honest about its boundary: this scores what the *repo* makes agent-reachable, not what the agent has installed at runtime. So when Layer 1 is **Present** (rung 3), the Evidence cell should not read "Reachable, full stop" - it should name the cited tools and conditionalise on their availability. Example: _"Runbook fences `kubectl logs`, `stern`, `logcli`; reachable *if* the agent has these in its execution context."_ When Partial or Missing, the caveat is moot - no rung-3 claim is being made.

### The headline (deterministic)

**State the headline only.** The headline is the `headline` block the core computed from the `level` block; paste it and never narrate a correction, a draft, or a reconciliation with the scorer's verdicts. If a layer verdict seems to disagree with the headline, the verdict is detail and the headline stands: the Step reads only the maturity model's conditions. The evidence markers above (`(unverified)`, `N of M claim(s) rejected`) are findings about the repository and stay.

- **Line 1, the Step.** The highest Step whose gates hold and whose count threshold `tally.met` reaches, phrased as the contracts in place ("Step 3 · Unattended agent PRs · contracts in place").
- **Line 2, the Next line.** The next Step and the change that reaches it: a gate first ("unlocks when CI runs the tests"), then any count still short ("and 1 more verified practice is met"). A gate the run could not see says "not measured here" with its reason.
- **Line 3, the tally.** "Verified practices: N of M measured": N is `tally.met` (measured and verified conditions met), M is `tally.measured` (measured conditions only), then the not-measured count with each reason and the maturity-model version. A condition that was not measured is never in M, so a blind run neither lowers nor raises the tally. Config-only and declared conditions are listed (`level.listed`), never counted.

There is no layered score, no fraction and no band name anywhere in the report. For a knowledge base (`.archetype.archetype` is not `"software"`), the scorecard marks the write-side layers N/A as above; the headline is unchanged, because it reads conditions, not layers.

### What the Step unlocks

Each Step names the agent **autonomy** this repo's contracts support, in the vocabulary of the adoption ladder ([Steps of AI Adoption](https://www.threads.com/@boris_cherny/post/Da4CkQnEea-), B. Cherny, 2026). Gates accumulate: each Step keeps the gates of the one below. The Step definitions and their count thresholds live with the maturity model (`skills/assess/levels/default.toml`), so they are versioned.

| Step | Contracts in place for | Gate added at this Step |
|------|------------------------|-------------------------|
| 1 | Assisted: one supervised agent | none |
| 2 | Supervised agent PRs | `instructions`: an instruction file an agent reads |
| 3 | Unattended agent PRs | `ci_runs_tests`: CI runs the tests, unmasked |
| 4 | Autonomous loops | `coverage_gate`: an enforced coverage gate |

**This is a bound, not a claim about how the team actually works** - a Step 3 repo whose team reviews every diff by hand is operating below its bound; a Step 1 repo running unattended agents is operating beyond it (the risk the headline names). A Step is a capability unlocked, never a grade of the team.

</details>

<details>
<summary>🔎 Cross-layer findings & lying signals (keyhole detail)</summary>

### Lying Signals

These artefacts look true but aren't - the most dangerous failure mode for an agent navigating the codebase. Each row is something the repo presents as trustworthy that a scan flagged as hollow: a map of a place that moved, code that reads as live but is never called, a gate that reports "tested" without pinning behaviour.

| Layer | Signal Type | Instance | Why it lies |
|-------|-------------|----------|-------------|
| 0 | Stale hub doc | `<path>` (<N>d stale; subject churned <M> commits in window) | A central doc agents anchor on, frozen while its subject code moves - reads as the map, describes terrain that no longer exists |
| 0 | False instruction claim | `<file>:<line>` - `<path>` (<kind>) | The agent instruction file states enforcement, a pin or a file count the repo does not back - an agent trusts the rule as guarded when nothing checks it |
| 0 | Ignored Claude Code config | `<file>:<line>` - `<key>: <value>` (<kind>) | A skill, command or agent field Claude Code drops without an error - the configuration reads as set while the runtime ignores it |
| 1 | Dead-but-present | `<path>` - `<symbol>` (<kind>) | Compiles and reads as live, but nothing in *this* repo calls it - an agent extends or trusts a path that is never exercised |
| 5 | Config snapshot drift | `<file>` - `<key>` (tracked <T>, live <L>) | A committed copy of platform configuration that reads as restorable truth while the live setting has moved - restoring it silently reverts a deliberate change |
| 6 | Green-but-hollow | `<path>` (coverage <C>% vs mutation <K>%) | Tests execute the file (green coverage) but don't constrain it (mutants survive) - the gate says "tested" while behaviour is unpinned |
| 7 | Hollow required review | `review_reality` (<R>% of <N> merged PRs reviewed, <A>% approved, by someone other than the author; <S>% self-merged) | The branch rules require an approving review, so review reads as enforced, while merges land without one - the gate is bypassed as routine |

**Populate each row from `run-context.json`; omit any row whose signal is absent or below threshold, and omit the entire Lying Signals subsection when no row qualifies:**

- **L0 - stale hub doc:** take `stale_hubs[0]` only when `stale_hubs` is a list (an object with `available: false` means the ranking failed: no row) and its `ratio > 2.0` **and** `confidence != "low"` (a `repo-baseline` subject is `confidence: low` - its "subject churn" is the whole repo's churn, too coarse to call a lie). Fill from `path`, `last_commit_days`, `code_churn_in_window`.
- **L0 - false instruction claim:** when `instruction_claims.failed > 0`, take the first entry of `instruction_claims.failures` that the scorer confirmed and cited in Layer 0 evidence; when it confirmed none, no row. Fill from `file`, `line`, `path`, `kind` and `reason` (`enforcement`: no CI configuration or task runner references the script; `pin`: the pinned file lacks `version` or does not exist; `count`: the sentence claims `claimed` files for the pattern and `actual` match - print both numbers). Name the count when more than one was confirmed ("and <n - 1> more").
- **L0 - conflicting doc claims:** when `doc_claims.contradictions_total > 0`, take the first entry of `doc_claims.contradictions` that the scorer confirmed and cited in Layer 0 evidence; when it confirmed none, no row. Fill from `kind`, `path` and the two `conflict_pair` sites (two different files whose values disagree) as `<file>:<line>` with each `value` (`pin`: two versions pinned in `path`; `count`: two counts for the pattern `path`). Name the count when more than one was confirmed ("and <n - 1> more").
- **L0 - ignored Claude Code config:** when `claude_config.available` is true and `claude_config.findings` holds an entry that is neither `legacy_command` nor a `command_unsupported_key` with `key: name`, take the first such entry; fill from `file`, `line`, `key`, `value` and `kind`, and end the Why cell with its `fix`. Name the count of the others when there are more ("and <n - 1> more"). Legacy command files and `name` in a command file alone get no row (both still load; a repo may keep `name` for another loader); mention them in the Layer 0 evidence instead, with the count from `claude_config.counts_by_kind.legacy_command`. `available: false` means no Claude Code configuration was found: no row, and never report it as clean. The row is a Layer 0 detail: it does not change the findings number on the badge.
- **L1 - dead-but-present:** take `dead_code.candidates[0]`; fill from `path`, `symbol`, `kind`. Keep the `dead_code.caveat` in mind - static reachability proves "nothing in this repo calls it," never "no external consumer calls it" - so frame it as a candidate, not a verdict.
- **L5 - config snapshot drift:** take `config_drift.entries[0]` (ranked worst first) only when `config_drift.available` is true; fill from `file`, `key`, `tracked`, `live` (a changed list carries `count`, `removed`/`added` and a short `sample` of names, not the list itself). `available: false` means the comparison did not run (`reason` says why; `no_access` when the token cannot read the setting) - no evidence either way, so no row.
- **L6 - green-but-hollow:** take `test_pressure.survivor_clusters[0]` only when `test_pressure.survivor_density.overall > 0.3`; fill the file from the cluster's `file`. State the mutation score as `1 - survivor_density.overall` and pair it with the file's line coverage when you have it. This is the hollow-gate pattern Layer 6 scores Partial for.
- **L7 - hollow required review:** add the row when `review_reality.hollow_required_review` is true (review is required and under 20% of sampled merged pull requests carry any review from someone other than the author: nobody looked) or `review_reality.required_approval_bypassed` is true (review is required and under 20% carry an approval from someone other than the author: a review bot may comment, but the required approval does not happen). Name which flag fired. Fill from `merged_count`, `reviewed_share`, `approved_share` and `self_merged_share` as percentages; the block holds counts and shares only, so name no pull request or account. The rules are read as they stand now; when `oldest_merged_days_ago` reaches back before the rule could have existed, say so. `available: false` or null flags (unreadable rules, or under 5 merged pull requests) - no row. On a GitLab remote (merged merge requests via `glab`) `reviewed_share` and `bot_review_share` come from the merge requests' approvals and notes; a null there means a merge request's notes were too many to decide within the up to 3 pages (300 notes) read - write the row from `required_approval_bypassed` and `approved_share`, and leave the reviewed percentage out rather than reporting it as unavailable.

### Instruction placement

<Layer 0 evidence, from `instruction_placement`. One line per entry of `files` that has a `relocatable` section or is `flagged`: `<path>` - `<always_on_lines>` of `<total_lines>` lines always-on (`<always_on_share>` as a percent), "flagged" when `flagged` is true. Under it, one bullet per `relocatable` entry: `<path>:<start_line>-<end_line>` *<heading>* (`<lines>` lines; `<signals>`) → `<home.kind>` `<home.target>`; for `existing_skill`, "replace with a pointer to `<home.target>`". Add "and <relocatable_total - len(relocatable)> more" when the list was capped. Then a separate **Loaded in full (informational)** line per `loaded_in_full` entry: `<path>` (`<kind>`, `<total_lines>` lines, `<always_on_share>` always-on), with its relocatable sections as bullets in the same form (home `reference_doc`, or `existing_skill` when the text is already in another skill; a `null` `reference_doc` target reads "a reference document the agent reads on need"). When `files` holds no flagged file and no relocatable section, one line: "no instruction-file section clearly belongs outside the always-loaded layer". Moving the largest sections of a flagged instruction file is a Top 3 candidate (see the scorer's Layer 0 rule); `loaded_in_full` never ranks. Not a lying-signal row, not counted on the badge; the instruction file's grade carries the same split through its `bloat_penalty`, and `loaded_in_full` has no grade effect. `available: false`: "instruction placement not checked" with the `reason`.>

### Agent harness

<Informational, beside the Claude Code configuration evidence. From `agent_harness`: one line naming what the committed agent configuration runs on every session, from `executes_by_kind` (e.g. "2 settings hooks, 1 MCP server (`.mcp.json`: `db` runs `npx`)"), or "no committed hooks, env, apiKeyHelper or MCP servers" when `executes` is empty. Then one bullet per entry of `findings`: `<file>[:<line>]` - `<detail>` (`<kind>`), ending with its `fix`. Never print a value the block does not carry: it names a tracked `.env` by path and an `env` variable by name only, and reports hidden characters as code points. When `tracked_known` is false, add "tracked-file checks not run (not a git repository)". A `tracked_env_file` or `tracked_local_settings` finding is a Top 3 candidate (see the scorer's Layer 0 rule); the rest stay here. Not a lying-signal row and not counted on the badge.>

### Agent consistency

<From `agent_consistency`: does every agent system get the same map? With `applicable` false, one line: "one agent system (`<systems>`): nothing to compare". Otherwise open with "<systems_total> agent systems; canonical source `<canonical>`". When `consistent` is true, one line saying every system reads the same instructions. Otherwise **Differences**: one bullet per `divergences` entry, a command as `` `<value>` `` named in `<named_in>`, missing from `<missing_from>`, a threshold as `<value>` stated as the `stated` numbers per system. Then **Next steps**: one bullet per `next_steps` entry, `<path>` (`<system>`): its `pointer` verbatim in a code span as the line to add to that file (for Aider, an entry under `read:` in `.aider.conf.yml`; keep the file's other settings), and `symlink` verbatim as the alternative when not null. Never rewrite a pointer line and never write one for a system the block does not list. Supportive voice: a shared source is what keeps every tool's agent on the same checks, never a failing. Not a lying-signal row.>

### Agent environment

<Layer 5 evidence, from `agent_environment`. One line each: **Setup** - the `setup.sources` paths, or "none committed"; **Check** - `check.named[0]` as `<file>:<line>` `<command>`; when `named` is empty and `check.unresolved` is not, its first entry as `<file>:<line>` `<command>` "does not resolve" with its `reason` (fix that command, do not add a second); when both are empty, "no instruction file names a check command" and the `check.entry_points` commands it could name; **Pinning** - the manifests with `locked: false`, or "every manifest locked" (`all_locked: null`: no manifest); **CI duration** - `slowest_median_seconds` as minutes for `branch` over `runs_sampled` runs against the 10-minute guidance, or `ci_duration.reason` when unavailable. Then the cap: when `layer5_cap.applies`, "Layer 5 capped at Partial: <reason>". One bullet per `findings` entry: `<file>[:<line>]` - `<detail>` (`<kind>`), ending with its `fix`. A capped Layer 5 makes the missing piece a Top 3 candidate: name the instruction-file line to add or the setup file to commit. Not a lying-signal row and not counted on the badge.>

### Guardrails stated

<From `guardrails`: do the instruction files tell an agent what the gates enforce, so the next session does not trip over the same gate? Open with one line: "<stated> of <enforced> configured gates are stated in the instruction files". Then **In place**: one bullet per gate with `stated: true`, `<kind>` (`<tool>` `<rule>`, threshold `<threshold>` when not null) at `<file>[:<line>]`, stated in `<stated_in>`. Then **Next steps**: one bullet per gate with `stated: false`, the gate as above, followed by its `snippet` verbatim in a code span as the line to add to `CLAUDE.md` or `AGENTS.md`. Never rewrite a snippet and never write one for a gate the block does not list. With `enforced` 0, one line: "no configured gate found to state" and the `not_read` forms. `available: false`: "guardrails not checked" with the `reason`. Supportive voice: a stated gate is something the repository already does well, an unstated one is a ready line to add, never a failing. Adding the snippets is a Top 3 candidate when two or more gates are unstated. Not a lying-signal row.>

<paste the `findings_markdown` string from run-context.json verbatim here - it renders as a `## Cross-Layer Findings (Keyhole Readiness)` block with the ten findings, their paths, their deterministic actions, and the attention list. Do not paraphrase, summarise, reorder, or drop any finding. The block's own `## Cross-Layer Findings (Keyhole Readiness)` line is the section's ONLY heading - never add a framing heading of your own (no `### Cross-Layer Findings (Keyhole Readiness)` above the block; the heading must appear exactly once in the report). If a sentence or two of framing helps a reader, place it directly under the block's heading line, before the first `###` finding; the findings and attention list themselves are copied, not rewritten.>

</details>

<details>
<summary>✅ Strengths & further opportunities</summary>

### Strengths

<3-5 bullet points. What this repo already does well. Be specific - name files, tools, and patterns. Acknowledge existing infrastructure.>

### Additional Opportunities

<If more than 3 gaps exist, list remaining as brief bullets. Keep to one line each. These are "after you've done the top 3" items.>

</details>

<details>
<summary>🧭 How to read this report (framing & method)</summary>

**What this is ultimately measuring.** When you hand work to an AI here, does it behave like a brand-new hire or like an engineer who has been in the org eighteen months? The difference isn't capability - it's *externalized context*: knowing where things live, which contracts other code relies on, where the minefields are. An AI contributor is structurally always the new hire - it starts every session with an empty head, seeing one narrow slice through its context window - so this report scores how much of the tenured engineer's implicit map the codebase has made explicit and navigable. The aim is not an AI that comprehends what humans no longer can and is trusted blindly (that is the dangerous case); it is a codebase legible enough that the relevant slice fits one context window and the agent's answers stay anchored to code you can still verify. **Legibility you can trust, not omniscience you can't.** The same ethic points at the write side: when a contributor makes a mistake, the useful question is what made it possible and what would make it impossible next time - so the write-side layers below (linters, architecture tests, CI gates, coverage, review automation) read not as a leash on the agent but as the guardrails that protect any contributor, human or AI, from costly mistakes by design. A **Missing** there means the codebase asks the contributor to be careful where it could instead make the wrong move hard to make.

This is an improvement roadmap, not a verdict. It measures one thing: **is the codebase kept honest, not just scaffolded.** It pairs three views:

- **Where the codebase is today** - the complexity heatmap shows current complexity and churn. Vivid red = complex AND actively changing = the files most likely to bite an agent (or a human) next week.
- **Whether an agent can navigate it** - the doc graph shows the docs' link structure: how much is reachable from the entry point, and which docs are stale maps of churning code.
- **What keeps it from getting worse** - the headline: the autonomy Step the contracts support and the verified practices behind it, with the per-layer scorecard underneath across three bands: read-side foundation (can the agent form a true picture?), write-side enforcement (can it be trusted to produce good output?), and meta (does the system keep itself honest over time?).

A codebase can hold Step 4 and still be on fire (strong contracts, legacy debt) - or Step 1 with a calm treemap (small codebase, no enforcement needed yet). The views matter together.

**Each layer is a sense the agent needs, not a box to tick - and this report is written to be understood without prior knowledge of the framework.** Every row in the scorecard names one thing an agent (or a newly-arrived human) must be able to *see or trust* before working safely here; it's phrased as the question that row answers. A **Missing** or **Partial** is not a mark against the codebase - it locates where someone working here is partly blind, and the Gap column says what would restore the view. One row matters most for cold readers, because it's the one most often misread: **Layer 1 (Runtime Legibility) is the agent's attention-sense - "which parts are actually live, which need attention, which are dead weight creating support burden?"** A running service answers that through telemetry (latency, errors, traffic); a repo with no deployed runtime answers it through complexity, churn, and reachability - so the complexity heatmap and the dead-code scan *are* its observability. A low telemetry score on such a repo means *look through those instruments instead*, *not* "the agent is flying blind." Observability, in this report, is whatever lets the agent see where to point effort - by whichever instrument fits the codebase.

**The legacy-transition lens - this is not a new problem.** The hard part of AI-assisted coding is the same one the industry has faced for 25 years: *what happens when the people who understood the code are gone, or the code outruns anyone's ability to understand it?* AI only changes the **velocity** - a codebase can now slide from modern to legacy in months rather than years, because agents can generate change faster than a team can keep comprehending it. So read this report through the accumulated discipline of legacy-code engineering, not as a novel AI concern. The grounded references below are deliberate: a reading agent already knows them, and they carry weight that "we think you should add tests" does not.

- **"Legacy code is code without tests."** - Michael Feathers, *Working Effectively with Legacy Code* (2004). A file that is **complex, churning, and untested is already legacy**, regardless of its age. The correct response is to write **characterization tests** (pin the code's *actual current behaviour*, since that is what callers depend on - not its intended spec) and find a **seam** (a place to change behaviour without editing the code) to get it under test, *then* refactor.
- **Don't rewrite - strangle.** The instinct under AI velocity is to throw a confusing module away and regenerate it. That is the oldest trap in the book (Joel Spolsky, *Things You Should Never Do*, 2000): a rewrite discards the undocumented behaviour and bug-fixes a working system has accreted. Prefer Martin Fowler's **Strangler Fig** - replace incrementally around the edges, behind tests. An agent's default ("regenerate from intent") is precisely the wrong move; say so where a hotspot invites it.
- **Hotspots and change-coupling are the map.** The complexity-×-churn heatmap and the coupling findings in this report *are* Adam Tornhill's (CodeScene) hotspot and change-coupling analyses - the modern, version-control-aware form of legacy comprehension. Treat the vivid-red hotspots and the `hidden_coupling` seams as the prioritized worklist, not trivia.
- **Decide per component (the 7 Rs).** Not every flagged unit should be refactored: retain, retire, rehost, replatform, refactor, re-architect, or rebuild. Dead weight → *retire*; complex-and-live → *characterize then refactor*; safe `refactor_boundary` zones → hand to an agent in isolation.

When you write the findings and Top 3 Actions, name these anchors where they apply - they tell a reading agent *how to think* about the situation, not just what the metric says.

**How it's measured.** This is an AI-readiness review run almost entirely on *traditional* tooling - static analysis, git history, and graph metrics over the docs and code. The model only writes the prose around those numbers; it does no scanning itself. That keeps a full run fast and close to zero in model tokens, and makes the structural findings reproducible run-to-run.

</details>

<details>
<summary>🤖 Machine-readable data (for agents)</summary>

If you are an agent working in this repo, the `.assess/` directory is actionable feedback written for you - the first place to look when deciding where to point effort. It is a compounding, AI-readable record that an agent can ingest directly, without re-parsing this prose, and it grows with every run:

- `.assess/assess-report.md` - this report: the scorecard, the lying signals, and the Top 3 Actions with exact commands and file paths.
- `.assess/run-context.json` - the full data bus (findings, attention, keyhole summary, prescribed actions, stats, diff).
- `.assess/complexity-stats.json` - complexity / estimated-token / LOC percentiles, the keyhole-budget rollup (`est_tokens.budget`), plus the ranked file lists (`top_hotspots`, `top_complex`, `top_large`) and `files`, every scored file's `{path, ccn}`.
- `.assess/hotspots/<file>.md` - per-file briefings, each with a **Suggested actions** section. Read a file's briefing before you change it.
- `.assess/index.md` - the catalog of every hotspot ever flagged (current and graduated), so you can see what has and hasn't been addressed.
- `.assess/log.md` - append-only run history, so a hotspot's trajectory (regressing / persistent / improving) is visible across runs.

</details>

---

<!-- chat-replace:report-footer -->
_Report generated by [`/ai-native-toolkit:assess`](https://github.com/bjcoombs/ai-native-toolkit). Install in any Claude Code session: `/plugin marketplace add https://github.com/bjcoombs/ai-native-toolkit` then `/plugin install ai-native-toolkit@ai-native-toolkit`._
```

### Filling the Top 3 Actions table (visible, essential)

Prioritize by leverage: agent instructions and CI first, then linters and coverage, then architecture tests and retro loops. Each action should be completable in a single session and reference **specific files** from the hotspot snapshot wherever possible - generic advice is the failure mode this report exists to prevent.

**Mandatory attention rule (hard, not a suggestion).** Every entry in `prescribed_actions` MUST appear in this Top 3 Actions table, at its `rank`, with its `path` and finding-derived action text (pre-rendered as table rows you can paste - see `render_prescribed_actions`). The core builds it from the top rows of `attention`: up to three, and only rank 1 when `attention_low_signal` is true (no row scores above 1, so the ranking separates nothing; say so in one line under the table). You MAY add context, attach a related gap to a prescribed path as a sub-item under the table (one concern per row, above), or fill the deterministic `?` cells (layer, effort, command) with judgement - but you may NOT omit a prescribed path unless it has already been addressed in this repo. Fill the slots `prescribed_actions` leaves free from `gap_actions` next, in list order, using each entry's `action` and `paths`, and only then by judgement, prioritising by leverage as above. A gap entry keeps its slot even when one of its `paths` is already a prescribed row: it is a different action on that file (measure or link it, not fix it), so both rows stay. This rule exists because both lists are the deterministic core's "look here first" output; letting the LLM silently drop them would reintroduce exactly the non-determinism Part 1 removes.

The `Issue` column is filled in later by the `assess-pr` step if the user opts to create tracking issues. Leave as `-` initially - that step mutates this table in place via the `assess-report.md` artifact, so the column is the explicit contract between the two units.

**Ground actions in the legacy-code precedent where it applies.** For a complex, churning, *untested* hotspot, the right first action is almost never "rewrite it" and rarely a bare "add tests" - it is **"add characterization tests for `<file>` to pin current behaviour, then refactor behind them"** (Feathers). For a tangled `hidden_coupling` seam, frame it as a **strangler-fig boundary** to replace incrementally, not a big-bang rewrite. For `candidate_dead_weight`, the action is **retire** (confirm liveness, then delete) - one of the 7 Rs. Naming the precedent (characterization test, seam, strangler fig, the 7 Rs) gives the action authority a reading agent recognizes and steers it away from the throw-it-away-and-regenerate instinct that AI velocity makes tempting and usually wrong.

**Frame actions positively.** "Add `cyclop` rule (threshold 15) to `.golangci.yml`" beats "Stop letting complex code through CI". Positive directives are easier for the next contributor (human or LLM) to act on - they say what to do, not what to avoid. If you find yourself writing "Don't X" or "Never Y", convert to "Use X (because Z)" instead.

**Use repo-relative paths only.** Never write absolute paths from your environment (e.g. `/Users/.../repo/src/foo.go`) into the report. They leak the author's directory layout, break shell commands for other contributors, and look unprofessional in committed artifacts. Repo-relative paths (`src/foo.go`, `.golangci.yml`) work everywhere.

**Currency-check any package you name (issue #62).** Before naming a specific package *version* in an action (`dart_code_metrics: ^5.7.6`, `some-linter@2.1.0`), prefer naming the **rule/concept plus a pointer to the language's package registry** (pub.dev, npm, crates.io, PyPI, RubyGems) over a hardcoded version - registries stay current; your training data doesn't. If you do name a specific package, verify it's still actively maintained for the repo's language/runtime version rather than trusting recall. This protects every language symmetrically: any canonical tool can be deprecated after the model's training cutoff (the `dart_code_metrics` case), so "name the rule + registry" is the safe default.

Good actions look like:

> _"Add `cyclop` rule (threshold 15) to `.golangci.yml`. Current per-function p95 is 23; immediate offenders by worst function: `internal/import/parser.go` (function ccn 67), `internal/sync/reconciler.go` (function ccn 54)."_

Cite the **per-function** value (`fn_ccn` / `max_fn_ccn`) against a per-function rule, not the file aggregate - a `cyclop:15` rule flags functions, so a file whose worst function is 12 is not an offender no matter how high its aggregate sums (issue #58).

Generic actions to avoid:

> ~~_"Improve code quality"_~~ - name the files and the threshold.
> ~~_"Add a linter"_~~ - name the linter, the rule, and the first three files it will flag.

**Read-side remediation must match the actual gap.** The diagnosis from Layers 0-1 changes the action:

- **Layer 1 Partial because observability is instrumented but not agent-reachable** (rung 1-2 - the `meridian` case): the action is _"make existing observability agent-queryable"_ - e.g. "add a `.mcp.json` log/metrics server" or "add a `view-logs` repo skill wrapping `logcli`" - **not** "add observability" (it's already there; the gap is reachability).
- **Layer 0 Partial because a hub doc is stale:** name the specific stale hub from `stale_hubs` and its churning subject - _"refresh `docs/architecture.md` (251d stale; subject `src/api/` had 47 commits in window)"_ - not "improve the docs".
- **Layer 0 Partial because the doc set is fragmented:** name the orphans / islands - _"link the 6 orphan docs into the MOC; `index.md` is named but not wired (out-degree 0)"_.
- **Layer 6 Partial because coverage is enforced but truth-pressure is unverified:** the actions are _"strengthen assertions to pin observable behaviour at the named survivors"_ and _"add mutation testing to CI (e.g. `mutmut`, Stryker, PITest)"_ - **not** "raise the coverage threshold" (higher line-coverage numbers manufacture more lying signal without improving behavioural constraint).

**Cross-check offenders against the recommendation's own scope (issue #59a).** When an action names a *scope* ("un-exclude `services/*/service/*.go`", "enforce the rule under `src/core/`"), every offender you list in that action's evidence must actually fall within that scope. Before writing the offender list, verify each path against the named pattern - a `service_modules.go` under `shared/pkg/saga/schema/` is **not** under `services/*/service/`, so it can't be cited as evidence for un-excluding `services/*/service/`. If the worst offenders sit outside the scope you're recommending, either widen the recommended scope to cover them or move them to a separate action with the right scope. The offender list and the recommended change must describe the same set of files.

**Apply the same truth-pressure to `/assess`'s own structural findings (issue #59c).** `/assess` grades other docs for being honest; its own topology/count claims are held to the same bar. Before stating any count-based finding - "the README lists 15 services but there are 19", "N modules lack a base doc" - **count the actual entries**, don't estimate or trust a single signal. For a service/component-count claim, enumerate both sides (what the doc lists vs what exists on disk) and report the real delta and the specific missing names, not a round-number guess. A wrong count ("missing 2" when it's actually missing 4, including specific named services) erodes trust exactly as a stale doc does. If you can't enumerate precisely, say "approximately" and name what you couldn't verify rather than asserting a false-precise number.

**Sub-threshold "approaching the cap" findings are not refactor tasks (issue #60).** Files in the *Watch* band but under the *High* threshold (e.g. 600-800 lines against an 800-line cap, or per-function ccn 10-14 against a 15 rule) are **not violations**. Do not default them into refactor tasks - pre-emptively decomposing files that currently work fine is churn-for-churn (review cost, merge conflicts, behaviour risk on working code). Instead:

- Keep actual violations (over the High threshold) as the standard remediation tasks.
- Surface "approaching the cap" files as an **optional, low-priority** item in *Additional Opportunities*, never the Top 3 - and frame it **annotate-first**: the recommendation is to add an acknowledgement marker (e.g. a `// large-file: tracked` comment or a lint allow-entry) so the file is consciously owned, leaving the decompose-or-not decision to the maintainer. Only escalate to a refactor task when the file is over the cap or the maintainer asks.
- Label the band explicitly in the report so a reader sees "under the cap, watch" - never present a sub-threshold file as if it failed a gate.

Complete the full assessment in under 2 minutes. Scan, don't deep-read.

Every snapshot SVG is embedded as a **clickable, relative link** - `[![alt](./complexity-heatmap.svg)](./complexity-heatmap.svg)` and the same for `./doc-graph.svg` and `./evidence-waffle.svg`. The `viewBox` lets GitHub scale them to the content column; the link lets a reader open the SVG on its own (the doc graph's hover tooltips only work when the raw SVG is opened directly - GitHub renders the inline copy as a static image). Keep the link relative, never a `raw.githubusercontent.com` URL (those are branch-specific and rot on rename). Omit a snapshot only if its script could not generate the SVG (record the reason in the snapshot-detail fold instead).

The plugin footer is important - it's how other engineers viewing the report in a PR discover the tool that produced it. Do not omit it, and keep it outside any fold so it stays visible.
