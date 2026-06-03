# Skills

The plugin's skills, auto-discovered by Claude Code from each `<name>/SKILL.md`. Each `SKILL.md` is its subtree's base doc and carries the frontmatter contract (`name`, `description` with a `TRIGGER` clause) documented in [`CLAUDE.md`](../CLAUDE.md). Bundled executables live under `<name>/scripts/`, reference docs under `<name>/references/`. Back to the [Map of Content](../docs/index.md).

## Portable

Work in any Claude Code session, and are also distributed as standalone ZIPs for Claude Desktop and claude.ai web.

| Skill | Base doc | Description |
|-------|----------|-------------|
| `/assess` | [`assess/SKILL.md`](./assess/SKILL.md) | Layered AI-readiness assessment (0-8 contract model) plus a complexity hotspot SVG and a doc-navigability graph SVG |
| `/huddle` | [`huddle/SKILL.md`](./huddle/SKILL.md) | Multi-perspective deliberation using Six Thinking Hats with Fibonacci team sizing |
| `/deslop` | [`deslop/SKILL.md`](./deslop/SKILL.md) | Detect and remove the telltale signs of AI writing; ships a [`references/full-checklist.md`](./deslop/references/full-checklist.md) |
| `/ghsync` | [`ghsync/SKILL.md`](./ghsync/SKILL.md) | Bulk-clone and fast-forward sync every GitHub repo you can access across an org |
| `/skill-forge` | [`skill-forge/SKILL.md`](./skill-forge/SKILL.md) | Harden a skill through judge-panel refinement rounds to a 3-tier promotion gate; refined through its own process |
| `/semantic-compress` | [`semantic-compress/SKILL.md`](./semantic-compress/SKILL.md) | Compress LLM-directed instructions in two modes: a local core->pointer pass, and an A/B-validated distill loop producing the smallest behaviourally-equivalent document. Point at core knowledge the model holds, keep project-specific detail verbatim. Forged by `/skill-forge` |

## Assessment helpers

`/assess` is split into an orchestrator plus two render-time helper skills:

| Skill | Base doc | Description |
|-------|----------|-------------|
| `assess-findings` | [`assess-findings/SKILL.md`](./assess-findings/SKILL.md) | Renders the report from the deterministic `run-context.json` and the layer scorecard |
| `assess-pr` | [`assess-pr/SKILL.md`](./assess-pr/SKILL.md) | The end-of-run offers - open a PR, track the Top 3 Actions, freeze a CI gate |

## Team-orchestration library skills

Invoked by the workflow commands ([`/tm`](../commands/tm.md), [`/issues`](../commands/issues.md), [`/fix-pr`](../commands/fix-pr.md), [`/fix-develop`](../commands/fix-develop.md)), never standalone. Excluded from the standalone-ZIP build.

| Skill | Base doc | Description |
|-------|----------|-------------|
| `marathon` | [`marathon/SKILL.md`](./marathon/SKILL.md) | Parallel agent marathon orchestration: DAG analysis, waves, crash recovery, retrospective |
| `pr-review-merge` | [`pr-review-merge/SKILL.md`](./pr-review-merge/SKILL.md) | The PR review-to-green loop plus smart merge |
