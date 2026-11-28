"""Plan the publish of a fleet run's page and database, and record where it went (issue #949).

The ``/fleet`` session publishes (``skills/fleet/SKILL.md``, "Publish the fleet page"); this
script only reads files and writes the plan the session sends. It makes no network call and
never publishes.

    fleet_publish_plan.py plan OUT --exports DIR [--org-dir ORG_DIR] [--allow-names] [--run-id ID]
    fleet_publish_plan.py record ORG_DIR --url URL --run-id ID
    fleet_publish_plan.py url ORG_DIR
    fleet_publish_plan.py shards OUT ORG_DIR [--run-id ID]
    fleet_publish_plan.py record-shard ORG_DIR OUT --url URL (--index | --shard FOLDER)

``plan`` reads ``OUT/fleet-data.json`` and ``OUT/fleet-view.html`` and, in ``--exports``, the
artifact's stored documents as ``<collection>.json`` (``runs``, ``units``, ``repos``, ``targets``
and any other the artifact lists; each a JSON list of ``{doc_id, version, data}``; a missing
file is an empty collection, as on a first publish). Documents outside what the fleet writes
are reported as strays.
It writes ``OUT/publish/``: ``page.html`` (the page with its database source),
``capabilities.json``, ``plan.json`` and ``batch-NNN.json`` (the ``ArtifactData`` ``batch``
writes, pinned with ``if_version``). A ``--full`` run is refused; a plain counts-only run is
refused without ``--allow-names``, since its documents name repositories. ``--run-id`` is the
run's history entry name (default: the run's scan date).

**Sharded fleets** (#950). A run over one artifact's limits, or one whose ``ORG_DIR/shards.json``
already records shards, is planned as one artifact per top-level unit plus an index
(``lib/fleet/fleet_shard.py``, ``lib/fleet/fleet_publish_shards.py``). ``shards`` prints one line
per artifact, ``<folder or index> <url or new>``, so the session knows which shard databases to
export into ``--exports DIR/<folder>/``; it prints nothing and exits 1 when the run fits one
artifact. ``plan`` with ``--org-dir`` then writes ``OUT/publish/plan.json`` (the publish steps in
order), ``index.html`` and ``shards/<folder>/`` per shard. ``record-shard`` keeps the index's or
one shard's URL in ``ORG_DIR/shards.json``; ``--shard`` takes the folder ``shards`` prints and is
refused unless ``OUT/publish/plan.json`` names it. Without ``--org-dir`` a run that needs shards is
refused, as before.

``record`` keeps the artifact URL in ``ORG_DIR/publish.json`` (the fleet's history directory for
the org), so the next run publishes to the same artifact. ``url`` prints that URL on stdout, or
nothing with exit 1 before the first publish. A ``publish.json`` that is not ``{url, runs}`` is
refused (exit 2) and left as it was.

Exit codes: 0 written or printed; 1 no URL recorded yet (``url``); 2 refused or unreadable input
(the reason on stderr).
"""
# /// script
# requires-python = ">=3.11"
# ///
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

# skills/fleet/scripts -> skills/assess/scripts (the plugin ships both skills)
ASSESS_SCRIPTS = Path(__file__).resolve().parents[2] / "assess" / "scripts"
sys.path.insert(0, str(ASSESS_SCRIPTS))

from lib.fleet.fleet_page import PAGE_FILE
from lib.fleet.fleet_page_live import live_page
from lib.fleet.fleet_publish import PublishError
from lib.fleet.fleet_publish_shards import plan_sharded, read_shards, record_shard, shard_key, write_sharded
from lib.fleet.fleet_shard import UNASSIGNED, shard_plan
from lib.fleet.fleet_publish_session import (
    LIMITS_PINNED,
    PUBLISH_DIR,
    STORED_COLLECTIONS,
    StoredDoc,
    plan_publish,
    read_export,
    record_url,
    recorded_url,
    write_plan,
)


_BUNDLE = re.compile(r'<script type="application/json" id="fleet-bundle">(.*?)</script>', re.S)
COMMANDS = ("plan", "record", "url", "shards", "record-shard")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plan the publish of a fleet run's page and database.")
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="Write OUT/publish/: the page, capabilities and pinned batches.")
    plan.add_argument("out", help="The fleet run's --out directory.")
    plan.add_argument("--exports", required=True, metavar="DIR",
                      help="Where the session saved runs.json, units.json and repos.json.")
    plan.add_argument("--allow-names", action="store_true",
                      help="Publish a plain counts-only run, whose documents name repositories and units.")
    plan.add_argument("--run-id", metavar="ID", help="The run's history entry name (default: its scan date).")
    plan.add_argument("--org-dir", metavar="ORG_DIR",
                      help="The fleet's history directory for the org: read for shards.json, so a run over one "
                           "artifact's limits is planned as shards.")
    shards = sub.add_parser("shards", help="Print each artifact of a sharded plan and its URL (exit 1 if not sharded).")
    shards.add_argument("out", help="The fleet run's --out directory.")
    shards.add_argument("org_dir", help="The fleet's history directory for the org.")
    shards.add_argument("--run-id", metavar="ID", help="The run's history entry name (default: its scan date).")
    shard = sub.add_parser("record-shard", help="Keep the index's or one shard's URL in ORG_DIR/shards.json.")
    shard.add_argument("org_dir", help="The fleet's history directory for the org.")
    shard.add_argument("out", help="The fleet run's --out directory, holding the written publish/plan.json.")
    shard.add_argument("--url", required=True, help="The artifact's claude.ai URL.")
    which = shard.add_mutually_exclusive_group(required=True)
    which.add_argument("--index", action="store_true", help="The URL is the index's.")
    which.add_argument("--shard", metavar="FOLDER", help="The shard's folder, as `shards` and plan.json name it.")
    record = sub.add_parser("record", help="Keep the artifact URL in ORG_DIR/publish.json.")
    record.add_argument("org_dir", help="The fleet's history directory for the org.")
    record.add_argument("--url", required=True, help="The artifact's claude.ai URL.")
    record.add_argument("--run-id", required=True, metavar="ID", help="The run published.")
    url = sub.add_parser("url", help="Print the artifact URL ORG_DIR/publish.json records (exit 1 if none).")
    url.add_argument("org_dir", help="The fleet's history directory for the org.")
    return parser.parse_args(argv)


def _json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise PublishError(f"cannot read {path.name}: {e}") from e


def _data(out: Path) -> dict[str, Any]:
    data = _json(out / "fleet-data.json")
    if not isinstance(data, dict) or not isinstance(data.get("run"), dict):
        raise PublishError("fleet-data.json is not a fleet-data.json bundle")
    return data


def _exports(folder: Path) -> dict[str, list[StoredDoc]]:
    exports: dict[str, list[StoredDoc]] = {c: [] for c in STORED_COLLECTIONS}  # a first publish has nothing stored
    for path in sorted(folder.glob("*.json")):
        exports[path.stem] = read_export(_json(path), path.stem)
    return exports


def _summary(out: Path) -> dict[str, Any]:
    found = _json(out / PUBLISH_DIR / "plan.json")
    if not isinstance(found, dict) or not found.get("sharded"):
        raise PublishError("publish/plan.json is not a sharded plan; run plan with --org-dir first")
    return found


def _shards(args: argparse.Namespace) -> int:
    data = _data(Path(args.out))
    run_id = args.run_id or str(data["run"].get("scan_date") or "")
    registry = read_shards(Path(args.org_dir))
    plan, _ = shard_plan(data, LIMITS_PINNED, run_id=run_id, registry=registry)  # type: ignore[arg-type]
    if not plan["sharded"]:
        print("fleet_publish_plan: the run fits one artifact; it is not sharded", file=sys.stderr)
        return 1
    print(f"index {plan['index_url'] or 'new'}")
    for shard in plan["shards"]:
        print(f"{shard['folder']} {shard['url'] or 'new'}")
    return 0


def _plan_sharded(args: argparse.Namespace, out: Path, data: dict[str, Any], page: str, run_id: str) -> int | None:
    """Plan and write a sharded publish; None when the run fits one artifact (see the module doc)."""
    registry = read_shards(Path(args.org_dir))
    root = Path(args.exports)
    exports = {p.name: _exports(p) for p in sorted(root.iterdir()) if p.is_dir()} if root.is_dir() else {}
    sharded = plan_sharded(data, exports, run_id=run_id, registry=registry, allow_names=args.allow_names)
    if sharded is None:
        return None
    found = _BUNDLE.search(page)
    steps = json.loads(found.group(1)).get("steps", []) if found else []
    folder = write_sharded(out, sharded, page, data, steps)
    summary = sharded["summary"]
    print(f"fleet_publish_plan: run {run_id}, {summary['mode']}, sharded into {len(sharded['plan']['shards'])} "
          f"artifact(s) plus an index -> {folder}", file=sys.stderr)
    for shard in sharded["plan"]["shards"]:
        name = "unassigned" if shard["key"] == UNASSIGNED else shard["folder"]
        own = sharded["shards"][shard["key"]][0]
        print(f"fleet_publish_plan: {name}: {len(own['batches'])} batch(es), {own['size']['documents']} of "
              f"{own['size']['document_limit']} documents", file=sys.stderr)
        for warning in own["size"]["warnings"]:
            print(f"fleet_publish_plan: {name}: {warning['document']} is {warning['bytes']} bytes, near the limit",
                  file=sys.stderr)
    if not summary["links_complete"]:
        print("fleet_publish_plan: some pages have no URL yet; publish them, record each URL, then plan again and "
              "republish only the pages so every link resolves", file=sys.stderr)
    return 0


def _plan(args: argparse.Namespace) -> int:
    out = Path(args.out)
    data = _data(out)
    try:
        page = (out / PAGE_FILE).read_text(encoding="utf-8")
    except OSError as e:
        raise PublishError(f"cannot read {PAGE_FILE}: rerun /fleet with --levels ({e.strerror})") from e
    run_id = args.run_id or str(data["run"].get("scan_date") or "")
    if args.org_dir is not None:
        done = _plan_sharded(args, out, data, page, run_id)
        if done is not None:
            return done
    exports = _exports(Path(args.exports))
    summary, batches = plan_publish(data, exports, run_id=run_id, allow_names=args.allow_names)
    size = summary["size"]
    if not size["fits"]:  # nothing oversized is left to send: no batch, and an earlier plan's removed
        for stale in (out / PUBLISH_DIR).glob("batch-*.json"):
            stale.unlink()
        print(f"fleet_publish_plan: the run needs {size['documents']} documents, over one artifact's "
              f"{size['document_limit']}; plan it with --org-dir to shard it, so nothing was planned", file=sys.stderr)
        return 2
    folder = write_plan(out, summary, batches, live_page(page, data, summary["mode"], run_id))
    print(f"fleet_publish_plan: run {run_id}, {summary['mode']}, {len(batches)} batch(es), {size['documents']} of "
          f"{size['document_limit']} documents, targets {'on' if summary['targets_enabled'] else 'off'} -> {folder}",
          file=sys.stderr)
    strays = summary["strays"]
    if strays["count"]:
        print(f"fleet_publish_plan: {strays['count']} document(s) outside what the fleet writes, in "
              f"{', '.join(strays['collections'])}; they count against the document cap", file=sys.stderr)
    for warning in size["warnings"]:
        print(f"fleet_publish_plan: {warning['document']} is {warning['bytes']} bytes, near the limit", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.command == "record":
            path = record_url(Path(args.org_dir), args.url, args.run_id)
            print(f"fleet_publish_plan: recorded -> {path}", file=sys.stderr)
            return 0
        if args.command == "record-shard":
            key = None if args.index else shard_key(args.shard, _summary(Path(args.out)))
            path = record_shard(Path(args.org_dir), args.url, key)
            print(f"fleet_publish_plan: recorded -> {path}", file=sys.stderr)
            return 0
        if args.command == "shards":
            return _shards(args)
        if args.command == "url":
            found = recorded_url(Path(args.org_dir))
            if found is None:
                print("fleet_publish_plan: no artifact recorded yet; this is a first publish", file=sys.stderr)
                return 1
            print(found)
            return 0
        return _plan(args)
    except PublishError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
