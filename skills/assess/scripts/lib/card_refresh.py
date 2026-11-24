"""Re-render the top card's Start here and Done when after finalize writes this run's Top 3 (#1252).

The core words the card (``lib/top_card.py``) before the model has chosen this run's Top 3,
so its Start here and Done when read the ``actions.json`` the previous run left.
``assess_finalize.py`` writes this run's ``actions.json`` and then calls
``refresh_card(assess_dir)``, which:

1. reads ``<assess_dir>/actions.json`` (``top_card.read_first_action``: the first open entry
   by rank) and the run context's ``level`` block;
2. rewords the card's two lines with ``top_card.start_lines``, the same function the core
   uses, so the wording cannot drift; every other card line stays as the core wrote it;
3. writes the three card fields of the run context's ``headline`` block back
   (``summary_lines``, ``call_to_action``, ``summary_markdown``; ``refreshed``);
4. replaces the card in ``<assess_dir>/assess-report.md`` (``refreshed_report``): the whole
   pasted ``summary_markdown`` when it is there verbatim, else the old Start here and Done when
   lines where each stands as a paragraph of its own.

It returns a warning for the caller to print when the report holds no card to refresh, and
None otherwise. A headline without card fields (counts-only, or an unavailable headline) and a
card whose lines did not change are left alone, so a second finalize writes nothing new.
A translation under the card (#1174) is the model's text and is not rewritten.
Tests: ``tests/test_card_refresh.py``.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from lib.level_result_types import Headline
from lib.top_card import read_first_action, start_lines

__all__ = ["refresh_card", "refreshed", "refreshed_report"]

_START, _DONE = "Start here:", "Done when:"


def _swap(items: list[str], start: str, done: str) -> list[str]:
    return [start if i.startswith(_START) else done if i.startswith(_DONE) else i for i in items]


def refreshed(block: Any, level: Any, action: Any) -> Headline | None:
    """``block`` with the card's Start here and Done when reworded for ``action`` (a
    ``CardAction`` or None); None when ``block`` carries no card or nothing changes."""
    if not isinstance(block, Mapping) or not isinstance(level, Mapping) or "tally" not in level:
        return None
    lines, markdown = block.get("summary_lines"), block.get("summary_markdown")
    if not isinstance(lines, list) or not lines or not isinstance(markdown, str) or not markdown:
        return None
    start, done = start_lines(level, action)
    new_lines = _swap([str(line) for line in lines], start, done)
    if new_lines == lines:
        return None
    new_markdown = "\n\n".join(_swap(markdown.split("\n\n"), start, done))
    return {**block, "summary_lines": new_lines, "call_to_action": start,  # type: ignore[return-value]
            "summary_markdown": new_markdown}


def _paragraph_swap(text: str, old: str, new: str) -> str | None:
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line == old:
            lines[i] = new
            return "\n".join(lines)
    return None


def refreshed_report(text: str, old: Mapping[str, Any], new: Mapping[str, Any]) -> str | None:
    """``text`` with the ``old`` card replaced by the ``new`` one; None when ``text`` holds
    neither the old ``summary_markdown`` nor its Start here and Done when lines."""
    before, after = old.get("summary_markdown"), new.get("summary_markdown")
    if isinstance(before, str) and isinstance(after, str) and before and before in text:
        return text.replace(before, after, 1)
    pairs = [(o, n) for o, n in zip(old.get("summary_lines") or [], new.get("summary_lines") or [])
             if isinstance(o, str) and o.startswith((_START, _DONE)) and o != n]
    for o, n in pairs:
        swapped = _paragraph_swap(text, o, n)
        if swapped is None:
            return None
        text = swapped
    return text if pairs else None


def refresh_card(assess_dir: Path) -> str | None:
    """Re-render the card in ``<assess_dir>/run-context.json`` and ``assess-report.md`` from
    ``<assess_dir>/actions.json``; a warning when the report holds no card to refresh."""
    ctx_path = assess_dir / "run-context.json"
    try:
        ctx = json.loads(ctx_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(ctx, dict):
        return None
    old = ctx.get("headline")
    new = refreshed(old, ctx.get("level"), read_first_action(assess_dir))
    if new is None or not isinstance(old, Mapping):
        return None
    ctx["headline"] = new
    ctx_path.write_text(json.dumps(ctx, indent=2), encoding="utf-8")
    report = assess_dir / "assess-report.md"
    try:
        text = report.read_text(encoding="utf-8")
    except OSError:
        return None
    swapped = refreshed_report(text, old, new)
    if swapped is None:
        return (f"{report.name}: the top card was not found, so its Start here still names the "
                "previous run's action; paste headline.summary_markdown from run-context.json")
    report.write_text(swapped, encoding="utf-8")
    return None
