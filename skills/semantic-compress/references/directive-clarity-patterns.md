# Directive-clarity patterns - detection heuristics

What this doc does: name the instruction shapes that force a reading model to **unpack an action before it can act**, and give surface forms and worked examples concrete enough to classify a real instruction file without guessing. Detection only - rewriting is `directive-clarity-rewrites.md`, and the preserve-vs-rewrite decision is `battle-scar-classifier.md`.

## The core defect: latent action

An instruction is **directive-clear** when it names the action to take in the words the reader acts on. It has **latent action** when the reader must first run an inference step - derive the positive action from a prohibition, infer an action from a stated fact, or resolve a vague pointer - before any work happens. Latent action is a tax paid on every read. The four patterns below are the surface shapes that carry it.

A pattern match is a *candidate* for rewrite, not a verdict. Some latent-action instructions are **battle-scars** - prohibitions earned from a specific past failure, where the prohibition itself is the load-bearing content. Those are flagged for preservation, never rewritten. This doc finds candidates; the classifier decides which survive.

## Pattern 1: Bare negation

**Definition.** An instruction that states what *not* to do without naming what to do instead, leaving the reader to derive the positive action. The prohibition is explicit; the action is latent.

**Watch (surface forms):**
- "never X", "don't X", "do not X", "avoid X", "must not X"
- "X is forbidden / not allowed / off-limits"
- a prohibition with no adjacent positive clause naming the replacement action

**Worked examples (from the toolkit's own skills):**

| Source | Instruction | Why it matches | Latent action the reader must derive |
|--------|-------------|----------------|--------------------------------------|
| `marathon` | "Never use `gh --jq` with complex filters" | Bare prohibition; the replacement is stated nearby but the rule itself only forbids | "pipe `gh` output to `jq` separately" |
| `pr-review-merge` | "Do not block on CI yourself" | Forbids an action, names no alternative inline | "spawn a background agent to watch CI" |
| `marathon` | "don't create additional PRs" | Prohibition only | "mention related work in your PR description instead" |

The first is a near-miss: the positive action sits in surrounding text, so the rewrite is mechanical. The detector still flags it - whether the action is truly latent or merely adjacent is the rewriter's call.

## Pattern 2: Fact-not-action

**Definition.** A statement describing a state of the world ("X happens", "Y behaves this way") with no imperative, leaving the reader to infer what the fact obliges them to do. The fact is true and useful; the action it implies is unstated.

**Watch (surface forms):**
- present-tense descriptions of a tool/system/actor behaviour: "CodeRabbit resolves its own threads", "GitHub returns UNKNOWN even when checks pass"
- equivalence/inequality framings stating a truth: "idle teammate != dead teammate"
- a sentence the reader is clearly meant to *act on* but which contains no verb directed at the reader

**Worked examples:**

| Source | Statement | Latent action |
|--------|-----------|---------------|
| `CLAUDE.md` | "CodeRabbit re-reviews and resolves its own threads" | "let CodeRabbit resolve its threads; push code changes instead of replying" |
| `marathon` | "Idle teammate != dead teammate" | "check the worktree for subagent activity before killing an idle teammate" |
| `pr-review-merge` | "GitHub sometimes returns `mergeStateStatus: UNKNOWN` even when all checks pass" | "on UNKNOWN with green CI and zero unresolved threads, retry up to 3 times then treat as CLEAN" |

The fact-not-action pattern is the highest-value catch: a fact reads as informational, so the reader may not register that an action is owed at all.

## Pattern 3: Vague pointer

**Definition.** An instruction that gestures at a location or response without naming a concrete one - it says *that* the reader should act, or *not* act here, without saying *where* or *how*. The action site is latent.

**Watch (surface forms):**
- "handle it appropriately", "as needed", "where relevant", "if necessary"
- "elsewhere", "somewhere else", "the right place"
- "report blocked if ambiguous" without a test for ambiguous
- any deictic ("here", "this", "that") whose referent the reader must reconstruct

**Worked examples:**

| Source | Pointer | Why vague |
|--------|---------|-----------|
| `pr-review-merge` | "report blocked if genuinely ambiguous" | "ambiguous" names no test; the reader guesses the threshold |
| generic | "handle it appropriately" | no object, no action, no site |
| generic | "configured elsewhere / as needed" | names where *not* to look without naming where to |

Vague pointers most often need human confirmation to resolve, because the concrete site is information the document never supplied. The rewrite rules flag this class as confirmation-gated.

## Pattern 4: Ordering / policy rule

**Definition.** A rule constraining *when* an action may happen relative to another ("X before Y", "never X while Y"), stated as a constraint rather than as the concrete ordered steps. Both polarities - the positive "before" and the negative "never while" - require the reader to unpack the constraint into an actual sequence.

**Watch (surface forms):**
- "X before Y", "only after Z", "not until W"
- "never X while Y", "don't X until Y is done"
- "get to green before refactoring", "stage changes but don't push yet"

**Worked examples:**

| Source | Rule | Sequence the reader must unpack |
|--------|------|---------------------------------|
| `marathon` | cleanup "only after confirming `state == MERGED`. Never chain them" | "1. merge. 2. confirm `state == MERGED`. 3. only then clean up" |
| `pr-review-merge` | "stage changes but don't push yet" while CI runs | "fix locally -> `git add` -> hold the push until CI finishes -> batch one push" |
| `marathon` | "Before creating PR, check for existing" | "1. query open PRs on the branch. 2. reuse if open, skip if merged. 3. otherwise create" |

Ordering rules are frequently **battle-scars**: the `state == MERGED` rule carries an explicit failure-mode ("a rejected merge with chained cleanup deletes the branch/worktree of a PR that never merged"). The detector flags the ordering shape; the classifier preserves the scar.

## Classification self-test

A correct application of these heuristics over the `marathon` and `pr-review-merge` skills must:

- flag **3+ convert-for-free negations** - e.g. "never use `gh --jq` with complex filters", "do not block on CI yourself", "don't create additional PRs". Generic prohibitions whose positive action is mechanical to name.
- preserve **2+ battle-scars** - e.g. "one strike, don't give sonnet a second chance on the same task" (explicit earned rule) and "Never run source-of-truth write commands as parallel background jobs" (carries "validated in the 047-security-audit marathon where 10 background `add-task` calls created tasks on wrong tags"). Flagged by Pattern 1/4 shape, held back by the classifier.
- flag **1+ vague pointer** - e.g. "report blocked if genuinely ambiguous".

A detector that rewrites the two battle-scars, or that misses the convert-for-free negations, has failed. The detector's job is recall (find every latent-action shape); the classifier's job is precision (decide which to rewrite). Detection over-includes on purpose.
