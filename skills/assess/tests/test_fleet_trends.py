"""Tests for the group trend lines (issue #676): lib/fleet_trends.py and its entry in
lib/fleet_diagrams.py.

The golden under ``tests/fixtures/fleet_trends/`` locks the whole render; regenerate it with
``uv run --locked python tests/fleet_trends_fixture.py`` from ``skills/assess``.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from lib import fleet_diagrams as fd
from lib import fleet_trends as ft
from lib.fleet_groups import GroupRow
from lib.rubric import Rubric
from fleet_trends_fixture import (CURRENT_STAMP, ENTRIES, FIXTURES, LEVELS, LONG, REPOS, TOP, build, goldens,
                                  group, runs, stamp)
from svg_cascade import overridden_text_attributes

SVG_NS = "{http://www.w3.org/2000/svg}"
def _run(label: str, version: str, means: dict[str, float | None], scaled: bool = True) -> ft.TrendRun:
    return {"label": label, "levels_version": version, "means": means, "scaled": scaled}


def _entry(org: Path, name: str, groups: list[GroupRow], version: str | None = "levels-2",
           window: str = "6mo", repos: tuple[str, ...] = ()) -> Path:
    entry = org / name
    entry.mkdir(parents=True)
    (entry / "run.json").write_text(json.dumps(stamp(name.split(".")[0], version, window)), encoding="utf-8")
    (entry / "groups.jsonl").write_text("".join(json.dumps(g) + "\n" for g in groups), encoding="utf-8")
    if repos:
        (entry / "repos.jsonl").write_text("".join(json.dumps({"repo": r}) + "\n" for r in repos),
                                           encoding="utf-8")
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
    assert "prefers-color-scheme: dark" in content and ".s-series7{" in content
    assert re.findall(r"https?://[^\s\"']+", content) == ["http://www.w3.org/2000/svg"]
    assert overridden_text_attributes(content) == []


# --- reading the history --------------------------------------------------------


def test_history_keeps_comparable_runs_before_today_oldest_first():
    labels = [run["label"] for run in runs(FIXTURES)]
    # 2026-05-20 has another window and 2026-04-20 is malformed: both passed over
    assert labels == ["2026-06-20", "2026-07-20", "2026-08-20", "2026-09-20", "2026-10-20", "2026-11-20"]


def test_a_group_mean_is_the_mean_share_of_top_level_over_known_team_rows():
    assert ft.group_mean(group("g", (1, 2, None), top=4, org=0)) == (1 / 4 + 2 / 4) / 2
    assert ft.group_mean(group("g", (None,), top=4)) is None


def test_a_run_with_top_levels_is_a_share_and_one_without_is_marked_not_drawn():
    new = ft.trend_run(stamp("2026-01-01", "v"), [group("g", (1, 2), top=TOP)])
    old = ft.trend_run(stamp("2026-01-01", "v"), [group("g", (1, 2))])
    assert new is not None and old is not None
    assert (new["scaled"], new["means"]["g"]) == (True, 0.5)
    assert (old["scaled"], old["means"]["g"]) == (False, None)
    # one known team row without a top level marks the whole run
    mixed = ft.trend_run(stamp("2026-01-01", "v"), [group("g", (1,), top=TOP), group("h", (2,))])
    assert mixed is not None and mixed["scaled"] is False


def test_an_older_entry_of_this_runs_model_version_takes_its_top_levels(tmp_path):
    org = tmp_path / "acme"
    _entry(org, "2026-01-01", [group("g", (1, 2))], version="levels-2")
    _entry(org, "2026-02-01", [group("g", (1, 2))], version="levels-1")
    found = ft.history_runs(org, CURRENT_STAMP, REPOS, LEVELS)
    assert [(r["label"], r["scaled"], r["means"]["g"]) for r in found] == [
        ("2026-01-01", True, 0.5), ("2026-02-01", False, None)]
    assert all(not r["scaled"] for r in ft.history_runs(org, CURRENT_STAMP, REPOS))  # no model given


def test_runs_without_top_levels_are_named_not_drawn_on_a_share_axis():
    drawn = runs(FIXTURES)
    hidden = ft.hidden_note(drawn)
    assert hidden == ("3 earlier runs on an older maturity-model version not shown: "
                      "2026-06-20, 2026-07-20, 2026-08-20")
    content = ft.render_trend(drawn)
    assert hidden in content and ft.AXIS_LABEL in content
    assert "2026-06-20</text>" not in content  # no date tick for a hidden run
    assert ft.layout(drawn).x == ft.layout(ft.share_runs(drawn)).x
    assert ft.hidden_note(ft.share_runs(drawn)) is None
    labels = re.findall(r'text-anchor="end" class="t-muted">([0-9.]+)<', content)
    assert labels == ["0", "0.25", "0.5", "0.75", "1"]  # always 0 to 1


def test_too_few_share_runs_is_a_skip_that_names_the_hidden_runs():
    old = [_run(f"2026-0{m}-01", "v1", {"g": None}, scaled=False) for m in (1, 2)]
    entry = _trend([*old, _run("2026-03-01", "v2", {"g": 0.5}), _run("2026-04-01", "v2", {"g": 0.5})])
    assert entry["svg"] is None
    assert entry["skipped"] == ("skipped: 2 comparable dated run(s), the trend needs at least 3 (same "
                                "window, scans and output mode, with a maturity model; 2 earlier runs on "
                                "an older maturity-model version not shown: 2026-01-01, 2026-02-01).")


def test_a_run_without_a_maturity_model_is_not_a_point(tmp_path):
    assert ft.trend_run(stamp("2026-01-01", None), [group("g", (1,))]) is None
    org = tmp_path / "acme"
    _entry(org, "2026-01-01", [group("g", (1,))], version=None)
    assert ft.history_runs(org, CURRENT_STAMP, REPOS) == []


def test_one_run_per_date_prefers_this_runs_repository_set(tmp_path):
    org = tmp_path / "acme"
    _entry(org, "2026-01-01", [group("g", (1,), top=TOP)], repos=tuple(sorted(REPOS)))
    _entry(org, "2026-01-01.2", [group("g", (3,), top=TOP)], repos=("payments/api",))  # a partial rerun
    _entry(org, "2026-02-01", [group("g", (1,), top=TOP)])
    _entry(org, "2026-02-01.2", [group("g", (2,), top=TOP)])  # same set unknown: the highest suffix
    found = ft.history_runs(org, CURRENT_STAMP, REPOS)
    assert [(r["label"], r["means"]["g"]) for r in found] == [("2026-01-01", 1 / TOP), ("2026-02-01", 2 / TOP)]


def test_unreadable_entries_and_a_missing_directory_are_passed_over(tmp_path):
    org = tmp_path / "acme"
    bad = org / "2026-01-01"
    bad.mkdir(parents=True)
    (bad / "run.json").write_text(json.dumps(stamp("2026-01-01", "levels-2")), encoding="utf-8")
    (bad / "groups.jsonl").write_text("{not json\n", encoding="utf-8")
    (org / "2026-02-01").mkdir()  # no run.json
    (org / "notes").mkdir()  # not a dated entry
    assert ft.history_runs(org, CURRENT_STAMP, REPOS) == []
    assert ft.history_runs(tmp_path / "missing", CURRENT_STAMP, REPOS) == []


def test_series_keeps_the_newest_runs_with_this_run_last():
    earlier = [_run(f"2025-{m:02d}-01", "v", {"g": 1.0}) for m in range(1, 13)]
    current = _run("2026-01-01", "v", {"g": 2.0})
    drawn = ft.series_runs(earlier, current)
    assert len(drawn) == ft.MAX_RUNS and drawn[-1] is current and drawn[0]["label"] == "2025-02-01"


# --- layout and render ------------------------------------------------------------


def test_a_version_change_breaks_the_line():
    drawn = [_run("a", "v1", {"g": 1.0}), _run("b", "v1", {"g": 2.0}),
             _run("c", "v2", {"g": 2.0}), _run("d", "v2", {"g": 3.0})]
    assert ft._segments(drawn, "g") == [[0, 1], [2, 3]]
    plan = ft.layout(drawn)
    assert plan.breaks == [2]
    assert plan.labels[0].delta == 1.0  # over the last unbroken stretch only
    content = ft.render_trend(drawn)
    assert content.count("<polyline") == 2
    assert "maturity model v2" in content


def test_an_unknown_level_is_a_gap_never_zero():
    drawn = [_run("a", "v", {"g": 1.0}), _run("b", "v", {"g": None}), _run("c", "v", {"g": 2.0}),
             _run("d", "v", {"h": 1.0})]
    assert ft._segments(drawn, "g") == [[0], [2]]
    content = ft.render_trend(drawn)
    assert "<polyline" not in content  # every stretch is one run long
    assert content.count(f'cy="{ft.PLOT_T + ft.PLOT_H:.1f}"') == 0  # nothing drawn on the zero line
    assert ft._gaps(drawn, ["g", "h"]) == ["g (no level on b; not in the run on d)",
                                           "h (not in the run on a, b, c)"]
    assert "Gaps in a line, unknown is not zero: g (no level on b" in content
    assert "fleet-hatch" in content


def test_a_one_run_segment_is_still_a_dot_with_no_delta():
    drawn = [_run("a", "v1", {"g": 1.0}), _run("b", "v1", {"g": 1.0}), _run("c", "v2", {"g": 2.0})]
    plan = ft.layout(drawn)
    assert plan.labels[0].delta is None
    assert ft.render_trend(drawn).count("<circle") == 3


def test_end_labels_keep_a_minimum_gap():
    plan = ft.layout(runs(FIXTURES))
    ys = sorted(label.y for label in plan.labels)
    assert len(ys) == 6
    assert all(b - a >= ft.LABEL_GAP for a, b in zip(ys, ys[1:]))


def test_end_labels_never_overlap_even_when_every_group_ends_level():
    drawn = [_run(d, "v", {f"group{i}": 2.0 for i in range(10)}) for d in ("a", "b", "c")]
    plan = ft.layout(drawn)
    ys = [label.y for label in plan.labels]
    assert all(b - a >= ft.LABEL_GAP for a, b in zip(ys, ys[1:]))
    content = ft.render_trend(drawn)
    height = float(content.split('height="')[1].split('"')[0])
    assert height > max(ys)
    assert 'stroke-dasharray="6 3"' in content  # past the eighth series colour


def test_a_long_group_name_is_truncated_with_the_full_name_as_tooltip():
    content = ft.render_trend(runs(FIXTURES))
    assert f"<title>{LONG}</title>" in content
    assert "platform/infrastructu… 0.83" in content


def test_group_names_are_escaped():
    drawn = [_run(d, "v", {'web<&>"ui': 1.0, "x": None}) for d in ("a", "b", "c")]
    content = ft.render_trend(drawn)
    ET.fromstring(content)
    assert 'web&lt;&amp;&gt;"ui 1.0' in content and "<&>" not in content


def test_date_ticks_thin_out_but_keep_the_last_run():
    assert ft._tick_indices(12, 42.0) == [1, 3, 5, 7, 9, 11]
    assert ft._tick_indices(3, 232.0) == [0, 1, 2]


def test_delta_text():
    assert [ft._delta_text(d) for d in (None, 0.0, 0.25, -0.2)] == ["", "0.00", "+0.25", "-0.20"]


# --- in the report ------------------------------------------------------------------


def _trend(history: list[ft.TrendRun] | None, levels: Rubric | None = LEVELS) -> fd.Diagram:
    return next(d for d in fd.build_diagrams(levels, [], "6mo", history=history) if d["file"] == ft.FILE)


@pytest.mark.parametrize("count", [0, 1, 2])
def test_fewer_than_three_runs_is_a_one_line_skip(count):
    entry = _trend([_run(str(i), "v", {"g": 1.0}) for i in range(count)])
    assert entry["svg"] is None
    assert entry["skipped"] == (f"skipped: {count} comparable dated run(s), the trend needs at least 3 "
                                "(same window, scans and output mode, with a maturity model).")


def test_three_runs_draw_and_embed():
    entry = _trend([_run(str(i), "v", {"g": 1.0}) for i in range(3)])
    assert entry["svg"] is not None and entry["skipped"] is None
    assert "[![Group trends](./trend.svg)](./trend.svg)" in fd.report_markdown([entry])


def test_no_history_and_no_levels_say_why():
    assert _trend(None)["skipped"] == fd.NO_HISTORY
    assert _trend([], levels=None)["skipped"] == fd.NO_LEVELS
    org_only: Rubric = {"version": "v", "rows": [r for r in LEVELS["rows"] if r["scope"] == "org"]}
    assert _trend([], levels=org_only)["skipped"] == fd.NO_TEAM_ROWS


def test_a_raising_trend_renderer_is_a_skip_line(monkeypatch):
    def bug(_runs: object) -> str:
        raise RuntimeError("render bug")
    monkeypatch.setattr(ft, "render_trend", bug)
    entry = _trend([_run(str(i), "v", {"g": 1.0}) for i in range(3)])
    assert entry["skipped"] == "skipped: drawing failed (RuntimeError)."


def test_trend_file_is_cleared_with_the_others(tmp_path):
    (tmp_path / ft.FILE).write_text("stale", encoding="utf-8")
    fd.clear_diagrams(tmp_path)
    assert not (tmp_path / ft.FILE).exists()


def test_fixture_entries_cover_a_version_change_and_another_window():
    assert {version for _, version, _, _, _ in ENTRIES} == {"levels-1", "levels-2"}
    assert {top for *_, top in ENTRIES} == {None, TOP}  # legacy entries and ones with top levels
    assert {window for _, _, window, _, _ in ENTRIES} == {"3mo", "6mo"}
