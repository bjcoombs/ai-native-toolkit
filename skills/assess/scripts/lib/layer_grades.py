"""Deterministic layer grades as one signals block (issue #620).

The core computes some per-layer grades with no model in the path, but keeps
them as run-context header keys (``instructions_grade``) that the headless
signals document never carries, so a maturity model could not name them. This
block republishes them under one registered scan, ``layer_grades``: the same
name keys it in a headless document's ``scans`` and in a full
``run-context.json``, so ``lib.level_signals`` accepts its paths and one
maturity model reads either.

Fields:

- ``instruction_presence`` (issue #821): where the agent instruction files are,
  one of three tokens:

  - ``root``: a tracked file at a root location
    (``instruction_files.INSTRUCTION_FILE_PATHS``), or a discovered file the
    tool loads at session start for the root context (``nested_instructions``
    with ``scope.always_loaded`` and ``scope.directory`` ``.``, such as an unscoped ``.claude/rules`` file or
    ``.windsurfrules``). Always loaded.
  - ``nested_only``: no root file, but ``nested_instructions`` lists at least
    one file (a ``pkg/AGENTS.md``, a path-scoped rule). Present, loaded on
    demand when the agent works in that directory or on a matching path.
  - ``none``: no instruction file at any known location.

  Null only when there is no root file and the ``nested_instructions`` block
  is unavailable, so whether a nested file exists was not measured.
- ``instructions_grade``: the best letter grade of the files ``instruction_presence``
  counts: the root grading (``instruction_files.grade_instruction_files``, the
  value of the run-context key of the same name) when a root location holds a
  file, else the best grade among the always-loaded or, for ``nested_only``,
  all nested files. Null only for ``none`` (or the unmeasured null above), the
  CLAUDE.md invariant: null means no instruction file at any known location.
- ``instruction_file_present``: whether ``instruction_presence`` is ``root`` or
  ``nested_only``; null with it. The default maturity model reads this for
  Layer 0 level 1, so a nested-only repository meets that level (harnesses load
  nested ``AGENTS.md`` where the agent works; maintainer decision on #821), and
  its advice is the ``gap_actions`` entry whose ``signal`` is ``layer_grades``
  (``lib.gap_actions``): add a root file that names the check command.

Only Layer 0 has a deterministic grade today. Layers 2 (code design), 3
(linters) and 4 (architecture tests) are judged by the assess-layer-scorer
reading configuration and code, so no field stands for them here. The default
maturity model's rows for them read proxies from other blocks instead.

Grades and the presence value are short tokens and the flag a boolean, so the
counts-only filter keeps the block whole; no path is carried. Pure: it reads the
setup grading and the nested block the core already built.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypedDict

from lib.instruction_files import GRADE_RANK

InstructionPresence = Literal["root", "nested_only", "none"]

ROOT: InstructionPresence = "root"
NESTED_ONLY: InstructionPresence = "nested_only"
NONE: InstructionPresence = "none"


class LayerGradesBlock(TypedDict):
    instructions_grade: str | None
    instruction_file_present: bool | None
    instruction_presence: InstructionPresence | None


def _best(grades: list[str]) -> str | None:
    """The best grade, ranked as the root grading ranks them (A, A-, B+, B, C, D, F)."""
    return max(grades, key=lambda g: GRADE_RANK.get(g, 0)) if grades else None


def _nested_files(nested: Any) -> list[Mapping[str, Any]] | None:
    """The graded nested files, or None when the block did not measure them."""
    if not isinstance(nested, Mapping) or nested.get("available") is False:
        return None
    files = nested.get("files")
    if not isinstance(files, list):
        return None
    return [f for f in files if isinstance(f, Mapping)]


def _always_loaded(entry: Mapping[str, Any]) -> bool:
    """Loaded at session start for the root context: always loaded and at the root.

    Cursor's ``alwaysApply`` marks a rule always loaded whatever its directory, so a
    nested ``pkg/.cursor/rules/x.mdc`` is not a root file (the same test as
    ``agent_consistency``'s root context).
    """
    scope = entry.get("scope")
    return (isinstance(scope, Mapping) and scope.get("always_loaded") is True
            and scope.get("directory") == ".")


def _grades(files: list[Mapping[str, Any]]) -> list[str]:
    return [f["grade"] for f in files if isinstance(f.get("grade"), str)]


def instruction_presence(
    root_grade: str | None, nested: Any,
) -> tuple[InstructionPresence | None, str | None]:
    """``(presence, grade)`` from the root grade and the ``nested_instructions`` block."""
    files = _nested_files(nested)
    if root_grade is not None:
        return ROOT, root_grade
    if files is None:
        return None, None
    loaded = [f for f in files if _always_loaded(f)]
    if loaded:
        return ROOT, _best(_grades(loaded))
    if files:
        return NESTED_ONLY, _best(_grades(files))
    return NONE, None


def scan_layer_grades(instruction_grading: tuple[Any, ...], nested_instructions: Any) -> LayerGradesBlock:
    """The block from ``run_setup.instruction_grading``'s ``(files, best_grade, ...)``
    and the ``nested_instructions`` block."""
    grade = instruction_grading[1]
    presence, best = instruction_presence(grade if isinstance(grade, str) else None,
                                          nested_instructions)
    present = None if presence is None else presence != NONE
    return {"instructions_grade": best, "instruction_file_present": present,
            "instruction_presence": presence}
