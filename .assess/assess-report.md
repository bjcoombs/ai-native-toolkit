# Codebase Assessment: ai-native-toolkit

_Generated 2026-10-11 by `/assess` 1.96.0 (`main` ef225e2 with #516-#522, plus this branch's version bump), measuring `0e884f26fdb7`: `main` plus the docs-index link fix and the version bump in this assessment's branch._

**Score: 7.5 / 8 - AI-Native** - a readiness snapshot, not a verdict · Keyhole: 24 structural concerns (12 hidden coupling, 1 untrusted hotspot, 2 self-referential tests, 9 accretion ratchet), 3 safe zones.

This loop taught `/assess` to grade instruction files on commands that resolve and on size (#517), to score the agent's working environment inside Layer 5 (#520), and to list what committed agent configuration runs (#516) and how nested instruction files load (#518). Both new rules would have marked this repo down a week ago: before #521 there was no setup target and no lockfile, so Layer 5 capped at Partial, and before #522 `CLAUDE.md` was 5,202 words and graded B/59 under the size cap. Both are fixed, so the score holds at 7.5; the last half point is still Layer 6, `scripts/floor_anchor.py`, which waits on the maintainer's floor decision in #410.

> **Agents start here.** The prioritized Top 3 actions below are also machine-readable in `.assess/actions.json` (schema v2: every entry carries `rank`, `action`, `done_when`, and `scope_fence`, plus the lifecycle fields `status` / `claimed_by` / `completed_sha` and a derived execution `mode`). Read that file to pick up the work - even with a smaller model - without parsing this report's prose.

## Top 3 Actions

All three are the prescribed attention rows, unchanged from #510: the two floor scripts with same-commit tests, then the test file that only ever grows. Action 1 is the only path to Layer 6 Present.

| # | Action | Layer | Effort | Command / First Step | Done when | Scope fence | Hotspot files this addresses | Issue |
|---|--------|-------|--------|---------------------|-----------|-------------|------------------------------|-------|
| 1 | Strengthen `scripts/floor_anchor.py`'s tests to pin observable behaviour, written from FLOOR.md's clause text, once the #410 decision on clause iii is made | 6 | medium | Read the review comment on #410; decide whether `floor_core_changed` widens to all seven clause-iii paths or the clause narrows to four; then write one test per clause | `floor_anchor.py`'s survivor density in the opt-in mutation pass falls below 0.3 (437 of 1,017 today), and a change under `scripts/contract/` requests `floor-signoff` or clause iii no longer lists it | Floor files: any change needs the maintainer's out-of-band `floor-signoff` approval; no agent self-applies it | `scripts/floor_anchor.py` | #410 |
| 2 | Add the clause-integrity and token-floor tests for `floor_check.py` from the #410 review (tests 2-4), written independently of `REQUIRED_CLAUSES` and `MINIMUM_TOKEN_COUNT` | 4 | medium | Write each test from FLOOR.md's sentence, not from the constants | Deleting a `REQUIRED_CLAUSES` entry or lowering `MINIMUM_TOKEN_COUNT` turns a test red | Same floor sign-off rule as Action 1; tests only, no change to the checker's behaviour | `scripts/floor_check.py` | #410 |
| 3 | Split `skills/assess/tests/test_complexity_treemap.py` (`accretion_ratchet`; 1,798 lines, held at its `.file-size-ratchet.toml` ceiling) by concern, moving tests unchanged | 3 | medium | Group its tests by the treemap stage they exercise (scoring backends, stats sidecar, layout, render) into `test_complexity_treemap_<stage>.py` files, as #478 split the keyhole tests | Every new file is under 800 lines, the file's `.file-size-ratchet.toml` ceiling is deleted, and the collected test names and the treemap's line coverage are identical before and after | Move tests only; no assertion or fixture logic changes | `skills/assess/tests/test_complexity_treemap.py` | - |

The top rows score 3 and 2, so the ranking separates them. No gap action is pending.

### Why these three?

The floor is the repo's constitutional gate, and its anchor checker is the one file the mutation pass proves hollow. Both floor actions wait on the maintainer, and Action 1 is the one that moves Layer 6. Action 3 retires the largest remaining test-file ceiling; the ratchet follows a file down, so lines a split removes can come back only through a ceiling raise someone reviews.

## Snapshots

### Complexity - riskiest to change

[![Complexity hotspot](./complexity-heatmap.svg)](./complexity-heatmap.svg)

The risk sits in `skills/assess/`. No top-five hotspot has a function above ccn 15, though the loop's new modules add several functions lizard scores at 17-20 that ruff's mccabe passes (Layer 3 below). `floor_anchor.py` carries the survivor hatching.

### Doc navigability - can an agent find its way?

[![Doc map](./doc-graph.svg)](./doc-graph.svg)

Every doc is reachable from an entry point, with no orphans, one island, no broken links and no lying maps, after this branch linked the one orphan the first pass found.

<details>
<summary>📈 Snapshot detail (commit, hotspots, navigability, lying maps)</summary>

#### Complexity profile

- **Measured at commit:** `0e884f26fdb7` (2026-10-11)
- **Files scored:** 244
- **Churn window chosen:** last 12mo
- **Complexity profile:** per-function ccn p95 9 (max 33, `authorship_analysis` in `lib/change_coupling.py`, under an explicit `# noqa: C901` with a reason); file-aggregate ccn p95 152 (max 264, `lib/command_resolver.py`, new in #517, worst function 15); p95 est. tokens 9,313 (max 19,417; code 19,417, data 1,572); p95 NLOC 545 (max 1,227; code 1,227, data 78)
- **Top hotspots** (composite `sqrt(effective_ccn) × sqrt(1 + commits) × sqrt(est_tokens)`, with `effective_ccn` the file aggregate weighted toward its worst function; a co-change breadth term was tested and left out, see `skills/assess/references/hotspot-cochange-experiment.md`). Sizes are NLOC (code lines); `ccn` is the **file aggregate**, with the worst single function in parentheses:
  1. `skills/assess/scripts/complexity-treemap.py` - 17,610 est. tokens (847 NLOC), aggregate ccn 214 (worst function `lizard_scores` 14), 34 commits in window
  2. `skills/assess/scripts/lib/keyhole_signals.py` - 17,957 est. tokens (860 NLOC), aggregate ccn 247 (worst function `render_findings_markdown` 11), 26 commits in window
  3. `skills/assess/scripts/assess_core.py` - 9,238 est. tokens (397 NLOC), aggregate ccn 26 (worst function `build_run_context` 13), 70 commits in window
  4. `skills/assess/scripts/lib/doc_graph.py` - 12,110 est. tokens (705 NLOC), aggregate ccn 209 (worst function `_derive_signals` 15), 23 commits in window
  5. `skills/assess/tests/test_complexity_treemap.py` - 19,417 est. tokens (1,227 NLOC), aggregate ccn 189 (worst function `test_write_stats_tie_break_by_path_takes_first_ten_in_path_order` 6), 26 commits in window
- **Per-function coverage:** no per-function data for Makefile, TOML (scored at file level only).
- **Keyhole budget:** the repo is ~889k est. tokens. No single file exceeds the ~200k keyhole budget; 1 subtree does: `skills` (~730k).

Size encodes estimated tokens, colour cyclomatic complexity, saturation recent churn.

**Hatching:** the opt-in mutation pass ran two complete groups through the `uv` runner. `scripts/floor_anchor.py` (437 of 1,017 survive, density 0.43) is hatched; `skills/assess/scripts/lib/doc_graph.py` (305 of 1,315, density 0.23) is not. Both figures equal #510's: neither file changed this loop. The pass mutates only focus files, so `keyhole_signals.py` has no figure here; the weekly CI run covers it.

#### Where to focus testing

Coverage data: `coverage.xml` (Cobertura)

Coverage gate: `scripts/pyproject.toml:46` (`[tool.coverage.report] fail_under` 91); `skills/assess/pyproject.toml:49` (`[tool.coverage.report] fail_under` 93)

| File | Risk | Test Signal | Suggested Action |
|------|------|-------------|------------------|
| `skills/assess/scripts/lib/doc_graph.py` | Medium | Covered but hollow | Strengthen assertions |
| `skills/assess/tests/test_complexity_treemap.py` | Medium | Test file present, coverage unmeasured | Measure coverage |
| `skills/assess/tests/test_assess_core.py` | Medium | Test file present, coverage unmeasured | Measure coverage |
| `scripts/floor_anchor.py` | Low | Covered but hollow | Strengthen assertions |

The test files show as unmeasured because coverage measures source, not the tests themselves. The mutation pass confirms `floor_anchor.py` as hollow (the `untrusted_hotspot` finding) and puts `doc_graph.py` under the hollow threshold. The `coverage.xml` comes from the repo-root recipe in `docs/contributing/ci.md`; `make check` writes per-package reports that `/assess` does not read.

#### Doc navigability

Of 118 docs, all are reachable from `CLAUDE.md`, `README.md` or `docs/index.md` by links and backticked doc paths, with no orphans, one connected island and no broken links. Links alone reach 93%; backticked citations carry the rest. The first pass of this run found one orphan, `skills/assess/references/hotspot-cochange-experiment.md` from #519: the report template cites it as `references/hotspot-cochange-experiment.md`, a path that does not resolve from `skills/assess-findings/`. This branch links it from `docs/index.md`.

**Base docs per code directory:** 2 of 13 code-containing directories hold their own base doc (`skills/assess/scripts/lib/`, `skills/assess/tests/`), a ratio of 0.154, unchanged from #510. `CLAUDE.md` covers the rest in prose, so an agent still reaches them through one hub.

**Lying maps:** none. The top stale hubs (`FLOOR.md`, `docs/floor-anchor-proof.md`) are matched only against whole-repo churn (low confidence).

#### What changed since last run

- **Graduated, regressed, restructured, new:** none.
- **Persistent:** the same ten top hotspots as #510.

</details>

<details>
<summary>📊 Full scorecard (per-layer evidence & gaps)</summary>

The two headline metrics measure different things and are never combined: the 0-8 score asks whether the scaffolding catches problems; the Keyhole summary counts today's structural pain.

| Layer | What it asks | Band | Status | Evidence | Gap |
|-------|--------------|------|--------|----------|-----|
| 0: Agent Instructions & Navigability | Can I build a true map of this codebase before I touch it? | read | Present | `CLAUDE.md` grades A/85 under the #517 rules: 157 lines, 1,760 words, 8 verified commands including `make check`, 0 stale references, no size penalty; base docs at `skills/assess/scripts/lib/README.md` and `skills/assess/tests/README.md`; 100% reachable, 0 orphans, 0 broken links; `claude_config` 0 findings over 27 files; `nested_instructions` 0 findings, start-up budget ~3,310 tokens | No `scripts/README.md`: 11 of 13 code dirs rely on `CLAUDE.md` alone (ratio 0.154). `.github/claude-review-instructions.md` grades B/58 (bloat penalty 3) |
| 1: Runtime Legibility / Liveness | Can I see which parts are live, which need attention, and which are dead weight? | read | Missing | No `OBSERVABILITY.md`, no `.mcp.json`. There is no deployed runtime, so liveness is read through complexity, churn and reachability; vulture found 0 dead-code candidates | No runtime by design; the JS canaries are unscanned (knip not installed) |
| 2: Code Design | Will the type-checker catch my mistakes? | write | Present | `disallow_any_generics = true` in `skills/assess/pyproject.toml` with strict mode and no per-module exceptions; `mypy` in `.github/workflows/tests.yml` | `scripts/` keeps three floor-protected mypy overrides |
| 3: Linters | Are complexity and style bounds enforced, or will my code drift? | write | Present | ruff `max-complexity = 15` in `skills/assess/pyproject.toml`, run by `ruff check` in `.github/workflows/tests.yml`; `.file-size-ratchet.toml` (`default_limit = 800`) enforced by `tests/test_file_size_ratchet.py`; 0 stale of 81 suppressions | Lizard scores functions above 15 that ruff's mccabe passes: `floor_check.cmd_markers` 22, and in this loop's new modules `agent_environment.check_copilot_setup` 20, `agent_harness.frontmatter_hooks` 19 |
| 4: Architecture Tests | Are the structural conventions executable, or just folklore? | write | Present | `skills/assess/tests/test_self_architecture.py`; `tests/test_plugin_contract.py`; the file-size ratchet test | One import rule |
| 5: CI Pipeline | Does something automatically catch a bad change before it merges? | write | Present | `.github/workflows/tests.yml` (`uv run --locked pytest`) under seven required checks; the #520 cap does not apply: `CLAUDE.md:23` names `make check`, setup is the `Makefile`'s `make setup`, and both manifests are locked (`skills/assess/uv.lock`, `scripts/uv.lock`); slowest CI median 100s against a 600s target | CodeQL and pip-audit are advisory |
| 6: Coverage Gates | Do the tests constrain behaviour, or just execute lines? | write | Partial | `fail_under = 93` in `skills/assess/pyproject.toml`, `fail_under = 91` in `scripts/pyproject.toml`; `mutmut` weekly in `.github/workflows/mutation.yml`; the opt-in pass ran two complete groups, so no cap | `scripts/floor_anchor.py`: 437 of 1,017 survive (density 0.43); overall 742 of 2,332 |
| 7: Code Review Bots | Is there design-level feedback on every change? | write | Present | `.github/workflows/claude-review.yml` with `.github/claude-review-instructions.md`; 93% of 30 merged PRs reviewed by someone other than the author, 63% approved | Review is advisory (solo-maintainer repo) |
| 8: AI Project Mgmt (capstone) | Do learnings feed back into the contracts, or evaporate? | meta | Present | `CLAUDE.md` `## Marathon Configuration` with the `marathon-retros.md` retro log; `skills/marathon/SKILL.md` | Retro log is per-machine; no `.taskmaster` in the repo; agent_ops all false (neutral) |

### Score derivation (worked)

L0 1 + L1 0 + L2 1 + L3 1 + L4 1 + L5 1 + L6 0.5 + L7 1 + L8 1 = **7.5**, under the cap of 8: 0.94 of 8, AI-Native. Every verdict equals #510's.

**Against the new method.** Run on `6157131` (#520 merged, before #521), this version caps Layer 5 at Partial (`setup_or_lockfile` unmet), which would read 7.0. Run on `a714747` (before #522), `CLAUDE.md` grades B/59 (5,202 words, size-capped, bloat penalty 12), a passing grade that leaves Layer 0 Present. The repo, not the method, holds the score.

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
| 6 | Green-but-hollow | `scripts/floor_anchor.py` (coverage 87% vs mutation 57%) | Tests execute 87% of the floor anchor's lines, but 437 of 1,017 mutants survive. The coverage gate reads "tested" while behaviour is unpinned. Across both mutated files the score is 68% (742 of 2,332 survived) |

### Agent harness

No committed hooks, env, apiKeyHelper or MCP servers. No findings: no tracked `.env` or `settings.local.json`, no hidden Unicode in instruction files.

### Agent environment

- **Setup:** `Makefile` (`make setup`)
- **Check:** `CLAUDE.md:23` `make check`
- **Pinning:** every manifest locked (`scripts/uv.lock`, `skills/assess/uv.lock`)
- **CI duration:** slowest median 1.7 minutes on `main` over 50 runs, against the 10-minute guidance

## Cross-Layer Findings (Keyhole Readiness)

`untrusted_hotspot` names `scripts/floor_anchor.py` on the mutation pass's own evidence (437 of 1,017 mutants survive). There is still no `lying_map` and no `unactioned_intent`, and the findings match #510 path for path.

### hidden_coupling

Action: investigate the seam

Paths:
- scripts
- scripts/contract
- scripts/tests
- skills/assess
- skills/assess/scripts
- skills/assess/scripts/lib
- skills/assess/scripts/lib/test_pressure
- skills/assess/tests
- skills/assess/tests/fixtures
- skills/skill-forge
_... 2 more omitted; full list in `.assess/run-context.json` `derived_findings` (`hidden_coupling`) `paths`_

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
- skills/assess/tests/test_promissory_markers.py

### refactor_boundary

Action: safe to hand an agent in isolation

Paths:
- docs/design
- docs/design/2026-09-modernization
- skills/tm

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

- **The repo passes its own new rules.** A Makefile with `make setup` and `make check`, committed `uv.lock` files checked in CI, and a `CLAUDE.md` cut to 1,760 words that grades A (#521, #522).
- **`/assess` sees the agent's environment.** `agent_environment` (#520), `agent_harness` (#516) and `nested_instructions` (#518) read what an agent loads and runs, and each reports `available: false` rather than a clean zero when it cannot check.
- **Instruction grades rest on checks.** #517 counts commands that resolve and penalises size, so a long file can no longer earn an A by directive counts.
- **A finished type gate and a two-way size ratchet,** carried over.

### Additional Opportunities

- **Layer 0 margin:** add base docs for `scripts/` and `skills/assess/scripts/` first, so the map does not rest on `CLAUDE.md` alone; extend the plugin contract link check to `docs/`.
- **Layer 3:** the new `agent_environment.py` and `agent_harness.py` functions at lizard ccn 17-20 pass ruff's mccabe at 15; watch them rather than refactor.
- **Layer 6 to Present:** Action 1 after #410; then the `doc_graph.py` and `keyhole_signals.py` survivor clusters.
- **Coverage for `/assess`:** `make check` writes `coverage.xml` per package, which `/assess` does not read; a `make coverage` target running the root recipe would remove a manual step.
- **Floor follow-up (sign-off needed):** `scripts/floor_check.py` still accepts the old `commands/<x>.md` component paths, as `scripts/tests/test_floor_check.py` asserts.
- **Open issues:** coverage-gate detector refinements (#488); whether to detect contradicting docs (#491).

</details>

<details>
<summary>🧭 How to read this report (framing & method)</summary>

**What this is ultimately measuring.** Whether an AI contributor here behaves like a brand-new hire or like an engineer with eighteen months of context. The difference is *externalized context*. **Legibility you can trust, not omniscience you can't.** The write-side layers are guardrails that protect any contributor from costly mistakes by design; a **Missing** there means the codebase asks the contributor to be careful where it could make the wrong move hard to make.

This is an improvement roadmap, not a verdict: **is the codebase kept honest, not just scaffolded.** Three views: the complexity heatmap, the doc graph, and the 0-8 score.

**Layer 1 on a repo with no runtime.** The complexity heatmap and the dead-code scan are its observability; a Missing means look through those instruments.

**The legacy-transition lens.** Characterization tests before refactoring (Feathers), Strangler Fig over rewrites (Fowler), hotspots and change-coupling as the worklist (Tornhill).

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
