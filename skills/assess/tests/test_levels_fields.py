"""lib/scoring/levels_fields.py: required truth tags, the legacy layer alias and the additive fields (#843)."""
from __future__ import annotations

import copy
import tomllib
from pathlib import Path
from typing import Any

import pytest

import lib.scoring.level_tally as lt
from lib.scoring.level_signals import DEFAULT_LEVELS, signal_path_validator
from lib.scoring.levels import LevelsError, MixedTruthError, evaluate_row, known_paths, load_levels, parse_levels
from lib.scoring.levels_fields import LEGACY_LAYER_TRUTH, LEGACY_ROW_TRUTH, TRUTH_TAGS, legacy_truth
from test_fleet_cli import fleet_cli

LEGACY = Path(__file__).parent / "fixtures" / "levels" / "levels.toml"


def _default() -> dict[str, Any]:
    return tomllib.loads(DEFAULT_LEVELS.read_text(encoding="utf-8"))


def _write(path: Path, data: dict[str, Any]) -> Path:
    """A minimal TOML writer for maturity-model tables (strings, numbers, bools, lists, tables)."""
    def scalar(v: Any) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, str):
            return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
        if isinstance(v, list):
            return "[" + ", ".join(scalar(x) for x in v) + "]"
        if isinstance(v, dict):
            return "{ " + ", ".join(f"{k} = {scalar(x)}" for k, x in v.items()) + " }"
        return repr(v)

    lines = [f"version = {scalar(data['version'])}"]
    for row in data["rows"]:
        lines.append("[[rows]]")
        lines += [f"{k} = {scalar(v)}" for k, v in row.items() if k != "levels"]
        for level in row["levels"]:
            lines.append("[[rows.levels]]")
            lines += [f"{k} = {scalar(v)}" for k, v in level.items()]
    for step in data.get("steps", []):
        lines.append("[[steps]]")
        lines += [f"{k} = {scalar(v)}" for k, v in step.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _model(*levels: dict[str, Any], row_id: str = "r") -> dict[str, Any]:
    return {"version": "v1", "rows": [{"id": row_id, "title": "R", "scope": "team", "aggregation": "min",
                                       "levels": [{"level": n, **lv} for n, lv in enumerate(levels, 1)]}]}


def _crit(signal: str = "doc_staleness.summary.stale_pct", value: Any = 0) -> list[dict[str, Any]]:
    return [{"signal": signal, "op": "eq", "value": value}]


# --- truth on the shipped model --------------------------------------------------


def test_every_default_condition_carries_known_truth_tags() -> None:
    model = load_levels(DEFAULT_LEVELS, signal_path_validator())
    for row in model["rows"]:
        for level in row["levels"]:
            assert level["truth"], (row["id"], level["level"])
            assert set(level["truth"]) <= set(TRUTH_TAGS)
    raw = _default()
    assert all("truth" in lv for row in raw["rows"] for lv in row["levels"])  # declared, not aliased


def test_step_gates_keep_their_condition_names() -> None:
    gates = {g["id"] for step in load_levels(DEFAULT_LEVELS)["steps"] for g in step["gates"]}
    assert gates == {"instructions", "ci_runs_tests", "coverage_gate"}


# --- the legacy alias, rule A ------------------------------------------------------


def test_a_model_with_no_truth_resolves_through_the_layer_alias() -> None:
    raw = _default()
    for row in raw["rows"]:
        for lv in row["levels"]:
            del lv["truth"]
    model = parse_levels(raw)
    for row in model["rows"]:
        for lv in row["levels"]:
            assert lv["truth"] == legacy_truth(row["id"]) != []


@pytest.mark.parametrize(("row_id", "tags"), [
    *((f"layer-{n}-x", list(t)) for n, t in LEGACY_LAYER_TRUTH.items()),
    *((r, list(t)) for r, t in LEGACY_ROW_TRUTH.items()),
    ("layer-9-x", []), ("layer-x", []), ("two-factor", []), ("players-1", []),
])
def test_the_alias_maps_by_layer_number_or_named_row(row_id: str, tags: list[str]) -> None:
    assert legacy_truth(row_id) == tags


def test_a_caller_model_without_truth_still_loads() -> None:
    model = load_levels(LEGACY)
    assert all(lv["truth"] == [] for row in model["rows"] for lv in row["levels"])


def test_removing_one_truth_is_a_mixed_model_naming_file_row_and_level(tmp_path: Path) -> None:
    raw = _default()
    del raw["rows"][3]["levels"][1]["truth"]
    path = _write(tmp_path / "notruth.toml", raw)
    with pytest.raises(MixedTruthError) as exc:
        load_levels(path, signal_path_validator())
    message = str(exc.value)
    assert message.startswith("MixedTruthError:")
    assert str(path) in message and f"row {raw['rows'][3]['id']!r} level 2" in message
    assert "unknown key" not in message


def test_adding_one_truth_to_a_legacy_model_is_rejected_too(tmp_path: Path) -> None:
    raw = tomllib.loads(LEGACY.read_text(encoding="utf-8"))
    raw["rows"][0]["levels"][0]["truth"] = ["behaviour"]
    path = _write(tmp_path / "truth1.toml", raw)
    with pytest.raises(MixedTruthError, match=r"^MixedTruthError: .*truth1\.toml"):
        load_levels(path)


def test_the_fleet_cli_refuses_a_mixed_model_before_any_scan(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    raw = _default()
    del raw["rows"][0]["levels"][0]["truth"]
    path = _write(tmp_path / "notruth.toml", raw)
    code = fleet_cli.main([str(tmp_path / "nothing"), "--out", str(tmp_path / "out"), "--levels", str(path)])
    assert code == 2
    assert "MixedTruthError" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(("truth", "message"), [
    (["speed"], r"row 'r' level 1: unknown truth tag\(s\) 'speed'"),
    ([], r"row 'r' level 1: 'truth' is empty"),
    (["docs", "docs"], r"row 'r' level 1: 'truth' repeats a tag"),
    ("docs", r"row 'r' level 1: 'truth' must be an array"),
])
def test_a_bad_truth_fails_at_load_naming_row_level_and_cause(truth: Any, message: str) -> None:
    with pytest.raises(LevelsError, match=message):
        parse_levels(_model({"criteria": _crit(), "truth": truth}))


def test_a_multi_tagged_condition_keeps_every_tag_in_order() -> None:
    model = parse_levels(_model({"criteria": _crit(), "truth": ["docs", "behaviour"]}))
    assert model["rows"][0]["levels"][0]["truth"] == ["docs", "behaviour"]


# --- polarity ------------------------------------------------------------------------


def test_a_hazard_is_never_counted_and_is_marked() -> None:
    model = parse_levels(_model({"criteria": _crit(), "truth": ["docs"], "polarity": "hazard"},
                                {"criteria": _crit(), "truth": ["docs"]}))
    result = lt.level_result(model, {"doc_staleness": {"summary": {"stale_pct": 0}}})
    hazard, practice = result["conditions"]
    assert hazard["state"] == "met" and hazard["counted"] is False and hazard["polarity"] == "hazard"
    assert practice["counted"] is True and "polarity" not in practice
    assert result["tally"]["met"] == 1 and result["listed"] == ["r.1"]


def test_positive_polarity_is_the_default_and_changes_nothing() -> None:
    plain = parse_levels(_model({"criteria": _crit(), "truth": ["docs"]}))
    explicit = parse_levels(_model({"criteria": _crit(), "truth": ["docs"], "polarity": "positive"}))
    assert plain == explicit


def test_an_unknown_polarity_fails_at_load() -> None:
    with pytest.raises(LevelsError, match=r"row 'r' level 1: 'polarity' must be one of positive, hazard"):
        parse_levels(_model({"criteria": _crit(), "truth": ["docs"], "polarity": "harm"}))


# --- guarded_by ------------------------------------------------------------------------

CI = "ci_checks.enforced_check_runs"  # ci_executed: verified


def _guarded(guard_value: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    model = parse_levels({"version": "v1", "rows": [
        {"id": "ci", "title": "CI", "scope": "team", "aggregation": "min",
         "levels": [{"level": 1, "truth": ["behaviour"], "criteria": [{"signal": CI, "op": "eq", "value": True}],
                     "guarded_by": "guard.1"}]},
        {"id": "guard", "title": "Guard", "scope": "team", "aggregation": "min",
         "levels": [{"level": 1, "truth": ["behaviour"], "criteria": _crit()}]},
    ]})
    data = {"ci_checks": {"enforced_check_runs": True}, "doc_staleness": {"summary": {"stale_pct": guard_value}}}
    result = lt.level_result(model, data)
    return result["conditions"][0], result["tally"]


def test_a_guarded_condition_is_verified_only_while_its_guard_is_met() -> None:
    held, tally_held = _guarded(0)
    lapsed, tally_lapsed = _guarded(5)
    assert held["grade"] == "verified" and lapsed["grade"] == "measured"
    assert held["counted"] and lapsed["counted"] and held["state"] == lapsed["state"] == "met"
    assert (tally_held["met"], tally_lapsed["met"]) == (2, 1)  # only the guard's own state moved


@pytest.mark.parametrize(("guard", "message"), [
    ("no-such.1", r"condition 'r.1': 'guarded_by' names unknown condition 'no-such.1'"),
    ("r.1", r"condition 'r.1': 'guarded_by' names the condition itself"),
    ("", r"row 'r' level 1: 'guarded_by' must be one condition id"),
    (["r.2"], r"row 'r' level 1: 'guarded_by' must be one condition id"),
])
def test_a_bad_guard_fails_at_load_naming_it(guard: Any, message: str) -> None:
    with pytest.raises(LevelsError, match=message):
        parse_levels(_model({"criteria": _crit(), "truth": ["docs"], "guarded_by": guard},
                            {"criteria": _crit(), "truth": ["docs"]}))


# --- eq_signal ---------------------------------------------------------------------------


def _eq_row(op: str = "eq") -> Any:
    model = parse_levels(_model({"truth": ["contracts"],
                                 "criteria": [{"signal": "a.x", "op": op, "eq_signal": "b.x"}]}))
    return model["rows"][0]


@pytest.mark.parametrize(("data", "status", "reason"), [
    ({"a": {"x": "h1"}, "b": {"x": "h1"}}, "met", None),
    ({"a": {"x": "h1"}, "b": {"x": "h2"}}, "unmet", None),
    ({"a": {"x": "h1"}}, "unknown", "unavailable"),
    ({"b": {"x": "h1"}}, "unknown", "unavailable"),
    ({"a": {"x": 1}, "b": {"x": "1"}}, "unknown", "type_mismatch"),
    ({"a": {"x": [1]}, "b": {"x": [1]}}, "unknown", "type_mismatch"),
])
def test_eq_signal_compares_two_measured_signals(data: dict[str, Any], status: str, reason: str | None) -> None:
    outcome = evaluate_row(_eq_row(), data)["levels"][0]
    if status == "met":
        assert outcome["met"] is True and outcome["unmet"] == []
    else:
        (crit,) = outcome["unmet"]
        assert (crit["status"], crit["reason"]) == (status, reason)


def test_eq_signal_with_ne() -> None:
    assert evaluate_row(_eq_row("ne"), {"a": {"x": 1}, "b": {"x": 2}})["levels"][0]["met"] is True


def test_the_parsed_eq_signal_criterion_keeps_the_path_as_its_value() -> None:
    (criterion,) = _eq_row()["levels"][0]["criteria"]
    assert criterion == {"signal": "a.x", "op": "eq", "value": "b.x", "eq_signal": "b.x"}


@pytest.mark.parametrize(("criterion", "message"), [
    ({"signal": "a.x", "op": "gt", "eq_signal": "b.x"}, "'eq_signal' needs op eq or ne"),
    ({"signal": "a.x", "op": "eq", "eq_signal": "b.x", "value": 1}, "takes no 'value'"),
    ({"signal": "a.x", "op": "eq", "eq_signal": "b..x"}, "'eq_signal' must be a signal path"),
])
def test_a_bad_eq_signal_fails_at_load(criterion: dict[str, Any], message: str) -> None:
    with pytest.raises(LevelsError, match=message):
        parse_levels(_model({"truth": ["docs"], "criteria": [criterion]}))


def test_the_path_validator_reads_the_second_signal_too() -> None:
    data = _model({"truth": ["docs"], "criteria": [{"signal": "a.x", "op": "eq", "eq_signal": "b.x"}]})
    with pytest.raises(LevelsError, match=r"team:b\.x \(row 'r'\)"):
        parse_levels(data, known_paths([], ["a.x"]))


# --- the defaults change nothing ---------------------------------------------------------


def test_the_documented_defaults_leave_the_default_results_unchanged() -> None:
    raw = _default()
    with_defaults = copy.deepcopy(raw)
    for row in with_defaults["rows"]:
        for lv in row["levels"]:
            lv["polarity"] = "positive"
    data = {"doc_staleness": {"summary": {"stale_pct": 10}}, "ci_checks": {"enforced_check_runs": True}}
    assert lt.level_result(parse_levels(with_defaults), data) == lt.level_result(parse_levels(raw), data)


def test_the_hazard_marker_survives_counts_only() -> None:
    from lib.counts_only.content_free import counts_only

    model = parse_levels(_model({"criteria": _crit(), "truth": ["docs"], "polarity": "hazard"}))
    level = lt.level_result(model, {"doc_staleness": {"summary": {"stale_pct": 0}}})
    assert counts_only({"level": level})["level"] == level


@pytest.mark.parametrize(("data", "missing", "reason"), [
    ({"a": {"x": 1}}, ["b.x"], "block_absent"),
    ({"b": {"x": 1}}, ["a.x"], "block_absent"),
    ({"a": {"x": 1}, "b": {"available": False, "reason": "network"}}, ["b.x"], "needs_platform"),
])
def test_an_unavailable_side_is_the_signal_named(data: dict[str, Any], missing: list[str], reason: str) -> None:
    row = _eq_row()
    assert evaluate_row(row, data)["missing_signals"] == missing
    model = parse_levels(_model({"truth": ["contracts"],
                                 "criteria": [{"signal": "a.x", "op": "eq", "eq_signal": "b.x"}]}))
    assert lt.level_result(model, data)["not_measured"] == [{"id": "r.1", "reason": reason}]


def test_the_catalog_never_counts_a_hazard() -> None:
    from lib import catalog

    raw = _default()
    raw["rows"][5]["levels"][0]["polarity"] = "hazard"  # layer-5-ci.1, a counted ci_executed signal
    signals = {"ci_checks.enforced_check_runs": {"evidence": "ci_executed", "title": "CI"}}
    rows = catalog._rows(parse_levels(raw), signals, {}, {})  # type: ignore[arg-type]
    assert rows[5]["levels"][0]["criteria"][0]["counts"] is False
    assert rows[5]["levels"][1]["criteria"][0]["grade"] is not None


def test_next_step_wording_names_both_paths_of_an_eq_signal_criterion() -> None:
    from lib.fleet.fleet_next_steps import _step

    (criterion,) = _eq_row()["levels"][0]["criteria"]
    text = "\n".join(_step(1, criterion, ["repo"]))
    assert "`a.x`" in text and "`b.x`" in text and "No repository-native wording" in text


def test_an_empty_set_on_the_eq_signal_side_takes_its_code() -> None:
    model = parse_levels(_model({"truth": ["contracts"], "criteria": [
        {"signal": "a.x", "op": "eq", "eq_signal": "stats_summary.loc_by_class.source.p95"}]}))
    data = {"a": {"x": 1}, "stats_summary": {"loc_by_class": {"source": {"files_scored": 0, "p95": None}}}}
    (crit,) = evaluate_row(model["rows"][0], data)["levels"][0]["unmet"]
    assert (crit["status"], crit["reason"]) == ("unknown", "no_source_files")


def test_a_guard_cannot_be_a_hazard() -> None:
    with pytest.raises(LevelsError, match=r"condition 'r.1': 'guarded_by' names hazard condition 'r.2'"):
        parse_levels(_model({"criteria": _crit(), "truth": ["docs"], "guarded_by": "r.2"},
                            {"criteria": _crit(), "truth": ["docs"], "polarity": "hazard"}))


@pytest.mark.parametrize(("other", "grade"), [
    ("ci_checks.lint_runs", "verified"),
    ("doc_staleness.summary.stale_pct", "measured"),
    ("no_such.signal", None),
])
def test_an_eq_signal_condition_takes_the_weaker_grade_of_its_two_signals(other: str, grade: str | None) -> None:
    model = parse_levels(_model({"truth": ["contracts"], "criteria": [
        {"signal": "ci_checks.enforced_check_runs", "op": "eq", "eq_signal": other}]}))
    assert lt.level_result(model, {})["conditions"][0]["grade"] == grade
