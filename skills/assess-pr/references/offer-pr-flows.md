# The PR flows and the PR body

Step 5 of the assess-pr skill, once the user selects a PR or upstream-issues offer: the direct, fork, upstream-issues and no-contributions flows, and the PR body every flow uses.

If the user **selected the PR offer** (direct flow, `CAN_PUSH=1`):
1. Create a branch in the target repo: `assess/snapshot-<YYYY-MM-DD>` (use the existing worktree workflow if `<repo>-main` + `worktree/` layout is present; otherwise branch in place).
2. Stage and commit the report, the complexity heatmap, and the doc graph. Commit message: `docs: Add AI-readiness assessment + complexity and doc-navigability snapshots`.
3. Push the branch and open a PR. Title: `docs: Codebase assessment - <YYYY-MM-DD>`.

If the user **selected the PR offer** (fork flow, `CAN_PUSH=0` on the upstream, `NO_CONTRIBUTIONS=0`):
1. `gh repo fork <owner>/<repo> --clone=false --remote=true` (creates the fork under the user's account and adds it as a remote named `origin` or similar; the upstream becomes `upstream` if the original was already `origin`).
2. Create the branch as above, push to the **fork** (`git push -u <fork-remote> <branch>`), and open the PR via `gh pr create --repo <owner>/<repo>` (head defaults to the fork).
3. Commit message, PR title, and body are unchanged from the direct flow.

If the user **selected the upstream-issues offer** (read-only flow, `CAN_PUSH=0` on `READ` / `TRIAGE`, `NO_CONTRIBUTIONS=0`): host the report in the fork, then file the issues upstream. Nothing under `.assess/` is proposed for upstream.
1. Resolve the fork and push the snapshot branch to it exactly as in step 1 of the no-contributions flow below (`FORK_SLUG` from the forks endpoint, the retry, the branch push), and stop there. Skip the rest of that step: do not align the fork with the assessed commit (no `BASE_SHA` comparison, no `assess/base-<YYYY-MM-DD>` branch push), write no PR body file, and open no PR, neither against upstream nor inside the fork.
2. Build the report link from the pushed branch: `REPORT_URL="https://<host>/$FORK_SLUG/blob/<branch>/.assess/assess-report.md"` (`<host>` is the GitHub host the remote uses). Confirm it resolves with `gh api "repos/$FORK_SLUG/contents/.assess/assess-report.md?ref=<branch>"` before quoting it.
3. Run Step 6 with the upstream repository (`$GH_SLUG`) as the tracker, using `$REPORT_URL` as each item's full-report link. If hosting was declined or failed, file the issues anyway: each body stands on its own without the link.
4. Suggest disabling Actions on the fork, as in step 3 of the no-contributions flow.

If the user **selected the PR offer** (no-contributions flow, `NO_CONTRIBUTIONS=1` with either `CAN_PUSH=0` on `READ` / `TRIAGE` or `IS_FORK=true`): the PR stays inside your fork; nothing is opened against upstream.
1. Resolve the fork. With `IS_OWN_FORK=true` and `CAN_PUSH=1` the current repo is the user's own fork: `FORK_SLUG="$GH_SLUG"`, and push to `origin`. Otherwise (including any clone of someone else's fork, whatever the permission) fork as in step 1 of the fork flow, then read the fork's real name back (GitHub returns the existing fork for a repeat request, and appends a suffix when the account already owns a repo of that name, so never assemble it by hand): `FORK_SLUG=$(gh api -X POST repos/<owner>/<repo>/forks | jq -r '.full_name' | tr -d '\r')`. Fork creation is asynchronous: if `gh repo view "$FORK_SLUG"` fails straight after, retry it a few times, a few seconds apart, before giving up. Create the branch and push it to the fork as in the first half of the fork flow's step 2; do **not** run that step's upstream `gh pr create`. Write the PR body template (below the flows) to a temp file and use it as `<body-file>`, with the direct flow's PR title as `<title>`. Open the PR against the fork's default branch instead: `FORK_BRANCH=$(gh repo view "$FORK_SLUG" --json defaultBranchRef | jq -r '.defaultBranchRef.name' | tr -d '\r')`. A fork made earlier can sit behind or ahead of the checkout that was assessed, and a PR onto it would then carry every commit in between. So compare `BASE_SHA=$(git rev-parse "<branch>^")` (the commit the snapshot branch was cut from) with `FORK_SHA=$(gh api "repos/$FORK_SLUG/branches/$FORK_BRANCH" | jq -r '.commit.sha' | tr -d '\r')`; if they differ, push the assessed commit to the fork as its own base (`git push <fork-remote> "$BASE_SHA:refs/heads/assess/base-<YYYY-MM-DD>"`, where `<fork-remote>` is the remote the branch went to), set `FORK_BRANCH=assess/base-<YYYY-MM-DD>`, and tell the user the PR is based on that branch because the fork's default branch does not match what was assessed. This leaves the fork's existing branches untouched. Then create the PR through the fork's own pulls endpoint, which cannot resolve to the parent repo: `gh api -X POST "repos/$FORK_SLUG/pulls" -f head=<branch> -f base="$FORK_BRANCH" -f title="<title>" -F body=@<body-file> | jq -r '.html_url'`.
2. Share the link: print the fork PR's URL (the `html_url` above; its path must end with `/$FORK_SLUG/pull/<number>`, whatever the host, so a GitHub Enterprise URL passes) so the user can pass it on to whoever wants the assessment.
3. Suggest disabling Actions on the fork: the fork carries the upstream's workflow files, so if the user ever enables Actions there, this PR and later pushes would start CI runs the fork's owner pays for on a private fork or self-hosted runners. Disabling them up front keeps the fork an inert place to share the report. Offer the command, never run it unasked: `gh api -X PUT "repos/$FORK_SLUG/actions/permissions" -F enabled=false`.
4. Commit message, PR title, and body are unchanged from the direct flow.

**Every PR flow (direct, fork, and no-contributions): the PR body leads with the findings and ends with the plugin reference** so reviewers can install the tool that generated the report. The Readiness line and the Top 3 table come from `assess_actions_render.py`, which reads `.assess/actions.json` and `.assess/run-context.json` (relative to the repo root) and never the report, so the report's Top 3 section can change shape without changing the PR body. Pass `--assess-dir "$REPO_ROOT/.assess/<slug>"` for a `--scope` run:

```bash
<!-- chat-replace:uv-actions-render -->
RENDER="${CLAUDE_SKILL_DIR}/../assess/scripts/assess_actions_render.py"
uv run "$RENDER" "$REPO_ROOT" readiness   # the Readiness value
uv run "$RENDER" "$REPO_ROOT" table       # the Top 3 table, one row per actions.json entry in rank order
```

A missing or unreadable `actions.json` exits 1 with a message: say so in the PR body in place of the table, and never rebuild the table from the report. Use this body template:

   ```markdown
   ## Summary

   Snapshot of this codebase's AI-agent readiness, complexity hotspots, and doc navigability as of <YYYY-MM-DD>.

   - **Readiness:** <the output of `assess_actions_render.py <repo> readiness`, verbatim>
   - **Hotspot leader:** `<top hotspot path>` (<loc> LOC, ccn <N>, <M> commits in window) - write `ccn ~<N> (approximate)` when the row is scc-only (`source` `scc`, `max_fn_ccn` null), and `ccn ~<N> (unreliable)` when its path is in `stats_summary.complexity_unreliable`
   - **Top lying map:** `<top stale-hub doc>` (<N>d stale, subject churn <M>)

   ## Top 3 Actions

   <the output of `assess_actions_render.py <repo> table`, verbatim>

   Full report: [`.assess/assess-report.md`](./.assess/assess-report.md) (the heatmap and doc graph render inline).

   ---

   <!-- chat-replace:pr-footer -->
   _Generated by [`/ai-native-toolkit:assess`](https://github.com/bjcoombs/ai-native-toolkit) — a Claude Code plugin for codebase readiness assessment with complexity hotspot heatmaps. Install in any Claude Code session: `/plugin marketplace add https://github.com/bjcoombs/ai-native-toolkit` then `/plugin install ai-native-toolkit@ai-native-toolkit`._
   ```
