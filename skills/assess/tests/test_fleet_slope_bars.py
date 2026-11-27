"""Tests for the slope chart (issues #675, #806: lowest Step): lib/fleet/fleet_slope.py. The layer bars it
shipped with were retired with the layer framing (#1073). The goldens under ``tests/fixtures/fleet_diagrams/``
(checked in ``test_fleet_diagrams.py``) lock the whole render; these say what a byte comparison cannot.
"""
from __future__ import annotations

import re

import lib.fleet.fleet_diagrams as fd
import lib.fleet.fleet_slope as fs
import lib.fleet.fleet_svg as svg
from lib.fleet.runner import FLEET_ROW_VERSION, FleetRow
from lib.fleet.fleet_compare import Comparison, FleetRun, not_comparable
from lib.fleet.fleet_view import read_repos
from lib.scoring.levels import MaturityModel, load_levels
from fleet_diagrams_fixture import FIXTURES, WINDOW, comparison, earlier_run, steps_of, stepped

Steps = dict[str, tuple[int | None, ...]]  # group -> each repository's Step; None: not measured


def _levels() -> MaturityModel:
    return stepped(load_levels(FIXTURES / "levels.toml"))


def _repos() -> list[FleetRow]:
    return read_repos(FIXTURES)


def _fixture_view() -> fs.SlopeView:
    levels, repos = _levels(), _repos()
    return fs.slope_view(comparison(levels, repos), earlier_run(levels), repos, steps_of(levels, repos))


def _rows(groups: Steps) -> tuple[list[FleetRow], dict[str, int | None]]:
    rows: list[FleetRow] = []
    steps: dict[str, int | None] = {}
    for group, values in sorted(groups.items()):
        for i, step in enumerate(values):
            repo = f"{group}/r{i}"
            rows.append({"fleet_row_version": FLEET_ROW_VERSION, "repo": repo, "group": group,  # type: ignore[typeddict-item]  # only the keys the slope reads
                         "status": "ok"})
            steps[repo] = step
    return rows, steps


def _view(then: Steps, now: Steps, set_changed: frozenset[str] = frozenset()) -> fs.SlopeView:
    base = comparison(_levels(), _repos())
    groups = [{**base["groups"][0], "group": g, "set_changed": g in set_changed} for g in sorted({*then, *now})]
    compared: Comparison = {**base, "groups": groups}  # type: ignore[typeddict-item]  # a GroupChange per group
    then_rows, then_steps = _rows(then)
    now_rows, now_steps = _rows(now)
    earlier = FleetRun(earlier_run(_levels()).stamp, then_rows, [], steps=then_steps)
    return fs.slope_view(compared, earlier, now_rows, now_steps)


# --- slope chart ----------------------------------------------------------------


def test_each_groups_end_is_its_lowest_step_with_n_of_m_measured_never_the_mean():
    """#806: Steps 1, 3 and 3 average 2.33; the line starts at the lowest, Step 1, with 3 of 3 measured."""
    view = _view({"g": (1, 3, 3)}, {"g": (2, 3, None)})
    group = view["groups"][0]
    assert group["then"] == {"step": 1, "measured": 3, "repos": 3}
    assert group["now"] == {"step": 2, "measured": 2, "repos": 3}  # the unmeasured repository counts in M only
    text = fs.render_slope(view, [1, 2, 3])
    assert ('data-group="g" data-then="1" data-now="2" data-change="+1" data-measured-then="3" '
            'data-repos-then="3" data-measured-now="2" data-repos-now="3">'
            "<title>g: Step 1 → Step 2 (+1); 3 of 3 measured then, 2 of 3 measured now</title>") in text
    assert "3 of 3 → 2 of 3 measured *" in text  # the measured count moved: starred
    assert "2.33" not in text and "mean" not in text


def test_fixture_view_steps_and_absent_groups():
    view = _fixture_view()
    assert (view["earlier"], view["later"]) == ("2026-10-20", "2026-11-20")
    by = {g["group"]: g for g in view["groups"]}
    assert (by["payments"]["then"], by["payments"]["now"]) == (
        {"step": 1, "measured": 3, "repos": 3}, {"step": 2, "measured": 3, "repos": 3})
    assert by["web"]["set_moved"] is True  # web/admin joined
    assert (by["mobile"]["then"], by["mobile"]["absent"]) == (None, "added")


def test_a_group_with_no_measured_repository_on_a_side_is_not_plotted():
    view = _view({"kept": (1,), "dark": (None,)}, {"kept": (2,), "dark": (2,)})
    assert [g["group"] for g in fs.plotted(view)] == ["kept"]
    text = fs.render_slope(view)
    assert "dark (no measured repository in both runs)" in text
    assert "dark Step" not in text


def test_end_labels_keep_a_minimum_gap_on_each_side():
    labels = fs.end_labels(_fixture_view(), [1, 2, 3])
    for side in ("left", "right"):
        ys = sorted(lab["y"] for lab in labels if lab["side"] == side)
        assert len(ys) == 4
        assert all(b - a >= fs.LABEL_GAP for a, b in zip(ys, ys[1:]))
    left = [lab for lab in labels if lab["side"] == "left"]
    assert len({lab["point"] for lab in left}) < len(left)  # ties: payments and data at Step 1 before


def test_unplotted_groups_are_named_on_the_hatched_strip_and_the_star_is_explained():
    text = fs.render_slope(_fixture_view(), [1, 2, 3])
    assert "mobile (new since the earlier run)" in text
    assert 'fill="url(#fleet-slope-fleet-hatch)"' in text
    assert "2 of 2 → 3 of 3 measured *" in text
    assert "* the group's repositories or how many were measured moved too" in text
    assert '-1</text>' in text and '+1</text>' in text and '>=</text>' in text


def test_a_removed_group_is_named_never_step_0():
    text = fs.render_slope(_view({"kept": (1,), "gone": (2,)}, {"kept": (2,)}))
    assert "gone (not in this run)" in text and "gone Step" not in text


def test_the_set_changed_flag_stars_a_group_with_the_same_counts():
    view = _view({"g": (1, 2)}, {"g": (2, 2)}, set_changed=frozenset({"g"}))
    assert view["groups"][0]["set_moved"] is True
    assert _view({"g": (1, 2)}, {"g": (2, 2)})["groups"][0]["set_moved"] is False


def test_the_axis_spans_the_models_steps():
    text = fs.render_slope(_view({"g": (2,)}, {"g": (2,)}), [1, 2, 3, 4])
    assert re.findall(r"<title>(Step \d)</title></line>", text) == ["Step 1", "Step 2", "Step 3", "Step 4"]


def test_group_names_are_escaped_and_long_ones_truncated():
    name = "web/<&>" + "x" * 60
    text = fs.render_slope(_view({name: (1,)}, {name: (2,)}))
    assert "web/&lt;&amp;&gt;" in text
    # The full name is the hover text: in a <title> and its data attributes (#1360), never drawn.
    assert "x" * 40 not in re.sub(r'<title>.*?</title>|\sdata-[\w-]+="[^"]*"', "", text)


def test_slope_skips_say_why():
    levels, repos = _levels(), _repos()
    real = comparison(levels, repos)
    data = {"repos": [{"key": r, "step": s} for r, s in steps_of(levels, repos).items()]}

    def skipped(model: MaturityModel | None, compared: Comparison | None,
                earlier: FleetRun | str | None = None, with_data: bool = True) -> str | None:
        drawn = fd.build_diagrams(model, repos, WINDOW, comparison=compared,
                                  earlier=earlier_run(levels) if earlier is None else earlier,
                                  data=data if with_data else None)
        return next(d for d in drawn if d["file"] == fs.FILE)["skipped"]

    assert skipped(levels, real) is None
    assert skipped(levels, None) == fd.NO_EARLIER
    refused = not_comparable(None, ["window changed '6mo'->'3mo'"])
    assert skipped(levels, refused) == (
        "skipped: the earlier run is not comparable (window changed '6mo'->'3mo').")
    assert "no maturity model" in (skipped(None, real) or "")
    assert skipped(load_levels(FIXTURES / "levels.toml"), real) == fd.NO_STEPS
    assert skipped(levels, real, with_data=False) == fd.NO_DATA
    assert skipped(levels, real, earlier="unreadable") == fd.EARLIER_UNREADABLE
    old = earlier_run(levels)
    assert skipped(levels, real, earlier=FleetRun(old.stamp, old.repos, old.groups)) == fd.EARLIER_NO_STEPS
    none_measured = FleetRun(old.stamp, old.repos, old.groups, steps={})
    assert skipped(levels, real, earlier=none_measured) == (
        "skipped: no group has a measured repository in both runs.")


def test_a_raising_slope_renderer_is_a_skip_line(monkeypatch):
    def bug(*_args: object) -> str:
        raise KeyError("x")
    monkeypatch.setattr(fs, "render_slope", bug)
    levels, repos = _levels(), _repos()
    data = {"repos": [{"key": r, "step": s} for r, s in steps_of(levels, repos).items()]}
    drawn = fd.build_diagrams(levels, repos, WINDOW, comparison=comparison(levels, repos),
                              earlier=earlier_run(levels), data=data)
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
    many = tuple([1] * 120)
    text = fs.render_slope(_view({"platform-engineering/shared/very-long-subgroup-name": (1,), "g": many},
                                 {"platform-engineering/shared/very-long-subgroup-name": (3,),
                                  "g": (*many[:-1], None)}), [1, 2, 3, 4])
    for box in _text_boxes(text):
        assert box.x >= fs.PAD and box.x + box.w <= fs.WIDTH - fs.PAD, box
