# Codebase Assessment: ai-native-toolkit

_Generated 2026-10-08 by `/assess` at `main` 34ee318 (plugin 1.94.0 plus #492-#498)._

**Score: 7.5 / 8 - AI-Native** - a readiness snapshot, not a verdict · Keyhole: 25 structural concerns (13 hidden coupling, 1 untrusted hotspot, 2 self-referential tests, 9 accretion ratchet), 3 safe zones.

The `/assess` type gate now covers all of `skills/assess`: mypy runs `strict` with `disallow_any_generics = true` and no per-module exceptions (#493, #495, #497), so Layer 2 moves to Present and the score rises from 7.0 to 7.5. The signals `/assess` reports about itself are cleaner too: the stale-marker count fell from 11 to 0 because the scan now ignores marker text inside string literals (#494); `keyhole_signals.py` is credited with the eight test files that import it (#496); and the three CodeQL alerts were traced to `ccn` matching CodeQL's credit-card-number heuristic and dismissed with the trace on #487.

The last half point is Layer 6. `scripts/floor_anchor.py` keeps 87% line coverage while 43% of its mutants survive, and pinning it needs the maintainer's floor decision in #410.

> **Agents start here.** The prioritized Top 3 actions below are also machine-readable in `.assess/actions.json` (schema v2: every entry carries `rank`, `action`, `done_when`, and `scope_fence`, plus the lifecycle fields `status` / `claimed_by` / `completed_sha` and a derived execution `mode`). Read that file to pick up the work - even with a smaller model - without parsing this report's prose.

## Top 3 Actions

All three are the prescribed attention rows, unchanged from #483: the two floor scripts with same-commit tests, then the test file that only ever grows. Action 1 is the only path to Layer 6 Present.

| # | Action | Layer | Effort | Command / First Step | Done when | Scope fence | Hotspot files this addresses | Issue |
|---|--------|-------|--------|---------------------|-----------|-------------|------------------------------|-------|
| 1 | Strengthen `scripts/floor_anchor.py`'s tests to pin observable behaviour, written from FLOOR.md's clause text, once the #410 decision on clause iii is made | 6 | medium | Read the review comment on #410; decide whether `floor_core_changed` widens to all seven clause-iii paths or the clause narrows to four; then write one test per clause | `floor_anchor.py`'s survivor density in the opt-in mutation pass falls below 0.3 (435 of 1,017 today), and a change under `scripts/contract/` requests `floor-signoff` or clause iii no longer lists it | Floor files: any change needs the maintainer's out-of-band `floor-signoff` approval; no agent self-applies it | `scripts/floor_anchor.py` | #410 |
| 2 | Add the clause-integrity and token-floor tests for `floor_check.py` from the #410 review (tests 2-4), written independently of `REQUIRED_CLAUSES` and `MINIMUM_TOKEN_COUNT` | 4 | medium | Write each test from FLOOR.md's sentence, not from the constants | Deleting a `REQUIRED_CLAUSES` entry or lowering `MINIMUM_TOKEN_COUNT` turns a test red | Same floor sign-off rule as Action 1; tests only, no change to the checker's behaviour | `scripts/floor_check.py` | #410 |
| 3 | Split `skills/assess/tests/test_complexity_treemap.py` (`accretion_ratchet`; 1,798 lines, held at its `.file-size-ratchet.toml` ceiling) by concern, moving tests unchanged | 3 | medium | Group its tests by the treemap stage they exercise (scoring backends, stats sidecar, layout, render) into `test_complexity_treemap_<stage>.py` files, as #478 split the keyhole tests | Every new file is under 800 lines, the file's `.file-size-ratchet.toml` ceiling is deleted, and the collected test names and the treemap's line coverage are identical before and after | Move tests only; no assertion or fixture logic changes | `skills/assess/tests/test_complexity_treemap.py` | - |

The top rows score 3 and 2, so the ranking separates them. No gap action is pending.

### Why these three?

The floor is the repo's constitutional gate, and its anchor checker is the one file the mutation pass proves hollow. Both floor actions wait on the maintainer, and Action 1 is the one that moves Layer 6. Action 3 retires the largest remaining test-file ceiling, the accretion the ratchet exists to prompt; since #498 the ratchet also follows a file down, so lines a split removes can come back only through a ceiling raise someone reviews.

## Snapshots

### Complexity - riskiest to change

[![Complexity hotspot](./complexity-heatmap.svg)](./complexity-heatmap.svg)

The risk sits in `skills/assess/`, and no function in the top hotspots exceeds ccn 15. `floor_anchor.py` carries the survivor hatching.

### Doc navigability - can an agent find its way?

[![Doc map](./doc-graph.svg)](./doc-graph.svg)

Every doc is reachable from an entry point, with no orphans, one island and no lying maps.

<details>
<summary>📈 Snapshot detail (commit, hotspots, navigability, lying maps)</summary>

#### Complexity profile

- **Measured at commit:** `338a2a3` (2026-10-08): `main` 34ee318 plus the README tree and `CLAUDE.md` ratchet-note corrections in 338a2a3
- **Files scored:** 219
- **Churn window chosen:** last 12mo
- **Complexity profile:** per-function ccn p95 8 (max 33, `authorship_analysis` in `lib/change_coupling.py`, under an explicit `# noqa: C901` with a reason; ruff's mccabe scores it 19, lizard counts boolean operators too); file-aggregate ccn p95 147 (max 247); p95 est. tokens 9,756 (max 19,417); p95 NLOC 580 (max 1,227)
- **Top hotspots** (composite `sqrt(ccn) × sqrt(1 + commits) × sqrt(est_tokens)`). Sizes are NLOC (code lines); `ccn` is the **file aggregate**, with the worst single function in parentheses:
  1. `skills/assess/scripts/complexity-treemap.py` - 17,610 est. tokens (847 NLOC), aggregate ccn 214 (worst function `lizard_scores` 14), 33 commits in window
  2. `skills/assess/scripts/lib/keyhole_signals.py` - 17,957 est. tokens (860 NLOC), aggregate ccn 247 (worst function `render_findings_markdown` 11), 26 commits in window
  3. `skills/assess/scripts/assess_core.py` - 9,221 est. tokens (394 NLOC), aggregate ccn 26 (worst function `build_run_context` 13), 68 commits in window
  4. `skills/assess/scripts/lib/doc_graph.py` - 12,110 est. tokens (705 NLOC), aggregate ccn 209 (worst function `_derive_signals` 15), 23 commits in window
  5. `skills/assess/tests/test_complexity_treemap.py` - 19,417 est. tokens (1,227 NLOC), aggregate ccn 189 (worst function `test_write_stats_tie_break_by_path_takes_first_ten_in_path_order` 6), 25 commits in window
- **Keyhole budget:** the repo is ~806k est. tokens. No single file exceeds the ~200k keyhole budget; 1 subtree does: `skills` (~645k).

Size encodes estimated tokens, colour cyclomatic complexity, saturation recent churn.

**Hatching:** the opt-in mutation pass ran, and both groups produced records through the `uv` runner. A diagonal hatch marks more than 30% survivor density and a cross-hatch more than 50%. `scripts/floor_anchor.py` (435 of 1,017 survive, density 0.43) is hatched. `skills/assess/scripts/lib/doc_graph.py` (304 of 1,315, density 0.23) is not. The pass mutates only focus files, so `keyhole_signals.py` and `mutmut3.py` have no figure here; the weekly CI run covers `keyhole_signals.py`.

#### Where to focus testing

Coverage data: `coverage.xml` (Cobertura)

Coverage gate: `scripts/pyproject.toml:41` (`[tool.coverage.report] fail_under` 91); `skills/assess/pyproject.toml:38` (`[tool.coverage.report] fail_under` 93)

| File | Risk | Test Signal | Suggested Action |
|------|------|-------------|------------------|
| `skills/assess/scripts/lib/doc_graph.py` | Medium | Covered but hollow | Strengthen assertions |
| `skills/assess/tests/test_complexity_treemap.py` | Medium | Test file present, coverage unmeasured | Measure coverage |
| `skills/assess/tests/test_assess_core.py` | Medium | Test file present, coverage unmeasured | Measure coverage |
| `scripts/floor_anchor.py` | Low | Covered but hollow | Strengthen assertions |

The test files show as unmeasured because coverage measures source, not the tests themselves. The mutation pass confirms `floor_anchor.py` as hollow (the `untrusted_hotspot` finding) and puts `doc_graph.py` under the hollow threshold.

#### Doc navigability

Of 113 docs, all are reachable from `CLAUDE.md`, `README.md` or `docs/index.md` by links and backticked doc paths, with no orphans and one connected island. Links alone reach 93%; backticked citations carry the rest.

**Lying maps:** none after 338a2a3. The first pass flagged `README.md`: its repository tree listed six of the eight workflows, missing `codeql` and `mutation` from loop 4, and omitted `.file-size-ratchet.toml`. Both are corrected in 338a2a3. The top stale hubs (`FLOOR.md`, `docs/floor-anchor-proof.md`) are matched only against whole-repo churn (low confidence).

#### What changed since last run

- **Graduated, regressed, restructured, new:** none.
- **Persistent:** `complexity-treemap.py`, `keyhole_signals.py`, `assess_core.py`, `doc_graph.py`, `test_complexity_treemap.py`, `wiki_writer.py`, `test_assess_core.py`, `assess_finalize.py`, `doc_staleness.py`, `scripts/floor_anchor.py`.

</details>

<details>
<summary>📊 Full scorecard (per-layer evidence & gaps)</summary>

The two headline metrics measure different things and are never combined: the 0-8 score asks whether the scaffolding catches problems; the Keyhole summary counts today's structural pain.

| Layer | What it asks | Band | Status | Evidence | Gap |
|-------|--------------|------|--------|----------|-----|
| 0: Agent Instructions & Navigability | Can I build a true map of this codebase before I touch it? | read | Present | `CLAUDE.md` (grade A; its `## North star` frames every rule); 100% reachable, 0 orphans, 1 island, no lying maps; instruction claims 2 of 2 verified | 2 of 13 module dirs have no base doc |
| 1: Runtime Legibility / Liveness | Can I see which parts are live, which need attention, and which are dead weight? | read | Missing | No `.mcp.json`, no `OBSERVABILITY.md`. There is no deployed runtime, so liveness is read through complexity, churn and reachability; vulture found 0 dead-code candidates | No runtime by design; the JS canaries are unscanned (knip not installed) |
| 2: Code Design | Will the type-checker catch my mistakes? | write | Present | `strict = true` and `disallow_any_generics = true` globally in `skills/assess/pyproject.toml`, no per-module exceptions; shared shapes are `TypedDict`s in `lib/run_context_types.py` | `scripts/pyproject.toml` keeps `disallow_any_generics = false` overrides for floor-protected modules, and `floor_check.py` sits outside the gate; tightening them needs floor sign-off |
| 3: Linters | Are complexity and style bounds enforced, or will my code drift? | write | Present | ruff `C901` in `skills/assess/pyproject.toml` and `max-complexity = 15` in `scripts/pyproject.toml`, run by `ruff check` in `.github/workflows/tests.yml`; `.file-size-ratchet.toml` (`default_limit` 800, ceilings follow a file down since #498); 0 stale of 78 suppressions | `test_complexity_treemap.py` sits at its 1,798-line ceiling; `mutmut3.py` is 773 lines against the 800 default |
| 4: Architecture Tests | Are the structural conventions executable, or just folklore? | write | Present | `skills/assess/tests/test_self_architecture.py` (inward-only lib rule); `tests/test_file_size_ratchet.py` | One import rule |
| 5: CI Pipeline | Does something automatically catch a bad change before it merges? | write | Present | `.github/workflows/tests.yml` runs the `pytest` suites and `mypy` under seven required checks | CodeQL and pip-audit are advisory |
| 6: Coverage Gates | Do the tests constrain behaviour, or just execute lines? | write | Partial | `fail_under` in `skills/assess/pyproject.toml` (93) and `scripts/pyproject.toml` (91); `only_mutate` scope run weekly by `.github/workflows/mutation.yml` (`mutmut`); the opt-in pass ran with no cap | `scripts/floor_anchor.py`: 435 of 1,017 survive (density 0.43); the hottest code (`keyhole_signals.py`, `mutmut3.py`) has no figure from this pass |
| 7: Code Review Bots | Is there design-level feedback on every change? | write | Present | `.github/workflows/claude-review.yml` (no `.coderabbit.yaml`; CodeRabbit runs on defaults); 97% of 30 merged PRs reviewed by someone other than the author, 57% approved | Review is advisory (solo-maintainer repo) |
| 8: AI Project Mgmt (capstone) | Do learnings feed back into the contracts, or evaporate? | meta | Present | `CLAUDE.md` `## Marathon Configuration` with a Retro log step; `commands/tm.md` | Retro log is per-machine; no `.claude/settings.json` (neutral) |

### Score derivation (worked)

L0 1 + L1 0 + L2 1 + L3 1 + L4 1 + L5 1 + L6 0.5 + L7 1 + L8 1 = **7.5**, under the cap of 8: 0.94 of 8, AI-Native. Up from 7.0 in #483: Layer 2 moved from Partial to Present.

### Maturity Level

| Score | Level | Description |
|-------|-------|-------------|
| 0-2 | Not Ready | Agent will produce inconsistent, unvalidated code |
| 3-4 | Basic | Norms exist but aren't enforced. Agent works but drifts |
| 5-6 | Solid | Contracts catch most issues. Agent is productive |
| 7-8 | AI-Native | System self-improves. Agents work reliably at scale |

### What this score unlocks

The maturity band bounds how much agent **autonomy** this repo's contracts can safely absorb ([Steps of AI Adoption](https://www.threads.com/@boris_cherny/post/Da4CkQnEea-), B. Cherny, 2026).

| Band | Contracts support up to | Why |
|------|------------------------|-----|
| Not Ready | Step 1 (Assisted) - one supervised agent | No verification contracts; a human must review every change |
| Basic | Step 1-2 boundary | Parallel agents are risky until L5/L6 self-verification holds |
| Solid | Step 2 (Parallel) - ~10 agents, one orchestrator | Self-verification and review automation absorb parallel diffs |
| AI-Native | Step 3 (Supervised autonomy) - background routines | Contracts plus L8 orchestration can absorb autonomous loops |

**This is a bound, not a claim about how the team actually works.**

</details>

<details>
<summary>🔎 Cross-layer findings & lying signals (keyhole detail)</summary>

### Lying Signals

| Layer | Signal Type | Instance | Why it lies |
|-------|-------------|----------|-------------|
| 6 | Green-but-hollow | `scripts/floor_anchor.py` (coverage 87% vs mutation 57%) | Tests execute 87% of the floor anchor's lines, but 435 of 1,017 mutants survive. The coverage gate reads "tested" while behaviour is unpinned. Across both mutated files the score is 68% (739 of 2,332 survived) |

## Cross-Layer Findings (Keyhole Readiness)

`untrusted_hotspot` names `scripts/floor_anchor.py` on the mutation pass's own evidence (435 of 1,017 mutants survive). `unactioned_intent` and `lying_map` are gone: the marker scan now counts only comment markers (#494), and the one lying map this run first found, the README's repository tree omitting the `codeql` and `mutation` workflows, is fixed in 338a2a3. `lib/test_pressure/mutmut3.py` remains an `accretion_ratchet` path at 773 lines against the 800 default.

### hidden_coupling

Action: investigate the seam

Paths:
- scripts
- scripts/contract
- scripts/tests
- skills
- skills/assess
- skills/assess/scripts
- skills/assess/scripts/lib
- skills/assess/scripts/lib/test_pressure
- skills/assess/tests
- skills/assess/tests/fixtures
_... 3 more omitted; full list in `.assess/run-context.json` `derived_findings` (`hidden_coupling`) `paths`_

### untrusted_hotspot

Action: strengthen tests to pin observable behaviour (not internal state)

Paths:
- scripts/floor_anchor.py

### self_referential_tests

Action: request human review - tests verify internal consistency, not truth

Paths:
- scripts/floor_anchor.py
- scripts/floor_check.py

### accretion_ratchet

Action: refactor down: extract, delete dead code, or split the file

Paths:
- scripts/floor_anchor.py
- scripts/floor_check.py
- scripts/tests/test_floor_anchor.py
- scripts/tests/test_floor_check.py
- skills/assess/scripts/lib/test_pressure/mutmut3.py
- skills/assess/scripts/lib/wiki_writer.py
- skills/assess/tests/test_assess_finalize.py
- skills/assess/tests/test_complexity_treemap.py
- skills/assess/tests/test_doc_staleness.py

### refactor_boundary

Action: safe to hand an agent in isolation

Paths:
- commands
- docs/design
- docs/design/2026-09-modernization

### Attention List (Priority Order)

- scripts/floor_anchor.py (score 3): accretion_ratchet, self_referential_tests, untrusted_hotspot
- scripts/floor_check.py (score 2): accretion_ratchet, self_referential_tests
- skills/assess/tests/test_complexity_treemap.py (score 1): accretion_ratchet
- skills/assess/scripts/lib/wiki_writer.py (score 1): accretion_ratchet
- scripts/contract (score 1): hidden_coupling
_... 5 more omitted; top 10 ranked rows in `.assess/run-context.json` `attention`_

</details>

<details>
<summary>✅ Strengths & further opportunities</summary>

### Strengths

- **A complete `/assess` type gate.** Every `/assess` module is under `strict` plus `disallow_any_generics`, with run-context shapes as `TypedDict`s; the ratchet that got there (#475, #493, #495, #497) proved byte-identical output at each step.
- **Self-signals that hold up.** The marker scan reads tokens, not lines (#494), and its output is now deterministic: `rg`'s parallel hit order had reordered `stale_by_file` between runs. Test credit follows imports, so split test files still count (#496).
- **A ratchet in both directions.** File ceilings may not grow and now follow a file down (#498).
- **Security findings triaged, not ignored.** The three CodeQL alerts were traced end to end before dismissal (#487).
- **Mutation evidence end to end.** Both packages are mutated in their own environments, and the hollow-test finding fires without help.

### Additional Opportunities

- **Layer 6 to Present:** Action 1 after #410; then pin `doc_graph.py`'s survivor clusters (`_derive_signals`, `build_doc_graph`) and `keyhole_signals.py`'s (`_integrate_blocks`) from the weekly run, and give the opt-in pass a figure for `keyhole_signals.py` and `mutmut3.py`.
- **Layer 1:** install knip so the JS canaries (`scripts/canaries/*.mjs`) get a dead-code read.
- **Import credit:** the stdlib guard covers only one-part names, so `from email import utils` can credit a repo's `app/email/utils.py`; JS imports in trailing comments or strings still count (documented).
- **Marker scan:** in Python, judge the comment marker on a line like `Status.TODO:  # TODO: real` instead of dropping the line.
- **Complexity measures:** lizard scores several functions above 15 that ruff's mccabe passes (`authorship_analysis` 33, `config_drift._diff_lists` 27, `git_churn.content_commit_clock` 25); decide which measure the gate follows.
- **Open issues:** coverage-gate detector refinements (#488); whether to detect contradicting docs (#491).

</details>

<details>
<summary>🧭 How to read this report (framing & method)</summary>

**What this is ultimately measuring.** Whether an AI contributor here behaves like a brand-new hire or like an engineer with eighteen months of context. The difference is *externalized context*. **Legibility you can trust, not omniscience you can't.** The write-side layers are guardrails that protect any contributor from costly mistakes by design; a **Missing** there means the codebase asks the contributor to be careful where it could make the wrong move hard to make.

This is an improvement roadmap, not a verdict: **is the codebase kept honest, not just scaffolded.** Three views: the complexity heatmap, the doc graph, and the 0-8 score.

**Layer 1 on a repo with no runtime.** The complexity heatmap and the dead-code scan are its observability; a Missing means look through those instruments.

**The legacy-transition lens.** Characterization tests before refactoring (Feathers), Strangler Fig over rewrites (Fowler), hotspots and change-coupling as the worklist (Tornhill). The generics ratchet followed that order: one module set at a time, each proved byte-identical before the next.

**How it's measured.** Static analysis, git history and graph metrics; the model writes only the prose.

</details>

<details>
<summary>🤖 Machine-readable data (for agents)</summary>

- `.assess/assess-report.md` - this report.
- `.assess/run-context.json` - the full data bus.
- `.assess/complexity-stats.json` - percentiles, the keyhole-budget rollup, and the ranked file lists.
- `.assess/hotspots/<file>.md` - per-file briefings with **Suggested actions**.
- `.assess/index.md` - every hotspot ever flagged.
- `.assess/log.md` - append-only run history.

</details>

---

_Report generated by [`/ai-native-toolkit:assess`](https://github.com/bjcoombs/ai-native-toolkit). Install in any Claude Code session: `/plugin marketplace add https://github.com/bjcoombs/ai-native-toolkit` then `/plugin install ai-native-toolkit@ai-native-toolkit`._
