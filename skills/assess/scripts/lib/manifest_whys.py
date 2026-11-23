"""The ``why`` of every check manifest entry: the rules ``check_manifest`` applies at load (#939).

A why says, in one or two sentences, what a check protects for an agent working in the
repository. ``lib.check_manifest`` calls :func:`why_errors` on every parsed manifest; the
catalog, the level result, the headline and the gap actions read whys through the accessors
here (:func:`why_of`, :func:`scan_why`, :func:`tag_why`, :func:`condition_whys`, :func:`scan_whys`), which
return ``""`` where a version 1 manifest declares none.

Where each why lives:

- a condition and a finding: ``why``, beside ``title`` and ``fix`` or ``action``;
- a scan: the ``whys`` table of the collector that publishes it, keyed by the scan name,
  the same keys as the collector's ``headless`` table (a spread collector such as
  ``run_header`` reports several scans, each with its own why);
- a truth tag (``lib.levels_fields.TRUTH_TAGS``): ``[tags.<id>] why`` in the root manifest
  (``checks/core.toml``), not in a fragment;
- a signal: none. A signal entry that carries ``why`` is rejected; the catalog shows the why
  of each condition the signal feeds.

An optional source sits beside a real why: ``why_source`` on a condition, finding or tag,
and ``why_sources`` keyed by scan on a collector. It is an ``https://`` URL or a
repository-relative path.

The version rule: ``manifest_version = 1`` loads with no whys, and a version 1 manifest
carrying any why, source or ``tags`` table is rejected. ``manifest_version = 2`` requires
every why above, whatever the manifest's ``owner``. A why is 1 to 300 characters and passes
``lib.vocabulary``: no banned word and no layer number in its prose (``Layer 5``).

The plumbing token: a scan's why may be exactly ``exempt: plumbing`` (:data:`PLUMBING`)
only when no row or finding reads the scan, directly or through another scan. A condition
reads the block its signal path starts with; a finding reads the block or collector its
inputs name; a collector that a row or finding reads, directly or through another
collector, reads everything in its ``reads``. A collector's read of its own block does not
count (``run_header`` publishes ``diff`` and reads the private ``diff`` collector). This is
the catalog's "Used by" relation (``lib.catalog._used_by``) closed over ``reads``. The
catalog renders the token as "Run plumbing". Stdlib only; imports no other check module.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, NotRequired, TypedDict

from lib.check_validation import ValidationError, finding
from lib.levels_fields import TRUTH_TAGS
from lib.vocabulary import banned_hits, prose

PLUMBING = "exempt: plumbing"
WHY_MAX = 300
WHY_VERSION = 2  # the first manifest version that carries whys
_LAYER_RE = re.compile(r"layer[ -]?[0-9]", re.IGNORECASE)
_SOURCE_RE = re.compile(r"^(https://\S+|(?![/~])(?!.*\.\.)[A-Za-z0-9_.][A-Za-z0-9_./-]*)$")


class TagWhy(TypedDict):
    why: str
    why_source: NotRequired[str]


def why_of(entry: Mapping[str, Any]) -> str:
    """A condition's or finding's why, ``""`` when it has none (a version 1 manifest)."""
    why = entry.get("why", "")
    return why if isinstance(why, str) else ""


def why_source_of(entry: Mapping[str, Any]) -> str:
    source = entry.get("why_source", "")
    return source if isinstance(source, str) else ""


def scan_why(collector: Mapping[str, Any], scan: str) -> str:
    """The why of ``scan`` from the ``whys`` table of the collector that publishes it."""
    why = collector.get("whys", {}).get(scan, "")
    return why if isinstance(why, str) else ""


def scan_why_source(collector: Mapping[str, Any], scan: str) -> str:
    source = collector.get("why_sources", {}).get(scan, "")
    return source if isinstance(source, str) else ""


def tag_why(manifest: Mapping[str, Any], tag: str) -> TagWhy:
    """A truth tag's why and source from the root ``tags`` table (``why`` ``""`` when absent)."""
    raw = manifest.get("tags", {}).get(tag, {})
    out = TagWhy(why=raw.get("why", "") if isinstance(raw, dict) else "")
    if isinstance(raw, dict) and isinstance(raw.get("why_source"), str):
        out["why_source"] = raw["why_source"]
    return out


def scan_whys(manifest: Mapping[str, Any]) -> dict[str, str]:
    """Scan name -> its why (possibly :data:`PLUMBING`), for every collector's ``whys``."""
    return {scan: why for e in manifest.get("entries", []) if e.get("kind") == "collector"
            for scan, why in e.get("whys", {}).items() if isinstance(why, str)}


def condition_whys(manifest: Mapping[str, Any]) -> dict[str, str]:
    """Condition key (``<row>.<level>:<signal>``) -> its why, for every condition entry."""
    return {e["key"]: why_of(e) for e in manifest.get("entries", [])
            if e.get("kind") == "condition" and isinstance(e.get("key"), str)}


def _text_errors(where: str, why: Any, out: list[ValidationError], *, token_ok: bool) -> None:
    """Required, a string of 1 to 300 characters, and clean against ``lib.vocabulary``."""
    if why is None:
        out.append(finding(where, "WHY_REQUIRED", "required: a version 2 manifest gives this "
                           f"entry a why of 1 to {WHY_MAX} characters"))
        return
    if not isinstance(why, str) or not why.strip() or len(why) > WHY_MAX:
        out.append(finding(where, "WHY_LENGTH", f"a why is a string of 1 to {WHY_MAX} characters"))
        return
    if why == PLUMBING:
        if not token_ok:
            out.append(finding(where, "PLUMBING_NOT_SCAN", f"{PLUMBING!r} is allowed only on a "
                               "scan's why, never on a condition, finding or tag"))
        return
    hits = sorted({word for word, _, _ in banned_hits(why)})
    if hits:
        out.append(finding(where, "WHY_VOCABULARY", f"the why uses {', '.join(map(repr, hits))}, "
                           "which user-facing prose leaves out (lib/vocabulary.py)"))
    if (layer := _LAYER_RE.search(prose(why))):
        out.append(finding(where, "WHY_LAYER", f"the why names a layer number ({layer.group(0)!r}); "
                           "name the check instead"))


def _source_errors(where: str, source: Any, why: Any, out: list[ValidationError]) -> None:
    if source is None:
        return
    if not isinstance(why, str) or not why or why == PLUMBING:
        out.append(finding(where, "SOURCE_WITHOUT_WHY", "a why_source sits only beside a real why"))
    elif not isinstance(source, str) or not _SOURCE_RE.match(source):
        out.append(finding(where, "WHY_SOURCE", "a why_source is an https:// URL or a "
                           "repository-relative path"))


class Reach(TypedDict):
    """What rows and findings read (:func:`reach`)."""

    direct: set[str]  # the names a condition's signal path or a finding's input starts with
    read: dict[tuple[str, str], list[str]]  # (collector id, scan) -> scan keys of its used readers


def _key(collector_id: str) -> str:
    return collector_id.split(".", 1)[-1]


def reach(entries: Sequence[Mapping[str, Any]]) -> Reach:
    """Which scans rows and findings read, directly or through the collectors' ``reads``.

    Each read resolves, as ``check_manifest``'s reads graph does, to the collector that
    produced the name before the reader in manifest order: its scan key, or a block of a
    spread collector. A collector is used when a row or finding names one of its blocks or
    its key, or a used collector reads it; a used collector's reads are all used, since it
    computes its blocks together. A collector's reads include those of its
    ``headless_variants``, since the headless runner builds the block from them. A scan is read
    when a used collector other than its own reads it by name (or reads its collector's key)."""
    cols = [e for e in entries if e.get("kind") == "collector"]
    produced: dict[str, str] = {}
    edges: list[tuple[str, str, str]] = []  # (reader id, producer id, name read)
    for c in cols:
        cid = str(c["id"])
        reads = [*c.get("reads", []), *(r for v in c.get("headless_variants", []) for r in v.get("reads", []))]
        edges += [(cid, produced[r], r) for r in dict.fromkeys(reads) if r in produced]
        produced[_key(cid)] = cid
        if c.get("spread"):
            produced.update({block: cid for block in c.get("publishes", [])})
    owner = {block: str(c["id"]) for c in cols for block in c.get("publishes", [])}
    direct = {str(e["key"]).split(":", 1)[1].split(".")[0] for e in entries
              if e.get("kind") == "condition" and ":" in str(e.get("key", ""))}
    direct |= {str(i).split(".")[0] for e in entries if e.get("kind") == "finding"
               for i in e.get("inputs", [])}
    used = {owner.get(n) or produced.get(n) for n in direct} - {None}
    while True:
        new = {p for r, p, _ in edges if r in used} - used
        if not new:
            break
        used |= new
    read: dict[tuple[str, str], list[str]] = {}
    for r, p, name in edges:
        if r in used and r != p:
            blocks = [name] if name in owner and owner[name] == p else \
                [b for b, o in owner.items() if o == p]
            for b in blocks:
                read.setdefault((p, b), []).append(_key(r))
    return Reach(direct={str(n) for n in direct}, read=read)


def _plumbing_errors(where: str, collector_id: str, scan: str, seen: Reach,
                     out: list[ValidationError]) -> None:
    readers = sorted(seen["read"].get((collector_id, scan), []))
    if scan in seen["direct"]:
        out.append(finding(where, "PLUMBING_READ", f"{PLUMBING!r} is for a scan no row or finding "
                           f"reads, and a condition or finding reads {scan!r} directly"))
    elif readers:
        out.append(finding(where, "PLUMBING_READ_INDIRECT", f"{PLUMBING!r} is for a scan no row or "
                           f"finding reads, and {scan!r} is read indirectly: {', '.join(readers)} "
                           "reads it, and a row or finding uses what that scan builds"))


def _v1_errors(data: Mapping[str, Any], where_of: Mapping[str, str],
               out: list[ValidationError]) -> None:
    if "tags" in data:
        out.append(finding("manifest.tags", "WHY_IN_V1", "a version 1 manifest carries no why; "
                           f"set manifest_version = {WHY_VERSION} to declare tag whys"))
    for e in data["entries"]:
        for key in ("why", "why_source", "whys", "why_sources"):
            if key in e and e.get("kind") != "signal":
                out.append(finding(f"{where_of[e['id']]}.{key}", "WHY_IN_V1", "a version 1 "
                                   f"manifest carries no why; set manifest_version = {WHY_VERSION}"))


def why_errors(data: Mapping[str, Any], where_of: Mapping[str, str]) -> list[ValidationError]:
    """Every why finding for a manifest whose entries are already well-formed."""
    out: list[ValidationError] = []
    entries = [e for e in data["entries"] if e.get("id") in where_of]
    for e in entries:
        if e.get("kind") == "signal" and "why" in e:
            out.append(finding(f"{where_of[e['id']]}.why", "SIGNAL_WHY", "a signal carries no why: "
                               "the catalog shows the why of each condition it feeds"))
    if data["manifest_version"] < WHY_VERSION:
        _v1_errors(data, where_of, out)
        return out
    seen = reach(entries)
    for e in entries:
        where = where_of[e["id"]]
        if e["kind"] in ("condition", "finding"):
            _text_errors(f"{where}.why", e.get("why"), out, token_ok=False)
            _source_errors(f"{where}.why_source", e.get("why_source"), e.get("why"), out)
        elif e["kind"] == "collector":
            _scan_errors(e, where, seen, out)
    _tag_errors(data.get("tags", {}), out)
    return out


def _scan_errors(c: Mapping[str, Any], where: str, seen: Reach,
                 out: list[ValidationError]) -> None:
    scans = list(c.get("headless", {}))
    whys, sources = c.get("whys", {}), c.get("why_sources", {})
    for extra in sorted((set(whys) | set(sources)) - set(scans)):
        out.append(finding(f"{where}.whys.{extra}", "WHY_NOT_SCAN", f"{extra!r} is not a scan "
                           "this collector reports (its headless table)", available=scans))
    for scan in scans:
        why = whys.get(scan)
        _text_errors(f"{where}.whys.{scan}", why, out, token_ok=True)
        if why == PLUMBING:
            _plumbing_errors(f"{where}.whys.{scan}", str(c["id"]), scan, seen, out)
        _source_errors(f"{where}.why_sources.{scan}", sources.get(scan), why, out)


def _tag_errors(tags: Any, out: list[ValidationError]) -> None:
    tags = tags if isinstance(tags, dict) else {}
    for extra in sorted(set(tags) - set(TRUTH_TAGS)):
        out.append(finding(f"manifest.tags.{extra}", "UNKNOWN_TAG", f"{extra!r} is not a truth tag",
                           available=TRUTH_TAGS))
    for tag in TRUTH_TAGS:
        raw = tags.get(tag, {})
        raw = raw if isinstance(raw, dict) else {}
        for key in sorted(set(raw) - {"why", "why_source"}):
            out.append(finding(f"manifest.tags.{tag}.{key}", "UNKNOWN_KEY", f"unknown key {key!r}",
                               available=("why", "why_source")))
        _text_errors(f"manifest.tags.{tag}.why", raw.get("why"), out, token_ok=False)
        _source_errors(f"manifest.tags.{tag}.why_source", raw.get("why_source"), raw.get("why"), out)
