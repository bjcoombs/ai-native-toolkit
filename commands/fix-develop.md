# Fix Default Branch

Assess failing CI on the repo's default branch (main / develop / etc., or nightly build), create a worktree with a fix, push a PR, and loop until CI passes and review comments are addressed.

**Usage**: `/fix-develop [repo-path]` — defaults to current repo context. (Command name is historical; works on whichever branch the repo treats as default.)

---

## Step 1: Assess Default Branch Health

```bash
set -euo pipefail

# Identify the repo
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "")
REPO_NAME=$(basename "$REPO_ROOT")
ORG=$(gh repo view --json owner --jq '.owner.login')
REPO=$(gh repo view --json name --jq '.name')
DEFAULT_BRANCH=$(gh repo view --json defaultBranchRef --jq '.defaultBranchRef.name')

# Fail fast if any of the above came back empty (wrong pane, no gh auth, not a repo)
: "${REPO_ROOT:?Not inside a git repo — re-run from the target repo}"
: "${ORG:?gh repo view returned no owner — check gh auth and current directory}"
: "${REPO:?gh repo view returned no name — check gh auth and current directory}"
: "${DEFAULT_BRANCH:?gh repo view returned no defaultBranchRef — check gh auth}"

# Get latest CI status on the default branch
gh api repos/$ORG/$REPO/commits/$DEFAULT_BRANCH/status --jq '{state: .state, total: .total_count}'

# Get failing workflow runs on the default branch
gh run list --branch $DEFAULT_BRANCH --status failure --limit 5 --json databaseId,name,conclusion,createdAt \
  | jq '.[] | {id: .databaseId, name, conclusion, created: .createdAt}'
```

If the default branch is green, report "$DEFAULT_BRANCH is healthy, nothing to fix" and STOP.

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
## Default Branch Diagnosis

Branch: <DEFAULT_BRANCH>
Failing runs: <list>
Root cause: <assessment>
Proposed fix: <plan>

Proceeding to create fix PR...
```

## Step 3: Create Worktree and Fix

```bash
set -euo pipefail
: "${ORG:?ORG unset — re-run Step 1 to derive repo identity}"
: "${REPO:?REPO unset — re-run Step 1 to derive repo identity}"
: "${DEFAULT_BRANCH:?DEFAULT_BRANCH unset — re-run Step 1 to derive repo identity}"

# From repo-main (sacred, always on default branch)
cd ~/dev/github.com/$ORG/$REPO/$REPO-main
git checkout $DEFAULT_BRANCH && git pull origin $DEFAULT_BRANCH

# Create fix branch and worktree (slug the default branch in case it contains '/')
DEFAULT_BRANCH_SLUG="${DEFAULT_BRANCH//\//-}"
BRANCH_NAME="fix-${DEFAULT_BRANCH_SLUG}-$(date +%Y%m%d)-$(echo '<brief-description>' | tr ' ' '-')"
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

# Create PR targeting the default branch
gh pr create --base $DEFAULT_BRANCH --title "fix: Resolve failing CI on $DEFAULT_BRANCH" --body "$(cat <<'EOF'
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

   This fixes the following $DEFAULT_BRANCH failures:
   - <list of what was broken>

   ---
   ## Current Work

   PR: #X - Fix failing CI on $DEFAULT_BRANCH
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
