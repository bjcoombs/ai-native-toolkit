"""Tests for the fleet diagrams (issue #674): lib/fleet/fleet_svg.py,
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
import lib.fleet.fleet_svg as svg
import lib.fleet.fleet_tooling_map as ftm
from lib.fleet.runner import FleetRow
from lib.fleet.fleet_view import read_repos
from lib.scoring.levels import MaturityModel, load_levels
from fleet_diagrams_fixture import FIXTURES, ROWS, WINDOW, build, comparison, goldens
from svg_cascade import overridden_text_attributes

SVG_NS = "{http://www.w3.org/2000/svg}"


def _tooling(diagrams: list[fd.Diagram]) -> fd.Diagram:
    """The shared-tooling entry, by file: the tag profile strip (#940) leads the list."""
    return next(d for d in diagrams if d["file"] == ftm.FILE)


def _slope(diagrams: list[fd.Diagram]) -> fd.Diagram:
    return next(d for d in diagrams if d["file"] == "slope.svg")


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
    if diagram["file"] == ftm.FILE:  # the diagram that names repositories
        assert "web/design&lt;&amp;&gt;\"sys" in text


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
    from lib.counts_only.content_free import counts_only_scans
    entry = {"kind": "gitlab_project", "source": "s", "id": "gitlab_project:x", "ref": None,
             "ref_variable": True, "path": ".gitlab-ci.yml", "line": 1, "tracked": True}
    block = {"available": True, "ci_includes": [entry], "marketplaces": [],
             "ci_source_ids": ["gitlab_project:x"], "marketplace_ids": []}
    kept = counts_only_scans({"shared_tooling": block})["shared_tooling"]["ci_includes"][0]
    assert (kept["ref"], kept["ref_variable"]) == (None, True)


def test_counts_only_keeps_a_token_ref_and_drops_a_slash_ref_to_pin_unknown():
    from lib.counts_only.content_free import counts_only_scans
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
    monkeypatch.setattr(ftm, "render_tooling_map", bug)
    diagrams = fd.build_diagrams(_levels(), _repos(), WINDOW, comparison=comparison(_levels(), _repos()))
    assert _tooling(diagrams)["skipped"] == "skipped: the renderer raised ValueError."
    assert _slope(diagrams)["svg"] is not None  # the others still draw
    assert "Not drawn: skipped: the renderer raised ValueError." in fd.report_markdown(diagrams)


def test_drawn_skipped_and_failed_diagrams_share_one_heading(monkeypatch):
    drawn = {d["file"]: (d["title"], d["question"]) for d in _drawn()}
    skipped = {d["file"]: (d["title"], d["question"]) for d in fd.build_diagrams(None, [], WINDOW)}
    assert drawn == skipped == fd.HEADINGS
    assert fd.DIAGRAM_FILES == tuple(fd.HEADINGS)

    def bug(*_args: object) -> str:
        raise ValueError("render bug")
    monkeypatch.setattr(ftm, "render_tooling_map", bug)
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
    trend = next(d for d in no_levels if d["file"] == "trend.svg")
    assert trend["svg"] is None and "no maturity model" in (trend["skipped"] or "")
    org_only: MaturityModel = {**_levels(), "rows": [r for r in _levels()["rows"] if r["scope"] == "org"]}
    with_org = fd.build_diagrams(org_only, _repos(), WINDOW, comparison=comparison(_levels(), _repos()))
    assert "no team practices" in (_slope(with_org)["skipped"] or "")
    empty = fd.build_diagrams(_levels(), [], WINDOW)
    by_file = {d["file"]: d["skipped"] for d in empty}
    assert by_file["shared-tooling.svg"] == "skipped: no repositories in the run."
    assert "layer-bars.svg" not in by_file  # retired with the layer framing (#1073)
    assert "quadrant.svg" not in by_file  # dropped: it placed repositories against each other (#1255)
    assert by_file["slope.svg"] == (
        "skipped: no earlier run compared (no --compare, or no history entry matched this run).")
    report = fd.report_markdown(no_levels)
    assert "Not drawn: skipped: the run had no maturity model" in report
    assert "[![Shared tooling](./shared-tooling.svg)](./shared-tooling.svg)" in report


def test_write_diagrams_replaces_stale_files_and_clear_removes_them(tmp_path):
    (tmp_path / "slope.svg").write_text("stale", encoding="utf-8")
    keep = tmp_path / "groups.jsonl"
    keep.write_text("{}", encoding="utf-8")
    written = fd.write_diagrams(tmp_path, fd.build_diagrams(None, list(ROWS), WINDOW))
    assert sorted(p.name for p in written) == ["report.md", "shared-tooling.svg"]
    assert not (tmp_path / "slope.svg").exists()  # skipped this run: the old picture is gone
    fd.clear_diagrams(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["groups.jsonl"]


@pytest.mark.parametrize("retired", ["layer-bars.svg", "quadrant.svg"])
def test_a_retired_diagram_from_an_earlier_run_is_removed(tmp_path, retired):
    """#1073: no per-layer bars; #1255: no quadrant. An earlier run's picture never stays."""
    (tmp_path / retired).write_text("stale", encoding="utf-8")
    fd.write_diagrams(tmp_path, fd.build_diagrams(None, list(ROWS), WINDOW))
    assert not (tmp_path / retired).exists()
    assert retired not in fd.DIAGRAM_FILES and fd.RETIRED_FILES == ("layer-bars.svg", "quadrant.svg")


# --- shared helpers -----------------------------------------------------------


def test_nice_ceiling_and_ticks():
    assert [svg.nice_ceiling(v) for v in (0, 1, 3, 7, 12, 254, 1000)] == [1, 1, 5, 10, 20, 500, 1000]
    scale = svg.LinearScale(0, 100, 10, 110)
    assert scale(50) == 60
    assert scale.ticks(4) == [0, 25, 50, 75, 100]
    assert svg.LinearScale(1, 1, 0, 10)(1) == 0
    assert [svg.tick_label(v) for v in (40.0, 0.25)] == ["40", "0.25"]


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


def test_the_report_has_what_is_working_ahead_of_the_opportunities():
    """#1334: report.md names what is working across the fleet before the opportunities."""
    from lib.fleet.fleet_diagrams import report_markdown
    from lib.fleet.fleet_opportunities_section import opportunities_section
    from test_fleet_opportunities import _data, _row

    data = _data([_row("grp/a", instr=True, ci=True), _row("grp/b", instr=True, ci=False)])
    report = report_markdown([], opportunities=opportunities_section(data, None))
    working = report.index("## What's working across the fleet")
    assert working < report.index("## Opportunities")
    section = report[working:report.index("## Opportunities")]
    assert "- Instructions: 2 of 2 measured repositories at the top level." in section
    assert "- CI: 1 of 2 measured repositories at the top level." in section
