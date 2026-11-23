"""Keeping one report on one maturity model when a run uses a profile or a --levels file (#1150).

A report must never show two Steps. ``lib/profiles.py`` lets a run choose its model; this
module keeps the parts of the report that compare or redraw the level on that same model:

- ``model_name(version)`` names the model a ``levels_version`` came from: ``default`` for any
  ``default-<date>`` version (the default model across releases is one model), the profile
  name for ``profile-<name>-<date>``, else the version itself (a custom file).
- ``model_for_level(level)`` is the model a ``level`` block was computed with when the
  report can reread it: the default model, or the shipped profile of that name. None for
  a custom file, or for a block that names no version. ``custom_run(ctx)`` is True when the
  run's executive summary opens with the custom-model line (``profiles.CUSTOM_MODEL_LINE``);
  the Step ladder and the evidence waffle (``lib/assess_diagrams.py``) are then not drawn,
  with ``CUSTOM_NOT_DRAWN`` in their place, rather than drawn from the default model beside
  a summary on another. The version alone cannot say so: any string is a valid version.
- ``previous_levels_version(assess_dir, superseded)`` is the ``levels_version`` the previous
  run's level was computed with, read in the order ``since_last_run`` reads its baseline:
  the superseded same-day run's ``level`` block, then ``prior-level.json`` when it carries a
  baseline (its ``levels_version`` was stamped with it), then ``run-context.json``. None
  when none of them names one.
- ``across_models(block, level, previous)`` is the ``since_last_run`` block unchanged when
  the two runs used one model (or either is unknown), else with ``gates_gained`` and
  ``gates_lost`` None and their two lines replaced by ``Model changed since last run
  (default → handover); gates not compared``: a gate is never reported gained or lost across
  models. Closed actions stay, because they are commits, not gates. ``model_changed_line``
  gives that line back for the executive summary (``lib/exec_summary.py``).
- ``units_label(block, choice)`` marks the monorepo unit roll-up "(default model)" when the
  run's own model is not the default, since the unit pass still reads the default model.

Tests: ``tests/test_model_change.py``.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from lib.levels import LevelsError, MaturityModel, load_levels
from lib.level_tally import default_model
from lib.prior_level import load_prior_level
from lib.profiles import CUSTOM_MODEL_LINE, ModelChoice, profile_names, profile_path
from lib.run_context_types_since import SinceLastRun
from lib.unit_rollup import UnitRollUpBlock

__all__ = ["CUSTOM_NOT_DRAWN", "MODEL_CHANGED", "across_models", "custom_run", "model_changed_line",
           "model_for_level",
           "model_name", "previous_levels_version", "units_label"]

MODEL_CHANGED = "Model changed since last run ({} → {}); gates not compared"
CUSTOM_NOT_DRAWN = ("the run used a custom maturity model (--levels), which the report cannot redraw; "
                    "the summary at the top names it and lists what is missing.")
_GATE_LINES = ("Gates gained:", "Gates lost:")


def model_name(version: str) -> str:
    """``default``, a profile name, or the version itself for a custom model."""
    if version.startswith("default-"):
        return "default"
    if version.startswith("profile-") and "-" in version[len("profile-"):]:
        return version[len("profile-"):].rpartition("-")[0]
    return version


def model_for_level(level: Any) -> MaturityModel | None:
    """The model a ``level`` block was computed with, when it is the default or a shipped profile."""
    version = level.get("levels_version") if isinstance(level, Mapping) else None
    if not isinstance(version, str):
        return None
    name = model_name(version)
    if name == "default":
        return default_model()
    if version.startswith("profile-") and name in profile_names():
        try:
            return load_levels(profile_path(name))
        except LevelsError:
            return None
    return None


def custom_run(ctx: Mapping[str, Any]) -> bool:
    """True when the run's executive summary names a custom ``--levels`` model
    (``profiles.model_lines`` writes that first line, and only for such a run)."""
    block = ctx.get("headline")
    lines = block.get("summary_lines") if isinstance(block, Mapping) else None
    return isinstance(lines, list) and bool(lines) and isinstance(lines[0], str) \
        and lines[0].startswith(CUSTOM_MODEL_LINE)


def _version(level: Any) -> str | None:
    version = level.get("levels_version") if isinstance(level, Mapping) else None
    return version if isinstance(version, str) else None


def previous_levels_version(assess_dir: Path, superseded: Any) -> str | None:
    """The previous run's ``levels_version``, read in ``since_last_run``'s baseline order."""
    if isinstance(superseded, Mapping):
        inherited = superseded.get("since_last_run")
        if isinstance(inherited, Mapping) and inherited.get("available") is True:
            return _version(superseded.get("level"))
    prior = load_prior_level(assess_dir)
    if isinstance(prior, Mapping) and isinstance(prior.get("baseline"), Mapping):
        return _version(prior)
    try:
        data = json.loads((assess_dir / "run-context.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return _version(data.get("level")) if isinstance(data, Mapping) else None


def across_models(block: SinceLastRun, level: Any, previous: str | None) -> SinceLastRun:
    """``block``, with no gate comparison when the previous run used another model."""
    current = _version(level)
    if (block.get("available") is not True or block.get("first_run") or previous is None
            or current is None or model_name(previous) == model_name(current)):
        return block
    line = MODEL_CHANGED.format(model_name(previous), model_name(current))
    lines = [entry for entry in block["lines"] if not entry.startswith(_GATE_LINES)]
    at = next((i for i, entry in enumerate(lines) if entry.startswith("Next blocker:")), len(lines))
    lines.insert(at, line)
    markdown = "\n".join(f"  - {e.strip()}" if e.startswith("  ") else f"- {e}" for e in lines)
    return cast(SinceLastRun, {**block, "gates_gained": None, "gates_lost": None,
                               "lines": lines, "markdown": markdown})


def model_changed_line(since: Any) -> str | None:
    """The model-changed line of a ``since_last_run`` block, or None when it has none."""
    lines = since.get("lines") if isinstance(since, Mapping) else None
    head = MODEL_CHANGED.split("(")[0]
    return next((e for e in lines if isinstance(e, str) and e.startswith(head)), None) \
        if isinstance(lines, list) else None


def units_label(block: UnitRollUpBlock, choice: ModelChoice | None) -> UnitRollUpBlock:
    """``block`` with its roll-up line marked "(default model)" under a non-default model."""
    if choice is None or choice.kind == "default" or not block.get("lines"):
        return block
    lines = [block["lines"][0].replace("Units:", "Units (default model):", 1), *block["lines"][1:]]
    return {**block, "lines": lines, "markdown": "\n\n".join(lines)}
