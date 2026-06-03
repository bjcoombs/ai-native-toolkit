# Battle-scar classifier - the precision safeguard

What this doc does: decide which detected negations are **convert-for-free** (rewrite into a positive directive) and which are **battle-scars** (preserve verbatim), *without* a human confirmation on every pattern. This is the precision half of directive-clarity: detection (`directive-clarity-patterns.md`) over-includes on purpose; this classifier holds back the prohibitions whose wording is the load-bearing content.

## Why this exists

A battle-scar is a prohibition earned from a specific past failure - "Never run source-of-truth write commands as parallel background jobs", carrying "validated in the 047-security-audit marathon where 10 background `add-task` calls created tasks on wrong tags". Rewriting it to a breezy positive ("run write commands sequentially") risks dropping the failure-mode that justifies the rule and tells the reader *how seriously* to take it. The negation is not latent action to be optimized away; it is the knowledge.

Routing every negation through human confirmation would defeat the transform - it must run autonomously over a whole document. So the classifier decides automatically, gated to a **precision target: under 10% false positives** (battle-scars incorrectly rewritten). When the heuristics leave a case genuinely uncertain, the default is **preserve** - a preserved convert-for-free instruction costs a little directness; a rewritten battle-scar loses earned safety knowledge. The asymmetry sets the default.

## Classification heuristics, ordered by confidence

Apply in order. The first that fires decides. A negation reaches "convert-for-free" only by passing every tier without a battle-scar signal firing.

### Tier 1: Explicit failure-mode marker (high-confidence battle-scar)

If the instruction (or its immediate surrounding text) contains a marker tying the rule to a real past event or consequence, classify **battle-scar, preserve**.

**Markers:** "because", "we learned", "after", "the hard way", "incident", "regression", "validated in", "this happened", "one strike", "wastes a recovery cycle", a named past event, a parenthetical consequence ("- deletes the branch of a PR that never merged").

These are near-certain: the author has already documented *why* the prohibition exists, and that why is exactly what a rewrite would drop.

### Tier 2: Specificity test (medium-confidence)

Ask: does the negation name a **specific bad outcome or specific condition**, or is it a **generic prohibition**?

- **Generic -> convert-for-free.** "never merge PRs that fail CI" forbids the obvious; the positive action ("wait for green CI before merging") is mechanical and loses nothing.
- **Specific -> likely battle-scar, preserve.** "never merge while CodeRabbit has unresolved threads" names a specific actor and a specific condition that reads as earned from experience. The specificity is a signal the rule encodes a particular failure even when the marker is implicit.

When the specificity is borderline, fall to Tier 3 rather than guessing.

### Tier 3: Rewrite reversibility (lower-confidence tiebreak)

Attempt the rewrite, then read it back: does the positive directive carry **everything** the original conveyed, including scope and severity? If the rewrite is **information-lossless and reversible** (you could reconstruct the original prohibition from it), it is safe to convert. If the rewrite **drops a qualifier, a severity signal, or a rationale** you cannot recover, the original held content the positive form cannot - preserve it.

Reversibility failure example: "one strike, don't give sonnet a second chance on the same task" -> "respawn failed sonnet tasks on opus" drops *one strike* (the severity: do not retry sonnet at all) and the *same task* scope. Not reversible. Preserve.

### Tier 4: Uncertainty default

If no tier produces a confident verdict, **preserve**. State the uncertainty in the report so a human can review the held-back set if they choose. Preserving is the cheap error; rewriting a scar is the expensive one.

## Worked classification: 10 real negations from the toolkit's skills

Five preserve, five convert. Each shows the deciding tier.

### Preserve (battle-scars)

| # | Negation | Source | Deciding tier | Why preserve |
|---|----------|--------|---------------|--------------|
| 1 | "Never run source-of-truth write commands as parallel background jobs" | `marathon` | T1 | "validated in the 047-security-audit marathon where 10 background `add-task` calls created tasks on wrong tags" - explicit incident |
| 2 | "one strike, don't give sonnet a second chance on the same task" | `marathon` | T1/T3 | "one strike" severity marker; rewrite drops the no-retry severity |
| 3 | "Never chain cleanup unconditionally after the merge command" | `pr-review-merge` | T1 | carries the consequence: "deletes the branch/worktree of a PR that never merged" |
| 4 | "Don't merge an AI-authored docs/content PR while its AI reviewer is still pending" | `marathon` | T2 | specific actor + specific condition; reads as earned, not generic |
| 5 | "Haiku cannot reliably handle review loops - never use for teammates" | `marathon` | T2 | names a specific capability limit; the fact is the rationale |

### Convert-for-free

| # | Negation | Source | Deciding tier | Positive rewrite |
|---|----------|--------|---------------|------------------|
| 6 | "never use `gh --jq` with complex filters" | `marathon` | T2/T3 | "pipe `gh` output to `jq` separately for complex filters" (qualifier kept) |
| 7 | "Do not block on CI yourself" | `pr-review-merge` | T2 | "spawn a background agent to watch CI; continue other work meanwhile" |
| 8 | "don't create additional PRs" | `marathon` | T2 | "keep all work on your one branch; note related work in the PR description" |
| 9 | "never merge PRs that fail CI" (generic form) | `pr-review-merge` | T2 | "wait for green CI before merging" |
| 10 | "Do NOT resolve human threads" | `pr-review-merge` | T3 | "leave human threads open; let the reviewer confirm the fix and resolve" - reversible, lossless |

Note #6: the *reason* zsh mangles `!=` is a battle-scar-flavoured rationale, but the prohibition itself converts cleanly as long as the rewrite keeps "complex filters". Tier 3 passes it because the rewrite is reversible. This is the boundary case: convert the directive, keep the adjacent rationale sentence intact rather than folding it away.

## Precision accounting

The target is **under 10% false positives** - battle-scars wrongly converted. Over this 10-case set, zero false positives is the pass bar; the asymmetric default (preserve on uncertainty) is what holds the rate down. The cost model: a false positive (rewriting a scar) silently drops earned safety knowledge and is caught only if a human re-reads; a false negative (preserving a convert-for-free) costs a few tokens of directness and nothing else. Tune every borderline call toward preserve.
