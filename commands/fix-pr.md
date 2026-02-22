# Fix PR Autonomously

Enter autonomous PR fixing loop for the current branch's PR until all checks pass.

---

## Check for Ralph Plugin

```bash
ls ~/.claude/plugins/cache/claude-plugins-official/ralph-loop/*/commands/ralph-loop.md 2>/dev/null && echo "RALPH_AVAILABLE" || echo "RALPH_NOT_AVAILABLE"
```

**If Ralph available:** Use Ralph for iteration (preserves context between iterations).

**IMPORTANT**: Keep prompt SIMPLE - no multi-line args with special characters.

```
Skill(
  skill: "ralph-loop:ralph-loop",
  args: "Fix PR autonomously. Get PR number, check CI, check inline comments, check conversation. Fix issues, commit, push, wait 30s, repeat. Report each iteration. Output \\<promise\\>PR_READY\\</promise\\> when all checks pass and no unresolved comments. --max-iterations 10 --completion-promise PR_READY"
)
```

**If Ralph NOT available:** Use manual loop below (⚠️ may burn context on many iterations).

---

## Protocol (Fallback without Ralph)

1. **Initial Check** (Iteration 0):
   - Get current PR number
   - Check CI status
   - Check inline comments (CodeRabbit, bots)
   - Check conversation comments
   - Report findings

2. **Autonomous Loop** (DO NOT ask for permission):
   - **If ANY issues exist** (failures or bot comments):
     - Analyze root cause
     - Implement fixes
     - Commit with descriptive message
     - Push changes (CodeRabbit re-reviews and resolves its own threads automatically)
     - **NEVER reply in CodeRabbit threads** — CodeRabbit ignores replies from other bots
     - Wait 30 seconds for CI
     - Report: "🔄 Iteration N: Fixed X issues"
     - Check status again (GOTO step 2)

   - **If ALL passing and NO unresolved threads**:
     - Report: "✅ PR #X: All checks passing, ready for human review"
     - Include final status footer
     - STOP

3. **Only ask for help if**:
   - Genuinely blocked (cannot resolve after trying)
   - After 5+ iterations with no progress
   - Need design decision from user

## Example Output Format

```
🔄 Iteration 1: Checking PR #123...
   ❌ CI: TypeScript errors in user.service.ts
   ❌ CodeRabbit: Missing error handling in auth.ts (line 45)
   Analyzing and fixing...
   
   *commits and pushes*

🔄 Iteration 2: Checking PR #123...
   ✅ CI: All checks passing
   ❌ CodeRabbit: Async/await pattern in payment.ts (line 89)
   Fixing...
   
   *commits and pushes*

🔄 Iteration 3: Checking PR #123...
   ✅ CI: All checks passing
   ✅ No unresolved comments

✅ PR #123: All checks passing, ready for your review!

---
## 📍 Current Work

🔗 **PR**: #123 - Add payment processing
   https://github.com/org/repo/pull/123

📊 **Latest**: All checks passing after 3 autonomous iterations
```

## Commands to Use

```bash
# Get PR info
gh pr view --json number,title,statusCheckRollup

# Check CI failures
gh pr checks <number> --json name,conclusion | jq '.[] | select(.conclusion == "FAILURE")'

# Check inline comments
gh api repos/{owner}/{repo}/pulls/<number>/comments | jq '[.[] | select(.user.login | test("bot"; "i"))]'

# Check conversation comments  
gh pr view <number> --comments

# View failed CI logs
gh run view <run-id> --log-failed
```

## Important

- **NO permission needed** between iterations - keep looping
- **Be transparent** - show each iteration's findings and fixes
- **Stop criteria**: ALL checks green AND NO bot comments
- **Max iterations**: 10 (ask for help after that)
