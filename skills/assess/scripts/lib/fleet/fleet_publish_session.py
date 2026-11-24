"""The ``/fleet`` publish step's deterministic half: what a signed-in session sends (issue #949).

``fleet.py`` never calls a network. Publishing the viewer and its database is a session step
(``skills/fleet/SKILL.md``, "Publish the fleet page"): the session lists the artifact's stored
documents with ``ArtifactData``, runs ``skills/fleet/scripts/fleet_publish_plan.py plan``, which calls
``plan_publish`` here, and then sends what it wrote. This module only reads files and returns
data; nothing in it, its CLI or its tests publishes or writes to a database.

**Mode** (``check_mode``, #705 decision 2). A ``--full`` run is never published
(``fleet_publish.FullRunRefused``). The default needs a hashed run (``--hash-paths`` or
``--hash-salt``), the strictest mode the issue allows. A plain counts-only run, whose
documents name repositories and units, is published only with ``allow_names``. Target entry and
the targets read-back (#948) work only for a plain counts-only run: the read-back matches plain
unit ids, so a hashed page shows its data read-only (``targets_enabled``).

**Stored state** (``stored_state``). Each collection's export is a JSON list of
``{doc_id, version, data}`` rows, one per stored document (the shape #948's targets export
takes). From the ``runs``, ``units`` and ``repos`` exports come ``stored`` (each document's
encoded size, for ``fleet_publish.project``'s size guard and its ``update``/``set`` choice),
``versions`` (each write's ``if_version`` pin: ``ArtifactData`` refuses an unpinned write to an
existing document) and ``previous`` (what this run id already holds, so a changed republish is
refused with ``RepublishConflict``, never merged). Rows are viewer-shared data: a row that is
not that shape stops the plan, naming the collection and the row index only.

**Strays** (``strays``, AR-148). The root rule lets Contributors write (#949 option B), so a
document can appear outside what the fleet writes and count against the document cap. The plan
counts every row of an exported collection other than ``runs``, ``units``, ``repos`` and
``targets``, and every ``targets`` row whose id is not a target id, and adds them and the valid
targets to the document count. The database tool lists one named collection at a time, so a
stray in a collection nobody exported is not seen; the session also exports ``targets`` and any
collection the artifact lists.

**Contract** (``CONTRACT``, ``LIMITS_PINNED``). The page is published pinned to runtime contract
0.2.96, whose ``db`` states at most 25,000 documents per artifact; the plan uses that cap. The
only declared capability is ``db`` (``fleet_publish.access_rules``, #947's rules with the root
write level of #949). The page asks ``user.can("data.write")`` before it offers target entry,
which needs no ``user`` declaration, and it records no viewer id.

**Output** (``write_plan``): under ``<out>/publish/``, ``batch-NNN.json`` (each a list of
``ArtifactData`` ``batch`` writes, each pinned when its document exists), ``capabilities.json``,
``plan.json`` (the run id, mode, contract, size report and batch files) and ``page.html``
(``fleet-view.html`` with the live data source, ``fleet_page_live.py``). The same inputs give
the same bytes. ``record_url`` keeps the artifact's URL in the fleet's history directory
(``<history>/<org>/publish.json``) so a later run publishes to the same artifact.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final, TypedDict

from lib.fleet.fleet_page_live import TARGET_ID_RE
from lib.fleet.fleet_publish import (
    HISTORY,
    LIMITS,
    REPOS,
    RUNS,
    TARGETS,
    UNITS,
    FullRunRefused,
    Limits,
    PublishError,
    PublishPlan,
    SizeReport,
    access_rules,
    doc_id,
    project,
)

CONTRACT: Final = "0.2.96"
# db.d.ts at runtime contract 0.2.96: "an artifact's database holds at most 25,000 documents".
LIMITS_PINNED: Final = LIMITS._replace(documents=25_000)
PUBLISH_DIR: Final = "publish"
REGISTRY_FILE: Final = "publish.json"
STORED_COLLECTIONS: Final = (RUNS, UNITS, REPOS)


class StoredDoc(TypedDict):
    doc_id: str
    version: int
    data: dict[str, Any]


class PinnedWrite(TypedDict, total=False):
    op: str
    collection: str
    doc_id: str
    data: dict[str, object]
    if_version: int


class StoredState(TypedDict):
    stored: dict[str, int]  # "<collection>/<doc id>" -> encoded size
    versions: dict[str, int]  # "<collection>/<doc id>" -> version, the write's pin
    previous: dict[str, object]  # "<collection>/<doc id>" -> what this run id already holds


class Strays(TypedDict):
    """Documents outside what the fleet writes (AR-148): the root lets Contributors write."""

    count: int  # stray documents in the exports the session saved
    collections: list[str]  # the collections they are in, sorted
    targets: int  # stored targets documents (each counts against the document cap)


class PlanSummary(TypedDict):
    run_id: str
    mode: str  # "hashed" | "counts_only"
    targets_enabled: bool
    contract: str
    size: SizeReport
    strays: Strays
    batches: list[str]  # the batch files, in send order


def check_mode(data: Mapping[str, Any], allow_names: bool) -> str:
    """The run's publish mode, or ``PublishError`` when it may not be published as asked."""
    run = data.get("run") or {}
    if not run.get("counts_only", False):
        raise FullRunRefused("a --full run is never published: rerun /fleet without --full")
    if run.get("paths_hashed"):
        return "hashed"
    if not allow_names:
        raise PublishError("this run is plain counts-only, so its pages and documents name repositories and "
                           "units; publish a --hash-paths run, or pass --allow-names to publish the names")
    return "counts_only"


def targets_enabled(mode: str) -> bool:
    """Target entry and the read-back work only on plain unit ids (#948 refuses hashed keys)."""
    return mode == "counts_only"


def read_export(rows: object, collection: str) -> list[StoredDoc]:
    """One collection's export, checked; raises ``PublishError`` naming the row index only."""
    if not isinstance(rows, list):
        raise PublishError(f"the {collection} export is not a JSON list")
    out: list[StoredDoc] = []
    for index, row in enumerate(rows):
        ok = (isinstance(row, dict) and isinstance(row.get("doc_id"), str)
              and type(row.get("version")) is int and row["version"] >= 1 and isinstance(row.get("data"), dict))
        if not ok:
            raise PublishError(f"the {collection} export row {index} is not {{doc_id, version, data}}")
        out.append({"doc_id": row["doc_id"], "version": row["version"], "data": row["data"]})
    return out


def _size(data: object) -> int:
    return len(json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def stored_state(exports: Mapping[str, Sequence[StoredDoc]], run_id: str) -> StoredState:
    """``stored``, ``versions`` and ``previous`` from the collections' exports (see the module doc)."""
    run_key = doc_id(run_id)
    state: StoredState = {"stored": {}, "versions": {}, "previous": {}}
    for collection in STORED_COLLECTIONS:
        for row in exports.get(collection, ()):
            name = f"{collection}/{row['doc_id']}"
            state["stored"][name] = _size(row["data"])
            state["versions"][name] = row["version"]
            if collection == RUNS:
                if row["doc_id"] == run_key:
                    state["previous"][name] = row["data"]
            else:
                history = row["data"].get(HISTORY)
                if isinstance(history, dict) and run_key in history:
                    state["previous"][name] = history[run_key]
    return state


def strays(exports: Mapping[str, Sequence[StoredDoc]]) -> Strays:
    """The documents the fleet did not write: every row of a collection other than ``runs``,
    ``units``, ``repos`` and ``targets``, and every ``targets`` row whose id is not a target id
    (``TARGET_ID_RE``)."""
    pattern = re.compile(TARGET_ID_RE)
    out: Strays = {"count": 0, "collections": [], "targets": 0}
    for collection in sorted(exports):
        rows = exports[collection]
        if collection in STORED_COLLECTIONS:
            continue
        if collection == TARGETS:
            bad = [row for row in rows if not pattern.fullmatch(row["doc_id"])]
            out["targets"] = len(rows) - len(bad)
        else:
            bad = list(rows)
        if bad:
            out["count"] += len(bad)
            out["collections"].append(collection)
    return out


def pinned(plan: PublishPlan, versions: Mapping[str, int]) -> list[list[PinnedWrite]]:
    """The plan's batches, each write pinned to its document's stored version when it has one."""
    out: list[list[PinnedWrite]] = []
    for batch in plan["batches"]:
        writes: list[PinnedWrite] = []
        for write in batch:
            entry: PinnedWrite = {"op": write["op"], "collection": write["collection"],
                                  "doc_id": write["doc_id"], "data": write["data"]}
            version = versions.get(f"{write['collection']}/{write['doc_id']}")
            if version is not None:
                entry["if_version"] = version
            writes.append(entry)
        out.append(writes)
    return out


def capabilities() -> dict[str, object]:
    """The page's ``capabilities``: the #947 ``db`` rules only. ``user.can("data.write")``, which
    decides whether target entry is offered, needs no declaration, and the page records no viewer id."""
    return {**access_rules()}


def plan_publish(data: Mapping[str, Any], exports: Mapping[str, Sequence[StoredDoc]], *, run_id: str,
                 allow_names: bool = False, limits: Limits = LIMITS_PINNED) -> tuple[PlanSummary, list[list[PinnedWrite]]]:
    """The checked, pinned plan for one run (see the module docstring)."""
    mode = check_mode(data, allow_names)
    state = stored_state(exports, run_id)
    plan = project(data, run_id, stored=state["stored"], previous=state["previous"], limits=limits)  # type: ignore[arg-type]
    batches = pinned(plan, state["versions"])
    extra = strays(exports)
    size = plan["size"]
    documents = size["documents"] + extra["count"] + extra["targets"]  # every document counts against the cap
    size = {**size, "documents": documents, "fits": documents <= size["document_limit"]}
    summary: PlanSummary = {"run_id": run_id, "mode": mode, "targets_enabled": targets_enabled(mode),
                            "contract": CONTRACT, "size": size, "strays": extra,
                            "batches": [f"batch-{i:03d}.json" for i in range(1, len(batches) + 1)]}
    return summary, batches


def dumps(value: object) -> str:
    return json.dumps(value, sort_keys=True, indent=1, ensure_ascii=False) + "\n"


def write_plan(out: Path, summary: PlanSummary, batches: Sequence[Sequence[PinnedWrite]], page: str) -> Path:
    """Write ``<out>/publish/`` (see the module docstring), replacing an earlier plan's files."""
    folder = out / PUBLISH_DIR
    folder.mkdir(parents=True, exist_ok=True)
    for stale in folder.glob("batch-*.json"):
        stale.unlink()
    for name, batch in zip(summary["batches"], batches):
        (folder / name).write_text(dumps(list(batch)), encoding="utf-8", newline="\n")
    (folder / "capabilities.json").write_text(dumps(capabilities()), encoding="utf-8", newline="\n")
    (folder / "plan.json").write_text(dumps(summary), encoding="utf-8", newline="\n")
    (folder / "page.html").write_text(page, encoding="utf-8", newline="\n")
    return folder


def record_url(history_org_dir: Path, url: str, run_id: str) -> Path:
    """Keep the artifact URL and the runs published to it in ``<history>/<org>/publish.json``."""
    if not url.startswith("https://claude.ai/"):
        raise PublishError("the artifact URL must be a https://claude.ai/ link")
    path = history_org_dir / REGISTRY_FILE
    runs: list[str] = []
    if path.is_file():
        old = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(old, dict) and old.get("url") not in (None, url):
            raise PublishError(f"{REGISTRY_FILE} already records another artifact; remove it to publish anew")
        runs = [r for r in (old.get("runs") or []) if isinstance(r, str)] if isinstance(old, dict) else []
    history_org_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps({"url": url, "runs": sorted(set(runs) | {run_id})}), encoding="utf-8", newline="\n")
    return path


def recorded_url(history_org_dir: Path) -> str | None:
    path = history_org_dir / REGISTRY_FILE
    try:
        found = json.loads(path.read_text(encoding="utf-8")).get("url")
    except (OSError, ValueError, AttributeError):
        return None
    return found if isinstance(found, str) else None
