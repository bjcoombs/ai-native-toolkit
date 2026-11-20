"""Deterministic layer grades as one signals block (issue #620).

The core computes some per-layer grades with no model in the path, but keeps
them as run-context header keys (``instructions_grade``) that the headless
signals document never carries, so a maturity rubric could not name them. This
block republishes them under one registered scan, ``layer_grades``: the same
name keys it in a headless document's ``scans`` and in a full
``run-context.json``, so ``lib.rubric_signals`` accepts its paths and one
rubric reads either.

Fields:

- ``instructions_grade``: the best letter grade across the tracked, present
  agent instruction files (``instruction_files.grade_instruction_files``), the
  value of the run-context key of the same name. Null means no committed
  instruction file, which ``lib.rubric`` reads as unavailable (unknown).
- ``instruction_file_present``: whether that grade exists. A rubric that must
  score "no instruction file" as level 0 rather than unknown reads this.

Only Layer 0 has a deterministic grade today. Layers 2 (code design), 3
(linters) and 4 (architecture tests) are judged by the assess-layer-scorer
reading configuration and code, so no field stands for them here.

Grades are short tokens and the flag a boolean, so the counts-only filter keeps
the block whole. Pure: it reads the setup grading the core already ran.
"""
from __future__ import annotations

from typing import Any, TypedDict


class LayerGradesBlock(TypedDict):
    instructions_grade: str | None
    instruction_file_present: bool


def scan_layer_grades(instruction_grading: tuple[Any, ...]) -> LayerGradesBlock:
    """The block from ``run_setup.instruction_grading``'s ``(files, best_grade, ...)``."""
    grade = instruction_grading[1]
    best = grade if isinstance(grade, str) else None
    return {"instructions_grade": best, "instruction_file_present": best is not None}
