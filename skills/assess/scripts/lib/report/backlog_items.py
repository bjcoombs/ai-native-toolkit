"""The condition marker on a filed backlog item, and dedup by condition id (#810).

``assess-pr`` Step 6 files each Top 3 action (and each lettered sub-action) as a tracker
item. An action that names a maturity-model ``condition`` (``<row id>.<level>``) gets one
hidden marker line at the end of its body, so a later run can find the item again by the
condition it moves rather than by its title::

    <!-- assess:condition=layer-5-ci@1 model=default-2026.11.24 -->
    <!-- assess:condition=layer-5-ci@1 sub=a model=default-2026.11.24 -->
    <!-- assess:condition=layer-5-ci@1 part=2 model=default-2026.11.24 -->

- ``condition=<row id>@<level>``: the condition id, its last ``.`` written as ``@`` so the
  row id and level read apart.
- ``sub=<letters>``: a sub-item only, the letters of its label (``a`` for ``2a``). The rank
  is left out because it moves between runs; the parent's condition and the letter stay.
- ``part=<n>``: an item split from one action (``assess-pr`` "One concern per item"), its
  1-based place in the split, so the parts of one action do not share a key.
- ``model=<levels_version>``: the maturity model the run scored against
  (``run-context.json`` ``level.levels_version``), ``unknown`` when the run has none.

The fields are written in that order, one space apart. A reader takes every
``<key>=<value>`` token after ``assess:`` and ignores keys it does not know, so a later field
does not break an older reader. The match key is ``(row, level, sub, part)``, ``part`` 0
when absent; the model version is
not part of it, so a model release does not file the condition again. An action without a
``condition`` gets no marker and is matched by label plus title as before.

``plan_filing(items, existing)`` decides, per rendered item, against the tracker's existing
items (``{number, title, body, state, labels}``, as ``gh issue list --json`` gives them):

1. ``reuse``: an existing item carries the same marker key, whatever its title now reads;
   among several with the key, the one whose title agrees comes first (#811: two Top 3
   actions that name one condition share a key, and each takes its own item).
   A key with ``sub`` or ``part`` is positional, because the model letters and numbers the
   parts afresh each run: it matches only when the title agrees too. A sub-item or part whose
   letter or number moved is matched by title among the items marked for the same
   condition, as ``reuse_and_mark`` with its marker rewritten. An action a previous run
   split and this run files whole (#811) takes one of its earlier parts, as
   ``reuse_and_mark`` with ``part`` dropped from the marker: the part whose title agrees, else
   an open part, else the lowest part number. The reconcile pass
   (``lib/report/backlog_reconcile.py``) proposes closing the other parts as superseded.
2. ``reuse_and_mark``: no marked item matches, and an unmarked item with the label
   (``assess-finding``; no label check when ``label`` is empty) has the same title, compared
   after the sub-item prefix (``2a. ``) and whitespace are dropped, case-folded. The plan
   carries ``marked_body``: the existing body with the marker appended (or, for a moved
   sub-item or part, its old marker replaced).
3. ``create``: nothing matches.

Each existing item is claimed by the first rendered item that matches it, whatever the
match, so two rendered items never take the same item in one run. Open items are preferred
over closed ones, then the lowest number. Pure stdlib; never touches the tracker. Tests: ``tests/test_backlog_items.py``.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import NotRequired, TypedDict

DEFAULT_LABEL = "assess-finding"
UNKNOWN_MODEL = "unknown"

_MARKER = re.compile(r"<!--\s*assess:(?P<fields>[^>]*?)\s*-->")
_FIELD = re.compile(r"(?P<key>[a-z_]+)=(?P<value>\S+)")
_UNSAFE = re.compile(r"[^A-Za-z0-9._:/+-]")
_SUB_PREFIX = re.compile(r"^\s*\d+[a-z]+\.\s+", re.I)

MarkerKey = tuple[str, int, str, int]  # (row id, level, sub-item letters or "", split part or 0)


class ExistingItem(TypedDict, total=False):
    """One tracker item, as ``gh issue list --json number,title,body,state,labels`` gives it."""

    number: int
    title: str
    body: str
    state: str
    labels: list[object]  # label names, or ``{"name": ...}`` objects


class FilingStep(TypedDict):
    """What to do with one rendered item: ``create``, ``reuse`` or ``reuse_and_mark``."""

    action: str
    number: NotRequired[int]
    state: NotRequired[str]
    marked_body: NotRequired[str]


def _safe(value: str) -> str:
    return _UNSAFE.sub("_", value) or UNKNOWN_MODEL


def condition_marker(condition: str, model: str | None, sub: str = "", part: int = 0) -> str:
    """The marker line for an item moving ``condition`` (``<row id>.<level>``); ``""`` when the id is malformed."""
    row, sep, level = condition.rpartition(".")
    if not sep or not row or not level.isdigit():
        return ""
    fields = [f"condition={_safe(row)}@{int(level)}"]
    letters = re.sub(r"[^a-z]", "", sub.lower())
    if letters:
        fields.append(f"sub={letters}")
    if part > 0:
        fields.append(f"part={part}")
    fields.append(f"model={_safe(model) if model else UNKNOWN_MODEL}")
    return f"<!-- assess:{' '.join(fields)} -->"


def parse_marker(body: str) -> dict[str, str] | None:
    """The fields of the first marker in ``body`` that names a condition, or None."""
    for m in _MARKER.finditer(body or ""):
        fields = {f.group("key"): f.group("value") for f in _FIELD.finditer(m.group("fields"))}
        if "condition" in fields:
            return fields
    return None


def marker_key(body: str) -> MarkerKey | None:
    """``(row, level, sub, part)`` from the marker in ``body``; None without a well-formed one."""
    fields = parse_marker(body)
    if fields is None:
        return None
    row, sep, level = fields["condition"].rpartition("@")
    if not sep or not row or not level.isdigit():
        return None
    part = fields.get("part", "0")
    return row, int(level), fields.get("sub", ""), int(part) if part.isdigit() else 0


def title_key(title: str) -> str:
    """A title as dedup compares it: the ``2a. `` prefix dropped, whitespace folded, case-folded."""
    return " ".join(_SUB_PREFIX.sub("", title or "").split()).casefold()


def item_labels(item: Mapping[str, object]) -> set[str]:
    """An item's label names, from plain names or ``{"name": ...}`` objects."""
    raw = item.get("labels")
    if not isinstance(raw, list):
        return set()
    names = (x.get("name") if isinstance(x, Mapping) else x for x in raw)
    return {n for n in names if isinstance(n, str)}


def _order(item: Mapping[str, object]) -> tuple[int, int]:
    state = str(item.get("state", "")).lower()
    number = item.get("number")
    return (0 if state in ("open", "opened") else 1, number if isinstance(number, int) else 0)


def _step(action: str, item: Mapping[str, object]) -> FilingStep:
    step = FilingStep(action=action)
    number, state = item.get("number"), item.get("state")
    if isinstance(number, int):
        step["number"] = number
    if isinstance(state, str):
        step["state"] = state
    return step


def _body(item: Mapping[str, object]) -> str:
    return str(item.get("body") or "")


def _with_marker(item: Mapping[str, object], marker: str) -> str:
    """``item``'s body with ``marker`` in place of its condition marker, or appended when it has none."""
    old = _body(item)
    for m in _MARKER.finditer(old):
        if marker_key(m.group(0)) is not None:
            return old[:m.start()] + marker + old[m.end():]
    return f"{old.rstrip()}\n\n{marker}\n"


def match_item(title: str, body: str, existing: Sequence[Mapping[str, object]], *,
               label: str = DEFAULT_LABEL, claimed: set[int] | None = None) -> FilingStep:
    """The filing step for one rendered item against the tracker's ``existing`` items.

    A key with ``sub`` or ``part`` is positional (the model letters and numbers the parts
    afresh each run), so it matches only an item whose title agrees as well; a sibling under
    the same condition with the same title is reused and its marker rewritten.
    """
    claimed = claimed if claimed is not None else set()
    ordered = [i for i in sorted(existing, key=_order) if i.get("number") not in claimed]
    key, wanted = marker_key(body), title_key(title)
    marker = next((line for line in body.splitlines() if marker_key(line) == key), "") if key else ""
    positional = key is not None and bool(key[2] or key[3])

    def same_title(item: Mapping[str, object]) -> bool:
        return title_key(str(item.get("title") or "")) == wanted

    def take(action: str, item: Mapping[str, object]) -> FilingStep:
        number = item.get("number")
        if isinstance(number, int):
            claimed.add(number)
        step = _step(action, item)
        if action == "reuse_and_mark":
            step["marked_body"] = _with_marker(item, marker)
        return step

    if key is not None:
        same_key = [i for i in ordered if marker_key(_body(i)) == key]
        hit = next((i for i in same_key if same_title(i)), None)
        if hit is None and not positional and same_key:
            hit = same_key[0]
        if hit is not None:
            return take("reuse", hit)
        if positional:
            for item in ordered:
                other = marker_key(_body(item))
                if other is not None and other[:2] == key[:2] and (other[2] or other[3]) and same_title(item):
                    return take("reuse_and_mark", item)
        else:
            parts = [i for i in ordered if _is_part_of(marker_key(_body(i)), key)]
            if parts:
                return take("reuse_and_mark", min(parts, key=lambda i: _part_order(i, same_title(i))))
    for item in ordered:
        if marker_key(_body(item)) is not None or (label and label not in item_labels(item)) or not same_title(item):
            continue
        return take("reuse" if key is None else "reuse_and_mark", item)
    return FilingStep(action="create")


def _is_part_of(other: MarkerKey | None, key: MarkerKey) -> bool:
    """True when ``other`` is a split part (``part`` > 0) of the unsplit ``key``'s action."""
    return other is not None and other[:3] == key[:3] and other[3] > 0


def _part_order(item: Mapping[str, object], titled: bool) -> tuple[int, int, int]:
    """Which earlier part carries an action filed unsplit: a title match, then an open item, then the lowest part."""
    key = marker_key(_body(item))
    return (0 if titled else 1, _order(item)[0], key[3] if key else 0)


def plan_filing(items: Sequence[Mapping[str, object]], existing: Sequence[Mapping[str, object]], *,
                label: str = DEFAULT_LABEL) -> list[FilingStep]:
    """One filing step per rendered item (``{title, body}``), in the order given."""
    claimed: set[int] = set()
    return [match_item(str(i.get("title") or ""), str(i.get("body") or ""), existing,
                       label=label, claimed=claimed) for i in items]
