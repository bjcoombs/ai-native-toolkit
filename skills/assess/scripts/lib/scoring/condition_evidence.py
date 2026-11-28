"""Evidence lines on a met condition: what an evidence-only block adds to a condition (issue #855).

Some practices are evidence for a condition rather than a condition of their own (maintainer
decision on #855): they never count, never move the tally or a Step, and never stand in for
another gate. ``EVIDENCE_FOR`` maps a criterion signal to the block and field that hold such
evidence. ``attach_evidence(model, conditions, data)`` sets ``evidence`` on each condition whose
criteria read that signal, only when the condition's state is ``met`` and the block is
``available`` with a non-empty list there; any other state, or an absent, unavailable or
malformed block, attaches nothing.

Today one entry: ``ci_checks.enforced_check_runs`` (the tests-run-in-CI condition,
``layer-5-ci.1`` in the default model) takes the ``behaviour_pins`` block's ``practices``
(``lib.testing.behaviour_pins``), strongest first. A ``--levels`` model whose condition reads
the same signal takes it too. ``EVIDENCE_BLOCKS`` are the blocks the ``level`` entry reads for
this, after the model's own (``level_tally.LEVEL_READS``).

Pure; never reads files. Tests: ``tests/test_behaviour_pins_evidence.py``.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from lib.scoring.level_result_types import ConditionResult
from lib.scoring.levels import MaturityModel, condition_id

EVIDENCE_FOR: Mapping[str, tuple[str, str]] = {
    "ci_checks.enforced_check_runs": ("behaviour_pins", "practices"),
}
EVIDENCE_BLOCKS = tuple(dict.fromkeys(block for block, _ in EVIDENCE_FOR.values()))


def _evidence(data: Mapping[str, Any] | None, block_name: str, field: str) -> list[str]:
    block = data.get(block_name) if isinstance(data, Mapping) else None
    if not isinstance(block, Mapping) or block.get("available") is not True:
        return []
    values = block.get(field)
    return [v for v in values if isinstance(v, str)] if isinstance(values, list) else []


def attach_evidence(model: MaturityModel, conditions: Sequence[ConditionResult],
                    data: Mapping[str, Any] | None) -> None:
    """Set ``evidence`` on each met condition whose criteria read an ``EVIDENCE_FOR`` signal."""
    by_id = {c["id"]: c for c in conditions}
    for row in model["rows"]:
        for level in row["levels"]:
            cond = by_id.get(condition_id(row["id"], level["level"]))
            if cond is None or cond["state"] != "met":
                continue
            found = [e for c in level["criteria"] if c["signal"] in EVIDENCE_FOR
                     for e in _evidence(data, *EVIDENCE_FOR[c["signal"]])]
            if found:
                cond["evidence"] = list(dict.fromkeys(found))
