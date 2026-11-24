"""Read the fleet artifact's page-written Step targets back into a targets file (issue #948).

The ``/fleet`` session exports the artifact database's ``targets`` collection to a JSON list
(each row's ``doc_id`` and ``data``), then runs this script before the next run; the file it
writes is what ``fleet.py --targets`` reads. No network call and no model call: the rows are
viewer-written, so each is validated as untrusted input and refused with a named reason.

    fleet_targets_import.py EXPORT --fleet-data FILE --levels FILE --out FILE

EXPORT is the JSON list of ``targets`` rows; ``--fleet-data`` the ``fleet-data.json`` of the
run the page shows (it names the units a row may target); ``--levels`` the maturity model
with ``[[steps]]`` the next run uses. The targets file is always rewritten, sorted and
byte-stable. Each refusal goes to stderr as ``row N: <reason>``; the reasons and the row
contract are in ``skills/assess/scripts/lib/fleet/fleet_targets_import.py``.

Exit codes:
    0  the targets file was written (refused rows are reported, not fatal)
    2  usage error: an unreadable export, bundle or maturity model, a model without Steps, or an
       export with rows of which none was accepted (a bundle from another run); the targets
       file is left untouched

Run:
    uv run fleet_targets_import.py targets-export.json --fleet-data out/fleet-data.json \\
        --levels levels.toml --out targets.toml
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

from lib.fleet.fleet_targets_import import ImportResult, TargetsImportError, dumps_targets, import_rows
from lib.levels import LevelsError, load_levels


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Turn an export of the fleet artifact's targets rows into a fleet.py --targets file.",
    )
    parser.add_argument("export", help="The exported targets rows (a JSON list).")
    parser.add_argument("--fleet-data", required=True, metavar="FILE",
                        help="fleet-data.json of the run the page shows.")
    parser.add_argument("--levels", required=True, metavar="FILE",
                        help="The maturity model (TOML) with [[steps]] the next run uses.")
    parser.add_argument("--out", required=True, metavar="FILE", help="The targets file to write.")
    return parser.parse_args(argv)


def _json(path: str, what: str) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise TargetsImportError(f"cannot read {what} {path}: {e}") from e


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    result: ImportResult | None = None
    try:
        model = load_levels(Path(args.levels))
        rows = _json(args.export, "the export")
        bundle = _json(args.fleet_data, "--fleet-data")
        if not isinstance(bundle, dict) or not isinstance(bundle.get("views"), list) \
                or not isinstance(bundle.get("repos"), list):
            raise TargetsImportError(f"--fleet-data {args.fleet_data} is not a fleet-data.json bundle")
        try:
            result = import_rows(rows, bundle, model)  # type: ignore[arg-type]
        except (KeyError, TypeError) as e:
            raise TargetsImportError(f"--fleet-data {args.fleet_data} is not a fleet-data.json bundle") from e
        if isinstance(rows, list) and rows and not result.targets:
            raise TargetsImportError(f"none of the {len(rows)} exported row(s) was accepted; is --fleet-data the "
                                     f"bundle of the run the page shows? {args.out} is left as it was")
        text = dumps_targets(result.targets, model)
        Path(args.out).write_bytes(text.encode("utf-8"))
    except (TargetsImportError, LevelsError, OSError) as e:
        for refusal in result.refusals if result is not None else []:
            print(f"fleet_targets_import: row {refusal.row}: {refusal.reason}", file=sys.stderr)
        print(f"error: {e}", file=sys.stderr)
        return 2
    for refusal in result.refusals:
        print(f"fleet_targets_import: row {refusal.row}: {refusal.reason}", file=sys.stderr)
    print(f"fleet_targets_import: {len(result.targets)} target(s) written, {len(result.refusals)} row(s) "
          f"refused -> {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
