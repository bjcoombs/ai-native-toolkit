"""The fixture fleet-data and golden renders of the estate tag heatmap (#940).

``DATA`` is a hand-built ``fleet-data.json`` slice: five repositories under the shipped
maturity model with different Steps (one with none, one failed with no tally), per-tag
tallies with real shares, a real 0, cells measured on no condition, and ``tag_profile``
saying ``contracts`` has no condition yet. ``FULL`` keeps names (``--full``), ``HASHED`` is a
``--hash-salt`` run whose keys are hashes, and ``COUNTS_ONLY`` neither, so its rows are
numbered. ``test_fleet_tag_heatmap.py`` fails when a committed golden differs from a fresh
render. Regenerate with ``uv run --locked python tests/fleet_tag_heatmap_fixture.py`` from
``skills/assess``, then review the diff.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib.fleet.fleet_tag_heatmap import FILE, heatmap_rows, render, uncarried
from lib.level_result_types import TagCell

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "fleet_diagrams"
NAMES = ("acme/payments-api", "acme/web-frontend", "acme/infra-terraform", "acme/data-pipeline",
         "acme/legacy-billing")


def _cell(tag: str, met: int, measured: int) -> TagCell:
    return {"tag": tag, "met": met, "measured": measured, "verified": 0, "not_measured": 0,
            "hazards": 0, "declined": 0}


def _tags(behaviour: tuple[int, int], docs: tuple[int, int], structure: tuple[int, int],
          agent: tuple[int, int], governance: tuple[int, int]) -> list[TagCell]:
    return [_cell("behaviour", *behaviour), _cell("docs", *docs), _cell("structure", *structure),
            _cell("agent_context", *agent), _cell("agent_runtime", 0, 0), _cell("governance", *governance),
            _cell("supply_chain", 0, 0)]


REPOS: list[dict[str, Any]] = [
    {"key": NAMES[0], "step": 2, "tags": _tags((2, 3), (1, 2), (3, 5), (4, 4), (0, 0))},
    {"key": NAMES[1], "step": 3, "tags": _tags((3, 3), (2, 2), (5, 5), (4, 4), (1, 1))},
    {"key": NAMES[2], "step": None, "tags": _tags((0, 0), (0, 2), (0, 0), (1, 4), (0, 0))},
    {"key": NAMES[3], "step": 2, "tags": _tags((1, 3), (0, 0), (0, 5), (2, 4), (0, 1))},
    {"key": NAMES[4], "step": None, "tags": None},
]
PROFILE = [{"tag": t, "conditions": 0 if t == "contracts" else 3} for t in
           ("behaviour", "contracts", "docs", "structure", "agent_context", "agent_runtime", "governance",
            "supply_chain")]


def _data(counts_only: bool, hashed: bool, repos: list[dict[str, Any]] = REPOS) -> dict[str, Any]:
    return {"run": {"counts_only": counts_only, "paths_hashed": hashed}, "repos": repos, "tag_profile": PROFILE}


FULL = _data(False, False)
COUNTS_ONLY = _data(True, False)
HASHED = _data(True, True, [{**r, "key": f"h:{i:04x}{i * 7919:08x}"} for i, r in enumerate(REPOS, start=1)])


def goldens() -> dict[str, str]:
    """The golden renders: ``expected-<file>`` -> text."""
    stem = FILE.removesuffix(".svg")
    return {f"expected-{stem}-full.svg": render(heatmap_rows(FULL), uncarried(FULL)),
            f"expected-{stem}-counts-only.svg": render(heatmap_rows(COUNTS_ONLY), uncarried(COUNTS_ONLY))}


if __name__ == "__main__":
    for name, content in goldens().items():
        (FIXTURES / name).write_text(content, encoding="utf-8", newline="\n")
