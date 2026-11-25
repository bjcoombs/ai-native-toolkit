"""Tests for the slope chart (issue #675): lib/fleet/fleet_slope.py. The layer bars it shipped with
were retired with the layer framing (#1073). The goldens under ``tests/fixtures/fleet_diagrams/`` (checked in
``test_fleet_diagrams.py``) lock the whole render; these say what a byte comparison cannot.
"""
from __future__ import annotations

import re

import lib.fleet.fleet_diagrams as fd
import lib.fleet.fleet_slope as fs
import lib.fleet.fleet_svg as svg
from lib.fleet.runner import FleetRow
from lib.fleet.fleet_compare import Comparison, GroupChange, LevelState, RowMove, not_comparable
from lib.fleet.fleet_view import read_repos
from lib.scoring.levels import MaturityModel, load_levels
from fleet_diagrams_fixture import FIXTURES, WINDOW, comparison


def _levels() -> MaturityModel:
    return load_levels(FIXTURES / "levels.toml")


def _repos() -> list[FleetRow]:
    return read_repos(FIXTURES)


def _fixture_view() -> fs.SlopeView:
    return fs.slope_view(_levels(), comparison(_levels(), _repos()))


def _state(level: int | None) -> LevelState:
    return {"status": "unknown" if level is None else "level", "level": level}


def _move(row_id: str, then: int | None | str, now: int | None | str,
          cause: str | None = "level") -> RowMove:
    """``"absent"`` for a side where the row is missing; ``None`` for an unknown level."""
    a = None if then == "absent" else _state(then if isinstance(then, int) else None)
    b = None if now == "absent" else _state(now if isinstance(now, int) else None)
    return {"id": row_id, "scope": "team", "then": a, "now": b, "moved": a != b,
            "cause": cause if a != b else None}


def _group(name: str, rows: list[RowMove]) -> GroupChange:
    return {"group": name, "set_changed": False,
            "set": {"added": [], "removed": [], "no_longer_scanned": [], "newly_scanned": [],
                    "no_longer_active": [], "newly_active": []},
            "settings_changed": False, "rows": rows}


def _comparison(groups: list[GroupChange]) -> Comparison:
    base = comparison(_levels(), _repos())
    return {**base, "groups": groups}


# --- slope chart ----------------------------------------------------------------


def test_fixture_view_means_movers_and_causes():
    view = _fixture_view()
    assert (view["earlier"], view["later"]) == ("2026-10-20", "2026-11-20")
    by = {g["group"]: g for g in view["groups"]}
    assert (by["payments"]["then"], round(by["payments"]["now"] or 0, 4)) == (0.0, round(5 / 12, 4))
    mover = by["payments"]["mover"]
    assert mover is not None and (mover["title"], mover["then"], mover["now"]) == (
        "Agent instructions", 0, 1)
    assert by["data"]["mover"] is None  # nothing moved: "no row moved", never a made-up mover
    web = by["web"]["mover"]
    assert web is not None and web["cause"] in ("set", "set_and_level")  # web/admin joined
    assert (by["mobile"]["then"], by["mobile"]["absent"]) == (None, "added")


def test_org_rows_are_left_out_of_the_mean_the_counts_and_the_mover():
    org_move: RowMove = {**_move("org2fa", 0, 1), "scope": "org"}
    view = fs.slope_view(_levels(), _comparison([_group("g", [org_move, _move("instructions", 1, 1)])]))
    group = view["groups"][0]
    assert (group["then"], group["now"], group["paired_rows"], group["rows"]) == (0.5, 0.5, 1, 1)
    assert group["mover"] is None  # the org row moved, but it is not a team row
    only_org = fs.slope_view(_levels(), _comparison([_group("o", [org_move])]))["groups"][0]
    assert (only_org["then"], only_org["now"]) == (None, None)


def test_a_row_unknown_in_one_run_is_left_out_so_the_line_does_not_move():
    levels = _levels()
    view = fs.slope_view(levels, _comparison([
        _group("g", [_move("instructions", 1, 1), _move("agent-use", None, 3)]),
    ]))
    group = view["groups"][0]
    assert (group["then"], group["now"], group["paired_rows"], group["rows"]) == (0.5, 0.5, 1, 2)
    assert group["mover"] is None


def test_the_mover_is_the_largest_share_change_ties_to_the_lower_id():
    view = fs.slope_view(_levels(), _comparison([
        # instructions 0->1 of 2 is +0.5; agent-use 0->1 of 3 is +0.33: instructions moved most
        _group("g", [_move("agent-use", 0, 1), _move("instructions", 0, 1)]),
    ]))
    mover = view["groups"][0]["mover"]
    assert mover is not None and mover["id"] == "instructions"


def test_end_labels_keep_a_minimum_gap_on_each_side():
    labels = fs.end_labels(_fixture_view())
    for side in ("left", "right"):
        ys = sorted(lab["y"] for lab in labels if lab["side"] == side)
        assert len(ys) == 4
        assert all(b - a >= fs.LABEL_GAP for a, b in zip(ys, ys[1:]))
    # the fixture has ties on both sides (payments and data at 0 before, data and web at 0 after)
    left = [lab for lab in labels if lab["side"] == "left"]
    assert len({lab["point"] for lab in left}) < len(left)


def test_unplotted_groups_are_named_on_the_hatched_strip_and_the_star_is_explained():
    text = fs.render_slope(_fixture_view())
    assert "mobile (new since the earlier run)" in text
    assert f'fill="{svg.HATCH_FILL}"' in text
    assert "web: Agent use 2→0 *" in text
    assert "* not a level change alone: the group's measured set of repositories moved too" in text
    assert '-0.58</text>' in text and '+0.42</text>' in text and '>=</text>' in text


def test_a_removed_group_and_a_group_unknown_throughout_are_named_never_zero():
    text = fs.render_slope(fs.slope_view(_levels(), _comparison([
        _group("kept", [_move("instructions", 1, 2)]),
        _group("gone", [_move("instructions", 1, "absent")]),
        _group("dark", [_move("instructions", None, None)]),
    ])))
    assert "gone (not in this run)" in text
    assert "dark (no practice measured in both runs)" in text
    assert "gone 0" not in text and "dark 0" not in text


def test_group_names_are_escaped_and_long_ones_truncated():
    name = "web/<&>" + "x" * 60
    text = fs.render_slope(fs.slope_view(_levels(), _comparison([
        _group(name, [_move("instructions", 0, 2)]),
    ])))
    assert "web/&lt;&amp;&gt;" in text
    assert "x" * 40 not in re.sub(r"<title>.*?</title>", "", text)


def test_slope_skips_say_why():
    levels, repos = _levels(), _repos()
    real = comparison(levels, repos)

    def skipped(model: MaturityModel | None, compared: Comparison | None) -> str | None:
        drawn = fd.build_diagrams(model, repos, WINDOW, comparison=compared)
        return next(d for d in drawn if d["file"] == fs.FILE)["skipped"]

    assert skipped(levels, real) is None
    assert skipped(levels, None) == (
        "skipped: no earlier run compared (no --compare, or no history entry matched this run).")
    refused = not_comparable(None, ["window changed '6mo'->'3mo'"])
    assert skipped(levels, refused) == (
        "skipped: the earlier run is not comparable (window changed '6mo'->'3mo').")
    assert "no maturity model" in (skipped(None, real) or "")
    nothing = _comparison([_group("g", [_move("instructions", None, 1)])])
    assert skipped(levels, nothing) == (
        "skipped: no group has a practice with a known level in both runs.")


def test_a_raising_slope_renderer_is_a_skip_line(monkeypatch):
    def bug(*_args: object) -> str:
        raise KeyError("x")
    monkeypatch.setattr(fs, "render_slope", bug)
    drawn = fd.build_diagrams(_levels(), _repos(), WINDOW,
                              comparison=comparison(_levels(), _repos()))
    entry = next(d for d in drawn if d["file"] == fs.FILE)
    assert entry["skipped"] == "skipped: the renderer raised KeyError."
    assert next(d for d in drawn if d["file"] == "shared-tooling.svg")["svg"] is not None


def _text_boxes(text: str) -> list[svg.Box]:
    boxes = []
    for match in re.finditer(r'<text x="([\d.-]+)" y="([\d.-]+)" font-size="([\d.]+)"[^>]*'
                             r'text-anchor="(\w+)"[^>]*>([^<]*)', text):
        x, y, size, anchor, content = match.groups()
        content = content.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
        boxes.append(svg.text_box(float(x), float(y), content, float(size), anchor))
    return boxes


def test_long_slope_labels_stay_inside_the_canvas():
    long_title = "Layer 9: a very long maturity model row title that keeps going"
    levels = _levels()
    levels = {**levels, "rows": [{**row, "title": long_title} for row in levels["rows"]]}
    text = fs.render_slope(fs.slope_view(levels, _comparison([
        _group("platform-engineering/shared/very-long-subgroup-name", [_move("instructions", 0, 2)]),
        _group("g", [_move("instructions", 2, 0, cause="set")]),
    ])))
    for box in _text_boxes(text):
        assert box.x >= fs.PAD and box.x + box.w <= fs.WIDTH - fs.PAD, box


def test_rows_left_out_of_a_mean_are_named_visibly():
    text = fs.render_slope(_fixture_view())
    assert "Practices left out of a mean, not known in both runs (left out/practices): data\u00a0(1/2)" in text
