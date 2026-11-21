"""``report.md``'s first section, "What changed and who earned it" (issue #704, parent #692).

Rendered from ``achievements.json`` (``lib/fleet_achievements.py``) and the maturity
model, before the diagrams, so the report opens on credit and the next step:

1. **Earned this run**: this run's achievements, by kind (level-ups, firsts, targets
   hit, personal bests, streaks), each its own credit line; within a kind, by view and
   unit key. Nothing is ordered by score, and units are never compared with each other.
   At most ``PER_KIND`` lines per kind; the rest are counted and stay in
   ``achievements.json``.
2. **Next level-ups**: each unit's (not repository's) nearest level-up: the row whose
   step needs the fewest repositories, then maturity-model order. At most ``MAX_STEPS``.
3. **Recount**, on the first run under a new maturity-model version (``recount`` in
   ``achievements.json``, ``lib/fleet_rescore.py``): each move the rule change alone caused,
   raised moves first. These are not achievements and take nothing away: every line says the
   repository did not change. At most ``PER_KIND`` lines.
4. **Targets**, when a targets file was given: met, or the distance (in levels for a
   level target, in share of top level for a share target).

Every line is phrased as credit or as a next step. Deterministic; no date.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from lib.fleet_achievements import KINDS, REPO_VIEW, Achievements, NextStep, TargetProgress
from lib.fleet_next_steps import code, text
from lib.fleet_rescore import NOW_MEASURED, UP
from lib.levels import MaturityModel

TITLE = "What changed and who earned it"
PER_KIND = 20
MAX_STEPS = 30
KIND_LABELS = {"level_up": "Level-ups", "first": "Firsts", "target_hit": "Targets hit",
               "personal_best": "Personal bests", "streak": "Streaks"}


def _intro(doc: Achievements) -> str:
    rule = "Every line is computed from run data."
    if doc["recount"] is not None:
        rule += (" A move caused by a change to the maturity model's rules is listed under Recount, "
                 "never as an achievement.")
    if len(doc["runs"]) < 2:
        return f"This run is the starting line ({doc['note'] or 'no earlier comparable run'}). {rule}"
    return (f"Since {code(doc['runs'][-2])}, over {len(doc['runs'])} comparable runs in this fleet's "
            f"history. {rule}")


def _earned(doc: Achievements) -> list[str]:
    mine = [a for a in doc["achievements"] if a["run"] == doc["run"]]
    if not mine:
        return ["Nothing new this run. The next level-ups below say what earns the next one."]
    lines: list[str] = []
    for kind in KINDS:
        of_kind = [a for a in mine if a["kind"] == kind]
        if not of_kind:
            continue
        lines += [f"**{KIND_LABELS[kind]}**", ""]
        lines += [f"- {a['credit']}" for a in of_kind[:PER_KIND]]
        if len(of_kind) > PER_KIND:
            lines.append(f"- And {len(of_kind) - PER_KIND} more in `achievements.json`.")
        lines.append("")
    return lines[:-1]


def _recount(doc: Achievements) -> list[str]:
    counted = doc["recount"]
    if counted is None:
        return []
    head = (f"The maturity model changed from {code(counted['from_version'])} to {code(counted['to_version'])}. "
            f"Run {code(counted['run'])} is re-scored under the new rules; these moves come from the rules, "
            "not from the repositories, and are not earned.")
    raised = [line for line in counted["lines"] if line["direction"] in (UP, NOW_MEASURED)]
    rest = [line for line in counted["lines"] if line["direction"] not in (UP, NOW_MEASURED)]
    ordered = raised + rest
    lines = ["### Recount", "", head, ""]
    if not ordered:
        return [*lines, "No repository's level moved under the new rules.", ""]
    lines += [f"- {line['text']}" for line in ordered[:PER_KIND]]
    if len(ordered) > PER_KIND:
        lines.append(f"- And {len(ordered) - PER_KIND} more in `achievements.json`.")
    return [*lines, ""]


def team_steps(steps: Sequence[NextStep], order: Mapping[str, int]) -> list[NextStep]:
    """One step per unit: fewest repositories needed, then maturity-model row order."""
    best: dict[tuple[str, str], NextStep] = {}
    for step in steps:
        if step["view"] == REPO_VIEW:
            continue
        key = (step["view"], step["unit"])
        rank = (step["needed"], order.get(step["row"], len(order)))
        held = best.get(key)
        if held is None or rank < (held["needed"], order.get(held["row"], len(order))):
            best[key] = step
    return [best[key] for key in sorted(best)]


def _steps(doc: Achievements, order: Mapping[str, int]) -> list[str]:
    chosen = team_steps(doc["nearest_level_ups"], order)
    if not chosen:
        return ["No unit has a measured next level-up in this run."]
    lines = [f"- {step['credit']}" for step in chosen[:MAX_STEPS]]
    if len(chosen) > MAX_STEPS:
        lines.append(f"- And {len(chosen) - MAX_STEPS} more units in `achievements.json`.")
    return lines


def _pct(share: float) -> str:
    return f"{share * 100:.0f}%"


def _target(t: TargetProgress, titles: Mapping[str, str]) -> str:
    title = text(titles.get(t["row"], t["row"]))
    if t["share_target"] is not None:
        where = f"{code(t['unit'])}, mean share {_pct(t['share_target'])} of top level on {title}"
        if t["share"] is None or t["share_distance"] is None:
            return f"- {where}: not measured this run; the next measured run shows the distance."
        if t["share_distance"] == 0:
            return f"- {where}: met ({_pct(t['share'])})."
        return f"- {where}: at {_pct(t['share'])}, {_pct(t['share_distance'])} to go."
    where = f"{code(t['unit'])}, level {t['target']} on {title}"
    if t["distance"] is None:
        return f"- {where}: not measured this run; the next measured run shows the distance."
    if t["distance"] == 0:
        return f"- {where}: met."
    plural = "level" if t["distance"] == 1 else "levels"
    return f"- {where}: at level {t['level']}, {t['distance']} {plural} to go."


def credit_section(doc: Achievements, model: MaturityModel | None) -> str:
    """The section as Markdown, ending in a blank line."""
    rows = model["rows"] if model is not None else []
    order = {row["id"]: i for i, row in enumerate(rows)}
    titles = {row["id"]: row["title"] for row in rows}
    lines = [f"## {TITLE}", "", _intro(doc), "", "### Earned this run", "", *_earned(doc), "",
             *_recount(doc), "### Next level-ups", "", *_steps(doc, order), ""]
    if doc["targets"]:
        lines += ["### Targets", "", *(_target(t, titles) for t in doc["targets"]), ""]
    return "\n".join(lines)
