---
name: pr-review-merge
description: >
  Drive a single pull request to merge-ready across all six criteria (sync, CI,
  inline comments, conversation, threads, bot re-review of the head commit), then
  smart-merge it. Source-agnostic
  library skill invoked by the /tm, /issues, /fix-pr, and /fix-develop commands and
  by marathon teammates. TRIGGER when a command or agent needs the PR review-to-green
  loop or the smart-merge (stale-bot-CR dismissal, auto-merge criteria, UNSTABLE/UNKNOWN
  handling, merge ordering), or when the user asks to take a PR to green/merge it.
---

<!-- floor:cold-verify-completion -->

# PR Review-to-Green + Smart Merge

Source-agnostic. Consumers pass: PR number, base branch, and bot-reviewer/CI rules
from the project's `## Marathon Configuration` (defaults if absent).

## Run-completion is gated elsewhere (floor)

This skill takes one PR to merge — a **process** signal. A green, merged PR is not proof the run's assembled product works, and this skill never certifies run-completion. That is gated by the acceptance-contract scripts the marathon engine invokes, not here: `scripts/contract/start_gate.py` fails closed at run start unless the contract is frozen before decomposition, `scripts/contract/spawn_verifier.py` is the sole custody chokepoint that spawns the cold non-implementing verifier against the assembled product, and `scripts/contract/complete_gate.py` fails closed unless that verifier's completion record validates. Merging here never substitutes for those gates. This note is part of the constitutional floor (`FLOOR.md`); the retro may propose changes but never self-apply them.

## Ready Criteria (ALL must be true)

The PR is merge-ready only when all six are simultaneously true. Re-check from the top after every push — a fix can reopen an earlier criterion.

1. **Branch in sync** — no merge conflicts with base branch
2. **CI passing** — all checks succeed (or skipped)
3. **All inline comments addressed** — see thread resolution rules
4. **No unaddressed conversation comments** — actionable feedback responded to
5. **All review threads resolved** — no unresolved threads remain
6. **Re-reviewing bots have reviewed the head SHA** - every bot the project's Marathon Configuration flags `Re-reviews on push: yes` has completed its pass on the current head SHA. For a bot without `Re-review check name`, its latest review must carry that head SHA as its `commit_id`; a review of an earlier commit does not count. For a bot configured with `Re-review check name`, only the check run of that name on the head SHA decides (its submitted reviews do not: the check run is what turns red when the reviewer exits with an error after posting), and that run has three observable states: **in progress** (or not yet created) - keep waiting until the max wait expires; **completed with conclusion `success`** - the criterion is satisfied for that bot, and the marathon skill's `Commit:`-line spot check still applies before merging on it; **completed with any other conclusion** (failure, cancelled, skipped, neutral, timed_out) - a settled verdict that the bot did not complete a green pass, so the criterion takes the warning path at once. Any push restarts the wait. The wait is bounded per bot by that bot's `Max wait for re-review`, measured from when the head commit was pushed (the earliest check-suite creation on the head SHA, not the commit's committer date, which predates the push and resets on rebase); when `Re-reviews on push: yes` and `Max wait for re-review` is absent, the bound is 15m. If no check suite exists on the head SHA, fall back to the head commit's committer date as a floor - explicitly not the push time, it only guarantees the bound always expires. When the max wait expires before the bot completes, or a check-name bot's run settles on a non-success conclusion, the criterion passes with a warning recorded in the merge record (the PR comment or report that accompanies the merge) naming the bot, the head SHA, and what was observed: for a check-name bot, the check run's conclusion on that SHA (or that no completed run exists) and, so the record never contradicts the reviews endpoint, whether path (a) shows a review of that head SHA anyway; for a review bot, that no review of that SHA was found. The record states that the bot did not complete a green pass, not that it did not review. Merge proceeds with that warning; nothing holds forever on an advisory bot. Bots without `Re-reviews on push: yes` are never waited on, so a project with no such flags sees no change.

**Checking criterion 6** (per flagged bot; `<bot-login>` and the optional `<check-name>` from its `Re-review check name` field in the Marathon Configuration):
```bash
HEAD_SHA=$(gh pr view $PR --json headRefOid | jq -r '.headRefOid')
# Push time of the head commit: GitHub creates check suites on push.
# --paginate emits one JSON page after another; jq -s gathers them so min spans all pages.
PUSHED_AT=$(gh api --paginate "repos/<owner>/<repo>/commits/$HEAD_SHA/check-suites?per_page=100" \
  | jq -r -s '[.[].check_suites[].created_at] | min // empty')
# No check suite on the head SHA (min of an empty list is null): use the committer date as a
# floor so the max wait always expires. This is not the push time; it predates the push.
if [ -z "$PUSHED_AT" ]; then
  PUSHED_AT=$(gh api "repos/<owner>/<repo>/commits/$HEAD_SHA" | jq -r '.commit.committer.date')
fi
# (a) Bot that submits reviews: its reviews whose commit_id is the head SHA.
# Reviews page in ascending order, so the newest head-SHA review sits on the last page:
# --paginate reads every page and jq streams matches from each one.
gh api --paginate "repos/<owner>/<repo>/pulls/$PR/reviews?per_page=100" \
  | jq -r --arg sha "$HEAD_SHA" --arg bot "<bot-login>" '.[] | select(.commit_id == $sha and .user.login == $bot) | .submitted_at'
# (b) Bot hosted as a check run (Re-review check name set): matched by check-run name.
# Prints every run of that name as "<status> <conclusion> <completed_at>", whatever its outcome.
gh api --paginate "repos/<owner>/<repo>/commits/$HEAD_SHA/check-runs?per_page=100" \
  | jq -r --arg n "<check-name>" '.check_runs[] | select(.name == $n) | "\(.status) \(.conclusion // "in_progress") \(.completed_at // "")"'
```
Complete when, for a bot with `Re-review check name` set, path (b) prints a line whose conclusion is `success` (path (a) does not decide for that bot, and its output is recorded only on the warning path); a `completed` line with any other conclusion is the settled non-success case, which takes the warning path at once; no line, or only lines whose status is not `completed`, means keep waiting until the max wait expires. For a bot without one, complete when path (a) prints at least one line (a head-SHA review), and otherwise keep waiting until the max wait expires. The max wait is `Max wait for re-review` since `PUSHED_AT`, 15m when that field is absent. The filters use `--arg` and pipe to `jq` rather than `gh api --jq`, per the Shell Pitfalls below. Never match check runs by app slug: every GitHub Actions job, the reviewer included, reports as `github-actions`, so an unrelated job completing would pass the criterion. Merge proceeds with the warning on expiry or on a settled non-success run. A green reviewer check still warrants the spot-check the marathon skill describes (the reviewer's summary cites the head SHA) before merging on the strength of it.

**Thread resolution rules:**
Follow bot reviewer rules from the project's CLAUDE.md Marathon Configuration. Generic defaults:
- **Bot threads**: Fix the code and push. Resolve via GraphQL if addressed. Use jq JSON builder (avoids zsh `$` escaping):
  ```bash
  jq -n --arg tid "$THREAD_ID" '{"query": "mutation { resolveReviewThread(input: {threadId: \"\($tid)\"}) { thread { isResolved } } }"}' | gh api graphql --input -
  ```
- **Human threads**: Fix the code, reply inline explaining the fix, `@mention` the reviewer. Do NOT resolve human threads — let the reviewer confirm.

## Shell Pitfalls

**Never use `gh ... --jq` with complex filters.** Always pipe to `jq` separately.

**Use positive jq filters, not negative.** zsh escapes `!=` to `\!=`, breaking filters silently:
```bash
# WRONG: gh pr view --json reviews --jq '.reviews[] | select(.state != "APPROVED")'
# WRONG: gh pr view --json reviews | jq '.reviews[] | select(.state != "APPROVED")'
# RIGHT:
gh pr view --json reviews | jq '.reviews[] | select(.state == "CHANGES_REQUESTED")'
```

## Review Loop (each iteration)

### Step 1: Sync with base branch (FIRST, every iteration)

```bash
BASE=$(gh repo view --json defaultBranchRef --jq '.defaultBranchRef.name' 2>/dev/null || echo "main")
git fetch origin $BASE && git merge origin/$BASE --no-edit
# If conflicts: resolve them, commit, push
# If can't auto-resolve: report blocked with details
```

**Resolving a `.claude-plugin/plugin.json` conflict (the common one in multi-PR waves).** The conflict is **not always confined to `.version`** — a sibling PR may also have reframed the marketplace `description` or another field. A version-only line edit (`sed`-ing just the version line, or a regex that targets only `.version`) silently keeps the stale side of *every other* field and can leave merge markers behind. Resolve the **whole file** deterministically: take the side carrying the siblings' already-merged field changes (usually base), then overwrite only the version with `jq`:

```bash
git checkout --theirs .claude-plugin/plugin.json   # base side, which has the merged siblings' description/other-field edits
jq --arg v "<your-next-version>" '.version = $v' .claude-plugin/plugin.json > /tmp/pj && mv /tmp/pj .claude-plugin/plugin.json
git add .claude-plugin/plugin.json
```

Always `git diff` the full `plugin.json` before resolving — never assume the only divergence is the version line.

**Conflict resolution patterns:**
- **Import/route files** (e.g., App.tsx, index.ts): Accept BOTH sides — additions are additive
- **Barrel exports** (e.g., shared/index.ts): Accept both sides — each adds its own export
- **Config/manifest files**: Accept both sides unless the same key is modified differently (then ask)

### Step 2: Check all criteria - delegate CI to background agent

```bash
PR=$(gh pr view --json number --jq '.number')
```

**Spawn a background agent to watch CI** (never block on CI yourself):
```
Agent(
  run_in_background: true,
  prompt: """
  Monitor PR #$PR for CI completion and review state.

  1. Block on CI: `gh pr checks $PR --watch --fail-fast`
  2. When CI settles, gather:
     - CI result: `gh pr checks $PR --json name,state,conclusion`
     - Unresolved threads: `gh api graphql ...` (all unresolved with path/line/author/body)
     - Conversation comments: `gh pr view $PR --comments --json comments`
     - Pending checks count
  3. Return structured report.
  """
)
```

**While CI runs (you are FREE)**, do an immediate check for threads and comments:
```bash
# Quick thread check - fix what you can now
# Substitute the repo owner/name the consumer passed in.
gh api graphql -f query='query { repository(owner: "<owner>", name: "<repo>") {
  pullRequest(number: '$PR') { reviewThreads(first: 50) { nodes {
    id isResolved path line comments(first: 1) { nodes { author { login } body } }
  }}}}}' --jq '.data.repository.pullRequest.reviewThreads.nodes[]
  | select(.isResolved == false)
  | {id, author: .comments.nodes[0].author.login, path, line, body: .comments.nodes[0].body[0:200]}'

# For each unresolved thread with a path, check local code to see if already fixed.
# If addressed, resolve bot threads via GraphQL immediately (no push needed).

# Conversation comments
gh pr view $PR --comments
```

### Step 3: Fix and batch

**Fix issues locally while CI runs** - stage changes but don't push yet:
- Unresolved bot threads - **Check local code first** at the referenced path:line. If already addressed, resolve via GraphQL immediately (no push needed). If not, fix the code and `git add`. Follow bot reviewer rules from CLAUDE.md Marathon Configuration.
- Unresolved human threads - Fix code, reply inline, @mention reviewer
- Actionable conversation comments - Respond or fix
- Merge conflicts - Resolve using patterns above, or report blocked if ambiguous

**When background agent notification arrives** (CI settled):
- CI passed + no local fixes staged: evaluate whether all six criteria are met, including criterion 6 (every bot flagged `Re-reviews on push: yes` has reviewed the head SHA, or its `Max wait for re-review` has expired)
- CI passed + local fixes staged: push once (batches all thread fixes into one CI cycle)
- CI failed: fix CI issues too, then push everything together, spawn new background watcher
- **All six criteria met** - Report ready (name any bot whose max wait expired so the merge record carries the warning), STOP

**This keeps you responsive.** While CI runs, you process threads and comments. When CI settles, you act on the full report. No blocking waits.

## Smart Merge

Invoked to merge a PR that has been reported merge-ready. Always verify via the API before merging — never trust the report.

**Known: Stale bot CHANGES_REQUESTED.** Some bot reviewers submit CR reviews that GitHub does not auto-dismiss on re-review. Check the project's Marathon Configuration for bot-specific patterns. Default: dismiss any stale bot CHANGES_REQUESTED before merging.

```bash
PR=<number>

# Step 1: Dismiss stale bot CRs
STALE_REVIEWS=$(gh api repos/<owner>/<repo>/pulls/$PR/reviews \
  --jq '[.[] | select(.state == "CHANGES_REQUESTED" and (.user.login | endswith("[bot]")))]')
echo "$STALE_REVIEWS" | jq -r '.[].id' | while read REVIEW_ID; do
  gh api repos/<owner>/<repo>/pulls/$PR/reviews/$REVIEW_ID/dismissals \
    --method PUT -f message="Stale bot review — verified findings addressed" -f event="DISMISS"
done

# Step 2: Check merge state
gh pr view $PR --json mergeStateStatus,mergedAt,reviews \
  | jq '{
    mergeStateStatus,
    mergedAt,
    approvals: [.reviews[] | select(.state == "APPROVED")] | length,
    changesRequested: [.reviews[] | select(.state == "CHANGES_REQUESTED")] | length
  }'
```

**Auto-Merge Criteria (ALL must be true):**
1. `mergeStateStatus` is `"CLEAN"` — OR `"UNSTABLE"` with only non-required checks failing
2. At least `$REQUIRED_APPROVALS` approvals — OR `$MARKDOWN_APPROVALS` for markdown-only PRs (some bot reviewers skip them)
3. Zero non-dismissed changes-requested reviews
4. **Re-review of the head SHA complete** - Ready Criterion 6 holds on the head SHA being merged: every bot flagged `Re-reviews on push: yes` has completed its review of that commit, or its `Max wait for re-review` expired and the merge record names the bot with a warning. Re-check at merge time; a push after the ready report restarts the wait.
5. **Base branch is healthy** — if the PR's CI failures exist on `$BASE_BRANCH` too (pre-existing), do NOT merge and compound the problem. Instead, spawn a separate worktree/PR to fix the failing tests on `$BASE_BRANCH` first, then rebase and merge the original PR.

**UNSTABLE handling:** If `mergeStateStatus == "UNSTABLE"`, check failing checks against `meta.flaky_checks` and any CI patterns from the project's Marathon Configuration. If ALL failing checks are non-required AND not pre-existing on `$BASE_BRANCH`, treat as merge-eligible. Report: "Merging with UNSTABLE — only non-required checks failing: <names>". If failures ARE pre-existing on `$BASE_BRANCH`, fix it first (criterion 5).

**UNKNOWN handling:** GitHub sometimes returns `mergeStateStatus: "UNKNOWN"` even when all checks pass. If UNKNOWN but CI all green and 0 unresolved threads, retry up to 3 times with 30s backoff. If still UNKNOWN after retries, treat as CLEAN and proceed (log the override).

**The merge command — use `--admin` on solo-maintainer repos.** When `$REQUIRED_APPROVALS` is 0 and only the named required checks gate, a plain `gh pr merge --squash` can be **refused** ("base branch policy prohibits the merge") whenever a *non-required* check (CodeRabbit, an advisory AI review, a regression gate that re-runs on base advance) is PENDING or re-running at the exact merge instant — even though `mergeStateStatus` reported CLEAN/UNSTABLE a moment earlier. Once the named required checks are all SUCCESS, merge with admin override so a mid-run non-required check can't bounce you:
```bash
gh pr merge $PR --squash --delete-branch --admin
```
Only do this once the *required* checks are green (admin override bypasses branch policy, not your own merge criteria). On multi-PR waves, a gate that re-runs on every base advance (each merge re-triggers it on the pending PRs) makes the plain-merge bounce recurring — `--admin` avoids a wait-and-retry cycle per PR.

**Verify before merging** — never trust the caller's claim that a PR is ready:
```bash
gh pr view $PR --json state,mergedAt,mergeStateStatus | jq '{state, mergedAt, mergeStateStatus}'
```
Trust the API, not the message.

**After merge — gate cleanup on a VERIFIED merge.** A merge call can be rejected (see the `--admin` note above) while a chained one-liner blindly runs cleanup anyway, deleting the worktree and branch of a PR that never merged. **Never chain cleanup unconditionally after the merge command.** Confirm `state == "MERGED"` (or `mergedAt != null`) first, then close the unit via the consumer's **close on merge** adapter operation and remove its worktree + branch:

```bash
MERGED=$(gh pr view $PR --json state --jq '.state')
if [ "$MERGED" = "MERGED" ]; then
  git worktree remove --force <worktree-path-for-this-unit>
  git branch -D <branch-for-this-unit>
else
  echo "MERGE NOT CONFIRMED ($MERGED) — skipping cleanup, retry merge"
fi
```

Report the merge to the user. Any wave/next-task orchestration after a merge is the caller's responsibility (the marathon engine handles waves and teammate lifecycle) — this skill's job ends at a clean merge + cleanup.

**If not merge-ready:**
- BLOCKED → report blocked to the caller
- DIRTY (merge conflict) → resolve using the Review Loop conflict-resolution patterns, or report blocked if genuinely ambiguous
- Human changes requested → report to the caller; do not merge

#### Merge Order (multiple PRs)

1. **Batch-dismiss stale CRs** across ALL eligible PRs first:
   ```bash
   for PR in <all-eligible-pr-numbers>; do
     gh api repos/<owner>/<repo>/pulls/$PR/reviews \
       --jq '.[] | select(.state == "CHANGES_REQUESTED" and (.user.login | endswith("[bot]"))) | .id' \
     | while read REVIEW_ID; do
       gh api repos/<owner>/<repo>/pulls/$PR/reviews/$REVIEW_ID/dismissals \
         --method PUT -f message="Stale bot review — batch dismissed" -f event="DISMISS"
     done
   done
   ```
2. **Sort by hot-file impact**: Fewer hot files first. Hot-file PRs merge LAST.
3. **Merge sequentially**: One at a time. 10-20s between for conflict detection.
4. **Re-check merge state** after each — CLEAN can flip to DIRTY from cascade.
