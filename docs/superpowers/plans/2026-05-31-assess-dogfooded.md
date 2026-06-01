# Assess — Dogfooded: teeth, a frozen harness, and decomposition — PRD

> **For agentic workers:** Implement on a worktree branched from `main` (e.g. `assess-dogfooded`). Committed home for this doc: `docs/superpowers/plans/2026-05-31-assess-dogfooded.md`. Steps use checkbox (`- [ ]`) syntax. This is a **multi-part** effort — each part below is independently shippable and should be its own PR with its own MINOR/PATCH bump in `.claude-plugin/plugin.json`. This is the plan only; no version bump in the planning PR (mirrors prior PRD precedent). `skills/assess/SKILL.md` is under the standalone-ZIP transform (`chat-skip` / `chat-replace` markers + `scripts/standalone_skill_config.py`); keep standalone-divergent wording in config, not body prose, and rebuild/validate the ZIP after any SKILL.md change.

**Type:** Capability hardening + refactor (three coordinated parts)
**Priority:** High — the keyhole-readiness signals shipped (v1.16.0) but are inert: they don't move the score, aren't deterministically surfaced, and only run when a human remembers to invoke `/assess`.
**Affected skill:** `skills/assess/` (`SKILL.md`, `scripts/assess_core.py`, `scripts/lib/`, new `scripts/assess_report.py` template renderer, new sub-skills or agents, `tests/`).

---

## The thesis: `/assess` violates its own contract three ways

`/assess` exists to catch three failure modes. It currently exhibits all three itself. This PRD is the tool dogfooded against its own rubric.

1. **It's a blob.** `SKILL.md` is **1233 lines** — a single monolithic procedure (Step 0 → Step 8+). By the tool's own keyhole-fit metric (comprehension footprint vs a keyhole budget), it is over budget: no agent can load and safely change one part without the whole. The tool flags exactly this in other repos.
2. **Its richest signals are computed-but-unused.** The keyhole core emits six derived findings (`hidden_coupling`, `lying_map`, `unexplained_complexity`, `orphaned_understanding`, `candidate_dead_weight`, `refactor_boundary`) + an `attention` list into `run-context.json`. None of them move the 0–8 score, and none are *deterministically* surfaced — they reach the report only if the LLM follows a prose instruction (`SKILL.md` line ~723). This is the tool's own "dead weight / lying map" anti-pattern: metadata that carries cost and delivers no enforced value.
3. **It's a norm, not a contract.** The README's core argument is that norms fail and contracts work because contracts are *enforced regardless of who's reading*. Yet `/assess` is a norm — a periodic manual run a human must remember. Nothing makes it run on every PR.

The three parts below fix each in turn, and they reinforce: decomposition (Part 3) makes the report renderer (Part 2) and the findings surfacing (Part 1) cleanly separable units instead of buried prose in a monolith.

---

## Part 1 — Give the findings teeth

The signals must influence outcomes deterministically, without depending on LLM cooperation.

### 1a. Deterministic surfacing (not LLM-dependent)

Today the LLM is *told* to lead with the attention list. Instead, the deterministic core writes a **rendered findings section** into the report skeleton (`assess_core.py` or the new renderer in Part 2), so the six findings + attention list appear verbatim whether or not the model elaborates. The LLM adds prose *around* a section it cannot omit. This is the same deterministic-core-writes-data / LLM-writes-prose split already used for the wiki.

### 1b. Findings contribute to a separate signal, not the 0–8 score

**Decision: do NOT fold findings into the 0–8 layered score.** That score means one specific thing — *is the codebase kept honest / scaffolded* — and is deliberately decoupled from current code state ("8/8 and still on fire"). Keyhole findings are *current pain* (same category as the treemap), not *scaffolding*. Mixing them muddies a contract the rest of the tool depends on.

Instead, introduce a second, parallel headline: a **Keyhole Readiness summary** — a deterministic count/severity roll-up of the findings (e.g. "3 lying maps, 1 orphaned-understanding hotspot, 2 refactor-safe zones"). Reported alongside the 0–8, never merged into it. The two answer different questions: 0–8 = *will the system catch the next class of pain?*; keyhole summary = *where is today's structural pain?*.

### 1c. Findings deterministically drive Top 3 Actions

The `attention` list (highest negative-finding units) must contribute at least the top entries to Top 3 Actions as a hard rule, not a suggestion. If `attention` is non-empty, its top unit is a mandatory Top-3 candidate with the finding's prescribed action.

### 1d. Close the trust-axis gap (Signal E)

The keyhole PRD named three axes — understand / **trust** / deserve-to-exist — but shipped signals for only two. The trust axis (can I trust it does what it claims?) has no keyhole signal, though the tool *does* already have `test_pressure` (mutation + hollow-test heuristics, v1.14.0). Add **Signal E: independent oracle**, two parts:

- **E1. Wire `test_pressure` into the keyhole findings explicitly.** A complex, churning hotspot whose tests are hollow (high survivor density / assertion-on-internal) is a *trust* failure — surface it as a finding (`untrusted_hotspot`) crossed with complexity, the same way C crosses complexity × doc-state.
- **E2. Self-referential test authorship** (the novel, keyhole-specific signal). If the same keyhole wrote the code and its tests — detectable by crossing B4 authorship with test→code mapping: test added in the *same commit, by the same author* (esp. an agent) as the code it covers — the suite verifies internal consistency, not truth. Flag `self_referential_tests`: a weak oracle even at high coverage. This is the measurable form of "did one keyhole grade its own work."

---

## Part 2 — The frozen harness (norm → contract)

Convert `/assess` from a thing-you-run into a thing-that-runs. The insight: **the AI's only irreplaceable job is discovery + prose.** The computation is already deterministic (`assess_core.py` → `run-context.json` + SVGs + wiki, zero model tokens). So the AI can do discovery *once*, freeze the discovered toolchain into a deterministic harness, and that harness runs on every PR forever with **no AI in the loop**.

### 2a. Deterministic templated report renderer

New `scripts/assess_report.py`: reads `run-context.json` and renders a Markdown report from a template (stdlib `string.Template` or a vendored minimal Jinja-free renderer — **no new heavy dependency**; honour the deterministic-core contract). This is the *deterministic* report: metrics dashboard + the six findings + keyhole summary + regression deltas. It does **not** reproduce the 0–8 layered score (that requires the LLM's present/partial/missing judgement) — and that's the honest boundary: the frozen report is the deterministic metrics + findings, not the full prose assessment.

### 2b. The third end-of-run offer

Today `/assess` ends by offering: (1) open a PR with the report, (2) create issues for the Top 3. Add a **third**: *"Freeze this into a repeatable check?"* — emit a CI job (GitHub Action / Makefile target) that:
- runs the discovered deterministic toolchain (the exact tools this run found: lizard/scc, git, grimp, the repo's linters),
- renders the deterministic report via `assess_report.py`,
- compares against the committed prior `run-context.json` and **gates on regression**: fail (or warn — configurable) when a new `lying_map` / `orphaned_understanding` appears, containment drops, p95 complexity rises past a threshold, or `self_referential_tests` increases.

The AI writes the workflow file *once*, baking in the discovered tool paths; thereafter it's a contract, not a norm. This is the README's own argument applied to the tool itself.

### 2c. Regression-gate config

A `[gate]` section in `.assess/config.toml` (the config file already exists): which findings fail vs warn, complexity thresholds, whether to gate at all. Missing config → warn-only defaults (never block a pipeline by surprise).

---

## Part 3 — Decompose the monolith (dogfood keyhole-fit)

`SKILL.md` at 1233 lines is the blob the tool warns about. Break it into cohesive units that still produce today's exact report. Two candidate mechanisms — the repo already has precedent for both:

- **Sub-skills** (precedent: `marathon`, `pr-review-merge` — shared skills invoked by `/tm`, `/issues`, `/fix-pr`).
- **Agents** (precedent: `/huddle` → six hat agents, each reading its own methodology file, spawned by a Blue-Hat orchestrator).

### 3a. Assessment of the seams

The monolith already has natural cut-lines — its sequential Steps and the layer model. Candidate decomposition (to be validated by running the tool's *own* containment/coupling analysis on `SKILL.md`'s sections during Phase 0):

| Unit | Responsibility | Mechanism candidate |
|---|---|---|
| **orchestrator** (`/assess`) | preflight, run core, sequence the phases, assemble final report | thin skill (stays `SKILL.md`, ~150 lines) |
| **layer-scorer** | read run-context, assign present/partial/missing per layer, write scorecard | agent (judgement-heavy, like a hat) |
| **findings-writer** | render keyhole findings + attention + Top 3 (mostly Part 1's deterministic surface) | sub-skill or deterministic script |
| **report-renderer** | the deterministic templated report | script (Part 2a) |
| **pr-and-issues** | the end-of-run offers (PR, issues, freeze-harness) | sub-skill (akin to `pr-review-merge`) |

### 3b. Decision criteria (skill vs agent)

- **Agent** when the unit is judgement-heavy and benefits from a fresh context window applying a methodology (layer-scoring — directly analogous to a hat).
- **Sub-skill** when the unit is a reusable procedure with deterministic steps (PR/issue creation, report rendering).
- **Stays inline** when splitting would raise change-amplification (the over-modularization-is-also-a-blob guard — don't shatter into confetti).

### 3c. Invariant: byte-for-byte report parity

The decomposition must produce the **same report** the monolith does today. Capture a golden-file snapshot of a current `/assess` run before refactoring; assert the decomposed pipeline reproduces it (modulo the new findings/harness sections from Parts 1–2, added deliberately). This is the strangler-fig discipline the tool itself preaches: replace incrementally behind a parity test, never big-bang rewrite.

### 3d. Standalone-ZIP implications

Decomposition interacts with the standalone build (`scripts/standalone_skill_config.py`). Sub-skills/agents referenced by the orchestrator must either be bundled into the ZIP (as huddle bundles its hats via `bundle_files`) or guarded behind `chat-skip` markers. Audit and update the ZIP build; the integration tests must still pass and the ZIP must still build.

---

## Scope / Sequencing

Three PRs, in dependency order:

1. **Part 1 (teeth)** — smallest, highest immediate value; makes the shipped signals actually matter. Doc + `assess_core.py` + SKILL.md + tests. Adds Signal E (E1 wires existing `test_pressure`; E2 is new).
2. **Part 2 (frozen harness)** — the headline feature; depends on Part 1's deterministic surfacing existing. New `assess_report.py` + the third offer + gate config + tests.
3. **Part 3 (decomposition)** — largest, riskiest; do last, behind a golden-file parity test, once Parts 1–2 have settled the report's final shape (so we decompose the *final* structure, not a moving target).

**Out of scope:** reproducing the 0–8 LLM score deterministically (the frozen report is metrics + findings only); auto-fixing any finding; multi-language static analysis beyond what already ships.

## Implementation Phases

- [ ] **Phase 0 — dogfood baseline.** Run `/assess` on this repo; capture the current report as a golden file; run the tool's own containment/coupling analysis on `SKILL.md`'s sections to validate the Part 3 seams empirically (not by intuition).
- [ ] **Phase 1 — Part 1 (teeth):** deterministic findings section; keyhole-readiness summary alongside 0–8; mandatory attention→Top-3 rule; Signal E (E1 + E2) with tests.
- [ ] **Phase 2 — Part 2 (harness):** `assess_report.py` template renderer; third end-of-run offer; CI-job emission; `[gate]` config; regression comparison; tests + ZIP rebuild.
- [ ] **Phase 3 — Part 3 (decomposition):** extract units per 3a/3b behind the 3c parity test; update standalone build + integration tests; trim `SKILL.md` to a thin orchestrator.

## Open Questions

1. **Keyhole-readiness summary shape.** A second number, a letter grade, or a pure count roll-up? Avoid implying it's commensurable with the 0–8.
2. **Gate defaults.** Which findings should *fail* vs *warn* out of the box? Likely warn-only by default (asymmetric-caution: never block a pipeline by surprise), opt into failing.
3. **Self-referential-authorship precision (E2).** Same-commit author match is high-precision but misses code+tests split across two commits by the same agent. Start same-commit; measure miss rate on the dogfood run.
4. **Decompose to skills or agents — or both?** Resolve per-unit in Phase 0 using 3b, validated against the containment analysis, not assumed up front.
5. **Template engine.** Can stdlib `string.Template` carry the report, or is a tiny vendored renderer needed for the conditionals (omit-finding-when-empty)? Prefer stdlib; vendor only if conditionals demand it.
6. **Renderer/agent reuse in the standalone ZIP.** Confirm the decomposed units survive the chat transform (some agent infra is Claude-Code-only).
