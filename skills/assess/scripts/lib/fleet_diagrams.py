"""The fleet diagrams and the Markdown report that embeds them (issue #674, parent #672).

``build_diagrams`` turns one fleet run's repository rows (and the maturity
model it used) into a list of ``Diagram`` entries, each either an SVG or the
one-line reason it was skipped. ``report_markdown`` renders ``report.md``: one
section per diagram, the SVG embedded with a relative, clickable image link,
or the skip reason in its place. ``write_diagrams`` writes the SVGs and the
report beside ``groups.jsonl``, removing any diagram file a previous run left
that this run did not draw, so a stale picture never sits beside new data;
``clear_diagrams`` removes them all (``fleet.py --no-diagrams``).

A later diagram (slope, layer bars, trend: #675, #676) adds its file name to
``DIAGRAM_FILES`` and its entry to ``build_diagrams``. Deterministic: entries
in a fixed order, no date in the report.
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TypedDict

from lib import fleet_quadrant, fleet_tooling_map
from lib.fleet import FleetRow
from lib.rubric import Rubric

REPORT_FILE = "report.md"
DIAGRAM_FILES = (fleet_quadrant.FILE, fleet_tooling_map.FILE)


class Diagram(TypedDict):
    file: str  # the SVG file name beside groups.jsonl
    title: str
    question: str
    svg: str | None  # None when skipped
    skipped: str | None  # the one-line reason, when skipped


def _quadrant(levels: Rubric | None, repos: Sequence[FleetRow], window: str) -> Diagram:
    entry: Diagram = {"file": fleet_quadrant.FILE, "title": "Activity and maturity",
                      "question": "Which busy repositories are least mature?",
                      "svg": None, "skipped": None}
    if levels is None:
        entry["skipped"] = "skipped: the run had no maturity model (--rubric), so no levels."
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


def build_diagrams(levels: Rubric | None, repos: Sequence[FleetRow], window: str) -> list[Diagram]:
    """Every diagram for one run, in report order."""
    return [_quadrant(levels, repos, window), _tooling(repos)]


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


def write_diagrams(out_dir: Path, diagrams: Sequence[Diagram]) -> list[Path]:
    """Write the drawn SVGs and ``report.md`` under ``out_dir``; returns the paths written."""
    clear_diagrams(out_dir)
    written: list[Path] = []
    for diagram in diagrams:
        if diagram["svg"] is not None:
            path = out_dir / diagram["file"]
            path.write_text(diagram["svg"], encoding="utf-8", newline="\n")
            written.append(path)
    report = out_dir / REPORT_FILE
    report.write_text(report_markdown(diagrams), encoding="utf-8", newline="\n")
    written.append(report)
    return written
