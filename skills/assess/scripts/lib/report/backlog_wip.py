"""The work-in-progress limit on filed backlog items, and the order new items take (#812).

``assess-pr`` Step 6 files each Top 3 action and sub-action as a tracker item. Without a
limit each run can add items while earlier ones stay open. ``.assess/config.toml``
``[backlog] wip`` (``assess_config.load_backlog_wip``, default 3) caps how many are open at
once. ``apply_wip(items, steps, existing, level, wip)`` takes the rendered items with their
``plan_filing`` steps (``backlog_items.py``) and gives each one a ``WipStep``:

- ``decision``: ``open`` when the step reuses an item that is already open (it takes no new
  slot), ``file`` when the item is created, or a closed item proposed for reopening, within the limit, and
  ``next_up`` when the limit is reached: the item is not filed, and the report lists it under
  "Next up" (``next_up_markdown``).
- ``tier``: the order the items that need a slot are taken in, the #747 next-unlock ranking:
  ``next_unlock`` (the item's condition is a blocker of the next Step,
  ``level.next_step.blockers[].condition``), then ``recovery`` (the step matches a closed
  item: the condition was done once and reads unmet again), then ``gain`` (anything else,
  an item without a condition included). Within a tier the Top 3 order stands.

An action filed split (``assess-pr`` "One concern per item") is passed as ``parts``: its index
mapped to the number of its parts that need a slot (created or reopened; a part that reuses
an open item is already in the open count). Its ``plan_filing`` step is ignored, each part
takes a slot in the item's tier, and its ``WipStep`` carries ``parts``, one ``file`` or
``next_up`` per such part; ``decision`` reads ``file`` when any part is filed, ``next_up``
when none is, and ``open`` when no part needs a slot. "Next up" lists the held parts'
count after the title (#1542).

The open count is the open tracker items in the backlog: an item with a condition marker
(``backlog_items.marker_key``), an unmarked one with ``label`` (``assess-finding``, the items
``plan_filing`` matches by title; no label is checked when ``label`` is empty), and any open
item a step reuses. `plan_filing` takes each item once per run; steps from another caller that
name one closed item take one slot between them. Free slots are
``wip`` minus the open count, never below 0, so a tracker already over the limit gets no new
item. Pure stdlib; never touches the tracker. Tests: ``tests/test_backlog_items.py``.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import NotRequired, TypedDict

from lib.assess_config import DEFAULT_BACKLOG_WIP
from lib.report.backlog_items import DEFAULT_LABEL, FilingStep, marker_key

DEFAULT_WIP = DEFAULT_BACKLOG_WIP  # one default, read from the config loader
TIERS = ("next_unlock", "recovery", "gain")
OPEN, FILE, NEXT_UP = "open", "file", "next_up"


class WipStep(TypedDict):
    """Whether one rendered item takes a slot under the limit: ``open``, ``file`` or ``next_up``."""

    decision: str
    tier: str  # one of TIERS
    parts: NotRequired[list[str]]  # an item filed split: ``file`` or ``next_up`` per part that needs a slot


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


def _reuses_open(step: FilingStep) -> bool:
    return step["action"] != "create" and _is_open(step)


def apply_wip(items: Sequence[Mapping[str, object]], steps: Sequence[FilingStep],
              existing: Sequence[Mapping[str, object]], level: object = None, *,
              wip: int = DEFAULT_WIP, label: str = DEFAULT_LABEL,
              parts: Mapping[int, int] | None = None) -> list[WipStep]:
    """One ``WipStep`` per rendered item, in the order given; ``steps`` from ``plan_filing``.

    ``parts`` maps the index of an item filed split to the number of its parts that need a
    slot (created or reopened); its ``steps`` entry is ignored, and its ``WipStep`` carries
    one decision per such part in ``parts``.
    """
    parts = parts or {}
    if any(not 0 <= i < len(items) or n < 0 for i, n in parts.items()):
        raise ValueError("parts maps an item index in range to a part count of 0 or more")
    unlocks = next_unlock_conditions(level)
    plain = [FilingStep(action="create") if i in parts else s for i, s in enumerate(steps)]
    tiers = [tier(i, s, unlocks) for i, s in zip(items, plain, strict=True)]
    counted = open_backlog(existing, label)
    counted |= {s["number"] for s in plain if _reuses_open(s) and "number" in s}
    free = max(0, wip - len(counted))
    out = [WipStep(decision=OPEN, tier=t) for t in tiers]
    for i in parts:
        out[i]["parts"] = []
    reopened: set[int] = set()
    units = [(i, 0) for i, s in enumerate(plain) if i not in parts and not _reuses_open(s)]
    units += [(i, p) for i, n in parts.items() for p in range(1, n + 1)]
    for i, part in sorted(units, key=lambda u: (TIERS.index(tiers[u[0]]), u[0], u[1])):
        number = plain[i].get("number")
        # Guards a caller that passes steps not from plan_filing, which takes each item once per run.
        if isinstance(number, int) and number in reopened:
            out[i]["decision"] = FILE
            continue
        decision = NEXT_UP
        if free > 0:
            free -= 1
            decision = FILE
            if isinstance(number, int):
                reopened.add(number)
        if part:
            out[i]["parts"].append(decision)
        else:
            out[i]["decision"] = decision
    for i in parts:
        held = out[i]["parts"]
        out[i]["decision"] = OPEN if not held else FILE if FILE in held else NEXT_UP
    return out


def _held_parts(step: WipStep) -> int:
    return sum(1 for d in step.get("parts", []) if d == NEXT_UP)


def next_up_markdown(items: Sequence[Mapping[str, object]], wip_steps: Sequence[WipStep],
                     limit: int = DEFAULT_WIP) -> str:
    """The report's "Next up" block for the items over the limit, in ranked order; ``""`` for none.

    The header names ``limit``, the configured ``[backlog] wip``, even when ``plan`` ran with
    another ``--wip`` given on the command line.
    """
    held = [(TIERS.index(w["tier"]), n, item, _held_parts(w)) for n, (item, w)
            in enumerate(zip(items, wip_steps, strict=True)) if w["decision"] == NEXT_UP or _held_parts(w)]
    if not held:
        return ""
    noun = "item" if limit == 1 else "items"
    lines = [f"**Next up** (not filed: the backlog holds at most {limit} open {noun}, the `[backlog] wip` limit):", ""]
    for _, _, item, count in sorted(held, key=lambda x: (x[0], x[1])):
        suffix = f" ({count} split part{'' if count == 1 else 's'})" if count else ""
        lines.append(f"- {item.get('title')}{suffix}")
    return "\n".join(lines) + "\n"
