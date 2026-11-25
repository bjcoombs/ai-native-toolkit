"""lib/scoring/levels.py: TOML maturity model loading and deterministic whole-level evaluation."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from lib.scoring.levels import (
    Group,
    LevelsError,
    dumps_result,
    evaluate,
    evaluate_row,
    known_paths,
    load_levels,
    parse_levels,
)

FIXTURES = Path(__file__).parent / "fixtures" / "levels"
LEVELS = FIXTURES / "levels.toml"

ORG_PATHS = {"two_factor_required", "default_branch_protection.required_reviews"}
TEAM_PATHS = {
    "instructions_grade",
    "test_pressure.ci_integrated",
    "skills_count",
    "coverage_report.line_rate",
}


def _group() -> Group:
    repos = {
        p.stem: json.loads(p.read_text(encoding="utf-8"))
        for p in sorted((FIXTURES / "repos").glob("*.json"))
    }
    settings = json.loads((FIXTURES / "settings.json").read_text(encoding="utf-8"))
    return {"name": "fixture-group", "settings": settings, "repos": repos}


def _row(levels_result: Any, row_id: str) -> Any:
    return next(r for r in levels_result["rows"] if r["id"] == row_id)


def _team_row(levels: list[Any], **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {"id": "r", "title": "R", "scope": "team", "aggregation": "min", "levels": levels}
    row.update(extra)
    return row


def _lv(n: int, *criteria: dict[str, Any]) -> dict[str, Any]:
    return {"level": n, "criteria": list(criteria) or [{"signal": "x", "op": "eq", "value": 1}]}


def _maturity_model(*rows: dict[str, Any]) -> dict[str, Any]:
    return {"version": "v1", "rows": list(rows)}


# --- fixture maturity model end to end ----------------------------------------------


def test_fixture_levels_reaches_expected_whole_levels() -> None:
    maturity_model = load_levels(LEVELS, known_paths(ORG_PATHS, TEAM_PATHS))
    result = evaluate(maturity_model, _group())

    assert result["levels_version"] == "fixture-2026.11"
    assert result["group"] == "fixture-group"
    org = _row(result, "two-factor")
    assert (org["status"], org["level"]) == ("level", 1)
    team = _row(result, "agent-ready")
    assert {n: ev["level"] for n, ev in team["repos"].items()} == {"alpha": 3, "beta": 2, "gamma": 3}
    assert (team["status"], team["level"]) == ("level", 2)
    assert team["unknown_repos"] == []


def test_unmet_criteria_are_exposed_per_level() -> None:
    result = evaluate(load_levels(LEVELS), _group())
    org_levels = _row(result, "two-factor")["levels"]
    assert org_levels[0] == {"level": 1, "met": True, "unmet": []}
    assert org_levels[1]["met"] is False
    (unmet,) = org_levels[1]["unmet"]
    assert unmet["signal"] == "default_branch_protection.required_reviews"
    assert (unmet["status"], unmet["actual"], unmet["value"]) == ("unmet", 0, 1)

    beta = _row(result, "agent-ready")["repos"]["beta"]
    assert [lv["met"] for lv in beta["levels"]] == [True, True, False]
    assert beta["levels"][2]["unmet"][0]["actual"] == 0.5


def test_removing_a_signal_turns_the_row_unknown_not_zero() -> None:
    group = _group()
    del group["repos"]["beta"]["instructions_grade"]
    team = _row(evaluate(load_levels(LEVELS), group), "agent-ready")

    beta = team["repos"]["beta"]
    assert (beta["status"], beta["level"]) == ("unknown", None)
    assert beta["missing_signals"] == ["instructions_grade"]
    assert beta["levels"][0]["met"] is None
    # beta could be anywhere from 0 to 3, so the minimum is not determined.
    assert (team["status"], team["level"]) == ("unknown", None)
    assert team["unknown_repos"] == ["beta"]
    assert team["missing_signals"] == ["instructions_grade"]


def test_missing_signal_above_a_failed_level_keeps_the_known_level() -> None:
    group = _group()
    group["repos"]["beta"]["instructions_grade"] = "F"
    del group["repos"]["beta"]["coverage_report"]
    beta = _row(evaluate(load_levels(LEVELS), group), "agent-ready")["repos"]["beta"]
    assert (beta["status"], beta["level"], beta["bounds"]) == ("level", 0, [0, 0])
    assert beta["missing_signals"] == ["coverage_report.line_rate"]


def test_definitely_unmet_level_one_with_missing_level_two_is_zero() -> None:
    levels = [_lv(1, {"signal": "a", "op": "eq", "value": True}), _lv(2, {"signal": "b", "op": "eq", "value": 1})]
    row = parse_levels(_maturity_model(_team_row(levels)))["rows"][0]
    ev = evaluate_row(row, {"a": False})
    assert (ev["status"], ev["level"], ev["bounds"]) == ("level", 0, [0, 0])
    assert ev["missing_signals"] == ["b"]
    assert [lv["met"] for lv in ev["levels"]] == [False, None]


def test_unknown_level_below_an_unmet_level_bounds_the_row() -> None:
    levels = [
        _lv(1, {"signal": "a", "op": "eq", "value": 1}),
        _lv(2, {"signal": "b", "op": "eq", "value": 1}),
        _lv(3, {"signal": "c", "op": "eq", "value": 1}),
    ]
    row = parse_levels(_maturity_model(_team_row(levels)))["rows"][0]
    ev = evaluate_row(row, {"a": 1, "c": 0})
    assert (ev["status"], ev["level"], ev["bounds"]) == ("unknown", None, [1, 2])
    ev = evaluate_row(row, {"c": 0})
    assert (ev["status"], ev["level"], ev["bounds"]) == ("unknown", None, [0, 2])


def test_org_row_without_group_settings_is_unknown() -> None:
    group = _group()
    group["settings"] = None
    org = _row(evaluate(load_levels(LEVELS), group), "two-factor")
    assert (org["status"], org["level"]) == ("unknown", None)
    assert org["missing_signals"] == ["default_branch_protection.required_reviews", "two_factor_required"]


def test_team_row_with_no_repositories_is_unknown() -> None:
    group = _group()
    group["repos"] = {}
    team = _row(evaluate(load_levels(LEVELS), group), "agent-ready")
    assert (team["status"], team["level"], team["repos"]) == ("unknown", None, {})


def test_rerun_is_byte_identical_regardless_of_input_order() -> None:
    maturity_model = load_levels(LEVELS)
    group = _group()
    first = dumps_result(evaluate(maturity_model, group))
    shuffled = copy.deepcopy(group)
    shuffled["repos"] = dict(reversed(list(shuffled["repos"].items())))
    assert dumps_result(evaluate(load_levels(LEVELS), shuffled)) == first
    assert first.endswith("\n") and '"levels_version": "fixture-2026.11"' in first


# --- signal resolution ------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "status"),
    [
        ({"block": {"available": False, "n": 5}}, "unknown"),
        ({"block": {"available": True, "n": None}}, "unknown"),
        ({"block": None}, "unknown"),
        ({"block": 3}, "unknown"),
        ({"block": {"available": True, "n": 5}}, "met"),
    ],
)
def test_unavailable_paths(data: dict[str, Any], status: str) -> None:
    row = parse_levels(_maturity_model(_team_row([_lv(1, {"signal": "block.n", "op": "gte", "value": 1})])))["rows"][0]
    ev = evaluate_row(row, data)
    outcome = ev["levels"][0]["unmet"][0] if ev["levels"][0]["unmet"] else None
    assert (outcome["status"] if outcome else "met") == status


def test_available_flag_of_unavailable_block_is_readable() -> None:
    crit = {"signal": "block.available", "op": "eq", "value": True}
    row = parse_levels(_maturity_model(_team_row([_lv(1, crit)])))["rows"][0]
    ev = evaluate_row(row, {"block": {"available": False}})
    assert (ev["status"], ev["level"]) == ("level", 0)


def test_unavailable_leaf_block() -> None:
    crit = {"signal": "block", "op": "eq", "value": True}
    row = parse_levels(_maturity_model(_team_row([_lv(1, crit)])))["rows"][0]
    assert evaluate_row(row, {"block": {"available": False}})["status"] == "unknown"


@pytest.mark.parametrize(
    ("op", "value", "actual"),
    [
        ("gte", 1, True),  # a bool is not a number, though True >= 1 in Python
        ("gte", 1, "high"),
        ("eq", "A", 1),
        ("in", [1, 2], "1"),
        ("eq", True, {"nested": 1}),
    ],
)
def test_type_mismatch_is_unknown_not_failed(op: str, value: Any, actual: Any) -> None:
    row = parse_levels(_maturity_model(_team_row([_lv(1, {"signal": "s", "op": op, "value": value})])))["rows"][0]
    ev = evaluate_row(row, {"s": actual})
    assert ev["status"] == "unknown"
    assert ev["levels"][0]["unmet"][0]["reason"] == "type_mismatch"


@pytest.mark.parametrize(
    ("op", "value", "actual", "met"),
    [
        ("eq", "A", "A", True),
        ("ne", "A", "B", True),
        ("lt", 3, 2, True),
        ("lte", 3, 3.0, True),
        ("gt", 3, 3, False),
        ("gte", 0.5, 0.49, False),
        ("in", ["A", "B"], "B", True),
        ("not_in", ["A", "B"], "B", False),
        ("eq", False, False, True),
    ],
)
def test_operators(op: str, value: Any, actual: Any, met: bool) -> None:
    row = parse_levels(_maturity_model(_team_row([_lv(1, {"signal": "s", "op": op, "value": value})])))["rows"][0]
    assert evaluate_row(row, {"s": actual})["level"] == (1 if met else 0)


def test_level_needs_every_lower_level() -> None:
    levels = [_lv(1, {"signal": "a", "op": "eq", "value": 1}), _lv(2, {"signal": "b", "op": "eq", "value": 1})]
    row = parse_levels(_maturity_model(_team_row(levels)))["rows"][0]
    ev = evaluate_row(row, {"a": 0, "b": 1})
    assert ev["level"] == 0
    assert [lv["met"] for lv in ev["levels"]] == [False, True]


# --- aggregation ------------------------------------------------------------


def _share_model(share: float) -> Any:
    levels = [_lv(1, {"signal": "n", "op": "gte", "value": 1}), _lv(2, {"signal": "n", "op": "gte", "value": 2})]
    return parse_levels(_maturity_model(_team_row(levels, aggregation="share", share=share)))


def _team(maturity_model: Any, repos: dict[str, Any]) -> Any:
    return evaluate(maturity_model, {"name": "g", "settings": None, "repos": repos})["rows"][0]


def test_share_aggregation_uses_exact_threshold() -> None:
    repos = {"a": {"n": 2}, "b": {"n": 2}, "c": {"n": 2}, "d": {"n": 0}}
    assert _team(_share_model(0.75), repos)["level"] == 2
    assert _team(_share_model(0.76), repos)["level"] == 0
    assert _team(_share_model(1), repos)["level"] == 0
    assert _team(_share_model(0.75), repos)["aggregation"] == {"rule": "share", "share": 0.75}


def test_share_level_reported_when_unknown_repo_cannot_change_it() -> None:
    repos = {"a": {"n": 2}, "b": {"n": 2}, "c": {"n": 2}, "d": {}}
    team = _team(_share_model(0.5), repos)
    assert (team["status"], team["level"]) == ("level", 2)
    assert team["unknown_repos"] == ["d"]
    assert team["missing_signals"] == ["n"]


def test_min_with_a_zero_repo_is_zero_even_with_unknowns() -> None:
    maturity_model = load_levels(LEVELS)
    group = _group()
    group["repos"]["beta"]["instructions_grade"] = "F"
    del group["repos"]["gamma"]["skills_count"]
    team = _row(evaluate(maturity_model, group), "agent-ready")
    assert (team["status"], team["level"], team["unknown_repos"]) == ("level", 0, ["gamma"])
    # A team row can name missing signals while its level is known.
    assert team["missing_signals"] == ["skills_count"]


# --- loader failures --------------------------------------------------------


def test_unknown_signal_path_fails_at_load_naming_it() -> None:
    with pytest.raises(LevelsError, match=r"team:coverage_report\.line_rate \(row 'agent-ready'\)"):
        load_levels(LEVELS, known_paths(ORG_PATHS, TEAM_PATHS - {"coverage_report.line_rate"}))


def test_validator_sees_scope() -> None:
    with pytest.raises(LevelsError, match="org:two_factor_required"):
        load_levels(LEVELS, known_paths(TEAM_PATHS, TEAM_PATHS | ORG_PATHS))


@pytest.mark.parametrize(
    ("maturity_model", "message"),
    [
        (_maturity_model(_team_row([_lv(1), _lv(3)])), r"numbered 1\.\.2 .* \(got \[1, 3\]\)"),
        (_maturity_model(_team_row([_lv(1), _lv(1)])), "no gap or duplicate"),
        (_maturity_model(_team_row([_lv(0), _lv(1)])), "no gap"),
        (_maturity_model({"id": "r", "title": "R", "levels": [_lv(1)]}), "'scope' must be one of"),
        (_maturity_model({"id": "r", "title": "R", "scope": "repo", "levels": [_lv(1)]}), "'scope' must be one of"),
        ({"rows": [_team_row([_lv(1)])]}, "'version'"),
        (_maturity_model(), "'rows'"),
        ({"version": "v", "rows": [], "extra": 1}, "unknown key"),
        (_maturity_model(_team_row([_lv(1)]), _team_row([_lv(1)])), "duplicate row id"),
        (_maturity_model("not a table"), "must be a table"),
        (_maturity_model({"title": "R", "scope": "org", "levels": [_lv(1)]}), "'id'"),
        (_maturity_model(_team_row([_lv(1)], scop="team")), "unknown key"),
        (_maturity_model(_team_row([_lv(1)], aggregation=None)), "needs 'aggregation'"),
        (_maturity_model(_team_row([_lv(1)], aggregation="mean")), "needs 'aggregation'"),
        (_maturity_model(_team_row([_lv(1)], share=0.5)), "applies only to"),
        (_maturity_model(_team_row([_lv(1)], aggregation="share")), "'share' must be"),
        (_maturity_model(_team_row([_lv(1)], aggregation="share", share=1.5)), "'share' must be"),
        (_maturity_model(_team_row([_lv(1)], aggregation="share", share=True)), "'share' must be"),
        (_maturity_model(_team_row([_lv(1)], scope="org")), "org row takes no aggregation"),
        (_maturity_model(_team_row([])), "'levels'"),
        (_maturity_model(_team_row(["x"])), "each level must be a table"),
        (_maturity_model(_team_row([{"level": 1.0, "criteria": []}])), "whole number"),
        (_maturity_model(_team_row([{"level": True, "criteria": []}])), "whole number"),
        (_maturity_model(_team_row([{"level": 1, "criteria": []}])), "'criteria'"),
        (_maturity_model(_team_row([{"level": 1, "criteria": ["x"]}])), "each criterion must be a table"),
        (_maturity_model(_team_row([_lv(1, {"signal": "a..b", "op": "eq", "value": 1})])), "malformed signal"),
        (_maturity_model(_team_row([_lv(1, {"signal": "a", "op": "~", "value": 1})])), "unknown operator"),
        (_maturity_model(_team_row([_lv(1, {"signal": "a", "op": "gte", "value": "1"})])), "needs a number"),
        (_maturity_model(_team_row([_lv(1, {"signal": "a", "op": "gte", "value": True})])), "needs a number"),
        (_maturity_model(_team_row([_lv(1, {"signal": "a", "op": "in", "value": []})])), "non-empty array"),
        (_maturity_model(_team_row([_lv(1, {"signal": "a", "op": "in", "value": [1, "1"]})])), "one scalar type"),
        (_maturity_model(_team_row([_lv(1, {"signal": "a", "op": "in", "value": "A"})])), "non-empty array"),
        (_maturity_model(_team_row([_lv(1, {"signal": "a", "op": "eq", "value": [1]})])), "string, number or boolean"),
        (_maturity_model(_team_row([_lv(1, {"signal": "a", "op": "eq", "value": 1, "x": 2})])), "unknown key"),
    ],
)
def test_malformed_levels_fails_at_load(maturity_model: dict[str, Any], message: str) -> None:
    with pytest.raises(LevelsError, match=message):
        parse_levels(maturity_model)


@pytest.mark.parametrize(
    ("op", "value"),
    [
        ("gte", "nan"),
        ("lt", "inf"),
        ("gt", "-inf"),
        ("eq", "nan"),
        ("ne", "inf"),
        ("in", "[1.0, nan]"),
        ("not_in", "[inf]"),
    ],
)
def test_non_finite_operand_fails_at_load(tmp_path: Path, op: str, value: str) -> None:
    maturity_model = tmp_path / "nan.toml"
    maturity_model.write_text(
        'version = "v"\n[[rows]]\nid = "r"\ntitle = "R"\nscope = "org"\n'
        f'[[rows.levels]]\nlevel = 1\ncriteria = [{{ signal = "s", op = "{op}", value = {value} }}]\n',
        encoding="utf-8",
    )
    with pytest.raises(LevelsError, match="r' level 1 s"):
        load_levels(maturity_model)


@pytest.mark.parametrize("share", ["nan", "inf"])
def test_non_finite_share_fails_at_load(tmp_path: Path, share: str) -> None:
    maturity_model = tmp_path / "share.toml"
    maturity_model.write_text(
        'version = "v"\n[[rows]]\nid = "r"\ntitle = "R"\nscope = "team"\n'
        f'aggregation = "share"\nshare = {share}\n'
        '[[rows.levels]]\nlevel = 1\ncriteria = [{ signal = "s", op = "eq", value = 1 }]\n',
        encoding="utf-8",
    )
    with pytest.raises(LevelsError, match="'share' must be"):
        load_levels(maturity_model)


def test_non_finite_signal_is_unknown_and_serialises() -> None:
    row = parse_levels(_maturity_model(_team_row([_lv(1, {"signal": "s", "op": "gte", "value": 1})])))["rows"][0]
    ev = evaluate_row(row, {"s": float("nan")})
    assert (ev["status"], ev["missing_signals"]) == ("unknown", ["s"])
    outcome = ev["levels"][0]["unmet"][0]
    assert (outcome["reason"], outcome["actual"]) == ("type_mismatch", None)
    result = evaluate(parse_levels(_maturity_model(_team_row([_lv(1, {"signal": "s", "op": "gte", "value": 1})]))),
                      {"name": "g", "settings": None, "repos": {"a": {"s": float("inf")}}})
    json.loads(dumps_result(result), parse_constant=lambda c: pytest.fail(f"non-JSON constant {c}"))


@pytest.mark.parametrize("signal", ['{"s": [NaN]}', '{"s": {"x": NaN}}', '{"s": [1, [Infinity]]}'])
def test_nested_non_finite_signal_never_reaches_the_result(signal: str) -> None:
    """Issue #602: an object or list actual is not echoed, so its NaN cannot be."""
    maturity_model = parse_levels(_maturity_model(_team_row([_lv(1, {"signal": "s", "op": "eq", "value": 1})])))
    result = evaluate(maturity_model, {"name": "g", "settings": None, "repos": {"a": json.loads(signal)}})
    outcome = result["rows"][0]["repos"]["a"]["levels"][0]["unmet"][0]
    assert (outcome["reason"], outcome["actual"]) == ("type_mismatch", None)
    json.loads(dumps_result(result), parse_constant=lambda c: pytest.fail(f"non-JSON constant {c}"))


def test_dumps_result_refuses_to_write_a_non_finite_number() -> None:
    result = evaluate(parse_levels(_maturity_model(_team_row([_lv(1)]))), {"name": "g", "settings": None, "repos": {}})
    result["rows"][0]["aggregation"]["share"] = float("nan")
    with pytest.raises(ValueError, match="JSON compliant"):
        dumps_result(result)


def test_missing_signals_lists_mistyped_as_well_as_unavailable() -> None:
    """The documented contract (issue #602): every signal whose criterion is unknown."""
    row = parse_levels(_maturity_model(_team_row([_lv(
        1, {"signal": "a", "op": "eq", "value": 1}, {"signal": "b", "op": "eq", "value": 1},
        {"signal": "c", "op": "eq", "value": 1})])))["rows"][0]
    ev = evaluate_row(row, {"a": "one", "c": 1})
    reasons = {o["signal"]: o["reason"] for o in ev["levels"][0]["unmet"]}
    assert reasons == {"a": "type_mismatch", "b": "unavailable"}
    assert ev["missing_signals"] == ["a", "b"]


def test_huge_integer_signal_compares_without_overflow() -> None:
    row = parse_levels(_maturity_model(_team_row([_lv(1, {"signal": "s", "op": "gte", "value": 1.5})])))["rows"][0]
    ev = evaluate_row(row, json.loads('{"s": ' + "9" * 400 + "}"))
    assert (ev["status"], ev["level"]) == ("level", 1)


def test_unreadable_toml_fails_at_load(tmp_path: Path) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text("version = \n", encoding="utf-8")
    with pytest.raises(LevelsError, match="cannot read maturity model bad.toml"):
        load_levels(bad)
    with pytest.raises(LevelsError, match="cannot read maturity model"):
        load_levels(tmp_path / "absent.toml")


def test_levels_may_be_listed_out_of_order() -> None:
    maturity_model = parse_levels(_maturity_model(_team_row([_lv(2), _lv(1)])))
    assert [lv["level"] for lv in maturity_model["rows"][0]["levels"]] == [1, 2]


def test_share_level_is_the_public_roll_up_rule() -> None:
    from fractions import Fraction

    from lib.scoring.levels import share_level
    assert share_level([2, 1, 2], Fraction(1), 2) == 1  # min
    assert share_level([2, 1, 2], Fraction(2, 3), 2) == 2
    assert share_level([], Fraction(1), 2) == 2  # vacuous: callers pass measured levels only
