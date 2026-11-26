"""Tests for lib/fleet/fleet_opportunities.py and lib/fleet/fleet_opportunities_section.py (issue #746)."""
from __future__ import annotations

import json
import tomllib

from fleet_data_fixture import STAMP
from lib.fleet.runner import FLEET_ROW_VERSION, FleetRow
from lib.fleet.fleet_achievements import HistoryRun, build_achievements
from lib.card_facts import fully_in_place
from lib.fleet.fleet_data import FleetData, Privacy, build_fleet_data, dumps_fleet_data
from lib.fleet.fleet_diagrams import report_markdown
from lib.fleet.fleet_level import repo_level
from lib.fleet.fleet_opportunities import MIN_INCLUDERS, Opportunities, build_opportunities
from lib.fleet.fleet_opportunities_section import opportunities_section
from lib.fleet.fleet_rescore import rescore
from lib.scoring.headline import practices_held
from lib.fleet.fleet_units import build_trees, empty_map
from lib.scoring.levels import MaturityModel, parse_levels
from lib.run_context_types import JsonDict

SOURCE = "gitlab_project:grp/ci-templates"
LEVELS = """\
version = "fixture-c"

[[rows]]
id = "instr"
title = "Instructions"
scope = "team"
aggregation = "min"

  [[rows.levels]]
  level = 1
  criteria = [{ signal = "layer_grades.instruction_file_present", op = "eq", value = true }]

[[rows]]
id = "ci"
title = "CI"
scope = "team"
aggregation = "min"

  [[rows.levels]]
  level = 1
  criteria = [{ signal = "ci_checks.enforced_check_runs", op = "eq", value = true }]
"""


def model(text: str = LEVELS) -> MaturityModel:
    return parse_levels(tomllib.loads(text))


def _row(repo: str, *, instr: bool = False, ci: bool | None = False, sources: tuple[str, ...] = ()) -> FleetRow:
    scans: JsonDict = {
        "layer_grades": {"available": True, "instruction_file_present": instr},
        "ci_checks": ({"available": True, "enforced_check_runs": ci} if ci is not None
                      else {"available": False, "reason": "unavailable"}),
        "shared_tooling": {"available": True, "ci_source_ids": list(sources), "marketplace_ids": [],
                           "ci_includes": [{"id": i, "kind": "gitlab_project", "tracked": True} for i in sources]},
    }
    return {"fleet_row_version": FLEET_ROW_VERSION, "repo": repo, "group": "grp", "status": "ok",
            "reason": None, "signals_schema_version": 1, "scans": scans, "active": True}


def fleet(source: str = SOURCE) -> list[FleetRow]:
    """The I746-1 shape: four repositories include the template, d runs tests, d and e have instructions."""
    return [*(_row(f"grp/{x}", sources=(source,)) for x in "abc"),
            _row("grp/e", instr=True, sources=(source,)),
            _row("grp/d", instr=True, ci=True),
            _row("grp/ci-templates")]


def _opps(rows: list[FleetRow], m: MaturityModel | None = None, key: Privacy = Privacy()) -> Opportunities:
    found = build_opportunities(m or model(), rows, key.key)
    assert found is not None
    return found


def _data(rows: list[FleetRow], privacy: Privacy = Privacy(), m: MaturityModel | None = None) -> FleetData:
    repos = [r["repo"] for r in rows]
    return build_fleet_data(STAMP, rows, m or model(), build_trees(repos, empty_map(), {}, {}), privacy=privacy)


def test_biggest_lift_is_the_criterion_that_lifts_the_most_repositories() -> None:
    lift = _opps(fleet())["biggest_lift"]
    assert lift is not None
    assert (lift["row"], lift["level"], lift["signal"]) == ("ci", 1, "ci_checks.enforced_check_runs")
    assert lift["repo_count"] == 5
    assert lift["repos"] == ["grp/a", "grp/b", "grp/c", "grp/ci-templates", "grp/e"]
    assert lift["change"] and lift["done_when"]
    assert lift["text"].startswith("Completing level 1 on CI would lift 5 repositories")


def test_shared_template_names_the_source_and_the_repositories_it_would_lift() -> None:
    (template,) = _opps(fleet())["shared_templates"]
    assert template["source"] == SOURCE and template["kind"] == "gitlab_project"
    assert (template["row"], template["level"], template["includers"]) == ("ci", 1, 4)
    assert template["repos"] == ["grp/a", "grp/b", "grp/c", "grp/e"] and template["repo_count"] == 4
    assert "would reach level 1 on CI" in template["text"]


def test_in_place_counts_repositories_at_each_rows_top_level() -> None:
    # #1470: instr reads a declared signal, so d and e are not at its top level; ci is counted
    assert _opps(fleet())["in_place"] == [{"row": "instr", "at_top": 0, "measured": 6},
                                          {"row": "ci", "at_top": 1, "measured": 6}]


def test_a_declared_practice_reads_the_same_in_the_badge_and_the_fleet() -> None:
    """#1470: a repository whose instruction file is only declared is not fully in place on
    Instructions in its own badge, and the fleet's What's working count leaves it out too."""
    m = model()
    repo = _row("grp/d", instr=True, ci=True)
    result = repo_level(m, repo)
    held = [e["row"] for e in fully_in_place(result)]
    assert held == ["ci"]
    assert practices_held(result) == " · 1 practice fully in place"  # the badge summary's count
    opps = _opps([repo], m)
    assert [(p["row"], p["at_top"]) for p in opps["in_place"]] == [("instr", 0), ("ci", 1)]
    assert [p["row"] for p in opps["in_place"] if p["at_top"]] == held
    section = opportunities_section(_data([repo]), None)
    assert section is not None
    assert "- CI: 1 of 1 measured repository at the top level." in section
    assert "- Instructions:" not in section


def test_a_declared_practice_reads_the_same_on_the_radar_and_in_whats_working() -> None:
    """#1474: a unit holding one repository whose instruction file is only declared gives the
    same answer in the radar's per-unit ``met`` as in "What's working"."""
    repo = _row("grp/d", instr=True, ci=True)
    data = _data([repo])
    opps = data["opportunities"]
    assert opps is not None
    working = {p["row"]: p["at_top"] for p in opps["in_place"]}
    for view in data["views"]:
        for unit in view["units"]:
            met = {lv["row"]: lv["met"] for lv in unit["levels"]}
            assert met == working == {"instr": 0, "ci": 1}


def test_a_rescore_counts_met_by_the_same_rule_as_a_fresh_run() -> None:
    """#1474: the re-score rebuilds each unit's ``met`` from the level result too, so a stored run
    re-scored under the same model reads what a fresh run reads (the ci row counted, instr not)."""
    rows = [_row("grp/d", instr=True, ci=True), _row("grp/e", instr=True)]
    fresh = _data(rows)
    again = rescore(fresh, rows, model(), Privacy().key)
    assert again is not None
    met = [[lv["met"] for lv in u["levels"]] for v in again["views"] for u in v["units"]]
    assert met == [[lv["met"] for lv in u["levels"]] for v in fresh["views"] for u in v["units"]]
    assert met and all(m == [0, 1] for m in met)


def test_in_place_reads_the_level_results_passed_in() -> None:
    rows = fleet()
    results = {r["repo"]: repo_level(model(), r) for r in rows}
    passed, computed = build_opportunities(model(), rows, str, results), build_opportunities(model(), rows, str)
    assert passed is not None and computed is not None
    assert passed["in_place"] == computed["in_place"]


def test_an_unknown_level_never_counts_as_a_lift() -> None:
    rows = [*fleet(), _row("grp/u", instr=True, ci=None, sources=(SOURCE,))]  # its ci row is unknown
    opps = _opps(rows)
    assert opps["biggest_lift"] is not None and "grp/u" not in opps["biggest_lift"]["repos"]
    assert "grp/u" not in opps["shared_templates"][0]["repos"]
    assert opps["shared_templates"][0]["includers"] == 5  # it includes the source, but is not promised a lift


def test_a_level_needing_two_changes_is_not_a_single_lift() -> None:
    two = LEVELS.replace('criteria = [{ signal = "ci_checks.enforced_check_runs", op = "eq", value = true }]',
                         'criteria = [{ signal = "ci_checks.enforced_check_runs", op = "eq", value = true }, '
                         '{ signal = "layer_grades.instruction_file_present", op = "eq", value = true }]')
    opps = _opps(fleet(), model(two))
    lift = opps["biggest_lift"]
    assert lift is not None
    # a, b, c and ci-templates need both criteria for ci level 1; only e needs the CI change alone
    assert (lift["row"], lift["repos"]) == ("instr", ["grp/a", "grp/b", "grp/c", "grp/ci-templates"])
    assert [t["repos"] for t in opps["shared_templates"]] == [["grp/e"]]


def test_a_source_too_few_repositories_include_is_not_shared() -> None:
    rows = [_row("grp/a", sources=("gitlab_project:grp/solo",)), _row("grp/b")]
    assert MIN_INCLUDERS == 2
    assert _opps(rows)["shared_templates"] == []


def test_ties_follow_maturity_model_row_order() -> None:
    rows = [_row("grp/a"), _row("grp/b")]  # both rows lift both repositories
    lift = _opps(rows)["biggest_lift"]
    assert lift is not None and lift["row"] == "instr"


def test_a_step_gate_condition_is_named() -> None:
    m = model()
    m["steps"] = [{"step": 3, "name": "Unattended agent PRs",
                   "gates": [{"id": "ci_runs_tests", "row": "ci", "level": 1}]}]
    lift = _opps(fleet(), m)["biggest_lift"]
    assert lift is not None and (lift["gate"], lift["step"]) == ("ci_runs_tests", 3)
    assert "`ci_runs_tests` gate for Step 3" in lift["text"]


def test_no_lift_when_every_repository_is_at_the_top() -> None:
    opps = _opps([_row("grp/a", instr=True, ci=True)])
    assert opps["biggest_lift"] is None and opps["shared_templates"] == []
    section = opportunities_section(_data([_row("grp/a", instr=True, ci=True)]), None)
    assert section is not None and "No single change would lift" in section


def test_without_a_maturity_model_there_are_no_opportunities() -> None:
    assert build_opportunities(None, fleet(), str) is None
    data = build_fleet_data(STAMP, fleet(), None, [])
    assert data["opportunities"] is None and opportunities_section(data, None) is None


def test_counts_only_keeps_every_opportunity_field_and_names_no_rank() -> None:
    doc = json.loads(dumps_fleet_data(_data(fleet())))
    opps = doc["opportunities"]
    assert set(opps) == {"measured", "in_place", "biggest_lift", "shared_templates", "step_lifts"}
    assert all(set(lift) == {"condition", "row", "title", "gate", "step", "repo_count"}
               for lift in opps["step_lifts"])  # #941: counts only, no repository key
    assert set(opps["biggest_lift"]) == {"row", "level", "signal", "repo_count", "repos", "gate", "step",
                                         "change", "done_when", "why", "text"}
    assert set(opps["shared_templates"][0]) == {"source", "kind", "includers", "row", "level", "signal",
                                                "repo_count", "repos", "text"}
    assert opps["shared_templates"][0]["source"] == SOURCE  # shared_tooling ids stay readable
    assert opps["biggest_lift"]["repos"] == ["grp/a", "grp/b", "grp/c", "grp/ci-templates", "grp/e"]
    dumped = json.dumps(opps)
    assert "rank" not in dumped and "position" not in dumped


def test_a_hashed_run_names_no_repository_or_source() -> None:
    privacy = Privacy(salt="s")
    hashed = privacy.key(SOURCE)  # the scan already hashed the id under the run's salt
    dumped = dumps_fleet_data(_data(fleet(hashed), privacy))
    opps = json.loads(dumped)["opportunities"]
    assert "grp/" not in dumped and "ci-templates" not in dumped
    (template,) = opps["shared_templates"]
    assert template["source"] == hashed and template["kind"] == "gitlab_project"  # joins repos.jsonl, no rehash
    assert opps["biggest_lift"]["repos"] == sorted(privacy.key(r) for r in
                                                   ["grp/a", "grp/b", "grp/c", "grp/ci-templates", "grp/e"])
    section = opportunities_section(json.loads(dumped), None)
    assert section is not None and "grp/" not in section


def test_the_same_run_gives_the_same_bytes() -> None:
    data = _data(fleet())
    assert dumps_fleet_data(data) == dumps_fleet_data(_data(list(reversed(fleet()))))
    assert opportunities_section(data, None) == opportunities_section(_data(fleet()), None)


def test_the_section_opens_with_what_is_in_place_and_ranks_no_one() -> None:
    section = opportunities_section(_data(fleet()), None)
    assert section is not None
    lines = section[section.index("## Opportunities"):].splitlines()
    # the counts are the What's working section's (#1334); the opportunities point at it
    assert lines[2] == ("In place: the practices under [What's working across the fleet]"
                        "(#whats-working-across-the-fleet) above, out of 2; the changes below lift the most "
                        "repositories next.")
    working = section[:section.index("## Opportunities")]
    assert working.splitlines()[:5] == [
        "## What's working across the fleet", "",
        "In place: 0 of 2 practices at the top level in every measured repository; 1 held most widely.",
        "", "- CI: 1 of 6 measured repositories at the top level."]
    assert "Applies to `grp/a`, `grp/b`, `grp/c`, `grp/ci-templates`, `grp/e`." in section
    assert f"- `{SOURCE}` (CI: GitLab project include):" in section
    assert not any(line.lstrip()[:2] in {"1.", "2.", "3."} for line in lines)
    assert "Not shown: this run's achievements were not written." in section


def test_with_nothing_in_place_the_opportunities_do_not_point_at_an_empty_list() -> None:
    """#1470: when What's working lists no practice, the In place: line says so in its own words."""
    section = opportunities_section(_data([_row("grp/a"), _row("grp/b")]), None)
    assert section is not None
    working, opps = section.split("## Opportunities")
    assert "In place: 0 of 2 practices at the top level in any measured repository yet" in working
    line = opps.splitlines()[2]
    assert line == ("In place: no measured repository holds any of the 2 practices at the top level yet; "
                    "the changes below lift the most repositories first.")
    assert "What's working" not in line


def test_the_report_puts_opportunities_after_credit_and_before_diagrams() -> None:
    report = report_markdown([], credit="## What changed and who earned it\n", opportunities="## Opportunities\n")
    assert report.index("## What changed") < report.index("## Opportunities")


def _history(earlier_rows: list[FleetRow]) -> tuple[FleetData, list[HistoryRun]]:
    m = model()
    before = HistoryRun("2026-11-13", _data(earlier_rows, m=m))
    now = _data(fleet(), m=m)
    return now, [before]


def test_recent_progress_lists_units_that_reached_a_new_level_from_the_history() -> None:
    before = [r if r["repo"] != "grp/d" else _row("grp/d", instr=True, ci=False) for r in fleet()]
    now, earlier = _history(before)
    doc = build_achievements(HistoryRun("2026-11-20", now), earlier, model())
    section = opportunities_section(now, doc)
    assert section is not None
    assert "Since `2026-11-13`, from the run history (shown only, never counted):" in section
    assert "- `grp/d` reached level 1 on `ci`." in section
    # display only: fleet-data.json is the same with or without the history
    assert dumps_fleet_data(now) == dumps_fleet_data(_data(fleet()))


def test_recent_progress_on_the_starting_line_and_with_nothing_new() -> None:
    now = _data(fleet())
    first = build_achievements(HistoryRun("2026-11-20", now), [], model())
    assert "This run is the starting line" in (opportunities_section(now, first) or "")
    note = "this run is not kept in the run history (--no-history or --full)"
    unkept = build_achievements(HistoryRun("2026-11-20", now), [], model(), note=note)
    section = opportunities_section(now, unkept) or ""
    assert f"Not shown: {note}, so there is no earlier run" in section and "starting line" not in section
    _, earlier = _history(fleet())
    same = build_achievements(HistoryRun("2026-11-20", now), earlier, model())
    assert "No unit reached a new level since `2026-11-13`" in (opportunities_section(now, same) or "")


def test_a_level_repeating_a_signal_lifts_on_the_unmet_criterion() -> None:
    """A range check: two criteria on one signal; the lift is worded from the one unmet."""
    ranged = LEVELS.replace('criteria = [{ signal = "layer_grades.instruction_file_present", op = "eq", value = true }]',
                            'criteria = [{ signal = "nested.lines", op = "gte", value = 10 }, '
                            '{ signal = "nested.lines", op = "lt", value = 100 }]')
    big = _row("grp/a", ci=True)
    assert big["scans"] is not None
    big["scans"]["nested"] = {"available": True, "lines": 500}  # meets >= 10, not < 100
    opps = _opps([big], model(ranged))
    lift = opps["biggest_lift"]
    assert lift is not None and (lift["row"], lift["signal"]) == ("instr", "nested.lines")
    small = _row("grp/b", ci=True)
    assert small["scans"] is not None
    small["scans"]["nested"] = {"available": True, "lines": 1}  # meets < 100, not >= 10
    both = _opps([big, small], model(ranged))
    assert both["biggest_lift"] is not None and both["biggest_lift"]["repo_count"] == 1  # two criteria, one each


def test_a_lift_without_wording_names_its_criterion() -> None:
    plain = LEVELS.replace('signal = "ci_checks.enforced_check_runs", op = "eq", value = true',
                           'signal = "ci_checks.enforced_check_runs", op = "in", value = [true]')  # no wording
    data = _data(fleet(), m=model(plain))
    assert data["opportunities"] is not None and data["opportunities"]["biggest_lift"] is not None
    assert data["opportunities"]["biggest_lift"]["change"] is None
    section = opportunities_section(data, None) or ""
    assert "The criterion to meet: `ci_checks.enforced_check_runs`." in section
