# Fix PR Autonomously

Enter autonomous PR review loop for the current branch's PR. Loops until the PR is merge-ready across ALL criteria — not just CI.

---

## Ready Criteria (ALL must be true)

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

---

## Shell Pitfalls
**Never use `gh ... --jq` with complex filters.** Always pipe to `jq` separately.

**Use positive jq filters, not negative.** zsh escapes `!=` to `\!=`, breaking filters silently:
```bash
# WRONG: gh pr view --json reviews --jq '.reviews[] | select(.state != "APPROVED")'
# WRONG: gh pr view --json reviews | jq '.reviews[] | select(.state != "APPROVED")'
# RIGHT:
gh pr view --json reviews | jq '.reviews[] | select(.state == "CHANGES_REQUESTED")'
```

## Each Iteration

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

---

## Protocol

1. **Autonomous Loop** (DO NOT ask for permission between iterations):
   - Sync base branch, check all 5 criteria, fix issues, push
   - Report: "Iteration N: Fixed X issues" or "Iteration N: Waiting for CI"
   - Loop until all green

2. **Stop when ALL 5 criteria met**:
   - Report: "PR #X: All criteria met, ready for human review"
   - Include status footer
   - STOP

3. **Only ask for help if**:
   - Genuinely blocked (ambiguous merge conflict, unclear review feedback)
   - After 5+ iterations with no progress
   - Need design decision from user

## Example Output

```
🔄 Iteration 1: Checking PR #123...
   ⚠️ Merge conflict with base branch in App.tsx — resolving (accept both sides)
   ❌ CI: TypeScript errors in user.service.ts
   ❌ CodeRabbit: 2 unresolved threads (missing error handling)
   Fixing...

🔄 Iteration 2: Checking PR #123...
   ✅ In sync with base branch
   ✅ CI passing
   ❌ CodeRabbit: 1 remaining thread (async pattern)
   Fixing...

🔄 Iteration 3: Checking PR #123...
   ✅ In sync with base branch
   ✅ CI passing
   ✅ All threads resolved
   ✅ No unaddressed comments

✅ PR #123: All 5 criteria met, ready for your review!

---
## 📍 Current Work

🔗 **PR**: #123 - Add payment processing
   https://github.com/org/repo/pull/123

📊 **Latest**: All criteria met after 3 iterations
```

## Commands Reference

```bash
# Get PR info
gh pr view --json number,title,statusCheckRollup

# CI failures
gh pr checks <number> --json name,conclusion | jq '.[] | select(.conclusion == "FAILURE")'

# Unresolved threads (GraphQL — most reliable)
gh api graphql -f query='...'

# Inline comments (REST fallback)
gh api repos/{owner}/{repo}/pulls/<number>/comments | jq '[.[] | select(.user.login | test("bot"; "i"))]'

# Conversation
gh pr view <number> --comments

# Failed CI logs
gh run view <run-id> --log-failed

# Resolve a review thread (for claude[bot] threads)
# Resolve a review thread (jq builder avoids zsh $ escaping)
jq -n --arg tid "$THREAD_ID" '{"query": "mutation { resolveReviewThread(input: {threadId: \"\($tid)\"}) { thread { isResolved } } }"}' | gh api graphql --input -
```

## Important

- **NO permission needed** between iterations — keep looping autonomously
- **Sync base branch FIRST** every iteration — prevents cascade conflicts
- **Stop criteria**: ALL 5 criteria green (not just CI + comments)
- **Max iterations**: 10 (ask for help after that)
