"""The fleet front screen (#1255): ``lib/fleet/fleet_front.py`` and its place in ``report.md``.

Goldens: ``tests/fixtures/fleet_front/`` (regenerate with
``uv run --locked python tests/fleet_front_fixture.py`` from ``skills/assess``).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

import fleet_data_fixture as fdf
import fleet_front_fixture as fx
import fleet_page_fixture as fpf
from lib.fleet.fleet_front import (TITLE, front_screen, front_section, gate_lifts, movement, report_teams)
from lib.fleet.fleet_page_data import COUNTS_ONLY, FULL, HASHED
from lib.fleet.fleet_step_movement import StepMoves
from lib.vocabulary import IN_PLACE, banned_hits


@pytest.mark.parametrize("name", sorted(fx.goldens()))
def test_report_matches_golden(name: str) -> None:
    assert fx.goldens()[name] == (fx.FIXTURES / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", sorted(fpf.VARIANTS))
def test_the_first_heading_of_the_report_is_the_front_screen(name: str) -> None:
    report = fx.report(name)
    assert re.findall(r"^## (.*)$", report, re.M)[0] == TITLE
    section = report.split(f"## {TITLE}\n\n", 1)[1]
    assert section.startswith(IN_PLACE)
    assert banned_hits(section.split("\n## ", 1)[0]) == []


@pytest.mark.parametrize("name", ["counts-only", "hashed"])
def test_a_counts_only_or_hashed_front_screen_names_no_repository_or_group(name: str) -> None:
    section = fx.report(name).split("\n## What changed", 1)[0]
    for leak in fpf.NAMES:
        assert leak not in section, leak


def test_the_counts_only_front_screen_links_only_the_team_index() -> None:
    section = fx.report("counts-only")
    assert "[`teams.md`](./teams.md)" in section and "./teams/" not in section
    full = fx.report("full")
    assert full.index("[grp/other](./teams/grp/other.md)") < full.index("[grp/sub](./teams/grp/sub.md)")


def test_report_teams_follow_the_output_mode() -> None:
    assert report_teams(["b", "a"], FULL) == ([{"label": "a", "href": "teams/a.md"},
                                               {"label": "b", "href": "teams/b.md"}], None)
    assert report_teams(["b"], COUNTS_ONLY) == ([], "teams.md")
    assert report_teams([], COUNTS_ONLY) == ([], None)
    hashed, index = report_teams(["h:00000000000000ff"], HASHED)
    assert hashed == [{"label": "h:00000000000000ff", "href": "teams/h-00000000000000ff.md"}] and index is None


def test_steps_count_every_measured_repository_once_and_set_the_unmeasured_apart() -> None:
    data = fpf.run(fpf.DATES[-1], fpf.VARIANTS["counts-only"])
    front = front_screen(fdf.model(), data)
    assert [(s["step"], s["name"], s["count"]) for s in front["steps"]] == [
        (1, "Assisted", 1), (2, "Docs", 2), (3, "Docs and CI", 0)]
    measured = [r for r in data["repos"] if r.get("step") is not None]
    assert sum(s["count"] for s in front["steps"]) == len(measured)
    assert front["not_measured"] == len(data["repos"]) - len(measured)
    assert front["moved"] is None and "This run is the baseline" in front_section(front, 4)


def _lifts(*entries: tuple[str, int]) -> dict[str, Any]:
    return {"opportunities": {"step_lifts": [
        {"condition": f"r{i}.1", "row": f"r{i}", "title": "t", "gate": gate, "step": 2, "repo_count": n}
        for i, (gate, n) in enumerate(entries)]}}


def test_gates_are_summed_and_ranked_most_repositories_first_then_by_id() -> None:
    ranked = gate_lifts(_lifts(("b", 2), ("a", 1), ("c", 3), ("a", 1), ("instructions", 4)))
    assert [(g["gate"], g["repos"]) for g in ranked] == [("instructions", 4), ("c", 3), ("a", 2), ("b", 2)]
    assert ranked[0]["task"].startswith("commit an instruction file")  # the top card's wording (#1252)
    assert ranked[1]["task"] == "make the c gate hold"
    assert gate_lifts({}) == []


def test_movement_counts_up_down_and_same_and_leaves_the_lanes_out() -> None:
    moved = movement(fpf.MOVES)
    assert (moved["up"], moved["down"], moved["same"], moved["rule_only"]) == (1, 1, 1, 1)
    apart: StepMoves = {**fpf.MOVES, "comparable": False, "reasons": ["the window differs"], "cells": []}
    front = front_screen(fdf.model(), fpf.run(fpf.DATES[-1], fpf.VARIANTS["full"]), apart)
    assert "Not compared with the earlier run: the window differs." in front_section(front, 4)


@pytest.mark.parametrize(("model", "data", "why"), [
    (None, {}, "no maturity model"),
    ({**fdf.model(), "steps": []}, {}, "defines no Steps"),
    (fdf.model(), None, "no fleet-data.json"),
])
def test_without_steps_the_front_screen_says_why(model: Any, data: Any, why: str) -> None:
    front = front_screen(model, data)
    section = front_section(front, 7)
    assert why in (front["skipped"] or "") and f"Not drawn: skipped: {front['skipped']}" in section
    assert section.splitlines()[2] == f"{IN_PLACE} 7 repositories in this run, scanned with no model call."
    assert "| Step |" not in section and "One change away" not in section


# --- a front screen that raises never costs the report (PR review) ------------------------


def _raise(*_args: object, **_kwargs: object) -> Any:
    raise ValueError("front bug")


def test_guarded_front_turns_a_raise_into_a_skip_line(monkeypatch: pytest.MonkeyPatch) -> None:
    import lib.fleet.fleet_front as ff
    data = fpf.run(fpf.DATES[-1], fpf.VARIANTS["full"])
    front, moves = ff.guarded_front(fdf.model(), data, _raise, ["grp/sub"], FULL)
    assert front["skipped"] == "the Step movement counts raised ValueError." and moves is None
    monkeypatch.setattr(ff, "front_screen", _raise)
    front, moves = ff.guarded_front(fdf.model(), data, lambda: fpf.MOVES, ["grp/sub"], FULL)
    assert front["skipped"] == "the front screen raised ValueError." and moves == fpf.MOVES
    section = front_section(front, 4)
    assert section.startswith(f"## {TITLE}\n\n{IN_PLACE}") and "Not drawn: skipped: the front screen raised" in section


@pytest.mark.parametrize("broken", ["front_screen", "step_moves_for"])
def test_a_raising_front_screen_still_writes_the_report_and_clears_stale_files(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, broken: str) -> None:
    import lib.fleet.fleet_front as ff
    from test_fleet_achievements_cli import STEP_LEVELS, _root
    from test_fleet_cli import fleet_cli
    root = _root(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "levels.toml").write_text(STEP_LEVELS, encoding="utf-8")
    args = [str(root), "--levels", "levels.toml", "--history-dir", "hist", "--out", "out"]
    assert fleet_cli.main(args) == 0  # a first run, so the second has an earlier run to compare
    out = tmp_path / "out"
    for stale in ("report.md", "quadrant.svg", "slope.svg", "step-movement.svg"):
        (out / stale).write_text("STALE", encoding="utf-8")
    monkeypatch.setattr(ff if broken == "front_screen" else fleet_cli, broken, _raise)
    assert fleet_cli.main([*args, "--compare"]) == 0
    report = (out / "report.md").read_text(encoding="utf-8")
    assert "STALE" not in report and re.findall(r"^## (.*)$", report, re.M)[0] == TITLE
    assert "Not drawn: skipped: the " in report.split("\n## ", 2)[1]
    assert "## What changed and who earned it" in report and "## Shared tooling" in report  # the rest is written
    assert (out / "fleet-view.html").is_file()
    for name in ("quadrant.svg", "slope.svg", "step-movement.svg"):
        assert not (out / name).exists() or (out / name).read_text(encoding="utf-8") != "STALE", name
