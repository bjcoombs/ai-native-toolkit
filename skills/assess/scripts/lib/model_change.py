"""Keeping one report on one maturity model when a run uses a profile or a --levels file (#1150).

A report must never show two Steps. ``lib/profiles.py`` lets a run choose its model; this
module keeps the parts of the report that compare or redraw the level on that same model:

- ``model_name(version)`` names the model a ``levels_version`` came from: ``default`` for any
  ``default-<date>`` version (the default model across releases is one model), the profile
  name for ``profile-<name>-<date>``, else the version itself (a custom file).
- ``model_for_level(level)`` is the model a ``level`` block was computed with when the
  report can reread it: the default model, or the shipped profile of that name. None for
  a custom file, or for a block that names no version. ``custom_run(ctx)`` is True when the
  run's executive summary opens with the custom-model line (``profiles.CUSTOM_MODEL_SUFFIX``),
  or, when the headline is degraded, when the level holds a condition the default model
  does not define;
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
- ``across_packs(block, level, record, packs)`` does the same when a practice pack of the run
  changed content since ``record``, the run's own committed record, was counted (#1316): a
  pack edited in place keeps its name and so its record, so its sha256 tells the edit apart.
  ``PACK_CHANGED`` names the changed packs, and ``PACK_NOT_RECORDED`` those a record written
  before #1316 holds no sha256 for, which are never read as unchanged. A run that supersedes a
  same-day run on the same commit keeps the pack lines that run showed.
- ``units_label(block, choice)`` marks the monorepo unit roll-up "(default model)" when the
  run's own model is not the default, since the unit pass still reads the default model.

Tests: ``tests/test_model_change.py``.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from lib.scoring.level_tally import default_model
from lib.prior_level import load_prior_level, pack_change, record_for
from lib.profiles import CUSTOM_MODEL_SUFFIX, ModelChoice, model_for_level
from lib.run_context_types_since import SinceLastRun
from lib.unit_rollup import UnitRollUpBlock

__all__ = ["CUSTOM_NOT_DRAWN", "MODEL_CHANGED", "PACK_CHANGED", "PACK_NOT_RECORDED", "across_models", "across_packs", "changed_models", "custom_run", "model_changed_line",
           "model_for_level",
           "model_name", "previous_levels_version", "units_label"]

MODEL_CHANGED = "Model changed since last run ({} → {}); gates not compared"
PACK_CHANGED = "Pack changed since last run ({}); gates not compared"
PACK_NOT_RECORDED = "Pack contents not recorded by last run ({}); gates not compared"
CUSTOM_NOT_DRAWN = ("the run used a custom maturity model (--levels), which the report cannot redraw; "
                    "the summary at the top names it and lists what is missing.")
_GATE_LINES = ("Gates gained:", "Gates lost:")
_PACK_HEADS = (PACK_CHANGED.partition("(")[0], PACK_NOT_RECORDED.partition("(")[0])


def model_name(version: str) -> str:
    """``default``, a profile name, or the version itself for a custom model."""
    if version.startswith("default-"):
        return "default"
    if version.startswith("profile-") and "-" in version[len("profile-"):]:
        return version[len("profile-"):].rpartition("-")[0]
    return version


def custom_run(ctx: Mapping[str, Any]) -> bool:
    """True when the run's level cannot be from the default model: the executive summary
    names a custom ``--levels`` model (``profiles.model_lines`` writes that first line, and
    only for such a run), or, with the run's headline block degraded, the level holds a
    condition the default model does not define."""
    block = ctx.get("headline")
    lines = block.get("summary_lines") if isinstance(block, Mapping) else None
    if isinstance(lines, list) and lines and isinstance(lines[0], str) \
            and lines[0].startswith("Maturity model: ") and lines[0].endswith(CUSTOM_MODEL_SUFFIX):
        return True
    if not isinstance(block, Mapping) or block.get("available") is True:
        return False  # no headline block at all is not a run's context; a whole one decided above
    level = ctx.get("level")
    conditions = level.get("conditions") if isinstance(level, Mapping) else None
    if not isinstance(conditions, list):
        return False
    known = {f"{r['id']}.{lv['level']}" for r in default_model()["rows"] for lv in r["levels"]}
    return any(isinstance(c, Mapping) and c.get("id") not in known for c in conditions)


def _version(level: Any) -> str | None:
    version = level.get("levels_version") if isinstance(level, Mapping) else None
    return version if isinstance(version, str) else None


def previous_levels_version(assess_dir: Path, superseded: Any, level: Any = None) -> str | None:
    """The previous run's ``levels_version``, read in ``since_last_run``'s baseline order: the
    committed record of ``level``'s model in ``prior-level.json`` (#1184), then ``run-context.json``."""
    if isinstance(superseded, Mapping):
        inherited = superseded.get("since_last_run")
        if isinstance(inherited, Mapping) and inherited.get("available") is True:
            return _version(superseded.get("level"))
    prior = record_for(load_prior_level(assess_dir), level)
    if prior is not None and "baseline" in prior:
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
    return _not_compared(block, [MODEL_CHANGED.format(model_name(previous), model_name(current))])


def across_packs(block: SinceLastRun, level: Any, record: Any, packs: Any, superseded: Any = None) -> SinceLastRun:
    """``block``, with no gate comparison when a pack of the run changed since ``record`` (#1316).

    A run that supersedes a same-day run on the same commit inherits that run's gate changes,
    and ``record`` is then the superseded run's own, so the pack lines that run showed are
    carried over: an inherited "not compared" keeps its reason."""
    if block.get("available") is not True or block.get("first_run"):
        return block
    changed, unrecorded = pack_change(record, level, packs)
    added = [*([PACK_CHANGED.format(", ".join(changed))] if changed else []),
             *([PACK_NOT_RECORDED.format(", ".join(unrecorded))] if unrecorded else [])]
    inherited = superseded.get("since_last_run") if isinstance(superseded, Mapping) else None
    if not added and isinstance(inherited, Mapping) and inherited.get("available") is True:
        lines = inherited.get("lines")
        added = [e for e in lines if isinstance(e, str) and e.startswith(_PACK_HEADS)] if isinstance(lines, list) else []
    return _not_compared(block, added) if added else block


def _not_compared(block: SinceLastRun, added: list[str]) -> SinceLastRun:
    """``block`` with ``gates_gained`` and ``gates_lost`` None and their lines replaced by ``added``."""
    lines = [entry for entry in block["lines"] if not entry.startswith(_GATE_LINES)]
    at = next((i for i, entry in enumerate(lines) if entry.startswith("Next blocker:")), len(lines))
    lines[at:at] = added
    return cast(SinceLastRun, {**block, "gates_gained": None, "gates_lost": None,
                               "lines": lines, "markdown": _markdown(lines)})


def _markdown(lines: list[str]) -> str:
    return "\n".join(f"  - {e.strip()}" if e.startswith("  ") else f"- {e}" for e in lines)


def changed_models(since: Any) -> tuple[str, str] | None:
    """The (previous, current) model names of a ``since_last_run`` model-changed line, or None."""
    line = model_changed_line(since)
    head, _, tail = MODEL_CHANGED.partition("{} → {}")
    if line is None or not line.endswith(tail):
        return None
    before, arrow, after = line[len(head):len(line) - len(tail)].partition(" → ")
    return (before, after) if arrow else None


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
