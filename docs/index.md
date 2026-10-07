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
- [`/skill-forge`](../skills/skill-forge/SKILL.md) - harden a skill through judge-panel refinement rounds to a 3-tier promotion gate; refined through its own process. Composes the `ab-equivalence` runner for its behavioural equivalence gate.
- [`/semantic-compress`](../skills/semantic-compress/SKILL.md) - optimize LLM-directed instructions while preserving behaviour. Two transforms in one family: **compress** (a local span-level core->pointer pass + an A/B-validated distill loop that produces the smallest behaviourally-equivalent version of a whole document or skill) and **directive-clarity** (rewrites latent-action instructions - bare negations, facts-not-actions, vague pointers - into directives that name the action, validated by a measured directness gain at zero regression). Both transforms compose `ab-equivalence` for the behavioural test. Directive-clarity design docs: [cognitive-ergonomics frame](../skills/semantic-compress/references/cognitive-ergonomics.md), [detection patterns](../skills/semantic-compress/references/directive-clarity-patterns.md), [battle-scar classifier](../skills/semantic-compress/references/battle-scar-classifier.md), [rewrite rules](../skills/semantic-compress/references/directive-clarity-rewrites.md).

Plugin-only (Claude Code, no standalone ZIP):

- [`/ghsync`](../skills/ghsync/SKILL.md) - bulk-clone and fast-forward sync every GitHub repo you can access across an org.
- [`/ghreport`](../skills/ghreport/SKILL.md) - read-only org repo state report: open PRs, default-branch CI, security alerts and branch protection per repo, reusing `/ghsync`'s repo discovery.

Team-orchestration library skills (invoked by the workflow commands, not standalone):

- [`marathon`](../skills/marathon/SKILL.md) - parallel agent marathon orchestration: DAG analysis, waves, crash recovery, retrospective. Its [forge report](../skills/marathon/forge/forge-report.md) is a real `/skill-forge` run output, kept as a worked example.
- [`pr-review-merge`](../skills/pr-review-merge/SKILL.md) - the PR review-to-green loop plus smart merge.
- [`ab-equivalence`](../skills/ab-equivalence/SKILL.md) - A/B behavioural equivalence testing: given two document versions and a transfer set, judges per-case equivalence. Composed by `skill-forge` and `semantic-compress`.

`/assess` is itself split into three skills - the orchestrator plus two render-time helpers:

- [`assess`](../skills/assess/SKILL.md) - the orchestrator and layered scorer.
- [`assess-findings`](../skills/assess-findings/SKILL.md) - renders the report from the deterministic `run-context.json` and the layer scorecard.
- [`assess-pr`](../skills/assess-pr/SKILL.md) - the end-of-run offers (open a PR, track the Top 3 Actions, freeze a CI gate), including the [uninstall steps](../skills/assess/references/uninstall.md) that remove what a run wrote.

The deterministic core's per-module reference is [`skills/assess/scripts/lib/README.md`](../skills/assess/scripts/lib/README.md); its test suites are mapped to the modules they pin in [`skills/assess/tests/README.md`](../skills/assess/tests/README.md).

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

## Acceptance-contract workflow

- [`FLOOR.md`](../FLOOR.md) - the constitutional floor: four clauses no automated step may rewrite, enforced by the required `floor enforcement` and `floor self-anchor` checks. Floor changes need the maintainer's out-of-band `floor-signoff` approval.
- [External-anchor proof](./floor-anchor-proof.md) - why the floor checks cannot be silently disarmed.
- [Acceptance contract verification](../skills/README.md#acceptance-contract-verification) - the map of the gates, from start gate to complete gate.
- [Readiness check prompt](../scripts/contract/readiness_check_prompt.md) - the fixed prompt for the one-pass, document-only readiness check.
- [Canary fixtures](../tests/canaries/README.md) - the contract-file format and the ground-truth fixtures the canary harness runs: `known-good` ([spec](../tests/canaries/known-good/spec.md), [contract](../tests/canaries/known-good/contract.md), [expected result](../tests/canaries/known-good/expected_result.md)), `known-good-interactive` ([spec](../tests/canaries/known-good-interactive/spec.md), [contract](../tests/canaries/known-good-interactive/contract.md), [expected result](../tests/canaries/known-good-interactive/expected_result.md)), `vacuous-contract` ([spec](../tests/canaries/vacuous-contract/spec.md), [contract](../tests/canaries/vacuous-contract/contract.md), [expected result](../tests/canaries/vacuous-contract/expected_result.md)) and `jet-fighters` ([PRD](../tests/canaries/jet-fighters/prd.md), [contract](../tests/canaries/jet-fighters/contract.md), [expected result](../tests/canaries/jet-fighters/expected_result.md)).

## Docs

- [Testing a branch locally](./testing-a-branch-locally.md) - run an unmerged branch's `SKILL.md` and scripts as a real plugin against a target repo.
- [Design history](./superpowers/README.md) - the plans and specs behind the skills as they were built.
- [Modernization programme (2026-09)](./design/2026-09-modernization/README.md) - intent, recorded probes, and the eleven workstream specs behind the multi-plugin split.
- [Code review instructions](../.github/claude-review-instructions.md) - the guidelines the automated reviewer follows on pull requests.
- [How to build a narrated skill explainer](./huddle-explainer/README.md) - the repeatable Claude Code + Claude Design + ElevenLabs pipeline, worked through end to end on `/huddle`.

The rendered example SVGs used in the README hero (the doc-navigability graph and the complexity heatmap, before and after the action sweep) live alongside this file in `docs/`.
