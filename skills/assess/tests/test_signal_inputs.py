"""lib/level_signals.py: every maturity-model signal's producer input class and the declared predicate (#783)."""
from __future__ import annotations

from pathlib import Path
from typing import get_args

import pytest

from lib.levels import Criterion, load_levels
from lib.level_signals import (
    INPUT_GRADES,
    SIGNAL_INPUTS,
    Grade,
    InputClass,
    UnclassifiedSignal,
    criterion_grade,
    input_class,
    is_declared,
    load_default_levels,
    signal_path_validator,
    team_path_ok,
)


def _default_signals() -> list[str]:
    return sorted({c["signal"] for row in load_default_levels()["rows"]
                   for level in row["levels"] for c in level["criteria"]})


@pytest.mark.parametrize("signal", _default_signals())
def test_every_default_signal_has_an_input_class(signal: str) -> None:
    assert input_class(signal) in get_args(InputClass)


def test_an_unclassified_signal_fails_the_classification_check(tmp_path: Path) -> None:
    # A valid typed path that no default criterion reads, so it has no class.
    model = tmp_path / "levels.toml"
    model.write_text(
        'version = "t-1"\n[[rows]]\nid = "r"\ntitle = "R"\nscope = "team"\n'
        'aggregation = "min"\n[[rows.levels]]\nlevel = 1\n'
        'criteria = [{ signal = "review_reality.merged_count", op = "gt", value = 0 }]\n',
        encoding="utf-8")
    loaded = load_levels(model, signal_path_validator())
    signal = loaded["rows"][0]["levels"][0]["criteria"][0]["signal"]
    with pytest.raises(UnclassifiedSignal, match="review_reality.merged_count"):
        input_class(signal)


@pytest.mark.parametrize(("criterion", "declared"), [
    ({"signal": "layer_grades.instruction_file_present", "op": "eq", "value": True}, True),
    ({"signal": "coverage_gate.enforced", "op": "eq", "value": True}, True),
    ({"signal": "promissory_markers.families.suppression.total", "op": "gt", "value": 0}, True),
    ({"signal": "observability.rung", "op": "gte", "value": 1}, True),
    ({"signal": "observability.rung", "op": "gte", "value": 3}, True),
    ({"signal": "promissory_markers.families.suppression.stale", "op": "lte", "value": 10}, False),
    ({"signal": "structure.modularity_q", "op": "gte", "value": 0.3}, False),
    ({"signal": "doc_staleness.summary.stale_pct", "op": "lte", "value": 20}, False),
    ({"signal": "review_reality.reviewed_share", "op": "gte", "value": 0.8}, False),
    ({"signal": "test_pressure.mutation_run", "op": "eq", "value": True}, False),
])
def test_is_declared_matches_the_classification_table(criterion: Criterion, declared: bool) -> None:
    assert is_declared(criterion) is declared


@pytest.mark.parametrize(("criterion", "grade"), [
    ({"signal": "coverage_gate.enforced", "op": "eq", "value": True}, "config_only"),
    ({"signal": "ci_checks.test_or_lint_runs", "op": "eq", "value": True}, "declared"),
    ({"signal": "observability.rung", "op": "gte", "value": 3}, "declared"),
    ({"signal": "stats_summary.loc.p95", "op": "lte", "value": 500}, "measured"),
    ({"signal": "doc_staleness.summary.stale_pct", "op": "lte", "value": 50}, "measured"),
    ({"signal": "review_reality.approved_share", "op": "gte", "value": 0.5}, "verified"),
])
def test_criterion_grade_follows_the_input_class(criterion: Criterion, grade: Grade) -> None:
    assert criterion_grade(criterion) == grade


@pytest.mark.parametrize("criterion", [
    {"signal": "agent_environment.check.named_total", "op": "gte", "value": 2},
    {"signal": "agent_environment.check.named_total", "op": "lte", "value": 0},
    {"signal": "promissory_markers.families.suppression.total", "op": "lt", "value": 1},
    {"signal": "observability.rung", "op": "ne", "value": 3},
    {"signal": "coverage_gate.enforced", "op": "gte", "value": True},
])
def test_no_operator_lifts_a_text_signal_to_a_counted_grade(criterion: Criterion) -> None:
    # #789: the grade comes from the evidence, never from the criterion's operator.
    assert is_declared(criterion)


def test_every_input_class_has_a_grade() -> None:
    assert set(INPUT_GRADES) == set(get_args(InputClass))
    assert set(INPUT_GRADES.values()) <= set(get_args(Grade))


def test_every_classified_signal_is_a_valid_team_path() -> None:
    assert sorted(s for s in SIGNAL_INPUTS if not team_path_ok(s)) == []


def test_the_table_holds_exactly_the_default_signals() -> None:
    # The table shrinks with its readers: a signal no default criterion reads is a stale line.
    assert set(SIGNAL_INPUTS) == set(_default_signals())
