---
name: pr-review-merge
description: >
  Drive a single pull request to merge-ready across all five criteria (sync, CI,
  inline comments, conversation, threads), then smart-merge it. Source-agnostic
  library skill invoked by the /tm, /issues, /fix-pr, and /fix-develop commands and
  by marathon teammates. TRIGGER when a command or agent needs the PR review-to-green
  loop or the smart-merge (stale-bot-CR dismissal, auto-merge criteria, UNSTABLE/UNKNOWN
  handling, merge ordering), or when the user asks to take a PR to green/merge it.
---

# PR Review-to-Green + Smart Merge

Source-agnostic. Consumers pass: PR number, base branch, and bot-reviewer/CI rules
from the project's `## Marathon Configuration` (defaults if absent).

## Ready Criteria (ALL must be true)

The PR is merge-ready only when all five are simultaneously true. Re-check from the top after every push — a fix can reopen an earlier criterion.

1. **Branch in sync** — no merge conflicts with base branch
2. **CI passing** — all checks succeed (or skipped)
3. **All inline comments addressed** — see thread resolution rules
4. **No unaddressed conversation comments** — actionable feedback responded to
5. **All review threads resolved** — no unresolved threads remain

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
- CI passed + no local fixes staged: evaluate whether all 5 criteria are met
- CI passed + local fixes staged: push once (batches all thread fixes into one CI cycle)
- CI failed: fix CI issues too, then push everything together, spawn new background watcher
- **ALL 5 criteria met** - Report ready, STOP

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
4. **Base branch is healthy** — if the PR's CI failures exist on `$BASE_BRANCH` too (pre-existing), do NOT merge and compound the problem. Instead, spawn a separate worktree/PR to fix the failing tests on `$BASE_BRANCH` first, then rebase and merge the original PR.

**UNSTABLE handling:** If `mergeStateStatus == "UNSTABLE"`, check failing checks against `meta.flaky_checks` and any CI patterns from the project's Marathon Configuration. If ALL failing checks are non-required AND not pre-existing on `$BASE_BRANCH`, treat as merge-eligible. Report: "Merging with UNSTABLE — only non-required checks failing: <names>". If failures ARE pre-existing on `$BASE_BRANCH`, fix it first (criterion 4).

**UNKNOWN handling:** GitHub sometimes returns `mergeStateStatus: "UNKNOWN"` even when all checks pass. If UNKNOWN but CI all green and 0 unresolved threads, retry up to 3 times with 30s backoff. If still UNKNOWN after retries, treat as CLEAN and proceed (log the override).

**Verify before merging** — never trust teammate claims:
```bash
gh pr view $PR --json state,mergedAt,mergeStateStatus | jq '{state, mergedAt, mergeStateStatus}'
```
Trust the API, not the message.

**After merge:**

Close the work unit via the consumer's **close on merge** adapter operation, then remove its worktree and delete its branch using the consumer's branch/worktree convention:

```bash
git worktree remove --force <worktree-path-for-this-unit>
git branch -D <branch-for-this-unit>
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
