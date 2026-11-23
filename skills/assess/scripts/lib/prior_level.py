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

``load_prior_level`` returns None for no file, a file that is not JSON, and any shape other
than ``PriorLevel`` (a version this reader does not know, a tag outside ``TRUTH_TAGS``, a
count that is not a non-negative integer): an unreadable earlier run is never drawn.
``write_prior_level`` writes nothing, and removes a stale file, when the level result has no
``tags`` (a degraded level block), so an old run is never compared under a new label.
The strip draws the earlier run only when its ``levels_version`` equals this run's
(``same_model``); otherwise it says why in one line.

Every value is a tag token, a condition id, a reason code, a version token or a count, so
the file and the run context's ``prior_level`` name no path, repository or person.
Tests: ``tests/test_prior_level.py``.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TypedDict, cast

from lib.level_result_types import TagRow
from lib.levels_fields import TRUTH_TAGS

PRIOR_LEVEL_FILE = "prior-level.json"
# 1 (#940): the first version.
PRIOR_LEVEL_VERSION = 1
_COUNTS = ("conditions", "countable", "set_aside", "met", "measured")


class PriorLevel(TypedDict):
    """``prior-level.json`` and the run context's ``prior_level`` (#940)."""

    prior_level_version: int
    levels_version: str  # the maturity-model version the tags were counted under
    tags: list[TagRow]  # the level block's tags, in TRUTH_TAGS order


def build_prior_level(level: Any) -> PriorLevel | None:
    """The file's content for one ``level`` block; None when the block has no ``tags``."""
    if not isinstance(level, Mapping) or not _valid_tags(level.get("tags")):
        return None
    if not isinstance(level.get("levels_version"), str):
        return None
    return {"prior_level_version": PRIOR_LEVEL_VERSION, "levels_version": level["levels_version"],
            "tags": cast(list[TagRow], level["tags"])}


def _valid_row(row: Any) -> bool:
    if not isinstance(row, Mapping) or row.get("tag") not in TRUTH_TAGS:
        return False
    if not all(type(row.get(k)) is int and row[k] >= 0 for k in _COUNTS):
        return False
    missing = row.get("not_measured")
    return isinstance(missing, list) and all(
        isinstance(m, Mapping) and isinstance(m.get("id"), str) and isinstance(m.get("reason"), str)
        for m in missing)


def _valid_tags(tags: Any) -> bool:
    return isinstance(tags, list) and all(_valid_row(row) for row in tags)


def as_prior_level(value: Any) -> PriorLevel | None:
    """``value`` when it is a ``PriorLevel`` this reader knows, else None."""
    if not isinstance(value, Mapping) or value.get("prior_level_version") != PRIOR_LEVEL_VERSION:
        return None
    if not isinstance(value.get("levels_version"), str) or not _valid_tags(value.get("tags")):
        return None
    return cast(PriorLevel, dict(value))


def load_prior_level(assess_dir: Path) -> PriorLevel | None:
    """The earlier run's ``prior-level.json``; None when absent or unreadable."""
    try:
        return as_prior_level(json.loads((assess_dir / PRIOR_LEVEL_FILE).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def write_prior_level(assess_dir: Path, level: Any) -> Path | None:
    """Write this run's ``prior-level.json`` (sorted keys, LF); remove a stale one when the
    level block has no tags. Returns the path written, or None."""
    path = assess_dir / PRIOR_LEVEL_FILE
    content = build_prior_level(level)
    if content is None:
        path.unlink(missing_ok=True)
        return None
    path.write_text(json.dumps(content, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


def same_model(prior: Mapping[str, Any] | None, level: Mapping[str, Any]) -> bool:
    """True when the earlier run was counted under this run's maturity-model version."""
    return prior is not None and prior.get("levels_version") == level.get("levels_version")
