"""The fixture per-tag tallies and golden render of the fleet median tag profile strip (#940).

``TALLIES`` are hand-built per-repository tallies (``fleet-data.json`` ``repos[].tags``, #1073)
under the shipped maturity model, which hold every row state of
``lib.fleet.fleet_tag_profile.row_state``: shares across several repositories (a whisker),
a tag one repository measured (the whisker collapses to the dot), a real 0, a tag no
condition carries (``contracts``), tags whose conditions are listed only (``agent_runtime``,
``supply_chain``) and a tag no repository measured (``governance``). The fourth repository
failed and has no tally. ``test_fleet_tag_profile.py`` fails when the committed golden
differs from a fresh render. Regenerate with
``uv run --locked python tests/fleet_tag_profile_fixture.py`` from ``skills/assess``, then
review the diff.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib.assess_diagrams_tags import tag_whys
from lib.fleet.fleet_tag_profile import FILE, FleetTagRow, render, tag_profile
from lib.scoring.level_result_types import TagCell
from lib.scoring.level_tally import default_model

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "fleet_diagrams"


def _cell(tag: str, met: int, measured: int, not_measured: int = 0) -> TagCell:
    return {"tag": tag, "met": met, "measured": measured, "verified": 0, "not_measured": not_measured,
            "hazards": 0, "declined": 0}


def _repo(behaviour: tuple[int, int], docs: tuple[int, int], structure: tuple[int, int],
          agent: tuple[int, int]) -> list[TagCell]:
    return [_cell("behaviour", *behaviour), _cell("docs", *docs), _cell("structure", *structure),
            _cell("agent_context", *agent), _cell("agent_runtime", 0, 0, 2),
            _cell("governance", 0, 0, 3), _cell("supply_chain", 0, 0, 1)]


TALLIES: list[list[TagCell] | None] = [
    _repo((2, 3), (1, 2), (0, 5), (4, 4)),
    _repo((1, 3), (0, 0), (2, 5), (1, 4)),
    _repo((3, 3), (0, 0), (0, 0), (2, 4)),
    None,
    _repo((0, 0), (0, 0), (0, 0), (3, 4)),
]


def rows() -> list[FleetTagRow]:
    profile = tag_profile(default_model(), TALLIES)
    assert profile is not None
    return profile


def goldens() -> dict[str, str]:
    """The golden render: ``expected-<file>`` -> text."""
    return {f"expected-{FILE.removesuffix('.svg')}-states.svg": render(rows(), tag_whys())}


if __name__ == "__main__":
    for name, content in goldens().items():
        (FIXTURES / name).write_text(content, encoding="utf-8", newline="\n")
