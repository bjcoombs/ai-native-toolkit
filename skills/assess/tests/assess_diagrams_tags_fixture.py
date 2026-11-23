"""The fixture tag rows and golden renders of the tag profile strip (#940).

``LEVEL`` is a hand-built level block whose ``tags`` hold every row state of
``lib.assess_diagrams_tags.row_state``: measured shares (including a real 0 of 2), a tag no
condition carries (``contracts``), a tag listed only, a tag not measured (a plain and a
``requires:`` reason) and a tag set aside by the owner. ``PRIOR`` is an earlier run under the
same maturity model (it measured docs, which this run did not), ``PRIOR_OTHER`` one under
another. ``test_assess_diagrams_tags.py`` fails
when a committed golden differs from a fresh render. Regenerate the goldens with
``uv run --locked python tests/assess_diagrams_tags_fixture.py`` from ``skills/assess``, then
review the diff.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib.assess_diagrams import diagrams_section  # noqa: E402
from lib.assess_diagrams_tags import TAG_FILE, tag_diagrams  # noqa: E402
from lib.level_result_types import TagRow  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "assess_diagrams"


def _row(tag: str, conditions: int, countable: int, set_aside: int, met: int, measured: int,
         missing: tuple[tuple[str, str], ...] = ()) -> TagRow:
    return {"tag": tag, "conditions": conditions, "countable": countable, "set_aside": set_aside,
            "met": met, "measured": measured,
            "not_measured": [{"id": cid, "reason": reason} for cid, reason in missing]}


TAGS: list[TagRow] = [
    _row("behaviour", 3, 3, 0, 2, 3),
    _row("contracts", 0, 0, 0, 0, 0),
    _row("docs", 3, 2, 0, 0, 0, (("doc.1", "needs_platform"), ("doc.2", "needs_platform"), ("doc.3", "no_remote"))),
    _row("structure", 6, 5, 0, 0, 2, (("st.3", "requires:layer-4-architecture.1"),)),
    _row("agent_context", 6, 4, 0, 4, 4),
    _row("agent_runtime", 4, 0, 0, 0, 0),
    _row("governance", 3, 1, 0, 0, 0, (("gov.2", "requires:layer-7-review.1"), ("gov.3", "requires:layer-7-review.1"))),
    _row("supply_chain", 2, 1, 1, 0, 0, (("sc.2", "no_value"),)),
]

LEVEL: dict[str, Any] = {
    "level_result_version": 3, "levels_version": "fixture-2", "tally": {"met": 6, "measured": 9, "not_measured": 6},
    "counted": [], "listed": [], "not_measured": [], "conditions": [], "step": None, "next_step": None,
    "tags": TAGS,
}
PRIOR: dict[str, Any] = {
    "prior_level_version": 1, "levels_version": "fixture-2",
    "tags": [{**row, "met": 1} if row["tag"] == "behaviour" else
             {**row, "met": 1, "measured": 2, "not_measured": []} if row["tag"] == "docs" else
             {**row, "met": 0, "measured": 0, "not_measured": [{"id": "a.1", "reason": "no_value"}]}
             if row["tag"] == "agent_context" else row for row in TAGS],
}
PRIOR_OTHER: dict[str, Any] = {**PRIOR, "levels_version": "fixture-1"}


def goldens() -> dict[str, str]:
    """The golden renders: ``expected-<file>`` -> text."""
    same = tag_diagrams({"level": LEVEL, "prior_level": PRIOR})
    other = tag_diagrams({"level": LEVEL, "prior_level": PRIOR_OTHER})
    assert same[0]["svg"] is not None and other[0]["svg"] is not None
    stem = TAG_FILE.removesuffix(".svg")
    return {f"expected-{TAG_FILE}": same[0]["svg"], f"expected-{stem}-other-model.svg": other[0]["svg"],
            "expected-tag-section.md": diagrams_section(same) + "\n"}


if __name__ == "__main__":
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for name, content in goldens().items():
        (FIXTURES / name).write_text(content, encoding="utf-8", newline="\n")
