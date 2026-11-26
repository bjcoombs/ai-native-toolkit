"""``report.md``'s "Opportunities" section (issue #746), rendered from ``fleet-data.json``.

Reads the ``opportunities`` object ``lib/fleet/fleet_opportunities.py`` computed (so the section and
the JSON always agree and carry the same privacy) plus this run's achievements for the
display-only recent progress, after a "What's working across the fleet" section (``whats_working``, #1334)
that names the practices every measured repository holds at the top level and those held most widely.
The opportunities' first line starts ``In place:`` (``lib/vocabulary.IN_PLACE``, #743) and points at it; then the
biggest lift, the shared-template leverage and the recent progress, each phrased as an
opportunity. Repository and unit names are listed in key order, never numbered or ranked.
Deterministic; no date.

`opportunities_section` renders `report.md`'s "What's working across the fleet" section (#1334: each
practice at the top level in every measured repository, then up to `MOST_HELD` held most widely, from
`opportunities.in_place`, so the counts are stated once, there) and its "Opportunities" section from a built
`FleetData` (so the report carries the JSON's privacy) and this run's `Achievements`:
an `In place:` line first (pointing at "What's working", or, when no practice is at the top level in
any measured repository, saying so in its own words, #1470), then the biggest lift, the shared templates and the display-only
recent progress (this run's `level_up` achievements, from the history; otherwise a
nothing-new line, a starting-line line on a kept first run (`fleet_achievements.BASELINE_NOTE`),
or the achievements note when the run is not kept). None when `opportunities` is null. Names are listed in key
order, at most `PER_LIST` per line. Then "Learn from" (#1155): per repository, and per
monorepo unit, whose next gate a sibling already holds, the gate's change and up to three
siblings (`learn_from` in `fleet-data.json`, `lib.fleet.fleet_learn_from`), at most `PER_LIST`
lines. Plain counts-only output writes no `learn_from`, so the block names no one there and
says so. Tests: `tests/test_fleet_opportunities.py`, `tests/test_learn_from.py`.
"""
from __future__ import annotations

from collections.abc import Sequence

from lib.fleet.fleet_achievements import BASELINE_NOTE, LEVEL_UP, Achievements
from lib.fleet.fleet_data import FleetData
from lib.fleet.fleet_next_steps import code, text
from lib.fleet.fleet_opportunities import PER_LIST, TITLE, InPlace, Opportunities
from lib.fleet.fleet_tooling_map import KINDS
from lib.scoring.headline import GATE_CHANGE
from lib.vocabulary import IN_PLACE

INTRO = ("Computed from this run's data with no model call. Each line is a change and what it "
         "unlocks; a repository the run could not measure is never counted, and no team is ranked.")


def _names(keys: Sequence[str]) -> str:
    shown = ", ".join(code(k) for k in keys[:PER_LIST])
    rest = len(keys) - PER_LIST
    return shown + (f" and {rest} more in `fleet-data.json`" if rest > 0 else "")


WORKING_TITLE = "What's working across the fleet"
WORKING_ANCHOR = "#whats-working-across-the-fleet"
MOST_HELD = 3  # practices named as held most widely, after the ones every repository holds


def _held(p: InPlace, titles: dict[str, str]) -> str:
    return (f"- {text(titles.get(p['row'], p['row']))}: {p['at_top']} of {p['measured']} measured "
            f"{'repository' if p['measured'] == 1 else 'repositories'} at the top level.")


def whats_working(opps: Opportunities, titles: dict[str, str]) -> list[str]:
    """The "What's working across the fleet" section (#1334), ahead of the opportunities: the
    practices every measured repository holds at the top level, then up to ``MOST_HELD`` more
    held most widely (share at the top level, then model order). Only measured levels count."""
    rows = opps["in_place"]
    every = [p for p in rows if p["measured"] > 0 and p["at_top"] == p["measured"]]
    rest = sorted((p for p in rows if 0 < p["at_top"] < p["measured"]),
                  key=lambda p: -p["at_top"] / p["measured"])[:MOST_HELD]
    lines = [f"## {WORKING_TITLE}", ""]
    if not every and not rest:
        return lines + [f"{IN_PLACE} 0 of {len(rows)} practices at the top level in any measured repository "
                        "yet; the opportunities below say where to start.", ""]
    head = f"{IN_PLACE} {len(every)} of {len(rows)} practices at the top level in every measured repository"
    if rest:
        head += f", and {len(rest)} more held most widely" if every else f"; {len(rest)} held most widely"
    return lines + [f"{head}.", "", *(_held(p, titles) for p in [*every, *rest]), ""]


def _in_place(opps: Opportunities) -> str:
    if not opps["in_place"]:
        return f"{IN_PLACE} the maturity model has no team practices to hold."
    if not any(p["at_top"] > 0 for p in opps["in_place"]):  # #1470: What's working lists none
        return (f"{IN_PLACE} no measured repository holds any of the {len(opps['in_place'])} practices "
                "at the top level yet; the changes below lift the most repositories first.")
    return (f"{IN_PLACE} the practices under [{WORKING_TITLE}]({WORKING_ANCHOR}) above, out of "
            f"{len(opps['in_place'])}; the changes below lift the most repositories next.")


def _lift(opps: Opportunities) -> list[str]:
    lift = opps["biggest_lift"]
    if lift is None:
        return ["**Biggest lift.** No single change would lift a measured repository this run: each one "
                "is at the top level, needs more than one change, or was not measured."]
    line = f"**Biggest lift.** {lift['text']} Applies to {_names(lift['repos'])}."
    if lift["change"] is not None:
        line += f" {lift['change']}"
    else:  # no wording for this signal: name the criterion so the change is still clear
        line += f" The criterion to meet: {code(lift['signal'])}."
    if lift["done_when"] is not None:
        line += f" Done when: {lift['done_when']}"
    return [line]


def _templates(opps: Opportunities) -> list[str]:
    lines = ["**Shared templates.**", ""]
    if not opps["shared_templates"]:
        return lines + ["No shared CI source stands between its repositories and a level this run."]
    for t in opps["shared_templates"]:
        kind = KINDS.get(t["kind"], t["kind"])
        lines.append(f"- {code(t['source'])} ({kind}): {t['text']} Applies to {_names(t['repos'])}.")
    return lines


def _progress(doc: Achievements | None) -> list[str]:
    lines = ["**Recent progress.**", ""]
    if doc is None:
        return lines + ["Not shown: this run's achievements were not written."]
    ups = sorted((a for a in doc["achievements"] if a["run"] == doc["run"] and a["kind"] == LEVEL_UP),
                 key=lambda a: (a["view"], a["unit"], a["evidence"]["row"] or ""))
    if len(doc["runs"]) < 2 and doc["note"] not in (None, BASELINE_NOTE):  # --no-history, --full: say why
        return lines + [f"Not shown: {doc['note']}, so there is no earlier run to show progress against."]
    if len(doc["runs"]) < 2:
        return lines + ["This run is the starting line, so there is no earlier run to show progress against."]
    if not ups:
        return lines + [f"No unit reached a new level since {code(doc['runs'][-2])}; the next run can show it."]
    lines.append(f"Since {code(doc['runs'][-2])}, from the run history (shown only, never counted):")
    lines += [f"- {code(a['unit'])} reached level {a['evidence']['to_level']} on {code(a['evidence']['row'] or '')}."
              for a in ups[:PER_LIST]]
    if len(ups) > PER_LIST:
        lines.append(f"- And {len(ups) - PER_LIST} more in `achievements.json`.")
    return lines


def _change(gate: str) -> str:
    return GATE_CHANGE.get(gate, f"the {gate} gate holds")


def _learn(data: FleetData) -> list[str]:
    """The "Learn from" lines (#1155): who already holds each next gate."""
    lines = ["**Learn from.**", ""]
    if data["run"]["counts_only"] and not data["run"]["paths_hashed"]:
        return lines + ["Not shown: counts-only output names no repository or unit to learn from."]
    found: list[str] = []
    for repo in data["repos"]:
        nxt = repo["next_step"]
        gates = [b["gate"] for b in (nxt["blockers"] if nxt is not None else []) if b["kind"] == "gate"]
        if repo["learn_from"] and gates:
            found.append(f"- {code(repo['key'])}: {_change(gates[0])}. "
                         f"Learn from: {', '.join(code(n) for n in repo['learn_from'])}.")
        for row in repo["unit_rows"] or []:
            if row["learn_from"] and row["next_blocker"] and row["key"]:
                found.append(f"- {code(repo['key'])} unit {code(row['key'])}: {_change(row['next_blocker']['gate'])}. "
                             f"Learn from: {', '.join(code(n) for n in row['learn_from'])}.")
    if not found:
        return lines + ["No repository or unit has a next gate that another one already holds this run."]
    more = len(found) - PER_LIST
    return lines + found[:PER_LIST] + ([f"- And {more} more in `fleet-data.json`."] if more > 0 else [])


def opportunities_section(data: FleetData, doc: Achievements | None) -> str | None:
    """The section as Markdown ending in a blank line; None without opportunities (no model)."""
    opps = data.get("opportunities")
    if opps is None:
        return None
    titles = {row["id"]: row["title"] for row in data["model"]}
    working = whats_working(opps, titles) if opps["in_place"] else []
    lines = [*working, f"## {TITLE}", "", _in_place(opps), "", INTRO, "", *_lift(opps), "",
             *_templates(opps), "", *_progress(doc), "", *_learn(data), ""]
    return "\n".join(lines)
