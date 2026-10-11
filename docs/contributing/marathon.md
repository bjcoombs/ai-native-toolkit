# Marathon notes

The reasoning behind the `## Marathon Configuration` values in the root `CLAUDE.md`. The `/tm`, `/issues`, `marathon`, `pr-review-merge` and `/fix-pr` skills read the values from `CLAUDE.md`, so every setting they extract stays there; this page explains them. Back to the [Map of Content](../index.md).

## Why the section exists

When the section is absent the skills fall back to defaults (base `main`, **1** approval). That is wrong for this solo repo and would stall every PR, so the section stays in `CLAUDE.md`.

## Approvals

Required approvals are 0 because this is a solo-maintainer repo with no second reviewer and no approving bot. The lead merges each PR itself at green CI and resolved threads. No human reviewers.

## Bot reviewers

### CodeRabbit (`coderabbitai[bot]`)

On this repo CodeRabbit is rate-limited and its check often reports neutral or `null`, so waiting on it would stall every PR. It comments only, is not a required status check, and never gates merge. Fix the code and push: CodeRabbit re-reviews and resolves its own threads. Never reply in CodeRabbit threads; it ignores replies from other bots.

### claude-review (`claude[bot]`)

The advisory AI review workflow. It re-reviews on push. The 20m max wait exists because the `claude-review` job has `timeout-minutes: 15` and runs of 9-12m are routine, so 20m keeps the timeout an escape hatch rather than the usual outcome. Resolve its threads via GraphQL after addressing the feedback.

The marathon's hold for the AI review before merging is that setting applied through `pr-review-merge` Ready Criterion 6. The `claude-review` check run on the head SHA has four states:

| State | What the lead does |
|-------|--------------------|
| In progress | Keep waiting until the 20m max wait expires. |
| Completed, conclusion `success` | Criterion satisfied. Confirm the summary's `Commit:` line cites the head before merging on it. |
| Completed, conclusion `skipped` | The reviewer is not applicable to this PR (the workflow skips Dependabot actors by design). Criterion satisfied, no warning. |
| Completed, any other conclusion (failure, cancelled, neutral, timed_out) | A settled verdict that the reviewer did not complete a green pass. Take the warning path at once. |

On expiry, or on a settled run whose conclusion is neither `success` nor `skipped`, the lead merges with a warning in the merge record naming `claude-review`, the head SHA, the run's conclusion, and whether the reviews endpoint shows a `claude[bot]` review of that head SHA anyway. Nothing holds forever on this advisory bot.

## CI patterns

Required (merge-gating) checks, enforced by branch protection (see [CI and merge gates](./ci.md#branch-protection)): `skills/assess pytest`, `scripts/ pytest`, `plugin contract pytest`, `ruff + mypy gates`, `Validate PR title`, `floor enforcement`, `floor self-anchor`.

Non-blocking checks:

- `CodeRabbit` - rate-limited bot.
- `Auto-label from PR title` - convenience automation.
- `canary suite (semantic layer)` - runs only on PRs that touch a floor-protected path.
- `canary path filter` - classifies the PR's changed paths for the canary and sign-off jobs.
- `floor sign-off summary` - tells the maintainer what a sign-off covers.
- `floor sign-off` - the `floor-signoff` environment deployment review. Runs only when a PR changes the floor core, and gates merge indirectly because `floor enforcement` goes red without an approved sign-off.
- `build` - push-only standalone publish; does not run on PRs.
- `mutmut (assess core)` - `mutation.yml`, weekly and manual only; never runs on PRs.
- `CodeQL (python)` / `CodeQL (javascript-typescript)` / `CodeQL (actions)` - advisory `codeql.yml` analysis jobs; red only on an analysis failure.
- `CodeQL` - the code-scanning check that carries the alerts those jobs upload.
- `pip-audit` - advisory dependency audit; red on a known vulnerability or a resolver error, so read the log.
- `claude-review` - advisory AI review. Posts findings as threads but never gates merge. Slow; often finishes after the required checks are green. Reports red when the reviewer itself errored, so a green check is evidence the reviewer completed, not that it reviewed the right head: confirm the summary's `Commit:` line matches the head before relying on it. The lead's hold on it follows the four-state rule above.

### AI-readiness regression gate

An `/assess`-based gate that is **effectively required but re-runs on every base advance** (it diffs the PR against `main`). It is not in the classic branch-protection `required_status_checks.contexts`, so it does not show in that API list, but a plain `gh pr merge` is refused while it is re-running. In a multi-PR wave each merge re-triggers it on the still-open PRs: either wait for it to re-settle per PR, or merge with `--admin` once the seven named required checks are green.

## The version hot file

`.claude-plugin/plugin.json` `.version` changes on every PR. Assign each teammate its target version explicitly at spawn and merge sequentially (highest version wins). Values assigned at spawn are final: if merge order shifts (for example an externally merged PR advances `main`), fix it at merge time by holding or lead-resolving the conflict, never by re-messaging an in-flight teammate.

**Identical bumps across parallel PRs are unsafe.** `build-standalone-skills.yml` publishes an immutable `standalone-skills-v<version>` on the version *change*, so the first merge ships an incomplete bundle and the identical later bumps never re-fire it. Have the last-merging PR bump one step higher, or bump once at the end, so the complete tree republishes. See [Releasing](./release.md).

## Retrospective log

The retro log lives at `~/.claude/projects/<project-slug>/memory/marathon-retros.md`. The path is per-machine; do not commit a resolved absolute path, because it leaks a home directory, the exact issue `/assess` warns about.
