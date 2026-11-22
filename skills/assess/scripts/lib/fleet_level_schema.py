"""The published JSON schema of ``level.json`` (#744).

Generated from ``FleetLevels`` (``lib/fleet_level.py``) and ``LevelResult``
(``lib/level_result_types.py``) with the ``groups.jsonl`` schema's type mapping
(``lib/fleet_group_schema.type_schema``). ``$defs.level_result`` is also the shape of
the ``/assess`` run context's ``level`` block. ``tests/test_level_tally.py`` fails
when ``SCHEMA_PATH`` differs from ``render()``. Regenerate after changing a shape:

    cd skills/assess && uv run --locked python scripts/lib/fleet_level_schema.py

Generates `skills/fleet/references/level.schema.json` from `FleetLevels` and `LevelResult`
(`fleet_group_schema.type_schema`); `tests/test_level_tally.py` fails when the checked-in
file drifts. Regenerate: `cd skills/assess && uv run --locked python scripts/lib/fleet_level_schema.py`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ in (None, ""):  # run as a script: make ``lib`` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.fleet_group_schema import type_schema  # noqa: E402
from lib.fleet import RepoCommit  # noqa: E402
from lib.fleet_level import FleetLevels  # noqa: E402
from lib.level_result_types import LevelResult  # noqa: E402
from lib.level_tally import COUNTED_GRADES, EXCLUDED_BY_OWNER, LEVEL_RESULT_VERSION, STATES  # noqa: E402
from lib.owner_exclusions import KINDS, SOURCE_REPO  # noqa: E402
from lib.level_signals import INPUT_GRADES  # noqa: E402
from lib.run_context_types import JsonDict  # noqa: E402

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "fleet" / "references" / "level.schema.json"
SCHEMA_ID = "https://github.com/bjcoombs/ai-native-toolkit/skills/fleet/references/level.schema.json"


def level_result_schema() -> JsonDict:
    schema = type_schema(LevelResult)
    props = schema["properties"]
    props["level_result_version"] = {"const": LEVEL_RESULT_VERSION}
    condition = props["conditions"]["items"]["properties"]
    condition["state"] = {"enum": list(STATES)}
    grades = sorted(set(INPUT_GRADES.values()), key=list(INPUT_GRADES.values()).index)
    condition["grade"] = {"enum": [*grades, None]}
    condition["counted"]["description"] = (
        f"true when the grade is one of {', '.join(sorted(COUNTED_GRADES))} and the owner did not exclude it")
    excluded = props["excluded"]["items"]["properties"]
    condition["exclusion_kind"] = excluded["kind"] = {"enum": list(KINDS)}
    condition["disposition"] = {"enum": [EXCLUDED_BY_OWNER]}
    excluded["source"] = {"enum": [SOURCE_REPO]}
    blocker = next(b for b in props["next_step"]["anyOf"] if b.get("type") == "object")
    blocker_fields = blocker["properties"]["blockers"]["items"]["properties"]
    blocker_fields["kind"] = {"enum": ["gate", "threshold"]}
    blocker_fields["disposition"] = {"enum": [EXCLUDED_BY_OWNER]}
    blocker_fields["exclusion_kind"] = {"enum": list(KINDS)}
    return schema


def build_schema() -> JsonDict:
    schema = type_schema(FleetLevels)
    schema["properties"]["level_result_version"] = {"const": LEVEL_RESULT_VERSION}
    schema["properties"]["repos"] = {"type": "object", "additionalProperties": {"$ref": "#/$defs/level_result"}}
    commit = type_schema(RepoCommit)
    commit["properties"]["head_sha"]["description"] = "the HEAD commit measured; null when the checkout has none"
    commit["properties"]["dirty"]["description"] = (
        "true when tracked files differed from HEAD (untracked files and .assess/ outputs are left out, "
        ".assess/config.toml is not); the result is then scored from HEAD's committed tree")
    schema["properties"]["commits"] = {"type": "object", "additionalProperties": commit,
                                       "description": "the commit each repos entry measured, under the same ids"}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": "fleet level results",
        "description": (
            "level.json written by skills/fleet/scripts/fleet.py --levels: each scanned repository's "
            "conditions with their state and grade, the verified tally (counted conditions met, of those "
            "measured, and the conditions not measured with a reason) and its autonomy Step with what blocks "
            "the next, and under commits the HEAD sha it measured and whether the checkout was dirty. $defs.level_result is also the /assess run context's level block. Generated from "
            "lib/fleet_level.py and lib/level_result_types.py by lib/fleet_level_schema.py."
        ),
        **schema,
        "$defs": {"level_result": level_result_schema()},
    }


def render() -> str:
    return json.dumps(build_schema(), indent=2) + "\n"


if __name__ == "__main__":
    SCHEMA_PATH.write_text(render(), encoding="utf-8", newline="\n")
    print(f"wrote {SCHEMA_PATH}")
