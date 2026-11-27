"""lib/scoring/layer_grades.py: deterministic layer grades as a registered signals block."""
from __future__ import annotations

import json
from typing import Any

import pytest

from lib.counts_only.content_free import counts_only_scans
from lib.scoring.layer_grades import scan_layer_grades
from lib.run.scan_registry import SCANS
from lib.run.signals import known_scans

NO_NESTED: dict[str, Any] = {"available": True, "files": []}


def _file(path: str, grade: str, *, always: bool = False, directory: str | None = None) -> dict[str, Any]:
    where = directory if directory is not None else path.rpartition("/")[0] or "."
    return {"path": path, "grade": grade,
            "scope": {"directory": where, "globs": [], "always_loaded": always}}


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


STUB = {"heading_only": True}


def test_a_heading_only_root_file_is_a_stub_and_not_present() -> None:
    """#922: CLAUDE.md = "# x" is committed but tells an agent nothing."""
    assert scan_layer_grades(({"CLAUDE.md": STUB}, "F"), NO_NESTED) == {
        "instructions_grade": "F", "instruction_file_present": False,
        "instruction_presence": "stub"}


def test_one_root_file_with_a_body_beside_a_stub_is_present() -> None:
    grading = ({"CLAUDE.md": STUB, "AGENTS.md": {"heading_only": False}}, "C")
    assert scan_layer_grades(grading, NO_NESTED)["instruction_presence"] == "root"


def test_a_stub_root_file_reads_by_a_nested_file_beside_it() -> None:
    nested = {"available": True, "files": [_file("pkg/AGENTS.md", "B")]}
    assert scan_layer_grades(({"CLAUDE.md": STUB}, "F"), nested) == {
        "instructions_grade": "B", "instruction_file_present": True,
        "instruction_presence": "nested_only"}
    assert scan_layer_grades(({"CLAUDE.md": STUB}, "F"), {"available": False}) == {
        "instructions_grade": None, "instruction_file_present": None,
        "instruction_presence": None}


def test_nested_only_is_present_with_the_best_nested_grade() -> None:
    """#821: only pkg/AGENTS.md - present, loaded on demand, graded, never null."""
    nested = {"available": True, "files": [_file("pkg/AGENTS.md", "D"), _file("svc/AGENTS.md", "B")]}
    assert scan_layer_grades(({}, None), nested) == {
        "instructions_grade": "B", "instruction_file_present": True,
        "instruction_presence": "nested_only"}


@pytest.mark.parametrize(("grades", "best"), [
    (["A-"], "A-"), (["C", "A-"], "A-"), (["B", "B+"], "B+"), (["F", "D"], "D"),
])
def test_the_best_nested_grade_ranks_every_grader_letter(grades: list[str], best: str) -> None:
    """The grader also gives A- and B+; neither is dropped or outranked by a lower letter."""
    nested = {"available": True, "files": [_file(f"p{i}/AGENTS.md", g) for i, g in enumerate(grades)]}
    assert scan_layer_grades(({}, None), nested)["instructions_grade"] == best


def test_an_always_loaded_discovered_file_reads_root() -> None:
    nested = {"available": True, "files": [_file("pkg/AGENTS.md", "A"),
                                           _file(".windsurfrules", "C", always=True)]}
    assert scan_layer_grades(({}, None), nested) == {
        "instructions_grade": "C", "instruction_file_present": True,
        "instruction_presence": "root"}


def test_a_nested_always_apply_rule_is_not_a_root_file() -> None:
    """A Cursor alwaysApply rule under pkg/ is always loaded but not at the root."""
    nested = {"available": True, "files": [_file("pkg/.cursor/rules/x.mdc", "B", always=True,
                                                  directory="pkg")]}
    assert scan_layer_grades(({}, None), nested)["instruction_presence"] == "nested_only"


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
