"""Tests for the group trend lines (issue #676): lib/fleet_trends.py and its entry in
lib/fleet_diagrams.py.

The golden under ``tests/fixtures/fleet_trends/`` locks the whole render; regenerate it with
``uv run --locked python tests/fleet_trends_fixture.py`` from ``skills/assess``.
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from lib import fleet_diagrams as fd
from lib import fleet_trends as ft
from lib.fleet_groups import GroupRow
from lib.rubric import Rubric
from fleet_trends_fixture import CURRENT_STAMP, ENTRIES, FIXTURES, LONG, REPOS, build, goldens, group, runs, stamp
from svg_cascade import overridden_text_attributes

SVG_NS = "{http://www.w3.org/2000/svg}"
LEVELS: Rubric = {"version": "levels-2", "rows": []}  # build_diagrams only checks it is present


def _run(label: str, version: str, means: dict[str, float | None]) -> ft.TrendRun:
    return {"label": label, "levels_version": version, "means": means}


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
    assert overridden_text_attributes(content) == []


# --- reading the history --------------------------------------------------------


def test_history_keeps_comparable_runs_before_today_oldest_first():
    labels = [run["label"] for run in runs(FIXTURES)]
    # 2026-05-20 has another window and 2026-04-20 is malformed: both passed over
    assert labels == ["2026-06-20", "2026-07-20", "2026-08-20", "2026-09-20", "2026-10-20", "2026-11-20"]


def test_mean_level_is_the_mean_of_known_row_levels():
    assert ft.mean_level(group("g", (1, 2, None))) == 1.5
    assert ft.mean_level(group("g", (None, None))) is None


def test_a_run_without_a_maturity_model_is_not_a_point(tmp_path):
    assert ft.trend_run(stamp("2026-01-01", None), [group("g", (1,))]) is None
    org = tmp_path / "acme"
    _entry(org, "2026-01-01", [group("g", (1,))], version=None)
    assert ft.history_runs(org, CURRENT_STAMP, REPOS) == []


def test_one_run_per_date_prefers_this_runs_repository_set(tmp_path):
    org = tmp_path / "acme"
    _entry(org, "2026-01-01", [group("g", (1,))], repos=tuple(sorted(REPOS)))
    _entry(org, "2026-01-01.2", [group("g", (3,))], repos=("payments/api",))  # a partial rerun
    _entry(org, "2026-02-01", [group("g", (1,))])
    _entry(org, "2026-02-01.2", [group("g", (2,))])  # same set unknown: the highest suffix
    found = ft.history_runs(org, CURRENT_STAMP, REPOS)
    assert [(r["label"], r["means"]["g"]) for r in found] == [("2026-01-01", 1.0), ("2026-02-01", 2.0)]


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
    assert "platform/infrastructu… 2.5" in content


def test_date_ticks_thin_out_but_keep_the_last_run():
    assert ft._tick_indices(12, 42.0) == [1, 3, 5, 7, 9, 11]
    assert ft._tick_indices(3, 232.0) == [0, 1, 2]


def test_delta_text():
    assert [ft._delta_text(d) for d in (None, 0.0, 0.5, -0.2)] == ["", "0.0", "+0.5", "-0.2"]


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
    assert {version for _, version, _, _ in ENTRIES} == {"levels-1", "levels-2"}
    assert {window for _, _, window, _ in ENTRIES} == {"3mo", "6mo"}
