"""The practice radar (``lib/fleet/fleet_radar.py``, #1367, part 2 of #1314).

Each test reads the SVG's static markup only: the ``<g data-radar>`` groups, the points'
``data-row`` and ``<title>`` and the outline's ``data-outline`` and path data.
"""
from __future__ import annotations

import copy
import re
from typing import Any

import pytest

import lib.fleet.fleet_data as fd
import lib.fleet.fleet_radar as fr
from fleet_data_fixture import ROWS, SALT, build, model
from lib.fleet.fleet_cli_args import parse_args
from lib.fleet.fleet_diagrams import build_diagrams, report_markdown
from lib.vocabulary import banned_hits, row_hits
from svg_cascade import overridden_text_attributes


def _model(*rows: str) -> list[dict[str, Any]]:
    return [{"id": "org2fa", "scope": "org", "title": "Two-factor", "top_level": 1},
            *({"id": r, "scope": "team", "title": r.title() + " practice", "top_level": 2} for r in rows)]


def _cell(row: str, level: int | None) -> dict[str, Any]:
    return {"row": row, "status": "unknown" if level is None else "level", "level": level, "top_level": 2}


def _repos(total: int, measured: dict[str, int]) -> list[dict[str, Any]]:
    """``total`` repositories; the first ``measured[row]`` have a known level (1 of 2) on ``row``."""
    return [{"key": f"acme/repo-{i}",
             "rows": [_cell(row, 1 if i < count else None) for row, count in measured.items()]}
            for i in range(total)]


def _unit(key: str, name: str | None, repos: int, counts: dict[str, tuple[int, int]],
          parent: str | None = None) -> dict[str, Any]:
    return {"key": key, "name": name, "repos": repos, "parent": parent,
            "levels": [{"row": row, "met": met, "measured": measured, "repos": repos, "lowest_level": None}
                       for row, (met, measured) in counts.items()]}


def _data(units: list[dict[str, Any]], *, rows: tuple[str, ...] = ("docs", "ci", "tests"),
          total: int = 10, measured: dict[str, int] | None = None, full: bool = True) -> dict[str, Any]:
    return {"run": {"counts_only": not full, "paths_hashed": False}, "model": _model(*rows),
            "repos": _repos(total, measured or {r: total for r in rows}),
            "views": [{"view": "ownership", "units": units}]}


def _radars(svg: str) -> list[str]:
    return re.findall(r'<g data-radar="\d+">.*?</g>', svg, re.S)


def _points(radar: str) -> list[tuple[str, str, str]]:
    """``(cx, cy, row)`` of each drawn point."""
    return re.findall(r'<circle cx="([\d.]+)" cy="([\d.]+)" r="3.5" class="f-accent" data-row="([^"]+)"', radar)


def _centre(radar: str) -> tuple[str, str]:
    m = re.search(r'<circle cx="([\d.]+)" cy="([\d.]+)" r="72.0"', radar)
    assert m is not None
    return m.group(1), m.group(2)


def test_an_unmeasured_row_breaks_the_outline_with_no_point_at_0() -> None:
    data = _data([_unit("ownership/a", "Payments", 4, {"docs": (2, 4), "ci": (0, 0), "tests": (1, 2)})])
    [radar] = _radars(fr.render(fr.radar_view(data)))
    rows = [row for _, _, row in _points(radar)]
    assert rows == ["docs", "tests"]  # no point for ci at all
    centre = _centre(radar)
    assert all((x, y) != centre for x, y, _ in _points(radar))
    assert 'data-row="ci" data-cell="not_measured"' in radar
    assert 'data-outline="broken"' in radar and 'data-outline="closed"' not in radar
    path = re.search(r'<path d="([^"]+)"', radar)
    assert path is not None and "Z" not in path.group(1)
    # docs -> ci and ci -> tests are not drawn; tests -> docs (the wrap) is the one segment
    assert path.group(1).count("M") == 1


def test_a_real_0_is_a_point_at_the_centre_and_a_fully_measured_outline_closes() -> None:
    data = _data([_unit("ownership/a", "Payments", 4, {"docs": (0, 4), "ci": (4, 4), "tests": (2, 4)})])
    [radar] = _radars(fr.render(fr.radar_view(data)))
    points = _points(radar)
    assert [row for _, _, row in points] == ["docs", "ci", "tests"]
    assert (points[0][0], points[0][1]) == _centre(radar)
    assert 'data-outline="closed"' in radar and "not_measured" not in radar


def test_a_row_under_20_percent_measured_is_dropped_by_default_and_kept_when_lowered() -> None:
    data = _data([_unit("ownership/a", "Payments", 10, {"docs": (5, 10), "ci": (1, 1), "tests": (3, 9)})],
                 measured={"docs": 10, "ci": 1, "tests": 9})
    default = fr.radar_view(data)
    assert [a["row"] for a in default["axes"]] == ["docs", "tests"]
    assert [a["row"] for a in default["dropped"]] == ["ci"]
    svg = fr.render(default)
    assert 'data-row="ci"' not in svg
    assert "left out: Ci practice." in fr.caption(default)
    lowered = fr.radar_view(data, 10)
    assert [a["row"] for a in lowered["axes"]] == ["docs", "ci", "tests"] and lowered["dropped"] == []
    assert 'data-row="ci" data-cell="share"' in fr.render(lowered)
    # through the diagram list, as fleet.py --radar-min-measured passes it
    by_default = next(d for d in build_diagrams(model(), [], "6mo", data=data) if d["file"] == fr.FILE)
    lowered_entry = next(d for d in build_diagrams(model(), [], "6mo", data=data, radar_min_share=10)
                         if d["file"] == fr.FILE)
    assert "Ci practice" not in by_default.get("table", "")
    assert "| Ci practice | 1/1 |" in lowered_entry.get("table", "")


def test_two_units_render_two_separate_radars() -> None:
    data = _data([_unit("ownership/a", "Payments", 4, {"docs": (2, 4), "ci": (4, 4), "tests": (1, 2)}),
                  _unit("ownership/b", "Search", 3, {"docs": (3, 3), "ci": (0, 2), "tests": (1, 3)})])
    radars = _radars(fr.render(fr.radar_view(data)))
    assert len(radars) == 2
    assert [r.count("data-outline=") for r in radars] == [1, 1]  # one shape per radar, never overlaid
    assert ">Payments<" in radars[0] and "Search" not in radars[0]
    assert ">Search<" in radars[1] and "Payments" not in radars[1]
    assert _centre(radars[0]) != _centre(radars[1])


def test_units_keep_the_document_order_and_are_never_ranked() -> None:
    data = _data([_unit("ownership/a", "Low", 2, {"docs": (0, 2), "ci": (0, 2), "tests": (0, 2)}),
                  _unit("ownership/b", "High", 2, {"docs": (2, 2), "ci": (2, 2), "tests": (2, 2)}),
                  _unit("ownership/c", "Empty", 0, {})])
    view = fr.radar_view(data)
    assert [u["label"] for u in view["units"]] == ["Low", "High"]  # a unit with no repository is left out


def test_each_point_title_holds_the_unit_the_row_and_met_over_measured() -> None:
    data = _data([_unit("ownership/a", "Payments", 4, {"docs": (2, 4), "ci": (0, 0), "tests": (1, 2)})])
    [radar] = _radars(fr.render(fr.radar_view(data)))
    titles = re.findall(r'data-row="(\w+)" data-cell="(\w+)"><title>([^<]+)</title>', radar)
    assert titles == [
        ("docs", "share", "Payments, Docs practice: 2/4 measured at the top level (4 repositories)"),
        ("ci", "not_measured", "Payments, Ci practice: not measured"),
        ("tests", "share", "Payments, Tests practice: 1/2 measured at the top level (4 repositories)"),
    ]


def test_rings_sit_at_25_50_75_and_100_percent() -> None:
    data = _data([_unit("ownership/a", "Payments", 4, {"docs": (2, 4), "ci": (4, 4), "tests": (1, 2)})])
    [radar] = _radars(fr.render(fr.radar_view(data)))
    assert re.findall(r'r="([\d.]+)" fill="none" class="s-rule" stroke-width="1" data-ring="(\d+%)"', radar) == [
        ("18.0", "25%"), ("36.0", "50%"), ("54.0", "75%"), ("72.0", "100%")]


def test_the_table_is_practice_by_unit_with_met_over_measured() -> None:
    data = _data([_unit("ownership/a", "Pay|ments", 4, {"docs": (2, 4), "ci": (0, 0), "tests": (1, 2)}),
                  _unit("ownership/b", "Search", 3, {"docs": (3, 3), "ci": (0, 2), "tests": (1, 3)})])
    assert fr.table(fr.radar_view(data)).splitlines() == [
        "Met/measured by practice for 2 units, the radar's numbers:", "",
        "| Practice | Pay\\|ments | Search |", "|---|---|---|",
        "| Docs practice | 2/4 | 3/3 |", "| Ci practice | not measured | 0/2 |", "| Tests practice | 1/2 | 1/3 |"]


def test_in_place_counts_strengths_first() -> None:
    data = _data([_unit("ownership/a", "Payments", 4, {"docs": (4, 4), "ci": (0, 0), "tests": (1, 2)}),
                  _unit("ownership/b", "Search", 3, {"docs": (3, 3), "ci": (2, 2), "tests": (1, 3)})])
    data["repos"][0]["rows"][0] = _cell("docs", 2)
    data["repos"][1]["rows"][2] = _cell("tests", 2)
    assert fr.in_place(fr.radar_view(data)) == (
        "In place: 2 of the run's 10 repositories meet the top level on at least one of the 3 "
        "practices drawn, each counted once, over 2 units.")


def test_a_parent_and_its_child_both_draw_but_no_total_counts_a_repository_twice() -> None:
    """The thread on #1438: a parent unit's counts already hold its child's repositories."""
    child = _unit("ownership/grp/sub", "Sub", 2, {"docs": (2, 2), "ci": (2, 2), "tests": (2, 2)},
                  parent="ownership/grp")
    parent = _unit("ownership/grp", "Grp", 2, {"docs": (2, 2), "ci": (2, 2), "tests": (2, 2)})
    data = _data([parent, child], total=2)
    for repo in data["repos"]:
        repo["rows"] = [_cell(r, 2) for r in ("docs", "ci", "tests")]
    view = fr.radar_view(data)
    assert [(u["label"], u["depth"], u["parent"]) for u in view["units"]] == [("Grp", 0, None), ("Sub", 1, "Grp")]
    radars = _radars(fr.render(view))
    assert len(radars) == 2
    assert "ownership view, 2 repositories" in radars[0]
    assert "ownership view, in Grp, 2 repositories" in radars[1]
    # the same two repositories sit in both units: counted once, not once per level of nesting
    assert fr.in_place(view).startswith("In place: 2 of the run's 2 repositories ")
    assert "Units nest: a unit counts every repository below it" in fr.caption(view)
    assert "| Practice | Grp | Sub (in Grp) |" in fr.table(view)
    flat = fr.radar_view(_data([_unit("ownership/a", "A", 2, {"docs": (1, 2)})], total=2))
    assert "Units nest" not in fr.caption(flat)


def test_the_axis_rule_is_the_page_rule_in_integers() -> None:
    """``known * 100 >= min_share * repos`` on cells with status ``level``, as the page's spokes
    (#1368): 3 of 15 is exactly 20% and kept."""
    data = _data([_unit("ownership/a", "A", 15, {"docs": (1, 3)})], rows=("docs",), total=15,
                 measured={"docs": 3})
    assert [a["row"] for a in fr.radar_view(data)["axes"]] == ["docs"]
    data["repos"][0]["rows"][0]["status"] = "floor"  # a level without status "level" is not counted
    assert [a["row"] for a in fr.radar_view(data)["dropped"]] == ["docs"]
    assert fr.radar_view(_data([], total=0))["axes"] == []


def test_counts_only_radar_names_no_repository() -> None:
    for privacy in (fd.Privacy(), fd.Privacy(salt=SALT)):
        data = dict(build(privacy))
        view = fr.radar_view(data, 0)
        assert view["units"]
        out = "\n".join([fr.render(view), fr.table(view), fr.in_place(view), fr.caption(view)])
        for row in ROWS:
            assert row["repo"] not in out
            assert row["repo"].rsplit("/", 1)[-1] not in out
        assert "Checkout" not in out  # a unit name is written only under --full
        if privacy.salt is None:
            for unit in (u for v in data["views"] for u in v["units"]):
                assert unit["key"] not in out
            assert [u["label"] for u in view["units"]][:2] == ["ownership unit 1", "ownership unit 2"]
        else:
            assert all(u["label"].startswith("h:") for u in view["units"])


def test_full_output_labels_each_unit_by_name() -> None:
    view = fr.radar_view(dict(build(fd.Privacy(full=True))), 0)
    assert [u["label"] for u in view["units"]] == ["grp", "other", "sub", "Checkout"]
    assert [u["view"] for u in view["units"]] == ["ownership"] * 3 + ["system"]
    assert [(u["depth"], u["parent"]) for u in view["units"]] == [(0, None), (1, "grp"), (1, "grp"), (0, None)]


def test_the_real_fixture_reads_met_and_measured_from_the_units() -> None:
    view = fr.radar_view(dict(build(fd.Privacy(full=True))))
    assert [a["row"] for a in view["axes"]] == ["docs", "ci"]  # 2 of 4 and 3 of 4 measured
    other = next(u for u in view["units"] if u["label"] == "other")
    assert other["points"] == [None, {"met": 1, "measured": 1, "repos": 1}]


def test_two_renders_are_byte_identical_the_cascade_is_clean_and_the_words_pass() -> None:
    data = dict(build(fd.Privacy(full=True)))
    svg = fr.render(fr.radar_view(copy.deepcopy(data)))
    assert svg == fr.render(fr.radar_view(copy.deepcopy(data)))
    assert overridden_text_attributes(svg) == []
    view = fr.radar_view(data)
    text = "\n".join([svg, fr.in_place(view), fr.caption(view), fr.table(view), fr.QUESTION, fr.TITLE])
    assert banned_hits(text) == [] and row_hits(text) == []
    assert "<script" not in svg and "http://www.w3.org/2000/svg" in svg


def test_the_report_prints_the_table_under_the_picture_and_skips_with_a_reason() -> None:
    data = dict(build(fd.Privacy(full=True)))
    drawn = {d["file"]: d for d in build_diagrams(model(), [], "6mo", data=data)}
    radar = drawn[fr.FILE]
    assert radar["svg"] is not None and radar["in_place"].startswith("In place: ")
    report = report_markdown([radar])
    section = report.split(f"## {fr.TITLE}", 1)[1]
    assert section.index("(./radar.svg)") < section.index(
        "| Practice | grp | other (in grp) | sub (in grp) | Checkout |")

    def skipped(**kwargs: Any) -> str:
        entry = next(d for d in build_diagrams(**kwargs) if d["file"] == fr.FILE)
        assert entry["svg"] is None and "table" not in entry
        return entry["skipped"] or ""

    assert "no maturity model" in skipped(levels=None, repos=[], window="6mo", data=data)
    assert "no fleet-data.json" in skipped(levels=model(), repos=[], window="6mo")
    assert "no ownership or system unit" in skipped(levels=model(), repos=[], window="6mo",
                                                    data={**data, "views": []})
    assert "--radar-min-measured" in skipped(levels=model(), repos=[], window="6mo", data=data,
                                            radar_min_share=90)
    org_only = {**model(), "rows": [r for r in model()["rows"] if r["scope"] == "org"]}
    assert "no team practices" in skipped(levels=org_only, repos=[], window="6mo", data=data)


def test_the_cli_takes_a_percent_from_0_to_100(tmp_path: Any) -> None:
    assert parse_args([str(tmp_path), "--out", str(tmp_path / "o")]).radar_min_measured == fr.RADAR_MIN_SHARE == 20
    assert parse_args([str(tmp_path), "--out", str(tmp_path / "o"), "--radar-min-measured", "0"]).radar_min_measured == 0
    with pytest.raises(SystemExit):
        parse_args([str(tmp_path), "--out", str(tmp_path / "o"), "--radar-min-measured", "101"])


def test_the_radar_and_the_page_share_one_axis_rule_and_default() -> None:
    import lib.fleet.fleet_page_data as fpd
    assert fr.RADAR_MIN_SHARE is fpd.RADAR_MIN_SHARE
    assert fr.keeps_axis is fpd.keeps_axis and fr.measured_counts is fpd.measured_counts
    assert fpd.keeps_axis(1, 5, 20) and not fpd.keeps_axis(1, 5, 21) and not fpd.keeps_axis(0, 0, 0)
