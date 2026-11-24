"""Plan the publish of a fleet run's page and database, and record where it went (issue #949).

The ``/fleet`` session publishes (``skills/fleet/SKILL.md``, "Publish the fleet page"); this
script only reads files and writes the plan the session sends. It makes no network call and
never publishes.

    fleet_publish_plan.py plan OUT --exports DIR [--allow-names] [--run-id ID]
    fleet_publish_plan.py record ORG_DIR --url URL --run-id ID

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

``record`` keeps the artifact URL in ``ORG_DIR/publish.json`` (the fleet's history directory for
the org), so the next run publishes to the same artifact.

Exit codes: 0 written; 2 refused or unreadable input (the reason on stderr).
"""
# /// script
# requires-python = ">=3.11"
# ///
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

# skills/fleet/scripts -> skills/assess/scripts (the plugin ships both skills)
ASSESS_SCRIPTS = Path(__file__).resolve().parents[2] / "assess" / "scripts"
sys.path.insert(0, str(ASSESS_SCRIPTS))

from lib.fleet.fleet_page import PAGE_FILE
from lib.fleet.fleet_page_live import live_page
from lib.fleet.fleet_publish import PublishError
from lib.fleet.fleet_publish_session import (
    PUBLISH_DIR,
    STORED_COLLECTIONS,
    StoredDoc,
    plan_publish,
    read_export,
    record_url,
    write_plan,
)


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
    record = sub.add_parser("record", help="Keep the artifact URL in ORG_DIR/publish.json.")
    record.add_argument("org_dir", help="The fleet's history directory for the org.")
    record.add_argument("--url", required=True, help="The artifact's claude.ai URL.")
    record.add_argument("--run-id", required=True, metavar="ID", help="The run published.")
    return parser.parse_args(argv)


def _json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise PublishError(f"cannot read {path.name}: {e}") from e


def _plan(args: argparse.Namespace) -> int:
    out = Path(args.out)
    data = _json(out / "fleet-data.json")
    if not isinstance(data, dict) or not isinstance(data.get("run"), dict):
        raise PublishError("fleet-data.json is not a fleet-data.json bundle")
    try:
        page = (out / PAGE_FILE).read_text(encoding="utf-8")
    except OSError as e:
        raise PublishError(f"cannot read {PAGE_FILE}: rerun /fleet with --levels ({e.strerror})") from e
    exports: dict[str, list[StoredDoc]] = {c: [] for c in STORED_COLLECTIONS}  # a first publish has nothing stored
    for path in sorted(Path(args.exports).glob("*.json")):
        exports[path.stem] = read_export(_json(path), path.stem)
    run_id = args.run_id or str(data["run"].get("scan_date") or "")
    summary, batches = plan_publish(data, exports, run_id=run_id, allow_names=args.allow_names)
    size = summary["size"]
    if not size["fits"]:  # nothing oversized is left to send: no batch, and an earlier plan's removed
        for stale in (out / PUBLISH_DIR).glob("batch-*.json"):
            stale.unlink()
        print(f"fleet_publish_plan: the run needs {size['documents']} documents, over one artifact's "
              f"{size['document_limit']}; it needs shards (#950), so nothing was planned", file=sys.stderr)
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
        return _plan(args)
    except PublishError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
