# Next steps: `grpc/sub`

In place: 5 of 7 practices at the top level in every repository of this group.

- Agent instructions: level 2 of 2 in 1 of 1 repository.
- Coverage \| mutation: level 2 of 2 in 1 of 1 repository.
- Agent permissions: level 1 of 1 in 1 of 1 repository.
- Shared tooling: level 1 of 1 in 1 of 1 repository.
- Agent environment: level 1 of 1 in 1 of 1 repository.

Maturity model `next-steps-fixture-1`. 1 repository: 1 scanned, 0 could not be scanned.

Each numbered step is one change: take or leave it on its own. "Done when" is checkable in the repository or its platform settings; the re-run check is optional.

## Levels

In place: 6 of 7 practices measured for this group, each with the roll-up rule it uses.

| Practice | Level | Roll-up |
|----------|-------|---------|
| Group settings | 0 of 2 | group settings `grpc` |
| Agent instructions | 2 of 2 (top) | every repository (min) |
| Coverage \| mutation | 2 of 2 (top) | 50% of repositories (share) |
| Runtime legibility | unknown | every repository (min) |
| Agent permissions | 1 of 1 (top) | 50% of repositories (share) |
| Shared tooling | 1 of 1 (top) | every repository (min) |
| Agent environment | 1 of 1 (top) | every repository (min) |

## Group settings

In place: nothing confirmed for this practice yet (level 0 of 2).

To reach level 1:

1. **Set the group's two-factor authentication requirement to `on`.**
   - Done when: The group settings page shows the two-factor authentication requirement as `on`.
   - Re-run check (optional): the next fleet run reads `two_factor.value` = `on`.
   - Now: `off`.
   - Applies to: group settings `grpc`, covering 1 repository.

## Agent instructions

In place: level 2 of 2, the top level. No next step.

## Coverage \| mutation

In place: level 2 of 2, the top level. No next step.

## Runtime legibility

In place: nothing confirmed for this practice yet; the level is not measured (between 0 and 1 of 1 once measured). This is a measurement gap: the run could not measure every signal the level depends on.

- `observability.rung`: not measured in 1 of 1 repository. Read from committed files by a full `/assess` run and a headless scan alike; unavailable when the scan raised (`scan_failed`) or the scan came from an older toolkit, which measured it in a full run only.
- Repositories whose own level is undetermined (1): `grpc/sub/app`.

## Agent permissions

In place: level 1 of 1, the top level. No next step.

## Shared tooling

In place: level 1 of 1, the top level. No next step.

## Agent environment

In place: level 1 of 1, the top level. No next step.
