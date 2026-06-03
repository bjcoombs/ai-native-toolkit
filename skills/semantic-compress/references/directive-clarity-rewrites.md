# Directive-clarity rewrites - transformation rules

What this doc does: turn each latent-action pattern (`directive-clarity-patterns.md`) into a concrete directive that names the action, under two hard checks - **names-the-concrete-action** and **semantic-equivalence** - with conservative safeguards for battle-scars. The preserve-vs-rewrite decision is made first by `battle-scar-classifier.md`; this doc rewrites only what the classifier releases.

## Precondition: classify before rewriting

Every detected pattern passes through the battle-scar classifier before reaching a rewrite rule. **Battle-scars are flagged for preservation, not rewritten.** A battle-scar's prohibition is the load-bearing content - the earned knowledge of a specific past failure - and a positive paraphrase can drop the failure-mode that justifies it. This doc assumes its input is the convert-for-free set the classifier released. It never overrides a preserve flag.

## The two acceptance checks

Every rewrite must pass both, or it is rejected and the original is kept:

1. **Names-the-concrete-action.** The output contains an imperative naming the action to take and its object. "wait for CI" passes; "be careful with CI" does not. If the rewrite cannot name a concrete action, the action site is genuinely absent from the document - escalate to human confirmation rather than invent one.
2. **Semantic-equivalence.** The output must induce the same behaviour as the original across the transfer set. This is not an inspection check - it is the A/B equivalence gate (`skills/skill-forge/references/ab-equivalence.md`). A rewrite that reads cleaner but permits a behaviour the original forbade is a regression, and the gate fails it. Directive-clarity's acceptance is stricter than compression's: no-regression **and** a measured directness gain (`candidate_directness` > `original_directness`, no `candidate-regressed`).

A rewrite that loses the original's scope is the characteristic failure. "Never use `gh --jq` with complex filters" rewritten to "always pipe `gh` to `jq`" drops "with complex filters" and over-broadens the rule. The equivalence check catches this; keep the qualifier.

## Rule 1: Bare negation -> positive imperative with object

Replace the prohibition with the positive action it implies, naming the object. Keep any qualifier that scopes the prohibition.

| Original (latent) | Rewrite (directive) |
|-------------------|---------------------|
| "never merge while X is pending" | "wait for X to finish before merging" |
| "Do not block on CI yourself" | "spawn a background agent to watch CI; continue other work meanwhile" |
| "don't create additional PRs" | "keep all work on your one branch; note related work in the PR description" |
| "never use `gh --jq` with complex filters" | "pipe `gh` output to `jq` separately for complex filters" |

Where the original prohibition and its positive action are *both* useful (the prohibition warns, the action directs), keep both: "X is forbidden; do Y instead." Do not delete the prohibition merely because the rewrite adds the action - that is a judgement the equivalence gate, not the rewriter, is allowed to make.

## Rule 2: Fact-not-action -> explicit consequence directive

Append the action the fact obliges, as an imperative, keeping the fact as its justification.

| Original (fact) | Rewrite (fact + directive) |
|-----------------|----------------------------|
| "CodeRabbit resolves its own threads" | "let CodeRabbit resolve its threads; push code changes instead of replying" |
| "idle teammate != dead teammate" | "an idle teammate may still have running subagents - check the worktree for recent file changes before killing it" |
| "GitHub returns UNKNOWN even when checks pass" | "on UNKNOWN with green CI and zero unresolved threads, retry up to 3 times, then treat as CLEAN" |

Keep the fact. The fact is the *why* - it is often bespoke project knowledge the model cannot reconstruct, and dropping it strips the directive of its rationale. The rewrite adds the imperative; it does not replace the fact with the imperative.

## Rule 3: Vague pointer -> concrete location / action (confirmation-gated)

Replace the gesture with the named site and action. **This class frequently requires human confirmation**, because the concrete referent is information the document never supplied - the rewriter cannot invent a file path or a threshold that was never stated.

| Original (vague) | Rewrite (concrete) | Confirmation |
|------------------|--------------------|--------------|
| "report blocked if genuinely ambiguous" | "report blocked when the merge conflict touches logic in both branches; auto-resolve pure import/format conflicts" | needed - the threshold was never stated |
| "handle errors appropriately" | (no rewrite possible) | needed - no action site exists in the document |
| "configured elsewhere" | "configured in `.taskmaster/config.json`" | needed unless the path appears in the document |

Decision rule for this class: if the concrete site/threshold is recoverable from elsewhere in the same document, rewrite and let the equivalence gate confirm. If it is not, **flag for human confirmation and leave the original in place** - never fabricate a referent. A fabricated path passes the names-the-concrete-action check and fails reality.

## Rule 4: Ordering / policy rule -> concrete ordered steps

Unpack the constraint into the numbered sequence it implies. Both polarities ("X before Y", "never X while Y") become the same ordered steps.

| Original (constraint) | Rewrite (sequence) |
|-----------------------|--------------------|
| "cleanup only after `state == MERGED`; never chain them" | "1. run the merge. 2. confirm `state == MERGED`. 3. only then remove the worktree and branch" |
| "stage changes but don't push yet while CI runs" | "fix locally and `git add`; hold the push until CI finishes, then push once" |
| "check for an existing PR before creating one" | "1. query open PRs on the branch. 2. reuse an open one; skip a merged one. 3. otherwise create the PR" |

Ordering rules carry battle-scars more often than any other pattern (the `state == MERGED` rule exists because chained cleanup once deleted the branch of an unmerged PR). When the classifier flags the ordering rule as a battle-scar, preserve the original wording; the failure-mode it encodes outweighs the directness gain.

## What this transform never does

- It never rewrites a flagged battle-scar. Preservation wins over directness.
- It never drops a qualifier that scopes a prohibition ("with complex filters", "while CI runs"). Over-broadening is a regression.
- It never drops the fact behind a fact-not-action rewrite. The fact is the rationale and often bespoke.
- It never fabricates a concrete referent for a vague pointer. Absent a real site, it flags for human confirmation and keeps the original.
- It never accepts a rewrite on inspection. Acceptance is the A/B equivalence gate: no-regression and a measured directness gain.
