"""Render the /assess end-of-run Top 3 offers from ``.assess/actions.json`` (#1251): the PR table, the tracker items and the Readiness line.

The assess-pr offers (``skills/assess-pr/SKILL.md`` Steps 5 and 6) used to paste the
report's Top 3 Actions table into the PR body and take each tracker item's title from it.
The report table is model-written prose; ``actions.json`` (schema 2, written by
``assess_finalize.py``) is the durable contract with every field the table shows. This
script reads that contract and never ``assess-report.md``, so the report's Top 3 section can
change shape without changing the PR body or the items.

Subcommands, each reading ``<assess-dir>`` (default ``<repo>/.assess``):

- ``table [--issue RANK=REF ...]`` prints the PR body's Top 3 table, one row per entry of
  ``actions`` in ``rank`` order: ``#``, ``Action``, ``Layer`` (with the ``condition`` id when
  set), ``Effort``, ``Command / First Step``, ``Done when``, ``Scope fence``, ``Files`` and
  ``Issue``. ``Issue`` reads ``-`` until Step 6d re-runs the command with one ``--issue``
  per filed rank (``--issue 2='#44, #45'`` for a rank filed as two items).
- ``items [--report-link URL]`` prints a JSON array, one object per entry in ``rank``
  order: ``rank``, ``title`` (the ``action`` text) and ``body`` (Markdown: the ``finding``
  and ``files``, or the ``condition`` when there is no ``finding``, or the ``files`` alone,
  or nothing when the entry has none of them; then ``first_step``, ``done_when``, ``scope_fence``, and a Context block
  with ``layer``, ``effort`` and the full-report link when given, then a one-line footer).
  An entry's optional ``sub_items`` (#1263: the report's lettered sub-actions, each
  ``{label, action, done_when}`` with an optional ``first_step`` and ``scope_fence``) each
  follow their parent as one more item: the same ``rank``, a ``label`` (``2a``: the entry's
  rank, then only the label's letters), the title ``2a. <action>``, and a body that names the parent action and takes the parent's finding,
  scope fence and Context unless the sub-item sets its own. The table keeps one row per
  entry; a filed sub-item's reference goes in its parent's Issue cell. An item whose entry
  names a ``condition`` ends with the condition marker (#810,
  ``<!-- assess:condition=<row id>@<level>[ sub=<letters>] model=<levels_version> -->``,
  the model read from ``run-context.json`` ``level.levels_version``) and carries it as
  ``marker``; format and match rules: ``lib/report/backlog_items.py``.
- ``plan --existing FILE [--label NAME] [--report-link URL]`` prints the ``items`` array with
  a ``filing`` step on each (``create``, ``reuse`` or ``reuse_and_mark`` with the number and
  the ``marked_body`` to write back), matched against the tracker's items in ``FILE``
  (``gh issue list --state all --json number,title,body,state,labels``): by marker first,
  then label plus title for an item filed before the marker existed. ``--label ""`` drops
  the label check (filing upstream without triage access). Each item also carries a ``wip``
  step (#812): ``open`` (reuses an open item), ``file`` or ``next_up``, under the open-item
  limit ``--wip N`` or ``.assess/config.toml`` ``[backlog] wip`` (default 3), the items that
  need a slot taken in the next-unlock order (``next_unlock``, ``recovery``, ``gain``) read
  from ``run-context.json`` ``level``; rules: ``lib/report/backlog_wip.py``. ``--parts REF=N``
  (repeatable, #1542) names an action filed split, by rank or sub-item label, and the number
  of its parts that need a slot: each part is counted against the limit and its ``wip`` step
  carries ``parts``, one decision per part. An unknown ``REF`` exits 1. An offer declined
  before (#1543: a file offer, or a reopen offer for a closed match, recorded in
  ``.assess/backlog-declines.json``, and a marked item closed as not planned, read from
  ``stateReason``) is held back while its condition's state and the model are the ones it was
  declined under: its ``wip`` step reads ``declined``, it takes no slot, and ``declined``
  names who declined it. ``--decline REF`` (repeatable; a repeated ``REF`` records once)
  records the offer for ``REF`` as declined, with ``--declined-by``, ``--declined-at`` and ``--decline-reason REF=TEXT``, and
  prints the plan with it held back; a ``REF`` whose ``wip`` step is not ``file``, or that
  ``--parts`` names, whose file offer another item not declined shares (the same condition
  and title, its ``wip`` step ``file`` or ``next_up``), or a ``--decline-reason`` for a ``REF`` not in ``--decline``, exits 1, and
  those three flags without ``--decline`` fail at argument parsing. ``plan`` and ``next-up``
  write ``.assess/backlog-declines.json`` when the listing holds a not-planned close not
  read before, or when a recorded decline has lapsed; the write is idempotent.
- ``next-up --existing FILE [--label NAME] [--wip N] [--parts REF=N ...]`` prints the
  report's "Next up" block, the items and split parts ``plan`` holds back as ``next_up``, in
  ranked order; nothing when none are. Pass the same ``--label``, ``--wip`` and ``--parts``
  as ``plan``; the header names the configured ``[backlog] wip`` limit, not a ``--wip`` given
  on the command line. A negative ``--wip`` fails at argument parsing.
- ``reconcile --existing FILE [--label NAME] [--filed N,...] [--confirm all|none|N,...]
  [--record-declines [--declined-by NAME] [--declined-at DATE] [--decline-reason N=TEXT ...]]``
  (#811) prints the propose-only reconcile of the tracker's items in ``FILE`` against
  ``run-context.json``: ``{proposals, rules_changed, notes, summary}``, the close, reopen,
  demote and superseded proposals and the batched summary to confirm. It writes nothing to
  the tracker. A reopen is proposed only while ``.assess/config.toml`` ``[backlog] wip``
  leaves a slot, counted after declined proposals are held back; the others are listed
  under ``next_up`` and "Next up" in ``summary`` (#1542). A marked item closed as not planned
  (``stateReason`` ``NOT_PLANNED``) is read as a declined reopen the first time it is listed
  (#1543), so it is held back like any other decline and keeps no slot.
  ``--filed`` names the items this run filed or reused, which are never proposed superseded.
  With ``--confirm`` it prints only the writes of the confirmed item numbers (``all`` for
  every proposal, ``none`` for none) as a JSON array. A proposal declined before is held
  back while the condition's state and the model are the ones it was declined under, and
  named under ``declined`` and at the foot of ``summary`` (#813); ``--record-declines``
  with ``--confirm`` records each shown proposal not confirmed in
  ``.assess/backlog-declines.json``, with ``--declined-by``, ``--declined-at`` and one
  ``--decline-reason N=TEXT`` per item the user gave a reason for. ``--record-declines``,
  ``--declined-by``, ``--declined-at`` or ``--decline-reason`` without both
  ``--record-declines`` and ``--confirm`` fails at argument parsing (#1533). That file is
  the only thing it writes, never the tracker; it also drops records whose evidence changed.
  It needs no ``actions.json``; rules:
  ``lib/report/backlog_reconcile.py`` and ``lib/run/decline_markers.py``. ``--scheduled
  --today DATE`` (#814) prints the daily workflow's writes instead, ``{mode, writes, notes,
  summary}``, under ``.assess/config.toml`` ``[backlog] mode`` (an unknown mode exits 1), from
  ``--existing`` items that carry their ``comments``; rules: ``lib/report/backlog_schedule.py``.
- ``status-comment --today DATE`` prints a new item's status comment, the one comment the
  scheduled reconcile edits in place (#814).
- ``readiness`` prints the PR summary's Readiness value: ``headline.lines`` of
  ``run-context.json`` joined with `` / ``; the ``badge.json`` ``message`` when the run
  context has no headline lines; ``not known`` when neither file has one.

An entry missing ``rank``, ``action``, ``done_when`` or ``scope_fence`` is skipped with a
warning on stderr, the same rule ``assess_finalize.py`` applies on write. A sub-item missing
``label``, ``action`` or ``done_when``, or whose label is empty or repeats another's once
normalized, is skipped the same way, and its parent still renders. A missing or
unreadable ``actions.json`` exits 1 with a message, never an empty table. A missing optional
field renders as ``-``. Prose fields escape Markdown punctuation (``\\ * _ [ ] < >``) outside
backtick code spans, so a command such as ``rg -n "^[a-z_]+\\(\\)"`` reads as written on GitHub.
Table cells also escape ``|`` and fold newlines, so a field cannot break the row. ``table`` and
``items`` exit 1 when ``actions.json``'s ``run_id`` and ``run-context.json``'s both exist and
differ: the run wrote no Top 3, and the file on disk is an earlier run's. Stdlib only.
Tests: ``tests/test_assess_actions_render.py``.
"""
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NotRequired, TypedDict

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.assess_config import BacklogModeError, load_backlog_mode, load_backlog_wip  # noqa: E402
from lib.report.backlog_items import (  # noqa: E402
    DEFAULT_LABEL,
    FilingStep,
    condition_marker,
    filing_offer,
    not_planned_closes,
    plan_filing,
)
from lib.report.backlog_reconcile import confirmed_writes, reconcile, run_facts, summary, within_wip  # noqa: E402
from lib.report.backlog_schedule import new_status_comment, plan_scheduled, scheduled_facts, status_line  # noqa: E402
from lib.report.backlog_wip import FILE, WipStep, apply_wip, next_unlock_conditions, next_up_markdown, tier  # noqa: E402
from lib.run.decline_markers import (  # noqa: E402
    ProposalDecline,
    decline_record,
    held_by,
    merge_proposal_declines,
    proposal_disclosure,
    refresh_declines,
    split_declined,
    write_proposal_declines,
)

REQUIRED_KEYS = ("rank", "action", "done_when", "scope_fence")
SUB_ITEM_KEYS = ("label", "action", "done_when")
TABLE_HEADER = (
    "| # | Action | Layer | Effort | Command / First Step | Done when | Scope fence | Files | Issue |\n"
    "|---|--------|-------|--------|----------------------|-----------|-------------|-------|-------|"
)
EMPTY = "-"
FOOTER = "Generated by /assess - https://github.com/bjcoombs/ai-native-toolkit"

Entry = Mapping[str, object]

# A backtick code span (a run of backticks, its content, the same run); text outside one
# is escaped so GitHub shows it as written, the text inside one is left alone.
_CODE_SPAN = re.compile(r"(`+)(?:.+?)\1", re.S)
_MD_PUNCT = re.compile(r"([\\*_\[\]<>])")


class TrackerItem(TypedDict):
    """One tracker item: its rank, title and Markdown body; a sub-item also carries its label (``2a``)."""

    rank: int
    title: str
    body: str
    label: NotRequired[str]
    marker: NotRequired[str]
    filing: NotRequired[FilingStep]
    wip: NotRequired[WipStep]
    declined: NotRequired[str]  # a 6b offer declined before: who, when, under which model (#1543)


class RenderError(Exception):
    """actions.json is missing or unreadable."""


def load_actions(assess_dir: Path) -> list[Entry]:
    """The well-formed entries of ``actions`` in ``assess_dir/actions.json``, in rank order.

    Raises ``RenderError`` when the file is missing or unreadable, or when its ``run_id``
    and ``run-context.json``'s both exist and differ: a committed Top 3 left over from an
    earlier run, which must not be rendered under this run's Readiness line.
    """
    path = assess_dir / "actions.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RenderError(f"{path} not found; run /assess first") from exc
    except (OSError, ValueError) as exc:
        raise RenderError(f"{path} could not be read: {exc}") from exc
    _check_run_id(payload, assess_dir)
    raw = payload.get("actions") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        raise RenderError(f"{path} has no actions list")
    entries: list[Entry] = []
    for item in raw:
        if not isinstance(item, dict):
            print("actions.json: skipping an entry that is not an object", file=sys.stderr)
            continue
        missing = [k for k in REQUIRED_KEYS if item.get(k) in (None, "")]
        if missing or not isinstance(item.get("rank"), int):
            print(f"actions.json: skipping entry missing {missing or ['rank']}", file=sys.stderr)
            continue
        entries.append(item)
    return sorted(entries, key=_rank)


def _check_run_id(payload: object, assess_dir: Path) -> None:
    context = _read_json(assess_dir / "run-context.json")
    ours = payload.get("run_id") if isinstance(payload, dict) else None
    theirs = context.get("run_id") if isinstance(context, dict) else None
    if ours and theirs and ours != theirs:
        raise RenderError(
            f"actions.json run_id {ours!r} is not this run's ({theirs!r}); "
            "this run wrote no Top 3, so the file on disk is an earlier run's"
        )


def _rank(entry: Entry) -> int:
    rank = entry["rank"]
    return rank if isinstance(rank, int) else 0


def _text(entry: Entry, key: str) -> str:
    value = entry.get(key)
    if value is None or value == "":
        return ""
    return str(value)


def _files(entry: Entry) -> list[str]:
    files = entry.get("files")
    if not isinstance(files, list):
        return []
    return [str(f) for f in files if isinstance(f, str) and f]


def _layer(entry: Entry) -> str:
    layer, condition = _text(entry, "layer"), _text(entry, "condition")
    if layer and condition:
        return f"{layer} (`{condition}`)"
    return layer or (f"`{condition}`" if condition else "")


def _md(value: str) -> str:
    """Escape Markdown punctuation outside code spans, so a regex or ``__init__.py`` reads as written."""
    out, last = [], 0
    for m in _CODE_SPAN.finditer(value):
        out += [_MD_PUNCT.sub(r"\\\1", value[last:m.start()]), m.group(0)]
        last = m.end()
    out.append(_MD_PUNCT.sub(r"\\\1", value[last:]))
    return "".join(out)


def _cell(value: str) -> str:
    """One table cell: pipes escaped, newlines folded, ``-`` when empty."""
    folded = " ".join(value.split())
    return folded.replace("|", "\\|") if folded else EMPTY


def render_table(entries: Sequence[Entry], issues: Mapping[int, str] | None = None) -> str:
    """The PR body's Top 3 table, one row per entry in the given order."""
    issues = issues or {}
    rows = [TABLE_HEADER]
    for e in entries:
        files = ", ".join(f"`{f}`" for f in _files(e))
        cells = [
            str(_rank(e)), _md(_text(e, "action")), _layer(e), _md(_text(e, "effort")),
            _md(_text(e, "first_step")), _md(_text(e, "done_when")), _md(_text(e, "scope_fence")),
            files, issues.get(_rank(e), ""),
        ]
        rows.append("| " + " | ".join(_cell(c) for c in cells) + " |")
    return "\n".join(rows) + "\n"


def _finding_line(entry: Entry) -> str:
    """What the item addresses: the finding, else the condition it moves, else its files; ``""`` with none."""
    finding, condition = _text(entry, "finding"), _text(entry, "condition")
    files = ", ".join(f"`{f}`" for f in _files(entry))
    if finding:
        head = f"**Finding:** `{finding}`"
    elif condition:
        head = f"**Condition:** `{condition}`"
    else:
        return f"**Files:** {files}" if files else ""
    return f"{head} in {files}" if files else head


def render_item_body(entry: Entry, report_link: str | None = None, *, parent: str = "", marker: str = "") -> str:
    """One tracker item's body: the finding first, then the change, its exit and fence, then context.

    ``parent`` is set for a sub-item: a line naming the parent action follows the finding.
    ``marker`` (the condition marker, #810) is the body's last line when set.
    """
    lead = _finding_line(entry)
    parts = [lead, ""] if lead else []
    if parent:
        parts += [parent, ""]
    first_step = _text(entry, "first_step")
    if first_step:
        parts += ["### First step", _md(first_step), ""]
    parts += ["### Done when", f"- {_md(_text(entry, 'done_when'))}", ""]
    parts += ["### Scope fence", _md(_text(entry, "scope_fence")), ""]
    parts += ["### Context", f"- Layer: {_layer(entry) or EMPTY}", f"- Effort: {_text(entry, 'effort') or EMPTY}"]
    if report_link:
        parts.append(f"- Full report: {report_link}")
    parts += ["", "---", FOOTER]
    if marker:
        parts += ["", marker]
    return "\n".join(parts) + "\n"


def _title(entry: Entry) -> str:
    return " ".join(_text(entry, "action").split())


def sub_items(entry: Entry) -> list[Entry]:
    """The well-formed ``sub_items`` of an entry, in the order written; a malformed one is skipped with a warning."""
    raw = entry.get("sub_items")
    if raw is None:
        return []
    if not isinstance(raw, list):
        print(f"actions.json: rank {_rank(entry)} sub_items is not a list; ignoring it", file=sys.stderr)
        return []
    out: list[Entry] = []
    seen: set[str] = set()
    for sub in raw:
        missing = [k for k in SUB_ITEM_KEYS if not isinstance(sub, dict) or sub.get(k) in (None, "")]
        label = _norm_label(sub) if isinstance(sub, dict) else ""
        if not missing and not label:
            missing = ["label"]
        if missing:
            print(f"actions.json: rank {_rank(entry)} skipping sub-item missing {missing}", file=sys.stderr)
            continue
        if label in seen:
            print(f"actions.json: rank {_rank(entry)} skipping repeated sub-item label {label!r}", file=sys.stderr)
            continue
        seen.add(label)
        out.append({**sub, "label": label})
    return out


def _norm_label(sub: Entry) -> str:
    """The letters of a label, lower-cased: ``a`` from ``a``, ``2a``, ``a.``, ``a)`` or ``(a)``; ``""`` with none."""
    return re.sub(r"[^a-z]", "", _text(sub, "label").lower())


def _sub_label(entry: Entry, sub: Entry) -> str:
    """``2a`` from rank 2 and a normalized label ``a``."""
    return f"{_rank(entry)}{_text(sub, 'label')}"


def _marker(entry: Entry, model: str | None, sub: str = "") -> str:
    condition = _text(entry, "condition")
    return condition_marker(condition, model, sub) if condition else ""


def _item(rank: int, title: str, body: str, marker: str, label: str = "") -> TrackerItem:
    item = TrackerItem(rank=rank, title=title, body=body)
    if label:
        item["label"] = label
    if marker:
        item["marker"] = marker
    return item


def _sub_item(entry: Entry, sub: Entry, report_link: str | None, model: str | None = None) -> TrackerItem:
    """One sub-item as its own item: its own change and exit, the parent's finding, fence and context."""
    label = _sub_label(entry, sub)
    merged = {
        **entry, "action": _text(sub, "action"), "done_when": _text(sub, "done_when"),
        "first_step": _text(sub, "first_step"),
        "scope_fence": _text(sub, "scope_fence") or _text(entry, "scope_fence"),
    }
    parent = f"**Sub-action {_md(label)} of action {_rank(entry)}:** {_md(_title(entry))}"
    marker = _marker(entry, model, _text(sub, "label"))
    return _item(_rank(entry), f"{label}. {_title(sub)}",
                 render_item_body(merged, report_link, parent=parent, marker=marker), marker, label)


def render_items(entries: Sequence[Entry], report_link: str | None = None,
                 model: str | None = None) -> list[TrackerItem]:
    """One tracker item per entry (``rank``, ``title``, ``body``), each followed by one per sub-item.

    ``model`` is the maturity model version the condition marker records (#810).
    """
    items: list[TrackerItem] = []
    for e in entries:
        marker = _marker(e, model)
        items.append(_item(_rank(e), _title(e), render_item_body(e, report_link, marker=marker), marker))
        items += [_sub_item(e, sub, report_link, model) for sub in sub_items(e)]
    return items


def model_version(assess_dir: Path) -> str | None:
    """``level.levels_version`` of ``run-context.json``: the model a condition marker records; None without one."""
    context = _read_json(assess_dir / "run-context.json")
    level = context.get("level") if isinstance(context, dict) else None
    version = level.get("levels_version") if isinstance(level, dict) else None
    return version if isinstance(version, str) and version else None


def plan_items(items: Sequence[TrackerItem], existing: object, label: str = DEFAULT_LABEL,
               level: object = None, wip: int | None = None,
               parts: Sequence[tuple[str, int]] = (), declines: Sequence[ProposalDecline] = (),
               conditions: Mapping[str, Mapping[str, object]] | None = None) -> list[TrackerItem]:
    """``items`` with a ``filing`` step each, matched against the tracker's ``existing`` items (#810).

    With ``wip`` set, each item also carries a ``wip`` step (#812): ``open``, ``file`` or
    ``next_up`` under the limit, ranked by the run's ``level`` (``lib/report/backlog_wip.py``).
    ``parts`` (``--parts REF=N``, #1542) names each action filed split, by rank (``2``) or
    sub-item label (``2a``), with the number of its parts that need a slot; its ``wip`` step
    then carries one decision per part. An item whose offer one of the live ``declines``
    covers (``decline_markers.held_by`` under ``conditions``, #1543) carries ``declined``, a
    ``wip`` step reading ``declined``, and takes no slot; a split action is never held.
    """
    if not isinstance(existing, list):
        raise RenderError("--existing must hold a JSON array of tracker items")
    known = [x for x in existing if isinstance(x, dict)]
    steps = plan_filing(items, known, label=label)
    planned = [TrackerItem(**item, filing=step) for item, step in zip(items, steps, strict=True)]
    split = {_item_index(items, ref): n for ref, n in parts}
    held: dict[int, ProposalDecline] = {}
    for n, (item, step) in enumerate(zip(items, steps, strict=True)):
        offer = None if n in split else filing_offer(item, step)
        record = held_by(offer, declines, conditions) if offer is not None else None
        if record is not None:
            held[n] = record
            planned[n]["declined"] = proposal_disclosure(record)
    if wip is not None:
        keep = [n for n in range(len(items)) if n not in held]
        place = {old: new for new, old in enumerate(keep)}
        wip_steps = apply_wip([items[n] for n in keep], [steps[n] for n in keep], known, level, wip=wip,
                              label=label, parts={place[i]: c for i, c in split.items()})
        for n, w in zip(keep, wip_steps, strict=True):
            planned[n]["wip"] = w
        unlocks = next_unlock_conditions(level)
        for n in held:
            planned[n]["wip"] = WipStep(decision=DECLINED, tier=tier(items[n], steps[n], unlocks))
    return planned


DECLINED = "declined"


def run_plan(assess_dir: Path, args: argparse.Namespace, items: Sequence[TrackerItem], wip: int) -> list[TrackerItem]:
    """The ``plan`` / ``next-up`` items, holding back declined offers; ``--decline`` records more first (#1543)."""
    existing = _read_json(args.existing)
    if not isinstance(existing, list):
        raise RenderError("--existing must hold a JSON array of tracker items")
    known = [x for x in existing if isinstance(x, dict)]
    context = _read_json(assess_dir / "run-context.json")
    facts = run_facts(context if isinstance(context, dict) else {})
    conditions, model = facts["conditions"], facts["model"]
    listed = {n for x in known if isinstance(n := x.get("number"), int)}
    live = refresh_declines(assess_dir, not_planned_closes(known), listed, conditions, model)
    level = context.get("level") if isinstance(context, dict) else None
    planned = plan_items(items, existing, args.label, level, wip, args.parts, live, conditions)
    refs = list(dict.fromkeys(getattr(args, "decline", None) or []))  # a repeated REF records once (#1553)
    if not refs:
        return planned
    reasons, split = dict(args.decline_reason), {ref for ref, _ in args.parts}
    stray = sorted(set(reasons) - set(refs))
    if stray:
        raise RenderError(f"--decline-reason {', '.join(stray)} names no --decline item")
    new: list[ProposalDecline] = []
    # Every item with an offer, next_up too: a twin waiting for a slot would read declined once one frees (#1553).
    offers = {_ref(i): o for i in planned if _ref(i) not in split
              and (o := filing_offer(i, i["filing"]) if "filing" in i else None) is not None}
    offered = {_ref(i) for i in planned if i.get("wip", {}).get("decision") == FILE}
    for ref in refs:
        _item_index(items, ref, "--decline")
        offer = offers.get(ref) if ref in offered else None
        if offer is None:
            raise RenderError(f"--decline {ref}: item {ref} is not offered (its wip step is not file)")
        same = {k: v for k, v in offer.items() if k != "reason"}
        twin = next((r for r, o in offers.items() if r != ref and r not in refs
                     and {k: v for k, v in o.items() if k != "reason"} == same), None)
        if twin is not None:  # the same condition and title: one record would hold back both (#811)
            raise RenderError(f"--decline {ref}: item {twin} shares its decline record (the same condition and title), "
                              "so recording it would hold that item back too")
        record = decline_record(offer, conditions, model, declined_by=args.declined_by,
                                declined_at=args.declined_at, reason=reasons.get(ref))
        if record is not None:
            new.append(record)
    live = merge_proposal_declines(live, new)
    write_proposal_declines(assess_dir, live)
    return plan_items(items, existing, args.label, level, wip, args.parts, live, conditions)


def _ref(item: TrackerItem) -> str:
    """The ``REF`` that names ``item``: a sub-item's label, else its rank."""
    return item.get("label", str(item["rank"]))


def _item_index(items: Sequence[TrackerItem], ref: str, flag: str = "--parts") -> int:
    """The index of the item ``ref`` names: a sub-item by its label (``2a``), else an action by its rank."""
    for n, item in enumerate(items):
        if item.get("label", str(item["rank"])) == ref:
            return n
    raise RenderError(f"{flag} names no item {ref} in actions.json")


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def readiness(assess_dir: Path) -> str:
    """``headline.lines`` joined with " / ", else the badge message, else ``not known``."""
    context = _read_json(assess_dir / "run-context.json")
    headline = context.get("headline") if isinstance(context, dict) else None
    lines = headline.get("lines") if isinstance(headline, dict) else None
    if isinstance(lines, list) and any(isinstance(x, str) and x for x in lines):
        return " / ".join(x for x in lines if isinstance(x, str) and x)
    badge = _read_json(assess_dir / "badge.json")
    message = badge.get("message") if isinstance(badge, dict) else None
    return message if isinstance(message, str) and message else "not known"


def parse_numbers(value: str) -> frozenset[int]:
    """``3,4`` to ``{3, 4}``; argparse reports a bad value."""
    parts = [x.strip().lstrip("#") for x in value.split(",") if x.strip()]
    if not all(x.isdigit() for x in parts):
        raise argparse.ArgumentTypeError(f"expected item numbers such as 3,4, got {value!r}")
    return frozenset(int(x) for x in parts)


DECLINED_HEAD = "Declined before, not proposed again until the condition's state or the model changes:"


def run_reconcile(assess_dir: Path, args: argparse.Namespace) -> int:
    """The ``reconcile`` subcommand: proposals, or with ``--confirm`` the confirmed writes only.

    A proposal declined earlier (``.assess/backlog-declines.json``, #813) is held back while
    its evidence holds and listed under ``declined``; a record whose evidence changed is
    dropped from the file. A record for an item missing from ``--existing`` stays: a
    truncated or author-filtered listing does not prove the item is gone. ``--record-declines`` records every shown proposal not confirmed.
    """
    existing, context = _read_json(args.existing), _read_json(assess_dir / "run-context.json")
    if not isinstance(existing, list):
        print("assess_actions_render: --existing must hold a JSON array of tracker items", file=sys.stderr)
        return 1
    if not isinstance(context, dict):
        print(f"assess_actions_render: no readable run-context.json in {assess_dir}", file=sys.stderr)
        return 1
    items = [x for x in existing if isinstance(x, dict)]
    facts = run_facts(context)
    mode = ""
    if args.scheduled:
        if not args.today:
            print("assess_actions_render: --scheduled needs --today YYYY-MM-DD", file=sys.stderr)
            return 1
        mode = load_backlog_mode(args.repo)
        facts = scheduled_facts(facts)
    conditions, model = facts["conditions"], facts["model"]
    result = reconcile(items, facts, label=args.label, filed=args.filed)
    listed = {n for x in items if isinstance(n := x.get("number"), int)}
    live = refresh_declines(assess_dir, not_planned_closes(items), listed, conditions, model)  # #1543
    proposals, held = split_declined(result["proposals"], live, conditions, model)
    wip = load_backlog_wip(args.repo)
    proposals, next_up = within_wip(proposals, items, facts, wip, label=args.label)  # after declines (#1542)
    if args.scheduled:
        plan = plan_scheduled(items, facts, {**result, "proposals": proposals, "next_up": next_up}, mode=mode,
                              today=args.today, declined={r["number"]: proposal_disclosure(r) for r in held},
                              label=args.label, wip=wip, level=context.get("level"))
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        return 0
    if args.confirm is None:
        declined = [proposal_disclosure(r) for r in held]
        text = summary(proposals, result["rules_changed"], result["notes"], next_up, wip)
        if declined:
            text += "\n" + DECLINED_HEAD + "\n" + "".join(f"- {line}.\n" for line in declined)
        out = {**result, "proposals": proposals, "next_up": next_up, "declined": declined, "summary": text}
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0
    confirmed = {p["number"] for p in proposals} if args.confirm == "all" else (
        frozenset() if args.confirm == "none" else parse_numbers(args.confirm))
    if args.record_declines:
        reasons = dict(args.decline_reason)
        new = [r for p in proposals if p["number"] not in confirmed
               and (r := decline_record(p, conditions, model, declined_by=args.declined_by,
                                        declined_at=args.declined_at, reason=reasons.get(p["number"]))) is not None]
        if new:
            write_proposal_declines(assess_dir, merge_proposal_declines(live, new))
    print(json.dumps(confirmed_writes(proposals, confirmed), indent=2, ensure_ascii=False))
    return 0


def parse_issue(value: str) -> tuple[int, str]:
    """``RANK=REF`` to ``(rank, ref)``; argparse reports a bad value."""
    rank, sep, ref = value.partition("=")
    if not sep or not rank.strip().isdigit() or not ref.strip():
        raise argparse.ArgumentTypeError(f"expected RANK=REF, got {value!r}")
    return int(rank), ref.strip()


def parse_ref_reason(value: str) -> tuple[str, str]:
    """``--decline-reason REF=TEXT`` (plan) to ``(ref, text)``; argparse reports a bad value."""
    ref, sep, text = value.partition("=")
    if not sep or not ref.strip() or not text.strip():
        raise argparse.ArgumentTypeError(f"expected REF=TEXT, got {value!r}")
    return ref.strip(), text.strip()


def parse_reason(value: str) -> tuple[int, str]:
    """``--decline-reason N=TEXT`` to ``(number, text)``; argparse reports a bad value."""
    number, sep, text = value.partition("=")
    if not sep or not number.strip().lstrip("#").isdigit() or not text.strip():
        raise argparse.ArgumentTypeError(f"expected N=TEXT, got {value!r}")
    return int(number.strip().lstrip("#")), text.strip()


def parse_parts(value: str) -> tuple[str, int]:
    """``--parts REF=N`` to ``(ref, n)``: an action's rank or sub-item label and 0 or more parts (#1542)."""
    ref, sep, count = value.partition("=")
    if not sep or not ref.strip() or not count.strip().isdigit():
        raise argparse.ArgumentTypeError(f"expected REF=N, such as 2=3 or 2a=2, got {value!r}")
    return ref.strip(), int(count)


def non_negative(value: str) -> int:
    """``--wip``: a whole number of 0 or more; argparse reports anything else (#812)."""
    try:
        number = int(value)
    except ValueError:
        number = -1
    if number < 0:
        raise argparse.ArgumentTypeError(f"expected a whole number of 0 or more, got {value!r}")
    return number


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    p.add_argument("repo", type=Path, help="repository root")
    p.add_argument("--assess-dir", type=Path, help="assessment directory (default <repo>/.assess)")
    sub = p.add_subparsers(dest="command", required=True)
    t = sub.add_parser("table", help="the PR body's Top 3 table")
    t.add_argument("--issue", action="append", type=parse_issue, default=[], metavar="RANK=REF")
    i = sub.add_parser("items", help="the tracker items as a JSON array")
    i.add_argument("--report-link", help="full-report link for each item's Context block")
    pl = sub.add_parser("plan", help="the tracker items with a create / reuse step each")
    pl.add_argument("--existing", type=Path, required=True,
                    help="JSON array of the tracker's items "
                         "(gh issue list --json number,title,body,state,labels,stateReason,closedAt)")
    pl.add_argument("--label", default=DEFAULT_LABEL, help="label an unmarked item must carry; '' for none")
    pl.add_argument("--report-link", help="full-report link for each item's Context block")
    pl.add_argument("--decline", action="append", default=[], metavar="REF",
                    help="record the 6b offer for item REF (rank, or sub-item label) as declined; repeat per item")
    pl.add_argument("--declined-by", help="with --decline: who declined, for the record")
    pl.add_argument("--declined-at", help="with --decline: the date of the declines, for the record")
    pl.add_argument("--decline-reason", action="append", type=parse_ref_reason, default=[], metavar="REF=TEXT",
                    help="with --decline: the user's reason for declining item REF; repeat per item")
    pl.set_defaults(subparser=pl)
    nu = sub.add_parser("next-up", help="the report's Next up block: the items over the [backlog] wip limit")
    nu.add_argument("--existing", type=Path, required=True, help="the same file as plan --existing")
    nu.add_argument("--label", default=DEFAULT_LABEL, help="as for plan")
    for sp in (pl, nu):
        sp.add_argument("--wip", type=non_negative, metavar="N",
                        help="open-item limit (default: .assess/config.toml [backlog] wip, else 3)")
        sp.add_argument("--parts", action="append", type=parse_parts, default=[], metavar="REF=N",
                        help="action REF (rank, or sub-item label such as 2a) is filed split, and N of its parts "
                             "need a slot (created or reopened); repeat per split action")
    rc = sub.add_parser("reconcile", help="propose closing, reopening or demoting filed items; writes nothing to the tracker")
    rc.add_argument("--existing", type=Path, required=True,
                    help="JSON array of the tracker's items "
                         "(gh issue list --json number,title,body,state,labels,stateReason,closedAt)")
    rc.add_argument("--label", default=DEFAULT_LABEL, help="label an item must carry; '' for none")
    rc.add_argument("--filed", type=parse_numbers, default=frozenset(), metavar="N,...",
                    help="items this run filed or reused: never proposed superseded")
    rc.add_argument("--confirm", metavar="all|none|N,...", help="print only the writes of these confirmed items")
    rc.add_argument("--record-declines", action="store_true",
                    help="with --confirm: record each shown proposal not confirmed in .assess/backlog-declines.json")
    rc.add_argument("--declined-by", help="who declined, for the record (git config user.name)")
    rc.add_argument("--declined-at", help="the date of the declines, for the record (date +%%Y-%%m-%%d)")
    rc.add_argument("--decline-reason", action="append", type=parse_reason, default=[], metavar="N=TEXT",
                    help="the user's reason for declining item N, for the record; repeat per item")
    rc.add_argument("--scheduled", action="store_true",
                    help="print the scheduled run's writes under .assess/config.toml [backlog] mode (#814); "
                         "--existing items carry their comments")
    rc.add_argument("--today", help="with --scheduled (required): the run date for the status line (YYYY-MM-DD)")
    sc = sub.add_parser("status-comment", help="a new status comment for a filed item, for the scheduled reconcile")
    sc.add_argument("--today", required=True, help="the filing date (YYYY-MM-DD)")
    rc.set_defaults(subparser=rc)
    sub.add_parser("readiness", help="the PR summary's Readiness value")
    return p


def _check_decline_flags(args: argparse.Namespace) -> None:
    """Reject the decline flags unless ``--record-declines`` and ``--confirm`` are both given (#1533).

    Without both, ``reconcile`` records nothing, so a decline flag would be silently ignored.
    The error prints the ``reconcile`` usage, not the root one.
    """
    if args.command == "plan":
        given = [flag for flag, value in (("--declined-by", args.declined_by is not None),
                                          ("--declined-at", args.declined_at is not None),
                                          ("--decline-reason", bool(args.decline_reason))) if value]
        if given and not args.decline:
            args.subparser.error(f"{', '.join(given)} needs --decline")
        return
    if args.command != "reconcile":
        return
    given = [flag for flag, value in (("--record-declines", args.record_declines),
                                      ("--declined-by", args.declined_by is not None),
                                      ("--declined-at", args.declined_at is not None),
                                      ("--decline-reason", bool(args.decline_reason))) if value]
    if given and not (args.record_declines and args.confirm is not None):
        parser: argparse.ArgumentParser = args.subparser
        parser.error(f"{', '.join(given)} needs both --record-declines and --confirm")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _check_decline_flags(args)
    assess_dir: Path = args.assess_dir or args.repo / ".assess"
    if args.command == "readiness":
        print(readiness(assess_dir))
        return 0
    if args.command == "status-comment":
        sys.stdout.write(new_status_comment(status_line("Filed by /assess; the scheduled reconcile updates this line.",
                                                        args.today, None)))
        return 0
    if args.command == "reconcile":
        try:
            return run_reconcile(assess_dir, args)
        except (argparse.ArgumentTypeError, BacklogModeError) as exc:
            print(f"assess_actions_render: {exc}", file=sys.stderr)
            return 1
    try:
        entries = load_actions(assess_dir)
    except RenderError as exc:
        print(f"assess_actions_render: {exc}", file=sys.stderr)
        return 1
    if args.command == "table":
        sys.stdout.write(render_table(entries, dict(args.issue)))
        return 0
    items = render_items(entries, getattr(args, "report_link", None), model_version(assess_dir))
    if args.command in ("plan", "next-up"):
        limit = load_backlog_wip(args.repo)
        wip = args.wip if args.wip is not None else limit
        try:
            items = run_plan(assess_dir, args, items, wip)
        except RenderError as exc:
            print(f"assess_actions_render: {exc}", file=sys.stderr)
            return 1
        if args.command == "next-up":
            sys.stdout.write(next_up_markdown(items, [i["wip"] for i in items if "wip" in i], limit))
            return 0
    print(json.dumps(items, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
