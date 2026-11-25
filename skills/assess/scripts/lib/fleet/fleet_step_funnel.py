"""The Step funnel: repositories at each Step, split by the gate that holds them there (#941).

``funnel_view`` reads a ``fleet-data.json`` mapping (each repository's ``step`` and
``next_step``, the level result's fields, ``lib/fleet/fleet_data_steps.py``) and the maturity
model's ``[[steps]]`` (the Step names and order). One bar per Step, with the count of
repositories at it. Each repository counts once, under the first gate of its next Step that
does not hold, in model order (the level result lists its blockers in that order), so a
Step's segments sum to its count. A segment is a gate and how it fails: ``unmet``,
``not_measured`` (hatched, with the reason code) or ``set_aside`` (``not_applicable``: the
owner excluded the gate's condition or one it requires; hatched too). The top Step has one
``top`` segment. ``held_by_more`` counts the repositories at a Step that more than one gate
holds back; they still count once. A Step reads its gates only (#1083), so there is no
threshold segment.

A repository with no measured Step (a failed scan) is never drawn at Step 1: it is counted
on its own hatched line, by its reason code. No repository is named, so the SVG reads the
same in ``--full``, counts-only and hashed output.

`funnel_view(model, data)` -> `FunnelView` (`bars`: `step`, `name`, `count`, `held_by_more`,
`segments` of `gate`, `kind`, `reason`, `count`; `not_measured`: `(reason, count)` pairs).
`render(model, data)` -> `step-funnel.svg`, the stable entry point the fleet page (#946)
calls; `in_place(view)` the report's opening line. Tests: `tests/test_fleet_step_diagrams.py`.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any, TypedDict

import lib.fleet.fleet_svg as svg
from lib.assess_diagrams import wrap
from lib.scoring.levels import MaturityModel
from lib.vocabulary import IN_PLACE

FILE = "step-funnel.svg"
TITLE = "Repositories at each Step"
QUESTION = "Where does each repository stand, and which gate holds it there?"
CAPTION = ("Each bar counts the repositories at a Step. Its parts name the first gate that holds "
           "them back. Hatched parts were not measured, so they are never read as unmet.")

KINDS = ("top", "unmet", "not_measured", "set_aside")
HATCHED = frozenset({"not_measured", "set_aside"})
# Gate colours by position in the next Step's gates: a categorical set that reads on both
# backgrounds (#743: growth hues, no alarm red for a gate that is not yet held).
GATE_ROLES: dict[str, tuple[str, str]] = {
    "g0": ("#1f6f6b", "#5fb8b1"), "g1": ("#4f6db8", "#8ea6e6"),
    "g2": ("#8a6a1f", "#d8b260"), "g3": ("#7a4f8c", "#bf98d0"),
    "top": (svg.LEVEL_RAMP[6], svg.LEVEL_RAMP[4]),
}

PAD = 16.0
WIDTH = 720.0
LABEL_W = 230.0
BAR_W = 300.0
BAR_H = 20.0
LINE_H = 17.0


class Segment(TypedDict):
    gate: str | None  # None: the top Step
    kind: str  # one of KINDS
    reason: str | None  # the reason code of a not-measured gate
    count: int


class FunnelBar(TypedDict):
    step: int
    name: str
    count: int
    held_by_more: int  # of ``count``, the repositories more than one gate holds back
    segments: list[Segment]


class FunnelView(TypedDict):
    bars: list[FunnelBar]
    not_measured: list[tuple[str, int]]  # repositories with no measured Step, by reason code


def _kind(blocker: Mapping[str, Any]) -> str:
    state = blocker.get("state")
    if state == "not_measured":
        return "not_measured"
    return "set_aside" if state == "not_applicable" else "unmet"


def funnel_view(model: MaturityModel, data: Mapping[str, Any]) -> FunnelView:
    """The funnel's counts (see the module docstring)."""
    steps = sorted(model.get("steps", []), key=lambda s: s["step"])
    gate_order = {g["id"]: i for s in steps for i, g in enumerate(s["gates"])}
    at: dict[int, Counter[tuple[str | None, str, str | None]]] = {s["step"]: Counter() for s in steps}
    more: Counter[int] = Counter()
    unmeasured: Counter[str] = Counter()
    for repo in data.get("repos") or []:
        step = repo.get("step")
        if step is None or step not in at:
            unmeasured[str(repo.get("reason") or "not_measured")] += 1
            continue
        blockers = (repo.get("next_step") or {}).get("blockers") or []
        if not blockers:
            at[step][(None, "top", None)] += 1
            continue
        first = blockers[0]
        kind = _kind(first)
        at[step][(first.get("gate"), kind, first.get("reason") if kind == "not_measured" else None)] += 1
        more[step] += len(blockers) > 1

    def order(key: tuple[str | None, str, str | None]) -> tuple[int, int, str]:
        gate, kind, reason = key
        return (-1 if gate is None else gate_order.get(gate, len(gate_order)), KINDS.index(kind), reason or "")

    bars: list[FunnelBar] = [{
        "step": s["step"], "name": s["name"], "count": sum(at[s["step"]].values()),
        "held_by_more": more[s["step"]],
        "segments": [{"gate": g, "kind": k, "reason": r, "count": n}
                     for (g, k, r), n in sorted(at[s["step"]].items(), key=lambda kv: order(kv[0]))],
    } for s in steps]
    return {"bars": bars, "not_measured": sorted(unmeasured.items())}


def _plural(n: int) -> str:
    return f"{n} repository" if n == 1 else f"{n} repositories"


def in_place(view: FunnelView) -> str:
    measured = sum(b["count"] for b in view["bars"])
    top = view["bars"][-1]["count"] if view["bars"] else 0
    return f"{IN_PLACE} {_plural(measured)} with a measured Step, {top} of them at the top Step."


def _side(bar: FunnelBar) -> str:
    side = _plural(bar["count"])
    if bar["held_by_more"]:
        side += f", {bar['held_by_more']} held by more than one gate"
    return side


def segment_text(segment: Segment) -> str:
    n = segment["count"]
    if segment["kind"] == "top":
        return f"{n} at the top Step"
    if segment["kind"] == "not_measured":
        return f"{n} where {segment['gate']} was not measured ({segment['reason'] or 'no_value'})"
    if segment["kind"] == "set_aside":
        return f"{n} where the owner set {segment['gate']} aside"
    return f"{n} held by {segment['gate']}"


def _role(segment: Segment, gates: dict[str, int]) -> str:
    if segment["gate"] is None:
        return "top"
    return f"g{gates.get(segment['gate'], 0) % 4}"


def _swatch(x: float, y: float, segment: Segment, role: str, w: float, h: float) -> str:
    if segment["kind"] in HATCHED:
        return (f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" fill="{svg.HATCH_FILL}" '
                f'class="s-{role}" stroke-width="1.5" data-mark="hatched"/>')
    return f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" class="f-{role}"/>'


def render(model: MaturityModel, data: Mapping[str, Any]) -> str:
    """``step-funnel.svg`` from the model's Steps and a ``fleet-data.json`` mapping."""
    view = funnel_view(model, data)
    steps = sorted(model.get("steps", []), key=lambda s: s["step"])
    top = max((b["count"] for b in view["bars"]), default=0)
    scale = svg.LinearScale(0, svg.nice_ceiling(top), 0, BAR_W)
    x0 = PAD + LABEL_W
    side_w = max((svg.text_width(_side(b), 11.5) for b in view["bars"]), default=0.0)
    width = max(WIDTH, x0 + BAR_W + 8 + side_w + PAD)
    body = [svg.text(PAD, 28, TITLE, size=15, weight="600", cls="fg"),
            svg.text(PAD, 48, "Each repository counts once, under the first gate of its next Step that does "
                     "not hold", size=12, cls="muted")]
    y = 76.0
    for bar in view["bars"]:
        nxt = next((s for s in steps if s["step"] == bar["step"] + 1), None)
        gates = {g["id"]: i for i, g in enumerate(nxt["gates"])} if nxt else {}
        side = _side(bar)
        parts = [f'<g data-step="{bar["step"]}">',
                 svg.text(x0 - 12, y + 14, svg.truncate(f"Step {bar['step']} · {bar['name']}", 32),
                          anchor="end", cls="fg", tooltip=bar["name"])]
        done = 0
        for segment in bar["segments"]:
            left, right = scale(done), scale(done + segment["count"])
            done += segment["count"]
            parts.append(_swatch(x0 + left, y, segment, _role(segment, gates), right - left, BAR_H))
        parts.append(svg.text(x0 + scale(done) + 8 if done else x0 + 8, y + 14, side, size=11.5, cls="fg"))
        y += BAR_H + 6
        for segment in bar["segments"]:
            parts.append(_swatch(x0, y + 3, segment, _role(segment, gates), 11.0, 11.0))
            parts.append(svg.text(x0 + 18, y + 13, segment_text(segment), size=11.5, cls="fg"))
            y += LINE_H
        parts.append("</g>")
        body.append("".join(parts))
        y += 14
    limit = int((width - 2 * PAD) / svg.CHAR_W)
    if view["not_measured"]:
        counts = ", ".join(f"{n} {reason}" for reason, n in view["not_measured"])
        total = sum(n for _, n in view["not_measured"])
        lines = wrap(f"{_plural(total)} with no measured Step, never drawn at Step 1: {counts}.", limit - 4)
        notes, used = svg.hatched_note(PAD, y - 6, lines, size=12, line_h=LINE_H)
        body.append(f'<g data-mark="unknown">{"".join(notes)}</g>')
        y += used + 6
    for line in wrap(CAPTION, limit):
        y += LINE_H + 1
        body.append(svg.text(PAD, y, line, size=12, cls="fg"))
    desc = f"{in_place(view)} {CAPTION}"
    return svg.document(width, y + PAD, TITLE, desc, body, style=svg.theme_style(GATE_ROLES),
                        defs=svg.THEMED_HATCH)
