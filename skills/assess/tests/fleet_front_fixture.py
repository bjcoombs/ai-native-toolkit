"""The fleet front screen fixture (#1255): ``report.md``'s first section in each output mode.

The fleet is ``fleet_page_fixture``'s last run (``fleet_data_fixture``'s fleet, with Steps), its
groups ``grp/other`` and ``grp/sub``, and the fixed Step movement grid ``fleet_page_fixture.MOVES``.
The goldens are the report, front screen first, under ``tests/fixtures/fleet_front/``.
Regenerate with ``uv run --locked python tests/fleet_front_fixture.py`` from ``skills/assess``,
then review the diff.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fleet_data_fixture as fdf
import fleet_page_fixture as fpf
from lib.fleet.fleet_diagrams import report_markdown
from lib.fleet.fleet_front import FrontScreen, front_screen, front_section, report_teams
from lib.fleet.fleet_page_data import mode_of

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "fleet_front"
GROUPS = ("grp/sub", "grp/other")  # unsorted on purpose: the links come out alphabetical


def front(name: str) -> FrontScreen:
    privacy = fpf.VARIANTS[name]
    data = fpf.run(fpf.DATES[-1], privacy)
    screen = front_screen(fdf.model(), data, fpf.MOVES)
    screen["teams"], screen["teams_index"] = report_teams([privacy.key(g) for g in GROUPS], mode_of(data))
    return screen


def report(name: str) -> str:
    data = fpf.run(fpf.DATES[-1], fpf.VARIANTS[name])
    return report_markdown([], credit="## What changed and who earned it\n\nIn place: credit.\n",
                           front=front_section(front(name), len(data["repos"])))


def goldens() -> dict[str, str]:
    return {f"expected-{name}.md": report(name) for name in fpf.VARIANTS}


if __name__ == "__main__":
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for file, text in goldens().items():
        (FIXTURES / file).write_text(text, encoding="utf-8", newline="\n")
