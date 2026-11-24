"""lib/levels_hints.py: an unknown signal path names its line and the closest real signals (#1151)."""
from __future__ import annotations

from pathlib import Path

import pytest

from lib.level_signals import ORG_PATHS, published_paths, signal_path_validator
from lib.levels import LevelsError, known_paths, load_levels, parse_levels
from lib.levels_hints import CATALOG, UnknownSignalError, closest

MODEL = """version = "t-1"

[[rows]]
id = "first"
title = "First"
scope = "team"
aggregation = "min"

  [[rows.levels]]
  level = 1
  truth = ["behaviour"]
  criteria = [{ signal = "coverage_gate.enforcd", op = "eq", value = true }]

[[rows]]
id = "second"
title = "Second"
scope = "team"
aggregation = "min"

  [[rows.levels]]
  level = 1
  truth = ["behaviour"]
  criteria = [{ signal = "coverage_gate.enforcd", op = "eq", value = true }]
"""


def test_published_team_paths_are_every_path_the_validator_accepts() -> None:
    validate = signal_path_validator()
    paths = published_paths("team")
    assert "coverage_gate.max_threshold_percent" in paths
    assert "coverage_gate.available" in paths
    assert all(validate("team", p) for p in paths)
    assert list(paths) == sorted(set(paths))
    assert published_paths("org") == tuple(sorted(ORG_PATHS))


@pytest.mark.parametrize(("typo", "first"), [
    ("coverage_gate.threshold", "coverage_gate.max_threshold_percent"),
    ("ci_check.lint_runs", "ci_checks.lint_runs"),
    ("layer_grades.instruction_file", "layer_grades.instruction_file_present"),
])
def test_closest_names_the_real_signal_first(typo: str, first: str) -> None:
    assert closest("team", typo)[0] == first


def test_closest_is_empty_for_nothing_close_or_an_unknown_scope() -> None:
    assert closest("team", "zzzz.qqqq") == []
    assert closest("repo", "coverage_gate.configured") == []


def test_load_names_the_line_of_each_row_and_the_fix(tmp_path: Path) -> None:
    path = tmp_path / "t.toml"
    path.write_text(MODEL, encoding="utf-8")
    with pytest.raises(UnknownSignalError) as caught:
        load_levels(path, signal_path_validator())
    message = str(caught.value)
    assert message.startswith("unknown signal path(s): team:coverage_gate.enforcd (row 'first') at line 12, "
                              "did you mean coverage_gate.enforced_basis")
    assert "(row 'second') at line 23, did you mean" in message
    assert isinstance(caught.value, LevelsError)


def test_one_close_name_reads_without_a_list_and_none_points_at_the_catalog() -> None:
    assert str(UnknownSignalError([("org", "provder", "r")])) == (
        "unknown signal path(s): org:provder (row 'r'), did you mean provider?")
    assert str(UnknownSignalError([("team", "zzzz.qqqq", "r")])).endswith(
        f"no signal has a close name; {CATALOG} lists them all")


def test_a_parsed_table_has_no_line_to_name() -> None:
    data = {"version": "t", "rows": [{"id": "r", "title": "R", "scope": "team", "aggregation": "min",
                                       "levels": [{"level": 1, "truth": ["behaviour"],
                                                   "criteria": [{"signal": "a.b", "op": "eq", "value": 1}]}]}]}
    with pytest.raises(UnknownSignalError, match=r"team:a\.b \(row 'r'\), "):
        parse_levels(data, known_paths([], []))


def test_a_path_not_in_the_text_keeps_no_line() -> None:
    error = UnknownSignalError([("team", "a.b", "r")]).located('id = "r"\n')
    assert " at line" not in str(error)
