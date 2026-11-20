"""The fleet diagrams and the Markdown report that embeds them (issue #674, parent #672).

``build_diagrams`` turns one fleet run's repository rows (and the maturity
model it used) into a list of ``Diagram`` entries, each either an SVG or the
one-line reason it was skipped. ``report_markdown`` renders ``report.md``: one
section per diagram, the SVG embedded with a relative, clickable image link,
or the skip reason in its place. ``write_diagrams`` writes the SVGs and the
report beside ``groups.jsonl``, removing any diagram file a previous run left
that this run did not draw, so a stale picture never sits beside new data;
``clear_diagrams`` removes them all (``fleet.py --no-diagrams``).

A later diagram (slope, layer bars: #675; trend: #676, ``lib/fleet_trends.py``) adds its file name to
``DIAGRAM_FILES`` and its entry to ``build_diagrams``. Deterministic: entries
in a fixed order, no date in the report.
"""
from __future__ import annotations

import os
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TypedDict

from lib import fleet_quadrant, fleet_tooling_map, fleet_trends
from lib.fleet import FleetRow
from lib.fleet_trends import TrendRun
from lib.rubric import Rubric

REPORT_FILE = "report.md"
DIAGRAM_FILES = (fleet_quadrant.FILE, fleet_tooling_map.FILE, fleet_trends.FILE)


class Diagram(TypedDict):
    file: str  # the SVG file name beside groups.jsonl
    title: str
    question: str
    svg: str | None  # None when skipped
    skipped: str | None  # the one-line reason, when skipped


TREND_TITLE = "Group trends"
TREND_QUESTION = "Is each team improving?"
NO_LEVELS = "skipped: the run had no maturity model (--rubric), so no levels."
NO_HISTORY = "skipped: the run is not kept in the run history (--no-history or --full), so no earlier runs."


def _quadrant(levels: Rubric | None, repos: Sequence[FleetRow], window: str) -> Diagram:
    entry: Diagram = {"file": fleet_quadrant.FILE, "title": "Activity and maturity",
                      "question": "Which busy repositories are least mature?",
                      "svg": None, "skipped": None}
    if levels is None:
        entry["skipped"] = NO_LEVELS
    elif not any(row["scope"] == "team" for row in levels["rows"]):
        entry["skipped"] = "skipped: the maturity model has no team rows to measure a repository by."
    elif not repos:
        entry["skipped"] = "skipped: no repositories in the run."
    else:
        entry["svg"] = fleet_quadrant.render_quadrant(fleet_quadrant.repo_points(levels, repos), window)
    return entry


def _tooling(repos: Sequence[FleetRow]) -> Diagram:
    entry: Diagram = {"file": fleet_tooling_map.FILE, "title": "Shared tooling",
                      "question": "What breaks if a shared template changes, and who drifted off it?",
                      "svg": None, "skipped": None}
    if not repos:
        entry["skipped"] = "skipped: no repositories in the run."
    else:
        entry["svg"] = fleet_tooling_map.render_tooling_map(fleet_tooling_map.tooling_view(repos))
    return entry


def _trend(levels: Rubric | None, history: Sequence[TrendRun] | None) -> Diagram:
    entry: Diagram = {"file": fleet_trends.FILE, "title": TREND_TITLE, "question": TREND_QUESTION,
                      "svg": None, "skipped": None}
    if levels is None:
        entry["skipped"] = NO_LEVELS
    elif history is None:
        entry["skipped"] = NO_HISTORY
    elif len(history) < fleet_trends.MIN_RUNS:
        entry["skipped"] = (f"skipped: {len(history)} comparable dated run(s), the trend needs at least "
                            f"{fleet_trends.MIN_RUNS} (same window, scans and output mode, with a maturity model).")
    else:
        entry["svg"] = fleet_trends.render_trend(history)
    return entry


def _guarded(build: Callable[[], Diagram], file: str, title: str, question: str) -> Diagram:
    """One diagram; a renderer that raises becomes a skip line naming the error class, so one
    broken picture never costs the others, the report or the run."""
    try:
        return build()
    except Exception as e:  # noqa: BLE001 - a diagram never fails a completed run
        return {"file": file, "title": title, "question": question, "svg": None,
                "skipped": f"skipped: drawing failed ({type(e).__name__})."}


def build_diagrams(levels: Rubric | None, repos: Sequence[FleetRow], window: str, *,
                   history: Sequence[TrendRun] | None = None) -> list[Diagram]:
    """Every diagram for one run, in report order; never raises for a diagram's sake.

    ``history`` is the trend's runs, oldest first and this run last
    (``fleet_trends.history_runs`` and ``series_runs``); None when no history was read.
    """
    return [
        _guarded(lambda: _quadrant(levels, repos, window), fleet_quadrant.FILE,
                 "Activity and maturity", "Which busy repositories are least mature?"),
        _guarded(lambda: _tooling(repos), fleet_tooling_map.FILE, "Shared tooling",
                 "What breaks if a shared template changes, and who drifted off it?"),
        _guarded(lambda: _trend(levels, history), fleet_trends.FILE, TREND_TITLE, TREND_QUESTION),
    ]


def report_markdown(diagrams: Sequence[Diagram]) -> str:
    """``report.md``: each diagram embedded with a relative link, or why it was skipped."""
    lines = ["# Fleet diagrams", "",
             "Drawn from this run's output with no model call; unknown is hatched and named, "
             "never drawn as zero.", ""]
    for diagram in diagrams:
        lines += [f"## {diagram['title']}", "", diagram["question"], ""]
        if diagram["svg"] is not None:
            lines.append(f"[![{diagram['title']}](./{diagram['file']})](./{diagram['file']})")
        else:
            lines.append(f"Not drawn: {diagram['skipped']}")
        lines.append("")
    return "\n".join(lines)


def clear_diagrams(out_dir: Path) -> None:
    """Remove the report and every diagram file a run writes (regular files only)."""
    for name in (REPORT_FILE, *DIAGRAM_FILES):
        path = out_dir / name
        if path.is_file() and not path.is_symlink():
            path.unlink()


def _write(path: Path, content: str) -> Path:
    """Replace ``path`` atomically (temp file and ``os.replace``), never writing through a symlink."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def write_diagrams(out_dir: Path, diagrams: Sequence[Diagram]) -> list[Path]:
    """Write the drawn SVGs and ``report.md`` under ``out_dir``; returns the paths written."""
    clear_diagrams(out_dir)
    written = [_write(out_dir / d["file"], d["svg"]) for d in diagrams if d["svg"] is not None]
    written.append(_write(out_dir / REPORT_FILE, report_markdown(diagrams)))
    return written
