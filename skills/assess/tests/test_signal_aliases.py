"""The 2.1.0 signal paths renamed in 2.2.0 load, score and compare under the new names (#970)."""
from __future__ import annotations

import io
from typing import Any

import pytest

from lib import fleet_rescore
from lib.fleet_compare import compare
from lib.level_signals import DEFAULT_LEVELS, load_default_levels, signal_path_validator
from lib.levels import evaluate, load_levels, parse_levels
from lib.signal_aliases import SIGNAL_ALIASES, aliased, dealias_model, deprecation_line, renamed
from test_fleet_compare import _row, _run

OLD_TEXT = DEFAULT_LEVELS.read_text(encoding="utf-8")


def _model(signal: str, eq_signal: str | None = None, scope: str = "team") -> dict[str, Any]:
    criterion: dict[str, Any] = {"signal": signal, "op": "eq"}
    if eq_signal is None:
        criterion["value"] = True
    else:
        criterion["eq_signal"] = eq_signal
    row: dict[str, Any] = {"id": "r", "title": "R", "scope": scope,
                           "levels": [{"level": 1, "truth": ["behaviour"], "criteria": [criterion]}]}
    if scope == "team":
        row["aggregation"] = "min"
    return {"version": "t-1", "rows": [row]}


def _repo(configured: bool) -> dict[str, Any]:
    return {"ci_checks": {"available": True, "test_or_lint_configured": configured},
            "coverage_gate": {"available": True, "configured": configured}}


def test_the_table_is_the_two_2_2_0_renames_and_fleet_rescore_re_exports_it() -> None:
    assert SIGNAL_ALIASES == {"ci_checks.test_or_lint_runs": "ci_checks.test_or_lint_configured",
                              "coverage_gate.enforced": "coverage_gate.configured"}
    assert fleet_rescore.SIGNAL_ALIASES is SIGNAL_ALIASES and fleet_rescore.aliased is aliased


@pytest.mark.parametrize("old", sorted(SIGNAL_ALIASES))
def test_a_model_naming_an_old_path_loads_scores_the_same_and_warns_once(
        old: str, capsys: pytest.CaptureFixture[str]) -> None:
    new = SIGNAL_ALIASES[old]
    assert not signal_path_validator()("team", old)  # the validator itself still rejects it
    raw = _model(old)
    raw["rows"][0]["levels"].append({"level": 2, "truth": ["behaviour"],
                                     "criteria": [{"signal": old, "op": "eq", "value": True}]})
    old_model = parse_levels(raw, signal_path_validator())
    assert capsys.readouterr().err == deprecation_line(old, new) + "\n"  # one line for two uses
    new_model = parse_levels(_model(new) | {"rows": [{**raw["rows"][0], "levels": [
        {"level": 1, "truth": ["behaviour"], "criteria": [{"signal": new, "op": "eq", "value": True}]},
        {"level": 2, "truth": ["behaviour"], "criteria": [{"signal": new, "op": "eq", "value": True}]}]}]},
        signal_path_validator())
    assert capsys.readouterr().err == ""
    assert old_model == new_model
    assert raw["rows"][0]["levels"][0]["criteria"][0]["signal"] == old  # input untouched
    for configured in (True, False):
        group: Any = {"name": "g", "repos": {"a": _repo(configured)}}
        result = evaluate(old_model, group)
        assert result == evaluate(new_model, group)
        assert result["rows"][0]["level"] == (2 if configured else 0)


def test_an_old_path_in_eq_signal_is_renamed_and_named_once() -> None:
    stream = io.StringIO()
    out = dealias_model(_model("coverage_gate.enforced", "ci_checks.test_or_lint_runs"), stream=stream)
    criterion = out["rows"][0]["levels"][0]["criteria"][0]
    assert (criterion["signal"], criterion["eq_signal"]) == ("coverage_gate.configured",
                                                             "ci_checks.test_or_lint_configured")
    assert stream.getvalue().splitlines() == [
        deprecation_line("ci_checks.test_or_lint_runs", "ci_checks.test_or_lint_configured"),
        deprecation_line("coverage_gate.enforced", "coverage_gate.configured")]


def test_org_rows_malformed_tables_and_the_default_model_are_left_alone() -> None:
    stream = io.StringIO()
    org = _model("coverage_gate.enforced", scope="org")
    assert dealias_model(org, stream=stream) is org
    for raw in ({"rows": "x"}, {"rows": ["x", {"levels": [1, {"criteria": [2, {"signal": 3}]}]}]}, {}):
        assert dealias_model(raw, stream=stream) is raw
    assert stream.getvalue() == ""
    load_default_levels()


def test_a_2_1_0_model_file_loads_through_load_levels(tmp_path: Any, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "old.toml"
    path.write_text(OLD_TEXT.replace('"coverage_gate.configured"', '"coverage_gate.enforced"')
                    .replace('"ci_checks.test_or_lint_configured"', '"ci_checks.test_or_lint_runs"'),
                    encoding="utf-8")
    assert load_levels(path, signal_path_validator()) == load_default_levels()


def test_renamed_moves_the_old_leaf_and_never_hides_a_real_difference() -> None:
    old = {"ci_checks": {"available": True, "test_or_lint_runs": True},
           "coverage_gate": {"available": True, "enforced": False}}
    assert renamed(old) == {"ci_checks": {"available": True, "test_or_lint_configured": True},
                            "coverage_gate": {"available": True, "configured": False}}
    assert old["coverage_gate"] == {"available": True, "enforced": False}  # input untouched
    both = {"coverage_gate": {"enforced": True, "configured": False}}
    assert renamed(both) == both
    assert renamed(None) is None


def test_comparing_a_2_1_0_row_with_a_2_2_0_row_lists_no_leaf_for_the_renames() -> None:
    then = [_row("g/a", {"ci_checks": {"available": True, "test_or_lint_runs": True},
                         "coverage_gate": {"available": True, "enforced": False}})]
    now = [_row("g/a", _repo(True) | {"coverage_gate": {"available": True, "configured": False}})]
    comparison = compare(_run(then), _run(now))
    assert comparison["comparable"] and comparison["repos"] == []
    moved = [_row("g/a", _repo(False))]
    (entry,) = compare(_run(then), _run(moved))["repos"]
    assert entry["signals"] == [{"path": "ci_checks.test_or_lint_configured", "then": True, "now": False}]
