"""Tests for the achievements engine, its history reader and report section (issue #704); Step targets: test_fleet_step_targets.py."""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import jsonschema
import pytest

from fleet_data_fixture import LEVELS, STAMP
import lib.fleet.fleet_achievements as fa
from lib.fleet.runner import FLEET_ROW_VERSION, FleetRow
from lib.fleet.fleet_achievements_runs import earlier_runs
from lib.fleet.fleet_achievements_schema import SCHEMA_PATH, render
from lib.fleet.fleet_credit import KIND_LABELS, TITLE, credit_section
from lib.fleet.fleet_data import FLEET_DATA_FILE, FleetData, Privacy, build_fleet_data, dumps_fleet_data
from lib.fleet.fleet_diagrams import report_markdown
from lib.fleet.fleet_stamp import FleetStamp, RUN_FILE
from lib.fleet.fleet_targets import TargetSpec, resolve
from lib.fleet.fleet_units import build_trees, empty_map
from lib.levels import MaturityModel, parse_levels
from lib.plain_english import CRITICAL_WORDS

MODEL: MaturityModel = parse_levels(tomllib.loads(LEVELS))
REPOS = ("grp/sub/a", "grp/sub/b", "grp/c")
TEAM = ("ownership", "ownership/grp/sub")
ORG = ("ownership", "ownership/grp")

# docs: 0 (no lines), 1 (lines, grade C), 2 (grade A); None: docs block unavailable
Spec = dict[str, tuple[int | None, bool]]  # repo -> (docs level, ci required)
FAILED = -1  # a docs level meaning the repository's scan failed


def _scans(docs: int | None, ci: bool) -> dict[str, object]:
    block: dict[str, object] = ({"available": False, "reason": "unavailable"} if docs is None else
                                {"available": True, "lines": 0 if docs == 0 else 10, "grade": "A" if docs == 2 else "C"})
    return {"docs": block, "ci": {"available": True, "required": ci}}


def _row(repo: str, docs: int | None, ci: bool) -> FleetRow:
    if docs == FAILED:
        return {"fleet_row_version": FLEET_ROW_VERSION, "repo": repo, "group": repo.rsplit("/", 1)[0],
                "status": "failed", "reason": "scan_timeout", "signals_schema_version": None, "scans": None,
                "active": None}
    return {"fleet_row_version": FLEET_ROW_VERSION, "repo": repo, "group": repo.rsplit("/", 1)[0], "status": "ok",
            "reason": None, "signals_schema_version": 1, "scans": _scans(docs, ci), "active": True}  # type: ignore[typeddict-item]  # the scans fixture is a partial block map


def _data(date: str, spec: Spec, privacy: Privacy = Privacy()) -> FleetData:
    rows = [_row(repo, *spec[repo]) for repo in sorted(spec)]
    stamp: FleetStamp = {**STAMP, "scan_date": date, "paths_hashed": privacy.salt is not None}
    return build_fleet_data(stamp, rows, MODEL, build_trees(sorted(spec), empty_map(), {}, {}), privacy=privacy)


BASE: Spec = {"grp/sub/a": (1, True), "grp/sub/b": (1, False), "grp/c": (0, True), "grp/two/d": (1, True)}
HISTORY: list[tuple[str, Spec]] = [
    ("2026-11-01", BASE),
    ("2026-11-08", {**BASE, "grp/sub/a": (2, True)}),  # a: docs 1 -> 2
    ("2026-11-15", {**BASE, "grp/sub/a": (2, True), "grp/sub/b": (1, True)}),  # b: ci 0 -> 1
    ("2026-11-22", {**BASE, "grp/sub/a": (2, True), "grp/sub/b": (2, True)}),  # b: docs 1 -> 2
]


def _runs(history: list[tuple[str, Spec]] = HISTORY, privacy: Privacy = Privacy()) -> list[fa.HistoryRun]:
    return [fa.HistoryRun(date, _data(date, spec, privacy)) for date, spec in history]


def _build(history: list[tuple[str, Spec]] = HISTORY, **kw: object) -> fa.Achievements:
    runs = _runs(history)
    return fa.build_achievements(runs[-1], runs[:-1], MODEL, **kw)  # type: ignore[arg-type]  # kw passes build_achievements' keyword options


def _earned(doc: fa.Achievements, kind: str | None = None, run: str | None = None) -> list[tuple[str, str, str, str | None]]:
    return [(a["run"], a["kind"], a["unit"], a["evidence"]["row"]) for a in doc["achievements"]
            if (kind is None or a["kind"] == kind) and (run is None or a["run"] == run)]


# --- the four-run fixture ----------------------------------------------------


def test_level_ups_across_four_runs_with_their_evidence() -> None:
    doc = _build()
    assert _earned(doc, fa.LEVEL_UP) == [
        ("2026-11-08", "level_up", "grp/sub/a", "docs"),
        ("2026-11-15", "level_up", "ownership/grp", "ci"),
        ("2026-11-15", "level_up", "ownership/grp/sub", "ci"),
        ("2026-11-15", "level_up", "grp/sub/b", "ci"),
        ("2026-11-22", "level_up", "ownership/grp/sub", "docs"),
        ("2026-11-22", "level_up", "grp/sub/b", "docs"),
    ]
    (team_docs,) = [a for a in doc["achievements"] if a["kind"] == fa.LEVEL_UP and a["unit"] == TEAM[1]
                    and a["evidence"]["row"] == "docs"]
    assert team_docs["evidence"]["from_level"] == 1 and team_docs["evidence"]["to_level"] == 2
    assert team_docs["evidence"]["moves"] == [{"repo": "grp/sub/b", "row": "docs", "from_level": 1, "to_level": 2,
                                               "criterion": "docs.grade"}]
    assert team_docs["previous_run"] == "2026-11-15" and team_docs["unit_kind"] == "team"
    assert "`grp/sub/b` met `docs.grade`" in team_docs["credit"]


def test_firsts_are_off_by_default() -> None:
    """#772 flips #759's default: ``first`` compares a unit with its peers (#740), so it is
    earned only when asked for, and no ``personal_best`` kind exists any more."""
    doc = _build()
    assert not _earned(doc, fa.FIRST)
    assert "personal_best" not in fa.KINDS
    assert {a["kind"] for a in doc["achievements"]} <= {fa.LEVEL_UP, fa.TARGET_HIT, fa.STREAK}
    assert _earned(doc, fa.LEVEL_UP)  # the level-ups the firsts would ride on are still earned


def test_firsts_are_milestones_of_the_fleets_own_history() -> None:
    doc = _build(firsts=True)
    firsts = _earned(doc, fa.FIRST)
    # the first repository at docs 2 (a) and the first team at docs 2 (grp/sub); grp/sub's
    # ci 1 is not a first (grp/two held it from the start), nor are b's moves (a repository
    # already held those levels); the one org root claims none
    assert firsts == [("2026-11-08", "first", "grp/sub/a", "docs"),
                      ("2026-11-22", "first", "ownership/grp/sub", "docs")]
    credit = [a["credit"] for a in doc["achievements"] if a["kind"] == fa.FIRST][-1]
    assert credit.startswith("A first for this fleet: `ownership/grp/sub` is the first team at level 2")


def test_streak() -> None:
    doc = _build()
    assert ("2026-11-15", "streak", "ownership/grp/sub", None) in _earned(doc, fa.STREAK)
    (streak,) = [a for a in doc["achievements"] if a["kind"] == fa.STREAK and a["unit"] == TEAM[1]]
    assert streak["evidence"]["runs"] == 3
    assert not _earned(doc, run="2026-11-01")  # the baseline earns nothing


def test_a_regression_breaks_the_streak() -> None:
    slipped = [*HISTORY[:2], ("2026-11-15", {**BASE, "grp/sub/a": (1, True)}),  # a slips back to docs 1
               ("2026-11-22", {**BASE, "grp/sub/a": (1, True)}), ("2026-11-29", {**BASE, "grp/sub/a": (1, True)})]
    streaks = _earned(_build(slipped), fa.STREAK)
    assert ("2026-11-15", "streak", "grp/sub/a", None) in _earned(_build(), fa.STREAK)  # without the slip
    assert ("2026-11-15", "streak", "grp/sub/a", None) not in streaks
    assert ("2026-11-29", "streak", "grp/sub/a", None) in streaks  # counted again from the slip
    # the team never slipped (min stays 1), so its streak still reaches 3
    assert ("2026-11-15", "streak", "ownership/grp/sub", None) in streaks


@pytest.mark.parametrize("history", [
    # c goes unknown: the org's measured set shrinks, its level rises from 0 to 1 without a real move
    [("2026-11-01", BASE), ("2026-11-08", {**BASE, "grp/c": (None, True)})],
    # c leaves the fleet
    [("2026-11-01", BASE), ("2026-11-08", {k: v for k, v in BASE.items() if k != "grp/c"})],
    # c from unknown straight to the top
    [("2026-11-01", {**BASE, "grp/c": (None, True)}), ("2026-11-08", {**BASE, "grp/c": (2, True)})],
])
def test_an_unknown_level_never_earns_or_loses_anything(history: list[tuple[str, Spec]]) -> None:
    doc = _build(history)
    assert _earned(doc) == []


def test_an_unscanned_run_is_neutral_for_the_streak() -> None:
    gap = [("2026-11-01", BASE), ("2026-11-08", BASE), ("2026-11-15", {**BASE, "grp/c": (FAILED, True)}),
           ("2026-11-22", BASE), ("2026-11-29", BASE)]
    mine = [run for run, _k, unit, _r in _earned(_build(gap), fa.STREAK) if unit == "grp/c"]
    # the gap run neither broke the streak nor counted toward it; the run after it compares
    # with the last run measuring c (#1041), so it extends the streak to 3 at once
    assert mine == ["2026-11-22"]


def test_running_twice_gives_identical_bytes_and_matches_the_schema() -> None:
    first, second = fa.dumps_achievements(_build()), fa.dumps_achievements(_build())
    assert first == second
    assert SCHEMA_PATH.read_text(encoding="utf-8") == render()
    jsonschema.validate(json.loads(first), json.loads(render()))


def test_hashed_runs_line_up_under_one_salt_and_name_no_repository() -> None:
    doc = fa.build_achievements(*reversed_split(_runs(privacy=Privacy(salt="s"))), MODEL)
    text = fa.dumps_achievements(doc)
    assert len([a for a in doc["achievements"] if a["kind"] == fa.LEVEL_UP]) == 6
    for name in ("grp", "sub/a", "sub/b"):
        assert name not in text


def test_runs_under_another_salt_have_no_prior_and_earn_nothing() -> None:
    runs = _runs(privacy=Privacy(salt="one"))
    other = _runs(privacy=Privacy(salt="two"))
    doc = fa.build_achievements(other[-1], runs[:-1], MODEL)
    assert [a for a in doc["achievements"] if a["run"] == doc["run"]] == []


def reversed_split(runs: list[fa.HistoryRun]) -> tuple[fa.HistoryRun, list[fa.HistoryRun]]:
    return runs[-1], runs[:-1]


# --- next steps ----------------------------------------------------------------


def test_nearest_level_up_names_the_criterion_repos_and_done_when() -> None:
    doc = _build(HISTORY[:2])
    steps = {(s["unit"], s["row"]): s for s in doc["nearest_level_ups"]}
    team = steps[(TEAM[1], "docs")]  # min rule: b must reach docs 2
    assert (team["level"], team["signal"], team["repos"], team["needed"]) == (2, "docs.grade", ["grp/sub/b"], 1)
    assert team["credit"].startswith("Next for `ownership/grp/sub`: level 2 of 2 on Instruction docs.")
    assert "Done when: " in team["credit"]
    assert steps[("grp/c", "docs")]["signal"] == "docs.lines"
    assert ("grp/sub/a", "docs") not in steps  # at the top level: no next step
    # the org's ci step: b is the one repository below level 1
    assert steps[(ORG[1], "ci")]["repos"] == ["grp/sub/b"]


def test_no_history_note_and_baseline() -> None:
    runs = _runs(HISTORY[:1])
    doc = fa.build_achievements(runs[0], [], MODEL, note="this run is not kept in the run history")
    assert doc["runs"] == ["2026-11-01"] and doc["note"] == "this run is not kept in the run history"
    assert doc["achievements"] == [] and doc["nearest_level_ups"]
    assert fa.build_achievements(runs[0], [], MODEL)["note"].startswith("no earlier comparable run")  # type: ignore[union-attr]  # note is set on a run with no history
    assert fa.build_achievements(runs[0], [], None)["nearest_level_ups"] == []


def test_known_level_is_the_single_floor_hook() -> None:
    cell: fa.Cell = {"row": "docs", "status": "unknown", "level": None, "top_level": 2, "signal": None,
                     "value": None, "reason": "unavailable"}
    assert fa.known_level(cell) is None
    assert fa.known_level({**cell, "status": "level", "level": 1}) == 1


# --- report section --------------------------------------------------------------


def test_report_section_renders_first_with_credit_and_next_steps() -> None:
    doc = _build(targets=resolve([TargetSpec("repo", "grp/c", 2)], Privacy().key), firsts=True)
    section = credit_section(doc, MODEL)
    report = report_markdown([], section)
    assert report.index(f"## {TITLE}") < len(report) and report.startswith("# Fleet diagrams")
    assert "- Level-ups: " in section and "- Firsts: " in section and "Personal best" not in section
    assert "- Firsts:" not in credit_section(_build(), MODEL)  # off by default (#772)
    assert "### Next level-ups" in section and "- Next for " not in section  # counted, never listed (#1255)
    assert re.search(r"^- \d+ units? (has|have) a measured next level-up", section, re.M)
    assert "- `grp/c`, Step 2: at Step 1 (1 of 1 measured); gates to go: `docs` holds back 1." in section
    assert "Since `2026-11-15`, over 4 comparable runs" in section


def test_report_with_nothing_new_and_no_history() -> None:
    runs = _runs([HISTORY[0], ("2026-11-08", BASE)])
    section = credit_section(fa.build_achievements(runs[1], runs[:1], MODEL), MODEL)
    assert "Nothing new this run." in section
    alone = credit_section(fa.build_achievements(runs[0], [], MODEL), MODEL)
    assert "This run is the starting line" in alone


def test_report_lines_are_credit_or_next_steps() -> None:
    sections = [credit_section(_build(h), MODEL) for h in (HISTORY, [*HISTORY[:2], ("2026-11-15", BASE)])]
    for section in sections:
        for word in CRITICAL_WORDS:
            assert word not in section.lower(), word


def test_the_credit_section_renders_one_count_line_per_kind_and_no_achievement_line() -> None:
    """#1255: the front screen leads the report, so the credit lists shrink to counts plus links."""
    many = {f"grp/r{i:02d}": (0, True) for i in range(25)}
    runs = _runs([("2026-11-01", many), ("2026-11-08", {k: (1, True) for k in many})])
    doc = fa.build_achievements(runs[1], runs[:1], MODEL)
    section = credit_section(doc, MODEL)
    earned = section.split("### Earned this run\n\n", 1)[1].split("\n### ", 1)[0]
    mine = [a for a in doc["achievements"] if a["run"] == doc["run"]]
    kinds = [k for k in fa.KINDS if any(a["kind"] == k for a in mine)]
    lines = [line for line in earned.splitlines() if line]
    assert len(lines) == len(kinds) and len(mine) > len(kinds)
    for kind, line in zip(kinds, lines):
        n = sum(1 for a in mine if a["kind"] == kind)
        assert line.startswith(f"- {KIND_LABELS[kind]}: {n}, each listed in [`achievements.json`](./achievements.json)")
    for a in mine:
        assert a["credit"] not in section
    assert "grp/r0" not in section


# --- the history reader --------------------------------------------------------


def _entry(org: Path, date: str, spec: Spec, stamp: dict[str, object] | None = None,
           data: FleetData | None = None) -> None:
    entry = org / date
    entry.mkdir(parents=True)
    (entry / RUN_FILE).write_text(json.dumps({**STAMP, "scan_date": date, **(stamp or {})}), encoding="utf-8")
    (entry / FLEET_DATA_FILE).write_text(dumps_fleet_data(data or _data(date, spec)), encoding="utf-8")
    (entry / "repos.jsonl").write_text("".join(json.dumps({"repo": r}) + "\n" for r in sorted(spec)),
                                       encoding="utf-8")


def test_earlier_runs_reads_comparable_entries_with_fleet_data(tmp_path: Path) -> None:
    org = tmp_path / "grp"
    for date, spec in HISTORY[:3]:
        _entry(org, date, spec)
    _entry(org, "2026-10-01", BASE, stamp={"window": "3mo"})  # not comparable
    (org / "2026-10-02").mkdir()  # no run.json
    _entry(org, "2026-10-03", BASE)
    (org / "2026-10-03" / FLEET_DATA_FILE).unlink()  # before #703: no fleet-data.json
    _entry(org, "2026-10-04", BASE)
    (org / "2026-10-04" / FLEET_DATA_FILE).write_text('{"fleet_data_version": 99}', encoding="utf-8")
    _entry(org, "2026-11-22", BASE)  # this run's date: from memory, never the history
    current: FleetStamp = {**STAMP, "scan_date": "2026-11-22"}
    runs = earlier_runs(org, current, frozenset(BASE))
    assert [r.run_id for r in runs] == ["2026-11-01", "2026-11-08", "2026-11-15"]
    doc = fa.build_achievements(fa.HistoryRun("2026-11-22", _data("2026-11-22", HISTORY[3][1])), runs, MODEL)
    assert doc == _build()


def test_an_entry_is_dated_by_its_run_json_not_the_fleet_data_copy(tmp_path: Path) -> None:
    # runs copied into the history with only run.json re-dated (fleet-data.json keeps the
    # date the run was made on) replay on run.json's date
    org = tmp_path / "grp"
    for date, spec in HISTORY[:3]:
        _entry(org, date, spec, data=_data("2026-12-31", spec))
    current: FleetStamp = {**STAMP, "scan_date": "2026-11-22"}
    runs = earlier_runs(org, current, frozenset(BASE))
    assert [r.run_id for r in runs] == ["2026-11-01", "2026-11-08", "2026-11-15"]
    doc = fa.build_achievements(fa.HistoryRun("2026-11-22", _data("2026-11-22", HISTORY[3][1])), runs, MODEL)
    assert {a["kind"] for a in doc["achievements"]} >= {fa.LEVEL_UP, fa.STREAK}
    assert _earned(doc) == _earned(_build())


def test_only_an_entry_without_run_json_is_dated_by_the_fleet_data_copy(tmp_path: Path) -> None:
    org = tmp_path / "grp"
    _entry(org, "2026-11-01", BASE)
    _entry(org, "2026-11-08", BASE)
    (org / "2026-11-08" / RUN_FILE).unlink()  # fleet-data.json's copy of the stamp stands in
    _entry(org, "2026-11-09", BASE, data=_data("2026-11-10", BASE))
    (org / "2026-11-09" / RUN_FILE).unlink()  # and its date must match the entry's
    _entry(org, "2026-11-10", BASE, stamp={"window": "3mo"})
    (org / "2026-11-10" / RUN_FILE).write_text("not json", encoding="utf-8")  # unreadable: passed over
    current: FleetStamp = {**STAMP, "scan_date": "2026-11-22"}
    assert [r.run_id for r in earlier_runs(org, current, frozenset(BASE))] == ["2026-11-01", "2026-11-08"]


def test_a_version_1_entry_with_the_old_roll_up_is_still_replayed(tmp_path: Path) -> None:
    """#772 removed the mean-share roll-up and bumped FLEET_DATA_VERSION; the engine reads only
    cells and units, so a fleet's earlier version 1 entries keep their achievements."""
    org = tmp_path / "grp"
    for date, spec in HISTORY[:3]:
        old = json.loads(dumps_fleet_data(_data(date, spec)))
        old["fleet_data_version"] = 1
        for repo in old["repos"]:
            repo["mean_share"] = 0.5
        for view in old["views"]:
            for unit in view["units"]:
                unit["overall"] = {"mean_share": 0.5, "measured": 1, "repos": 1, "weakest": None}
                for roll in unit["levels"]:
                    roll.update({"mean_share": 0.5, "weakest": None})
        _entry(org, date, spec)
        (org / date / FLEET_DATA_FILE).write_text(json.dumps(old), encoding="utf-8")
    current: FleetStamp = {**STAMP, "scan_date": "2026-11-22"}
    runs = earlier_runs(org, current, frozenset(BASE))
    assert [r.run_id for r in runs] == ["2026-11-01", "2026-11-08", "2026-11-15"]
    doc = fa.build_achievements(fa.HistoryRun("2026-11-22", _data("2026-11-22", HISTORY[3][1])), runs, MODEL)
    assert _earned(doc) == _earned(_build())


def test_a_version_2_entry_without_opportunities_is_still_replayed(tmp_path: Path) -> None:
    """#746 bumped FLEET_DATA_VERSION to 3 for ``opportunities`` only, which the engine never
    reads, so a fleet's earlier version 2 entries keep their achievements."""
    org = tmp_path / "grp"
    for date, spec in HISTORY[:3]:
        old = json.loads(dumps_fleet_data(_data(date, spec)))
        old["fleet_data_version"] = 2
        del old["opportunities"]
        _entry(org, date, spec)
        (org / date / FLEET_DATA_FILE).write_text(json.dumps(old), encoding="utf-8")
    current: FleetStamp = {**STAMP, "scan_date": "2026-11-22"}
    runs = earlier_runs(org, current, frozenset(BASE))
    assert [r.run_id for r in runs] == ["2026-11-01", "2026-11-08", "2026-11-15"]
    doc = fa.build_achievements(fa.HistoryRun("2026-11-22", _data("2026-11-22", HISTORY[3][1])), runs, MODEL)
    assert _earned(doc) == _earned(_build())


@pytest.mark.parametrize("version", [[1], {"v": 1}, True, "1", 1.0, None])
def test_a_history_entry_with_a_malformed_version_is_passed_over(tmp_path: Path, version: object) -> None:
    org = tmp_path / "grp"
    _entry(org, "2026-11-01", BASE)
    bad = json.loads(dumps_fleet_data(_data("2026-11-08", BASE)))
    bad["fleet_data_version"] = version
    _entry(org, "2026-11-08", BASE)
    (org / "2026-11-08" / FLEET_DATA_FILE).write_text(json.dumps(bad), encoding="utf-8")
    current: FleetStamp = {**STAMP, "scan_date": "2026-11-15"}
    assert [r.run_id for r in earlier_runs(org, current, frozenset(BASE))] == ["2026-11-01"]


@pytest.mark.parametrize("break_it", [
    lambda d: d.__setitem__("run", "not an object"),
    lambda d: d["repos"][0].pop("rows"),
    lambda d: d["repos"][0]["rows"][0].__setitem__("level", "two"),
    lambda d: d["repos"][0]["rows"][0].__setitem__("status", 1),
    lambda d: d["views"][0]["units"][0].pop("kind"),
    lambda d: d["views"][0]["units"][0].pop("parent"),
    lambda d: d["repos"][0]["units"].append({"view": "ownership"}),
])
def test_a_malformed_history_entry_is_passed_over(tmp_path: Path, break_it: object) -> None:
    org = tmp_path / "grp"
    _entry(org, "2026-11-01", BASE)
    bad = json.loads(dumps_fleet_data(_data("2026-11-08", BASE)))
    break_it(bad)  # type: ignore[operator]  # break_it is a parametrized callable
    _entry(org, "2026-11-08", BASE)
    (org / "2026-11-08" / FLEET_DATA_FILE).write_text(json.dumps(bad), encoding="utf-8")
    current: FleetStamp = {**STAMP, "scan_date": "2026-11-15"}
    runs = earlier_runs(org, current, frozenset(BASE))
    assert [r.run_id for r in runs] == ["2026-11-01"]
    fa.build_achievements(fa.HistoryRun("2026-11-15", _data("2026-11-15", BASE)), runs, MODEL)


def test_every_fleet_data_version_up_to_the_current_one_stays_readable() -> None:
    """A FLEET_DATA_VERSION bump must not silently drop the version before it (3.0.0 release)."""
    from lib.fleet.fleet_achievements_runs import READABLE_FLEET_DATA_VERSIONS
    from lib.fleet.fleet_data import FLEET_DATA_VERSION

    assert READABLE_FLEET_DATA_VERSIONS == frozenset(range(1, FLEET_DATA_VERSION + 1))
