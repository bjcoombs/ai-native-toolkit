"""lib/layer_grades.py: deterministic layer grades as a registered signals block."""
from __future__ import annotations

from typing import Any

import pytest

from lib.counts_only import counts_only_scans
from lib.layer_grades import scan_layer_grades
from lib.scan_registry import SCANS
from lib.signals import known_scans


@pytest.mark.parametrize(("grading", "expected"), [
    (({"CLAUDE.md": {}}, "B"), {"instructions_grade": "B", "instruction_file_present": True}),
    (({"CLAUDE.md": {}}, "F"), {"instructions_grade": "F", "instruction_file_present": True}),
    (({}, None), {"instructions_grade": None, "instruction_file_present": False}),
])
def test_block_republishes_the_best_grade(grading: tuple[Any, ...], expected: dict[str, Any]) -> None:
    assert scan_layer_grades(grading) == expected


def test_registered_reading_the_setup_grading_and_reported_headless() -> None:
    spec = next(s for s in SCANS if s.key == "layer_grades")
    assert spec.fn is scan_layer_grades
    assert spec.reads == ("instruction_grading",)
    assert not spec.network and not spec.private
    assert "layer_grades" in known_scans()


def test_counts_only_keeps_the_grade_and_the_flag() -> None:
    block = scan_layer_grades(({}, "A"))
    assert counts_only_scans({"layer_grades": dict(block)})["layer_grades"] == block
