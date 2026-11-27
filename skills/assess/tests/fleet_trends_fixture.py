"""Builds the fixture run history under ``tests/fixtures/fleet_trends/`` (issues #676, #806).

``history/acme/<date>[.<n>]/`` holds synthetic ``run.json``, ``repos.jsonl`` and, for runs
that stored Steps, ``level.json``; ``CURRENT`` is this run, held in memory as ``fleet.py``
holds it. ``test_fleet_trends.py`` fails when the committed files differ from a fresh build.
Regenerate them and the golden render with ``uv run --locked python tests/fleet_trends_fixture.py``
from ``skills/assess``, then review the diff.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib.fleet.fleet_diagrams import build_diagrams, report_markdown
from lib.fleet.runner import FLEET_ROW_VERSION
from lib.fleet.fleet_stamp import FLEET_STAMP_VERSION, FleetStamp
from lib.scoring.levels import MaturityModel
from lib.fleet.fleet_trends import TrendRun, history_runs, render_trend, series_runs, trend_run

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "fleet_trends"
ORG = FIXTURES / "history" / "acme"
LONG = "platform/infrastructure-and-shared-services"
REPOS = frozenset({"payments/r0", "platform/r0"})

Steps = dict[str, tuple[int | None, ...]]  # group -> each repository's Step; None: not measured


def stamp(scan_date: str, version: str | None, window: str = "6mo") -> FleetStamp:
    return {"fleet_stamp_version": FLEET_STAMP_VERSION, "toolkit_commit": None,
            "plugin_version": "2.0.0", "scan_date": scan_date, "window": window,
            "levels_version": version, "counts_only": True, "paths_hashed": False, "scans": None,
            "repositories": {"selected": 2, "scanned_ok": 2, "failed": 0, "qualifying": 2}}


def members(groups: Steps) -> list[tuple[str, str]]:
    """``(group, repository)`` pairs, repository ``<group>/r<i>``."""
    return [(group, f"{group}/r{i}") for group, steps in sorted(groups.items()) for i in range(len(steps))]


def repo_steps(groups: Steps) -> dict[str, int | None]:
    return {f"{group}/r{i}": step for group, steps in groups.items() for i, step in enumerate(steps)}


# This run's maturity model (levels-2) with four Steps; the trend reads only its Step numbers.
LEVELS: MaturityModel = {"version": "levels-2", "rows": [
    {"id": "row0", "title": "Row 0", "scope": "team", "levels": [{"level": 1, "criteria": []}]}],
    "steps": [{"step": n, "name": f"Step {n}", "gates": []} for n in range(1, 5)]}
# (entry name, maturity-model version, window, {group: repository Steps}, Steps stored)
# payments holds Steps 1, 3 and 3: its mean (2.33) and its lowest (1) differ, and the line is the lowest.
ENTRIES: tuple[tuple[str, str | None, str, Steps, bool], ...] = (
    ("2026-05-20", "levels-1", "3mo", {"payments": (3, 3)}, True),  # another window: not comparable
    ("2026-06-20", "levels-1", "6mo", {"payments": (1, 1), "platform": (2, 2)}, False),  # before level.json
    ("2026-07-20", "levels-1", "6mo", {"payments": (1, 2), "platform": (2, 3)}, False),
    ("2026-08-20", "levels-1", "6mo", {"payments": (1, 3, 3), "platform": (2, 3), "data": (None, None),
                                       "web": (2, 2), "mobile": (1, None), LONG: (2, 2)}, True),
    ("2026-09-20", "levels-2", "6mo", {"payments": (1, 3, 3), "platform": (2, 3), "data": (2, 1),
                                       "web": (2, 2), "mobile": (1, 2), LONG: (1, 2)}, True),
    ("2026-10-20", "levels-2", "6mo", {"payments": (2, 3, 3), "platform": (3, 3), "data": (2, 2),
                                       "web": (2, None), LONG: (2, 2)}, True),
)
CURRENT_STAMP = stamp("2026-11-20", "levels-2")
CURRENT: Steps = {"payments": (2, 3, 4), "platform": (3, 3), "data": (2, 2), "web": (2, 3),
                  "mobile": (1, 1), LONG: (3, 3)}


def build(root: Path) -> None:
    """Write the history entries under ``root/history/acme`` plus a malformed one."""
    org = root / "history" / "acme"
    for name, version, window, groups, stored in ENTRIES:
        entry = org / name
        entry.mkdir(parents=True, exist_ok=True)
        (entry / "run.json").write_text(json.dumps(stamp(name, version, window), sort_keys=True) + "\n",
                                         encoding="utf-8", newline="\n")
        rows = [json.dumps({"fleet_row_version": FLEET_ROW_VERSION, "group": g, "repo": r}, sort_keys=True)
                for g, r in members(groups)]
        (entry / "repos.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")
        if stored:
            level = {"repos": {r: {"step": s} for r, s in sorted(repo_steps(groups).items())}}
            (entry / "level.json").write_text(json.dumps(level, sort_keys=True) + "\n", encoding="utf-8",
                                              newline="\n")
    broken = org / "2026-04-20"
    broken.mkdir(parents=True, exist_ok=True)
    (broken / "run.json").write_text("{not json\n", encoding="utf-8", newline="\n")


def runs(root: Path) -> list[TrendRun]:
    """The trend's runs: the history under ``root``, then ``CURRENT``."""
    current = trend_run(CURRENT_STAMP, members(CURRENT), repo_steps(CURRENT))
    assert current is not None
    return series_runs(history_runs(root / "history" / "acme", CURRENT_STAMP, REPOS), current)


STEPS = [step["step"] for step in LEVELS.get("steps", [])]


def goldens(root: Path) -> dict[str, str]:
    """The trend render and a report embedding it (the other diagrams skip: no repositories)."""
    report = report_markdown(build_diagrams(LEVELS, [], "6mo", history=runs(root)))
    return {"expected-trend.svg": render_trend(runs(root), STEPS), "expected-report.md": report}


if __name__ == "__main__":
    build(FIXTURES)
    for file, content in goldens(FIXTURES).items():
        (FIXTURES / file).write_text(content, encoding="utf-8", newline="\n")
