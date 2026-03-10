# Fix Develop Branch

Assess failing CI on develop (or nightly build), create a worktree with a fix, push a PR, and loop until CI passes and review comments are addressed.

**Usage**: `/fix-develop [repo-path]` — defaults to current repo context.

---

## Step 1: Assess Develop Health

```bash
# Identify the repo
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "")
REPO_NAME=$(basename "$REPO_ROOT")
ORG=$(gh repo view --json owner --jq '.owner.login')
REPO=$(gh repo view --json name --jq '.name')

# Get latest develop CI status
gh api repos/$ORG/$REPO/commits/develop/status --jq '{state: .state, total: .total_count}'

# Get failing workflow runs on develop
gh run list --branch develop --status failure --limit 5 --json databaseId,name,conclusion,createdAt \
  | jq '.[] | {id: .databaseId, name, conclusion, created: .createdAt}'
```

If develop is green, report "Develop is healthy, nothing to fix" and STOP.

## Step 2: Diagnose Failures

```bash
# For each failing run, get the failed jobs and logs
RUN_ID=<from step 1>
gh run view $RUN_ID --json jobs | jq '.jobs[] | select(.conclusion == "FAILURE") | {name, conclusion}'
gh run view $RUN_ID --log-failed 2>&1 | tail -100
```

**Categorize failures:**
- **Test failures** — read the failing test + source code to understand root cause
- **Lint/build errors** — read the offending file at the reported line
- **Flaky/infrastructure** — check if the same test passed on a recent re-run. If so, report as flaky (not actionable) and STOP
- **Dependency issues** — check if a dependency update broke something

Report diagnosis to user before proceeding:
```
## Develop Diagnosis

Branch: develop
Failing runs: <list>
Root cause: <assessment>
Proposed fix: <plan>

Proceeding to create fix PR...
```

## Step 3: Create Worktree and Fix

```bash
# From repo-main (sacred, always on develop)
cd ~/dev/github.com/$ORG/$REPO/$REPO-main
git checkout develop && git pull origin develop

# Create fix branch and worktree
BRANCH_NAME="fix-develop-$(date +%Y%m%d)-$(echo '<brief-description>' | tr ' ' '-')"
git branch $BRANCH_NAME
git worktree add ../worktree/$BRANCH_NAME $BRANCH_NAME
cd ../worktree/$BRANCH_NAME
```

Implement the fix. Keep changes minimal — fix the failing tests/build only. Do not refactor, do not improve, do not clean up.

```bash
# Commit and push
git add <specific-files>
git commit -m "fix: <description of what was failing and why>"
git push -u origin $BRANCH_NAME

# Create PR targeting develop
gh pr create --base develop --title "fix: Resolve failing CI on develop" --body "$(cat <<'EOF'
## Summary
- <What was failing>
- <Root cause>
- <What this PR fixes>

## Evidence
- Failing run: <link>
- Error: <brief>
EOF
)"
```

## Step 4: Loop Until Green

### Check for Ralph Plugin

```bash
ls ~/.claude/plugins/cache/claude-plugins-official/ralph-loop/*/commands/ralph-loop.md 2>/dev/null && echo "RALPH_AVAILABLE" || echo "RALPH_NOT_AVAILABLE"
```

**If Ralph available:**

```
Skill(
  skill: "ralph-loop:ralph-loop",
  args: "Fix develop CI PR. EACH iteration: check CI status, inline comments, conversation comments. Fix issues, commit, push, wait 60s, repeat. Output \\<promise\\>DEVELOP_FIXED\\</promise\\> when CI passes and all comments addressed. --max-iterations 10 --completion-promise DEVELOP_FIXED"
)
```

**If Ralph NOT available:** Use manual loop below.

### Each Iteration

```bash
PR=$(gh pr view --json number --jq '.number')

# CI status — use positive filters (zsh escapes != to \!=)
gh pr checks $PR --json name,state | jq '.[] | select(.state == "FAILURE" or .state == "CANCELLED")'

# Unresolved review threads
gh api graphql -f query='query { repository(owner: "'$ORG'", name: "'$REPO'") {
  pullRequest(number: '$PR') { reviewThreads(first: 50) { nodes {
    id isResolved path line comments(first: 1) { nodes { author { login } body } }
  }}}}}' | jq '.data.repository.pullRequest.reviewThreads.nodes[]
  | select(.isResolved == false)
  | {id, author: .comments.nodes[0].author.login, path, line, body: .comments.nodes[0].body[0:200]}'

# Conversation comments
gh pr view $PR --comments
```

**Decision tree:**
- CI failing → Fix code, commit, push
- CodeRabbit threads → Fix code, push (CodeRabbit re-reviews and resolves automatically. **Never reply in CodeRabbit threads.**)
- claude[bot] threads → Check if already fixed locally, resolve via GraphQL if so
- Human threads → Fix code, reply inline, @mention reviewer
- **ALL green** → Report ready, STOP

Wait 60s between iterations for CI to start.

---

## Protocol

1. **Autonomous Loop** — no permission needed between iterations:
   - Check CI + comments, fix, push
   - Report: "Iteration N: Fixed X issues" or "Iteration N: Waiting for CI"

2. **Stop when green**:
   ```
   PR #X: CI passing, all comments addressed. Ready for merge.

   This fixes the following develop failures:
   - <list of what was broken>

   ---
   ## Current Work

   PR: #X - Fix failing CI on develop
      https://github.com/org/repo/pull/X

   Latest: All green after N iterations
   ```

3. **Only ask for help if**:
   - Root cause is unclear after reading logs + source
   - Fix requires architectural decision
   - After 5+ iterations with no progress
   - Failure is in code you don't have context on

4. **After merge** (if user merges):
   ```bash
   cd ~/dev/github.com/$ORG/$REPO
   cd $REPO-main && git worktree remove --force ../worktree/$BRANCH_NAME
   git branch -d $BRANCH_NAME
   ```

---

## Important

- **Minimal fixes only** — fix what's broken, nothing else
- **Use positive jq filters** — `select(.state == "FAILURE")` not `select(.state != "SUCCESS")`
- **Never work in repo-main** — always create a worktree
- **Max iterations**: 10 (ask for help after that)
- **If flaky/infrastructure**: Report and STOP — don't create a PR for transient failures
