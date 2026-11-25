# Fleet diagrams

## Fleet at a glance

In place: 3 repositories with a measured Step.

How many repositories sit at each Step, what moved since the last run, and which gate would move the most repositories?

| Step | Repositories |
|------|--------------|
| Step 1 · Assisted | 1 |
| Step 2 · Docs | 2 |
| Step 3 · Docs and CI | 0 |

1 repository had no measured Step in this run, so it is not counted at any Step.

**Since the last run.** 1 repository moved up a Step and 1 repository moved down; 1 stayed at the same Step. 1 move came from rule changes alone.

**One change away.** 1 repository is one change from the next Step.

**The gates that would move the most repositories**, most first:

1. `ci`: make the ci gate hold. Moves 1 repository to the next Step.

**Team pages** are listed in [`teams.md`](./teams.md), which `fleet_next_steps.py` writes.

Drawn from this run's output with no model call; unknown is hatched and named, never drawn as zero.

## What changed and who earned it

In place: credit.
