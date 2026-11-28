"""The ``/fleet`` publish step for a sharded fleet: what a signed-in session sends per shard (issue #950).

``plan_sharded`` runs ``fleet_publish_session.plan_publish`` on each shard's bundle
(``fleet_shard.shard_plan``) with that shard's own exports, so each shard is checked, sized and
pinned against its own artifact. Nothing here publishes or calls a network.

**Exports.** Each shard is its own artifact with its own database, so the session saves each
shard's collections under ``<exports>/<shard folder>/`` (``fleet_shard.shard_folder``), in the
single-artifact export format. A missing folder is an empty artifact, as on a first publish; a
shard whose documents, with its exported targets and strays, exceed one artifact's cap is refused
(``ShardTooLarge``), as the single-artifact plan is. A current shard the registry records is
refused when it has no export folder or an empty one (no collection at all: ``exports`` maps its
folder to an empty mapping), since its stored documents would otherwise be written over without
their ``if_version`` pins; a retired one is never written, so it needs none. An exported
collection with no documents is a list, so a shard stored empty still passes.

**Output** (``write_sharded``): under ``<out>/publish/``, ``plan.json`` (``sharded`` true, the
run id, the index URL, ``links_complete`` and ``steps``, the publish order: the index, then
each shard by key, ``unassigned`` last), ``index.html`` (``fleet_shard_index.index_page``) and
per shard ``shards/<folder>/`` holding ``page.html``, ``capabilities.json``, ``plan.json`` and
``batch-NNN.json`` as ``fleet_publish_session.write_plan_to`` writes them. An earlier plan's files
are removed first, so a stale batch is never sent.

**Registry** (``read_shards``, ``record_shard``): ``<history>/<org>/shards.json`` holds
``{"index": <url or null>, "shards": {<shard key>: <url>}}``. ``shard_key`` reads a shard's key from
the written ``plan.json`` by its folder, refusing a folder the plan does not name. ``record_shard`` adds the index
URL or one shard's URL, written through a temp file. It refuses a URL that is not
``https://claude.ai/`` and a second URL for a key that already has one; it never removes an
entry, so a unit that leaves the fleet keeps its artifact. A file of another shape is refused,
never overwritten.

**Targets.** Each shard page offers targets on its own units and writes them to its own
``targets`` collection, so the session exports ``targets`` from each shard and passes every
export to one ``fleet_targets_import.py`` call, which merges them into one targets file (#948).

Tests: ``tests/test_fleet_publish_shards.py``.
"""
from __future__ import annotations

import json
import os
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final, TypedDict, cast

from lib.fleet.fleet_data import FleetData
from lib.fleet.fleet_page_live import live_page
from lib.fleet.fleet_publish import PublishError
from lib.fleet.fleet_publish_session import (
    LIMITS_PINNED,
    PUBLISH_DIR,
    PinnedWrite,
    PlanSummary,
    StoredDoc,
    check_mode,
    dumps,
    plan_publish,
    stored_state,
    write_plan_to,
)
from lib.fleet.fleet_shard import ShardPlan, ShardTooLarge, ShardRegistry, assignment, empty_registry, shard_folder, shard_plan
from lib.fleet.fleet_shard_index import URL_PREFIX, index_page

SHARDS_FILE: Final = "shards.json"
SHARDS_DIR: Final = "shards"
INDEX_PAGE: Final = "index.html"

Exports = Mapping[str, Sequence[StoredDoc]]


class Step(TypedDict):
    kind: str  # "index" | "shard"
    key: str | None  # the shard key; None for the index
    folder: str | None  # the shard's folder under shards/; None for the index
    url: str | None  # the recorded artifact URL; None: publish it new, then record it
    page: str  # the page file to publish, relative to publish/


class ShardedSummary(TypedDict):
    run_id: str
    sharded: bool
    mode: str
    index_url: str | None
    links_complete: bool
    added: list[str]
    retired: list[str]
    steps: list[Step]


class ShardedPlan(TypedDict):
    summary: ShardedSummary
    plan: ShardPlan
    shards: dict[str, tuple[PlanSummary, list[list[PinnedWrite]]]]  # shard key -> its own plan


def read_shards(history_org_dir: Path) -> ShardRegistry | None:
    """What ``<history>/<org>/shards.json`` records, or ``None`` when there is no file yet."""
    path = history_org_dir / SHARDS_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as e:
        raise PublishError(f"{SHARDS_FILE} is not readable; fix or remove it ({type(e).__name__})") from e
    try:
        found = json.loads(text)
    except ValueError:
        found = None
    index = found.get("index") if isinstance(found, dict) else None
    shards = found.get("shards") if isinstance(found, dict) else None
    if (not isinstance(found, dict) or not (index is None or isinstance(index, str)) or not isinstance(shards, dict)
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in shards.items())):
        raise PublishError(f"{SHARDS_FILE} is not {{index, shards}}; fix or remove it")
    return {"index": index, "shards": dict(shards)}


def shard_key(folder: str, summary: Mapping[str, Any]) -> str:
    """The shard key of ``folder``, one of the shard steps of a written plan's ``summary``
    (``plan.json``); refuses any other, so a mistyped name never becomes a registry entry."""
    for step in summary.get("steps") or []:
        if step.get("kind") == "shard" and step.get("folder") == folder and isinstance(step.get("key"), str):
            return str(step["key"])
    raise PublishError(f"{folder} is not a shard of the written plan; pass a folder that `shards` or plan.json names")


def record_shard(history_org_dir: Path, url: str, key: str | None) -> Path:
    """Keep the index URL (``key`` None) or one shard's URL in ``shards.json`` (see the module doc)."""
    if not url.startswith(URL_PREFIX):
        raise PublishError("the artifact URL must be a https://claude.ai/ link")
    registry = read_shards(history_org_dir) or empty_registry()
    held = registry["index"] if key is None else registry["shards"].get(key)
    if held is not None and held != url:
        what = "the index" if key is None else "this shard"
        raise PublishError(f"{SHARDS_FILE} already records another artifact for {what}; fix the file to move it")
    if key is None:
        registry["index"] = url
    else:
        registry["shards"][key] = url
    history_org_dir.mkdir(parents=True, exist_ok=True)
    path = history_org_dir / SHARDS_FILE
    temp = path.with_name(f".{SHARDS_FILE}.tmp")
    temp.write_text(dumps({"index": registry["index"], "shards": dict(sorted(registry["shards"].items()))}),
                    encoding="utf-8", newline="\n")
    os.replace(temp, path)
    return path


def plan_sharded(data: Mapping[str, Any], exports: Mapping[str, Exports], *, run_id: str,
                 registry: ShardRegistry | None, allow_names: bool = False) -> ShardedPlan | None:
    """The sharded plan, or ``None`` when the run fits one artifact and no shard is recorded.

    ``exports`` maps a shard folder to that shard's exports."""
    mode = check_mode(data, allow_names)
    registry = registry or empty_registry()
    bundle = cast(FleetData, data)
    stored: dict[str, dict[str, int]] = {}
    current = set(assignment(bundle))  # a retired shard is never written, so it needs no export
    for key in sorted(k for k in registry["shards"] if k in current):
        folder = shard_folder(key)
        if folder not in exports:
            raise PublishError(f"shard {folder} is recorded but its collections were not exported; export them "
                               f"into <exports>/{folder}/ first")
        if not exports[folder]:
            raise PublishError(f"shard {folder} is recorded but <exports>/{folder}/ holds no collection; export "
                               "its collections into it first")
        stored[key] = stored_state(exports[folder], run_id)["stored"]
    plan, bundles = shard_plan(bundle, LIMITS_PINNED, run_id=run_id, registry=registry, stored=stored)
    if not plan["sharded"]:
        return None
    shards: dict[str, tuple[PlanSummary, list[list[PinnedWrite]]]] = {}
    steps: list[Step] = [{"kind": "index", "key": None, "folder": None, "url": plan["index_url"], "page": INDEX_PAGE}]
    for shard in plan["shards"]:
        own = plan_publish(bundles[shard["key"]], exports.get(shard["folder"], {}), run_id=run_id,
                           allow_names=allow_names)
        size = own[0]["size"]
        if not size["fits"]:  # the shard's strays and targets count against its cap too
            raise ShardTooLarge(f"unit {shard['label']} needs about {size['documents']} documents with its stored "
                                f"targets and strays, over one artifact's {size['document_limit']}")
        shards[shard["key"]] = own
        steps.append({"kind": "shard", "key": shard["key"], "folder": shard["folder"], "url": shard["url"],
                      "page": f"{SHARDS_DIR}/{shard['folder']}/page.html"})
    summary: ShardedSummary = {"run_id": run_id, "sharded": True, "mode": mode, "index_url": plan["index_url"],
                               "links_complete": plan["links_complete"], "added": plan["added"],
                               "retired": plan["retired"], "steps": steps}
    return {"summary": summary, "plan": plan, "shards": shards}


def write_sharded(out: Path, sharded: ShardedPlan, page: str, data: Mapping[str, Any],
                  steps: Sequence[Mapping[str, Any]] = ()) -> Path:
    """Write ``<out>/publish/`` for a sharded plan (see the module docstring)."""
    folder = out / PUBLISH_DIR
    if folder.is_dir() and not folder.is_symlink():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    summary = sharded["summary"]
    for shard in sharded["plan"]["shards"]:
        own, batches = sharded["shards"][shard["key"]]
        page_html = live_page(page, data, summary["mode"], summary["run_id"], shard["view"])
        write_plan_to(folder / SHARDS_DIR / shard["folder"], own, batches, page_html)
    index = sharded["plan"]["index"]
    assert index is not None
    (folder / INDEX_PAGE).write_text(index_page(index, steps), encoding="utf-8", newline="\n")
    (folder / "plan.json").write_text(dumps(summary), encoding="utf-8", newline="\n")
    return folder
