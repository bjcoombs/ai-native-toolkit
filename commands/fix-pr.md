# Fix PR Autonomously

Enter autonomous PR review loop for the current branch's PR. Loops until the PR is merge-ready across ALL criteria — not just CI.

---

## Ready Criteria (ALL must be true)

1. **Branch in sync** — no merge conflicts with develop
2. **CI passing** — all checks succeed (or skipped)
3. **All inline comments addressed** — see thread resolution rules
4. **No unaddressed conversation comments** — actionable feedback responded to
5. **All review threads resolved** — no unresolved threads remain

**Thread resolution rules:**
- **CodeRabbit threads**: Fix the code and push. CodeRabbit re-reviews automatically and resolves its own threads. **NEVER reply in CodeRabbit threads** — CodeRabbit ignores replies from other bots.
- **claude[bot] threads**: Resolve directly via GraphQL if addressed. Use jq JSON builder (avoids zsh `$` escaping):
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

### Step 1: Sync with develop (FIRST, every iteration)

```bash
git fetch origin develop && git merge origin/develop --no-edit
# If conflicts: resolve them, commit, push
# If can't auto-resolve: report blocked with details
```

**Conflict resolution patterns:**
- **Import/route files** (e.g., App.tsx, index.ts): Accept BOTH sides — additions are additive
- **Barrel exports** (e.g., shared/index.ts): Accept both sides — each adds its own export
- **Config/manifest files**: Accept both sides unless the same key is modified differently (then ask)

### Step 2: Check all criteria concurrently

```bash
PR=$(gh pr view --json number --jq '.number')

# Start CI watch in background
gh pr checks $PR --watch --fail-fast &
CI_PID=$!

# While CI runs, check threads and comments (don't wait for CI)
# Unresolved review threads (covers inline comments from all reviewers)
gh api graphql -f query='query { repository(owner: "<owner>", name: "<repo>") {
  pullRequest(number: '$PR') { reviewThreads(first: 50) { nodes {
    id isResolved path line comments(first: 1) { nodes { author { login } body } }
  }}}}}' --jq '.data.repository.pullRequest.reviewThreads.nodes[]
  | select(.isResolved == false)
  | {id, author: .comments.nodes[0].author.login, path, line, body: .comments.nodes[0].body[0:200]}'

# For each unresolved thread with a path, check current local code to see if already fixed:
# Read the local file at the referenced line - if the concern is addressed in the current
# working tree, resolve the thread directly (for bot threads) instead of pushing and waiting
# for a re-review cycle.
# Example: sed -n '<line-5>,<line+5>p' <path>

# Conversation comments
gh pr view $PR --comments
```

### Step 3: Fix and batch

**Fix issues locally while CI runs** - stage changes but don't push yet:
- Unresolved bot threads (CodeRabbit, claude[bot]) - **Check local code first** at the referenced path:line. If already addressed, resolve via GraphQL immediately (no push needed). If not, fix the code and `git add`.
- Unresolved human threads - Fix code, reply inline, @mention reviewer
- Actionable conversation comments - Respond or fix
- Merge conflicts - Resolve using patterns above, or report blocked if ambiguous

**When CI finishes** (`wait $CI_PID`):
- CI passed + no local fixes staged: evaluate whether all 5 criteria are met
- CI passed + local fixes staged: push once (batches all thread fixes into one CI cycle)
- CI failed: fix CI issues too, then push everything together
- **ALL 5 criteria met** - Report ready, STOP

**This batches fixes into fewer pushes.** A 15-min CI run where CodeRabbit posts at minute 2 no longer wastes 13 minutes idle - thread fixes are ready before CI finishes, saving a full CI cycle.

---

## Protocol

1. **Autonomous Loop** (DO NOT ask for permission between iterations):
   - Sync develop, check all 5 criteria, fix issues, push
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
   ⚠️ Merge conflict with develop in App.tsx — resolving (accept both sides)
   ❌ CI: TypeScript errors in user.service.ts
   ❌ CodeRabbit: 2 unresolved threads (missing error handling)
   Fixing...

🔄 Iteration 2: Checking PR #123...
   ✅ In sync with develop
   ✅ CI passing
   ❌ CodeRabbit: 1 remaining thread (async pattern)
   Fixing...

🔄 Iteration 3: Checking PR #123...
   ✅ In sync with develop
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
- **Sync develop FIRST** every iteration — prevents cascade conflicts
- **Stop criteria**: ALL 5 criteria green (not just CI + comments)
- **Max iterations**: 10 (ask for help after that)
