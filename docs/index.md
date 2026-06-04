# Map of Content

The navigation index for `ai-native-toolkit`. Every shipped doc in this repo is reachable from here by following links - no directory-walking required. Start at the [README](../README.md) for the project overview, the [CLAUDE.md](../CLAUDE.md) contract for the rules that govern edits, then use the trails below to reach any agent, command, or skill.

## Subtrees at a glance

| Subtree | Entry doc | What lives there |
|---------|-----------|------------------|
| Skills | [`skills/`](../skills/README.md) | The plugin's skills - the headline `/assess`, `/huddle`, `/deslop`, `/skill-forge`, `/semantic-compress`, plus `/ghsync` and the team-orchestration library skills |
| Commands | [`commands/`](../commands/README.md) | Slash commands - portable framework commands and opt-in personal workflow commands |
| Agents | [`agents/`](../agents/README.md) | The Six Thinking Hats team that `/huddle` and `/6hats` orchestrate |
| Docs | this file | Design history, runbooks, and the rendered example SVGs |

## Skills

The auto-discovered skills, each with its own `SKILL.md` base doc. See [`skills/README.md`](../skills/README.md) for the full catalog.

Portable (work in any Claude Code session, also shipped as standalone ZIPs):

- [`/assess`](../skills/assess/SKILL.md) - score a codebase's readiness for AI agent contributors against the 0-8 layered contract model; emits a complexity hotspot SVG and a doc-navigability graph SVG.
- [`/huddle`](../skills/huddle/SKILL.md) - structured multi-perspective deliberation using Six Thinking Hats with Fibonacci team sizing.
- [`/deslop`](../skills/deslop/SKILL.md) - detect and remove the telltale signs of AI writing. Ships an exhaustive [reference checklist](../skills/deslop/references/full-checklist.md).
- [`/ghsync`](../skills/ghsync/SKILL.md) - bulk-clone and fast-forward sync every GitHub repo you can access across an org.
- [`/skill-forge`](../skills/skill-forge/SKILL.md) - harden a skill through judge-panel refinement rounds to a 3-tier promotion gate; refined through its own process. Composes the `ab-equivalence` runner for its behavioural equivalence gate. Ships an [example forge report](../skills/skill-forge/references/example-forge-report.md).
- [`/semantic-compress`](../skills/semantic-compress/SKILL.md) - optimize LLM-directed instructions while preserving behaviour. Two transforms in one family: **compress** (a local span-level core->pointer pass + an A/B-validated distill loop that produces the smallest behaviourally-equivalent version of a whole document or skill) and **directive-clarity** (rewrites latent-action instructions - bare negations, facts-not-actions, vague pointers - into directives that name the action, validated by a measured directness gain at zero regression). Both transforms compose `ab-equivalence` for the behavioural test. Directive-clarity design docs: [cognitive-ergonomics frame](../skills/semantic-compress/references/cognitive-ergonomics.md), [detection patterns](../skills/semantic-compress/references/directive-clarity-patterns.md), [battle-scar classifier](../skills/semantic-compress/references/battle-scar-classifier.md), [rewrite rules](../skills/semantic-compress/references/directive-clarity-rewrites.md).

Team-orchestration library skills (invoked by the workflow commands, not standalone):

- [`marathon`](../skills/marathon/SKILL.md) - parallel agent marathon orchestration: DAG analysis, waves, crash recovery, retrospective.
- [`pr-review-merge`](../skills/pr-review-merge/SKILL.md) - the PR review-to-green loop plus smart merge.
- [`ab-equivalence`](../skills/ab-equivalence/SKILL.md) - A/B behavioural equivalence testing: given two document versions and a transfer set, judges per-case equivalence. Composed by `skill-forge` and `semantic-compress`.

`/assess` is itself split into three skills - the orchestrator plus two render-time helpers:

- [`assess`](../skills/assess/SKILL.md) - the orchestrator and layered scorer.
- [`assess-findings`](../skills/assess-findings/SKILL.md) - renders the report from the deterministic `run-context.json` and the layer scorecard.
- [`assess-pr`](../skills/assess-pr/SKILL.md) - the end-of-run offers (open a PR, track the Top 3 Actions, freeze a CI gate).

## Commands

The slash commands, indexed in [`commands/README.md`](../commands/README.md).

Portable:

- [`/6hats`](../commands/6hats.md) - solo Six Hats analysis, an alias for `/huddle` at team size 1.
- [`/understand`](../commands/understand.md) - deep understanding mode (nemawashi): exhaustive context-gathering before action.

Personal workflow commands (opt-in - see [Adapting for your workflow](../README.md#adapting-for-your-workflow)):

- [`/tm`](../commands/tm.md) - Task Master orchestration: starts, reviews, or cleans up tasks by current state.
- [`/issues`](../commands/issues.md) - GitHub-issue marathon: triage open issues, then run agent-ready ones to merge with Agent Teams.
- [`/fix-pr`](../commands/fix-pr.md) - autonomous PR fixing loop: iterates on CI failures and review comments until green.
- [`/fix-develop`](../commands/fix-develop.md) - autonomous fix loop for failing CI on the default branch.
- [`/tm-marathon-config-example`](../commands/tm-marathon-config-example.md) - reference configuration block for marathon-mode `/tm` and `/issues`.

## Agents

The Six Thinking Hats team, indexed in [`agents/README.md`](../agents/README.md):

- [`white-hat`](../agents/white-hat.md) - facts and evidence.
- [`red-hat`](../agents/red-hat.md) - gut feelings and emotional drivers.
- [`black-hat`](../agents/black-hat.md) - risks and critical analysis.
- [`yellow-hat`](../agents/yellow-hat.md) - benefits and opportunities.
- [`green-hat`](../agents/green-hat.md) - creative alternatives.
- [`blue-hat`](../agents/blue-hat.md) - synthesis and recommendation.
- [`scribe`](../agents/scribe.md) - structures hat output into actionable documentation.
- [`assess-layer-scorer`](../agents/assess-layer-scorer.md) - scores a codebase against the `/assess` layered contract model.

## Docs

- [Testing a branch locally](./testing-a-branch-locally.md) - run an unmerged branch's `SKILL.md` and scripts as a real plugin against a target repo.
- [Design history](./superpowers/README.md) - the plans and specs behind the skills as they were built.
- [Code review instructions](../.github/claude-review-instructions.md) - the guidelines the automated reviewer follows on pull requests.

The rendered example SVGs used in the README hero (the doc-navigability graph and the complexity heatmap, before and after the action sweep) live alongside this file in `docs/`.
