"""Tests for the fleet diagrams (issue #674): lib/fleet/fleet_svg.py, lib/fleet/fleet_quadrant.py,
lib/fleet/fleet_tooling_map.py and lib/fleet/fleet_diagrams.py.

The golden files under ``tests/fixtures/fleet_diagrams/`` lock the whole render; the
semantic tests say what a byte comparison cannot. Regenerate the fixture and goldens with
``uv run --locked python tests/fleet_diagrams_fixture.py`` from ``skills/assess``.
"""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import lib.fleet.fleet_diagrams as fd
import lib.fleet.fleet_quadrant as fq
import lib.fleet.fleet_svg as svg
import lib.fleet.fleet_tooling_map as ftm
from lib.fleet.runner import FleetRow
from lib.fleet.fleet_view import read_repos
from lib.levels import MaturityModel, load_levels
from fleet_diagrams_fixture import FIXTURES, ROWS, WINDOW, build, comparison, goldens
from svg_cascade import overridden_text_attributes

SVG_NS = "{http://www.w3.org/2000/svg}"


def _quad(diagrams: list[fd.Diagram]) -> fd.Diagram:
    """The quadrant entry, by file: the tag profile strip (#940) now leads the list."""
    return next(d for d in diagrams if d["file"] == fq.FILE)


def _levels() -> MaturityModel:
    return load_levels(FIXTURES / "levels.toml")


def _repos() -> list[FleetRow]:
    return read_repos(FIXTURES)


def _drawn() -> list[fd.Diagram]:
    return fd.build_diagrams(_levels(), _repos(), WINDOW,
                             comparison=comparison(_levels(), _repos()))


# --- fixture and goldens ------------------------------------------------------


def test_fixture_matches_a_fresh_build(tmp_path):
    build(tmp_path)
    assert (tmp_path / "repos.jsonl").read_bytes() == (FIXTURES / "repos.jsonl").read_bytes()


@pytest.mark.parametrize("name", sorted(goldens(FIXTURES)))
def test_render_matches_golden(name):
    assert goldens(FIXTURES)[name] == (FIXTURES / name).read_text(encoding="utf-8")


def test_two_renders_are_byte_identical():
    assert _drawn() == _drawn()


@pytest.mark.parametrize("diagram", [d for d in _drawn() if d["svg"] is not None], ids=lambda d: d["file"])
def test_svg_is_self_contained_and_well_formed(diagram):
    text = diagram["svg"]
    assert text is not None
    ET.fromstring(text)  # well-formed: names with <&" are escaped
    assert "<script" not in text and "@import" not in text and "href" not in text
    assert re.findall(r"https?://[^\s\"']+", text) == ["http://www.w3.org/2000/svg"]
    assert "prefers-color-scheme: dark" in text
    assert overridden_text_attributes(text) == []
    if diagram["file"] in (fq.FILE, ftm.FILE):  # the diagrams that name repositories
        assert "web/design&lt;&amp;&gt;\"sys" in text


# --- quadrant -----------------------------------------------------------------


def test_repo_points_measure_commits_and_mean_share_of_top_level():
    points = {p["repo"]: p for p in fq.repo_points(_levels(), _repos())}
    # instructions 1 of 2, agent use 1 of 3 (3 agent commits)
    api = points["payments/api"]
    assert (api["commits"], api["measured_rows"], api["team_rows"]) == (212, 2, 2)
    assert math.isclose(api["maturity"] or 0, (1 / 2 + 1 / 3) / 2)
    # no instruction signal: only agent use (level 0) is measured, so the mean is 0, not unknown
    assert points["data/notebooks"]["measured_rows"] == 1
    assert points["data/notebooks"]["maturity"] == 0.0
    assert points["mobile/android"]["commits"] is None  # failed scan: unknown, never 0
    assert points["mobile/android"]["maturity"] is None
    assert points["mobile/watch"]["commits"] is None  # ai_usage unavailable


def test_unknown_repositories_are_named_on_the_hatched_strip_not_plotted():
    text = _quad(fd.build_diagrams(_levels(), _repos(), WINDOW))["svg"]
    assert text is not None
    assert "mobile/android (level and activity unknown)" in text
    assert "mobile/watch (activity" in text
    assert 'fill="url(#fleet-hatch)"' in text
    circles = [c.find(f"{SVG_NS}title") for c in ET.fromstring(text).iter(f"{SVG_NS}circle")]
    plotted = {t.text.split(":", 1)[0] for t in circles if t is not None and t.text}
    assert "mobile/android" not in plotted and "mobile/watch" not in plotted
    assert "payments/api" in plotted


def test_busy_immature_repositories_take_the_alarm_colour():
    points = fq.repo_points(_levels(), _repos())
    plotted = [p for p in points if p["commits"] is not None and p["maturity"] is not None]
    split = fq.median_commits(plotted)
    risky = sorted(p["repo"] for p in plotted if fq.at_risk(p, split))
    assert risky == ["mobile/ios", "payments/api", "payments/ledger"]
    assert fq.median_commits([]) == 1


def test_in_a_quiet_window_a_repository_with_no_commits_is_never_busy():
    quiet: list[fq.RepoPoint] = [
        {"repo": f"g/r{i}", "commits": c, "maturity": 0.0, "measured_rows": 1, "team_rows": 1}
        for i, c in enumerate((0, 0, 3))]
    split = fq.median_commits(quiet)
    assert split == 1
    assert [fq.at_risk(p, split) for p in quiet] == [False, False, True]


@pytest.mark.parametrize(("busiest", "ticks"), [(0, ["0", "1"]), (1, ["0", "1"]), (2, ["0", "1", "2"]),
                                               (4, ["0", "1", "2", "3", "4", "5"])])
def test_a_quiet_window_has_whole_commit_ticks(busiest, ticks):
    points: list[fq.RepoPoint] = [{"repo": "g/r", "commits": busiest, "maturity": 0.5,
                                   "measured_rows": 1, "team_rows": 1}]
    text = fq.render_quadrant(points, WINDOW)
    # x tick labels sit 18px under the plot, centred
    labels = re.findall(r'y="{:.1f}"[^>]*text-anchor="middle"[^>]*>([^<]*)<'.format(fq.PLOT_T + fq.PLOT_H + 18),
                        text)
    assert labels == ticks
    assert fq.count_ticks(10.0) == 5


def test_quadrant_labels_never_overlap_each_other_points_or_captions():
    plotted, x, y, labels = fq.layout(fq.repo_points(_levels(), _repos()))
    boxes = [label.box for label in labels if label is not None]
    discs = [svg.Box(x(p["commits"] or 0) - 6.5, y(p["maturity"] or 0) - 6.5, 13, 13) for p in plotted]
    captions = [svg.text_box(cx, cy, t, 12.5, a) for cx, cy, t, a, _, _ in fq.CAPTIONS]
    for i, box in enumerate(boxes):
        assert not any(svg.overlaps(box, other) for other in boxes[i + 1:])
        assert not any(svg.overlaps(box, other) for other in [*discs, *captions])
    assert len(boxes) >= len(plotted) - 2  # the crowded fixture still labels nearly every dot


def test_a_label_that_fits_nowhere_is_named_under_the_plot():
    text = _quad(fd.build_diagrams(_levels(), _repos(), WINDOW))["svg"]
    assert text is not None
    _, _, _, labels = fq.layout(fq.repo_points(_levels(), _repos()))
    assert None in labels
    assert "Not labelled (crowded): " in text


# --- shared-tooling map -------------------------------------------------------


def test_tooling_view_splits_users_none_and_unknown():
    view = ftm.tooling_view(_repos())
    assert view["none"] == ["data/notebooks"]
    assert view["unknown"] == ["mobile/android", "mobile/watch"]
    assert view["refs_collected"] is True
    by_repo = {(link["repo"], link["item"]): link["refs"] for link in view["links"]}
    assert by_repo[("payments/api", "gitlab_project:ci/python")] == ["v4"]
    assert by_repo[("mobile/ios", "gitlab_project:ci/mobile")] == []
    ios = next(link for link in view["links"] if link["repo"] == "mobile/ios")
    assert ios["unpinned"] is True and ios["pin_unknown"] is False


def _rows_with(includes: list[dict[str, object]]) -> list[FleetRow]:
    rows: list[FleetRow] = []
    for index, entry in enumerate(includes):
        block = {"available": True, "ci_includes": [entry], "marketplaces": [],
                 "ci_source_ids": [entry["id"]], "marketplace_ids": []}
        rows.append({"fleet_row_version": 2, "repo": f"g/r{index}", "group": "g", "status": "ok",
                     "reason": None, "signals_schema_version": 1, "active": True,
                     "scans": {"shared_tooling": block}})
    return rows


def test_a_dropped_ref_is_pin_unknown_never_in_step():
    base = {"kind": "gitlab_project", "id": "gitlab_project:x", "tracked": True}
    view = ftm.tooling_view(_rows_with([{**base, "ref": "v1"}, {**base, "ref": "v1"}, base,
                                        {**base, "ref": None}]))
    flags = [(link["refs"], link["unpinned"], link["pin_unknown"]) for link in view["links"]]
    assert flags == [(["v1"], False, False), (["v1"], False, False), ([], False, True), ([], True, False)]
    common = ftm.common_refs(view["links"])
    assert not any(ftm.drifted(link, common) for link in view["links"])  # unpinned is not drift
    text = ftm.render_tooling_map(view)
    assert "1 pins unknown (dotted)" in text
    assert 'stroke-dasharray="1 3"><title>g/r2 → gitlab_project:x @ (pin unknown)' in text
    assert "pinned v1 · 1 unpinned" in text


def test_a_variable_ref_is_pin_unknown_never_unpinned_or_drift():
    base = {"kind": "gitlab_project", "id": "gitlab_project:x", "tracked": True}
    variable = {**base, "ref": None, "ref_variable": True}
    view = ftm.tooling_view(_rows_with([{**base, "ref": "v1", "ref_variable": False}, variable]))
    flags = [(link["refs"], link["unpinned"], link["pin_unknown"]) for link in view["links"]]
    assert flags == [(["v1"], False, False), ([], False, True)]
    assert not any(ftm.drifted(link, ftm.common_refs(view["links"])) for link in view["links"])
    text = ftm.render_tooling_map(view)
    assert 'stroke-dasharray="1 3"><title>g/r1 → gitlab_project:x @ (pin unknown)' in text
    assert "1 unpinned" not in text  # a variable pin is not counted as unpinned


def test_a_fleet_pinned_only_by_variables_still_draws_pins_unknown():
    variable = {"kind": "gitlab_project", "id": "gitlab_project:x", "tracked": True, "ref": None,
                "ref_variable": True}
    view = ftm.tooling_view(_rows_with([variable, variable]))
    assert view["refs_collected"] is True
    text = ftm.render_tooling_map(view)
    assert "2 pins unknown (dotted)" in text
    assert "drift not shown" not in text
    assert text.count('stroke-dasharray="1 3"') == 2


def test_counts_only_keeps_ref_variable():
    from lib.counts_only import counts_only_scans
    entry = {"kind": "gitlab_project", "source": "s", "id": "gitlab_project:x", "ref": None,
             "ref_variable": True, "path": ".gitlab-ci.yml", "line": 1, "tracked": True}
    block = {"available": True, "ci_includes": [entry], "marketplaces": [],
             "ci_source_ids": ["gitlab_project:x"], "marketplace_ids": []}
    kept = counts_only_scans({"shared_tooling": block})["shared_tooling"]["ci_includes"][0]
    assert (kept["ref"], kept["ref_variable"]) == (None, True)


def test_counts_only_keeps_a_token_ref_and_drops_a_slash_ref_to_pin_unknown():
    from lib.counts_only import counts_only_scans
    block = {"available": True, "ci_includes": [{"kind": "gitlab_project", "source": "s",
                                                 "id": "gitlab_project:x", "ref": "release/1.2",
                                                 "path": ".gitlab-ci.yml", "line": 1, "tracked": True}],
             "marketplaces": [], "ci_source_ids": ["gitlab_project:x"], "marketplace_ids": []}
    out = counts_only_scans({"shared_tooling": block})
    assert "ref" not in out["shared_tooling"]["ci_includes"][0]
    row: FleetRow = {"fleet_row_version": 2, "repo": "g/r", "group": "g", "status": "ok", "reason": None,
                     "signals_schema_version": 1, "active": True, "scans": out}
    assert ftm.tooling_view([row])["links"][0]["pin_unknown"] is True
    block["ci_includes"][0]["ref"] = "v4.1"
    assert counts_only_scans({"shared_tooling": block})["shared_tooling"]["ci_includes"][0]["ref"] == "v4.1"


def test_a_raising_renderer_is_a_skip_line_naming_the_error(monkeypatch):
    def bug(*_args: object) -> str:
        raise ValueError("render bug")
    monkeypatch.setattr(fq, "render_quadrant", bug)
    diagrams = fd.build_diagrams(_levels(), _repos(), WINDOW)
    assert _quad(diagrams)["skipped"] == "skipped: the renderer raised ValueError."
    assert next(d for d in diagrams if d["file"] == ftm.FILE)["svg"] is not None  # the others still draw
    assert "Not drawn: skipped: the renderer raised ValueError." in fd.report_markdown(diagrams)


def test_drawn_skipped_and_failed_diagrams_share_one_heading(monkeypatch):
    drawn = {d["file"]: (d["title"], d["question"]) for d in _drawn()}
    skipped = {d["file"]: (d["title"], d["question"]) for d in fd.build_diagrams(None, [], WINDOW)}
    assert drawn == skipped == fd.HEADINGS
    assert fd.DIAGRAM_FILES == tuple(fd.HEADINGS)

    def bug(*_args: object) -> str:
        raise ValueError("render bug")
    for module, name in ((fq, "render_quadrant"), (ftm, "render_tooling_map")):
        monkeypatch.setattr(module, name, bug)
    failed = {d["file"]: (d["title"], d["question"]) for d in fd.build_diagrams(_levels(), _repos(), WINDOW)}
    assert failed == fd.HEADINGS


def test_drift_is_a_pin_off_the_fleets_most_common_ref():
    view = ftm.tooling_view(_repos())
    common = ftm.common_refs(view["links"])
    assert common["gitlab_project:ci/python"] == "v4"
    late = sorted((link["repo"], link["item"]) for link in view["links"] if ftm.drifted(link, common))
    assert late == [("data/warehouse", "gitlab_project:ci/python"),
                    ("payments/ledger", "gitlab_project:ci/python"),
                    ("web/design<&>\"sys", "gitlab_project:ci/node")]


def test_common_ref_ties_go_to_the_lexically_greatest():
    links: list[ftm.Link] = [{"repo": "a", "item": "x", "refs": ["v1"]},
                             {"repo": "b", "item": "x", "refs": ["v2"]}]
    assert ftm.common_refs(links) == {"x": "v2"}


def test_without_refs_the_map_says_drift_is_not_shown():
    view = ftm.tooling_view(_repos())
    bare: ftm.ToolingView = {**view, "refs_collected": False,
                             "links": [{**link, "refs": []} for link in view["links"]]}
    text = ftm.render_tooling_map(bare)
    assert "pinned refs not collected in this run: drift not shown" in text
    assert "stroke-dasharray" not in text


def test_an_empty_tooling_view_still_renders():
    text = ftm.render_tooling_map({"links": [], "none": ["a/b"], "unknown": [], "refs_collected": False})
    ET.fromstring(text)
    assert "No repository references a shared CI source or marketplace." in text
    assert "No shared tooling: a/b" in text


def test_item_parts_names_the_kind():
    assert ftm.item_parts("marketplace:platform") == ("platform", "Agent marketplace")
    assert ftm.item_parts("github_reusable_workflow:o/r") == ("o/r", "CI: GitHub reusable workflow")
    assert ftm.item_parts("odd") == ("odd", "odd")


# --- report, skips and files --------------------------------------------------


def test_skipped_diagrams_say_why_in_the_report():
    no_levels = fd.build_diagrams(None, _repos(), WINDOW)
    assert _quad(no_levels)["svg"] is None
    assert "no maturity model" in (_quad(no_levels)["skipped"] or "")
    org_only: MaturityModel = {**_levels(), "rows": [r for r in _levels()["rows"] if r["scope"] == "org"]}
    assert "no team rows" in (_quad(fd.build_diagrams(org_only, _repos(), WINDOW))["skipped"] or "")
    empty = fd.build_diagrams(_levels(), [], WINDOW)
    by_file = {d["file"]: d["skipped"] for d in empty}
    assert by_file["quadrant.svg"] == by_file["shared-tooling.svg"] == "skipped: no repositories in the run."
    assert "layer-bars.svg" not in by_file  # retired with the layer framing (#1073)
    assert by_file["slope.svg"] == (
        "skipped: no earlier run compared (no --compare, or no history entry matched this run).")
    report = fd.report_markdown(no_levels)
    assert "Not drawn: skipped: the run had no maturity model" in report
    assert "[![Shared tooling](./shared-tooling.svg)](./shared-tooling.svg)" in report


def test_write_diagrams_replaces_stale_files_and_clear_removes_them(tmp_path):
    (tmp_path / fq.FILE).write_text("stale", encoding="utf-8")
    keep = tmp_path / "groups.jsonl"
    keep.write_text("{}", encoding="utf-8")
    written = fd.write_diagrams(tmp_path, fd.build_diagrams(None, list(ROWS), WINDOW))
    assert sorted(p.name for p in written) == ["report.md", "shared-tooling.svg"]
    assert not (tmp_path / fq.FILE).exists()  # skipped this run: the old picture is gone
    fd.clear_diagrams(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["groups.jsonl"]


def test_a_retired_layer_bars_file_from_an_earlier_run_is_removed(tmp_path):
    """#1073: the fleet no longer draws per-layer bars; an earlier run's picture never stays."""
    (tmp_path / "layer-bars.svg").write_text("stale", encoding="utf-8")
    fd.write_diagrams(tmp_path, fd.build_diagrams(None, list(ROWS), WINDOW))
    assert not (tmp_path / "layer-bars.svg").exists()
    assert "layer-bars.svg" not in fd.DIAGRAM_FILES and fd.RETIRED_FILES == ("layer-bars.svg",)


# --- shared helpers -----------------------------------------------------------


def test_nice_ceiling_and_ticks():
    assert [svg.nice_ceiling(v) for v in (0, 1, 3, 7, 12, 254, 1000)] == [1, 1, 5, 10, 20, 500, 1000]
    scale = svg.LinearScale(0, 100, 10, 110)
    assert scale(50) == 60
    assert scale.ticks(4) == [0, 25, 50, 75, 100]
    assert svg.LinearScale(1, 1, 0, 10)(1) == 0
    assert [svg.tick_label(v) for v in (40.0, 0.25)] == ["40", "0.25"]


def test_place_labels_falls_back_through_candidates_then_gives_up():
    bounds = svg.Box(0, 0, 400, 200)
    placed = svg.place_labels([(100, 100, "aaaa"), (100, 100, "bbbb"), (100, 100, "cccc"),
                               (100, 100, "dddd"), (100, 100, "eeee"), (100, 100, "ffff"),
                               (100, 100, "gggg")], bounds)
    assert [p is not None for p in placed[:4]] == [True] * 4  # right, right-below, left, left-below
    assert placed[-1] is None  # every candidate taken: the caller names it elsewhere
    assert [p.anchor for p in placed[:4] if p] == ["start", "start", "end", "end"]
    edge = svg.place_labels([(395, 100, "near the right edge")], bounds)[0]
    assert edge is not None and edge.anchor == "end"


def test_spread_vertical_keeps_order_and_minimum_gap():
    ys = [100.0, 104.0, 50.0, 101.0]
    out = svg.spread_vertical(ys, 16)
    assert out == [100.0, 132.0, 50.0, 116.0]
    ordered = sorted(out)
    assert all(b - a >= 16 for a, b in zip(ordered, ordered[1:]))


def test_text_escapes_and_classes():
    element = svg.text(1, 2, "a<b&c", cls="muted", tooltip="t<")
    assert element == ('<text x="1.0" y="2.0" font-size="12" text-anchor="start" class="t-muted">'
                       'a&lt;b&amp;c<title>t&lt;</title></text>')
    box = svg.text_box(10, 20, "abc", 12, "middle")
    assert all(math.isclose(a, b) for a, b in zip((box.x, box.y, box.w, box.h), (-0.5, 8, 21, 14.4)))


def test_theme_style_defines_every_role_for_both_schemes():
    style = svg.theme_style()
    light, dark = style.split("@media (prefers-color-scheme: dark)")
    for role, (day, night) in svg.THEME.items():
        assert f".t-{role},.f-{role}{{fill:{day}}}" in light
        assert f".s-{role}{{stroke:{night}}}" in dark


def test_hatch_pattern_keeps_the_heat_map_bytes():
    assert svg.hatch_pattern("fleet-unknown", "#ffffff", "#8c8c8c") == (
        '<pattern id="fleet-unknown" width="6" height="6" patternUnits="userSpaceOnUse" '
        'patternTransform="rotate(45)"><rect width="6" height="6" fill="#ffffff"/>'
        '<line x1="0" y1="0" x2="0" y2="6" stroke="#8c8c8c" stroke-width="1.6"/></pattern>')


def test_written_files_land_beside_groups_jsonl(tmp_path: Path):
    paths = fd.write_diagrams(tmp_path, _drawn())
    assert all(p.parent == tmp_path for p in paths)
