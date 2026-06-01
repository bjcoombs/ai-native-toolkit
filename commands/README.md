# Commands

Slash commands shipped by the plugin. Portable framework commands work in any Claude Code session; the workflow commands embed one author's personal setup and are opt-in - see [Adapting for your workflow](../README.md#adapting-for-your-workflow) before relying on them. Back to the [Map of Content](../docs/index.md).

## Portable

| Command | Description |
|---------|-------------|
| [`/6hats`](./6hats.md) | Solo Six Hats analysis - alias for [`/huddle`](../skills/huddle/SKILL.md) at team size 1 |
| [`/understand`](./understand.md) | Deep understanding mode (nemawashi) - exhaustive context-gathering before action |

## Workflow (personal setup, opt-in)

| Command | Description |
|---------|-------------|
| [`/tm`](./tm.md) | Task Master orchestration - starts, reviews, or cleans up tasks based on current state |
| [`/issues`](./issues.md) | GitHub-issue marathon - triage open issues, then run agent-ready ones to merge with Agent Teams |
| [`/fix-pr`](./fix-pr.md) | Autonomous PR fixing loop - iterates on CI failures and review comments until green |
| [`/fix-develop`](./fix-develop.md) | Autonomous fix loop for failing CI on the repo's default branch |
| [`/tm-marathon-config-example`](./tm-marathon-config-example.md) | Reference configuration block to drop into a project's `CLAUDE.md` for marathon-mode `/tm` and `/issues` |

`/tm`, `/issues`, `/fix-pr`, and `/fix-develop` share the [`marathon`](../skills/marathon/SKILL.md) and [`pr-review-merge`](../skills/pr-review-merge/SKILL.md) library skills as their single source of truth. Each command supplies a thin work-source adapter; the skills own the execution.
