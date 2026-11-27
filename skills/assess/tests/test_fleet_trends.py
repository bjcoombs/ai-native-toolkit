"""Tests for the group trend lines (issues #676, #806: lowest Step): lib/fleet/fleet_trends.py and
its entry in lib/fleet/fleet_diagrams.py.

The golden under ``tests/fixtures/fleet_trends/`` locks the whole render; regenerate it with
``uv run --locked python tests/fleet_trends_fixture.py`` from ``skills/assess``.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import lib.fleet.fleet_diagrams as fd
import lib.fleet.fleet_trends as ft
from lib.fleet.runner import FLEET_ROW_VERSION
from lib.scoring.levels import MaturityModel
from fleet_trends_fixture import (CURRENT, CURRENT_STAMP, ENTRIES, FIXTURES, LEVELS, LONG, REPOS, STEPS, build,
                                  goldens, members, repo_steps, runs, stamp)
from svg_cascade import overridden_text_attributes

SVG_NS = "{http://www.w3.org/2000/svg}"


def _run(label: str, version: str, steps: dict[str, int | None], stepped: bool = True) -> ft.TrendRun:
    """A run whose groups each hold one measured repository at ``steps`` (None: not measured)."""
    return {"label": label, "levels_version": version, "stepped": stepped,
            "points": {g: {"step": s, "measured": 0 if s is None else 1, "repos": 1} for g, s in steps.items()}}


def _entry(org: Path, name: str, groups: dict[str, tuple[int | None, ...]], version: str | None = "levels-2",
           window: str = "6mo", stored: bool = True) -> Path:
    entry = org / name
    entry.mkdir(parents=True)
    (entry / "run.json").write_text(json.dumps(stamp(name.split(".")[0], version, window)), encoding="utf-8")
    (entry / "repos.jsonl").write_text("".join(
        json.dumps({"fleet_row_version": FLEET_ROW_VERSION, "group": g, "repo": r}) + "\n"
        for g, r in members(groups)), encoding="utf-8")
    if stored:
        level = {"repos": {r: {"step": s} for r, s in repo_steps(groups).items()}}
        (entry / "level.json").write_text(json.dumps(level), encoding="utf-8")
    return entry


# --- fixture and golden -------------------------------------------------------


def test_fixture_matches_a_fresh_build(tmp_path):
    build(tmp_path)
    built = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*") if p.is_file())
    for rel in built:
        assert (tmp_path / rel).read_bytes() == (FIXTURES / rel).read_bytes()


def test_render_matches_golden():
    for name, content in goldens(FIXTURES).items():
        assert content == (FIXTURES / name).read_text(encoding="utf-8")


def test_two_renders_are_byte_identical():
    assert ft.render_trend(runs(FIXTURES)) == ft.render_trend(runs(FIXTURES))


def test_svg_is_self_contained_and_well_formed():
    content = ft.render_trend(runs(FIXTURES))
    assert ET.fromstring(content).tag == f"{SVG_NS}svg"
    assert "<script" not in content and "href" not in content and "@import" not in content
    assert "prefers-color-scheme: dark" in content and ".s-series7 {" in content
    assert re.findall(r"https?://[^\s\"']+", content) == ["http://www.w3.org/2000/svg"]
    assert overridden_text_attributes(content) == []


# --- reading the history --------------------------------------------------------


def test_history_keeps_comparable_runs_before_today_oldest_first():
    labels = [run["label"] for run in runs(FIXTURES)]
    # 2026-05-20 has another window and 2026-04-20 is malformed: both passed over
    assert labels == ["2026-06-20", "2026-07-20", "2026-08-20", "2026-09-20", "2026-10-20", "2026-11-20"]


def test_each_groups_point_is_its_lowest_step_with_n_of_m_measured_never_the_mean():
    """#806: payments holds Steps 1, 3 and 3 on 2026-09-20; its mean is 2.33, its point is Step 1."""
    run = next(r for r in runs(FIXTURES) if r["label"] == "2026-09-20")
    assert run["points"]["payments"] == {"step": 1, "measured": 3, "repos": 3}
    content = ft.render_trend(runs(FIXTURES), STEPS)
    assert ('data-group="payments" data-step="1" data-measured="3" data-repos="3" data-run="2026-09-20">'
            "<title>payments: Step 1 on 2026-09-20, 3 of 3 measured</title>") in content
    assert "2.33" not in content and "data-mean" not in content


def test_an_unmeasured_repository_counts_in_m_only_never_as_a_step():
    run = ft.trend_run(stamp("2026-01-01", "v"), [("g", "a"), ("g", "b"), ("h", "c")], {"a": 3, "b": None})
    assert run is not None and run["stepped"] is True
    assert run["points"] == {"g": {"step": 3, "measured": 1, "repos": 2},
                             "h": {"step": None, "measured": 0, "repos": 1}}


def test_a_run_without_stored_steps_is_marked_not_drawn():
    old = ft.trend_run(stamp("2026-01-01", "v"), [("g", "a")], None)
    assert old is not None and old["stepped"] is False and old["points"]["g"]["step"] is None


def test_runs_kept_before_steps_were_stored_are_named_not_drawn():
    drawn = runs(FIXTURES)
    hidden = ft.hidden_note(drawn)
    assert hidden == "2 earlier runs kept before Steps were stored not shown: 2026-06-20, 2026-07-20"
    content = ft.render_trend(drawn, STEPS)
    assert hidden in content and ft.AXIS_LABEL in content
    assert "2026-06-20</text>" not in content  # no date tick for a hidden run
    assert ft.layout(drawn).x == ft.layout(ft.stepped_runs(drawn)).x
    assert ft.hidden_note(ft.stepped_runs(drawn)) is None
    labels = re.findall(r'text-anchor="end" class="t-muted">(Step \d+)<', content)
    assert labels == ["Step 1", "Step 2", "Step 3", "Step 4"]  # every Step of the model


def test_the_axis_spans_the_models_steps_and_any_step_drawn():
    assert ft.step_range([_run("a", "v", {"g": 2})], [1, 2, 3]) == [1, 2, 3]
    assert ft.step_range([_run("a", "v", {"g": 5})], [1, 2, 3]) == [1, 2, 3, 4, 5]  # an older model's Step
    assert ft.step_range([_run("a", "v", {"g": 2})]) == [2, 3]  # never a zero span
    assert ft.step_range([]) == [1, 2]


def test_too_few_stepped_runs_is_a_skip_that_names_the_hidden_runs():
    old = [_run(f"2026-0{m}-01", "v1", {"g": None}, stepped=False) for m in (1, 2)]
    entry = _trend([*old, _run("2026-03-01", "v2", {"g": 2}), _run("2026-04-01", "v2", {"g": 2})])
    assert entry["svg"] is None
    assert entry["skipped"] == ("skipped: 2 comparable dated run(s), the trend needs at least 3 (same "
                                "window, scans and output mode, with a maturity model; 2 earlier runs kept "
                                "before Steps were stored not shown: 2026-01-01, 2026-02-01).")


def test_a_run_without_a_maturity_model_is_not_a_point(tmp_path):
    assert ft.trend_run(stamp("2026-01-01", None), [("g", "a")], {"a": 1}) is None
    org = tmp_path / "acme"
    _entry(org, "2026-01-01", {"g": (1,)}, version=None)
    assert ft.history_runs(org, CURRENT_STAMP, REPOS) == []


def test_one_run_per_date_prefers_this_runs_repository_set(tmp_path):
    org = tmp_path / "acme"
    _entry(org, "2026-01-01", {"payments": (1,), "platform": (1,)})
    _entry(org, "2026-01-01.2", {"payments": (3,)})  # a partial rerun
    _entry(org, "2026-02-01", {"g": (1,)})
    _entry(org, "2026-02-01.2", {"g": (2,)})  # same set unknown: the highest suffix
    found = ft.history_runs(org, CURRENT_STAMP, REPOS)
    assert [(r["label"], ft.step_of(r, "payments"), ft.step_of(r, "g")) for r in found] == [
        ("2026-01-01", 1, None), ("2026-02-01", None, 2)]


def test_unreadable_entries_and_a_missing_directory_are_passed_over(tmp_path):
    org = tmp_path / "acme"
    bad = org / "2026-01-01"
    bad.mkdir(parents=True)
    (bad / "run.json").write_text(json.dumps(stamp("2026-01-01", "levels-2")), encoding="utf-8")
    (bad / "repos.jsonl").write_text("{not json\n", encoding="utf-8")
    (org / "2026-02-01").mkdir()  # no run.json
    (org / "notes").mkdir()  # not a dated entry
    assert ft.history_runs(org, CURRENT_STAMP, REPOS) == []
    assert ft.history_runs(tmp_path / "missing", CURRENT_STAMP, REPOS) == []


def test_an_unreadable_level_json_is_a_run_without_stored_steps(tmp_path):
    org = tmp_path / "acme"
    entry = _entry(org, "2026-01-01", {"g": (1,)})
    (entry / "level.json").write_text("{not json\n", encoding="utf-8")
    (found,) = ft.history_runs(org, CURRENT_STAMP, REPOS)
    assert found["stepped"] is False


def test_series_keeps_the_newest_runs_with_this_run_last():
    earlier = [_run(f"2025-{m:02d}-01", "v", {"g": 1}) for m in range(1, 13)]
    current = _run("2026-01-01", "v", {"g": 2})
    drawn = ft.series_runs(earlier, current)
    assert len(drawn) == ft.MAX_RUNS and drawn[-1] is current and drawn[0]["label"] == "2025-02-01"


# --- layout and render ------------------------------------------------------------


def test_a_version_change_breaks_the_line():
    drawn = [_run("a", "v1", {"g": 1}), _run("b", "v1", {"g": 2}),
             _run("c", "v2", {"g": 2}), _run("d", "v2", {"g": 3})]
    assert ft._segments(drawn, "g") == [[0, 1], [2, 3]]
    plan = ft.layout(drawn)
    assert plan.breaks == [2]
    assert plan.labels[0].delta == 1  # over the last unbroken stretch only
    content = ft.render_trend(drawn)
    assert content.count("<polyline") == 2
    assert "maturity model v2" in content


def test_an_unknown_level_is_a_gap_never_zero():
    drawn = [_run("a", "v", {"g": 1}), _run("b", "v", {"g": None}), _run("c", "v", {"g": 2}),
             _run("d", "v", {"h": 1})]
    assert ft._segments(drawn, "g") == [[0], [2]]
    content = ft.render_trend(drawn)
    assert "<polyline" not in content  # every stretch is one run long
    assert content.count("<circle") == 3 and 'data-run="b"' not in content  # nothing drawn for b, never Step 0
    assert ft._gaps(drawn, ["g", "h"]) == ["g (no measured repository on b; not in the run on d)",
                                           "h (not in the run on a, b, c)"]
    assert "Gaps in a line, unknown is not Step 0: g (no measured repository on b" in content
    assert "fleet-hatch" in content


def test_a_one_run_segment_is_still_a_dot_with_no_delta():
    drawn = [_run("a", "v1", {"g": 1}), _run("b", "v1", {"g": 1}), _run("c", "v2", {"g": 2})]
    plan = ft.layout(drawn)
    assert plan.labels[0].delta is None
    assert ft.render_trend(drawn).count("<circle") == 3


def test_end_labels_keep_a_minimum_gap():
    plan = ft.layout(runs(FIXTURES))
    ys = sorted(label.y for label in plan.labels)
    assert len(ys) == 6
    assert all(b - a >= ft.LABEL_GAP for a, b in zip(ys, ys[1:]))


def test_end_labels_never_overlap_even_when_every_group_ends_level():
    drawn = [_run(d, "v", {f"group{i}": 2 for i in range(10)}) for d in ("a", "b", "c")]
    plan = ft.layout(drawn)
    ys = [label.y for label in plan.labels]
    assert all(b - a >= ft.LABEL_GAP for a, b in zip(ys, ys[1:]))
    content = ft.render_trend(drawn)
    height = float(content.split('height="')[1].split('"')[0])
    assert height > max(ys)
    assert 'stroke-dasharray="6 3"' in content  # past the eighth series colour


def test_a_long_group_name_is_truncated_with_the_full_name_as_tooltip():
    content = ft.render_trend(runs(FIXTURES), STEPS)
    assert f"<title>{LONG}</title>" in content
    assert "platform/infrastr… Step 3</text>".replace("</text>", "<title>") in content


def test_group_names_are_escaped():
    drawn = [_run(d, "v", {'web<&>"ui': 1, "x": None}) for d in ("a", "b", "c")]
    content = ft.render_trend(drawn)
    ET.fromstring(content)
    assert 'web&lt;&amp;&gt;"ui Step 1' in content and "<&>" not in content


def test_date_ticks_thin_out_but_keep_the_last_run():
    assert ft._tick_indices(12, 42.0) == [1, 3, 5, 7, 9, 11]
    assert ft._tick_indices(3, 232.0) == [0, 1, 2]


def test_delta_text():
    assert [ft._delta_text(d) for d in (None, 0, 1, -2)] == ["", "=", "+1", "-2"]


# --- in the report ------------------------------------------------------------------


def _trend(history: list[ft.TrendRun] | None, levels: MaturityModel | None = LEVELS) -> fd.Diagram:
    return next(d for d in fd.build_diagrams(levels, [], "6mo", history=history) if d["file"] == ft.FILE)


@pytest.mark.parametrize("count", [0, 1, 2])
def test_fewer_than_three_runs_is_a_one_line_skip(count):
    entry = _trend([_run(str(i), "v", {"g": 1}) for i in range(count)])
    assert entry["svg"] is None
    assert entry["skipped"] == (f"skipped: {count} comparable dated run(s), the trend needs at least 3 "
                                "(same window, scans and output mode, with a maturity model).")


def test_three_runs_draw_and_embed():
    entry = _trend([_run(str(i), "v", {"g": 1}) for i in range(3)])
    assert entry["svg"] is not None and entry["skipped"] is None
    assert "[![Group trends](./trend.svg)](./trend.svg)" in fd.report_markdown([entry])


def test_no_history_and_no_levels_say_why():
    assert _trend(None)["skipped"] == fd.NO_HISTORY
    assert _trend([], levels=None)["skipped"] == fd.NO_LEVELS
    no_steps: MaturityModel = {"version": "v", "rows": LEVELS["rows"]}
    assert _trend([], levels=no_steps)["skipped"] == fd.NO_STEPS


def test_a_raising_trend_renderer_is_a_skip_line(monkeypatch):
    def bug(_runs: object, _steps: object) -> str:
        raise RuntimeError("render bug")
    monkeypatch.setattr(ft, "render_trend", bug)
    entry = _trend([_run(str(i), "v", {"g": 1}) for i in range(3)])
    assert entry["skipped"] == "skipped: the renderer raised RuntimeError."


def test_trend_file_is_cleared_with_the_others(tmp_path):
    (tmp_path / ft.FILE).write_text("stale", encoding="utf-8")
    fd.clear_diagrams(tmp_path)
    assert not (tmp_path / ft.FILE).exists()


def test_fixture_entries_cover_a_version_change_and_another_window():
    assert {version for _, version, _, _, _ in ENTRIES} == {"levels-1", "levels-2"}
    assert {stored for *_, stored in ENTRIES} == {False, True}  # entries before level.json and after
    assert {window for _, _, window, _, _ in ENTRIES} == {"3mo", "6mo"}
    assert "mobile" not in ENTRIES[-1][3] and "mobile" in CURRENT  # a group absent from one run
