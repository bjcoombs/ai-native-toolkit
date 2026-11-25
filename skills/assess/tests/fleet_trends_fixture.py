"""Builds the fixture run history under ``tests/fixtures/fleet_trends/`` (issue #676).

``history/acme/<date>[.<n>]/`` holds synthetic ``run.json`` and ``groups.jsonl`` files;
``CURRENT`` is this run, held in memory as ``fleet.py`` holds it. ``test_fleet_trends.py``
fails when the committed files differ from a fresh build. Regenerate them and the golden
render with ``uv run --locked python tests/fleet_trends_fixture.py`` from ``skills/assess``,
then review the diff.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib.fleet.fleet_diagrams import build_diagrams, report_markdown
from lib.fleet.fleet_groups import FLEET_GROUP_VERSION, GroupRow, RowLevel
from lib.fleet.fleet_stamp import FLEET_STAMP_VERSION, FleetStamp
from lib.scoring.levels import MaturityModel
from lib.fleet.fleet_trends import TrendRun, history_runs, render_trend, series_runs, trend_run

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "fleet_trends"
ORG = FIXTURES / "history" / "acme"
LONG = "platform/infrastructure-and-shared-services"
REPOS = frozenset({"payments/api", "platform/gateway"})


def stamp(scan_date: str, version: str | None, window: str = "6mo") -> FleetStamp:
    return {"fleet_stamp_version": FLEET_STAMP_VERSION, "toolkit_commit": None,
            "plugin_version": "2.0.0", "scan_date": scan_date, "window": window,
            "levels_version": version, "counts_only": True, "paths_hashed": False, "scans": None,
            "repositories": {"selected": 2, "scanned_ok": 2, "failed": 0, "qualifying": 2}}


def group(name: str, levels: tuple[int | None, ...], top: int | None = None,
          org: int | None = None) -> GroupRow:
    """A group row: team rows at ``levels``, each with ``top_level`` ``top`` unless None (an
    entry written before #684), plus an org row at ``org`` when given (left out of the mean)."""
    rows: list[RowLevel] = [
        {"id": f"row{i}", "scope": "team", "status": "unknown" if level is None else "level",
         "level": level, "aggregation": "min", "missing_signals": [], "unknown_repos": []}
        for i, level in enumerate(levels)]
    if org is not None:
        rows.append({"id": "org0", "scope": "org", "status": "level", "level": org,
                     "aggregation": None, "missing_signals": [], "unknown_repos": []})
    if top is not None:
        for row in rows:
            row["top_level"] = top
    return {"fleet_group_version": FLEET_GROUP_VERSION, "group": name, "repos": 2, "scanned": 2,
            "failed": 0, "shared_tooling_repos": 0, "distinct_ci_sources": None,
            "distinct_marketplaces": None, "settings_scope": None, "settings": None,
            "levels_version": None, "levels": rows}


# This run's maturity model (levels-2): two team rows of 3 levels and an org row. Entries
# before 2026-10-20 were written before groups.jsonl carried top_level (#684): the levels-2
# one (2026-09-20) takes its top levels from this model, the levels-1 ones plot as plain means.
TOP = 3
LEVELS: MaturityModel = {"version": "levels-2", "rows": [
    {"id": f"row{i}", "title": f"Row {i}", "scope": "team",
     "levels": [{"level": n, "criteria": []} for n in range(1, TOP + 1)]} for i in range(2)] + [
    {"id": "org0", "title": "Org 0", "scope": "org", "levels": [{"level": 1, "criteria": []}]}]}
# (entry name, maturity-model version, window, {group: row levels}, top_level or None)
ENTRIES: tuple[tuple[str, str | None, str, dict[str, tuple[int | None, ...]], int | None], ...] = (
    ("2026-05-20", "levels-1", "3mo", {"payments": (3, 3)}, None),  # another window: not comparable
    ("2026-06-20", "levels-1", "6mo", {"payments": (0, 1), "platform": (2, 2), "data": (1, 1),
                                       "web": (1, 1), "mobile": (1, 1), LONG: (2, 1)}, None),
    ("2026-07-20", "levels-1", "6mo", {"payments": (1, 1), "platform": (2, 2), "data": (1, 2),
                                       "web": (1, 2), "mobile": (1, 1), LONG: (1, 2)}, None),
    ("2026-08-20", "levels-1", "6mo", {"payments": (1, 1), "platform": (2, 3), "data": (None, None),
                                       "web": (2, 2), "mobile": (1, 0), LONG: (2, 2)}, None),
    ("2026-09-20", "levels-2", "6mo", {"payments": (1, 2), "platform": (2, 3), "data": (2, 1),
                                       "web": (2, 2), "mobile": (0, 1), LONG: (1, 2)}, None),
    ("2026-10-20", "levels-2", "6mo", {"payments": (1, 2), "platform": (3, 2), "data": (2, 2),
                                       "web": (2, 2), LONG: (2, 2)}, TOP),
)
CURRENT_STAMP = stamp("2026-11-20", "levels-2")
CURRENT_GROUPS = [group(name, levels, TOP, org=0) for name, levels in sorted({
    "payments": (2, 2), "platform": (3, 2), "data": (2, 2), "web": (2, 3), "mobile": (0, 1),
    LONG: (3, 2)}.items())]


def build(root: Path) -> None:
    """Write the history entries under ``root/history/acme`` plus a malformed one."""
    org = root / "history" / "acme"
    for name, version, window, groups, top in ENTRIES:
        entry = org / name
        entry.mkdir(parents=True, exist_ok=True)
        (entry / "run.json").write_text(json.dumps(stamp(name, version, window), sort_keys=True) + "\n",
                                         encoding="utf-8", newline="\n")
        lines = [json.dumps(group(g, levels, top), sort_keys=True) for g, levels in sorted(groups.items())]
        (entry / "groups.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    broken = org / "2026-04-20"
    broken.mkdir(parents=True, exist_ok=True)
    (broken / "run.json").write_text("{not json\n", encoding="utf-8", newline="\n")


def runs(root: Path) -> list[TrendRun]:
    """The trend's runs: the history under ``root``, then ``CURRENT``."""
    current = trend_run(CURRENT_STAMP, CURRENT_GROUPS)
    assert current is not None
    return series_runs(history_runs(root / "history" / "acme", CURRENT_STAMP, REPOS, LEVELS), current)


def goldens(root: Path) -> dict[str, str]:
    """The trend render and a report embedding it (the other diagrams skip: no repositories)."""
    report = report_markdown(build_diagrams(LEVELS, [], "6mo", history=runs(root)))
    return {"expected-trend.svg": render_trend(runs(root)), "expected-report.md": report}


if __name__ == "__main__":
    build(FIXTURES)
    for file, content in goldens(FIXTURES).items():
        (FIXTURES / file).write_text(content, encoding="utf-8", newline="\n")
