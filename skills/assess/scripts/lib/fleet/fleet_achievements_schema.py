"""The published JSON schema of ``achievements.json`` (issue #704).

Generated from the ``Achievements`` TypedDict in ``lib/fleet/fleet_achievements.py`` with the
``groups.jsonl`` schema's type mapping (``fleet_group_schema.type_schema``), so the
checked-in file cannot drift from the type mypy checks: ``tests/test_fleet_achievements.py``
fails when ``SCHEMA_PATH`` differs from ``render()``. Regenerate after changing a shape:

    cd skills/assess && uv run --locked python scripts/lib/fleet/fleet_achievements_schema.py

Generates `skills/fleet/references/achievements.schema.json` from the `Achievements`
TypedDict with `fleet_group_schema.type_schema`. `tests/test_fleet_achievements.py` fails
when the checked-in file differs; regenerate with
`uv run --locked python scripts/lib/fleet/fleet_achievements_schema.py` from `skills/assess`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ in (None, ""):  # run as a script: make ``lib`` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lib.fleet.fleet_achievements import (
    ACHIEVEMENTS_VERSION,
    KINDS,
    NEAREST_LEVEL_UP,
    REPO_VIEW,
    Achievements,
)
from lib.fleet.fleet_data import FLEET_DATA_VERSION
from lib.fleet.fleet_group_schema import type_schema
from lib.fleet.fleet_rescore import DIRECTIONS, RULES
from lib.fleet.fleet_units import KINDS as UNIT_KINDS, VIEWS
from lib.run_context_types import JsonDict

SCHEMA_PATH = Path(__file__).resolve().parents[4] / "fleet" / "references" / "achievements.schema.json"
SCHEMA_ID = "https://github.com/bjcoombs/ai-native-toolkit/skills/fleet/references/achievements.schema.json"


def build_schema() -> JsonDict:
    schema = type_schema(Achievements)
    props = schema["properties"]
    props["achievements_version"] = {"const": ACHIEVEMENTS_VERSION}
    props["fleet_data_version"] = {"const": FLEET_DATA_VERSION}
    views = {"enum": [*VIEWS, REPO_VIEW]}
    unit_kinds = {"enum": [*UNIT_KINDS, "repo"]}
    earned = props["achievements"]["items"]["properties"]
    earned["kind"] = {"enum": list(KINDS)}
    earned["view"], earned["unit_kind"] = views, unit_kinds
    step = props["nearest_level_ups"]["items"]["properties"]
    step["kind"] = {"const": NEAREST_LEVEL_UP}
    step["view"], step["unit_kind"] = views, unit_kinds
    props["targets"]["items"]["properties"]["view"] = views
    recount = next(branch for branch in props["recount"]["anyOf"] if branch.get("type") == "object")
    line = recount["properties"]["lines"]["items"]["properties"]
    line["direction"] = {"enum": list(DIRECTIONS)}
    line["attributed_to"] = {"const": RULES}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": "fleet achievements",
        "description": (
            "achievements.json written by skills/fleet/scripts/fleet.py: what each unit and repository "
            "earned over the comparable runs in the history (level-ups, firsts, streaks, Step "
            "targets hit), each with its evidence and a credit line, plus each one's nearest "
            "level-up and each Step target's progress (lowest Step, missing gates), and the Recount of a maturity-model change. Generated from lib/fleet/fleet_achievements.py by "
            "lib/fleet/fleet_achievements_schema.py."
        ),
        **schema,
    }


def render() -> str:
    return json.dumps(build_schema(), indent=2) + "\n"


if __name__ == "__main__":
    SCHEMA_PATH.write_text(render(), encoding="utf-8", newline="\n")
    print(f"wrote {SCHEMA_PATH}")
