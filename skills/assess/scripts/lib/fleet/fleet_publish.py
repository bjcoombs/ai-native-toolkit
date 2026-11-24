"""The fleet artifact database projection: ``fleet-data.json`` to ``batch`` writes (issue #947, parent #705).

``project`` is a pure function from one run's bundle (``lib.fleet.fleet_data``) to the writes the
``ArtifactData`` ``batch`` action takes, chunked into batches, with a size report. It never
calls the network; the ``/fleet`` publish step (#949) sends the batches.

**Collections** (#705's design proposal):

- ``runs/{runId}``: ``set``. The bundle without its ``views`` and ``repos``, plus ``views``
  as ``{view, units}`` counts and ``counts`` (``repos``, ``units``). Republishing a run
  replaces the document with the same bytes.
- ``units/{unitKey}`` and ``repos/{repoKey}``: one document per unit and repository whose body
  is exactly ``{"history": {<runId>: <entry>}}``, where the entry is the bundle's unit or
  repository object without its ``key``. Every field of the bundle is copied generically, so
  a field the bundle gains (#945's ``gate_blockers``) is published with no change here. The
  database's ``update`` merges nested objects recursively, so a run touches only its own
  ``history.<runId>``; republishing the same bundle writes the same value again. A run id is
  immutable once published: a merge cannot remove a field or an entry, so a republish whose
  ``runs`` document or any entry differs from what the run id holds (``previous``), or that
  no longer writes a document the run id holds, is refused with ``RepublishConflict``. The
  check runs only on what ``previous`` holds, so the publisher always passes it (#949). ``update`` requires the
  document to exist, so ``stored`` (below) decides the op: ``update`` for a document already
  stored, ``set`` (the same body) for a new one.
- ``targets``: never written here; Contributors write it from the page (#948 reads it back).

**Document ids** (``doc_id``): the database allows ``[A-Za-z0-9_-.~:@+]``, 1 to 200
characters, in one path segment, and a unit key holds ``/``. Every other character, and
``~`` and ``.`` themselves, is written as ``~`` plus two upper-case hex digits per UTF-8 byte
(``ownership/grp/sub`` is ``ownership~2Fgrp~2Fsub``; a hashed key ``h:...`` is unchanged).
The encoding is injective and ``decode_doc_id`` reverses it. The run id is encoded the same
way when it is the ``history`` map key, so a same-day id (``2026-11-20.2``) holds no dot.

**Privacy.** A ``--full`` bundle (``run.counts_only`` false) is refused (#705 decision 2).
Every string in the batch is checked against the counts-only rules by its ``fleet-data.json``
pattern (``counts_only_rules.FLEET_DATA_RULES``, else ``FLEET_DATA_KEPT``; a unit entry reads
as ``views.units.*``, a repository entry as ``repos.*``): a ``token`` must match ``TOKEN_RE``,
a ``drop`` must be absent, and a string under neither table is refused. Under ``--hash-paths``
(``run.paths_hashed``) every ``path`` string and every unit and repository key must be a
hash, and an ``id`` (``web_url``) must be absent. ``PrivacyError`` names each offending pattern.

**Size guard** (``Limits``, ``LIMITS``): each document's encoded size is its body as compact,
key-sorted UTF-8 JSON, plus, for an ``update``, the stored size the caller passes in
``stored`` (the guard sees only this run's increment otherwise; a republished run is
counted twice, which overstates and never understates). At ``WARN_SHARE`` (80%) of the
per-document limit the document is listed in ``warnings``; above the limit, or deeper than
the depth limit, ``DocumentTooLarge`` names it. The document count after the publish (the
stored documents plus the new ones) is reported against the per-artifact limit with
``fits``, for #950's sharding; the count is not refused here. A key that encodes to more
than 200 characters (possible only without hashing) refuses the whole publish.

**Batches**: writes are ordered ``runs``, ``units``, ``repos``, each by doc id, and cut
greedily into batches of at most ``Limits.batch_writes`` writes and ``Limits.batch_bytes``
of body. The same bundle, run id and ``stored`` give byte-identical ``dumps_plan`` output.

Writes carry no ``if_version``: the ``ArtifactData`` tool refuses a write to an existing
document without one, so the publisher reads the versions and pins each write (#949).

``access_rules`` is the ``db`` capability's rules manifest: a root rule (``path`` ``""``) of
read ``view`` and write ``ROOT_WRITE`` (``interact`` since #949, so the page can tell a
Contributor from a Viewer; ``owner``, #1044's choice, kept every viewer from writing documents
outside the collections, where each counts against the document cap), then ``runs``, ``units`` and ``repos`` read
``view`` and write ``owner``, and ``targets`` read ``view`` and write ``interact``.

Reference for the fleet's readers: ``skills/fleet/references/publish.md``.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from typing import Final, NamedTuple, TypedDict

from lib.counts_only.content_free import is_hashed
from lib.counts_only.counts_only_rules import (
    DROP,
    FLEET_DATA_KEPT,
    FLEET_DATA_RULES,
    HASHED_PATH,
    ID,
    PATH,
    TOKEN,
    TOKEN_RE,
)
from lib.fleet.fleet_data import FleetData

RUNS, UNITS, REPOS, TARGETS = "runs", "units", "repos", "targets"
HISTORY = "history"


class Limits(NamedTuple):
    """The artifact database limits the guard and the batching use."""

    document_bytes: int  # one document's serialized body
    documents: int  # documents in one artifact's database
    depth: int  # nesting levels in one document
    batch_writes: int  # writes in one ``batch`` call
    batch_bytes: int  # one ``batch`` call's body


# Sources: #692's "Artifact database layout" (the layout #705's proposal adopts) states
# 25,000 documents per artifact and 256 KB per document; the db capability's type
# definitions (``db.d.ts``) state "at most 256 KiB serialized and 32 levels deep", so 256 KB
# is read as 256 KiB. The document cap depends on the runtime contract: ``db.d.ts`` at 0.2.47
# states "at most 5,000 documents in total" and at 0.2.96 "at most 25,000", and a create over
# it rejects ``quota_exceeded``. ``documents`` is the smaller, 5,000, so a plan that fits fits
# under either; a publisher pinned to a contract stating more passes its own ``Limits``. The
# ``ArtifactData`` tool's ``batch`` action takes "up to 50" writes, each document addressed at
# most once, in a body of "at most 1 MiB".
LIMITS: Final = Limits(document_bytes=256 * 1024, documents=5_000, depth=32,
                       batch_writes=50, batch_bytes=1024 * 1024)
WARN_SHARE: Final = 0.8


class AccessRule(TypedDict):
    path: str
    read: str
    write: str


# The root's write level (#949, option B). At runtime contract 0.2.96 ``user.can("data.write")``
# answers for the root rule only, so the page can offer target entry to a Contributor only when
# the root lets ``interact`` write. The cost: a Contributor can create stray documents outside the
# collections, against the document cap (#1044 chose "owner" to close that). "owner" (option A) is
# this one constant; the maintainer's call is recorded on #949's PR.
ROOT_WRITE: Final = "interact"

# #705's proposal, "Access rules": read view; runs, units and repos write owner; targets write interact.
ACCESS_RULES: Final[tuple[AccessRule, ...]] = (
    {"path": "", "read": "view", "write": ROOT_WRITE},  # db.d.ts: the root is a rule path ("")
    {"path": RUNS, "read": "view", "write": "owner"},
    {"path": UNITS, "read": "view", "write": "owner"},
    {"path": REPOS, "read": "view", "write": "owner"},
    {"path": TARGETS, "read": "view", "write": "interact"},
)


class DbCapability(TypedDict):
    rules: list[AccessRule]


class AccessManifest(TypedDict):
    db: DbCapability


def access_rules() -> AccessManifest:
    """The ``capabilities`` entry for the artifact's ``db``."""
    return {"db": {"rules": [{**rule} for rule in ACCESS_RULES]}}


class PublishError(ValueError):
    """The bundle cannot be published."""


class FullRunRefused(PublishError):
    pass


class PrivacyError(PublishError):
    pass


class DocumentTooLarge(PublishError):
    pass


class RepublishConflict(PublishError):
    pass


# --- document ids --------------------------------------------------------------------------

_SAFE = re.compile(r"[A-Za-z0-9_\-:@+]")
MAX_ID = 200


def doc_id(key: str) -> str:
    """``key`` as one database path segment (see the module docstring)."""
    out = "".join(ch if _SAFE.fullmatch(ch) else "".join(f"~{b:02X}" for b in ch.encode("utf-8"))
                  for ch in key)
    if not out or len(out) > MAX_ID:
        raise PublishError(f"key {key!r} encodes to {len(out)} characters; a document id takes 1 to {MAX_ID}")
    return out


def decode_doc_id(value: str) -> str:
    """The key ``doc_id`` encoded."""
    return re.sub(rb"~([0-9A-F]{2})", lambda m: bytes([int(m.group(1), 16)]),
                  value.encode("utf-8")).decode("utf-8")


# --- writes --------------------------------------------------------------------------------

Body = dict[str, object]


class Write(TypedDict):
    op: str  # "set" | "update"
    collection: str
    doc_id: str
    data: Body


class DocSize(TypedDict):
    document: str  # "<collection>/<doc id>"
    bytes: int


class SizeReport(TypedDict):
    documents: int  # stored documents plus the ones this publish creates
    document_limit: int
    fits: bool  # documents within the per-artifact limit (else #950 shards)
    document_bytes_limit: int
    peak: DocSize  # the largest document this publish writes
    warnings: list[DocSize]  # documents at or above WARN_SHARE of the per-document limit


class PublishPlan(TypedDict):
    run_id: str
    batches: list[list[Write]]
    size: SizeReport


def _encoded(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def _depth(value: object) -> int:
    if isinstance(value, dict):
        return 1 + max((_depth(v) for v in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((_depth(v) for v in value), default=0)
    return 0


def _strings(node: object, pattern: str) -> Iterator[tuple[str, str]]:
    """Every string under ``node`` with its dotted pattern (list levels left out)."""
    if isinstance(node, str):
        yield pattern, node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from _strings(value, f"{pattern}.{key}" if pattern else str(key))
    elif isinstance(node, list):
        for item in node:
            yield from _strings(item, pattern)


def _violation(pattern: str, value: str, hashed: bool) -> str | None:
    if pattern in FLEET_DATA_KEPT:
        return None
    cls = FLEET_DATA_RULES.get(pattern)
    if cls is None:
        return f"{pattern}: no counts-only rule"
    if cls == DROP:
        return f"{pattern}: dropped in counts-only output"
    if cls == TOKEN and not TOKEN_RE.match(value):
        return f"{pattern}: not a token"
    if cls == ID and hashed:
        return f"{pattern}: not written under --hash-paths"
    if cls == PATH and hashed and not is_hashed(value):
        return f"{pattern}: not hashed under --hash-paths"
    if cls == HASHED_PATH and not (hashed and is_hashed(value)):
        return f"{pattern}: written only hashed, under --hash-paths"
    return None


def check_strings(node: object, base: str, hashed: bool) -> list[str]:
    """The counts-only violations among the strings under ``node``, read as ``base`` patterns."""
    return sorted({v for p, s in _strings(node, base) if (v := _violation(p, s, hashed)) is not None})


def _run_document(bundle: FleetData) -> Body:
    rest: Body = {k: v for k, v in bundle.items() if k not in ("views", "repos")}
    views = [{"view": view["view"], "units": len(view["units"])} for view in bundle["views"]]
    units = sum(len(view["units"]) for view in bundle["views"])
    return {**rest, "views": views, "counts": {"repos": len(bundle["repos"]), "units": units}}


Member = tuple[str, str, Mapping[str, object]]  # (collection, bundle key, bundle entry)


def _members(bundle: FleetData) -> list[Member]:
    members: list[Member] = [(UNITS, unit["key"], unit) for view in bundle["views"] for unit in view["units"]]
    return members + [(REPOS, repo["key"], repo) for repo in bundle["repos"]]


def _check_privacy(bundle: FleetData, run_document: Body, members: list[Member]) -> None:
    run = bundle["run"]
    if not run["counts_only"]:
        raise FullRunRefused("a --full run is never published: rerun /fleet without --full")
    hashed = run["paths_hashed"]
    problems = check_strings(run_document, "", hashed)
    for collection, key, entry in members:
        problems += check_strings(entry, "views.units" if collection == UNITS else "repos", hashed)
        if hashed and not is_hashed(key):
            problems.append(f"{collection} document id: not hashed under --hash-paths")
    if problems:
        raise PrivacyError("counts-only check failed: " + "; ".join(sorted(set(problems))))


def _writes(bundle: FleetData, run_id: str, stored: Mapping[str, int]) -> list[Write]:
    run_key = doc_id(run_id)
    run_document = _run_document(bundle)
    members = _members(bundle)
    _check_privacy(bundle, run_document, members)
    writes: list[Write] = [{"op": "set", "collection": RUNS, "doc_id": run_key, "data": run_document}]
    entries = sorted(((collection, doc_id(key), entry) for collection, key, entry in members),
                     key=lambda m: (m[0] != UNITS, m[1]))
    for collection, key, entry in entries:
        body: Body = {HISTORY: {run_key: {k: v for k, v in entry.items() if k != "key"}}}
        op = "update" if f"{collection}/{key}" in stored else "set"
        writes.append({"op": op, "collection": collection, "doc_id": key, "data": body})
    seen = [f"{w['collection']}/{w['doc_id']}" for w in writes]
    if len(seen) != len(set(seen)):
        raise PublishError("two bundle entries share a document id")
    return writes


def size_report(writes: list[Write], stored: Mapping[str, int], limits: Limits = LIMITS) -> SizeReport:
    """The guard over ``writes`` (see the module docstring); raises ``DocumentTooLarge``."""
    sizes: list[DocSize] = []
    for write in writes:
        name = f"{write['collection']}/{write['doc_id']}"
        size = len(_encoded(write["data"])) + (stored.get(name, 0) if write["op"] == "update" else 0)
        if size > limits.document_bytes:
            raise DocumentTooLarge(f"{name} is about {size} bytes, over the {limits.document_bytes}-byte "
                                   "per-document limit")
        if _depth(write["data"]) > limits.depth:
            raise DocumentTooLarge(f"{name} is nested deeper than {limits.depth} levels")
        sizes.append({"document": name, "bytes": size})
    peak = max(sizes, key=lambda s: (s["bytes"], s["document"]))
    documents = len(set(stored) | {s["document"] for s in sizes})
    return {"documents": documents, "document_limit": limits.documents, "fits": documents <= limits.documents,
            "document_bytes_limit": limits.document_bytes, "peak": peak,
            "warnings": [s for s in sizes if s["bytes"] >= WARN_SHARE * limits.document_bytes]}


def batches(writes: list[Write], limits: Limits = LIMITS) -> list[list[Write]]:
    """``writes`` in order, cut into ``batch`` calls within the count and body limits."""
    out: list[list[Write]] = []
    current: list[Write] = []
    size = len(_encoded({"writes": []}))
    for write in writes:
        add = len(_encoded(write)) + 1
        if current and (len(current) == limits.batch_writes or size + add > limits.batch_bytes):
            out.append(current)
            current, size = [], len(_encoded({"writes": []}))
        current.append(write)
        size += add
    if current:
        out.append(current)
    return out


def check_republish(writes: list[Write], previous: Mapping[str, object]) -> None:
    """Refuse when this run id already holds something else: a merge cannot remove a field.

    ``previous`` maps ``"<collection>/<doc id>"`` to what the run already holds there: the
    ``runs`` document, or a unit's or repository's ``history.<runId>`` entry."""
    written = {f"{w['collection']}/{w['doc_id']}" for w in writes}
    changed = sorted(name for name in previous if name not in written)  # a unit or repo the run dropped
    for write in writes:
        name = f"{write['collection']}/{write['doc_id']}"
        if name not in previous:
            continue
        new: object = write["data"]
        if write["collection"] != RUNS:
            history = write["data"][HISTORY]
            assert isinstance(history, dict)
            new = next(iter(history.values()))
        if _encoded(new) != _encoded(previous[name]):
            changed.append(name)
    if changed:
        shown = ", ".join(changed[:5]) + (f" and {len(changed) - 5} more" if len(changed) > 5 else "")
        raise RepublishConflict(f"run {writes[0]['doc_id']} is already published with other content "
                                f"in {shown}; a published run id is immutable, so publish under a new one")


def project(bundle: FleetData, run_id: str, *, stored: Mapping[str, int] | None = None,
            previous: Mapping[str, object] | None = None, limits: Limits = LIMITS) -> PublishPlan:
    """The publish plan for one run.

    ``run_id`` is the run's history entry name (``scan_date``, or ``<scan_date>.<n>`` for a
    later same-day run). ``stored`` maps ``"<collection>/<doc id>"`` to the encoded size of
    each document already in the database (empty for a first publish). ``previous`` holds
    what this run id already holds in each document (``check_republish``); ``limits``
    defaults to ``LIMITS``, the smallest caps any runtime contract states."""
    stored = stored or {}
    writes = _writes(bundle, run_id, stored)
    check_republish(writes, previous or {})
    return {"run_id": run_id, "batches": batches(writes, limits), "size": size_report(writes, stored, limits)}


def dumps_plan(plan: PublishPlan) -> str:
    """Canonical bytes: sorted keys, compact, trailing newline."""
    return _encoded(plan).decode("utf-8") + "\n"
