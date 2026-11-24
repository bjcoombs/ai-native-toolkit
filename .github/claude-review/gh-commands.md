# Review commands

The `gh` calls the automated reviewer runs, read on demand from
`.github/claude-review-instructions.md`. Placeholders (`{REPO}`, `{PR_NUMBER}`,
`{HEAD_SHA}`, `{REPO_OWNER}`, `{REPO_NAME}`) carry the values the workflow
prompt supplies.

## Read a full file at the head commit

```bash
gh api "repos/{REPO}/contents/{filepath}?ref={HEAD_SHA}" --jq '.content' | base64 -d
```

## Unresolved bot threads (CodeRabbit)

```bash
gh api graphql -f query='
query { repository(owner: "{REPO_OWNER}", name: "{REPO_NAME}") {
  pullRequest(number: {PR_NUMBER}) {
    reviewThreads(first: 100) { nodes {
      id isResolved path line
      comments(first: 5) { nodes { author { login } body } }
    } }
  } } }' --jq '
  .data.repository.pullRequest.reviewThreads.nodes[]
  | select(.isResolved == false)
  | select(.comments.nodes[0].author.login | test("\\[bot\\]$|coderabbitai"))
  | {id, path, line, author: .comments.nodes[0].author.login, body: .comments.nodes[0].body[0:300]}'
```

## Summary comment: find, create or update

```bash
EXISTING_ID=$(gh api "repos/{REPO}/issues/{PR_NUMBER}/comments" \
  --jq '[.[] | select(.user.login=="claude[bot]") | select(.body | contains("## Claude Code Review"))] | last | .id // empty')

# create (no EXISTING_ID)
gh pr comment {PR_NUMBER} --body "$BODY"
# update in place
gh api "repos/{REPO}/issues/comments/$EXISTING_ID" --method PATCH -f body="$BODY"
```

## Review with inline comments

```bash
gh pr diff {PR_NUMBER}   # confirm the exact lines that appear in the diff

gh api "repos/{REPO}/pulls/{PR_NUMBER}/reviews" --method POST \
  -f event="COMMENT" \
  -f body="See summary comment." \
  --raw-field comments='[
    {"path": "skills/assess/scripts/lib/foo.py", "line": 42, "body": "**MUST FIX**: ..."},
    {"path": "skills/assess/SKILL.md", "start_line": 55, "line": 60, "body": "**Suggestion**: ..."}
  ]'
```

`event` is `APPROVE`, `COMMENT` or `REQUEST_CHANGES`.

## Your own unresolved threads, and resolving one

```bash
gh api graphql -f query='
query { repository(owner: "{REPO_OWNER}", name: "{REPO_NAME}") {
  pullRequest(number: {PR_NUMBER}) {
    reviewThreads(first: 50) { nodes {
      id isResolved
      comments(first: 5) { nodes { author { login } body } }
    } }
  } } }' --jq '
  .data.repository.pullRequest.reviewThreads.nodes[]
  | select(.isResolved == false)
  | select(.comments.nodes[0].author.login == "claude[bot]") | .id'

# for each addressed thread id:
gh api graphql -f query='
mutation { resolveReviewThread(input: {threadId: "THREAD_ID"}) { thread { isResolved } } }'
```
