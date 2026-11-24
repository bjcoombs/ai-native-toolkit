"""The ``fleet-view.html`` fixture (#946): four dated runs of the ``fleet_data_fixture`` fleet.

The fleet is ``fleet_data_fixture``'s: a three-level unit tree (group, subgroup, project), one
owner exclusion (``grp/sub/proj1`` excludes ``ci.1``), one not-measured cell (``grp/other/proj3``
docs) and one failed scan. Three earlier weekly runs lead up to it, so the replay credits level
moves, and one Step target is set (``grp`` to Step 3). The goldens are the page in each output
mode. Regenerate with ``uv run --locked python tests/fleet_page_fixture.py`` from
``skills/assess``, then review the diff.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fleet_data_fixture as fdf
from lib.fleet.fleet_achievements import Achievements, HistoryRun, build_achievements
from lib.fleet.fleet_data import FleetData, Privacy, build_fleet_data
from lib.fleet.fleet_page import build_page
from lib.fleet.fleet_page_data import PageBundle, page_bundle, picture
from lib.fleet.fleet_step_funnel import FILE as FUNNEL_FILE, QUESTION as FUNNEL_Q, TITLE as FUNNEL_T
from lib.fleet.fleet_step_funnel import render as funnel
from lib.fleet.fleet_step_targets import Target
from lib.fleet.fleet_units import build_trees, parse_units, repo_owners
from lib.fleet.runner import FleetRow

import tomllib

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "fleet_page"
DATES = ("2026-10-30", "2026-11-06", "2026-11-13", "2026-11-20")
VARIANTS = {"full": Privacy(full=True), "counts-only": Privacy(), "hashed": Privacy(salt=fdf.SALT)}
NAMES = ("grp", "proj1", "proj2", "proj3", "proj4", "Checkout", "gitlab.example.com", "bob", "maintainers",
         "payments", "checkout-leads")


def _rows(date: str) -> list[FleetRow]:
    """The fleet on ``date``: proj2 had no docs before this run; proj3 CI was off before 2026-11-13."""
    rows = copy.deepcopy(fdf.ROWS)
    for row in rows:
        scans = row["scans"]
        if scans is None:
            continue
        if row["repo"] == "grp/sub/proj2" and date < "2026-11-20":
            scans["docs"] = {"available": True, "lines": 0, "grade": "F"}
        if row["repo"] == "grp/other/proj3" and date < "2026-11-13":
            scans["ci"] = {"available": True, "required": False}
    return rows


def run(date: str, privacy: Privacy) -> FleetData:
    mapping = parse_units(tomllib.loads(fdf.UNITS))
    rows = _rows(date)
    repos = [row["repo"] for row in rows]
    stamp = {**fdf.STAMP, "scan_date": date, "counts_only": not privacy.full, "paths_hashed": privacy.salt is not None}
    return build_fleet_data(stamp, rows, fdf.model(), build_trees(repos, mapping, {}, fdf.CODEOWNERS),  # type: ignore[arg-type]
                            owners=repo_owners(mapping, fdf.CODEOWNERS, repos), web_urls=fdf.WEB_URLS, privacy=privacy)


def achievements(privacy: Privacy) -> Achievements:
    runs = [HistoryRun(d, run(d, privacy)) for d in DATES]
    target = Target("ownership", privacy.key("ownership/grp"), 3)
    return build_achievements(runs[-1], runs[:-1], fdf.model(), targets=[target])


def bundle(name: str) -> PageBundle:
    privacy = VARIANTS[name]
    data: dict[str, Any] = dict(run(DATES[-1], privacy))
    pictures = [picture(FUNNEL_FILE, FUNNEL_T, FUNNEL_Q, funnel(fdf.model(), data))]
    return page_bundle(data, dict(achievements(privacy)), fdf.model(), pictures)


def goldens() -> dict[str, str]:
    return {f"expected-{name}.html": build_page(bundle(name)) for name in VARIANTS}


if __name__ == "__main__":
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for file, text in goldens().items():
        (FIXTURES / file).write_text(text, encoding="utf-8", newline="\n")
