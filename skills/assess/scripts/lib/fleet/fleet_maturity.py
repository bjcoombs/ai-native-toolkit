"""Each group's lowest Step, shared by the fleet's slope chart and trend lines (issue #806).

The slope chart (``lib/fleet/fleet_slope.py``) and the trend lines (``lib/fleet/fleet_trends.py``)
plot one point per group and run, taken here by the #772 roll-up rule (option A), the rule
``fleet-data.json`` units follow (``lib.fleet.fleet_data_steps.step_roll_up``):

- **Lowest Step.** The lowest Step a measured repository of the group holds. No average is
  taken across repositories, so no group is placed beside another on a mean.
- **N of M measured.** ``measured`` counts the group's repositories with a Step; ``repos``
  counts them all. A repository with no Step (failed, or not measured) counts in ``repos``
  only, never as Step 1 or 0; with none measured the Step is unknown (None), never 0.

``SCOPE`` stays the row set the fleet's per-practice views read: ``team`` rows, the only rows
a single repository is measured by.

Pure: no I/O, no ordering dependence.

`group_steps(members, steps)` takes `(group, repository)` pairs and each repository's Step
(`None` when not measured) and gives each group a `StepPoint`: `step`, the lowest Step over
its measured repositories (`None` when none is), with `measured` of `repos`, by
`fleet_data_steps.step_roll_up`, the rule `fleet-data.json` units use (#772 option A). No mean
is taken. `data_steps(data)` reads each repository's Step from a `fleet-data.json`
document by its key (None without one). `measured_text` is the "N of M measured" label both charts print. `SCOPE` is the
`team` row set.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, TypedDict, cast

from lib.fleet.fleet_data_steps import NO_STEPS, RepoSteps, step_roll_up

SCOPE = "team"


class StepPoint(TypedDict):
    step: int | None  # the lowest Step a measured repository holds; None when none is measured
    measured: int  # repositories with a Step
    repos: int  # every repository of the group


def group_steps(members: Iterable[tuple[str, str]],
                steps: Mapping[str, int | None]) -> dict[str, StepPoint]:
    """Each group's lowest Step with ``measured`` of ``repos``, sorted by group; ``members`` are
    ``(group, repository)`` pairs and a repository missing from ``steps`` is not measured."""
    by_group: dict[str, list[RepoSteps]] = {}
    for group, repo in members:
        step = steps.get(repo)
        by_group.setdefault(group, []).append(
            cast(RepoSteps, {**NO_STEPS, "step": step if type(step) is int else None}))
    points: dict[str, StepPoint] = {}
    for group in sorted(by_group):
        roll = step_roll_up((), by_group[group])
        points[group] = {"step": roll["lowest_step"], "measured": roll["measured"], "repos": roll["repos"]}
    return points


def data_steps(data: Mapping[str, Any] | None) -> dict[str, int | None] | None:
    """Each repository's Step as ``fleet-data.json`` gives it, by its key; None without the document."""
    if data is None:
        return None
    return {repo["key"]: repo["step"] if type(repo.get("step")) is int else None
            for repo in data.get("repos") or [] if isinstance(repo.get("key"), str)}


def measured_text(point: StepPoint) -> str:
    """The "N of M measured" label."""
    return f"{point['measured']} of {point['repos']} measured"
