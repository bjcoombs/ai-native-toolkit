"""The evidence rule for a line of the report's top card (#1253, #1300).

Every line above the fold must give the reader something to check: a digit, a Markdown link,
or a backticked command or path. One rule, applied in two places:

- ``tests/test_card_construction.py`` lints the rendered first screen of every card state with
  ``lint_errors``;
- ``assess_finalize.py`` applies ``done_when_warnings`` to the model-written ``done_when`` of
  each Top 3 action, because the card's Done when line is the first open action's
  ``done_when`` (``lib/card_refresh.py``). A failing ``done_when`` is a warning naming the
  action's rank and text, never a refusal: the contract is still written.

- ``carries_evidence(text)``: True when ``text`` holds a digit, a link or a backticked span;
- ``lint_errors(paragraphs)``: the paragraphs that carry none, in order;
- ``done_when_warnings(actions)``: one warning per action whose string ``done_when`` carries
  none, then one per item of its ``sub_items`` list (#1263) that carries none, named by the
  parent's rank and the sub-item's ``label`` (#1518); an entry or sub-item that is not a
  mapping, or has no string ``done_when``, is skipped. Finalize passes only the entries it
  writes to ``actions.json``, with only the sub-items it keeps, so a dropped one is not linted.

Tests: ``tests/test_card_lint.py``, ``tests/test_card_construction.py``.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

__all__ = ["CODE", "DIGIT", "LINK", "carries_evidence", "done_when_warnings", "lint_errors"]

LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
CODE = re.compile(r"`[^`]+`")
DIGIT = re.compile(r"\d")


def carries_evidence(text: str) -> bool:
    """True when ``text`` holds a digit, a Markdown link or a backticked command or path."""
    return bool(DIGIT.search(text) or LINK.search(text) or CODE.search(text))


def lint_errors(paragraphs: Sequence[str]) -> list[str]:
    """Paragraphs with no digit, no Markdown link and no backticked command or path."""
    return [p for p in paragraphs if not carries_evidence(p)]


def done_when_warnings(actions: Sequence[object]) -> list[str]:
    """One warning per action, then per sub-item, whose ``done_when`` gives the reader nothing to check."""
    warnings: list[str] = []
    for a in actions:
        if not isinstance(a, Mapping):
            continue
        rank = a.get("rank", "?")
        warnings += _warning(a, f"action rank {rank}")
        subs = a.get("sub_items")
        for sub in subs if isinstance(subs, list) else []:
            if isinstance(sub, Mapping):
                warnings += _warning(sub, f"action rank {rank} sub-item {sub.get('label', '?')!r}")
    return warnings


def _warning(item: Mapping[str, object], name: str) -> list[str]:
    """The one warning for ``item`` when its string ``done_when`` carries no evidence, else none."""
    done_when = item.get("done_when")
    if not isinstance(done_when, str) or carries_evidence(done_when):
        return []
    return [f"{name} ({item.get('action', '?')!r}): done_when "
            f"{done_when!r} has no backticked command or path, link or number"]
