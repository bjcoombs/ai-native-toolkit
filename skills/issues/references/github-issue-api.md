# Dependency and sub-issue API

The REST calls behind Dependency Authoring, Size by Judgment and the adapter in the issues skill.

Paths are relative to `repos/{o}/{r}` (owner and repository). Every id these endpoints
take is the **numeric REST id** from `gh api repos/{o}/{r}/issues/{n} --jq .id`, NOT the
GraphQL node id that `gh issue view --json id` returns (that one 422s). Pass ids with typed
`-F`, not `-f`, so they are sent as integers.

Dependencies (ordering - what the marathon DAG consumes):

```bash
gh api repos/{o}/{r}/issues/{n}/dependencies/blocked_by                 # list blockers
gh api repos/{o}/{r}/issues/{n}/dependencies/blocking                   # reverse direction
BLOCKER_ID=$(gh api repos/{o}/{r}/issues/{m} --jq '.id')                # numeric REST id, NOT node id
gh api repos/{o}/{r}/issues/{n}/dependencies/blocked_by \
  --method POST -F issue_id="$BLOCKER_ID"                               # issue n is blocked by m
gh api repos/{o}/{r}/issues/{n}/dependencies/blocked_by/{id} --method DELETE   # {id} = numeric REST id
```

Sub-issues (decomposition with rollup, distinct from ordering):

```bash
gh api repos/{o}/{r}/issues/{n}/sub_issues                              # list children
gh api repos/{o}/{r}/issues/{n}/sub_issues --method POST -F sub_issue_id={id}
gh api repos/{o}/{r}/issues/{n}/sub_issue --method DELETE -F sub_issue_id={id}   # singular path
gh api repos/{o}/{r}/issues/{n}/sub_issues/priority --method PATCH -F sub_issue_id={id} -F after_id={id}
```

Constraints: one parent per issue, about 8 nesting levels, about 100 children per parent;
the parent's `sub_issues_summary` field carries the rollup progress.
