"""Deterministic layer grades as one signals block (issue #620).

The core computes some per-practice grades with no model in the path, but keeps
them as run-context header keys (``instructions_grade``) that the headless
signals document never carries, so a maturity model could not name them. This
block republishes them under one registered scan, ``layer_grades``: the same
name keys it in a headless document's ``scans`` and in a full
``run-context.json``, so ``lib.scoring.level_signals`` accepts its paths and one
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
  - ``stub`` (#922): every root file holds nothing beyond headings and blank
    lines (``instruction_files.is_heading_only``; an alias inherits its target's
    value), and no nested file exists. A file is committed, but it tells an
    agent nothing. A stub root file with a nested file beside it reads by the
    nested file (``root`` when always loaded, else ``nested_only``).
  - ``none``: no instruction file at any known location.

  Null only when there is no root file with a body (none, or only stubs) and
  the ``nested_instructions`` block is unavailable, so whether a nested file
  exists was not measured. ``instructions_grade`` keeps a stub's grade then.
- ``instructions_grade``: the best letter grade of the files ``instruction_presence``
  counts: the root grading (``instruction_files.grade_instruction_files``, the
  value of the run-context key of the same name) when a root location holds a
  file, else the best grade among the always-loaded or, for ``nested_only``,
  all nested files. Null only for ``none`` (or the unmeasured null above), the
  CLAUDE.md invariant: null means no instruction file at any known location.
- ``instruction_file_present``: whether ``instruction_presence`` is ``root`` or
  ``nested_only``; false for ``stub`` and ``none``, null with it. The default maturity model reads this for
  Layer 0 level 1, so a nested-only repository meets that level (harnesses load
  nested ``AGENTS.md`` where the agent works; maintainer decision on #821), and
  its advice is the ``gap_actions`` entry whose ``signal`` is ``layer_grades``
  (``lib.report.gap_actions``): add a root file that names the check command.

Only Layer 0 has a deterministic grade today. Layers 2 (code design), 3
(linters) and 4 (architecture tests) are judged by the assess-layer-scorer
reading configuration and code, so no field stands for them here. The default
maturity model's rows for them read proxies from other blocks instead.

Grades and the presence value are short tokens and the flag a boolean, so the
counts-only filter keeps the block whole; no path is carried. Pure: it reads the
setup grading and the nested block the core already built.

Deterministic layer grades as one registered block (issue #620), so the headless
signals document carries them and a maturity model can name them.
`scan_layer_grades(instruction_grading, nested_instructions)` reads the setup grading
(`run_setup.instruction_grading`) and the `nested_instructions` block and writes
`{instructions_grade, instruction_file_present, instruction_presence}`.
`instruction_presence` (issue #821) is three-way: `root` (a tracked file at a root
location, or a discovered file its tool loads at session start, `scope.always_loaded`:
always loaded), `nested_only` (no root file, but `nested_instructions.files` lists one,
such as `pkg/AGENTS.md`: present, loaded on demand) or `none` (no instruction file at any
known location); null only when there is no root file and the nested block is
unavailable, so a nested file was not looked for. `instructions_grade` is the best letter
grade of the files that value counts (the root grading, the top-level
`instructions_grade` value, which stays root-only; else the always-loaded or nested
grades), null only for `none`. `instruction_file_present` is true for `root` and
`nested_only` and false for `stub` (every root file is headings only, #922) and `none`
(null with the presence), so a maturity model scores
"no instruction file" as level 0 rather than unknown and a nested-only repository meets
Layer 0 level 1 (maintainer decision on #821); its advice is the `gap_actions`
`layer_grades` entry. Only Layer 0 has a deterministic grade; Layers 2-4 have none (the
default maturity model's rows for them read proxies, and its header says how they diverge). A short token and a boolean, kept whole by the
counts-only filter. Tests: `tests/test_layer_grades.py`, and the maturity model rows in
`tests/test_level_signals.py`.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypedDict

from lib.instructions.instruction_files import GRADE_RANK

InstructionPresence = Literal["root", "nested_only", "stub", "none"]

ROOT: InstructionPresence = "root"
NESTED_ONLY: InstructionPresence = "nested_only"
STUB: InstructionPresence = "stub"
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


def _root_files(instruction_grading: tuple[Any, ...]) -> list[Mapping[str, Any]]:
    files = instruction_grading[0] if instruction_grading else None
    return [m for m in files.values() if isinstance(m, Mapping)] if isinstance(files, Mapping) else []


def scan_layer_grades(instruction_grading: tuple[Any, ...], nested_instructions: Any) -> LayerGradesBlock:
    """The block from ``run_setup.instruction_grading``'s ``(files, best_grade, ...)``
    and the ``nested_instructions`` block."""
    grade = instruction_grading[1]
    root_grade = grade if isinstance(grade, str) else None
    files = _root_files(instruction_grading)
    stub_grade = None
    if root_grade is not None and files and all(f.get("heading_only") is True for f in files):
        stub_grade, root_grade = root_grade, None  # every root file is a stub (#922)
    presence, best = instruction_presence(root_grade, nested_instructions)
    if stub_grade is not None and presence == NONE:
        presence, best = STUB, stub_grade
    elif stub_grade is not None and presence is None:
        best = stub_grade  # a file is committed: the grade stays non-null (CLAUDE.md invariant)
    present = None if presence is None else presence not in (NONE, STUB)
    return {"instructions_grade": best, "instruction_file_present": present,
            "instruction_presence": presence}
