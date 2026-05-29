# CLAUDE.md

Lean in-repo contract. Topic-specific guidance is factored into on-demand
skills so the agent only loads what is relevant - progressive disclosure
keeps this file short and the context window focused.

## Scope

Repo-specific rules only. Global conventions live in the user's config and
aren't repeated here.

## Conventions by topic

- Java conventions (logging, dependency injection, testing) load on demand via
  the `java-conventions` skill.
- Go conventions (Connect-Go Content-Type, protobuf JSON) load on demand via
  the `go-conventions` skill.

Each skill lives under `.claude/skills/<name>/SKILL.md` and is loaded when the
router matches its trigger. Prefer adding a new skill over inlining a wall of
text here, because a lean pointer file beats a monolith the agent must hold in
full context.

## Verifiable outcomes

- Working if: `pytest` passes from the repo root.
- Run the linter in tools/lint.sh before pushing.

## Where things live

- Source: src/app/
- Tests: tests/
