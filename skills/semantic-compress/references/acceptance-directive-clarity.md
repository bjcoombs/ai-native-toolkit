<!-- chat-skip:start -->
<!-- Real-world acceptance evidence for the directive-clarity transform. The two LIVE A/B
     cases (M1, P1) were executed with fresh-context subagent runners + an equivalence judge
     per case, exactly as runner-prompt.md / equivalence-judge-prompt.md / ab-equivalence.md
     prescribe. Everything else in this report is explicitly labelled contract-level. -->
<!-- chat-skip:end -->
# Directive-clarity acceptance test: `marathon` + `pr-review-merge`

Real-world acceptance for the **directive-clarity transform** (semantic-compress Transform #2). The transform rewrites latent-action instructions (bare negations, facts-not-actions, vague pointers, ordering rules) into directives that name the action, gated on the A/B harness's no-regression **and** a measured directness gain.

**Targets:** the toolkit's two most negation-dense operational skills - `skills/marathon/SKILL.md` (463 lines) and `skills/pr-review-merge/SKILL.md` (204 lines). Both are battle-tested orchestration skills, so they are a hard test in two directions at once: they carry many genuine battle-scars the classifier must preserve, and a handful of convert-for-free instructions the transform should improve.

**Run date:** 2026-06-03  **Transform:** directive-clarity  **Verdict:** PASS (both skills clear the gate)

**Scope:** this is a VALIDATION run. It produces this report and does **not** rewrite either skill. The convert-for-free rewrites below are *proposed*; the live A/B confirms the proposal earns the gate on the cases tested. Applying any rewrite to the operational skills is a separate, carefully-reviewed change (see Recommended follow-ups).

## What was live vs contract-level (read this first)

Honesty about the evidence matters more than the headline.

- **LIVE A/B (2 cases, M1 + P1):** real fresh-context runner subagents executed each version on an identical scenario input and produced full self-reports; a real equivalence judge compared the two transcripts and emitted the structured verdict + efficiency signal. These are the measured directness gains the gate requires - one per skill.
- **Contract-level walkthrough (the rest):** detection, classification, and the remaining rewrites are reasoned against the documented detection/classifier/rewrite rules and the harness contract. They are **not** live A/B runs and are not presented as measured equivalence verdicts.

**A structural limitation surfaced, and it shaped the live-case design.** `marathon` and `pr-review-merge` are infrastructure-coupled: a runner applying the whole skill to "run this queue to merge" would block on live team creation, real worktrees, and live PRs, with no sandbox to execute against - the same non-executable / gate-truncation class the distillation acceptance hit on the interactive `office-hours` skill (`acceptance-distillation-report.md`). A naive end-to-end A/B here would be gate-truncated. The live cases therefore use a **scoped, scenario-based A/B**: the runner receives the self-contained instruction section under test plus a concrete decision scenario and states the action it would take. That exercises the specific rewritten instruction without needing live infrastructure, and yields a real directness measurement on the thing being changed. The full-skill end-to-end A/B for these operational skills needs the runner-harness gate-scripting follow-up already tracked from the distillation acceptance.

---

## Skill 1: `marathon`

### 1.1 Detection coverage (recall)

Detection over-includes on purpose (`directive-clarity-patterns.md`). Twelve candidates across all four pattern shapes:

| # | Instruction (location) | Pattern |
|---|------------------------|---------|
| M-a | "Never run source-of-truth write commands as parallel background jobs" (Step 1) | 1 bare negation |
| M-b | "never use `gh --jq` with complex filters" (Shell Rules) | 1 bare negation |
| M-c | "Do not block on CI yourself" (teammate workflow, step 3) | 1 bare negation |
| M-d | "don't create additional PRs" (Scope) | 1 bare negation |
| M-e | "Never reuse a teammate for a different task" (Ephemeral Teammates) | 1 bare negation |
| M-f | "Haiku cannot reliably handle review loops - never use for teammates" | 1 bare negation |
| M-g | "one strike - don't give sonnet a second chance on the same task" | 1 bare negation |
| M-h | "Never chain them [cleanup] unconditionally after the merge call" (Smart Merge) | 1 + 4 |
| M-i | "Don't merge an AI-authored docs/content PR while its AI reviewer is still pending" | 1 + 4 |
| M-j | "Idle teammate != dead teammate" (header) | 2 fact-not-action |
| M-k | "Sonnet ... has a recurring false REVIEW_CLEAR problem" | 2 fact-not-action |
| M-l | "relay to user if genuinely ambiguous" (CLARIFICATION_NEEDED) | 3 vague pointer |

Twelve candidates, well above the 5+ recall bar.

### 1.2 Classification (precision)

Routed through `battle-scar-classifier.md`. **Five preserved scars, four convert-for-free, three near-miss/keep-both.**

**Preserved battle-scars (never rewritten):**

| # | Deciding tier | Why preserve |
|---|---------------|--------------|
| M-a | T1 | Carries the incident verbatim: "validated in the 047-security-audit marathon where 10 background `add-task` calls created tasks on wrong tags." The wording is the earned knowledge. |
| M-f | T2 | Names a specific capability limit; the fact ("cannot reliably handle review loops") *is* the rationale a positive rewrite would drop. |
| M-g | T1/T3 | "one strike" is a severity marker; rewriting to "respawn failed sonnet tasks on opus" drops the no-retry severity and the "same task" scope. Not reversible. |
| M-h | T1 | Carries the consequence: "a rejected merge with chained cleanup deletes the branch/worktree of a PR that never merged ... wastes a recovery cycle every time." |
| M-i | T2 | Specific actor + specific condition + the bespoke rationale "AI-written docs are exactly where AI-authoring residue ... hides." |

This is the asymmetric default working: five scars held back, well past the "2+ preserved per skill" bar, at zero false conversions. M-k (the sonnet false-REVIEW_CLEAR fact) also leans battle-scar - it carries a waste marker ("~15 min waste per marathon when it happens") - so it is preserved on T1, not converted.

**Convert-for-free (released to rewrite):** M-b, M-c, M-d, plus the ordering rule "check for an existing PR before creating one" and "batch-dismiss stale CRs before spawning the next wave." M-e ("Never reuse a teammate") and M-j ("Idle teammate != dead teammate") are **near-misses**: the positive action already sits adjacent in the same passage, so the rewrite is keep-both (add nothing, the action is present) rather than a conversion. M-l is a **vague pointer held for confirmation** - "genuinely ambiguous" names no test the document supplies, so the rewriter flags it and leaves the original in place rather than fabricating a threshold.

### 1.3 Proposed rewrites (convert-for-free set)

Each keeps every qualifier and passes the names-the-concrete-action check (`directive-clarity-rewrites.md`):

| Original | Rewrite |
|----------|---------|
| M-b "never use `gh --jq` with complex filters" | "pipe `gh` output to `jq` separately for complex filters" (qualifier kept; adjacent zsh-`!=` rationale left intact - the classifier #6 boundary case) |
| M-c "Do not block on CI yourself" | "spawn a background agent to watch CI (`gh pr checks <PR> --watch --fail-fast`) and continue other work meanwhile; do not block on CI yourself" (keep-both: prohibition retained, action named) |
| M-d "don't create additional PRs" | "keep all work on your one branch; note related work in the PR description instead" |
| ordering: "Before creating PR, check for existing" | "1. query open PRs on the branch. 2. reuse an open one; skip a merged one. 3. otherwise create the PR" |
| ordering: "Batch-dismiss stale CRs ... before spawning next wave" | "1. batch-dismiss stale CRs across all eligible PRs. 2. then spawn the next wave" |

### 1.4 A/B equivalence - case M1 (LIVE)

**Rewrite under test:** M-c, "Do not block on CI yourself" -> the background-watcher keep-both rewrite.
**Scenario:** a teammate has pushed a fix to PR #123, CI is running (~8 min), 2 unresolved CodeRabbit threads. "Walk through what you do next, step by step, until you would send REVIEW_CLEAR."
**Method:** two fresh-context runner subagents (teacher = original line, candidate = rewrite), one equivalence judge. Real runs.

| Field | Value |
|-------|-------|
| Verdict | `candidate-diverged` |
| `original_directness` | 3 |
| `candidate_directness` | **4** (measured gain) |
| Regression? | No |

**Judge's behaviour delta:** every teacher discipline preserved (did non-CI work now, fixed the 2 bot threads via TDD, resolved via `resolveReviewThread`, did not block on CI, withheld REVIEW_CLEAR until all five criteria met). The candidate additionally spawned the explicit background CI watcher the rewrite names; that is an added mechanism, not a lost behaviour.
**Why the directness gain:** the judge noted the original forced the runner to resolve a real tension ("do not block on CI" vs "REVIEW_CLEAR only when CI is green") with no mechanism given - the teacher transcript explicitly flagged an instinct-to-wait it had to suppress and an underspecified path to learning CI passed. The candidate handed it the exact mechanism, so it acted directly.

**Gate result:** no-regression (zero `candidate-regressed`) AND `candidate_directness (4) > original_directness (3)`. **M1 clears the directive-clarity gate.**

---

## Skill 2: `pr-review-merge`

### 2.1 Detection coverage (recall)

Eleven candidates across all four shapes:

| # | Instruction (location) | Pattern |
|---|------------------------|---------|
| P-a | "Never use `gh ... --jq` with complex filters" (Shell Pitfalls) | 1 bare negation |
| P-b | "Use positive jq filters, not negative" (+ zsh `!=` rationale) | 1 bare negation |
| P-c | "Do NOT resolve human threads - let the reviewer confirm" | 1 bare negation |
| P-d | "never block on CI yourself" (Step 2) | 1 bare negation |
| P-e | "Never chain cleanup unconditionally after the merge command" (After merge) | 1 + 4 |
| P-f | "never trust the caller's claim that a PR is ready" / "Trust the API, not the message" | 1 / 2 |
| P-g | "GitHub sometimes returns `mergeStateStatus: UNKNOWN` even when all checks pass" | 2 fact-not-action |
| P-h | "a plain `gh pr merge --squash` can be refused ... whenever a non-required check ... is PENDING" | 2 fact-not-action |
| P-i | "CLEAN can flip to DIRTY from cascade" (Merge Order) | 2 fact-not-action |
| P-j | "report blocked if genuinely ambiguous" (DIRTY, If not merge-ready) | 3 vague pointer |
| P-k | "stage changes but don't push yet" while CI runs (Step 3) | 4 ordering |

Eleven candidates, above the 5+ bar.

### 2.2 Classification (precision)

**Three preserved scars, four convert-for-free, four near-miss/keep-both.**

**Preserved battle-scars (never rewritten):**

| # | Deciding tier | Why preserve |
|---|---------------|--------------|
| P-e | T1 | Carries the consequence verbatim: "a chained one-liner blindly runs cleanup anyway, deleting the worktree and branch of a PR that never merged." |
| P-f | T2 | Earned distrust - "never trust the caller's claim", "Trust the API, not the message." The prohibition is the discipline; a breezy positive ("verify via the API") drops the *never trust the report* severity. |
| P-h | T1/T2 | The `--admin` rationale: a specific earned failure mode ("can be refused ... whenever a non-required check is PENDING ... even though `mergeStateStatus` reported CLEAN a moment earlier"). The fact is the whole justification for the override. |

Past the "2+ preserved per skill" bar at zero false conversions.

**Convert-for-free (released):** P-a, P-c, P-d, P-j (vague pointer, recoverable from the conflict-resolution patterns in the same section), P-k. **Near-miss/keep-both:** P-b (action + rationale already adjacent), P-g (UNKNOWN-handling action already paired in the same paragraph), P-i ("re-check merge state after each" already adjacent). P-g's fact-not-action is well-formed in the original, so it is left as is.

### 2.3 Proposed rewrites (convert-for-free set)

| Original | Rewrite |
|----------|---------|
| P-a "Never use `gh ... --jq` with complex filters" | "pipe `gh` output to `jq` separately for complex filters" (keep-both with the adjacent example) |
| P-c "Do NOT resolve human threads - let the reviewer confirm" | "leave human threads open for the reviewer to confirm and resolve; fix the code, reply inline, @mention the reviewer" (reversible, lossless - classifier #10) |
| P-d "never block on CI yourself" | "spawn a background agent to watch CI and continue other work meanwhile; do not block on CI yourself" |
| P-j "report blocked if genuinely ambiguous" | "report blocked when both branches modify the same key or line differently (e.g. one config key set to different values), citing the file, key/line, and both values; auto-resolve additive import/route/barrel conflicts per the patterns above" (threshold recovered from the conflict-resolution patterns above it - not fabricated) |
| P-k "stage changes but don't push yet" | "fix locally and `git add`; hold the push until CI finishes, then push once" |

### 2.4 A/B equivalence - case P1 (LIVE)

**Rewrite under test:** P-j, the "report blocked if genuinely ambiguous" vague-pointer rewrite.
**Scenario:** two merge conflicts after merging base - (a) `src/routes/index.ts`, both branches add a route import on adjacent lines; (b) `config/app.json`, `timeout` is `30` on base and `60` on the branch. "For each, state concretely what you do."
**Method:** two fresh-context runners + one equivalence judge. Real runs.

| Field | Value |
|-------|-------|
| Verdict | `candidate-diverged` |
| `original_directness` | 3 |
| `candidate_directness` | **4** (measured gain) |
| Regression? | No |

**Judge's behaviour delta:** both versions auto-resolved (a) by accepting both imports, and both escalated (b) without auto-picking a value - the core discipline (never auto-resolve a same-key config conflict; surface both values) is fully preserved. The difference is escalation *form*: the teacher posed a live "ask" gate while reconciling the bullet's "then ask" against the closing "report blocked if genuinely ambiguous"; the candidate operationalized escalation as a structured blocked report (file, key, both values) with no interpretive overhead.
**Why the directness gain:** the judge cited the teacher having to reconcile the "ask vs blocked" tension via a compatibility treatment (more unpacking), while the candidate's concrete report format pre-resolved the action shape.

**Gate result:** no-regression AND `candidate_directness (4) > original_directness (3)`. **P1 clears the directive-clarity gate.**

---

## Summary

| Skill | Detected | Preserved scars | Convert-for-free | Live A/B verdict | Directness |
|-------|----------|-----------------|------------------|------------------|------------|
| `marathon` | 12 | 5 | 4 (+2 near-miss) | M1 `candidate-diverged`, 0 regressions | 3 -> **4** |
| `pr-review-merge` | 11 | 3 | 5 (+3 near-miss) | P1 `candidate-diverged`, 0 regressions | 3 -> **4** |

Every required bar met: 5+ detections per skill, 2+ battle-scars correctly preserved per skill (5 and 3), a proposed rewrite for each convert-for-free pattern, and a **live, measured directness gain at zero regression per skill**. `candidate-diverged` is a passing verdict under the harness's strict no-regression gate (`summary.pass` is true iff zero `candidate-regressed`); divergences are surfaced, not failures. Both live candidates therefore satisfy directive-clarity's stricter gate: no-regression **and** a measured directness gain.

The classifier earned its keep on the hardest input in the repo: eight battle-scars across the two skills, all preserved, zero rewritten. On these mature operational skills the convert-for-free yield is modest and several "negations" are already keep-both (action adjacent) - itself a finding: a forged skill is already fairly directive-clear, and the transform's value concentrates on the few genuinely bare negations and vague pointers.

## Distribution-shift caveat

These results are valid over the two scoped scenarios M1 and P1 and the contract-level reasoning for the remaining candidates. **They are not a full end-to-end A/B of either skill** - that is gate-truncated for these infrastructure-coupled skills (see "What was live vs contract-level"). The directness gains are real and measured on the specific instructions tested; the broader detection/classification/rewrite set is reasoned against the documented contract, not live-validated, and is labelled as such throughout.

## Recommended follow-ups (NOT applied here)

Surfaced by this run, deliberately out of scope (these skills are battle-scar-dense; any rewrite is a separately-reviewed change):

1. **Apply the M-c / P-d background-CI-watcher rewrite** to both skills' "do not block on CI" lines - the highest-value convert-for-free, measured at +1 directness on M1, and the same instruction appears in both skills.
2. **Apply the P-j vague-pointer rewrite** to `pr-review-merge`'s DIRTY-handling line, recovering the threshold from the conflict-resolution patterns directly above it - measured +1 directness on P1.
3. **Gate-scripting for the runner harness** (`skills/ab-equivalence/references/runner-prompt.md`) to drive runners past sequential interactive gates, enabling a full end-to-end A/B of infrastructure-coupled and interactive skills. Already tracked from the distillation acceptance; this run is a second data point that wants it.
