"""The headline block with the report's top card filled in (#1102, #1252).

``run_headline(level, since, choice, rollup, measured, assess_dir)`` is the run context's
``headline`` block: ``lib/headline.py`` words the level under the run's model, and
``summarised`` fills the three fields that block leaves empty with the top card
(``lib/top_card.py``), which states each fact about where the repository stands once:

- ``summary_lines``: the card's lines, plain text. A run under a profile or a ``--levels``
  model (#1150) leads them with ``lib.profiles.model_lines``: the model named, then whether
  every practice holds (``Ready for handover (profile: handover).``) or exactly which do not,
  then the practices the default model does not define, when there are any. A default run
  has none of them;
- ``call_to_action``: the card's one ``Start here:`` line;
- ``summary_markdown``: the lines as paragraphs, the card's lead line bold, which the
  deterministic report (``assess_report.py``) and the findings template paste verbatim at the
  very top.

The card reads the ``since_last_run`` block, the ``unit_rollup`` block (the sibling unit that
already holds the blocking gate), the measured commit and the first open action of
``<assess_dir>/actions.json`` (``top_card.read_first_action``). A headline that is unavailable
keeps the fields empty. Under counts-only the three fields are dropped.

``run_headline`` also suffixes the one-line ``summary`` with the model's label under a profile
or a ``--levels`` model (``Step 3 · all gates held (profile: citizen)``), because the badge and
the ``log.md`` readiness line carry only that line; a default run's summary is unchanged. Tests:
``tests/test_exec_summary.py``.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from lib.headline import headline
from lib.level_result_types import Headline, LevelResult
from lib.levels import MaturityModel
from lib.profiles import ModelChoice, chosen_model, model_lines
from lib.top_card import GATE_TASK, STEP_GATES_URL, CardAction, card_lines, card_markdown, read_first_action

__all__ = ["GATE_TASK", "STEP_GATES_URL", "run_headline", "summarised"]


def summarised(block: Headline, level: Any, since: Any, model: MaturityModel,
               choice: ModelChoice | None = None, rollup: Any = None, head_sha: str | None = None,
               action: CardAction | None = None) -> Headline:
    """``block`` with its summary fields holding the top card; unchanged when the headline is
    unavailable or ``level`` is not a level result. ``choice`` is the run's model choice: a
    profile or ``--levels`` model leads the lines with ``profiles.model_lines``."""
    if block.get("available") is not True or not isinstance(level, dict) or "tally" not in level:
        return block
    result = cast(LevelResult, level)  # narrowed by the tally check above
    named = model_lines(result, choice)
    card = card_lines(result, model, since, rollup, head_sha, action)
    lines = [*named, *card]
    start = next((line for line in card if line.startswith("Start here:")), "")
    return {**block, "summary_lines": lines, "call_to_action": start,
            "summary_markdown": card_markdown(lines, lead_at=len(named))}


def _head_sha(measured: Any) -> str | None:
    sha = measured.get("head_sha") if isinstance(measured, Mapping) else None
    return sha if isinstance(sha, str) and sha else None


def run_headline(level: Any, since: Any, choice: ModelChoice | None, rollup: Any = None,
                 measured: Any = None, assess_dir: Path | None = None) -> Headline:
    """The run context's ``headline`` block: the headline of ``level`` under the run's model
    (``choice``, the default when None), with the top card from ``since``, the unit roll-up,
    the measured commit and ``<assess_dir>/actions.json``."""
    model = chosen_model(choice)
    block = summarised(headline(level, model), level, since, model, choice, rollup,
                       _head_sha(measured), read_first_action(assess_dir))
    if choice is None or choice.kind == "default":
        return block
    # the badge and the log.md line carry only the summary: it names the model it came from
    return {**block, "summary": f"{block['summary']} ({choice.label})"}
