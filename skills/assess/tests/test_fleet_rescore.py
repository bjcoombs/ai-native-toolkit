"""Tests for re-scoring stored runs under the current maturity model and the Recount (issue #771)."""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from fleet_data_fixture import LEVELS, STAMP
from lib import fleet_achievements as fa
from lib import fleet_rescore as fr
from lib.fleet import FLEET_ROW_VERSION, FleetRow, dumps_rows
from lib.fleet_achievements_runs import earlier_runs
from lib.fleet_achievements_schema import render
from lib.fleet_credit import credit_section
from lib.fleet_data import FLEET_DATA_FILE, Cell, FleetData, Privacy, build_fleet_data, dumps_fleet_data
from lib.fleet_stamp import RUN_FILE, FleetStamp
from lib.fleet_units import build_trees, empty_map
from lib.levels import MaturityModel, parse_levels


def _model(text: str) -> MaturityModel:
    return parse_levels(tomllib.loads(text))


V1 = _model(LEVELS)
SAME = _model(LEVELS.replace('version = "fleet-data-1"', 'version = "fleet-data-2"'))
# docs 2 needs grade A (was A or B); ci 1 is met by any ci block (was required only)
V2 = _model(LEVELS.replace('version = "fleet-data-1"', 'version = "fleet-data-2"')
            .replace('value = ["A", "B"]', 'value = ["A"]')
            .replace('{ signal = "ci.required", op = "eq", value = true }',
                     '{ signal = "ci.required", op = "in", value = [true, false] }'))
# ci 1 reads a signal no stored scan holds
V3 = _model(LEVELS.replace('version = "fleet-data-1"', 'version = "fleet-data-3"')
            .replace('{ signal = "ci.required", op = "eq", value = true }',
                     '{ signal = "ci_checks.test_or_lint_configured", op = "eq", value = true }'))

# repo -> (docs lines, docs grade, ci required); a None docs entry: docs block unavailable
Spec = dict[str, tuple[int, str, bool] | tuple[None, None, bool]]
BASE: Spec = {"grp/sub/a": (10, "B", True), "grp/sub/b": (10, "C", False), "grp/c": (0, "C", True),
              "grp/two/d": (10, "A", True)}
KEY = Privacy().key
DATES = ("2026-11-01", "2026-11-08", "2026-11-15")
NOW = "2026-11-22"


def _row(repo: str, lines: int | None, grade: str | None, ci: bool) -> FleetRow:
    docs: dict[str, object] = ({"available": False, "reason": "unavailable"} if lines is None else
                               {"available": True, "lines": lines, "grade": grade})
    return {"fleet_row_version": FLEET_ROW_VERSION, "repo": repo, "group": repo.rsplit("/", 1)[0], "status": "ok",
            "reason": None, "signals_schema_version": 1, "active": True,
            "scans": {"docs": docs, "ci": {"available": True, "required": ci}}}  # type: ignore[typeddict-item]


def _rows(spec: Spec) -> list[FleetRow]:
    return [_row(repo, *spec[repo]) for repo in sorted(spec)]


def _stamp(date: str, model: MaturityModel, **extra: object) -> FleetStamp:
    return {**STAMP, "scan_date": date, "levels_version": model["version"], **extra}  # type: ignore[typeddict-item]


def _data(date: str, spec: Spec, model: MaturityModel, privacy: Privacy = Privacy()) -> FleetData:
    stamp = _stamp(date, model, paths_hashed=privacy.salt is not None)
    return build_fleet_data(stamp, _rows(spec), model, build_trees(sorted(spec), empty_map(), {}, {}),
                            privacy=privacy)


def _entry(org: Path, date: str, spec: Spec, model: MaturityModel = V1, *, repos: str | None = None,
           **stamp: object) -> Path:
    entry = org / date
    entry.mkdir(parents=True)
    (entry / RUN_FILE).write_text(json.dumps(_stamp(date, model, **stamp)), encoding="utf-8")
    (entry / FLEET_DATA_FILE).write_text(dumps_fleet_data(_data(date, spec, model)), encoding="utf-8")
    (entry / "repos.jsonl").write_text(dumps_rows(_rows(spec)) if repos is None else repos, encoding="utf-8")
    return entry


def _history(tmp_path: Path, specs: tuple[Spec, ...] = (BASE, BASE, BASE), model: MaturityModel = V1) -> Path:
    org = tmp_path / "org"
    for date, spec in zip(DATES, specs):
        _entry(org, date, spec, model)
    return org


def _doc(org: Path, model: MaturityModel, spec: Spec = BASE, date: str = NOW) -> fa.Achievements:
    earlier = earlier_runs(org, _stamp(date, model), frozenset(spec), model=model, key=KEY)
    return fa.build_achievements(fa.HistoryRun(date, _data(date, spec, model)), earlier, model)


def _earned(doc: fa.Achievements, run: str | None = None) -> list[tuple[str, str, str, str | None]]:
    return [(a["run"], a["kind"], a["unit"], a["evidence"]["row"]) for a in doc["achievements"]
            if run is None or a["run"] == run]


# --- re-score -------------------------------------------------------------------


def test_rescore_with_same_criteria_reproduces_stored_cells() -> None:
    stored = _data(DATES[0], BASE, V1)
    again = fr.rescore(stored, _rows(BASE), SAME, KEY)
    assert again is not None
    assert again["run"]["levels_version"] == "fleet-data-2"
    assert {**again, "run": stored["run"]} == stored


def test_rescore_recomputes_the_unit_roll_ups() -> None:
    again = fr.rescore(_data(DATES[0], BASE, V1), _rows(BASE), V2, KEY)
    fresh = _data(DATES[0], BASE, V2)
    assert again is not None
    assert again["views"] == fresh["views"] and again["repos"] == fresh["repos"]
    assert again["model"] == fresh["model"]
    # grp/sub/a dropped from docs 2 to 1 (grade B no longer meets level 2)
    a_docs = next(c for r in again["repos"] if r["key"] == "grp/sub/a" for c in r["rows"] if c["row"] == "docs")
    assert a_docs["level"] == 1


def test_rescore_returns_none_when_keys_do_not_join() -> None:
    stored = _data(DATES[0], BASE, V1, Privacy(salt="A"))
    assert fr.rescore(stored, _rows(BASE), V2, Privacy(salt="B").key) is None
    assert fr.rescore(stored, _rows(BASE), V2, Privacy(salt="A").key) is not None


def test_signal_aliases_read_a_renamed_signal_under_its_new_name() -> None:
    renamed = _model(LEVELS.replace('version = "fleet-data-1"', 'version = "fleet-data-2"')
                     .replace('signal = "ci.required"', 'signal = "ci.enforced"'))
    stored = _data(DATES[0], BASE, V1)
    plain = fr.rescore(stored, _rows(BASE), renamed, KEY)
    aliased = fr.rescore(stored, _rows(BASE), renamed, KEY, {"ci.required": "ci.enforced"})
    assert plain is not None and aliased is not None
    assert fr.recount(stored, aliased, renamed, DATES[0], "fleet-data-1")["lines"] == []
    assert {line["direction"] for line in fr.recount(stored, plain, renamed, DATES[0], "fleet-data-1")["lines"]} \
        == {fr.NOT_MEASURED}
    assert fr.aliased({"ci": {"enforced": False, "required": True}}, {"ci.required": "ci.enforced"}) \
        == {"ci": {"enforced": False, "required": True}}  # never overwrites the new name


def test_the_783_config_only_renames_are_aliased_so_a_stored_scan_reads_the_new_names() -> None:
    assert fr.SIGNAL_ALIASES == {"ci_checks.test_or_lint_runs": "ci_checks.test_or_lint_configured",
                                 "coverage_gate.enforced": "coverage_gate.configured"}
    stored = {"ci_checks": {"available": True, "test_or_lint_runs": True},
              "coverage_gate": {"available": True, "enforced": False}}
    read = fr.aliased(stored)
    assert read["ci_checks"]["test_or_lint_configured"] is True
    assert read["coverage_gate"]["configured"] is False
    assert stored == {"ci_checks": {"available": True, "test_or_lint_runs": True},
                      "coverage_gate": {"available": True, "enforced": False}}  # input untouched


def test_a_model_reading_a_renamed_signal_gives_no_recount_over_scans_stored_under_the_old_name(
        monkeypatch: pytest.MonkeyPatch) -> None:
    # The old model as 2.1.0 loaded it, before #970 read an old path under its new name.
    monkeypatch.setattr("lib.levels.dealias_model", lambda data: data)
    old = _model(LEVELS.replace('{ signal = "ci.required", op = "eq", value = true }',
                                '{ signal = "coverage_gate.enforced", op = "eq", value = true }'))
    new = _model(LEVELS.replace('version = "fleet-data-1"', 'version = "fleet-data-2"')
                 .replace('{ signal = "ci.required", op = "eq", value = true }',
                          '{ signal = "coverage_gate.configured", op = "eq", value = true }'))
    monkeypatch.undo()
    rows = _rows(BASE)
    for row in rows:  # a scan stored before #783: the gate under its old name
        scans: dict[str, Any] = row["scans"]  # type: ignore[assignment]
        scans["coverage_gate"] = {"available": True, "enforced": scans["ci"]["required"]}
    stored = build_fleet_data(_stamp(DATES[0], old), rows, old,
                              build_trees(sorted(BASE), empty_map(), {}, {}), privacy=Privacy())
    rescored = fr.rescore(stored, rows, new, KEY)
    assert rescored is not None
    assert fr.recount(stored, rescored, new, DATES[0], "fleet-data-1")["lines"] == []
    plain = fr.rescore(stored, rows, new, KEY, {})
    assert plain is not None and fr.recount(stored, plain, new, DATES[0], "fleet-data-1")["lines"]


# --- the history reader ---------------------------------------------------------


def test_earlier_runs_rescores_an_entry_whose_only_difference_is_the_model_version(tmp_path: Path) -> None:
    org = _history(tmp_path)
    runs = earlier_runs(org, _stamp(NOW, V2), frozenset(BASE), model=V2, key=KEY)
    assert [r.run_id for r in runs] == list(DATES)
    assert all(r.rescored_from == "fleet-data-1" and r.stored is not None for r in runs)
    assert all(r.data["run"]["levels_version"] == "fleet-data-2" for r in runs)
    assert not any((org / d / "fleet-data.json").read_text(encoding="utf-8").count("fleet-data-2") for d in DATES)


def test_earlier_runs_still_refuses_other_differences_alongside_a_model_change(tmp_path: Path) -> None:
    org = tmp_path / "org"
    _entry(org, DATES[0], BASE, window="1y")
    _entry(org, DATES[1], BASE)
    runs = earlier_runs(org, _stamp(NOW, V2), frozenset(BASE), model=V2, key=KEY)
    assert [r.run_id for r in runs] == [DATES[1]]


@pytest.mark.parametrize("repos", [
    "".join(json.dumps({"repo": r}) + "\n" for r in sorted(BASE)),  # stub lines with no scans
    None,  # the file is missing
    "{not json\n",
])
def test_earlier_runs_passes_over_entry_without_readable_scans(tmp_path: Path, repos: str | None) -> None:
    org = tmp_path / "org"
    entry = _entry(org, DATES[0], BASE, repos=repos or "")
    if repos is None:
        (entry / "repos.jsonl").unlink()
    assert earlier_runs(org, _stamp(NOW, V2), frozenset(BASE), model=V2, key=KEY) == []


def test_earlier_runs_without_model_keeps_old_refusal(tmp_path: Path) -> None:
    org = _history(tmp_path)
    assert earlier_runs(org, _stamp(NOW, V2), frozenset(BASE)) == []
    assert len(earlier_runs(org, _stamp(NOW, V1), frozenset(BASE))) == 3  # same version: read as stored


# --- the replay -------------------------------------------------------------------

MOVES: tuple[Spec, ...] = (BASE, {**BASE, "grp/sub/b": (10, "C", True)},  # b: ci 0 -> 1 under v1
                           {**BASE, "grp/sub/b": (10, "A", True)})  # b: docs 1 -> 2


def test_streak_and_level_up_history_survive_a_toml_only_bump(tmp_path: Path) -> None:
    org = _history(tmp_path, MOVES)
    now_spec = {**BASE, "grp/sub/b": (10, "A", True), "grp/c": (10, "A", True)}
    bumped = _doc(org, SAME, now_spec)
    runs = [fa.HistoryRun(d, _data(d, s, V1)) for d, s in zip(DATES, MOVES)]
    same = fa.build_achievements(fa.HistoryRun(NOW, _data(NOW, now_spec, V1)), runs, V1)
    assert bumped["runs"] == [*DATES, NOW]
    assert bumped["achievements"] == same["achievements"]
    assert ("2026-11-15", "streak", "ownership/grp/sub", None) in _earned(bumped)
    assert bumped["recount"] is not None and bumped["recount"]["lines"] == []


def test_a_new_signal_row_is_neutral_not_a_slip(tmp_path: Path) -> None:
    org = _history(tmp_path, MOVES)
    now_spec = {**BASE, "grp/sub/b": (10, "A", True), "grp/c": (10, "A", True)}
    doc = _doc(org, V3, now_spec)
    assert doc["runs"] == [*DATES, NOW]
    assert not [e for e in _earned(doc) if e[3] == "ci"]  # ci is unknown in every run: nothing earned
    assert ("2026-11-15", "level_up", "grp/sub/b", "docs") in _earned(doc)  # docs moves kept
    assert ("2026-11-22", "level_up", "grp/c", "docs") in _earned(doc, NOW)
    lines = doc["recount"]["lines"] if doc["recount"] else []
    assert {line["direction"] for line in lines if line["row"] == "ci"} == {fr.NOT_MEASURED}
    assert all(line["attributed_to"] == fr.RULES for line in lines)


def test_rescored_replay_is_byte_identical_on_rerun(tmp_path: Path) -> None:
    org = _history(tmp_path, MOVES)
    assert fa.dumps_achievements(_doc(org, V2)) == fa.dumps_achievements(_doc(org, V2))


def test_a_repository_caused_unknown_after_a_rescored_entry_breaks_its_streak(tmp_path: Path) -> None:
    org = _history(tmp_path)
    unknown = {**BASE, "grp/sub/a": (None, None, True)}  # a's docs block goes unavailable this run
    doc = _doc(org, V2, unknown)
    later = earlier_runs(org, _stamp(NOW, V2), frozenset(BASE), model=V2, key=KEY)
    runs = [*later, fa.HistoryRun(NOW, _data(NOW, unknown, V2)),
            fa.HistoryRun("2026-11-29", _data("2026-11-29", BASE, V2))]
    then = fa.build_achievements(runs[-1], runs[:-1], V2)
    assert ("2026-11-15", "streak", "grp/sub/a", None) in _earned(doc)
    # a held ci the whole time, so without the lapse its streak would reach 5 on 11-29
    assert ("2026-11-29", "streak", "grp/sub/a", None) not in _earned(then)
    assert ("2026-11-29", "streak", "grp/sub/b", None) in _earned(then)
    assert fa.unknown_move({"a": {"r": _cell(1)}}, {"a": {"r": _cell(None)}}, "a", "r", frozenset()) == fa.LAPSE
    assert fa.unknown_move({"a": {"r": _cell(None)}}, {"a": {"r": _cell(1)}}, "a", "r", frozenset()) == fa.GAIN
    assert fa.unknown_move({"a": {"r": _cell(None)}}, {"a": {"r": _cell(1)}}, "a", "r",
                           frozenset({("a", "r")})) is None  # the rules made it unknown
    assert fa.unknown_move({"a": {"r": _cell(1)}}, {"a": {"r": _cell(None, "not_scanned")}}, "a", "r",
                           frozenset()) is None  # a failed scan stays neutral


def _cell(level: int | None, reason: str | None = None) -> Cell:
    return {"row": "r", "status": "level" if level is not None else "unknown", "level": level, "top_level": 2,
            "signal": None, "value": None, "reason": reason if level is None else None}


# --- the Recount ------------------------------------------------------------------

TEMPLATE = re.compile(r"^`[^`]+` .+: level \S+ -> \S+ under maturity model \S+ \(was \S+\)\. .+: `[^`]+`\. "
                      r"The repository did not change\.$")


def test_recount_appears_on_first_run_under_new_version(tmp_path: Path) -> None:
    doc = _doc(_history(tmp_path), V2)
    assert doc["recount"] is not None
    assert (doc["recount"]["from_version"], doc["recount"]["to_version"], doc["recount"]["run"]) == (
        "fleet-data-1", "fleet-data-2", DATES[-1])


def test_recount_absent_on_the_next_run(tmp_path: Path) -> None:
    org = _history(tmp_path)
    _entry(org, NOW, BASE, V2)
    doc = _doc(org, V2, date="2026-11-29")
    assert doc["recount"] is None
    assert doc["runs"] == [*DATES, NOW, "2026-11-29"]
    assert "### Recount" not in credit_section(doc, V2)


def test_recount_lists_both_directions_with_one_template(tmp_path: Path) -> None:
    doc = _doc(_history(tmp_path), V2)
    assert doc["recount"] is not None
    lines = {(line["repo"], line["row"]): line for line in doc["recount"]["lines"]}
    down, up = lines[("grp/sub/a", "docs")], lines[("grp/sub/b", "ci")]
    assert (down["direction"], down["cause"], down["signal"]) == (fr.DOWN, "Now checked", "docs.grade")
    assert (up["direction"], up["cause"], up["signal"]) == (fr.UP, "No longer checked", "ci.required")
    assert (down["from_level"], down["to_level"], up["from_level"], up["to_level"]) == (2, 1, 0, 1)
    assert all(TEMPLATE.match(line["text"]) for line in lines.values())
    assert down["text"] == ("`grp/sub/a` Instruction docs: level 2 -> 1 under maturity model fleet-data-2 "
                            "(was fleet-data-1). Now checked: `docs.grade`. The repository did not change.")
    assert ("grp/two/d", "docs") not in lines  # unchanged under the new rules


def test_recount_unknown_to_known_and_known_to_unknown_are_attributed_to_the_rules(tmp_path: Path) -> None:
    measured = _model(LEVELS.replace('version = "fleet-data-1"', 'version = "fleet-data-2"')
                      .replace('{ signal = "docs.lines", op = "gt", value = 0 }',
                               '{ signal = "ci.required", op = "eq", value = true }')
                      .replace('{ signal = "docs.grade", op = "in", value = ["A", "B"] }',
                               '{ signal = "ci.required", op = "eq", value = true }'))
    spec = {**BASE, "grp/c": (None, None, True)}  # docs unavailable: unknown under v1
    org = _history(tmp_path, (spec, spec, spec))
    up = {(ln["repo"], ln["row"]): ln for ln in (_doc(org, measured, spec)["recount"] or {"lines": []})["lines"]}
    assert (up[("grp/c", "docs")]["direction"], up[("grp/c", "docs")]["cause"]) == (fr.NOW_MEASURED, "Now measured")
    down = {(ln["repo"], ln["row"]): ln for ln in (_doc(org, V3, spec)["recount"] or {"lines": []})["lines"]}
    line = down[("grp/sub/a", "ci")]
    assert (line["direction"], line["cause"], line["signal"]) == (
        fr.NOT_MEASURED, "Not measured under the new rules", "ci_checks.test_or_lint_configured")
    assert line["text"].startswith("`grp/sub/a` CI gate: level 1 -> unknown under maturity model fleet-data-3")
    assert {ln["attributed_to"] for ln in [*up.values(), *down.values()]} == {fr.RULES}


def test_recount_never_moves_streak_reached_or_best(tmp_path: Path) -> None:
    org = _history(tmp_path)
    doc = _doc(org, V2)
    same = _doc(_history(tmp_path / "same"), SAME)
    # repositories unchanged: whatever the rule change, this run earns exactly what an unchanged model gives
    assert [a for a in doc["achievements"] if a["run"] == NOW] == []
    assert [a for a in same["achievements"] if a["run"] == NOW] == []
    assert not [a for a in doc["achievements"] if a["previous_run"] == DATES[-1]]
    assert doc["recount"] is not None and doc["recount"]["lines"]


def test_report_recount_section_renders_after_earned(tmp_path: Path) -> None:
    section = credit_section(_doc(_history(tmp_path), V2), V2)
    assert section.index("### Earned this run") < section.index("### Recount") < section.index("### Next level-ups")
    assert "`grp/sub/b` CI gate: level 0 -> 1" in section
    recount = section[section.index("### Recount"):section.index("### Next level-ups")]
    # raised moves first
    assert recount.index("No longer checked") < recount.index("Now checked")
    for word in ("regress", "lost", "broke", "behind", "worst", "fail", "bad", "rank", "lapse"):
        assert word not in section.lower(), word


def test_achievements_schema_accepts_recount(tmp_path: Path) -> None:
    schema = json.loads(render())
    with_recount = json.loads(fa.dumps_achievements(_doc(_history(tmp_path), V2)))
    assert with_recount["recount"]["lines"]
    jsonschema.validate(with_recount, schema)
    jsonschema.validate({**with_recount, "recount": None}, schema)
    bad = {**with_recount["recount"]["lines"][0], "direction": "lapse"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**with_recount, "recount": {**with_recount["recount"], "lines": [bad]}}, schema)


def test_a_row_new_to_the_model_that_rescores_unknown_is_rule_caused(tmp_path: Path) -> None:
    added = _model(LEVELS.replace('version = "fleet-data-1"', 'version = "fleet-data-2"') + """
[[rows]]
id = "lint"
title = "Lint gate"
scope = "team"
aggregation = "min"

  [[rows.levels]]
  level = 1
  criteria = [{ signal = "lint.present", op = "eq", value = true }]
""")
    stored = _data(DATES[0], BASE, V1)
    again = fr.rescore(stored, _rows(BASE), added, KEY)
    assert again is not None
    caused = fr.rule_unknowns(stored, again)
    assert {(repo, "lint") for repo in BASE} <= caused
    # this run measures lint: the move from the rule-caused unknown earns no streak step
    then = {"grp/c": {"lint": _cell(None)}}
    assert fa.unknown_move(then, {"grp/c": {"lint": _cell(1)}}, "grp/c", "lint", caused) is None


def test_report_keeps_the_unknown_rule_when_no_run_is_rescored(tmp_path: Path) -> None:
    org = tmp_path / "org"
    _entry(org, NOW, BASE, V2)
    doc = _doc(org, V2, date="2026-11-29")
    assert doc["rescored_runs"] == []
    plain = credit_section(doc, V2)
    assert "An unknown level never earns or loses anything." in plain
    assert "An unknown level never earns" not in credit_section(_doc(_history(tmp_path / "b"), V2), V2)


def test_report_drops_the_unknown_rule_while_rescored_runs_are_replayed(tmp_path: Path) -> None:
    org = _history(tmp_path)
    _entry(org, NOW, BASE, V2)
    doc = _doc(org, V2, date="2026-11-29")  # second run under v2: no Recount, older runs still re-scored
    assert doc["recount"] is None and doc["rescored_runs"] == list(DATES)
    section = credit_section(doc, V2)
    assert "An unknown level never earns or loses anything." not in section
    assert "starts its streak again" in section and "### Recount" not in section


def test_a_signal_absent_from_stored_scans_is_rule_caused_even_when_already_unknown(tmp_path: Path) -> None:
    # grp/c's docs block is unavailable: unknown on docs.lines under v1; v4 reads docs.coverage,
    # which no stored scan holds, so the re-scored unknown is the rules' doing
    v4 = _model(LEVELS.replace('version = "fleet-data-1"', 'version = "fleet-data-4"')
                .replace('{ signal = "docs.lines", op = "gt", value = 0 }',
                         '{ signal = "docs.coverage", op = "gt", value = 0 }'))
    spec = {**BASE, "grp/c": (None, None, True)}
    stored = _data(DATES[0], spec, V1)
    again = fr.rescore(stored, _rows(spec), v4, KEY)
    assert again is not None
    caused = fr.rule_unknowns(stored, again)
    assert ("grp/c", "docs") in caused
    assert fa.unknown_move({"grp/c": {"docs": _cell(None)}}, {"grp/c": {"docs": _cell(1)}}, "grp/c", "docs",
                           caused) is None
    # still unknown on the same signal: the repository's own unknown, not the rules'
    same = fr.rescore(stored, _rows(spec), SAME, KEY)
    assert same is not None
    assert ("grp/c", "docs") not in fr.rule_unknowns(stored, same)


def test_a_bump_leaves_the_streaks_of_old_steps_unchanged(tmp_path: Path) -> None:
    # a's docs go unknown between two v1 runs: neutral under v1, and still neutral once both are re-scored
    specs = (BASE, {**BASE, "grp/sub/a": (None, None, True)}, BASE)
    org = _history(tmp_path, specs)
    native_runs = [fa.HistoryRun(d, _data(d, s, V1)) for d, s in zip(DATES, specs)]
    native = fa.build_achievements(fa.HistoryRun(NOW, _data(NOW, BASE, V1)), native_runs, V1)
    bumped = _doc(org, SAME)
    assert [r.rescored_from for r in earlier_runs(org, _stamp(NOW, SAME), frozenset(BASE), model=SAME, key=KEY)] \
        == ["fleet-data-1"] * 3
    streaks = [a for a in native["achievements"] if a["kind"] == fa.STREAK]
    assert streaks and streaks == [a for a in bumped["achievements"] if a["kind"] == fa.STREAK]
    assert bumped["achievements"] == native["achievements"]
