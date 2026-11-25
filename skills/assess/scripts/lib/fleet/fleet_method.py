"""The method stamp of a fleet run: the rules it scored with and its collector revisions (issue #952).

Two runs under one maturity-model version can still score with different rules: a
``--levels FILE`` edited without a version change, or the same version string in another
file (#771). ``rules_sha256`` names the rules themselves, and ``collectors`` names the
revision of every core collector (``skills/assess/checks/core.toml``), so a comparison can
tell a rule change and a scanner change from a change in the repositories.

**Rule identity.** ``rules_sha256`` is the sha256 of the canonical JSON (sorted keys, no
spaces) of the maturity model as ``fleet.py`` loaded it, the resolved model every result
is scored with, so anything that changes the loaded model changes the hash, a named
profile included. Keys that only name things are left out (``UNHASHED_KEYS``): ``version``
(stamped on its own as ``levels_version``), a row's ``title`` and a Step's ``name``.
``demoted`` is left out too, though it changes scoring: the exit rule (#1086) decides it per
run from the run history after the stamp is written, and ``demotions.json`` records it. Every
other key is hashed, a new one too. The hash is conservative: reordering rows or gates, or
renaming a row or gate id, moves it on purpose; the cost is a re-score of the earlier run,
nothing worse. None without ``--levels``.

**Collector identity.** ``collectors`` is ``[{id, revision}]`` for every collector in the
core check manifest, sorted by id. A ``revision`` is bumped when a collector's output
changes; ``tests/test_collector_fingerprints.py`` fails when the fleet golden's output of a
collector changes and its revision does not.

**Pack identity** (#1273). A practice pack (``--pack``, ``lib/packs.py``) is named by its
``{name, version, sha256}`` (``packs.PackIdentity``), never as a core collector revision:
``collector_revisions`` leaves the pack collector (``PACK_COLLECTOR``) out of ``collectors``, so
a run without ``--pack`` writes the ``collectors`` list it wrote before packs existed. The
stamp's ``packs`` key holds the identities in pass order and is written only when a pack was
passed; a stamp without it (no pack, or written before #1273) reads as no pack.

**Comparability** (``lib/fleet/fleet_compare.py`` ``comparability``):

- ``rule_reasons``: two recorded hashes that differ give the reason ``maturity-model rules
  changed``. Like a ``levels_version`` change it is a rule reason, so the achievements replay
  re-scores the earlier run from its stored scans (``RULE_KEYS``,
  ``lib/fleet/fleet_achievements_runs.py``) and ``--compare`` refuses the pair. Two stamps
  whose packs differ (by name, in order, and sha256; ``pack_reasons``) give the rule reason
  ``practice packs changed``. ``packs`` is not in ``RULE_KEYS``: stored scans carry no pack
  block to re-score from, so that reason is never bridged by a re-score.
- ``method_notes``: a stamp written before #952 has neither key (``fleet_legacy.STAMP_DEFAULTS``:
  None, unrecorded). Against a stamp that has them it compares on the stamped version alone,
  with a note; it is never read as the same rules, and never refused for it. A collector
  revision change is a note naming each collector, never a refusal: the #774 scanner diff
  attributes the moves.

**Elsewhere.** A same-day rerun under other rules is kept beside the earlier entry, not over
it (``fleet_history._MATCH_KEYS``, which holds ``packs`` too, #1276; ``fleet.py`` plans the hash
and the packs before any scan), while a bare
``--compare`` still takes an entry that recorded no hash as its baseline. The trend chart breaks
its lines at a rules change both runs recorded (``fleet_trends``).

Tests: ``tests/test_fleet_method.py``.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, TypedDict

from lib.scoring.levels import MaturityModel
from lib.manifest.check_manifest import CheckManifest, collectors, core_manifest

# The stamp keys that name the rules a run scored with: a difference in any is re-scorable.
RULE_KEYS = ("levels_version", "rules_sha256")
# The pack collector (lib/pack_checks.py): named by each pack's sha256 in the stamp's
# ``packs``, never by a collector revision, so it is left out of ``collectors``.
PACK_COLLECTOR = "core.pack_checks"
PACKS_PREFIX = "practice packs changed "
# Model keys left out of rules_sha256, by level of the model: names, plus ``demoted``, which the
# exit rule decides per run after the stamp and demotions.json records.
UNHASHED_KEYS: Mapping[str, frozenset[str]] = {
    "model": frozenset({"version", "demoted"}), "row": frozenset({"title"}), "step": frozenset({"name"}),
}
RULES_NOTE = ("one run did not record its maturity-model rules, so they are compared "
              "by maturity-model version only")
COLLECTORS_NOTE = "one run did not record its collector revisions"
CHANGED_PREFIX = "collector revisions changed: "
# Every method note starts with one of these, so the stderr summary can print them.
METHOD_PREFIXES = (RULES_NOTE, COLLECTORS_NOTE, CHANGED_PREFIX)
_SHORT = 12


class CollectorRevision(TypedDict):
    id: str
    revision: int


class MethodKeys(TypedDict):
    rules_sha256: str | None
    collectors: list[CollectorRevision]


def _rules(model: MaturityModel) -> dict[str, Any]:
    """The model without the keys that only name things."""
    out: dict[str, Any] = {k: v for k, v in model.items() if k not in UNHASHED_KEYS["model"]}
    out["rows"] = [{k: v for k, v in row.items() if k not in UNHASHED_KEYS["row"]} for row in model["rows"]]
    if "steps" in model:
        out["steps"] = [{k: v for k, v in step.items() if k not in UNHASHED_KEYS["step"]}
                        for step in model["steps"]]
    return out


def rules_sha256(model: MaturityModel | None) -> str | None:
    """The sha256 of the rules in ``model``; None without one."""
    if model is None:
        return None
    text = json.dumps(_rules(model), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def collector_revisions(manifest: CheckManifest | None = None) -> list[CollectorRevision]:
    """Every collector's ``{id, revision}`` in ``manifest`` (the core manifest by default), by id."""
    entries = collectors(core_manifest() if manifest is None else manifest)
    return sorted(({"id": c["id"], "revision": c["revision"]} for c in entries if c["id"] != PACK_COLLECTOR),
                  key=lambda c: c["id"])


def method_keys(model: MaturityModel | None) -> MethodKeys:
    """The stamp's method keys for a run scored with ``model``."""
    return {"rules_sha256": rules_sha256(model), "collectors": collector_revisions()}


def _short(value: object) -> str:
    return value[:_SHORT] if isinstance(value, str) else repr(value)


def rule_reasons(earlier: Mapping[str, Any], later: Mapping[str, Any]) -> list[str]:
    """The rules-hash reason (both stamps recorded one, and they differ), then the
    ``pack_reasons``."""
    then, now = earlier.get("rules_sha256"), later.get("rules_sha256")
    reasons = [] if then is None or now is None or then == now else \
        [f"maturity-model rules changed {_short(then)}->{_short(now)}"]
    return reasons + pack_reasons(earlier, later)


def _packs(stamp: Mapping[str, Any]) -> list[tuple[str, str]]:
    """``(name, sha256)`` of each pack a stamp names, in pass order; none without ``packs``."""
    found = stamp.get("packs")
    if not isinstance(found, list):
        return []
    return [(str(p.get("name")), str(p.get("sha256"))) for p in found if isinstance(p, dict)]


def _pack_words(packs: list[tuple[str, str]]) -> str:
    return ",".join(f"{name}@{_short(sha)}" for name, sha in packs) or "none"


def pack_reasons(earlier: Mapping[str, Any], later: Mapping[str, Any]) -> list[str]:
    """The packs reason (#1273): the two stamps name different packs, in order, by name and
    sha256. A stamp without ``packs`` names none."""
    then, now = _packs(earlier), _packs(later)
    return [] if then == now else [f"{PACKS_PREFIX}{_pack_words(then)}->{_pack_words(now)}"]


def _unrecorded_rules(stamp: Mapping[str, Any]) -> bool:
    """Scored with a maturity model whose rules the stamp does not name (a stamp before #952)."""
    return stamp.get("levels_version") is not None and stamp.get("rules_sha256") is None


def _revisions(stamp: Mapping[str, Any]) -> dict[str, Any] | None:
    found = stamp.get("collectors")
    if not isinstance(found, list):
        return None
    return {c["id"]: c.get("revision") for c in found if isinstance(c, dict) and isinstance(c.get("id"), str)}


def method_notes(earlier: Mapping[str, Any], later: Mapping[str, Any]) -> list[str]:
    """Caveats on the method: rules or collector revisions one run did not record, and
    collectors whose revision changed."""
    notes: list[str] = []
    if earlier.get("levels_version") == later.get("levels_version") \
            and _unrecorded_rules(earlier) != _unrecorded_rules(later):
        notes.append(RULES_NOTE)
    then, now = _revisions(earlier), _revisions(later)
    if (then is None) != (now is None):
        notes.append(COLLECTORS_NOTE)
    elif then is not None and now is not None:
        changed = [f"{cid} {then.get(cid)!r}->{now.get(cid)!r}" for cid in sorted(then.keys() | now.keys())
                   if then.get(cid) != now.get(cid)]
        if changed:
            notes.append(CHANGED_PREFIX + ", ".join(changed))
    return notes
