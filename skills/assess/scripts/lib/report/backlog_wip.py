"""The work-in-progress limit on filed backlog items, and the order new items take (#812).

``assess-pr`` Step 6 files each Top 3 action and sub-action as a tracker item. Without a
limit each run can add items while earlier ones stay open. ``.assess/config.toml``
``[backlog] wip`` (``assess_config.load_backlog_wip``, default 3) caps how many are open at
once. ``apply_wip(items, steps, existing, level, wip)`` takes the rendered items with their
``plan_filing`` steps (``backlog_items.py``) and gives each one a ``WipStep``:

- ``decision``: ``open`` when the step reuses an item that is already open (it takes no new
  slot), ``file`` when the item is created or a closed item reopened within the limit, and
  ``next_up`` when the limit is reached: the item is not filed, and the report lists it under
  "Next up" (``next_up_markdown``).
- ``tier``: the order the items that need a slot are taken in, the #747 next-unlock ranking:
  ``next_unlock`` (the item's condition is a blocker of the next Step,
  ``level.next_step.blockers[].condition``), then ``recovery`` (the step reopens a closed
  item: the condition was done once and reads unmet again), then ``gain`` (anything else,
  an item without a condition included). Within a tier the Top 3 order stands.

The open count is the open tracker items in the backlog: an item with a condition marker
(``backlog_items.marker_key``), an unmarked one with ``label`` (``assess-finding``, the items
``plan_filing`` matches by title; no label is checked when ``label`` is empty), and any open
item a step reuses. Two items that reopen the same closed item take one slot. Free slots are
``wip`` minus the open count, never below 0, so a tracker already over the limit gets no new
item. Pure stdlib; never touches the tracker. Tests: ``tests/test_backlog_items.py``.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TypedDict

from lib.report.backlog_items import DEFAULT_LABEL, FilingStep, marker_key

DEFAULT_WIP = 3
TIERS = ("next_unlock", "recovery", "gain")
OPEN, FILE, NEXT_UP = "open", "file", "next_up"


class WipStep(TypedDict):
    """Whether one rendered item takes a slot under the limit: ``open``, ``file`` or ``next_up``."""

    decision: str
    tier: str  # one of TIERS


def _is_open(item: Mapping[str, object]) -> bool:
    return str(item.get("state", "")).lower() in ("open", "opened")


def _labels(item: Mapping[str, object]) -> set[str]:
    raw = item.get("labels")
    if not isinstance(raw, list):
        return set()
    names = (x.get("name") if isinstance(x, Mapping) else x for x in raw)
    return {n for n in names if isinstance(n, str)}


def open_backlog(existing: Sequence[Mapping[str, object]], label: str = DEFAULT_LABEL) -> set[int]:
    """The numbers of the open backlog items: marked, or carrying ``label`` when it is set."""
    out: set[int] = set()
    for item in existing:
        number = item.get("number")
        if not isinstance(number, int) or not _is_open(item):
            continue
        if marker_key(str(item.get("body") or "")) is not None or (label and label in _labels(item)):
            out.add(number)
    return out


def next_unlock_conditions(level: object) -> set[str]:
    """The condition ids the next Step waits on: ``level.next_step.blockers[].condition``."""
    nxt = level.get("next_step") if isinstance(level, Mapping) else None
    blockers = nxt.get("blockers") if isinstance(nxt, Mapping) else None
    if not isinstance(blockers, list):
        return set()
    return {b["condition"] for b in blockers if isinstance(b, Mapping) and isinstance(b.get("condition"), str)}


def _condition(item: Mapping[str, object]) -> str | None:
    key = marker_key(str(item.get("marker") or item.get("body") or ""))
    return f"{key[0]}.{key[1]}" if key else None


def tier(item: Mapping[str, object], step: FilingStep, unlocks: set[str]) -> str:
    """The item's tier in the next-unlock ranking (``TIERS``)."""
    if _condition(item) in unlocks:
        return "next_unlock"
    if step["action"] != "create" and not _is_open(step):
        return "recovery"
    return "gain"


def apply_wip(items: Sequence[Mapping[str, object]], steps: Sequence[FilingStep],
              existing: Sequence[Mapping[str, object]], level: object = None, *,
              wip: int = DEFAULT_WIP, label: str = DEFAULT_LABEL) -> list[WipStep]:
    """One ``WipStep`` per rendered item, in the order given; ``steps`` from ``plan_filing``."""
    unlocks = next_unlock_conditions(level)
    tiers = [tier(i, s, unlocks) for i, s in zip(items, steps, strict=True)]
    counted = open_backlog(existing, label)
    counted |= {s["number"] for s in steps if s["action"] != "create" and "number" in s and _is_open(s)}
    free = max(0, wip - len(counted))
    out = [WipStep(decision=OPEN, tier=t) for t in tiers]
    reopened: set[int] = set()
    wanting = [i for i, s in enumerate(steps) if not (s["action"] != "create" and _is_open(s))]
    for i in sorted(wanting, key=lambda i: TIERS.index(tiers[i])):
        number = steps[i].get("number")
        if isinstance(number, int) and number in reopened:
            out[i]["decision"] = FILE
            continue
        if free > 0:
            free -= 1
            out[i]["decision"] = FILE
            if isinstance(number, int):
                reopened.add(number)
        else:
            out[i]["decision"] = NEXT_UP
    return out


def next_up_markdown(items: Sequence[Mapping[str, object]], wip_steps: Sequence[WipStep],
                     wip: int = DEFAULT_WIP) -> str:
    """The report's "Next up" block for the items over the limit, in ranked order; ``""`` for none."""
    held = [(TIERS.index(w["tier"]), n, item) for n, (item, w) in enumerate(zip(items, wip_steps, strict=True))
            if w["decision"] == NEXT_UP]
    if not held:
        return ""
    lines = [f"**Next up** (not filed: the backlog holds {wip} open items at most, `[backlog] wip`):", ""]
    for _, _, item in sorted(held, key=lambda x: (x[0], x[1])):
        lines.append(f"- {item.get('title')}")
    return "\n".join(lines) + "\n"
