"""``prior-level.json``: this run's per-tag values, kept for the next run's tag profile strip (#940).

Each run writes ``prior-level.json`` into its artifact directory (``.assess/`` or
``.assess/<scope-slug>/``) with the maturity-model version (``levels_version``) and the
``level`` block's ``tags`` rows (``lib/level_tags.py``). ``run_publish.publish_run`` reads the
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
ledger survive one failed run; the strip still compares only runs counted under the same model
(``same_model``).
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

from lib.level_result_types import TagRow
from lib.levels_fields import TRUTH_TAGS

PRIOR_LEVEL_FILE = "prior-level.json"
# 1 (#940): the first version.
# 2 (#1122): adds the optional ``baseline``. Version-1 files are still read, without one.
PRIOR_LEVEL_VERSION = 2
READABLE_VERSIONS = (1, 2)
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


class PriorLevel(TypedDict):
    """``prior-level.json`` and the run context's ``prior_level`` (#940)."""

    prior_level_version: int
    levels_version: str  # the maturity-model version the tags were counted under
    tags: list[TagRow]  # the level block's tags, in TRUTH_TAGS order
    baseline: NotRequired[Baseline]  # version 2 (#1122); absent with no measured commit
    pruning: NotRequired[list[PruningEntry]]  # the pruning ledger's removals (#1084)


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


def as_prior_level(value: Any) -> PriorLevel | None:
    """``value`` when it is a ``PriorLevel`` this reader knows, else None."""
    if not isinstance(value, Mapping) or value.get("prior_level_version") not in READABLE_VERSIONS:
        return None
    if not isinstance(value.get("levels_version"), str) or not _valid_tags(value.get("tags")):
        return None
    out = dict(value)
    if "baseline" in out and not _valid_baseline(out["baseline"]):
        del out["baseline"]  # a malformed baseline is dropped alone: the tags still draw
    if "pruning" in out and not _valid_pruning(out["pruning"]):
        del out["pruning"]  # so is a malformed ledger
    return cast(PriorLevel, out)


def load_pruning(assess_dir: Path) -> list[PruningEntry]:
    """The pruning ledger in the earlier run's ``prior-level.json``; none when absent (#1084)."""
    prior = load_prior_level(assess_dir)
    found = prior.get("pruning") if isinstance(prior, Mapping) else None
    return cast(list[PruningEntry], found) if isinstance(found, list) else []


def load_baseline(assess_dir: Path) -> Baseline | None:
    """The committed baseline in the earlier run's ``prior-level.json``, or None (#1122)."""
    prior = load_prior_level(assess_dir)
    found = prior.get("baseline") if isinstance(prior, Mapping) else None
    return cast(Baseline, found) if isinstance(found, Mapping) else None


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


def write_prior_level(assess_dir: Path, level: Any, baseline: Baseline | None = None,
                      pruning: list[PruningEntry] | None = None) -> Path | None:
    """Write this run's ``prior-level.json`` (sorted keys, LF).

    A run whose level block has no tags (a degraded level) leaves the existing file untouched
    (#1158): it holds the previous good run's baseline and pruning ledger, which the next good
    run compares with. A symlink in the file's place is removed, never written through. A file
    that cannot be written (``OSError``) is named on stderr and skipped, as
    ``assess_diagrams.write_diagrams`` does: the record never stops a run. Returns the path
    written, or None."""
    path = assess_dir / PRIOR_LEVEL_FILE
    content = build_prior_level(level, baseline, pruning)
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
