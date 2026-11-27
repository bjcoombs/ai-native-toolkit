"""Owner exclusions of maturity-model conditions (``.assess/config.toml`` ``[checks]``, #910).

An owner may state that a condition does not apply to this repository, with a
reason. The entry is committed, so the level stays a function of the commit,
the maturity-model version and the committed config::

    [checks]
    exclude = [
      { id = "layer-6-coverage.1", kind = "measured_elsewhere",
        reason = "Coverage is enforced in the release pipeline repository.",
        excluded_by = "ben", excluded_at = "2026-11-22", plugin_version = "2.2.0" },
    ]

- ``id`` is a condition id of the default maturity model (``<row id>.<level>``,
  ``lib.scoring.levels.condition_id``). Required.
- ``reason`` is required and non-empty. An entry without one is skipped, so the
  condition stays in force.
- ``kind`` is one of ``KINDS``; it defaults to ``other`` only when the key is left
  out. A kind that is written but unknown, empty or not a string is skipped
  rather than guessed.
- ``excluded_by``, ``excluded_at`` and ``plugin_version`` are optional
  provenance, named as a decline marker's (``lib/run/decline_markers.py``).

Every skipped entry is listed with a ``PROBLEMS`` code and a one-line warning on
stderr, and the report lists it too; nothing here raises. A repeated id keeps its
first entry. A ``[checks]`` that is not a table, or an ``exclude`` that is not a
list (a single inline table, say), is one skipped entry with index 0 and code
``not_a_list``, never a silent no-op.

``lib.scoring.level_tally`` reads the exclusions in force: an excluded condition reads
``not_applicable`` with ``disposition: excluded_by_owner``, leaves the
denominator, never earns, and never holds a Step gate, so the Step it gates
stays unreachable while it is excluded (#744 decision). The headless runner
reports this block too (``lib.run.headless_static``), so ``/fleet`` applies a
repository's own exclusions (#944). Fleet-wide exclusions (``--fleet-config``)
wait on the method stamp (#952), so this reads the repository's own file only.

Reads the owner's condition exclusions from `.assess/config.toml` (#910):
`[checks] exclude = [{id, reason, kind?, excluded_by?, excluded_at?, plugin_version?}]`.
`id` is a default-model condition id (`<row id>.<level>`) and `reason` is required;
`kind` is one of `KINDS` (`not_applicable`, `measured_elsewhere`, `tool_unsupported`,
`false_positive`, `other`, the default when the key is left out; a written kind that is not
one of them is skipped). The provenance keys are named as a decline marker's.
`parse_exclusions(config, known)` returns the run-context `owner_exclusions` block
(`OwnerExclusionsBlock`): the `exclusions` in force (with `source: "repo"`), the `skipped`
entries with a `PROBLEMS` code (`not_a_list` for a `[checks]` or `exclude` of the wrong type, `not_a_table`, `no_id`, `no_reason`, `unknown_kind`,
`unknown_condition`, `duplicate`; the first entry per id wins) and one report `disclosures`
line per exclusion. A skipped entry leaves its condition in force. `owner_exclusions_block`
reads the repository's config and warns on stderr per skipped entry; never raises. A
`scan_registry` entry (`core_scans.owner_exclusions`); the headless runner reports it too
(`headless_static.owner_exclusions`, #944), so `/fleet` applies a repository's own exclusions
in `level.json` and `fleet-data.json`. The block also carries `declines` (#828), present only
when `.assess/agents-declined.json` exists (`decline_markers.read_agent_decline`): the
declared agent opt-out that `level_tally` reads as `declined`, and also when a Copilot
instruction file declines Copilot review for the whole repository
(`ai_governance.read_copilot_opt_out`, #851), whose record declines only `agent_runtime`
conditions. Counts-only keeps
ids, kinds and dates and drops the reason and the owner. Tests: `tests/test_owner_exclusions.py`.
"""
from __future__ import annotations

import datetime
import sys
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any

from lib.assess_config import ASSESS_DIR, load_config
from lib.scoring.level_result_types import OwnerExclusion, OwnerExclusionsBlock, SkippedExclusion
from lib.ai_governance import read_copilot_opt_out
from lib.run.decline_markers import read_agent_decline
from lib.scoring.level_tally import condition_ids, default_model

SECTION = "checks"
KEY = "exclude"
SOURCE_REPO = "repo"
DISPOSITION = "excluded_by_owner"
KINDS = ("not_applicable", "measured_elsewhere", "tool_unsupported", "false_positive", "other")
DEFAULT_KIND = "other"
# Why an entry was skipped, as SkippedExclusion.problem holds it.
PROBLEMS = ("not_a_list", "not_a_table", "no_id", "no_reason", "unknown_kind", "unknown_condition", "duplicate")


def _text(entry: Mapping[str, Any], key: str) -> str | None:
    value = entry.get(key)
    if isinstance(value, (datetime.date, datetime.datetime, datetime.time)):
        return value.isoformat()  # an unquoted TOML date, such as excluded_at = 2026-11-22
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _problem(entry: Any, known: Collection[str], seen: Collection[str]) -> str | None:
    if not isinstance(entry, Mapping):
        return "not_a_table"
    cid = _text(entry, "id")
    if cid is None:
        return "no_id"
    if _text(entry, "reason") is None:
        return "no_reason"
    if "kind" in entry and entry["kind"] not in KINDS:
        return "unknown_kind"
    if cid not in known:
        return "unknown_condition"
    if cid in seen:
        return "duplicate"
    return None


def parse_exclusions(config: Mapping[str, Any], known: Collection[str]) -> OwnerExclusionsBlock:
    """The ``[checks] exclude`` entries of a parsed config, checked against the condition ids ``known``."""
    section = config.get(SECTION)
    entries = section.get(KEY) if isinstance(section, Mapping) else None
    exclusions: list[OwnerExclusion] = []
    skipped: list[SkippedExclusion] = []
    written_badly = SECTION in config and (not isinstance(section, Mapping)
                                           or (KEY in section and not isinstance(entries, list)))
    if written_badly:
        named = _text(entries, "id") if isinstance(entries, Mapping) else None
        skipped.append({"index": 0, "id": named, "problem": "not_a_list"})
    if not isinstance(entries, list):
        entries = []
    for index, entry in enumerate(entries):
        problem = _problem(entry, known, [e["id"] for e in exclusions])
        if problem is not None:
            written = _text(entry, "id") if isinstance(entry, Mapping) else None
            skipped.append({"index": index, "id": written, "problem": problem})
            continue
        exclusions.append({
            "id": str(_text(entry, "id")), "kind": _text(entry, "kind") or DEFAULT_KIND,
            "reason": str(_text(entry, "reason")), "source": SOURCE_REPO,
            "excluded_by": _text(entry, "excluded_by"), "excluded_at": _text(entry, "excluded_at"),
            "version": _text(entry, "plugin_version"),
        })
    return {"exclusions": exclusions, "skipped": skipped,
            "disclosures": [disclosure(e) for e in exclusions]}


def disclosure(exclusion: OwnerExclusion) -> str:
    """The report line for one exclusion in force."""
    line = f"`{exclusion['id']}` excluded by owner: {exclusion['reason']} ({exclusion['kind'].replace('_', ' ')}"
    if exclusion["excluded_by"]:
        line += f", by {exclusion['excluded_by']}"
    if exclusion["excluded_at"]:
        line += f" on {exclusion['excluded_at']}"
    if exclusion["version"]:
        line += f", plugin v{exclusion['version']}"
    return line + ")"


def _warn(skip: SkippedExclusion) -> None:
    where = f"[{SECTION}] {KEY}[{skip['index']}]"
    named = f" ({skip['id']})" if skip["id"] else ""
    print(f"warning: .assess/config.toml {where}{named} skipped: {skip['problem'].replace('_', ' ')}; "
          "the condition stays in force", file=sys.stderr)


def owner_exclusions_block(repo_root: Path) -> OwnerExclusionsBlock:
    """The run context's ``owner_exclusions`` block for one repository, warning on each skipped entry."""
    block = parse_exclusions(load_config(repo_root), condition_ids(default_model()))
    for skip in block["skipped"]:
        _warn(skip)
    # #828, #851: present only when declared, so a block without one is unchanged
    records = [r for r in (read_agent_decline(repo_root / ASSESS_DIR), read_copilot_opt_out(repo_root)) if r is not None]
    if records:
        block["declines"] = records
    return block
