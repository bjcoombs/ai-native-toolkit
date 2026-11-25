"""``prior-level.json``: this run's per-tag values, kept for the next run's tag profile strip (#940).

Each run writes ``prior-level.json`` into its artifact directory (``.assess/`` or
``.assess/<scope-slug>/``) with the maturity-model version (``levels_version``) and the
``level`` block's ``tags`` rows (``lib/scoring/level_tags.py``). ``run_publish.publish_run`` reads the
previous file into the run context's ``prior_level`` key **before** it draws the diagrams and
rewrites the file, as ``prior_findings_snapshot`` is read before ``findings.json`` is
rewritten, so the strip (``lib/assess_diagrams_tags.py``) draws the earlier run from the
context and never from the file, and a later redraw (``assess_report.py``, the opt-in mutation
pass) reads the same earlier run. ``assess_core.run_opt_in_mutation`` rewrites the file from
the recounted level without reading it again.

``load_prior_level`` returns None when there is no file, and ``UNREADABLE``
(``{"available": false, "reason": "unreadable"}``) for a file that exists but is not JSON or
is any shape other than ``PriorLevel`` (a version this reader does not know, a tag outside
``TRUTH_TAGS``, a count that is not a non-negative integer, or counts that do not nest as
``met <= measured <= countable <= conditions`` with ``measured + set_aside <= countable``):
an unreadable earlier run is never drawn, and the strip says it could not be read.
``write_prior_level`` writes nothing when the level result has no ``tags`` (a degraded level
block) and leaves the previous good run's file as it was (#1158), so its baseline and pruning
ledger survive one failed run. A custom ``--levels`` run writes nothing (``model_key`` None,
#1150); a profile run writes only its own record (#1184, below), and leaves a file it cannot
read untouched, so it never drops the default record the CI gate reads. The strip still
compares only runs counted under the same model (``same_model``).
The strip draws the earlier run only when its ``levels_version`` equals this run's
(``same_model``); otherwise it says why in one line.

Version 2 (#1122) adds ``baseline``: ``{run_id, head_sha, held_gates}``, the run's id, the
commit it measured and the Step gates it held (``since_last_run.baseline_record``). The file is
committed while ``run-context.json`` is gitignored, so this is the baseline "since last run"
reads first: a fresh clone or a CI checkout has it. ``load_baseline`` returns it, or None when
the file has none (a version-1 file, which this reader still accepts, a run with no commit,
or a malformed baseline, which is dropped while the tags still read). ``pruning`` (#1084)
holds the pruning ledger's removals beside it (``PruningEntry``, ``load_pruning``), so the
hold count survives a fresh clone too; a malformed ledger is dropped the same way.

Version 3 (#1184) keeps one record per model. The top-level ``levels_version``, ``tags``,
``baseline`` and ``pruning`` are the default model's record, as before; ``models`` maps a
shipped profile's name to its own ``ModelRecord`` (``levels_version``, ``tags``, ``baseline``),
and the top-level record may be absent when only profiles have run. A run whose model holds
practice-pack rows (#1274) keeps its own record under ``models``, keyed by the base model and
each pack in model order (``default--company--team``, ``PACK_KEY_JOIN``, #1276): runs with and
without packs, or with other packs, never share a record, so "since last run" never reads a
pack coming or going as gates gained or lost. ``base_model_key`` is the base alone, which
``profiles.model_for_level`` reads. ``model_key(level)`` names
the record a run reads and writes (``default``, a profile name, or None for a custom
``--levels`` model, which keeps none), and ``record_for(prior, level)`` returns that record as
a ``PriorLevel`` view, so ``load_baseline(assess_dir, level)``, the gate and the tag strip each
read the run's own model. ``write_prior_level`` replaces only that record and keeps every other;
the pruning ledger is the default record's alone.

Every value is a tag token, a condition id, a reason code, a version token, a count, a
commit sha (``baseline.head_sha``, ``pruning[].completed_sha``), a run id token, a gate id or,
in ``pruning[].path``, the repository-relative path of an instruction file the ledger tracks
(hashed in counts-only output); no repository or person is named.
Tests: ``tests/test_prior_level.py``.
"""
from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NotRequired, TypedDict, cast

from lib.scoring.level_result_types import TagRow
from lib.scoring.levels_fields import TRUTH_TAGS

PRIOR_LEVEL_FILE = "prior-level.json"
# Joins a base model and its practice packs in a record key (#1276): a pack name is
# ``[a-z][a-z0-9_]*`` (lib/packs.py), so the key stays an identifier counts-only output keeps.
PACK_KEY_JOIN = "--"
# 1 (#940): the first version.
# 2 (#1122): adds the optional ``baseline``. Version-1 files are still read, without one.
# 3 (#1184): adds the optional ``models``, one record per shipped profile keyed by its name, and
#   lets the default model's top-level record be absent when only profiles have run.
PRIOR_LEVEL_VERSION = 3
READABLE_VERSIONS = (1, 2, 3)
_COUNTS = ("conditions", "countable", "set_aside", "met", "measured")


class Baseline(TypedDict):
    """The committed "since last run" baseline (#1122): what the next run compares with."""

    run_id: str | None
    head_sha: str  # the commit this run measured
    held_gates: list[str]  # the Step gate ids this run held, in Step order


class PruningEntry(TypedDict):
    """One removal the pruning ledger tracks (``lib/pruning_ledger.py``, #1084)."""

    path: str  # the instruction file the removal trimmed
    completed_sha: str  # the commit that closed the removal action
    cost_before: int  # the always-on lines when the removal was proposed
    runs_held: int  # runs on new commits with the file unflagged and every gate held, in a row
    credited: bool  # credited once runs_held reached HOLD_RUNS
    saved: int | None  # the measured drop in always-on lines, set when credited
    gates: list[str] | None  # the Step gates the removal must keep, set when first seen
    credited_at: str | None  # the commit the credit was given at
    merged: NotRequired[list[str]]  # earlier removals of the same file folded into this one (#1168)


class ModelRecord(TypedDict):
    """One shipped profile's record under ``models`` (#1184): its tags and committed baseline."""

    levels_version: str  # the profile version the tags were counted under
    tags: list[TagRow]
    baseline: NotRequired[Baseline]  # absent with no measured commit


class PriorLevel(TypedDict):
    """``prior-level.json`` and the run context's ``prior_level`` (#940). The top-level record
    is the default model's; ``models`` holds each shipped profile's (#1184)."""

    prior_level_version: int
    levels_version: NotRequired[str]  # the maturity-model version the tags were counted under
    tags: NotRequired[list[TagRow]]  # the level block's tags, in TRUTH_TAGS order
    baseline: NotRequired[Baseline]  # version 2 (#1122); absent with no measured commit
    pruning: NotRequired[list[PruningEntry]]  # the pruning ledger's removals (#1084), default only
    models: NotRequired[dict[str, ModelRecord]]  # version 3 (#1184): profile name -> its record


class PriorLevelUnread(TypedDict):
    """The run context's ``prior_level`` when ``prior-level.json`` exists but cannot be read."""

    available: bool  # always False
    reason: str  # always "unreadable"


UNREADABLE: PriorLevelUnread = {"available": False, "reason": "unreadable"}


class PriorLevelBlock(PriorLevel, total=False):
    """Every field the run context's ``prior_level`` can hold, for the counts-only walk and the
    check manifest's shape: a ``PriorLevel``, or ``PriorLevelUnread``'s two fields."""

    available: bool
    reason: str  # a fixed token, never text from a file


def build_prior_level(level: Any, baseline: Baseline | None = None,
                      pruning: list[PruningEntry] | None = None) -> PriorLevel | None:
    """The file's content for one ``level`` block and the run's ``baseline``; None when the
    block has no ``tags``."""
    if not isinstance(level, Mapping) or not _valid_tags(level.get("tags")):
        return None
    if not isinstance(level.get("levels_version"), str):
        return None
    out: PriorLevel = {"prior_level_version": PRIOR_LEVEL_VERSION, "levels_version": level["levels_version"],
                       "tags": cast(list[TagRow], level["tags"])}
    if baseline is not None and _valid_baseline(baseline):
        out["baseline"] = baseline
    if pruning and _valid_pruning(pruning):
        out["pruning"] = pruning
    return out


def _valid_pruning(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(e, Mapping) and isinstance(e.get("path"), str) and isinstance(e.get("completed_sha"), str)
        and type(e.get("cost_before")) is int and type(e.get("runs_held")) is int and e["runs_held"] >= 0
        and isinstance(e.get("credited"), bool) and (e.get("saved") is None or type(e.get("saved")) is int)
        and (e.get("gates") is None or (isinstance(e.get("gates"), list) and all(isinstance(g, str) for g in e["gates"])))
        and (e.get("credited_at") is None or isinstance(e.get("credited_at"), str))
        and (isinstance(e.get("merged", []), list) and all(isinstance(m, str) for m in e.get("merged", [])))
        for e in value)


def _valid_baseline(value: Any) -> bool:
    return (isinstance(value, Mapping) and isinstance(value.get("head_sha"), str) and bool(value["head_sha"])
            and (value.get("run_id") is None or isinstance(value.get("run_id"), str))
            and isinstance(value.get("held_gates"), list)
            and all(isinstance(g, str) for g in value["held_gates"]))


def _valid_row(row: Any) -> bool:
    if not isinstance(row, Mapping) or row.get("tag") not in TRUTH_TAGS:
        return False
    if not all(type(row.get(k)) is int and row[k] >= 0 for k in _COUNTS):
        return False
    # the counts nest: a share above 1 or more measured than countable is not a real row
    if not row["met"] <= row["measured"] <= row["countable"] <= row["conditions"]:
        return False
    if row["measured"] + row["set_aside"] > row["countable"]:
        return False
    return all(_valid_missing(row.get(k)) for k in ("not_measured", "countable_not_measured"))


def _valid_missing(missing: Any) -> bool:
    return isinstance(missing, list) and all(
        isinstance(m, Mapping) and isinstance(m.get("id"), str) and isinstance(m.get("reason"), str)
        for m in missing)


def _valid_tags(tags: Any) -> bool:
    return isinstance(tags, list) and all(_valid_row(row) for row in tags)


def _valid_record(value: Any) -> bool:
    return isinstance(value, Mapping) and isinstance(value.get("levels_version"), str) \
        and _valid_tags(value.get("tags"))


def _models(value: Any) -> dict[str, ModelRecord]:
    """The readable profile records of a ``models`` value; a malformed record is dropped alone,
    and a malformed baseline inside a record is dropped from it."""
    if not isinstance(value, Mapping):
        return {}
    out: dict[str, ModelRecord] = {}
    for name, record in value.items():
        if isinstance(name, str) and _valid_record(record):
            kept = dict(record)
            if "baseline" in kept and not _valid_baseline(kept["baseline"]):
                del kept["baseline"]
            out[name] = cast(ModelRecord, kept)
    return out


def as_prior_level(value: Any) -> PriorLevel | None:
    """``value`` when it is a ``PriorLevel`` this reader knows, else None. The default record may
    be absent only in a version-3 file that holds a profile record."""
    if not isinstance(value, Mapping) or value.get("prior_level_version") not in READABLE_VERSIONS:
        return None
    out = dict(value)
    raw_models = out.pop("models", None)  # only version 3 defines it: an older file's is dropped
    models = _models(raw_models) if value.get("prior_level_version") == 3 else {}
    has_default = "levels_version" in out or "tags" in out
    if has_default and not _valid_record(out):
        return None
    if not has_default and not models:
        return None
    if "baseline" in out and not _valid_baseline(out["baseline"]):
        del out["baseline"]  # a malformed baseline is dropped alone: the tags still draw
    if "pruning" in out and not _valid_pruning(out["pruning"]):
        del out["pruning"]  # so is a malformed ledger
    if models:
        out["models"] = models
    return cast(PriorLevel, out)


def base_model_key(level: Any) -> str | None:
    """The base model a ``level`` block was counted under: ``default`` for a default-model
    version (or a level that names none), a profile's name for ``profile-<name>-<date>``, None
    for a custom ``--levels`` model. Practice packs layered onto it are not part of it."""
    version = level.get("levels_version") if isinstance(level, Mapping) else None
    if not isinstance(version, str) or version.startswith("default-"):
        return "default"
    name = version[len("profile-"):].rpartition("-")[0] if version.startswith("profile-") else ""
    return name or None


def level_packs(level: Any) -> list[str]:
    """The practice packs whose rows a ``level`` block holds, in model order: every condition
    ``source`` after the first, which is the base model's (#1274); ``[]`` without one."""
    conditions = level.get("conditions") if isinstance(level, Mapping) else None
    sources = [c["source"] for c in conditions or [] if isinstance(c, Mapping) and isinstance(c.get("source"), str)]
    return list(dict.fromkeys(sources))[1:]


def model_key(level: Any) -> str | None:
    """The record a ``level`` block reads and writes: the ``base_model_key``, joined with each
    layered pack's name by ``PACK_KEY_JOIN`` when packs added rows (#1276), so a run with packs
    and a run without them never share a record; None for a custom ``--levels`` model, which
    keeps no committed record."""
    base = base_model_key(level)
    packs = level_packs(level)
    return base if base is None or not packs else PACK_KEY_JOIN.join([base, *packs])


def record_for(prior: Any, level: Any) -> PriorLevel | None:
    """The record of ``level``'s model in a read ``prior-level.json`` (``as_prior_level``), as a
    ``PriorLevel`` view: the top level for the default model, ``models[<name>]`` for a profile;
    None when that model has no record, for a custom model, or for an unreadable file."""
    found = as_prior_level(prior)
    key = model_key(level)
    if found is None or key is None:
        return None
    if key == "default":
        return found if "levels_version" in found else None
    record = found.get("models", {}).get(key)
    return None if record is None else cast(PriorLevel, {"prior_level_version": found["prior_level_version"], **record})


def load_pruning(assess_dir: Path) -> list[PruningEntry]:
    """The pruning ledger in the earlier run's ``prior-level.json``; none when absent (#1084)."""
    prior = load_prior_level(assess_dir)
    found = prior.get("pruning") if isinstance(prior, Mapping) else None
    return cast(list[PruningEntry], found) if isinstance(found, list) else []


def load_baseline(assess_dir: Path, level: Any = None) -> Baseline | None:
    """The committed baseline of ``level``'s model (the default model when None) in the earlier
    run's ``prior-level.json``, or None (#1122, #1184)."""
    prior = record_for(load_prior_level(assess_dir), level)
    return None if prior is None else prior.get("baseline")


def load_prior_level(assess_dir: Path) -> PriorLevel | PriorLevelUnread | None:
    """The earlier run's ``prior-level.json``: None when there is no such file, ``UNREADABLE``
    when it exists but cannot be read or is not a ``PriorLevel`` this reader knows."""
    path = assess_dir / PRIOR_LEVEL_FILE
    if not path.exists() and not path.is_symlink():
        return None
    try:
        prior = as_prior_level(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        prior = None
    return prior if prior is not None else {**UNREADABLE}


def default_level(level: Any) -> bool:
    """True when ``level`` was counted under the default model (a ``default-<date>`` version)
    or names no model (a degraded level, which ``build_prior_level`` already keeps out)."""
    return model_key(level) == "default"


def _content(assess_dir: Path, level: Any, baseline: Baseline | None,
             pruning: list[PruningEntry] | None) -> PriorLevel | None:
    """The file to write for this run: its own model's record replaced, every other kept."""
    key = model_key(level)
    built = build_prior_level(level, baseline, pruning if key == "default" else None)
    if key is None or built is None:
        return None
    raw = load_prior_level(assess_dir)
    existing = as_prior_level(raw)
    if key != "default" and raw is not None and existing is None:
        return None  # an unreadable file: a profile run never replaces the default record in it
    models: dict[str, ModelRecord] = dict(existing.get("models", {})) if existing is not None else {}
    if key == "default":
        out: PriorLevel = {**built}
    else:
        record: ModelRecord = {"levels_version": built["levels_version"], "tags": built["tags"]}
        if "baseline" in built:
            record["baseline"] = built["baseline"]
        models[key] = record
        kept = {k: v for k, v in (existing or {}).items() if k != "models"}
        out = cast(PriorLevel, {**kept, "prior_level_version": PRIOR_LEVEL_VERSION})
    if models:
        out["models"] = models
    return out


def write_prior_level(assess_dir: Path, level: Any, baseline: Baseline | None = None,
                      pruning: list[PruningEntry] | None = None) -> Path | None:
    """Write this run's ``prior-level.json`` (sorted keys, LF).

    A run whose level block has no tags (a degraded level) leaves the existing file untouched
    (#1158): it holds the previous good run's baseline and pruning ledger, which the next good
    run compares with. A run writes only its own model's record (#1184): a default run the top
    level, keeping every profile record; a profile run ``models[<name>]``, keeping the default
    record and the pruning ledger, which only a default run writes, and leaving a file it cannot
    read as it is. A custom ``--levels`` run writes nothing (#1150). A symlink in the file's place is removed, never written through. A file
    that cannot be written (``OSError``) is named on stderr and skipped, as
    ``assess_diagrams.write_diagrams`` does: the record never stops a run. Returns the path
    written, or None."""
    path = assess_dir / PRIOR_LEVEL_FILE
    if model_key(level) is None:
        return None
    content = _content(assess_dir, level, baseline, pruning)
    try:
        if path.is_symlink():
            path.unlink(missing_ok=True)
        if content is None:
            return None
        path.write_text(json.dumps(content, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")
    except OSError as e:
        print(f"/assess: {PRIOR_LEVEL_FILE} not written ({type(e).__name__}: {e})", file=sys.stderr)
        return None
    return path


def same_model(prior: Mapping[str, Any] | None, level: Mapping[str, Any]) -> bool:
    """True when the earlier run was counted under this run's maturity-model version."""
    return prior is not None and prior.get("levels_version") == level.get("levels_version")
