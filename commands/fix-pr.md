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
- **claude[bot] threads**: Resolve directly via GraphQL `resolveReviewThread` mutation if the concern is addressed in the current code.
- **Human threads**: Fix the code, reply inline explaining the fix, `@mention` the reviewer. Do NOT resolve human threads — let the reviewer confirm.

---

## Check for Ralph Plugin

```bash
ls ~/.claude/plugins/cache/claude-plugins-official/ralph-loop/*/commands/ralph-loop.md 2>/dev/null && echo "RALPH_AVAILABLE" || echo "RALPH_NOT_AVAILABLE"
```

**If Ralph available:** Use Ralph for iteration (preserves context between iterations).

```
Skill(
  skill: "ralph-loop:ralph-loop",
  args: "Fix PR autonomously. Get PR number. EACH iteration: merge origin/develop first, then check all 5 green criteria -- no merge conflicts, CI passing, no unresolved inline comments, conversation addressed, all review threads resolved. Fix issues, commit, push, wait 60s, repeat. Output \\<promise\\>PR_READY\\</promise\\> when ALL 5 criteria are met. --max-iterations 10 --completion-promise PR_READY"
)
```

**If Ralph NOT available:** Use manual loop below (may burn context on many iterations).

---

## Shell Pitfall: gh --jq escaping
**Never use `gh ... --jq` with complex filters containing `!=`, `$`, or shell metacharacters.** Always pipe to `jq` separately:
```bash
# WRONG: gh pr view --json reviews --jq '.reviews[] | select(.state != "APPROVED")'
# RIGHT:
gh pr view --json reviews | jq '.reviews[] | select(.state != "APPROVED")'
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

### Step 2: Check remaining criteria

```bash
PR=$(gh pr view --json number --jq '.number')

# CI status
gh pr checks $PR --json state --jq '.[] | select(.state != "SUCCESS" and .state != "SKIPPED")'

# Unresolved review threads (covers inline comments from all reviewers)
# Include path and line so we can check if the concern is already fixed locally
gh api graphql -f query='query { repository(owner: "<owner>", name: "<repo>") {
  pullRequest(number: '$PR') { reviewThreads(first: 50) { nodes {
    id isResolved path line comments(first: 1) { nodes { author { login } body } }
  }}}}}' --jq '.data.repository.pullRequest.reviewThreads.nodes[]
  | select(.isResolved == false)
  | {id, author: .comments.nodes[0].author.login, path, line, body: .comments.nodes[0].body[0:200]}'

# For each unresolved thread with a path, check current local code to see if already fixed:
# Read the local file at the referenced line — if the concern is addressed in the current
# working tree, resolve the thread directly (for bot threads) instead of pushing and waiting
# for a re-review cycle.
# Example: sed -n '<line-5>,<line+5>p' <path>

# Conversation comments
gh pr view $PR --comments
```

### Step 3: Fix and loop

**Decision tree:**
- Merge conflicts → Resolve using patterns above, or report blocked if ambiguous
- CI failing → Fix code, commit, push
- Unresolved bot threads (CodeRabbit, claude[bot]) → **Check local code first** at the referenced path:line. If the concern is already addressed in the working tree, resolve the thread directly via GraphQL (saves a push→wait→re-review cycle). If not addressed, fix the code, then push.
- Unresolved human threads → Fix code, reply inline, @mention reviewer
- Actionable conversation comments → Respond or fix
- **ALL 5 criteria met** → Report ready, STOP

Wait 60s between iterations for CI to start.

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
gh api graphql -f query='mutation { resolveReviewThread(input: {threadId: "<thread-id>"}) { thread { isResolved } } }'
```

## Important

- **NO permission needed** between iterations — keep looping autonomously
- **Sync develop FIRST** every iteration — prevents cascade conflicts
- **Stop criteria**: ALL 5 criteria green (not just CI + comments)
- **Max iterations**: 10 (ask for help after that)
