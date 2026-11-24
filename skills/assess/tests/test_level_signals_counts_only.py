"""Counts-only output keeps the coverage_gate signals the default maturity model reads (#781, #788, #921).

Moved out of ``tests/test_level_signals.py`` to keep it under the file-size limit."""
from __future__ import annotations

import pytest

from lib.counts_only.content_free import counts_only_scans


@pytest.mark.parametrize("meets", [True, False, None])
def test_coverage_threshold_signal_survives_counts_only(meets: bool | None) -> None:
    """Counts-only output keeps the three-state signal and both percentages (#781)."""
    gate = {"file": "pom.xml", "line": 3, "tool": "jacoco", "form": "<rules> <minimum>",
            "threshold": 0.8, "unit": "ratio", "threshold_percent": 80.0, "metric": "line"}
    block = {"available": True, "configured": True, "gates": [gate], "max_threshold_percent": 80.0,
             "threshold_meets_minimum": meets, "not_detected": ["x"]}
    out = counts_only_scans({"coverage_gate": block})["coverage_gate"]
    assert out["threshold_meets_minimum"] is meets
    assert out["max_threshold_percent"] == 80.0
    assert (out["gates"][0]["threshold"], out["gates"][0]["threshold_percent"]) == (0.8, 80.0)


@pytest.mark.parametrize(("meets", "basis"), [(True, "ci_run"), (False, "not_run_in_ci"),
                                              (None, "indirect_run")])
def test_enforced_signal_survives_counts_only(meets: bool | None, basis: str) -> None:
    """Counts-only output keeps the #921 three-state signal and its basis token."""
    block = {"available": True, "configured": True, "gates": [], "max_threshold_percent": 80.0,
             "threshold_meets_minimum": True, "enforced_meets_minimum": meets,
             "enforced_basis": basis, "not_detected": ["x"]}
    out = counts_only_scans({"coverage_gate": block})["coverage_gate"]
    assert (out["enforced_meets_minimum"], out["enforced_basis"]) == (meets, basis)


def test_unmeasured_reason_survives_counts_only() -> None:
    block = {"available": True, "configured": False, "gates": [], "max_threshold_percent": None,
             "threshold_meets_minimum": None,
             "unmeasured_reason": "go_rust_gate_form_not_read", "not_detected": ["x"]}
    out = counts_only_scans({"coverage_gate": block})["coverage_gate"]
    assert out["unmeasured_reason"] == "go_rust_gate_form_not_read"
    assert out["threshold_meets_minimum"] is None
