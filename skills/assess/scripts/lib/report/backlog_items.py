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

1. ``reuse``: an existing item carries the same marker key, whatever its title now reads.
   A key with ``sub`` or ``part`` is positional, because the model letters and numbers the
   parts afresh each run: it matches only when the title agrees too. A sub-item or part whose
   letter or number moved is matched by title among the items marked for the same
   condition, as ``reuse_and_mark`` with its marker rewritten.
2. ``reuse_and_mark``: no marked item matches, and an unmarked item with the label
   (``assess-finding``; no label check when ``label`` is empty) has the same title, compared
   after the sub-item prefix (``2a. ``) and whitespace are dropped, case-folded. The plan
   carries ``marked_body``: the existing body with the marker appended (or, for a moved
   sub-item or part, its old marker replaced).
3. ``create``: nothing matches.

Each existing item is claimed once per run by a title match, and by any match on a
positional key, so two rendered items never take the same item that way. Open items are preferred over closed ones, then the lowest
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
        if positional and isinstance(number, int):
            claimed.add(number)
        step = _step(action, item)
        if action == "reuse_and_mark":
            step["marked_body"] = _with_marker(item, marker)
        return step

    if key is not None:
        for item in ordered:
            if marker_key(_body(item)) == key and (not positional or same_title(item)):
                return take("reuse", item)
        if positional:
            for item in ordered:
                other = marker_key(_body(item))
                if other is not None and other[:2] == key[:2] and (other[2] or other[3]) and same_title(item):
                    return take("reuse_and_mark", item)
    for item in ordered:
        if marker_key(_body(item)) is not None or (label and label not in _labels(item)) or not same_title(item):
            continue
        number = item.get("number")
        if isinstance(number, int):
            claimed.add(number)
        return take("reuse" if key is None else "reuse_and_mark", item)
    return FilingStep(action="create")


def plan_filing(items: Sequence[Mapping[str, object]], existing: Sequence[Mapping[str, object]], *,
                label: str = DEFAULT_LABEL) -> list[FilingStep]:
    """One filing step per rendered item (``{title, body}``), in the order given."""
    claimed: set[int] = set()
    return [match_item(str(i.get("title") or ""), str(i.get("body") or ""), existing,
                       label=label, claimed=claimed) for i in items]
