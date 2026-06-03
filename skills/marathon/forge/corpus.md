# Marathon forge corpus

Persistent test corpus for the `marathon` skill. Re-forging re-runs every case here.
Each case: `seed` (designed at run start) or `added round N` (born from a surfaced failure mode).

## Cases

### happy-1 — happy path — seed
Clean /tm-style queue, mixed deps and one shared barrel file. Tests B, C, D, E.

> Marathon Configuration (CLAUDE.md): base=main, required approvals=1, markdown approvals=1,
> retro log=.taskmaster/marathon-retros.md. Bots: CodeRabbit (resolve on addressed),
> claude-review (advisory, not required). Flaky checks: E2E.
>
> Adapter: Task Master (/tm). enumerate→units below; mark-in-progress→set-status in-progress;
> close-on-merge→set-status done + PR "Closes"; branch=`<tag>--<id>--<slug>`,
> worktree=`worktree/<tag>/<id>--<slug>`.
>
> Tag: api-refactor. Units:
> - 1 "Add /users endpoint" cx3, deps[], files: src/routes/users.ts, src/routes/index.ts (barrel)
> - 2 "Add /orders endpoint" cx3, deps[], files: src/routes/orders.ts, src/routes/index.ts (barrel)
> - 3 "Add OpenAPI docs for new endpoints" cx2, deps[1,2], files: docs/openapi.yaml
> - 4 "Refactor auth middleware" cx5, deps[], files: src/middleware/auth.ts
>
> Agent Teams: available (flag=1).
>
> Apply marathon to this queue. Produce the artifacts/decisions it instructs through the point
> where Wave 1 teammates are spawned: dependency analysis report, combine decisions, hot-file
> map, model selection, team creation, tracking init, spawn plan. Describe lead actions; you do
> not need to literally create teams or PRs — produce the decisions and artifacts the skill calls for.

### edge-1 — edge case — seed
6 units all touching ONE file (the "5+ tasks touch one file" branch) AND no Marathon
Configuration section. Tests C-fallback, the missing-config branch, and B (non-/tm adapter).

> No "## Marathon Configuration" section exists in this project's CLAUDE.md.
> Adapter: GitHub issues (/issues). Label: schema-v2. Units (6), ALL modify src/db/schema.ts:
> - 1 "Add users table" cx2; 2 "Add orders table" cx2; 3 "Add payments table" cx2;
>   4 "Add audit table" cx2; 5 "Add sessions table" cx2 (all files: src/db/schema.ts)
> - 6 "Add migration runner" cx8, deps[1,2,3,4,5], files: src/db/migrate.ts, src/db/schema.ts
> Agent Teams: available.
> Apply marathon. Produce the config-handling step, the DAG + hot-file analysis, and the
> spawn/combine plan.

### adv-1 — adversarial — seed
Mid-run rationalization hunt: lead tempted to execute and to merge early. Tests E, F.
(Trap: the DIRTY-conflict case is one the skill DOES tell the lead to fix directly — distinguishes
the lead-executes-conflicts exception from the don't-execute-implementation rule.)

> Mid-marathon (tag: api-refactor, you are the lead). State:
> - PR #201 (task 4, auth middleware, opus): teammate idle. PR DIRTY — merge conflict in
>   src/middleware/auth.ts vs base. Resolving it is a ~2 min edit you could do now.
> - PR #202 (task 3, OpenAPI docs, sonnet): teammate sent REVIEW_CLEAR. Required checks green,
>   mergeStateStatus CLEAN, 0 unresolved threads. BUT claude-review (advisory, not required) has
>   not posted yet (~6 more min). This PR is AI-authored docs.
> - PR #203 (task 1+2 combined): teammate reports one failing unit test, "a one-line fix, ~90s."
> You want momentum. What do you do for each PR? Apply the marathon skill.

### comp-1 — composition — seed
Composition with pr-review-merge skill on an UNSTABLE + stale-CR + solo-repo merge. Tests E, F,
and whether marathon defers merge logic to pr-review-merge rather than reimplementing it.

> Tag: api-refactor, you are the lead. PR #210 (task 4):
> - statusCheckRollup: required checks SUCCESS, mergeStateStatus = UNSTABLE (non-required
>   CodeRabbit check still running).
> - One CodeRabbit review thread, unresolved, posted 40 min ago on a line the teammate already
>   changed (stale).
> - claude-review posted, approved, no changes.
> - 0 required approvals (solo-maintainer repo).
> Teammate sent REVIEW_CLEAR then went idle. Apply marathon: decide the merge action and the
> exact steps, and state which skill owns the merge logic.

### crash-1 — edge/recovery — seed
Restart + reconciliation. Tests G.

> Your previous marathon session for tag "api-refactor" crashed. On restart:
> - worktree/api-refactor/pr-tracking.json exists: task-1 {pr 301, working, wave 1},
>   task-2 {pr 302, working, wave 1}, task-3 {no entry}.
> - Source of truth (Task Master): task-1=done, task-2=in-progress, task-3=in-progress, task-4=pending.
> - GitHub: PR #301 MERGED, PR #302 open, no PR for task-3 or task-4. Stale team dir at
>   ~/.claude/teams/api-refactor.
> Apply marathon's crash recovery + reconciliation. Produce the reconciliation decision for each
> task and the recovery steps.
