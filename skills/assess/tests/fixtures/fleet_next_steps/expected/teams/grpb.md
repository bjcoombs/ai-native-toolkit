# Next steps: `grpb`

Maturity model `next-steps-fixture-1`. 1 repository: 1 scanned, 0 failed.

Each numbered step is one change: take or leave it on its own. "Done when" is checkable in the repository or its platform settings; the re-run check is optional.

## Levels

| Row | Level | Roll-up |
|-----|-------|---------|
| Group settings | unknown | group settings |
| Agent instructions | 1 of 2 | every repository (min) |
| Coverage \| mutation | 0 of 2 | 50% of repositories (share) |
| Runtime legibility | unknown | every repository (min) |
| Agent permissions | 1 of 1 (top) | 50% of repositories (share) |
| Shared tooling | 0 of 1 | every repository (min) |
| Agent environment | 0 of 1 | every repository (min) |

## Group settings

Level unknown. This is a measurement gap, not a failure: the run could not measure every signal the level depends on.

- No ghreport settings matched this group: pass ghreport's settings to the fleet run with `--settings`.

## Agent instructions

Level 1 of 2. To reach level 2: 0 of 1 repository already there; the rule needs 1.

1. **Name the command an agent runs to check its work (for example `make check` or `npm test`) in the agent instruction file.**
   - Done when: `CLAUDE.md` or `AGENTS.md` names a check command that exists in the repository (a Makefile target, a package script or a committed script).
   - Re-run check (optional): the next fleet run reads `agent_environment.check.named_total` > 0.
   - Holding the group back (1): `grpb/lib1` (now 0).

## Coverage \| mutation

Level 0 of 2. To reach level 1: 0 of 1 repository already there; the rule needs 1.

1. **Make the test run fail when line coverage drops below a floor.**
   - Done when: The test command CI runs enforces a coverage threshold (`fail_under` in `pyproject.toml`, `--cov-fail-under`, Jest `coverageThreshold` or similar), and a run below it fails.
   - Re-run check (optional): the next fleet run reads `coverage_gate.enforced` = true.
   - Holding the group back (1): `grpb/lib1` (now false).

## Runtime legibility

Level unknown (between 0 and 1 of 1). This is a measurement gap, not a failure: the run could not measure every signal the level depends on.

- `observability.rung`: not measured in 1 of 1 repository. Only a full `/assess` run reports the observability rung; a headless fleet scan leaves it unavailable.
- Repositories whose own level is undetermined (1): `grpb/lib1`.

## Agent permissions

Level 1 of 1: the top level. No next step.

## Shared tooling

Level 0 of 1. To reach level 1: 0 of 1 repository already there; the rule needs 1.

1. **Meet `shared_tooling.available` = true.** No repository-native wording exists for this signal.
   - Done when: the next fleet run reads `shared_tooling.available` = true.
   - Holding the group back (1): `grpb/lib1` (now false).

## Agent environment

Level 0 of 1. To reach level 1: 0 of 1 repository already there; the rule needs 1.

1. **Name a check command that resolves (a Makefile target, a package script or a committed script) in the agent instruction file and commit a lockfile for every dependency manifest, or a setup (`.devcontainer/`, a setup script, `copilot-setup-steps`).**
   - Done when: `CLAUDE.md` or `AGENTS.md` names a check command that exists in the repository, and every dependency manifest has its lockfile committed, or a devcontainer or setup script is committed.
   - Re-run check (optional): the next fleet run reads `agent_environment.layer5_cap.applies` = false.
   - Holding the group back (1): `grpb/lib1` (now true; unmet: `check_named`, `setup_or_lockfile`).
