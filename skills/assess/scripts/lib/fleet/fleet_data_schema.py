"""The published JSON schema of ``fleet-data.json`` (issue #703).

Generated from the ``FleetData`` TypedDict in ``lib/fleet/fleet_data.py`` with the
``groups.jsonl`` schema's type mapping (``lib/fleet_group_schema.type_schema``),
so the checked-in file cannot drift from the type mypy checks:
``tests/test_fleet_data.py`` fails when ``SCHEMA_PATH`` differs from ``render()``.
Regenerate after changing a ``fleet_data`` shape:

    cd skills/assess && uv run --locked python scripts/lib/fleet/fleet_data_schema.py

Generates `skills/fleet/references/fleet-data.schema.json` from the `FleetData` TypedDict
with `fleet_group_schema.type_schema`, adding the enums. `tests/test_fleet_data.py` fails
when the checked-in file differs; regenerate with
`uv run --locked python scripts/lib/fleet/fleet_data_schema.py` from `skills/assess`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ in (None, ""):  # run as a script: make ``lib`` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lib.fleet.fleet_data import FLEET_DATA_VERSION, FleetData  # noqa: E402
from lib.fleet.fleet_group_schema import type_schema  # noqa: E402
from lib.fleet.fleet_units import KINDS, VIEWS  # noqa: E402
from lib.run_context_types import JsonDict  # noqa: E402

SCHEMA_PATH = Path(__file__).resolve().parents[4] / "fleet" / "references" / "fleet-data.schema.json"
SCHEMA_ID = "https://github.com/bjcoombs/ai-native-toolkit/skills/fleet/references/fleet-data.schema.json"


def build_schema() -> JsonDict:
    schema = type_schema(FleetData)
    props = schema["properties"]
    props["fleet_data_version"] = {"const": FLEET_DATA_VERSION}
    props["model"]["items"]["properties"]["scope"] = {"enum": ["org", "team"]}
    views = props["views"]["items"]["properties"]
    views["view"] = {"enum": list(VIEWS)}
    unit = views["units"]["items"]["properties"]
    unit["view"] = {"enum": list(VIEWS)}
    unit["kind"] = {"enum": list(KINDS)}
    unit["source"] = {"enum": ["path", "github_teams", "codeowners", "mapping"]}
    repo = props["repos"]["items"]["properties"]
    repo["units"]["items"]["properties"]["view"] = {"enum": list(VIEWS)}
    repo["rows"]["items"]["properties"]["status"] = {"enum": ["level", "unknown"]}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": "fleet data bundle",
        "description": (
            "fleet-data.json written by skills/fleet/scripts/fleet.py: the run stamp, the maturity "
            "model rows, the ownership and system unit trees with rolled-up levels, and per "
            "repository its units, web URL and per-row level, value and reason. Generated from "
            "lib/fleet/fleet_data.py by lib/fleet/fleet_data_schema.py."
        ),
        **schema,
    }


def render() -> str:
    return json.dumps(build_schema(), indent=2) + "\n"


if __name__ == "__main__":
    SCHEMA_PATH.write_text(render(), encoding="utf-8", newline="\n")
    print(f"wrote {SCHEMA_PATH}")
