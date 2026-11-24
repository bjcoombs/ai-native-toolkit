"""Named maturity-model profiles and the model choice a single-repository run uses (#1150).

A profile is a shipped maturity model under ``levels/profiles/<name>.toml``, selected by
name with ``--profile <name>`` on ``assess_core.py`` and ``fleet.py``; ``--levels FILE``
selects a custom model instead, and neither flag keeps the default
(``levels/default.toml``). ``add_model_arguments`` adds both flags, mutually exclusive;
``resolve`` turns them into a ``ModelChoice`` (the loaded model, its ``kind`` -
``default``, ``profile`` or ``file`` - and its name), raising ``ProfileError`` on an unknown
profile or a malformed model. ``profile_path`` gives a profile's file, so ``/fleet``
turns ``--profile`` into its ``--levels`` and every later ``--levels`` check holds.

Built-in profiles reuse the default model's practices: each row is a default row of the
same id, title and criteria, cut to fewer levels, so a profile never redefines a signal
(``tests/test_profiles.py``). ``handover`` is the bar a production team expects before it
takes a repository over; ``citizen`` is the lighter bar while a citizen developer builds.
Each file justifies every practice it holds in one line.

A single-repository run evaluates the chosen model over the run context's ``level``
reads (``level_tally.LEVEL_READS``: the blocks the default model reads), so
``single_repo_choice`` refuses a custom model whose criteria read any other block, naming
it, rather than reporting its criteria as not measured; over those blocks the run gives
the same Step as ``/fleet --levels`` for the same repository.

``model_lines(level, choice)`` are the lines the executive summary (``lib/exec_summary.py``)
adds for a model that is not the default, ``[]`` for the default; ``model_facts`` returns the
same lines as ``card_facts.CardFact`` entries with the fact ids ``model``, ``model_holds`` and
``model_extra`` (#1253):

1. ``Maturity model: profile handover, not the default.`` (``Maturity model: my.toml, a
   custom file, not the default.`` for ``--levels``). A custom file's ``version`` may not
   start ``default-`` or ``profile-`` (``RESERVED_PREFIXES``, refused by ``resolve``):
   those name the shipped models, and the report tells models apart by version;
2. the readiness line: ``Ready for handover (profile: handover).`` when every practice
   holds, else ``Not ready for handover (profile: handover). Missing: <practice> (<state>).
   Missing: ...``, one sentence per practice, (``READY_WORDS``; ``Every practice met`` and ``Not every practice met``
   for a model the table does not name), naming each condition that does not hold, in model order. A positive condition
   holds only when met, a hazard only when unmet; not measured, excluded by the owner and
   a ``requires`` not met are missing, each with its reason, so unknown never reads ready.
   A practice is its row's title, with ``, level N`` above level 1: like the rest of the
   summary, the lines name no condition id;
3. when the model holds practices the default model does not define the same way, ``Not
   in the default model: <practice>; ...``; a missing practice among them carries ``[not in
   the default model]`` on line 2 too.

``model_for_level(level)`` is the model a ``level`` block names, the default or a shipped profile
(None for a custom model); ``since_last_run``, the committed baseline and the gate read a run's
own Steps through it (#1184).

``run_level(ctx, choice)`` is the ``level`` block under the choice and ``chosen_model`` its
model; the ``level`` scan and the ``--opt-in-mutation`` recount both call them, as
``exec_summary.run_headline`` for the headline.

Tests: ``tests/test_profiles.py``.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from lib.card_facts import CardFact
from lib.headline import reason_word
from lib.level_result_types import ConditionResult, LevelResult
from lib.level_tally import DEFAULT_BLOCKS, default_model, level_result, model_blocks, run_context_level
from lib.level_tally import run_exclusions
from lib.levels import LevelsError, MaturityModel, PathValidator, load_levels
from lib.prior_level import model_key
from lib.signal_grades import DEFAULT_LEVELS

__all__ = ["CUSTOM_MODEL_SUFFIX", "PROFILES_DIR", "RESERVED_PREFIXES", "READY_WORDS", "ModelChoice", "ProfileError", "add_model_arguments",
           "chosen_model", "model_for_level", "model_facts", "model_lines", "non_default_ids", "profile_names", "profile_path", "resolve",
           "run_level", "single_repo_choice"]

PROFILES_DIR = DEFAULT_LEVELS.parent / "profiles"

# What a run reads, before "(profile: <name>)", when every practice of a profile holds and
# when one does not.
READY_WORDS = {"handover": ("Ready for handover", "Not ready for handover"),
               "citizen": ("Meets the citizen bar", "Below the citizen bar")}
_READY_DEFAULT = ("Every practice met", "Not every practice met")

ModelKind = Literal["default", "profile", "file"]

# How the summary's first line ends for a custom --levels model (lib/model_change.custom_run).
CUSTOM_MODEL_SUFFIX = ", a custom file, not the default."
# Version prefixes that name the shipped models (lib/model_change.model_name): a custom file
# may not use them, or the report would read it as the shipped model.
RESERVED_PREFIXES = ("default-", "profile-")


class ProfileError(ValueError):
    """An unknown profile, a malformed model, or a model a single-repository run cannot read."""


@dataclass(frozen=True)
class ModelChoice:
    """The maturity model a run evaluates, and how it was chosen."""

    model: MaturityModel
    kind: ModelKind
    name: str  # the profile name, the --levels file name, or "default"

    @property
    def label(self) -> str:
        """``profile: handover``, ``model: my.toml``, or ``model: default``."""
        return f"profile: {self.name}" if self.kind == "profile" else f"model: {self.name}"


def chosen_model(choice: ModelChoice | None) -> MaturityModel:
    """The model a run evaluates: ``choice``'s, or the default model when it is None."""
    return default_model() if choice is None else choice.model


def run_level(ctx: Mapping[str, Any], choice: ModelChoice | None) -> LevelResult:
    """The run context's ``level`` block under ``choice`` (the default model when None), with
    the owner's exclusions from its ``owner_exclusions`` block."""
    if choice is None:
        return run_context_level(ctx)
    return level_result(choice.model, ctx, run_exclusions(ctx))


def model_for_level(level: Any) -> MaturityModel | None:
    """The model a ``level`` block was computed with, when it is the default or a shipped profile
    (``prior_level.model_key``); None for a custom ``--levels`` model or a profile not shipped."""
    version = level.get("levels_version") if isinstance(level, Mapping) else None
    key = model_key(level) if isinstance(version, str) else None
    if key == "default":
        return default_model()
    if key is None or key not in profile_names():
        return None
    try:
        return load_levels(profile_path(key))
    except LevelsError:
        return None


def profile_names() -> tuple[str, ...]:
    """The shipped profile names, sorted."""
    return tuple(sorted(p.stem for p in PROFILES_DIR.glob("*.toml")))


def profile_path(name: str) -> Path:
    """The file of profile ``name``; ``ProfileError`` naming the known profiles otherwise."""
    if name not in profile_names():
        raise ProfileError(f"unknown profile {name!r}; the profiles are: {', '.join(profile_names())}")
    return PROFILES_DIR / f"{name}.toml"


def add_model_arguments(parser: argparse.ArgumentParser, *, levels: bool = True) -> None:
    """``--profile NAME`` and (unless ``levels`` is False) ``--levels FILE``, mutually exclusive."""
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--profile", metavar="NAME",
        help=f"Evaluate a shipped maturity-model profile ({', '.join(profile_names())}) instead of "
             "the default model. The report's summary names it and says whether every practice holds.")
    if levels:
        group.add_argument(
            "--levels", metavar="FILE",
            help="Evaluate a custom maturity model (TOML, lib/levels.py format) instead of the default.")


def resolve(profile: str | None, levels: str | Path | None,
            validator: PathValidator | None = None) -> ModelChoice:
    """The model ``--profile`` or ``--levels`` names, else the default; ``ProfileError`` on a
    bad name, both flags at once, or a model ``lib.levels`` refuses."""
    if profile is not None and levels is not None:
        raise ProfileError("--profile and --levels each choose the model: pass one")
    try:
        if profile is not None:
            return ModelChoice(load_levels(profile_path(profile), validator), "profile", profile)
        if levels is not None:
            path = Path(levels)
            model = load_levels(path, validator)
            if model["version"].startswith(RESERVED_PREFIXES) or model["version"] in ("default", *profile_names()):
                raise ProfileError(
                    f"{path.name} has version {model['version']!r}: 'default', a profile name and versions "
                    f"starting {' or '.join(RESERVED_PREFIXES)} name the shipped models, so give a custom "
                    "model its own")
            return ModelChoice(model, "file", path.name)
    except (LevelsError, OSError) as e:
        raise ProfileError(str(e)) from e
    return ModelChoice(default_model(), "default", "default")


def single_repo_choice(profile: str | None, levels: str | Path | None,
                       validator: PathValidator | None = None) -> ModelChoice:
    """``resolve``, refusing a model that reads a block the single-repository level does not."""
    choice = resolve(profile, levels, validator)
    outside = sorted(set(model_blocks(choice.model)) - set(DEFAULT_BLOCKS))
    if outside:
        raise ProfileError(
            f"{choice.label} reads {', '.join(outside)}, which the single-repository level does not "
            f"read (it reads the default model's blocks: {', '.join(DEFAULT_BLOCKS)}); "
            "use /fleet --levels for this model")
    return choice


def _definitions(model: MaturityModel) -> dict[str, tuple[object, ...]]:
    """Each condition id with what defines it: the row's title and scope and the level's fields."""
    return {f"{row['id']}.{level['level']}": (row["title"], row["scope"], sorted(level.items()))
            for row in model["rows"] for level in row["levels"]}


def non_default_ids(model: MaturityModel) -> list[str]:
    """The condition ids ``model`` defines that the default model does not define the same way."""
    default = _definitions(default_model())
    return [cid for cid, d in _definitions(model).items() if default.get(cid) != d]


def _holds(condition: ConditionResult) -> bool:
    """Met for a positive practice, unmet for a hazard; any other state (not measured,
    excluded, declined) never holds, so an unknown never reads ready."""
    state = condition["state"]
    return state == ("unmet" if condition.get("polarity") == "hazard" else "met")


def _state_words(condition: ConditionResult) -> str:
    state = condition["state"]
    if condition.get("disposition") == "excluded_by_owner":
        return f"excluded by owner: {condition.get('exclusion_kind', 'other').replace('_', ' ')}"
    reason = condition.get("reason", "no_value")
    if reason.startswith("requires:"):
        return reason_word(reason)
    if state == "not_measured":
        return f"not measured: {reason_word(reason)}"
    if condition.get("polarity") == "hazard":
        return "hazard present"
    return "not met"


def _practice(cid: str, model: MaturityModel) -> str:
    """A condition in words: its row's title, with the level when it is above 1."""
    row_id, _, number = cid.rpartition(".")
    title = next((row["title"] for row in model["rows"] if row["id"] == row_id), row_id)
    return title if number == "1" else f"{title}, level {number}"


def _readiness(level: LevelResult, choice: ModelChoice, foreign: set[str]) -> str:
    ready, not_ready = READY_WORDS.get(choice.name, _READY_DEFAULT) if choice.kind == "profile" \
        else _READY_DEFAULT
    missing = [c for c in level["conditions"] if not _holds(c)]
    if not missing:
        return f"{ready} ({choice.label})."
    parts = [f"{_practice(c['id'], choice.model)} ({_state_words(c)})"
             + (" [not in the default model]" if c["id"] in foreign else "") for c in missing]
    # one short sentence per missing practice, so the line stays plain however many are missing
    return " ".join([f"{not_ready} ({choice.label}).", *(f"Missing: {part}." for part in parts)])


def model_facts(level: LevelResult, choice: ModelChoice | None) -> list[CardFact]:
    """The summary lines naming a non-default model and whether it holds, each with its fact
    id; ``[]`` for the default."""
    if choice is None or choice.kind == "default":
        return []
    first = (f"Maturity model: profile {choice.name}, not the default." if choice.kind == "profile"
             else f"Maturity model: {choice.name}{CUSTOM_MODEL_SUFFIX}")
    foreign = non_default_ids(choice.model)
    facts: list[CardFact] = [{"fact_id": "model", "text": first},
                             {"fact_id": "model_holds", "text": _readiness(level, choice, set(foreign))}]
    if foreign:
        facts.append({"fact_id": "model_extra", "text": "Not in the default model: "
                      f"{'; '.join(_practice(c, choice.model) for c in foreign)}."})
    return facts


def model_lines(level: LevelResult, choice: ModelChoice | None) -> list[str]:
    """``model_facts`` as plain text."""
    return [f["text"] for f in model_facts(level, choice)]
