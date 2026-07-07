# Codebase Assessment: ai-native-toolkit

_Generated <<normalized>>._

**Score: 6.0 / 8 - Solid** - a readiness snapshot, not a verdict · Keyhole: 5 structural concerns (5 hidden coupling), 1 safe zone.

The write-side here is genuinely enforced, not decorative: a ruff complexity ratchet, a mypy type gate, and three blocking pytest jobs under branch protection catch a bad change before it merges, and per-function complexity actually sits under the bar (p95 9 against a 15 cap). The clearest opportunity is behaviour-constraint - 26 tests run in CI but no coverage or mutation gate yet proves they pin behaviour - which is exactly what would carry this from Solid toward AI-Native.

_Note: Mutation testing was not run. Layer 6 (Coverage) is capped at Partial and truth-pressure remains unproven._

> **Agents start here.** The prioritized Top 3 actions below are also machine-readable in `.assess/actions.json` (schema v2: every entry carries `rank`, `action`, `done_when`, and `scope_fence`, plus the lifecycle fields `status` / `claimed_by` / `completed_sha` and a derived execution `mode`). Read that file to pick up the work - even with a smaller model - without parsing this report's prose.

## Top 3 Actions

The mandatory attention paths (the `hidden_coupling` seams) are covered by Actions 1-2; Action 3 addresses the single highest-leverage scorecard gap (L0 navigability), which moves the overall score from 6.0 toward 7.0.

| # | Action | Layer | Effort | Command / First Step | Hotspot files this addresses | Issue |
|---|--------|-------|--------|---------------------|------------------------------|-------|
| 1 | Add a coverage step with a patch-coverage floor, then add mutation testing on the deterministic core to confirm the 26 test files actually pin behaviour | 6 | medium | Add `pytest --cov` to `tests.yml` with a patch floor; pilot `mutmut`/`cosmic-ray` on `skills/assess/scripts/lib/` | `skills/assess/scripts/lib/doc_graph.py`, `skills/assess/scripts/assess_core.py` | - |
| 2 | Extend the `mypy` gate from `scripts/lib/` to the orchestrator scripts so the most-churned files are type-checked too | 2 | small | Widen the `mypy` target in `skills/assess/pyproject.toml` / the lint job to include `assess_core.py`, `complexity-treemap.py`, `doc-graph-svg.py` | `skills/assess/scripts/assess_core.py`, `skills/assess/scripts/complexity-treemap.py` | - |
| 3 | Wire a small Map-of-Content (or extend `README.md`) linking skills, agents, and the plan archive, and close the missing cross-references so the README becomes a real index | 0 | small | Add an `index.md` MOC or a "Map" section to `README.md` linking `skills/*/SKILL.md`, `agents/*.md`, `docs/superpowers/plans/` | - | - |

### Why these three?

Action 1 is the highest-leverage gap: write-side enforcement is strong everywhere except behaviour-constraint - 26 test files run in CI but nothing proves they pin behaviour rather than just execute lines, and the deterministic core is exactly the kind of code (high aggregate complexity, heavy churn) where a survivor cluster would hide. Action 2 closes the remaining type-safety gap on the files that change most often (`assess_core.py` leads churn at 14 commits) - the config comment already names this as the next ratchet step. Action 3 is low-priority human-wayfinding polish: an agent navigates this repo fine by convention, but a human browsing on GitHub gets no map.

## Snapshots

### Complexity - riskiest to change

[![Complexity hotspot](./complexity-heatmap.svg)](./complexity-heatmap.svg)

Every hotspot is in `skills/assess/scripts/` - the assessment engine is the most complex, most-churned surface in the repo; per-function ccn p95 9 sits under the C901 gate of 15, so the high file-aggregate numbers are sums of many small functions, not monster functions.

### Doc navigability - can an agent find its way?

[![Doc map](./doc-graph.svg)](./doc-graph.svg)

Only 11% link-reachable (89% orphan, 35 islands), but this is curation, not access: most "docs" are skill-trigger / agent-persona files Claude Code loads by convention, not by link-traversal. No credible lying maps.

<details>
<summary>📈 Snapshot detail (commit, hotspots, navigability, lying maps)</summary>

#### Complexity profile

- **Measured at commit:** <<normalized>>
- **Files scored:** 58 (56 lizard, 2 scc)
- **Churn window chosen:** last 12mo
- **Complexity profile:** per-function ccn p95 9 (max 38); file-aggregate ccn p95 107 (max 169); p95 LOC 484 (max 761)
- **Top hotspots** (composite `sqrt(ccn) × sqrt(1 + commits)`). `ccn` here is the **file aggregate**; the worst single function per file is in parentheses:
  1. `skills/assess/scripts/assess_core.py` - 493 LOC, aggregate ccn 106 (worst function 38), 14 commits in window
  2. `skills/assess/scripts/lib/doc_graph.py` - 514 LOC, aggregate ccn 169 (worst function 23), 7 commits in window
  3. `skills/assess/scripts/complexity-treemap.py` - 482 LOC, aggregate ccn 115 (worst function 19), 10 commits in window

The hotspots are exactly the deterministic core of `/assess` itself - the most-churned, most-complex files are the scanners. That is expected for an actively-developed analysis tool, and the per-function complexity is fenced: ccn p95 is 9, well under the C901 threshold of 15, with the genuine offenders (worst function 38 in `assess_core.py`) carrying explicit `# noqa: C901` ratchet markers. The high *aggregate* ccn (169 on `doc_graph.py`) is many simple functions summed, not one monster function.

Size encodes lines of code, colour encodes cyclomatic complexity (dark red = high), saturation encodes recent git churn (vivid = active). Vivid red blocks are the migration risk.

No hatching visible - mutation analysis was not run. The hatching would mark covered-but-unpinned code (tests that execute without constraining). Run `/assess` and accept the mutation offer to enable it.

#### Where to focus testing

Coverage data: none found - test signals are heuristic-only.

The cheap, always-on read of which risky files most need test work. No coverage report exists for this repo, so every covered/uncovered call is unknown rather than guessed - each risky file is surfaced for test work, not silently blessed as clean.

| File | Risk | Test Signal | Suggested Action |
|------|------|-------------|------------------|
| `skills/assess/scripts/assess_core.py` | High | Unknown (no coverage) | Add tests |
| `skills/assess/scripts/lib/doc_graph.py` | High | Unknown (no coverage) | Add tests |
| `skills/assess/scripts/complexity-treemap.py` | High | Unknown (no coverage) | Add tests |
| `skills/assess/tests/test_assess_core.py` | Medium | Unknown (no coverage) | Add tests |
| `skills/assess/scripts/lib/doc_staleness.py` | Medium | Unknown (no coverage) | Add tests |
| `skills/assess/scripts/lib/liveness_scan.py` | Medium | Unknown (no coverage) | Add tests |
| `skills/assess/tests/test_doc_graph.py` | Medium | Unknown (no coverage) | Add tests |
| `skills/assess/scripts/doc-graph-svg.py` | Low | Unknown (no coverage) | Add tests |

(8 of 10 focus targets shown; the full ranked list is in `run-context.json` under `.test_focus.entries`.)

These are the **cheap** signals - risk band plus the absence of any coverage report. The **expensive** confirmation lives in the cross-layer findings: the `untrusted_hotspot` finding confirms which files mutation testing proved hollow, and the Layer 6 green-but-hollow row in the Lying Signals table pairs coverage against the mutation score. Both are silent on this run because mutation wasn't collected - which is the Layer 6 gap itself, and the reason every signal above reads "unknown" rather than a confirmed hollow.

#### Doc navigability

Of 37 docs, **11% are reachable** by following links from the entry points (`CLAUDE.md`, `README.md`); the rest sit in **35 disconnected islands** (89% orphan rate, only 2 inter-doc links total). At face value that reads alarming, but read it as **curation, not access**: this is a Claude Code *plugin* repo, and most of its "docs" are not prose articles - they are skill-trigger files (`skills/*/SKILL.md`), agent-persona prompts (`agents/*.md`), and dated planning records under `docs/superpowers/plans/`. Claude Code loads these by *trigger and convention*, not by link-traversal, so the absence of cross-links is largely by design. An agent can still `ls skills/` and open any file by path - nothing is hidden.

The honest gap: a human browsing on GitHub has no map. The `README.md` is rich but doesn't function as a wired index - it links out once. Adding a Map-of-Content that links the skills, agents, and plan archive would help human wayfinding. This is a **wayfinding improvement, not a blocker** - priority is low for an agent-loaded plugin repo. The scanner also flags **missing cross-references** (docs that name another doc's filename in prose without linking it, e.g. several plans naming `skills/pr-review-merge/SKILL.md`) - low-effort wins if you want the README to become a real index.

Colour = staleness (vivid red = a frozen doc beside churning code); structure = reachability (centre = entry, rim = unreachable, dashed ring = orphan); size = file length. Open the SVG directly for per-node hover tooltips.

#### What changed since last run

_Diff suppressed - the prior snapshot was produced by an earlier plugin version, and file-filter differences across versions surface phantom graduated/new transitions that didn't really happen. Cross-run comparison resumes once two runs share a plugin version._

</details>

<details>
<summary>📊 Full scorecard (per-layer evidence & gaps)</summary>

The two headline metrics measure **different things and are never combined.** The 0-8 score answers _"is the scaffolding in place to catch problems?"_ The Keyhole Readiness summary answers _"where is today's structural pain?"_ - a count of structural concerns and safe zones from the eight cross-layer findings. Here: strong scaffolding (6.0/8) over a small, cohesive, actively-developed subsystem whose files change together.

| Layer | What it asks | Band | Status | Evidence | Gap |
|-------|--------------|------|--------|----------|-----|
| 0: Agent Instructions & Navigability | Can I build a true map of this codebase before I touch it? | read | Partial | `CLAUDE.md` grades A (184 lines, lean), `.github/claude-review-instructions.md` grades A; 6 skills factored for progressive disclosure; no broken/untracked refs, no sensitive content | Doc set is link-fragmented (89% orphan, 35 islands, missing xrefs) - strong instructions, weak human wayfinding map |
| 1: Runtime Legibility / Liveness | Can I see which parts are live, which need attention, and which are dead weight? | read | Partial | Honest rung 0: `instrumented: false`. No deployed runtime - this is an on-demand script/prompt repo, so liveness reads through the complexity heatmap + churn + reachability, not telemetry. `vulture` absent so intra-repo dead-code not run | No telemetry to instrument (expected for a prompt/tooling repo); install `vulture` to enable the Python dead-code scan |
| 2: Code Design | Will the type-checker catch my mistakes? | write | Present | Python type hints throughout; `mypy` gate enforced in CI on `scripts/lib/`; degrade-gracefully fallbacks carry typed ignores | mypy scoped to `lib/` - orchestrator scripts (`assess_core.py`, `complexity-treemap.py`, `doc-graph-svg.py`) not yet type-gated |
| 3: Linters | Are complexity and style bounds enforced, or will my code drift? | write | Present | `ruff` with `C901` complexity gate (threshold 15) enforced in CI; per-function ccn p95 9 sits under the threshold; offenders carry explicit `# noqa` ratchet notes | - |
| 4: Architecture Tests | Are the structural conventions executable, or just folklore? | write | Partial | `plugin contract pytest` enforces the plugin structural contract (required check on `main`): every skill has a valid `SKILL.md`, every marketplace entry exists, internal links resolve; C901 ratchet fences complexity | No import-boundary / file-size architecture enforcement on the repo's own code (grimp scans *targets*, not self) |
| 5: CI Pipeline | Does something automatically catch a bad change before it merges? | write | Present | `tests.yml` (3 pytest jobs + lint), `pr-lint.yml`, `claude-review.yml`, `build-standalone-skills.yml`; branch protection on `main` with `enforce_admins: true`; `skills/assess pytest`, `scripts/ pytest`, `plugin contract pytest`, `Validate PR title` are required, blocking | `ruff + mypy gates` job runs but is **not** a required status check - lint can fail without blocking merge; no coverage step |
| 6: Coverage Gates | Do the tests constrain behaviour, or just execute lines? | write | Missing | 26 test files run in CI, but no coverage config, no threshold, no patch-coverage gate, no mutation testing | No behaviour-constraint enforcement - coverage could regress silently |
| 7: Code Review Bots | Is there design-level feedback on every change? | write | Present | `claude-review.yml` runs automated Claude PR review with repo-specific guidelines in `.github/claude-review-instructions.md` (graded A); CodeRabbit also reviews PRs | - |
| 8: AI Project Mgmt (capstone) | Do learnings feed back into the contracts, or evaporate? | meta | Partial | Task Master (`.taskmaster/`) drives multi-task marathons; dated plan files under `docs/superpowers/plans/` capture intent; issue-driven contract evolution traced in code comments (#58, #59, #62) - learnings feed back into contracts | No dedicated in-repo retro/learnings log (retro path is per-machine under `~/.claude/`); Task Master state isn't committed |

### Score derivation (worked)

Present = 1, Partial = 0.5, Missing = 0:

`L0 0.5 + L1 0.5 + L2 1 + L3 1 + L4 0.5 + L5 1 + L6 0 + L7 1 + L8 0.5 = 6.0` raw → `min(6.0, 8)` = **6.0 / 8**.

### Maturity Level

| Score | Level | Description |
|-------|-------|-------------|
| 0-2 | Not Ready | Agent will produce inconsistent, unvalidated code |
| 3-4 | Basic | Norms exist but aren't enforced. Agent works but drifts |
| 5-6 | **Solid** | Contracts catch most issues. Agent is productive |
| 7-8 | AI-Native | System self-improves. Agents work reliably at scale |

This repo sits at the top of **Solid**. The write-side enforcement (types, linting, CI, review bots) is genuinely strong - the thing holding it back from AI-Native is a coverage/behaviour-constraint gate (L6) and two read-side gaps that are mostly benign for a plugin repo (L1 has no runtime to instrument; L0's doc-link fragmentation is curation, not inaccessibility).

</details>

<details>
<summary>🔎 Cross-layer findings & lying signals (keyhole detail)</summary>

### Lying Signals

The most dangerous failure mode is an artefact that looks true but isn't. This run produced **none** - a clean result, and a useful contrast with the 2026-05-31 baseline where the tool emitted two false positives about itself (since fixed):

- **L0 stale hub doc:** no entry clears the `ratio > 2.0` AND `confidence != "low"` bar - every stale-hub candidate is `repo-baseline`/`confidence: low` (whole-repo churn proxy) and edited within 3 days.
- **L1 dead-but-present:** the intra-repo dead-code scan didn't run (`vulture` absent), so there is no candidate to surface - degrade, not a lie.
- **L6 green-but-hollow:** mutation data wasn't collected (opt-in), so survivor density is unknown - which is itself the L6 gap, not a lie.

## Cross-Layer Findings (Keyhole Readiness)

These are the axis-crossing signals no single layer surfaces - where the static structure and the git history disagree. The dominant signal here is benign-by-context: the assessment engine's scripts and their tests change together because they *are* one cohesive, actively-developed subsystem.

### hidden_coupling

Action: investigate the seam

Paths:
- scripts
- scripts/tests
- skills/assess/scripts
- skills/assess/scripts/lib
- skills/assess/tests

### refactor_boundary

Action: safe to hand an agent in isolation

Paths:
- commands

### Attention List (Priority Order)

- scripts (score 1): hidden_coupling
- scripts/tests (score 1): hidden_coupling
- skills/assess/scripts (score 1): hidden_coupling
- skills/assess/scripts/lib (score 1): hidden_coupling
- skills/assess/tests (score 1): hidden_coupling

**Reading these:** every concern is `hidden_coupling` within the `/assess` engine - `scripts/lib` modules and their `tests/` move in the same commits. For a tightly-cohesive subsystem under active development this is *expected*, not a defect. The one **safe zone** is `commands/` (containment 0.92) - edits there stay local, so it is the directory you can hand an agent in isolation with the least risk.

</details>

<details>
<summary>✅ Strengths & further opportunities</summary>

### Strengths

- **Real write-side enforcement, not theatre.** `ruff` C901 complexity ratchet (threshold 15, with documented `# noqa` exceptions) and a `mypy` type gate both run in CI - and the per-function complexity actually sits under the bar (p95 9 across 777 functions).
- **Branch protection with teeth:** `enforce_admins: true` and 3 blocking pytest jobs + PR-title lint on `main`. CI failure means a real regression.
- **Dogfooded AI review:** `claude-review.yml` runs automated review against repo-specific guidelines (`.github/claude-review-instructions.md`, graded A), and CodeRabbit reviews PRs - the project reviews its own PRs with the kind of tooling it advocates.
- **Issue-driven contract evolution:** code comments trace decisions back to issues (#58, #59, #62), and dated plan files under `docs/superpowers/plans/` capture intent - the feedback loop the L8 capstone looks for is visibly working.
- **Excellent agent instructions:** `CLAUDE.md` (A, 184 lean lines) plus 6 factored skills for progressive disclosure - lean pointers over a monolith.
- **A safe refactor zone:** `derived_findings.refactor_boundary` flags `commands/` as high-containment - edits stay local, so it is safe to hand an agent in isolation.

### Additional Opportunities

- **L5:** add the `ruff + mypy gates` job to required status checks on `main` - it runs today but a lint/type failure won't block merge.
- **L1:** `uv tool install vulture` to enable the Python intra-repo dead-code scan - it degrades silently today, so a dead export in the scanners wouldn't be flagged.
- **Hidden coupling (keyhole finding):** `derived_findings.hidden_coupling` flags `skills/assess/scripts`, `skills/assess/scripts/lib`, `skills/assess/tests`, `scripts`, and `scripts/tests` as changing together historically despite being separate directories - expected for a tool whose scanners, their tests, and the standalone-build scripts evolve in lockstep, but worth watching as a seam before trusting those boundaries.

</details>

<details>
<summary>🧭 How to read this report (framing & method)</summary>

**Meta-assessment.** This is `/assess` run against the repository that *owns* `/assess`, captured as the Phase-0 dogfood baseline for the `assess-dogfooded` work (teeth + frozen harness + decomposition). Two false positives the tool previously produced about itself - a self-referential Layer-1 rung-3 and a `lying_map` flag on a same-day-edited doc - are now fixed: this run scores observability at rung 0 (honest: no deployed runtime) and emits an empty `lying_map`. The snapshot is preserved as the regression baseline for the decomposition parity tests.

This is an improvement roadmap, not a verdict. It measures one thing: **is the codebase kept honest, not just scaffolded.** It pairs three views:

- **Where the codebase is today** - the complexity heatmap shows current complexity and churn. Vivid red = complex AND actively changing = the files most likely to bite an agent (or a human) next week.
- **Whether an agent can navigate it** - the doc graph shows the docs' link structure: how much is reachable from the entry point, and which docs are stale maps of churning code.
- **What keeps it from getting worse** - the AI Readiness score (0-8) across three bands: read-side foundation, write-side enforcement, and meta.

A codebase can be 8/8 and still on fire, or 2/8 with a calm treemap. The views matter together.

**How it's measured.** This is an AI-readiness review run almost entirely on *traditional* tooling - static analysis, git history, and graph metrics over the docs and code. The model only writes the prose around those numbers; it does no scanning itself. That keeps a full run fast and close to zero in model tokens, and makes the structural findings reproducible run-to-run.

</details>

<details>
<summary>🤖 Machine-readable data (for agents)</summary>

If you are an agent working in this repo, the `.assess/` directory is actionable feedback written for you - the first place to look when deciding where to point effort. It is a compounding, AI-readable record an agent can ingest directly, without re-parsing this prose:

- `.assess/assess-report.md` - this report: the scorecard, the lying signals, and the Top 3 Actions with exact commands and file paths.
- `.assess/run-context.json` - the full data bus (findings, attention, keyhole summary, prescribed actions, stats, diff).
- `.assess/complexity-stats.json` - complexity percentiles plus the ranked file lists (`top_hotspots`, `top_complex`, `top_large`).
- `.assess/hotspots/<file>.md` - per-file briefings, each with a **Suggested actions** section. Read a file's briefing before you change it.
- `.assess/index.md` - the catalog of every hotspot ever flagged (current and graduated).
- `.assess/log.md` - append-only run history, so a hotspot's trajectory (regressing / persistent / improving) is visible across runs.

</details>

---

_Report generated by [`/ai-native-toolkit:assess`](https://github.com/bjcoombs/ai-native-toolkit). Install in any Claude Code session: `/plugin marketplace add https://github.com/bjcoombs/ai-native-toolkit` then `/plugin install ai-native-toolkit@ai-native-toolkit`._
