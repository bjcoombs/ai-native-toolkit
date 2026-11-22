"""``report.md``'s "Opportunities" section (issue #746), rendered from ``fleet-data.json``.

Reads the ``opportunities`` object ``lib/fleet_opportunities.py`` computed (so the section and
the JSON always agree and carry the same privacy) plus this run's achievements for the
display-only recent progress. The first line starts ``In place:`` (``lib/vocabulary.IN_PLACE``, #743); then the
biggest lift, the shared-template leverage and the recent progress, each phrased as an
opportunity. Repository and unit names are listed in key order, never numbered or ranked.
Deterministic; no date.
"""
from __future__ import annotations

from collections.abc import Sequence

from lib.fleet_achievements import BASELINE_NOTE, LEVEL_UP, Achievements
from lib.fleet_data import FleetData
from lib.fleet_next_steps import code, text
from lib.fleet_opportunities import PER_LIST, TITLE, Opportunities
from lib.fleet_tooling_map import KINDS
from lib.vocabulary import IN_PLACE

INTRO = ("Computed from this run's data with no model call. Each line is a change and what it "
         "unlocks; a repository the run could not measure is never counted, and no team is ranked.")


def _names(keys: Sequence[str]) -> str:
    shown = ", ".join(code(k) for k in keys[:PER_LIST])
    rest = len(keys) - PER_LIST
    return shown + (f" and {rest} more in `fleet-data.json`" if rest > 0 else "")


def _in_place(opps: Opportunities, titles: dict[str, str]) -> str:
    if not opps["in_place"]:
        return f"{IN_PLACE} the maturity model has no team rows to hold."
    parts = [f"{p['at_top']} of {p['measured']} measured on {text(titles.get(p['row'], p['row']))}"
             for p in opps["in_place"]]
    return f"{IN_PLACE} repositories at the top level, {'; '.join(parts)}."


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


def opportunities_section(data: FleetData, doc: Achievements | None) -> str | None:
    """The section as Markdown ending in a blank line; None without opportunities (no model)."""
    opps = data.get("opportunities")
    if opps is None:
        return None
    titles = {row["id"]: row["title"] for row in data["model"]}
    lines = [f"## {TITLE}", "", _in_place(opps, titles), "", INTRO, "", *_lift(opps), "",
             *_templates(opps), "", *_progress(doc), ""]
    return "\n".join(lines)
