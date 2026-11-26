"""The Step ladder (``step-ladder.svg``, issue #1102): where the run stands on the autonomy Steps.

``render_ladder(level, steps)`` draws one rung per Step of the maturity model
(``levels/default.toml`` ``[[steps]]``), bottom to top, each with its number, its model
name and one plain sentence of what an agent can do there (``STEP_MEANING``, the one
table of those sentences; a Step it does not know reads its model name only). Between
two rungs sits the gate that lifts you to the upper one: the gates the upper Step adds
over the one below, worded by ``headline.GATE_CHANGE`` ("CI turns red on a broken check"), each marked
"in place", "not yet", "not measured" or "excluded by owner" from the level result's
conditions (``gate_state``; a gate holds when its row's conditions 1..level are met, and
missing evidence never reads "not yet"). The run's Step (``level["step"]``) carries the "You are
here" marker; the gate below ``level["next_step"]`` is drawn in the warn colour as the
next one. When one change would take the run past its next Step (``headline.one_change_ceiling``
over the level result's ``ceiling_step``, #1333), the Step it reaches carries the "One change
away" marker and ``data-one-change``, and each gate the result lists in ``held_above`` reads
"already in place" rather than "in place". Nothing is hard-coded about the number of Steps or
their gates.

``ladder_in_place`` and ``ladder_caption`` are the report section's opening line and
caption; the caption reads ``next_step.blockers`` the way the headline's Next line does, so
the two name the same unlock. ``lib/assess_diagrams.py`` builds the diagram entry and puts it first in the
report. Rungs carry ``data-step`` and ``data-state`` (``reached``, ``here``, ``ahead``),
gate bands ``data-gate-to`` and, on the next one, ``data-next``, so tests read the
picture back. Deterministic: model order, no date, no set iteration.
Tests: ``tests/test_assess_diagrams_ladder.py``.
"""
from __future__ import annotations

from collections.abc import Sequence

import lib.fleet.fleet_svg as svg
from lib.svg_scope import SCOPES
from lib.scoring.headline import GATE_CHANGE, gate_change, one_change_ceiling
from lib.scoring.level_result_types import LevelResult
from lib.scoring.levels import StepDef, StepGate
from lib.vocabulary import IN_PLACE

LADDER_FILE = "step-ladder.svg"
LADDER_TITLE = "Where you are on the Steps"
# The mark on a gate held above the run's Step, and the label on the Step one change reaches (#1333).
ALREADY = "already in place"
ONE_CHANGE = "One change away"

# What an agent can do at each Step, in one plain sentence: the one table of them.
STEP_MEANING: dict[int, str] = {
    1: "an agent can read and help with your code",
    2: "an agent opens pull requests, a person reviews each one",
    3: "agents open pull requests nobody has to watch",
    4: "agents run longer loops on their own",
}

PAD = 16.0
WIDTH = 680.0
RUNG_H = 52.0
GATE_H = 40.0
RUNG_X = PAD + 24.0
RUNG_W = 500.0


def meaning(step: int) -> str:
    """The plain sentence for a Step, or ``""`` for a Step the table does not know."""
    return STEP_MEANING.get(step, "")


def new_gates(steps: Sequence[StepDef], step: int) -> list[StepGate]:
    """The gates Step ``step`` adds over the Step below it, in model order."""
    by_step = {s["step"]: s for s in steps}
    below = {g["id"] for g in by_step[step - 1].get("gates", [])} if step - 1 in by_step else set()
    return [g for g in by_step[step].get("gates", []) if g["id"] not in below]


def gate_state(level: LevelResult, gate: StepGate) -> str:
    """What the run read for a gate: ``in place`` when its row's conditions 1..level are all
    met, else read from the first one not met, as the level engine picks the gate's blocker
    (``level_tally._gate_blocker``): ``not measured``, ``excluded by owner`` (not
    applicable), or ``not yet`` (unmet, declined, or absent from the result). Missing
    evidence never reads as "not yet"."""
    by_id = {c["id"]: c["state"] for c in level["conditions"]}
    states = [by_id.get(f"{gate['row']}.{n}", "unmet") for n in range(1, gate["level"] + 1)]
    first = next((state for state in states if state != "met"), None)
    if first is None:
        return "in place"
    return {"not_measured": "not measured", "not_applicable": "excluded by owner"}.get(first, "not yet")


def _held_state(level: LevelResult, gate: StepGate) -> str:
    """``gate_state``, but ``already in place`` for a gate the level result lists in
    ``held_above`` (#1333): held above the run's Step before the Step below it is reached."""
    state = gate_state(level, gate)
    return ALREADY if state == "in place" and gate["id"] in level.get("held_above", []) else state


def _gate_words(gate: StepGate) -> str:
    return GATE_CHANGE.get(gate["id"], f"the {gate['id']} gate holds")


def _rung(x: float, y: float, step: StepDef, state: str, one_change: bool = False) -> list[str]:
    paint = {"here": 'class="f-accent s-accent"', "reached": 'class="f-bg s-accent"',
             "ahead": 'class="f-bg s-warn"' if one_change else 'class="f-bg s-rule"'}[state]
    head_cls, body_cls = {"here": ("bg", "bg"), "reached": ("fg", "fg"), "ahead": ("muted", "muted")}[state]
    mark = ' data-one-change="true"' if one_change else ""
    parts = [f'<rect x="{x:.1f}" y="{y:.1f}" width="{RUNG_W:.1f}" height="{RUNG_H:.1f}" rx="6" '
             f'{paint} stroke-width="2" data-step="{step["step"]}" data-state="{state}"{mark}/>',
             svg.text(x + 14, y + 21, f"Step {step['step']} · {step['name']}", size=14,
                      weight="600", cls=head_cls)]
    sentence = meaning(step["step"])
    if sentence:
        parts.append(svg.text(x + 14, y + 40, sentence[:1].upper() + sentence[1:], size=12, cls=body_cls))
    return parts


def _gate_band(x: float, y: float, gates: Sequence[StepGate], to: int, level: LevelResult,
               is_next: bool) -> list[str]:
    cls = "warn" if is_next else "muted"
    mid = x + 30
    extra = ' data-next="true"' if is_next else ""
    parts = [f'<path d="M{mid:.1f} {y + GATE_H - 4:.1f} L{mid:.1f} {y + 8:.1f} M{mid - 6:.1f} {y + 14:.1f} '
             f'L{mid:.1f} {y + 8:.1f} L{mid + 6:.1f} {y + 14:.1f}" fill="none" class="s-{cls}" '
             f'stroke-width="{2.5 if is_next else 1.5}" data-gate-to="{to}"{extra}/>']
    words = "; ".join(f"{_gate_words(g)} ({_held_state(level, g)})"
                      for g in gates) or "no new gate"
    label = f"{'Next gate' if is_next else 'Gate'}: {words}"
    parts.append(svg.text(mid + 18, y + GATE_H / 2 + 4, label, size=12,
                          weight="600" if is_next else None, cls="warn" if is_next else "muted"))
    return parts


def render_ladder(level: LevelResult, steps: Sequence[StepDef]) -> str:
    """The Step ladder SVG for one level result under the model's ``steps``."""
    ordered = sorted(steps, key=lambda s: s["step"])
    here = level["step"]
    nxt = level["next_step"]["step"] if level["next_step"] is not None else None
    ceiling = one_change_ceiling(level, ordered)
    body = [svg.text(PAD, 28, LADDER_TITLE, size=15, weight="600", cls="fg"),
            svg.text(PAD, 48, "Each rung is a Step of agent autonomy; the gate between two rungs "
                     "lifts you to the next", size=12, cls="muted")]
    y = 68.0
    for index, step in enumerate(reversed(ordered)):
        n = step["step"]
        state = "here" if n == here else ("reached" if here is not None and n < here else "ahead")
        body += _rung(RUNG_X, y, step, state, n == ceiling)
        if n == ceiling:
            body.append(svg.text(RUNG_X + RUNG_W + 10, y + RUNG_H / 2 + 5, ONE_CHANGE, size=13,
                                 weight="600", cls="warn"))
        if state == "here":
            arrow_x = RUNG_X + RUNG_W + 10
            body.append(f'<path d="M{arrow_x:.1f} {y + RUNG_H / 2:.1f} l10 -7 l0 14 z" class="f-accent"/>')
            body.append(svg.text(arrow_x + 16, y + RUNG_H / 2 + 5, "You are here", size=13,
                                 weight="600", cls="accent"))
        y += RUNG_H
        if index < len(ordered) - 1:
            body += _gate_band(RUNG_X, y, new_gates(ordered, n), n, level, n == nxt)
            y += GATE_H
    desc = f"{ladder_in_place(level, ordered)} {ladder_caption(level, ordered)}"
    return svg.document(WIDTH, y + PAD, LADDER_TITLE, desc, body, scope=SCOPES["step-ladder"])


def _name(steps: Sequence[StepDef], step: int) -> str:
    return next((s["name"] for s in steps if s["step"] == step), "")


def ladder_in_place(level: LevelResult, steps: Sequence[StepDef]) -> str:
    """The section's opening line: the Step reached, its name and what an agent can do there."""
    here = level["step"]
    top = max((s["step"] for s in steps), default=0)
    sentence = meaning(here) if here is not None else ""
    tail = f": {sentence}" if sentence else ""
    return f"{IN_PLACE} Step {here} of {top}, {_name(steps, here or 0)}{tail}."


def ladder_caption(level: LevelResult, steps: Sequence[StepDef]) -> str:
    """The caption: what opens the next Step, read from ``next_step.blockers`` as the
    headline's Next line reads them, or that the top Step is held.

    Each blocking gate is worded by ``headline.gate_change`` (``GATE_CHANGE``, plus why a
    not-measured or owner-excluded gate is open). A Step reads its gates only (#1083), so
    the caption names gates and never a count, and it names the same unlock as the
    headline."""
    nxt = level["next_step"]
    if nxt is None:
        return "This is the top Step; it holds while its gates hold."
    gates = [b for b in nxt["blockers"] if b["kind"] == "gate"]
    changes = [gate_change(b) for b in gates]
    unlock = " and ".join(changes) if changes else "its gates hold"
    return f"Step {nxt['step']}, {_name(steps, nxt['step'])}, opens when {unlock}."


def gate_conditions(steps: Sequence[StepDef]) -> list[str]:
    """The condition id at each Step gate's level, once each, in model order (the waffle outlines them)."""
    ids = [f"{g['row']}.{g['level']}" for s in sorted(steps, key=lambda s: s["step"])
           for g in s.get("gates", [])]
    return list(dict.fromkeys(ids))


__all__ = ["ALREADY", "LADDER_FILE", "ONE_CHANGE", "LADDER_TITLE", "STEP_MEANING", "gate_conditions", "gate_state",
           "ladder_caption", "ladder_in_place", "meaning", "new_gates", "render_ladder"]

