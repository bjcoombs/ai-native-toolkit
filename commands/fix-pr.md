# Fix PR Autonomously

Enter autonomous PR review loop for the current branch's PR. Loops until the PR is merge-ready across ALL criteria — not just CI.

---

## What this does

Drive the current branch's PR to merge-ready across all five criteria, then stop for human
review (this command does not auto-merge).

Use the pr-review-merge skill: it owns the 5 ready criteria, thread-resolution rules, shell
pitfalls, base-sync-first, and the background CI watcher. Pass the current PR number
(`gh pr view --json number --jq '.number'`) and the base branch
(`gh repo view --json defaultBranchRef --jq '.defaultBranchRef.name'`), plus any bot rules
from the project's Marathon Configuration.

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
```

## Important

- **NO permission needed** between iterations — keep looping autonomously
- **Sync base branch FIRST** every iteration — prevents cascade conflicts
- **Stop criteria**: ALL 5 criteria green (not just CI + comments)
- **Max iterations**: 10 (ask for help after that)
