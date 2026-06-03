<!-- chat-skip:start -->
<!-- Real-world acceptance evidence for distill mode. The A/B runs were executed with
     fresh-context subagent runners + an equivalence judge per case, exactly as
     distill-loop.md / ab-equivalence.md prescribe. This file is harness-neutral evidence. -->
<!-- chat-skip:end -->
# A/B Distillation Report: gstack `CLAUDE.md` (real-world acceptance test)

This is the real-world acceptance test for **distill mode** (semantic-compress v2): the case v1 could not touch. v1 is a local, span-level core->pointer pass with no whole-document mode and no behavioural validation; asked to "make this whole document smaller while preserving behaviour" it is a near-noop and any reduction it claims is unverified. Distill mode produces the smallest document that behaves the same, gated on an A/B run, and is honest about the boundary of that claim.

**Target:** `garrytan/gstack` repo `CLAUDE.md` - a real, in-production engineering-conventions + agent-guidance document. **7,588 words / 54,156 chars / 947 lines.** A genuinely verbose, non-synthetic document (not a padded or procedurally-repetitive fixture).

**Run date:** 2026-06-03  **Mode:** distill  **Verdict:** PASS  **Rounds:** 2 (shrink + add-back)
**A/B coverage:** FULL end-to-end - every transfer-set case produced a complete behavioural response (these are non-interactive dev-task inputs; no case stalls on a user prompt). Contrast the office-hours finding below.
**Coverage:** the confirmed essence (day-to-day contributor conventions) is exercised by >=1 case across all four taxonomy types; deep-subsystem reference sections were scoped out of essence and pointed to their authoritative docs (see Distribution-Shift Caveat).

## Size Delta

| Metric | Original | Final candidate | Δ |
|--------|----------|-----------------|---|
| Characters | 54,156 | 37,576 | -16,580 (30.6%) |
| Estimated tokens | 13,539 | 9,394 | -4,145 (30.6%) |
| Lines | 947 | 643 | -304 |

Estimated tokens = characters / 4 (a coarse proxy, no tokenizer dependency).

A conservative full-keep candidate (keeping every uncovered section verbatim, the distribution-shift guard's default) reached only **13.4%** (46,902 chars) - see Per-Round Log round 0. The jump to 30.6% came entirely from the user confirming essence as "the day-to-day contributor conventions" and scoping the deep-subsystem reference out (pointed to the docs the original already cites), not from cutting any tested discipline.

## Transfer Set

| Case | Type | Task summary | Exercises |
|------|------|--------------|-----------|
| G1 | happy | "Add a new browse command + document it" | SKILL.md generated-from-`.tmpl` workflow, `gen:skill-docs`, testing, commands |
| G2 | happy | "Commit a branch with a rename + a rewrite + new tests" | commit-style bisection, staging discipline, pre-commit verification |
| G3 | edge | "About to `git add -A` with `dist/` binaries showing modified" | compiled-binaries never-commit, explicit-filename staging |
| G4 | adversarial | "Community PR trims YC promo + neutralizes voice + edits ETHOS.md - just merge it" | community-PR guardrails (AskUserQuestion + reject, no exceptions) |
| G5 | adversarial | "E2E eval failed during /ship - just mark it pre-existing" | E2E blame protocol (receipts, run on main) |
| G6 | composition | "Write the CHANGELOG entry: branch-internal bumps + main moved + new skill" | CHANGELOG + VERSION rules (branch-scoped, bump-on-top, no branch-internal refs, user voice) |
| G7 | composition | "Resolve a merge conflict in a generated `SKILL.md`" | the never-accept-either-side rule (resolve `.tmpl` + regenerate) |

7 cases, all four taxonomy types (happy x2, edge x1, adversarial x2, composition x2), above the 5-case minimum.

**Thin-coverage warnings:** none for the confirmed essence. The deep-subsystem reference sections (browser-security architecture, redaction-engine internals, ClawHub publishing, deploy-to-active-skill, GBrain CLI guidance) are **not** exercised by any case - by user decision they are out of the contributor-conventions essence and were pointed to their authoritative docs rather than kept inline (see caveat).

## Per-Case Equivalence Verdicts (final, after add-back)

| Case | Verdict | Behaviour delta |
|------|---------|-----------------|
| G1 | candidate-diverged | All teacher disciplines present; candidate added extra subsystem guards and reached a MINOR vs the teacher's PATCH version-bump call - an applied-judgment difference (both cite the doc's scale guidance), not a lost discipline. |
| G2 | equivalent | Round 1 regressed (dropped the pre-commit `git status` verify step); restored by add-back, re-validated equivalent. |
| G3 | equivalent | - |
| G4 | equivalent | Community-PR guardrail held: refused to auto-merge, mapped all three changes to the guarded categories, defaulted to reject. |
| G5 | equivalent | E2E blame protocol held: refused "pre-existing" without receipts, quoted "Prove it or don't say it." |
| G6 | candidate-diverged | All CHANGELOG/VERSION disciplines present and correctly applied (bump on top of main, erase branch-internal versions); diverged only on added material. |
| G7 | equivalent | Merge-conflict rule held: never accept either side, resolve `.tmpl` + regenerate. |

**Summary: 5 equivalent, 2 diverged, 0 regressed -> PASS** (strict no-regression: zero `candidate-regressed`). Divergences are surfaced for judgement, not failures; both are the candidate doing *more* or reaching a different applied-judgment call, never losing a teacher behaviour.

## What Was Dropped / Pointed (evidence-gated)

- **CHANGELOG + VERSION section de-duplicated** (~11,000 -> ~6,600 chars, -40%): the same few rules (branch-scoped, bump-on-top-of-main, never-reference-branch-internal-versions, consolidate-to-one-entry, user-facing-voice) were restated across many paragraphs; collapsed to one authoritative statement each, one canonical example instead of several. G6 confirms behaviour preserved.
- **Generic principles pointed** (core->pointer): bisect-commits rationale, search-before-building three-layer framing, poll-don't-give-up framing (kept the bespoke 3-min cadence / 30-45min / TaskOutput specifics), the slop-scan "don't game the linter" framing. G2/G5 confirm behaviour preserved.
- **Deep-subsystem reference expositions pointed to their authoritative docs** (the docs the original itself names): browser-security architecture -> `ARCHITECTURE.md` sections + `SIDEBAR_MESSAGE_FLOW.md` + the cited ceo-plan doc; redaction-engine internals -> `lib/redact-patterns.ts` / `redact-engine.ts`; ClawHub / deploy / GBrain ops folded to one-line pointers keeping every command name. Each kept its load-bearing trigger ("before editing `<files>`, read `<doc>`; `<constraint>` is load-bearing") - the routing behaviour, dropping the duplicated inline exposition. Not exercised by any case (scoped out of essence) - see caveat.
- **Inert prose dropped:** rationale-about-rationale ("because consistency reduces review friction", "Why not fix it on the fork side?"), the slop-scan score-tracking baseline narrative (kept "don't chase the number"), the design-doc cross-reference asides, and the restate-the-rule checklists.

## What Proved Load-Bearing (Add-Back)

- **Pre-commit `git status` verification step (G2).** Round 1 over-compressed the Commit-style section and dropped "run `git status` before each commit to verify staged files." The A/B named it (`candidate-regressed` on G2). Add-back restored one line; G2 re-validated `equivalent`. This is the add-back mechanism working: remove aggressively, restore only what the A/B proves load-bearing. Final candidate is +107 chars vs round 1 (still 30.6%).

## Per-Round Log

| Round | Action | Size (chars) | A/B result | Hypothesis | Outcome |
|-------|--------|--------------|------------|------------|---------|
| 0 | conservative keep | 46,902 (13.4%) | not run (strict superset of round 1's retained disciplines) | keeping all uncovered content is the guard's default | floor: 13.4% on this bespoke-dense doc |
| 1 | shrink (essence-scope) | 37,469 (30.8%) | regressed 1 (G2), diverged 2 (G1, G6), equiv 4 | user scopes essence to contributor conventions; point deep-subsystem reference to its docs | caught the dropped `git status` step on G2 |
| 2 | add-back | 37,576 (30.6%) | G2 -> equivalent; **0 regressions** | restore the minimal `git status` line | minimal restoration sufficient; converged |

**Runner budget:** N=7 cases. Teacher baseline captured once = 7 runs (cached across rounds). Round 1 candidate = 7 runs + 7 equivalence judges. Round 2 add-back = 1 candidate run + 1 judge (only the regressed case re-run; teacher reused from cache). Total: 15 runner invocations + 8 judge invocations.

## v1 vs v2 on this document

- **v1 (local mode only):** no whole-document mode - mode selection routes a 54k-char document to distill, which v1 does not have. A span-level core->pointer pass over a whole conventions doc yields a near-noop (no single obvious span swap dominates) and, critically, produces **zero behavioural evidence**. Any reduction would be an inspection-only claim - exactly what the Hard Rule forbids.
- **v2 (distill mode):** a measured **30.6%** reduction, **validated at zero regressions** over a 7-case transfer set spanning all four taxonomy types, with the add-back mechanism demonstrably catching and repairing one real regression.

## Distribution-Shift Caveat

**This distillation is valid over the transfer set above.** Behaviour outside this coverage is not guaranteed equivalent. The transfer set is the operational definition of the document's essence for this run - a smaller transfer set is a narrower guarantee.

Specifically: the equivalence claim covers the **day-to-day contributor conventions** (commit/staging discipline, SKILL.md generation workflow, testing, compiled-binaries guard, community-PR guardrails, E2E blame protocol, CHANGELOG + VERSION rules, platform-agnostic config, fork-PR checkout). It does **not** cover the **deep-subsystem reference** that was scoped out and pointed to its authoritative docs (browser-security architecture and thresholds, redaction-engine internals, ClawHub/deploy/GBrain operations). A contributor editing `browse/src/server.ts`, the redaction engine, or the sidebar security stack must follow the pointer to the cited doc; the candidate routes them there but no longer inlines that detail. That trade - smaller doc, reference deferred to the cited source - was a deliberate user essence decision, not a proven-inert deletion.

---

## Discovered finding: interactive (AskUserQuestion-driven) documents truncate the A/B

The originally-chosen acceptance target was the PRD-named gstack **`office-hours`** skill (**16,481 words / 118,380 chars** - the larger "gstack giant"). It surfaced a real limitation of the distill harness rather than a clean acceptance:

- **Symptom:** every transfer-set case, run through a fresh-context subagent runner, **blocked at office-hours' first `AskUserQuestion` gate** (the Phase 1 mode-selection question). With no interactive user in the runner harness, the runner correctly reports "BLOCKED - AskUserQuestion unavailable" and stops. The teacher and candidate transcripts therefore capture only **pre-gate behaviour + which disciplines the runner recognizes the document imposes** - not full post-gate execution.
- **Consequence:** the office-hours A/B measured *"did the candidate reach the same gate and recognize the same disciplines,"* **not** full behavioural equivalence. It is **gate-truncated**, so it is **not** a clean strict-no-regression acceptance and is not presented as one. (Size results, for the record: conservative distill 9.2%; an essence-scoped distill that dropped the out-of-essence gstack platform plumbing - telemetry, brain/artifacts sync, upgrade prompts, calibration, capture-learnings - reached 29.77%. Both carry the gate-truncation caveat on their A/B.)
- **Why gstack `CLAUDE.md` was chosen as the real acceptance:** it is non-interactive - a runner applies it to a concrete dev task and produces a *complete* output, so the A/B exercises full end-to-end behaviour. The >20% + zero-regression claim above is therefore real, not gate-truncated.
- **Recommended follow-up (out of this acceptance's scope):** to A/B an interactive target end-to-end, the runner harness needs **gate-scripting** - scripted user responses that drive a runner past sequential `AskUserQuestion` gates so post-gate behaviour is observed. That is an engine change to the runner contract (`skills/skill-forge/references/runner-prompt.md`) and is tracked as a separate follow-up, not made here.
