# Next steps: `grpa`

Rubric `next-steps-fixture-1`. 3 repositories: 2 scanned, 1 failed.

Each numbered step is one change: take or leave it on its own. "Done when" is checkable in the repository or its platform settings; the re-run check is optional.

## Levels

| Row | Level | Roll-up |
|-----|-------|---------|
| Group settings | 1 of 2 | group settings `grpa` |
| Agent instructions | unknown | every repository (min) |
| Coverage \| mutation | 1 of 2 | 50% of repositories (share) |
| Runtime legibility | unknown | every repository (min) |
| Agent permissions | unknown | 50% of repositories (share) |
| Shared tooling | unknown | every repository (min) |
| Agent environment | 0 of 1 | every repository (min) |

## Group settings

Level 1 of 2. To reach level 2:

1. **Set the group's push rules to `on`.**
   - Done when: The group settings page shows the push rules as `on`.
   - Re-run check (optional): the next fleet run reads `push_rules.value` = `on`.
   - Now: set.
   - Applies to: group settings `grpa`, covering 3 repositories.

## Agent instructions

Level unknown (between 0 and 1 of 2). This is a measurement gap, not a failure: the run could not measure every signal the level depends on.

- `agent_environment.check.named_total`: not measured in 1 of 3 repositories. The repository scan failed in each of them; the reason token is listed below.
- `nested_instructions.budget.claude_code.bytes`: not measured in 1 of 3 repositories. The repository scan failed in each of them; the reason token is listed below.
- `nested_instructions.budget.claude_code.lines`: not measured in 1 of 3 repositories. The repository scan failed in each of them; the reason token is listed below.
- Repositories whose own level is undetermined (1): `grpa/svc3` (scan failed: `scan_timeout`).

Measured unmet criteria already cap the group at level 1, whatever the missing signals show. Toward level 2:

1. **Name the command an agent runs to check its work (for example `make check` or `npm test`) in the agent instruction file.**
   - Done when: `CLAUDE.md` or `AGENTS.md` names a check command that exists in the repository (a Makefile target, a package script or a committed script).
   - Re-run check (optional): the next fleet run reads `agent_environment.check.named_total` > 0.
   - Holding the group back (1): `grpa/svc2` (now 0).
2. **Cut the always-loaded Claude Code instructions (root `CLAUDE.md`, the files it imports, unscoped `.claude/rules/*.md`) to 200 lines or fewer.**
   - Done when: `wc -l` over those files totals 200 lines or fewer.
   - Re-run check (optional): the next fleet run reads `nested_instructions.budget.claude_code.lines` <= 200.
   - Holding the group back (1): `grpa/svc2` (now 340).

## Coverage \| mutation

Level 1 of 2. To reach level 2: 0 of 3 repositories already there; the rule needs 2.

1. **Run mutation testing against the test suite.**
   - Done when: A mutation tool (mutmut, Stryker, PIT or similar) is configured in the repository and a run of it completes.
   - Re-run check (optional): the next fleet run reads `test_pressure.mutation_run` = true.
   - Holding the group back (2): `grpa/svc1` (now false), `grpa/svc2` (now false).

Below level 2 with no unmet criterion found, because a signal was not measured (1): `grpa/svc3` (scan failed: `scan_timeout`).

## Runtime legibility

Level unknown (between 0 and 1 of 1). This is a measurement gap, not a failure: the run could not measure every signal the level depends on.

- `observability.rung`: not measured in 3 of 3 repositories. Only a full `/assess` run reports the observability rung; a headless fleet scan leaves it unavailable.
- Repositories whose own level is undetermined (3): `grpa/svc1`, `grpa/svc2`, `grpa/svc3` (scan failed: `scan_timeout`).

## Agent permissions

Level unknown (between 0 and 1 of 1). This is a measurement gap, not a failure: the run could not measure every signal the level depends on.

- `agent_ops.summary.permissions_encoded`: not measured in 1 of 3 repositories. The repository scan failed in each of them; the reason token is listed below.
- Repositories whose own level is undetermined (1): `grpa/svc3` (scan failed: `scan_timeout`).

## Shared tooling

Level unknown (between 0 and 1 of 1). This is a measurement gap, not a failure: the run could not measure every signal the level depends on.

- `shared_tooling.available`: not measured in 1 of 3 repositories. The repository scan failed in each of them; the reason token is listed below.
- Repositories whose own level is undetermined (1): `grpa/svc3` (scan failed: `scan_timeout`).

## Agent environment

Level 0 of 1. To reach level 1: 1 of 3 repositories already there; the rule needs 3.

1. **Name a check command that resolves (a Makefile target, a package script or a committed script) in the agent instruction file.**
   - Done when: `CLAUDE.md` or `AGENTS.md` names a check command that exists in the repository.
   - Re-run check (optional): the next fleet run reads `agent_environment.layer5_cap.applies` = false.
   - Holding the group back (1): `grpa/svc2` (now true; unmet: `check_named`).

Below level 1 with no unmet criterion found, because a signal was not measured (1): `grpa/svc3` (scan failed: `scan_timeout`).
