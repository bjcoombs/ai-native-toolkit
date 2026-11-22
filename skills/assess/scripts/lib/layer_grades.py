"""Deterministic layer grades as one signals block (issue #620).

The core computes some per-layer grades with no model in the path, but keeps
them as run-context header keys (``instructions_grade``) that the headless
signals document never carries, so a maturity model could not name them. This
block republishes them under one registered scan, ``layer_grades``: the same
name keys it in a headless document's ``scans`` and in a full
``run-context.json``, so ``lib.level_signals`` accepts its paths and one
maturity model reads either.

Fields:

- ``instructions_grade``: the best letter grade across the tracked, present
  agent instruction files (``instruction_files.grade_instruction_files``), the
  value of the run-context key of the same name. Null means no committed
  instruction file, which ``lib.levels`` reads as unavailable (unknown).
- ``instruction_file_present``: whether that grade exists. A maturity model that must
  score "no instruction file" as level 0 rather than unknown reads this.

Only Layer 0 has a deterministic grade today. Layers 2 (code design), 3
(linters) and 4 (architecture tests) are judged by the assess-layer-scorer
reading configuration and code, so no field stands for them here. The default
maturity model's rows for them read proxies from other blocks instead.

Grades are short tokens and the flag a boolean, so the counts-only filter keeps
the block whole. Pure: it reads the setup grading the core already ran.

Deterministic layer grades as one registered block (issue #620), so the headless
signals document carries them and a maturity model can name them.
`scan_layer_grades(instruction_grading)` reads the setup grading
(`run_setup.instruction_grading`) and writes `{instructions_grade,
instruction_file_present}`: the best letter grade of the tracked, present instruction
files (null with none, the top-level `instructions_grade` value, which stays) and
whether one exists, so a maturity model can score "no instruction file" as level 0 rather
than unknown. Only Layer 0 has a deterministic grade; Layers 2-4 have none (the
default maturity model's rows for them read proxies, and its header says how they diverge). A short token and a boolean, kept whole by the
counts-only filter. Tests: `tests/test_layer_grades.py`, and the maturity model rows in
`tests/test_level_signals.py`.
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
