"""``fleet-view.html`` (#946): the Practice shape panel (#1368, part 3 of #1314), split from
``tests/test_fleet_page.py``, whose Node runner and golden parser it reuses.
"""
from __future__ import annotations

import re
from typing import Any

import pytest

import fleet_data_fixture as fdf
import fleet_page_fixture as fx
from test_fleet_page import _parts, _root_page


# --- practice shape (#1368, part 3 of #1314) ---------------------------------------------


def _shape(page: str) -> str:
    start = page.index("<h2>Practice shape</h2>")
    end = page.find("<h2>Fleet pictures</h2>")
    return page[start:] if end < 0 else page[start:end]


@pytest.mark.parametrize("name", sorted(fx.VARIANTS))
def test_the_practice_shape_panel_draws_one_radar_per_unit_and_the_table_fallback(name: str) -> None:
    """The root page draws one chart per ownership unit, each its own ``<svg>`` holding one unit's
    outline (never overlaid), units by label, then a table of met/measured, practice by unit."""
    _, bundle = _parts(fx.goldens()[f"expected-{name}.html"])
    shape = _shape(_root_page(bundle))
    keys = sorted(u["key"] for u in bundle["views"][0]["units"])
    charts = re.findall(r'<svg class="radar"[^>]*data-unit="([^"]+)">(.*?)</svg>', shape, re.S)
    assert [k for k, _ in charts] == keys
    for key, svg in charts:
        assert set(re.findall(r"<title>([^,<]+), ", svg)) == {key}
        assert [t for t in re.findall(r"<title>(\d+)%</title>", svg)] == ["25", "50", "75", "100"]
    table = re.search(r'<table class="radar-table">(.*?)</table>', shape, re.S)
    assert table is not None
    assert re.findall(r"<th>([^<]+)</th>", table.group(1)) == keys
    titles = [bundle["rows"][i]["title"] for i in bundle["radar"]["axes"]]
    body = table.group(1).split("<tbody>")[1]
    assert re.findall(r'<tr><th class="repo">([^<]+)</th>', body) == titles
    root = next(u for u in bundle["views"][0]["units"] if u["parent"] is None)
    assert f"<td>{root['practice'][0]['met']}/{root['practice'][0]['measured']}</td>" in table.group(1)
    if name != "full":
        for leak in fx.NAMES:
            assert leak not in shape, leak


def test_an_unmeasured_practice_breaks_the_outline_with_no_point_at_zero() -> None:
    """``grp/other`` has no measured Instruction docs repository: its spoke is dashed and labelled
    not measured, it has no point, no closed outline, and the table cell reads not measured."""
    _, bundle = _parts(fx.goldens()["expected-full.html"])
    shape = _shape(_root_page(bundle))
    svg = re.search(r'data-unit="grp/other">(.*?)</svg>', shape, re.S)
    assert svg is not None
    assert '<tspan x="200.0" dy="-11">Instruction docs</tspan><tspan x="200.0" dy="11">(not measured)</tspan>' in svg.group(1)
    assert 'class="spoke gap"' in svg.group(1)
    assert "<polygon" not in svg.group(1)
    assert re.findall(r"<title>(grp/other, [^<]+)</title>", svg.group(1)) == [
        "grp/other, CI gate: 0/1 measured repositories at the top level"]  # never counted at top (#1474)
    assert '<td class="unknown">not measured</td>' in shape
    closed = re.search(r'data-unit="grp/sub">(.*?)</svg>', shape, re.S)
    assert closed is not None and closed.group(1).count("<polygon") == 1


def test_each_point_s_title_names_unit_practice_and_met_of_measured() -> None:
    _, bundle = _parts(fx.goldens()["expected-full.html"])
    shape = _shape(_root_page(bundle))
    # 0: the fixture's conditions are listed, never counted, so none is held at its top (#1474)
    assert "<title>grp, Instruction docs: 0/2 measured repositories at the top level</title>" in shape
    assert "<title>grp, CI gate: 0/3 measured repositories at the top level</title>" in shape


def test_a_practice_measured_in_under_a_fifth_of_the_fleet_is_not_a_spoke() -> None:
    from lib.fleet.fleet_page_data import RADAR_MIN_SHARE, page_bundle, radar_axes
    data: dict[str, Any] = dict(fx.run(fx.DATES[-1], fx.VARIANTS["counts-only"]))
    rows = {"docs": 0, "ci": 1}
    assert RADAR_MIN_SHARE == 20
    assert radar_axes(data, rows) == [0, 1]  # docs 2 of 4 measured, ci 3 of 4
    assert radar_axes(data, rows, 60) == [1] and radar_axes(data, rows, 80) == []
    # a share exactly at the floor is kept: docs 2 of 4 at 50, ci 3 of 4 at 75
    assert radar_axes(data, rows, 50) == [0, 1] and radar_axes(data, rows, 51) == [1]
    assert radar_axes(data, rows, 75) == [1] and radar_axes(data, rows, 76) == []
    assert radar_axes({"repos": []}, rows) == []
    for repo in data["repos"]:  # leave docs measured in 1 of 4 repositories, then 0 of 4
        for cell in repo["rows"]:
            if cell["row"] == "docs" and repo["key"] != "grp/sub/proj1":
                cell["status"], cell["level"] = "unknown", None
    assert radar_axes(data, rows) == [0, 1]  # 25% still reaches the 20% floor
    assert radar_axes(data, rows, 25) == [0, 1] and radar_axes(data, rows, 26) == [1]  # 1 of 4 is 25%
    for cell in data["repos"][2]["rows"]:
        cell["status"] = "unknown"
    assert radar_axes(data, rows) == [1]
    bundle = dict(page_bundle(data, None, fdf.model()))
    assert bundle["radar"] == {"min_share": 20, "axes": [1]}
    shape = _shape(_root_page(bundle))
    assert "Instruction docs" not in shape and "CI gate" in shape


def test_no_spoke_draws_no_chart_and_says_why() -> None:
    _, bundle = _parts(fx.goldens()["expected-counts-only.html"])
    bundle["radar"]["axes"] = []
    shape = _shape(_root_page(bundle))
    assert '<svg class="radar"' not in shape and "radar-table" not in shape
    assert "No practice is measured in enough of the fleet's repositories to draw." in shape


def _six_spokes(measured: list[int], title: str = "Practice") -> dict[str, Any]:
    """The counts-only bundle cut to six spokes and its root unit's counts set from ``measured``."""
    _, bundle = _parts(fx.goldens()["expected-counts-only.html"])
    bundle["rows"] = [{"title": f"{title} {i}", "top_level": 1} for i in range(6)]
    bundle["radar"]["axes"] = list(range(6))
    root = next(u for u in bundle["views"][0]["units"] if u["parent"] is None)
    root["practice"] = [{"row": i, "met": 0, "measured": m} for i, m in enumerate(measured)]
    return bundle


def _points(svg: str) -> list[tuple[float, float]]:
    return [(float(x), float(y)) for x, y in re.findall(r'class="point" cx="([^"]+)" cy="([^"]+)"', svg)]


def test_unmeasured_spokes_split_the_outline_into_runs_that_never_cross_a_gap() -> None:
    """Spokes 2 and 5 of six unmeasured: two runs, {0, 1} and {3, 4}, each joining adjacent measured
    spokes only. A measured practice with nothing met is a real 0, drawn at the centre."""
    bundle = _six_spokes([2, 2, 0, 2, 2, 0])
    root = next(u for u in bundle["views"][0]["units"] if u["parent"] is None)
    root["practice"] = [{"row": i, "met": [2, 1, 0, 0, 2, 0][i], "measured": m}
                        for i, m in enumerate([2, 2, 0, 2, 2, 0])]
    svg = re.search(r'data-unit="org 1">(.*?)</svg>', _shape(_root_page(bundle)), re.S)
    assert svg is not None
    runs = re.findall(r'<polyline class="outline" points="([^"]+)"></polyline>', svg.group(1))
    assert "<polygon" not in svg.group(1) and len(runs) == 2
    points = _points(svg.group(1))
    assert len(points) == 4  # the two gap spokes have none
    assert [tuple(float(v) for v in xy.split(",")) for run in runs for xy in run.split()] == [
        points[2], points[3], points[0], points[1]]  # the run after the first gap, then the wrap to spoke 0
    assert (200.0, 150.0) in points  # spoke 3: 0 of 2 met, a measured 0
    assert svg.group(1).count('class="spoke gap"') == 2


def test_long_practice_titles_wrap_inside_the_chart() -> None:
    bundle = _six_spokes([1] * 6, "Cross-agent instruction consistency and coverage gates")
    svg = re.search(r'data-unit="org 1">(.*?)</svg>', _shape(_root_page(bundle)), re.S)
    assert svg is not None
    lines = re.findall(r"<tspan[^>]*>([^<]+)</tspan>", svg.group(1))
    assert lines and max(len(line) for line in lines) <= 16
    assert " ".join(lines[:4]) == "Cross-agent instruction consistency and coverage gates 0"
    for x, anchor, first in re.findall(r'<text x="([^"]+)" y="[^"]+" text-anchor="(\w+)">(.*?)</text>', svg.group(1)):
        width = 6 * max(len(t) for t in re.findall(r">([^<]+)</tspan>", first))
        left = float(x) - (width if anchor == "end" else width / 2 if anchor == "middle" else 0)
        assert 0 <= left and left + width <= 400, (x, anchor)


def test_eighteen_spokes_are_numbered_titled_and_keyed_to_the_table() -> None:
    """The 18 team rows of ``default.toml``: each spoke is labelled by its number, the full title is
    in the spoke's ``<title>``, the table lists the practices under the same numbers, and no two
    labels' boxes (6 units a character, 11 a line) overlap."""
    _, bundle = _parts(fx.goldens()["expected-counts-only.html"])
    bundle["rows"] = [{"title": f"Cross-agent practice number {i}", "top_level": 1} for i in range(18)]
    bundle["radar"]["axes"] = list(range(18))
    root = next(u for u in bundle["views"][0]["units"] if u["parent"] is None)
    root["practice"] = [{"row": i, "met": 1, "measured": 0 if i == 4 else 2} for i in range(18)]
    shape = _shape(_root_page(bundle))
    svg = re.search(r'data-unit="org 1">(.*?)</svg>', shape, re.S)
    assert svg is not None
    numbers = [str(i) for i in range(1, 19)]
    labels = re.findall(r'<text x="([^"]+)" y="([^"]+)" text-anchor="(\w+)"[^>]*><tspan[^>]*>([^<]+)</tspan></text>',
                        svg.group(1))
    assert [t for *_, t in labels] == numbers
    assert re.findall(r'<line class="spoke[^"]*"[^>]*><title>([^<]+)</title></line>', svg.group(1)) == [
        f"{i + 1}. Cross-agent practice number {i}" + (" (not measured)" if i == 4 else "") for i in range(18)]
    boxes = []
    for x, y, anchor, text in labels:
        width = 6 * len(text)
        left = float(x) - (width if anchor == "end" else width / 2 if anchor == "middle" else 0)
        boxes.append((left, float(y) - 10, left + width, float(y) + 1))
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1], (a, b)
    table = re.search(r'<table class="radar-table">(.*?)</table>', shape, re.S)
    assert table is not None
    assert re.findall(r'<tr><th class="repo">([^<]+)</th>', table.group(1).split("<tbody>")[1]) == [
        f"{i + 1}. Cross-agent practice number {i}" for i in range(18)]
    assert "labelled by its number" in shape


def test_eight_spokes_keep_their_titles_beside_the_spoke() -> None:
    _, bundle = _parts(fx.goldens()["expected-counts-only.html"])
    bundle["rows"] = [{"title": f"Practice {i}", "top_level": 1} for i in range(8)]
    bundle["radar"]["axes"] = list(range(8))
    shape = _shape(_root_page(bundle))
    assert "<tspan" in shape and ">Practice 7</tspan>" in shape and "</title></line>" not in shape
    assert '<th class="repo">Practice 0</th>' in shape and "labelled by its number" not in shape
