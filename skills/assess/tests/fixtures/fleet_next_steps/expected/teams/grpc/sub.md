# Next steps: `grpc/sub`

Rubric `next-steps-fixture-1`. 1 repository: 1 scanned, 0 failed.

Each numbered step is one change: take or leave it on its own. "Done when" is checkable in the repository or its platform settings; the re-run check is optional.

## Levels

| Row | Level | Roll-up |
|-----|-------|---------|
| Group settings | 0 of 2 | group settings `grpc` |
| Agent instructions | 2 of 2 (top) | every repository (min) |
| Coverage \| mutation | 2 of 2 (top) | 50% of repositories (share) |
| Runtime legibility | unknown | every repository (min) |
| Agent permissions | 1 of 1 (top) | 50% of repositories (share) |
| Shared tooling | 1 of 1 (top) | every repository (min) |
| Agent environment | 1 of 1 (top) | every repository (min) |

## Group settings

Level 0 of 2. To reach level 1:

1. **Set the group's two-factor authentication requirement to `on`.**
   - Done when: The group settings page shows the two-factor authentication requirement as `on`.
   - Re-run check (optional): the next fleet run reads `two_factor.value` = `on`.
   - Now: `off`.
   - Applies to: group settings `grpc`, covering 1 repository.

## Agent instructions

Level 2 of 2: the top level. No next step.

## Coverage \| mutation

Level 2 of 2: the top level. No next step.

## Runtime legibility

Level unknown (between 0 and 1 of 1). This is a measurement gap, not a failure: the run could not measure every signal the level depends on.

- `observability.rung`: not measured in 1 of 1 repository. Only a full `/assess` run reports the observability rung; a headless fleet scan leaves it unavailable.
- Repositories whose own level is undetermined (1): `grpc/sub/app`.

## Agent permissions

Level 1 of 1: the top level. No next step.

## Shared tooling

Level 1 of 1: the top level. No next step.

## Agent environment

Level 1 of 1: the top level. No next step.
