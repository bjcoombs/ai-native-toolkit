"""The fleet diagrams and the Markdown report that embeds them (issue #674, parent #672).

``build_diagrams`` turns one fleet run's repository rows (and the maturity
model it used) into a list of ``Diagram`` entries, each either an SVG or the
one-line reason it was skipped. ``report_markdown`` renders ``report.md``: the
"What changed and who earned it" section first when given (``lib/fleet/fleet_credit.py``), then one
section per diagram, the SVG embedded with a relative, clickable image link,
or the skip reason in its place. Every ``## `` section opens with a line starting
``In place:`` (``lib/vocabulary.IN_PLACE``, #743) that says what this run already has
before the question and any next step. The report ends with "How this was measured"
(the scans that ran and were skipped, ``lib/measured_appendix.py``, #794).
``write_diagrams`` writes the SVGs and the
report beside ``groups.jsonl``, removing any diagram file a previous run left
that this run did not draw, so a stale picture never sits beside new data;
``clear_diagrams`` removes them all (``fleet.py --no-diagrams``).

The fleet median tag profile strip (#940, ``lib/fleet/fleet_tag_profile.py``) comes first, the
summary view, drawn from ``fleet-data.json``'s ``tag_profile`` (``build_diagrams(...,
tag_profile=)``) and skipped without it; the estate tag heatmap
(``lib/fleet/fleet_tag_heatmap.py``) follows it as the drill-down, from the repositories'
per-tag tallies in the same document (``data=``). The Step funnel and one change away (#941,
``lib/fleet/fleet_step_funnel.py``, ``lib/fleet/fleet_step_lifts.py``) follow, from the same
document's Step fields and ``opportunities.step_lifts``; the Step movement grid
(``lib/fleet/fleet_step_movement.py``) comes last, from the ``--compare`` run (``earlier=``).
All three are skipped without a model with Steps or without ``fleet-data.json``. The slope chart (#675) follows the trend; it reads
the ``--compare`` result (``comparison``) and is skipped without one; the trend
(#676, ``lib/fleet/fleet_trends.py``) reads the run history. A new diagram adds its file,
report heading and question to ``HEADINGS`` (the one source a drawn, skipped or failed diagram
reads them from; ``DIAGRAM_FILES`` follows it) and its entry to ``build_diagrams``. Deterministic: entries
in a fixed order, no date in the report.

The diagram list and `report.md` that `fleet.py` writes beside `groups.jsonl` (#674).
`build_diagrams(levels, repos, window, *, history, comparison)` returns a `Diagram` per picture
(`file`, `title`, `question`, `in_place`: the section's opening `In place:` line (#743), and `svg` or the one-line `skipped` reason: no maturity model, no
team rows, no repositories; for the trend no run history or fewer than 3 comparable runs; for
the slope chart no `--compare`, runs not comparable, or no row known in both runs). `history`
is the trend's runs (`fleet_trends`), `None` when the run is not kept in the history;
`comparison` is the result `_keep_and_compare` wrote. `report_markdown` opens with the
`fleet_coverage.coverage_line` paragraph when given, then the
`fleet_credit` section when given, then the `fleet_opportunities_section` section when
given, then embeds each with a relative clickable image link or says
why it is not drawn, then the `measured_appendix` section when given (`fleet.py` passes it,
built from the ok rows' `scans`). `write_diagrams` first removes every file in `DIAGRAM_FILES` and the
report (`clear_diagrams`, also `fleet.py --no-diagrams`), so a skipped diagram never leaves a
stale picture. `RETIRED_FILES` names a diagram the fleet no longer draws (`layer-bars.svg`,
retired with the layer framing in #1073: the fleet rolls up by truth tag), which `clear_diagrams`
also removes, so a picture from before never sits beside new data. A new diagram (#676) adds its module, its `FILE` with its
report heading and question to `HEADINGS` (one source for a drawn, skipped or failed diagram,
#684; `DIAGRAM_FILES` follows it) and an entry to `build_diagrams`. Golden renders: `tests/fixtures/fleet_diagrams/` (regenerate with
`uv run --locked python tests/fleet_diagrams_fixture.py` from `skills/assess`).
"""
from __future__ import annotations

import os
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, TypedDict

import lib.fleet.fleet_maturity as fleet_maturity
import lib.fleet.fleet_quadrant as fleet_quadrant
import lib.fleet.fleet_slope as fleet_slope
import lib.fleet.fleet_step_funnel as fleet_step_funnel
import lib.fleet.fleet_step_lifts as fleet_step_lifts
import lib.fleet.fleet_step_movement as fleet_step_movement
import lib.fleet.fleet_tag_heatmap as fleet_tag_heatmap
import lib.fleet.fleet_tag_profile as fleet_tag_profile
import lib.fleet.fleet_tooling_map as fleet_tooling_map
import lib.fleet.fleet_trends as fleet_trends
from lib.vocabulary import IN_PLACE
from lib.fleet.runner import FleetRow
from lib.fleet.fleet_compare import Comparison, FleetRun
from lib.fleet.fleet_tag_profile import FleetTagRow
from lib.fleet.fleet_trends import TrendRun
from lib.levels import MaturityModel

REPORT_FILE = "report.md"
# Each diagram's report heading and question, by file: the one source for a drawn
# diagram, a skipped one and one whose renderer raised (#684).
HEADINGS: dict[str, tuple[str, str]] = {
    fleet_tag_profile.FILE: (fleet_tag_profile.TITLE,
                             "Which truth tags hold across the estate, and how far apart are the repositories on each?"),
    fleet_tag_heatmap.FILE: (fleet_tag_heatmap.TITLE, fleet_tag_heatmap.QUESTION),
    fleet_step_funnel.FILE: (fleet_step_funnel.TITLE, fleet_step_funnel.QUESTION),
    fleet_step_lifts.FILE: (fleet_step_lifts.TITLE, fleet_step_lifts.QUESTION),
    fleet_quadrant.FILE: ("Activity and maturity", "Which busy repositories have the most room to grow?"),
    fleet_tooling_map.FILE: ("Shared tooling",
                             "What does a shared template change reach, and which pins differ from the fleet's?"),
    fleet_trends.FILE: ("Group trends", "Is each team improving?"),
    fleet_slope.FILE: ("Before and after", "What moved since the earlier run, and which row moved it?"),
    fleet_step_movement.FILE: (fleet_step_movement.TITLE, fleet_step_movement.QUESTION),
}


DIAGRAM_FILES = tuple(HEADINGS)
# Diagrams no longer drawn, removed with the others so an earlier run's picture never stays:
# the per-layer bars, retired when the fleet moved from layers to truth tags (#1073).
RETIRED_FILES = ("layer-bars.svg",)


class Diagram(TypedDict):
    file: str  # the SVG file name beside groups.jsonl
    title: str
    question: str
    svg: str | None  # None when skipped
    skipped: str | None  # the one-line reason, when skipped
    in_place: str  # the section's opening line: what this run already has (#743)


NO_LEVELS = "skipped: the run had no maturity model (--levels), so no levels."
NO_HISTORY = "skipped: the run is not kept in the run history (--no-history or --full), so no earlier runs."
NO_TEAM_ROWS = "skipped: the maturity model has no team rows to measure maturity by."
NO_REPOS = "skipped: no repositories in the run."
NO_STEPS = "skipped: the maturity model defines no Steps."
NO_DATA = "skipped: the run wrote no fleet-data.json to read the Steps from."


def _step_ready(entry: Diagram, levels: MaturityModel | None, data: Mapping[str, Any] | None) -> bool:
    """True when a Step diagram can draw; otherwise sets the entry's skip line (#941)."""
    if levels is None:
        entry["skipped"] = NO_LEVELS
    elif not levels.get("steps"):
        entry["skipped"] = NO_STEPS
    elif data is None:
        entry["skipped"] = NO_DATA
    return entry["skipped"] is None


def _step_funnel(levels: MaturityModel | None, data: Mapping[str, Any] | None, repos: int) -> Diagram:
    entry = _entry(fleet_step_funnel.FILE, repos)
    if _step_ready(entry, levels, data) and levels is not None and data is not None:
        entry["in_place"] = fleet_step_funnel.in_place(fleet_step_funnel.funnel_view(levels, data))
        entry["svg"] = fleet_step_funnel.render(levels, data)
    return entry


def _step_lifts(levels: MaturityModel | None, data: Mapping[str, Any] | None, repos: int) -> Diagram:
    entry = _entry(fleet_step_lifts.FILE, repos)
    if _step_ready(entry, levels, data) and data is not None:
        entry["in_place"] = fleet_step_lifts.in_place(data)
        entry["svg"] = fleet_step_lifts.render(data)
    return entry


def _step_movement(levels: MaturityModel | None, data: Mapping[str, Any] | None,
                   earlier: FleetRun | str | None, comparison: Comparison | None, repos: int) -> Diagram:
    entry = _entry(fleet_step_movement.FILE, repos)
    if earlier is None:
        entry["in_place"] = f"{IN_PLACE} this run, the baseline a later comparison starts from."
        entry["skipped"] = "skipped: no earlier run compared (no --compare, or no history entry matched this run)."
    elif _step_ready(entry, levels, data) and levels is not None and data is not None:
        moves = fleet_step_movement.step_moves(levels, earlier, data,
                                               comparison["reasons"] if comparison else ())
        entry["in_place"] = fleet_step_movement.in_place(moves)
        entry["svg"] = fleet_step_movement.render(moves)
    return entry


def _repos(count: int) -> str:
    return f"{count} repository" if count == 1 else f"{count} repositories"


def _entry(file: str, repos: int = 0) -> Diagram:
    """A diagram entry with the file's heading, not yet drawn or skipped; its opening line says
    what the run holds until a drawn diagram states more."""
    title, question = HEADINGS[file]
    return {"file": file, "title": title, "question": question, "svg": None, "skipped": None,
            "in_place": f"{IN_PLACE} {_repos(repos)} in this run, scanned with no model call."}


def _has_team_rows(levels: MaturityModel) -> bool:
    return any(row["scope"] == fleet_maturity.SCOPE for row in levels["rows"])


def _quadrant(levels: MaturityModel | None, repos: Sequence[FleetRow], window: str) -> Diagram:
    entry = _entry(fleet_quadrant.FILE, len(repos))
    if levels is None:
        entry["skipped"] = NO_LEVELS
    elif not _has_team_rows(levels):
        entry["skipped"] = NO_TEAM_ROWS
    elif not repos:
        entry["skipped"] = NO_REPOS
    else:
        points = fleet_quadrant.repo_points(levels, repos)
        measured = [p for p in points if p["maturity"] is not None]
        half = sum(1 for p in measured if (p["maturity"] or 0.0) >= 0.5)
        entry["in_place"] = (f"{IN_PLACE} {_repos(len(measured))} with a measured level, {half} of them "
                             "at or above half the top level.")
        entry["svg"] = fleet_quadrant.render_quadrant(points, window)
    return entry


def _tag_profile(levels: MaturityModel | None, profile: Sequence[FleetTagRow] | None, repos: int) -> Diagram:
    entry = _entry(fleet_tag_profile.FILE, repos)
    if levels is None:
        entry["skipped"] = NO_LEVELS
    elif profile is None:
        entry["skipped"] = "skipped: the run wrote no per-tag tally (fleet-data.json tag_profile)."
    elif not any(row["repos"] for row in profile):
        entry["skipped"] = "skipped: no repository in the run has a per-tag tally (none was scanned)."
    else:
        from lib.assess_diagrams_tags import tag_whys  # lazy: the check manifest loads once
        entry["in_place"] = fleet_tag_profile.in_place(profile)
        entry["svg"] = fleet_tag_profile.render(profile, tag_whys())
    return entry


def _tag_heatmap(levels: MaturityModel | None, data: Mapping[str, Any] | None, repos: int) -> Diagram:
    entry = _entry(fleet_tag_heatmap.FILE, repos)
    if levels is None:
        entry["skipped"] = NO_LEVELS
    elif data is None:
        entry["skipped"] = "skipped: the run wrote no fleet-data.json to read the per-tag tallies from."
    elif not any(r.get("tags") is not None for r in data.get("repos") or []):
        entry["skipped"] = "skipped: no repository in the run has a per-tag tally (none was scanned)."
    else:
        rows = fleet_tag_heatmap.heatmap_rows(data)
        entry["in_place"] = fleet_tag_heatmap.in_place(rows)
        entry["svg"] = fleet_tag_heatmap.render(rows, fleet_tag_heatmap.uncarried(data))
    return entry


def _tooling(repos: Sequence[FleetRow]) -> Diagram:
    entry = _entry(fleet_tooling_map.FILE, len(repos))
    if not repos:
        entry["skipped"] = NO_REPOS
    else:
        view = fleet_tooling_map.tooling_view(repos)
        sharing = len({link["repo"] for link in view["links"]})
        items = len({link["item"] for link in view["links"]})
        entry["in_place"] = (f"{IN_PLACE} {_repos(sharing)} share {items} CI "
                             f"{'source or marketplace' if items == 1 else 'sources and marketplaces'}.")
        entry["svg"] = fleet_tooling_map.render_tooling_map(view)
    return entry


def _slope(levels: MaturityModel | None, comparison: Comparison | None, repos: int) -> Diagram:
    entry = _entry(fleet_slope.FILE, repos)
    if comparison is None:
        entry["in_place"] = f"{IN_PLACE} this run, the baseline a later comparison starts from."
        entry["skipped"] = ("skipped: no earlier run compared (no --compare, or no history entry "
                            "matched this run).")
    elif not comparison["comparable"]:
        entry["skipped"] = ("skipped: the earlier run is not comparable ("
                            + "; ".join(comparison["reasons"]) + ").")
    elif levels is None:
        entry["skipped"] = NO_LEVELS
    elif not _has_team_rows(levels):
        entry["skipped"] = NO_TEAM_ROWS
    else:
        view = fleet_slope.slope_view(levels, comparison)
        paired = len(fleet_slope.plotted(view))
        entry["in_place"] = (f"{IN_PLACE} {paired} {'group' if paired == 1 else 'groups'} with a known "
                             "level in both runs.")
        if not paired:
            entry["skipped"] = "skipped: no group has a team row with a known level in both runs."
        else:
            entry["svg"] = fleet_slope.render_slope(view)
    return entry


def _trend(levels: MaturityModel | None, history: Sequence[TrendRun] | None, repos: int) -> Diagram:
    entry = _entry(fleet_trends.FILE, repos)
    if levels is None:
        entry["skipped"] = NO_LEVELS
        return entry
    if not _has_team_rows(levels):
        entry["skipped"] = NO_TEAM_ROWS
        return entry
    if history is None:
        entry["skipped"] = NO_HISTORY
        return entry
    shown = fleet_trends.share_runs(history)
    entry["in_place"] = (f"{IN_PLACE} {len(shown)} comparable dated "
                         f"{'run' if len(shown) == 1 else 'runs'} in the history.")
    if len(shown) < fleet_trends.MIN_RUNS:
        hidden = fleet_trends.hidden_note(history)
        entry["skipped"] = (f"skipped: {len(shown)} comparable dated run(s), the trend needs at least "
                            f"{fleet_trends.MIN_RUNS} (same window, scans and output mode, with a maturity model"
                            + (f"; {hidden}" if hidden else "") + ").")
    else:
        entry["svg"] = fleet_trends.render_trend(history)
    return entry


def _guarded(build: Callable[[], Diagram], file: str, repos: int) -> Diagram:
    """One diagram; a renderer that raises becomes a skip line naming the error class, so one
    broken picture never costs the others, the report or the run."""
    try:
        return build()
    except Exception as e:  # noqa: BLE001 - a diagram never fails a completed run
        entry = _entry(file, repos)
        entry["skipped"] = f"skipped: the renderer raised {type(e).__name__}."
        return entry


def build_diagrams(levels: MaturityModel | None, repos: Sequence[FleetRow], window: str, *,
                   history: Sequence[TrendRun] | None = None,
                   comparison: Comparison | None = None,
                   tag_profile: Sequence[FleetTagRow] | None = None,
                   data: Mapping[str, Any] | None = None,
                   earlier: FleetRun | str | None = None) -> list[Diagram]:
    """Every diagram for one run, in report order; never raises for a diagram's sake.

    ``data`` is this run's ``fleet-data.json`` (#940): its ``tag_profile`` (the per-tag roll-up,
    drawn first as the fleet's summary view, unless ``tag_profile`` is given) and its
    repositories' per-tag tallies (the tag heatmap, second, the drill-down). None when the run
    wrote none.

    ``history`` is the trend's runs, oldest first and this run last
    (``fleet_trends.history_runs`` and ``series_runs``); None when no history was read.
    ``comparison`` is this run's ``--compare`` result, None without ``--compare``; ``earlier``
    is the run it compared against (or the reason it could not be read), for the Step
    movement grid (#941).
    """
    count = len(repos)
    profile = tag_profile if tag_profile is not None else (data or {}).get("tag_profile")
    return [
        _guarded(lambda: _tag_profile(levels, profile, count), fleet_tag_profile.FILE, count),
        _guarded(lambda: _tag_heatmap(levels, data, count), fleet_tag_heatmap.FILE, count),
        _guarded(lambda: _step_funnel(levels, data, count), fleet_step_funnel.FILE, count),
        _guarded(lambda: _step_lifts(levels, data, count), fleet_step_lifts.FILE, count),
        _guarded(lambda: _quadrant(levels, repos, window), fleet_quadrant.FILE, count),
        _guarded(lambda: _tooling(repos), fleet_tooling_map.FILE, count),
        _guarded(lambda: _trend(levels, history, count), fleet_trends.FILE, count),
        _guarded(lambda: _slope(levels, comparison, count), fleet_slope.FILE, count),
        _guarded(lambda: _step_movement(levels, data, earlier, comparison, count), fleet_step_movement.FILE,
                 count),
    ]


def report_markdown(diagrams: Sequence[Diagram], credit: str | None = None,
                    coverage: str | None = None, opportunities: str | None = None,
                    measured: str | None = None) -> str:
    """``report.md``: the coverage line when given (``lib/fleet/fleet_coverage.py``), the credit
    section when given (``lib/fleet/fleet_credit.py``), the opportunities section when given
    (``lib/fleet/fleet_opportunities_section.py``), then each diagram embedded with a relative
    link, or why it was skipped, then the "How this was measured" section when given
    (``lib/measured_appendix.py``)."""
    lines = ["# Fleet diagrams", ""]
    if coverage is not None:
        lines += [coverage, ""]
    lines += ["Drawn from this run's output with no model call; unknown is hatched and named, "
              "never drawn as zero.", ""]
    for section in (credit, opportunities):
        if section is not None:
            lines += [section.rstrip("\n"), ""]
    for diagram in diagrams:
        lines += [f"## {diagram['title']}", "", diagram["in_place"], "", diagram["question"], ""]
        if diagram["svg"] is not None:
            lines.append(f"[![{diagram['title']}](./{diagram['file']})](./{diagram['file']})")
        else:
            lines.append(f"Not drawn: {diagram['skipped']}")
        lines.append("")
    if measured is not None:
        lines += [measured.rstrip("\n"), ""]
    return "\n".join(lines)


def clear_diagrams(out_dir: Path) -> None:
    """Remove the report and every diagram file a run writes (regular files only)."""
    for name in (REPORT_FILE, *DIAGRAM_FILES, *RETIRED_FILES):
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


def write_diagrams(out_dir: Path, diagrams: Sequence[Diagram], credit: str | None = None,
                   coverage: str | None = None, opportunities: str | None = None,
                   measured: str | None = None) -> list[Path]:
    """Write the drawn SVGs and ``report.md`` (with ``coverage``, ``credit`` and
    ``opportunities`` first and ``measured`` last) under ``out_dir``; returns the paths written."""
    clear_diagrams(out_dir)
    written = [_write(out_dir / d["file"], d["svg"]) for d in diagrams if d["svg"] is not None]
    written.append(_write(out_dir / REPORT_FILE,
                          report_markdown(diagrams, credit, coverage, opportunities, measured)))
    return written
