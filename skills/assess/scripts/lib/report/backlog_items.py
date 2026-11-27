"""The condition marker on a filed backlog item, and dedup by condition id (#810).

``assess-pr`` Step 6 files each Top 3 action (and each lettered sub-action) as a tracker
item. An action that names a maturity-model ``condition`` (``<row id>.<level>``) gets one
hidden marker line at the end of its body, so a later run can find the item again by the
condition it moves rather than by its title::

    <!-- assess:condition=layer-5-ci@1 model=default-2026.11.24 -->
    <!-- assess:condition=layer-5-ci@1 sub=a model=default-2026.11.24 -->

- ``condition=<row id>@<level>``: the condition id, its last ``.`` written as ``@`` so the
  row id and level read apart.
- ``sub=<letters>``: a sub-item only, the letters of its label (``a`` for ``2a``). The rank
  is left out because it moves between runs; the parent's condition and the letter stay.
- ``model=<levels_version>``: the maturity model the run scored against
  (``run-context.json`` ``level.levels_version``), ``unknown`` when the run has none.

The fields are written in that order, one space apart. A reader takes every
``<key>=<value>`` token after ``assess:`` and ignores keys it does not know, so a later field
does not break an older reader. The match key is ``(row, level, sub)``; the model version is
not part of it, so a model release does not file the condition again. An action without a
``condition`` gets no marker and is matched by label plus title as before.

``plan_filing(items, existing)`` decides, per rendered item, against the tracker's existing
items (``{number, title, body, state, labels}``, as ``gh issue list --json`` gives them):

1. ``reuse``: an existing item carries the same marker key, whatever its title now reads.
2. ``reuse_and_mark``: no marked item matches, and an unmarked item with the label
   (``assess-finding``; no label check when ``label`` is empty) has the same title, compared
   after the sub-item prefix (``2a. ``) and whitespace are dropped, case-folded. The plan
   carries ``marked_body``: the existing body with the marker appended.
3. ``create``: nothing matches.

Each existing item is claimed once per run by a title match, so two rendered items never
take the same unmarked item. Open items are preferred over closed ones, then the lowest
number. Pure stdlib; never touches the tracker. Tests: ``tests/test_backlog_items.py``.
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

MarkerKey = tuple[str, int, str]  # (row id, level, sub-item letters or "")


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


def condition_marker(condition: str, model: str | None, sub: str = "") -> str:
    """The marker line for an item moving ``condition`` (``<row id>.<level>``); ``""`` when the id is malformed."""
    row, sep, level = condition.rpartition(".")
    if not sep or not row or not level.isdigit():
        return ""
    fields = [f"condition={_safe(row)}@{int(level)}"]
    letters = re.sub(r"[^a-z]", "", sub.lower())
    if letters:
        fields.append(f"sub={letters}")
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
    """``(row, level, sub)`` from the marker in ``body``; None without a well-formed one."""
    fields = parse_marker(body)
    if fields is None:
        return None
    row, sep, level = fields["condition"].rpartition("@")
    if not sep or not row or not level.isdigit():
        return None
    return row, int(level), fields.get("sub", "")


def title_key(title: str) -> str:
    """A title as dedup compares it: the ``2a. `` prefix dropped, whitespace folded, case-folded."""
    return " ".join(_SUB_PREFIX.sub("", title or "").split()).casefold()


def _labels(item: Mapping[str, object]) -> set[str]:
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
    if isinstance(item.get("number"), int):
        step["number"] = item["number"]  # type: ignore[typeddict-item]
    if isinstance(item.get("state"), str):
        step["state"] = item["state"]  # type: ignore[typeddict-item]
    return step


def match_item(title: str, body: str, existing: Sequence[Mapping[str, object]], *,
               label: str = DEFAULT_LABEL, claimed: set[int] | None = None) -> FilingStep:
    """The filing step for one rendered item against the tracker's ``existing`` items."""
    claimed = claimed if claimed is not None else set()
    ordered = sorted(existing, key=_order)
    key = marker_key(body)
    if key is not None:
        for item in ordered:
            if marker_key(str(item.get("body") or "")) == key:
                return _step("reuse", item)
    wanted = title_key(title)
    for item in ordered:
        number = item.get("number")
        if number in claimed or marker_key(str(item.get("body") or "")) is not None:
            continue
        if label and label not in _labels(item):
            continue
        if title_key(str(item.get("title") or "")) != wanted:
            continue
        if isinstance(number, int):
            claimed.add(number)
        if key is None:
            return _step("reuse", item)
        step = _step("reuse_and_mark", item)
        marker = next(line for line in body.splitlines() if marker_key(line) == key)
        step["marked_body"] = f"{str(item.get('body') or '').rstrip()}\n\n{marker}\n"
        return step
    return FilingStep(action="create")


def plan_filing(items: Sequence[Mapping[str, object]], existing: Sequence[Mapping[str, object]], *,
                label: str = DEFAULT_LABEL) -> list[FilingStep]:
    """One filing step per rendered item (``{title, body}``), in the order given."""
    claimed: set[int] = set()
    return [match_item(str(i.get("title") or ""), str(i.get("body") or ""), existing,
                       label=label, claimed=claimed) for i in items]
