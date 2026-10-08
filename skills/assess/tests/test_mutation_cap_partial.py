"""Layer 6 cap on partial-only mutation evidence.

A mutmut 3 group stopped at the time budget keeps the verdicts it saved and is
marked ``partial``. mutmut tests the fastest mutants first, so those figures are
a biased sample: when every group with records is partial, the cap holds Layer 6
at Partial and finalize refuses Present. ``applies`` stays False, because
mutation did run and the report's "not run" caveat keys on it.
"""
from __future__ import annotations

from typing import Any

import pytest

from assess_finalize import FinalizeValidationError, _validate_layer6_cap
from lib.mutation_cap import (
    MUTATION_NOT_RUN_ANNOTATION,
    MUTATION_PARTIAL_ANNOTATION,
    layer6_cap_violation,
    mutation_not_run_cap,
)

_RECORD = {"file": "src/a.py", "killed": 3, "survived": 1, "total": 4}


def _group(root: str, *, ran: bool = True, partial: bool = False) -> dict[str, Any]:
    group: dict[str, Any] = {"root": root, "config": "repo", "mutation_run": ran}
    if partial:
        group["partial"] = True
        group["reason"] = "stopped at the 300s budget"
    return group


def _block(groups: list[dict[str, Any]] | None, *, records: bool = True) -> dict[str, Any]:
    block: dict[str, Any] = {
        "mutation_run": records,
        "per_file": [_RECORD] if records else [],
    }
    if groups is not None:
        block["mutation_groups"] = groups
    return block


# (case id, test_pressure block, applies, partial, max band, annotation)
CASES = [
    ("not_run", {"mutation_run": False, "per_file": []},
     True, False, "Partial", MUTATION_NOT_RUN_ANNOTATION),
    ("all_complete", _block([_group("a"), _group("b")]),
     False, False, "Present", None),
    ("all_partial", _block([_group("a", partial=True), _group("b", partial=True)]),
     False, True, "Partial", MUTATION_PARTIAL_ANNOTATION),
    ("mixed", _block([_group("a"), _group("b", partial=True)]),
     False, False, "Present", None),
    ("partial_plus_not_run_group",
     _block([_group("a", partial=True), _group("b", ran=False)]),
     False, True, "Partial", MUTATION_PARTIAL_ANNOTATION),
    ("partial_zero_records",
     _block([{**_group("a", partial=True), "mutation_run": False}], records=False),
     True, False, "Partial", MUTATION_NOT_RUN_ANNOTATION),
    ("no_groups_key", _block(None),
     False, False, "Present", None),
    ("empty_groups", _block([]),
     False, False, "Present", None),
]


@pytest.mark.parametrize(
    ("block", "applies", "partial", "band", "annotation"),
    [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_cap_matrix(block: dict[str, Any], applies: bool, partial: bool,
                    band: str, annotation: str | None) -> None:
    cap = mutation_not_run_cap(block)
    assert cap["applies"] is applies
    assert cap["mutation_run"] is (not applies)
    assert cap["partial"] is partial
    assert cap["max_layer6_band"] == band
    assert cap["annotation"] == annotation


@pytest.mark.parametrize(("block", "band"), [(c[1], c[4]) for c in CASES],
                         ids=[c[0] for c in CASES])
def test_finalize_refuses_present_exactly_when_band_is_partial(
        block: dict[str, Any], band: str) -> None:
    ctx = {"mutation_not_run_cap": mutation_not_run_cap(block)}
    _validate_layer6_cap({"layer_scores": {"6": 0.5}}, ctx)  # Partial always passes
    present = {"layer_scores": {"6": 1.0}}
    if band == "Present":
        _validate_layer6_cap(present, ctx)
    else:
        with pytest.raises(FinalizeValidationError,
                           match="Layer 6 cannot exceed Partial"):
            _validate_layer6_cap(present, ctx)


def test_partial_refusal_names_partial_annotation() -> None:
    ctx = {"mutation_not_run_cap": mutation_not_run_cap(
        _block([_group("a", partial=True)]))}
    msg = layer6_cap_violation(ctx, 1.0)
    assert msg is not None
    assert MUTATION_PARTIAL_ANNOTATION in msg
    assert "stopped at the time budget" in msg


def test_not_run_refusal_keeps_its_message() -> None:
    ctx = {"mutation_not_run_cap": mutation_not_run_cap({"mutation_run": False})}
    msg = layer6_cap_violation(ctx, 1.0)
    assert msg is not None
    assert "when mutation testing was not run" in msg
    assert MUTATION_NOT_RUN_ANNOTATION in msg


def test_older_cap_without_partial_key_behaves_as_before() -> None:
    """A run-context written before the ``partial`` key: a ran cap lifts."""
    legacy_ran = {"applies": False, "mutation_run": True,
                  "max_layer6_band": "Present", "annotation": None}
    assert layer6_cap_violation({"mutation_not_run_cap": legacy_ran}, 1.0) is None
    legacy_not_run = {"applies": True, "mutation_run": False,
                      "max_layer6_band": "Partial",
                      "annotation": MUTATION_NOT_RUN_ANNOTATION}
    assert layer6_cap_violation({"mutation_not_run_cap": legacy_not_run}, 1.0)


def test_context_without_cap_block_falls_back_to_test_pressure() -> None:
    assert layer6_cap_violation({"test_pressure": {"mutation_run": True}}, 1.0) is None
    assert layer6_cap_violation({"test_pressure": {"mutation_run": False}}, 1.0)
    assert layer6_cap_violation({}, 1.0)


def test_missing_layer6_score_is_not_checked() -> None:
    ctx = {"mutation_not_run_cap": mutation_not_run_cap({"mutation_run": False})}
    assert layer6_cap_violation(ctx, None) is None
    _validate_layer6_cap({}, ctx)


def test_non_dict_block_reads_as_not_run() -> None:
    cap = mutation_not_run_cap("broken")  # type: ignore[arg-type]  # malformed input
    assert cap["applies"] is True
    assert cap["partial"] is False
