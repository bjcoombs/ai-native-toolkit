# Merging through a merge queue

The Smart Merge rules of the marathon skill for a base branch that has a merge queue.

**When the base branch has a merge queue, the lead enqueues and the queue merges.** Enqueue with
`gh pr merge $PR --match-head-commit <head sha>` (no strategy flag, never `--admin`, which either merges
past the queue as a ruleset bypass actor or is refused) or the GraphQL `enqueuePullRequest` mutation, in hot-file order. The queue tests the base plus
every PR ahead plus this one on the required checks and merges only when they pass, so it replaces the
lead's manual re-check of a hot PR (main plus the PR, the full suite and `make regen` run by hand before
merging): a semantic conflict between two individually green PRs now fails the second one's group, and
`$BASE_BRANCH` stays green. The lead no longer serialises merges behind that re-check, so throughput is
bounded by CI capacity, not by the lead. What stays with the lead:
- **The pre-review of hot and contract PRs.** Read the diff of a PR that touches a hot file or a frozen
  contract's paths before enqueueing it; the queue runs checks, it does not judge intent.
- **Reacting to a dequeue.** A failed group removes the PR from the queue with a timeline event naming the
  failing check. Send the failure to the PR's teammate (or a fresh one), and enqueue again once its
  required checks are green on the new head. A push to a queued PR dequeues it as well.
- **Confirming the merge.** Enqueueing returns at once; the verified-`MERGED` gate below runs when the
  queue reports the merge, not after the enqueue call.
