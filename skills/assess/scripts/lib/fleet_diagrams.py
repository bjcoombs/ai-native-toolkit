"""The fleet diagrams and the Markdown report that embeds them (issue #674, parent #672).

``build_diagrams`` turns one fleet run's repository rows (and the maturity
model it used) into a list of ``Diagram`` entries, each either an SVG or the
one-line reason it was skipped. ``report_markdown`` renders ``report.md``: one
section per diagram, the SVG embedded with a relative, clickable image link,
or the skip reason in its place. ``write_diagrams`` writes the SVGs and the
report beside ``groups.jsonl``, removing any diagram file a previous run left
that this run did not draw, so a stale picture never sits beside new data;
``clear_diagrams`` removes them all (``fleet.py --no-diagrams``).

The slope chart and the layer bars (#675) follow the trend; the slope chart reads
the ``--compare`` result (``comparison``) and is skipped without one; the trend
(#676, ``lib/fleet_trends.py``) reads the run history. A new diagram adds its file,
report heading and question to ``HEADINGS`` (the one source a drawn, skipped or failed diagram
reads them from; ``DIAGRAM_FILES`` follows it) and its entry to ``build_diagrams``. Deterministic: entries
in a fixed order, no date in the report.
"""
from __future__ import annotations

import os
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TypedDict

from lib import fleet_layer_bars, fleet_quadrant, fleet_slope, fleet_tooling_map, fleet_trends
from lib.fleet import FleetRow
from lib.fleet_compare import Comparison
from lib.fleet_trends import TrendRun
from lib.rubric import Rubric

REPORT_FILE = "report.md"
# Each diagram's report heading and question, by file: the one source for a drawn
# diagram, a skipped one and one whose renderer raised (#684).
HEADINGS: dict[str, tuple[str, str]] = {
    fleet_quadrant.FILE: ("Activity and maturity", "Which busy repositories are least mature?"),
    fleet_tooling_map.FILE: ("Shared tooling",
                             "What breaks if a shared template changes, and who drifted off it?"),
    fleet_trends.FILE: ("Group trends", "Is each team improving?"),
    fleet_slope.FILE: ("Before and after", "What moved since the earlier run, and which row moved it?"),
    fleet_layer_bars.FILE: ("Maturity by layer", "Which layer is the org-wide weak spot?"),
}


DIAGRAM_FILES = tuple(HEADINGS)


class Diagram(TypedDict):
    file: str  # the SVG file name beside groups.jsonl
    title: str
    question: str
    svg: str | None  # None when skipped
    skipped: str | None  # the one-line reason, when skipped


NO_LEVELS = "skipped: the run had no maturity model (--rubric), so no levels."
NO_HISTORY = "skipped: the run is not kept in the run history (--no-history or --full), so no earlier runs."
NO_TEAM_ROWS = "skipped: the maturity model has no team rows to measure a repository by."
NO_REPOS = "skipped: no repositories in the run."


def _entry(file: str) -> Diagram:
    """A diagram entry with the file's heading, not yet drawn or skipped."""
    title, question = HEADINGS[file]
    return {"file": file, "title": title, "question": question, "svg": None, "skipped": None}


def _quadrant(levels: Rubric | None, repos: Sequence[FleetRow], window: str) -> Diagram:
    entry = _entry(fleet_quadrant.FILE)
    if levels is None:
        entry["skipped"] = NO_LEVELS
    elif not any(row["scope"] == "team" for row in levels["rows"]):
        entry["skipped"] = NO_TEAM_ROWS
    elif not repos:
        entry["skipped"] = NO_REPOS
    else:
        entry["svg"] = fleet_quadrant.render_quadrant(fleet_quadrant.repo_points(levels, repos), window)
    return entry


def _tooling(repos: Sequence[FleetRow]) -> Diagram:
    entry = _entry(fleet_tooling_map.FILE)
    if not repos:
        entry["skipped"] = NO_REPOS
    else:
        entry["svg"] = fleet_tooling_map.render_tooling_map(fleet_tooling_map.tooling_view(repos))
    return entry


def _slope(levels: Rubric | None, comparison: Comparison | None) -> Diagram:
    entry = _entry(fleet_slope.FILE)
    if comparison is None:
        entry["skipped"] = ("skipped: no earlier run compared (no --compare, or no history entry "
                            "matched this run).")
    elif not comparison["comparable"]:
        entry["skipped"] = ("skipped: the earlier run is not comparable ("
                            + "; ".join(comparison["reasons"]) + ").")
    elif levels is None:
        entry["skipped"] = NO_LEVELS
    else:
        view = fleet_slope.slope_view(levels, comparison)
        if not fleet_slope.plotted(view):
            entry["skipped"] = "skipped: no group has a row with a known level in both runs."
        else:
            entry["svg"] = fleet_slope.render_slope(view)
    return entry


def _layer_bars(levels: Rubric | None, repos: Sequence[FleetRow]) -> Diagram:
    entry = _entry(fleet_layer_bars.FILE)
    if levels is None:
        entry["skipped"] = NO_LEVELS
    elif not any(row["scope"] == "team" for row in levels["rows"]):
        entry["skipped"] = NO_TEAM_ROWS
    elif not repos:
        entry["skipped"] = NO_REPOS
    else:
        org = [row["title"] for row in levels["rows"] if row["scope"] == "org"]
        entry["svg"] = fleet_layer_bars.render_layer_bars(
            fleet_layer_bars.layer_bars(levels, repos), org)
    return entry


def _trend(levels: Rubric | None, history: Sequence[TrendRun] | None) -> Diagram:
    entry = _entry(fleet_trends.FILE)
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


def _guarded(build: Callable[[], Diagram], file: str) -> Diagram:
    """One diagram; a renderer that raises becomes a skip line naming the error class, so one
    broken picture never costs the others, the report or the run."""
    try:
        return build()
    except Exception as e:  # noqa: BLE001 - a diagram never fails a completed run
        entry = _entry(file)
        entry["skipped"] = f"skipped: drawing failed ({type(e).__name__})."
        return entry


def build_diagrams(levels: Rubric | None, repos: Sequence[FleetRow], window: str, *,
                   history: Sequence[TrendRun] | None = None,
                   comparison: Comparison | None = None) -> list[Diagram]:
    """Every diagram for one run, in report order; never raises for a diagram's sake.

    ``history`` is the trend's runs, oldest first and this run last
    (``fleet_trends.history_runs`` and ``series_runs``); None when no history was read.
    ``comparison`` is this run's ``--compare`` result, None without ``--compare``.
    """
    return [
        _guarded(lambda: _quadrant(levels, repos, window), fleet_quadrant.FILE),
        _guarded(lambda: _tooling(repos), fleet_tooling_map.FILE),
        _guarded(lambda: _trend(levels, history), fleet_trends.FILE),
        _guarded(lambda: _slope(levels, comparison), fleet_slope.FILE),
        _guarded(lambda: _layer_bars(levels, repos), fleet_layer_bars.FILE),
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
