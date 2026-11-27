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
  the label check (filing upstream without triage access).
- ``reconcile --existing FILE [--label NAME] [--filed N,...] [--confirm all|N,...]`` (#811)
  prints the propose-only reconcile of the tracker's items in ``FILE`` against
  ``run-context.json``: ``{proposals, rules_changed, notes, summary}``, the close, reopen,
  demote and superseded proposals and the batched summary to confirm. It writes nothing.
  ``--filed`` names the items this run filed or reused, which are never proposed superseded.
  With ``--confirm`` it prints only the writes of the confirmed item numbers (``all`` for
  every proposal) as a JSON array. It needs no ``actions.json``; rules:
  ``lib/report/backlog_reconcile.py``.
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

from lib.report.backlog_items import DEFAULT_LABEL, FilingStep, condition_marker, plan_filing  # noqa: E402
from lib.report.backlog_reconcile import confirmed_writes, reconcile, run_facts  # noqa: E402

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


def plan_items(items: Sequence[TrackerItem], existing: object, label: str = DEFAULT_LABEL) -> list[TrackerItem]:
    """``items`` with a ``filing`` step each, matched against the tracker's ``existing`` items (#810)."""
    if not isinstance(existing, list):
        raise RenderError("--existing must hold a JSON array of tracker items")
    known = [x for x in existing if isinstance(x, dict)]
    steps = plan_filing(items, known, label=label)
    return [TrackerItem(**item, filing=step) for item, step in zip(items, steps, strict=True)]


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


def run_reconcile(assess_dir: Path, args: argparse.Namespace) -> int:
    """The ``reconcile`` subcommand: proposals, or with ``--confirm`` the confirmed writes only."""
    existing, context = _read_json(args.existing), _read_json(assess_dir / "run-context.json")
    if not isinstance(existing, list):
        print("assess_actions_render: --existing must hold a JSON array of tracker items", file=sys.stderr)
        return 1
    if not isinstance(context, dict):
        print(f"assess_actions_render: no readable run-context.json in {assess_dir}", file=sys.stderr)
        return 1
    items = [x for x in existing if isinstance(x, dict)]
    result = reconcile(items, run_facts(context), label=args.label, filed=args.filed)
    if args.confirm is None:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    proposals = result["proposals"]
    confirmed = {p["number"] for p in proposals} if args.confirm == "all" else parse_numbers(args.confirm)
    print(json.dumps(confirmed_writes(proposals, confirmed), indent=2, ensure_ascii=False))
    return 0


def parse_issue(value: str) -> tuple[int, str]:
    """``RANK=REF`` to ``(rank, ref)``; argparse reports a bad value."""
    rank, sep, ref = value.partition("=")
    if not sep or not rank.strip().isdigit() or not ref.strip():
        raise argparse.ArgumentTypeError(f"expected RANK=REF, got {value!r}")
    return int(rank), ref.strip()


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
                    help="JSON array of the tracker's items (gh issue list --json number,title,body,state,labels)")
    pl.add_argument("--label", default=DEFAULT_LABEL, help="label an unmarked item must carry; '' for none")
    pl.add_argument("--report-link", help="full-report link for each item's Context block")
    rc = sub.add_parser("reconcile", help="propose closing, reopening or demoting filed items; writes nothing")
    rc.add_argument("--existing", type=Path, required=True,
                    help="JSON array of the tracker's items (gh issue list --json number,title,body,state,labels)")
    rc.add_argument("--label", default=DEFAULT_LABEL, help="label an item must carry; '' for none")
    rc.add_argument("--filed", type=parse_numbers, default=frozenset(), metavar="N,...",
                    help="items this run filed or reused: never proposed superseded")
    rc.add_argument("--confirm", metavar="all|N,...", help="print only the writes of these confirmed items")
    sub.add_parser("readiness", help="the PR summary's Readiness value")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    assess_dir: Path = args.assess_dir or args.repo / ".assess"
    if args.command == "readiness":
        print(readiness(assess_dir))
        return 0
    if args.command == "reconcile":
        try:
            return run_reconcile(assess_dir, args)
        except argparse.ArgumentTypeError as exc:
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
    items = render_items(entries, args.report_link, model_version(assess_dir))
    if args.command == "plan":
        try:
            items = plan_items(items, _read_json(args.existing), args.label)
        except RenderError as exc:
            print(f"assess_actions_render: {exc}", file=sys.stderr)
            return 1
    print(json.dumps(items, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
