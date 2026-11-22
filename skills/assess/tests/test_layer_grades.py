"""lib/layer_grades.py: deterministic layer grades as a registered signals block."""
from __future__ import annotations

import json
from typing import Any

import pytest

from lib.counts_only import counts_only_scans
from lib.layer_grades import scan_layer_grades
from lib.scan_registry import SCANS
from lib.signals import known_scans

NO_NESTED: dict[str, Any] = {"available": True, "files": []}


def _file(path: str, grade: str, *, always: bool = False) -> dict[str, Any]:
    return {"path": path, "grade": grade,
            "scope": {"directory": path.rpartition("/")[0] or ".", "globs": [],
                      "always_loaded": always}}


@pytest.mark.parametrize(("grading", "expected"), [
    (({"CLAUDE.md": {}}, "B"), {"instructions_grade": "B", "instruction_file_present": True,
                                "instruction_presence": "root"}),
    (({"CLAUDE.md": {}}, "F"), {"instructions_grade": "F", "instruction_file_present": True,
                                "instruction_presence": "root"}),
    (({}, None), {"instructions_grade": None, "instruction_file_present": False,
                  "instruction_presence": "none"}),
])
def test_block_republishes_the_best_grade(grading: tuple[Any, ...], expected: dict[str, Any]) -> None:
    assert scan_layer_grades(grading, NO_NESTED) == expected


def test_nested_only_is_present_with_the_best_nested_grade() -> None:
    """#821: only pkg/AGENTS.md - present, loaded on demand, graded, never null."""
    nested = {"available": True, "files": [_file("pkg/AGENTS.md", "D"), _file("svc/AGENTS.md", "B")]}
    assert scan_layer_grades(({}, None), nested) == {
        "instructions_grade": "B", "instruction_file_present": True,
        "instruction_presence": "nested_only"}


def test_an_always_loaded_discovered_file_reads_root() -> None:
    nested = {"available": True, "files": [_file("pkg/AGENTS.md", "A"),
                                           _file(".windsurfrules", "C", always=True)]}
    assert scan_layer_grades(({}, None), nested) == {
        "instructions_grade": "C", "instruction_file_present": True,
        "instruction_presence": "root"}


def test_a_root_file_keeps_the_root_grade_over_nested_files() -> None:
    nested = {"available": True, "files": [_file("pkg/AGENTS.md", "A")]}
    block = scan_layer_grades(({"AGENTS.md": {}}, "D"), nested)
    assert (block["instruction_presence"], block["instructions_grade"]) == ("root", "D")


@pytest.mark.parametrize("nested", [{"available": False, "reason": "scan_failed"}, None, {}])
def test_an_unmeasured_nested_block_is_unknown_not_none(nested: Any) -> None:
    """No root file and no nested measurement: null, never a false 'none'."""
    assert scan_layer_grades(({}, None), nested) == {
        "instructions_grade": None, "instruction_file_present": None,
        "instruction_presence": None}
    assert scan_layer_grades(({"CLAUDE.md": {}}, "A"), nested)["instruction_presence"] == "root"


def test_registered_reading_the_setup_grading_and_reported_headless() -> None:
    spec = next(s for s in SCANS if s.key == "layer_grades")
    assert spec.fn is scan_layer_grades
    assert spec.reads == ("instruction_grading", "nested_instructions")
    assert not spec.network and not spec.private
    assert "layer_grades" in known_scans()


@pytest.mark.parametrize("hash_paths", [False, True])
def test_counts_only_keeps_the_grade_the_flag_and_the_presence(hash_paths: bool) -> None:
    """Every field survives counts-only (a dropped field fails), and no nested path leaks."""
    nested = {"available": True, "files": [_file("pkg/AGENTS.md", "C")]}
    block = scan_layer_grades(({}, None), nested)
    kept = counts_only_scans({"layer_grades": dict(block)}, hash_paths=hash_paths)["layer_grades"]
    assert kept == block
    assert set(kept) == {"instructions_grade", "instruction_file_present", "instruction_presence"}
    assert "pkg" not in json.dumps(kept)
