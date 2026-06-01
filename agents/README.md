# Agents

Subagent definitions invoked by the skills, or directly via `Task(subagent_type=...)`. Each file carries the frontmatter contract (`name`, `description`, `model`, `color`) documented in [`CLAUDE.md`](../CLAUDE.md). Back to the [Map of Content](../docs/index.md).

## Six Thinking Hats team

Orchestrated by [`/huddle`](../skills/huddle/SKILL.md) and [`/6hats`](../commands/6hats.md):

| Agent | Role |
|-------|------|
| [`white-hat`](./white-hat.md) | Facts and evidence |
| [`red-hat`](./red-hat.md) | Gut feelings and emotional drivers |
| [`black-hat`](./black-hat.md) | Risks and critical analysis |
| [`yellow-hat`](./yellow-hat.md) | Benefits and opportunities |
| [`green-hat`](./green-hat.md) | Creative alternatives |
| [`blue-hat`](./blue-hat.md) | Synthesis and recommendation |
| [`scribe`](./scribe.md) | Structures hat output into actionable documentation |

## Assessment

| Agent | Role |
|-------|------|
| [`assess-layer-scorer`](./assess-layer-scorer.md) | Scores a codebase against the [`/assess`](../skills/assess/SKILL.md) 0-8 layered contract model, assigning Present / Partial / Missing per layer with evidence |
