# Assess — Truth-Pressure Signals (Write-Side Verification) — PRD

> **For agentic workers:** Implement on a worktree branched from `main` (e.g. `assess-write-side-truth-pressure`). Committed home for this doc: `docs/superpowers/plans/2026-05-29-assess-write-side-truth-pressure.md`. Steps use checkbox (`- [ ]`) syntax for tracking. The eventual *implementation* is a **MINOR** feature (new deterministic verification tier + reframed Layer 6 scoring; backward-compatible report shape, no renumber) - bump `.claude-plugin/plugin.json` `.version` minor **in the implementing PR**, not in this planning PR (this PR is the plan only, mirroring the dismiss-false-positives and keyhole-readiness PRD precedent). `skills/assess/SKILL.md` is subject to the standalone-ZIP transform (`chat-skip` / `chat-replace` markers + `scripts/standalone_skill_config.py`); keep standalone-divergent wording in config, not body prose, and rebuild/validate the ZIP.
>
> **Companion doc:** this is the *write-side completion* of `2026-05-27-assess-truth-pressure-signals.md`. That PRD reframed the read side (L0 navigability, L1 liveness) from presence to truth-pressure and stated the general thesis - "every layer is a truth-pressure check in disguise" - but only *implemented* the reframe for the read side. This PRD applies the same move to the write-side test layer. Distinct from `2026-05-29-assess-keyhole-readiness.md`, which is structural legibility (does a change *fit* the keyhole); this is signal honesty (do the test signals *mean* what they claim).

**Type:** Feature / model enhancement
**Priority:** High - a green coverage gate is the most confident lie a repo tells an agent, and the read-side reframe left it untouched.
**Affected skill:** `skills/assess/` (`SKILL.md`, `scripts/assess_core.py`, `scripts/lib/`, `tests/`).

---

## Problem Statement

`/assess` Layer 6 (Coverage Gates) scores coverage on **presence and gate strictness** (`SKILL.md` L6 scoring): "Coverage gates block PRs below threshold, patch coverage enforced." That measures whether a gate *exists and blocks* - never whether the coverage behind it *constrains behaviour*. This is exactly the presence-trap the read-side PRD's own thesis warns against, and Layer 6 is the one layer the truth-pressure pass renumbered but did not reframe.

This matters more at L6 than anywhere else: **a green coverage gate is the highest-confidence lie a repo can tell an agent.** A missing doc makes the agent go look. A green coverage badge tells the agent "behaviour is pinned here" when it may only mean "this line executed once." An AI contributor reads coverage as a safety signal and builds on top of unverified logic.

Concrete evidence (the `meridian` reference repo, an `/assess`-driven bug-fix run): a CSV-resume off-by-one guard was **live code with hollow verification**. The guard was executed by the suite (coverage green), its tests passed (CI green), yet the tests asserted on an **internal field** (`LastProcessedLine`) instead of the **observable behaviour** (no duplicate ledger positions at the resume boundary). You could break the behaviour and no test would fail. That is the fingerprint of the class this PRD targets: **covered, but not pinned.** A structural tell accompanied it - a redundant field (`LastProcessedLine` always `== ProcessedRows`) that duplicated a source of truth, which is what let the off-by-one hide in plain sight.

Two failures follow, both invisible to the current model:

1. **Coverage measures execution, not constraint.** A line can be 100% covered and 0% pinned. The current L6 reads execution as if it were constraint.
2. **The treemap renders the danger as safe.** Its risk axis is complexity × churn; the resume guard was a one-line, low-complexity comparison, so it renders cool/safe. Simple-but-unpinned reads as low-risk - itself a false reassurance, and the *opposite* of where attention is owed.

## Conceptual Foundation (read this before implementing - it changes how you score)

The read-side PRD established the load-bearing frame: **the real signal is never presence; it is whether a thing is under active pressure to stay true.** That frame already names the verifier for behaviour - "Tests keep behaviour honest (CI fails when it's wrong)." This PRD checks that the parenthetical is *true rather than nominal*. Right now L6 assumes it.

The symmetry with the read side is exact:

| Read side | Write side |
|---|---|
| **L1: found ≠ live** - finding the code does not mean it runs | **L6: covered ≠ pinned** - covering the code does not mean its behaviour is constrained |
| Verified by a **dead-code / reachability scan** (the deterministic tier) | Verified by a **mutation score** (the deterministic tier) |
| Honest limit: static reachability proves "nothing in *this* repo calls it," never "no external consumer does" | Honest limit: mutation testing samples, it does not prove; a killed mutant set is evidence, not a guarantee |

So this is not a new layer. It is **Layer 6 graduating from presence to pressure** - the same move the read-side PRD made on Layer 0. Mutation score is the *verification tier* for the coverage layer, exactly as the dead-code scan is the verification tier for liveness. It sharpens an existing node; it does not add a dependency edge.

The unifying class across L0 / L1 / L6 is **lying signals**: self-descriptions that look true and aren't - a stale hub doc, a dead-but-present subsystem, a green-but-hollow coverage report. Naming them as one class in the report is what makes the thesis land for the reader.

## Technical Context

- **L6 today** (`SKILL.md`, Layer 6 section): scans for coverage config (`codecov.yml`, `.coveragerc`, `jest`/`vitest` config), assesses gate strictness (project/patch/component thresholds, fail-on-regression), and scores Present/Partial/Missing on whether the gate blocks. No notion of whether coverage constrains behaviour.
- **The readiness score is produced by the LLM** following `SKILL.md`; the deterministic core (`assess_core.py`) supplies *inputs* via `.assess/run-context.json`. This PRD adds a `test_pressure` input block the LLM scores against, identical in shape to how `dead_code_candidates` / `doc_staleness` feed the read-side layers.
- **Mutation testing is external, language-specific, and heavy** (minutes to hours on a real suite). The deterministic core must therefore **detect mutation setup** the way it already detects coverage config, **run bounded mutation only when cheap or opt-in**, and lean on always-on cheap heuristics for the default path. It must degrade to "not assessed" rather than block - the same contract as the read-side scans.
- `skills/assess/SKILL.md` is authoritative; the standalone ZIP is derived via the marker transform. Respect the pipeline; rebuild and validate.

## Layer Position - In-place Reframe of Layer 6, No Renumber (DECIDED)

Unlike the read-side PRD (which *added* Layer 1 because liveness sits on a real dependency edge between read and write), this capability adds **no node and no edge**. Mutation score is a deeper verification of the coverage layer's existing claim, not a new claim. The `/8` model from the read-side PRD stays `/8`; no renumber, no maturity-band rescale, no report-template renumber. The only change to L6 is its **scoring rubric** - from "is the gate enforced?" to "does the covered behaviour stay pinned?" Stating this firmly here closes it as a non-question: do not reopen layer numbering for this work.

## Solution Requirements

1. **Reframe Layer 6 scoring** from gate-strictness to behaviour-constraint. Present requires coverage that *constrains*; a high-coverage / low-mutation-score repo scores **at or below** an honest lower-coverage repo - mirroring the read-side `stale-doc-scores-below-absent` rule. (A hollow green gate is a lying map; honest low coverage at least makes the agent go look.)
2. **Add a mutation verification tier** to the deterministic core, alongside the read-side dead-code tier. Detect-don't-always-run; bounded/sampled; graceful degradation; emit a `test_pressure` block. The signal is the **gap** (high coverage + low mutation score / survivor clusters), not the raw score.
3. **Add an always-on cheap-heuristic tier** for the common case where full mutation is too heavy - AST/grep signals that approximate hollow verification at near-zero cost (noisier; mutation is the trusted detector).
4. **Surface "covered-but-unpinned" in the risk view** so simple-low-complexity-but-unverified code stops rendering safe on the treemap.
5. **Report lying signals as one cross-layer class** (L0 stale hub / L1 dead-but-present / L6 green-but-hollow), so the unifying thesis is visible in the output.
6. **State the honest limits** in the report: mutation testing samples rather than proves; the cheap heuristics are candidate signals for human judgement, never verdicts. Never block an assessment on either.

## Proposed Changes

### Part A - Reframe Layer 6 scoring (covered ≠ pinned)

- [ ] Rewrite the L6 scoring rubric in `SKILL.md` from gate-presence/strictness to **behaviour-constraint**:
  - **Present** - coverage is enforced **and** carries truth-pressure: a mutation score / survivor density that shows tests pin behaviour (or, where mutation is unavailable, the cheap heuristics show no hollow-assertion clusters), with patch-level enforcement.
  - **Partial** - coverage enforced but **unverified or weakly pinned**: high line coverage with low/unknown mutation score, survivor clusters, or assertion-on-internal-state clusters. This is the meridian resume case: green gate, hollow constraint.
  - **Missing** - no coverage configuration or gates.
- [ ] Encode the **asymmetry rule** explicitly, mirroring `stale-doc-below-absent`: a high-coverage repo whose tests do not constrain behaviour scores **at or below** a repo with honest lower coverage. A confident-but-hollow gate is actively misleading; honest low coverage is merely incomplete.
- [ ] Update `Top 3 Actions` guidance so a Partial-on-L6 produces the *correct* remediation - "strengthen assertions to pin observable behaviour at the named survivors" (or "add mutation testing to CI"), **not** "raise the coverage threshold." Raising the threshold on hollow tests manufactures more lying signal.

### Part B - Mutation verification tier (deterministic core, the trusted detector)

- [ ] **Detect mutation setup first** (presence is itself a truth-pressure signal, like detecting coverage gates): scan for `stryker.conf.*`, `mutmut`/`cosmic-ray` config, `cargo-mutants` / `gremlins` / `go-mutesting` invocation in CI. A repo already running mutation in CI carries strong write-side truth-pressure even before `/assess` runs anything.
- [ ] **Run bounded mutation only when cheap or explicitly opted in** (see Open Questions): a sampled, time-boxed run over the highest-risk files (the treemap's hot cells + recently-churned code), language-appropriate tool where present:
  - Go: `gremlins` / `go-mutesting` · TS/JS: `Stryker` · Python: `mutmut` / `cosmic-ray` · Rust: `cargo-mutants`.
  - Degrade gracefully where no tool exists or the run would exceed the budget - emit "not assessed," never crash, never block.
- [ ] Emit a **`test_pressure`** block into `run-context.json`: `mutation_config_present`, `mutation_run` (bool + scope/sample size), per-file `survivors` / `mutants` / `score`, and `survivor_density` over assessed files. **The headline derived signal is the gap:** high line-coverage ∧ low mutation score (or survivor clusters) = "tests are theatre here."
- [ ] **State the limit in the report:** mutation testing is sampling, not proof; a clean sampled run is evidence the assessed surface is pinned, not a guarantee the whole suite is.

### Part C - Cheap-heuristic tier (always-on, AST/grep; calibrated by the meridian fingerprint)

Cheaper but noisier than mutation; these are the default-path approximations of hollow verification. All are **candidate signals for human judgement**, never verdicts.

- [ ] **Assertion-on-internal-state.** Covered functions whose only test assertions reference private fields / getters, with **no assertion on the public side-effect** (return value, DB rows, emitted output). This is the direct fingerprint of the resume-guard sham. Emit `assertion_on_internal` candidates into `test_pressure`.
- [ ] **Boundary operators without boundary tests.** `<=` / `<` / `+1` / `-1` comparisons that are covered but never exercised *across* the boundary (the off-by-one's home). Emit `untested_boundaries`.
- [ ] **Duplicate source of truth.** A field only ever assigned `= otherField` (or `otherField + k`) and never independently - the structural tell that let the off-by-one hide. (See Open Questions: this may belong to Layer 2 / Code Design with a cross-ref, since it is a design smell that *enables* hollow tests rather than a test-honesty signal itself.) Emit `duplicate_truth_fields`.
- [ ] **`<!-- task-1-tells -->` placeholder.** task-1 (the teammate that fixed the meridian resume bug) is to relay the specific tells it observed and its design opinion; fold its concrete fingerprints into the three heuristics above to tighten precision and reduce false positives. Until then, ship the heuristics as best-effort with conservative thresholds. **This subsection is the only part of the PRD that depends on task-1; the spine (A, B) does not.**

### Part D - Risk-view overlay (covered-but-unpinned)

- [ ] Add a **survivor-density / "covered-but-unpinned" overlay** to the risk view so simple, low-complexity, unverified code stops rendering cool/safe (today the hottest-risk cell in the meridian run rendered cold). Reuse the existing treemap machinery; add **one** encoding only - resist cramming a second variable into the complexity treemap. Prefer either a dedicated single-variable cut (survivor density per file) or a hatch/marker overlay on existing cells. Optional and non-blocking - the deterministic `test_pressure` value stands without it.

### Part E - Lying-signals report section (cross-layer)

- [ ] Add a concise **"Lying signals"** section to the report that names the one class across layers: stale hub doc (L0), dead-but-present subsystem (L1), green-but-hollow coverage (L6). Pull the worst instance from each available block; if none, omit the section. This is presentation glue, not new computation - it makes the truth-pressure thesis legible to the reader rather than leaving it implicit across three layers.

### Part F - Tests & release

- [ ] Unit tests for the new deterministic inputs: `test_pressure` emission, the cheap heuristics (assertion-on-internal, untested-boundary, duplicate-truth detection), and **graceful-degradation paths** (no mutation tool / over-budget → `mutation_run: false`, no crash).
- [ ] **Fixtures that encode the sham itself:** a hollow-test fixture (covered, passing, asserts an internal field, a mutant survives at a boundary) vs an honest-test fixture (same code, asserts the observable side-effect, mutant killed). The hollow fixture must score L6 **Partial** and the honest one **Present**, with otherwise-identical coverage - the regression test for the whole thesis.
- [ ] Respect the standalone pipeline: rebuild (`bash scripts/build-standalone-skills.sh assess`), confirm no orphan markers and standalone wording intact; run `cd scripts && uv run --with pytest pytest -v` and the skill-local suite.
- [ ] MINOR version bump in `.claude-plugin/plugin.json` (this implementing PR; not the planning PR).

## File Map

| Action | Path | Purpose |
|--------|------|---------|
| Modify | `skills/assess/SKILL.md` | Reframe L6 scoring (covered ≠ pinned, asymmetry rule); Top-3 remediation; Lying-signals report section; truth-pressure framing cross-ref |
| Modify | `skills/assess/scripts/assess_core.py` | Emit `test_pressure` into `run-context.json`; orchestrate detect → bounded-run → heuristics |
| Modify/Create | `skills/assess/scripts/lib/` (e.g. `test_pressure.py`) | Mutation detect/run + parse; cheap heuristics (assertion-on-internal, untested-boundary, duplicate-truth); survivor-density aggregation |
| Modify | `skills/assess/scripts/complexity-treemap.py` (or new `test-pressure-treemap.py`) | Optional covered-but-unpinned overlay / single-variable survivor-density cut |
| Create/Modify | `skills/assess/tests/` | Hollow-vs-honest fixtures + tests for inputs and degradation paths |
| Modify | `scripts/standalone_skill_config.py` | Any standalone-divergent wording relocated out of `SKILL.md` body |
| Modify | `.claude-plugin/plugin.json` | MINOR version bump |

## Success Criteria

- Two repos with identical line coverage - one asserting observable behaviour, one asserting internal state - score L6 **Present** and **Partial** respectively; the hollow one is named, not rewarded.
- `/assess` emits a `test_pressure` block and reports the **coverage-vs-mutation gap** as the headline write-side signal; a repo already running mutation in CI is recognised as carrying that truth-pressure even when `/assess` runs no mutation itself.
- The cheap heuristics flag the meridian resume pattern (assertion-on-internal-state at an untested boundary, beside a duplicate-source-of-truth field) on a fixture modelled on it.
- The report's **Lying signals** section names the green-but-hollow-coverage instance alongside the read-side stale-doc / dead-code instances when present.
- A low-complexity, low-churn, unverified file no longer renders as safe in the risk view (overlay or single-variable cut).
- New deterministic inputs are tested, degrade gracefully when mutation tooling is absent or over budget, and never block an assessment.
- No layer renumber; `/8` model and maturity bands unchanged. ZIP rebuild clean; `pytest` green; MINOR version bumped.

## Risk Assessment

Moderate, leaning low. Mostly additive: a new deterministic input block, an L6 rubric reframe, and report glue; **no renumber** (the main structural risk of the read-side PRD is absent here). Primary risks: (1) **mutation cost** - a naive full run is minutes-to-hours and would make `/assess` unusable; mitigate by detect-don't-always-run, time-boxed sampling over hot files only, and graceful "not assessed." (2) **Cheap-heuristic false positives** - assertion-on-internal-state is noisy across languages/test frameworks; mitigate with conservative thresholds, candidate-not-verdict framing, and task-1 calibration. (3) **Language coverage** - mutation tools are per-language and brittle; mitigate by treating them as best-effort with the same degradation contract as the read-side scans. (4) **Standalone/CLI wording divergence** - mitigate via the existing ZIP forbidden-string test plus a rebuild-and-read. No customer-facing runtime affected; the skill is a read-only assessor.

## Open Questions

1. **Run policy for the heavy tier.** Does `/assess` ever run mutation by default, or only (a) when a mutation config already exists, and/or (b) under an explicit `--mutate` opt-in, leaning on the cheap heuristics for the always-on path? Leaning toward detect-by-default + bounded-sample-on-opt-in. Decide before Part B implementation.
2. **Mutation budget and scope.** If sampled, what bounds (wall-clock cap, file count, mutate-only-churned-files)? Calibrate on this repo and 1-2 reference repos.
3. **Where "duplicate source of truth" belongs.** Layer 6 (test honesty) or Layer 2 (Code Design)? It is a design smell that *enables* hollow tests rather than a test-honesty signal itself - likely L2 with an L6 cross-ref. Resolve during Part C.
4. **Heuristic precision (task-1 calibration).** False-positive rate of assertion-on-internal-state across Go / TS / Python test idioms; fold in task-1's observed fingerprints before baking thresholds.
5. **Mutation-config presence vs results scoring.** How much L6 credit does a repo earn for *running* mutation in CI (presence of pressure) versus the *results* `/assess` can sample? Presence alone is a strong signal - decide the weighting.

## Out of Scope / Phase 2

- **Running full-suite mutation during assessment.** Detection + bounded sampling ships now; an exhaustive mutation run is a separate, heavier capability.
- **Cross-boundary behaviour verification.** Whether an external consumer relies on the pinned behaviour (the L1 cross-boundary liveness limit applies symmetrically here) needs telemetry / a named human, not static analysis.
- **Property-based / fuzz-coverage signals.** A richer "constraint strength" axis beyond mutation (e.g. detecting property-based test presence) is a future refinement.
