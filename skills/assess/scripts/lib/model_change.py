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
- ``on_own_steps(block, level, previous)`` compares the gates of two runs of one model on
  that model's own Steps when ``since_last_run`` (which reads the default Steps) left them
  unknown: a profile with fewer Steps, such as ``citizen``. ``previous`` is the level block
  of the ``run-context.json`` the earlier run left (``previous_level``). Two runs of one
  profile then read their gains and losses as two default runs do. A default-model run is
  never refilled (``since_last_run`` already reads its Steps), nor a run that inherited a
  superseded same-day run's block (``previous_level`` gives None). A block ``across_models``
  already marked as a model change stays as it is, even when ``run-context.json`` names the
  same model (the baseline in ``prior-level.json`` decided it, as for ``since_last_run``).
- ``units_label(block, choice)`` marks the monorepo unit roll-up "(default model)" when the
  run's own model is not the default, since the unit pass still reads the default model.

Tests: ``tests/test_model_change.py``.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from lib.headline import GATE_NOUN
from lib.levels import LevelsError, MaturityModel, load_levels
from lib.level_tally import default_model
from lib.prior_level import default_level, load_prior_level
from lib.profiles import CUSTOM_MODEL_SUFFIX, ModelChoice, profile_names, profile_path
from lib.run_context_types_since import SinceLastRun
from lib.since_last_run import gate_changes
from lib.unit_rollup import UnitRollUpBlock

__all__ = ["CUSTOM_NOT_DRAWN", "MODEL_CHANGED", "across_models", "changed_models", "custom_run", "model_changed_line",
           "model_for_level", "on_own_steps", "previous_level",
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
    """The previous run's ``levels_version``, read in ``since_last_run``'s baseline order; under a
    profile or ``--levels`` ``level`` that order skips ``prior-level.json``, as it does."""
    if isinstance(superseded, Mapping):
        inherited = superseded.get("since_last_run")
        if isinstance(inherited, Mapping) and inherited.get("available") is True:
            return _version(superseded.get("level"))
    prior = load_prior_level(assess_dir) if default_level(level) else None
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
    return cast(SinceLastRun, {**block, "gates_gained": None, "gates_lost": None,
                               "lines": lines, "markdown": _markdown(lines)})


def _markdown(lines: list[str]) -> str:
    return "\n".join(f"  - {e.strip()}" if e.startswith("  ") else f"- {e}" for e in lines)


def previous_level(assess_dir: Path, superseded: Any = None) -> Mapping[str, Any] | None:
    """The ``level`` block of the ``run-context.json`` the previous run left, or None; None
    too when ``since_last_run`` inherited a superseded same-day run's block, since that file
    is the superseded run itself, not the run before it."""
    if isinstance(superseded, Mapping):
        inherited = superseded.get("since_last_run")
        if isinstance(inherited, Mapping) and inherited.get("available") is True:
            return None
    try:
        data = json.loads((assess_dir / "run-context.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    level = data.get("level") if isinstance(data, Mapping) else None
    return level if isinstance(level, Mapping) else None


def on_own_steps(block: SinceLastRun, level: Any, previous: Any) -> SinceLastRun:
    """``block`` with the gates compared on the run's own Steps, when two runs of one
    non-default model left them unknown (``since_last_run`` reads the default Steps, which a
    profile with fewer Steps, such as ``citizen``, cannot fill)."""
    model, version, before = model_for_level(level), _version(level), _version(previous)
    if (block.get("available") is not True or block.get("first_run") or block.get("gates_gained") is not None
            or model_changed_line(block) is not None or model is None or version is None or before is None
            or model_name(version) == "default" or model_name(before) != model_name(version)):
        return block
    gained, lost = gate_changes(model.get("steps", []), level, previous)
    if gained is None or lost is None:
        return block
    gates = {"Gates gained:": gained, "Gates lost:": lost}
    lines = [next((f"{k} {', '.join(GATE_NOUN.get(g, g) for g in v) or 'none'}"
                   for k, v in gates.items() if e.startswith(k)), e) for e in block["lines"]]
    return cast(SinceLastRun, {**block, "gates_gained": gained, "gates_lost": lost,
                               "lines": lines, "markdown": _markdown(lines)})


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
