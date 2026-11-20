"""The published JSON schema of one fleet ``repos.jsonl`` row (issue #567).

Generated from the ``FleetRow`` TypedDict in ``lib/fleet.py`` with the same
mapping ``signals_schema`` uses, so the checked-in file cannot drift from the
type mypy checks: ``tests/test_fleet.py`` fails when ``SCHEMA_PATH`` differs
from ``render()``. Regenerate after changing ``FleetRow``:

    cd skills/assess && uv run --locked python scripts/lib/fleet_schema.py

The envelope is strict. ``scans`` holds the ``assess_signals`` blocks
(``skills/assess/references/signals.schema.json``), so a block reporting
``"available": false`` carries a ``reason``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ in (None, ""):  # run as a script: make ``lib`` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.fleet import FAILURE_REASONS, STATUS_FAILED, STATUS_OK, FleetRow  # noqa: E402
from lib.run_context_types import JsonDict  # noqa: E402
from lib.signals_schema import type_schema  # noqa: E402

# skills/assess/scripts/lib -> skills/
SCHEMA_PATH = Path(__file__).resolve().parents[3] / "fleet" / "references" / "repos.schema.json"
SCHEMA_ID = "https://github.com/bjcoombs/ai-native-toolkit/skills/fleet/references/repos.schema.json"
REASON_PATTERN = "^(" + "|".join(FAILURE_REASONS) + r"|scan_exit_-?[0-9]+)$"


def build_schema() -> JsonDict:
    """The row schema, ready to serialise."""
    schema = type_schema(FleetRow)
    props = schema["properties"]
    props["status"] = {"enum": [STATUS_OK, STATUS_FAILED]}
    props["reason"] = {"anyOf": [{"type": "string", "pattern": REASON_PATTERN}, {"type": "null"}]}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": "fleet repository row",
        "description": (
            "One line of repos.jsonl written by skills/fleet/scripts/fleet.py. A failed row "
            "has a reason token and null signals_schema_version and scans. Generated from "
            "lib/fleet.py by lib/fleet_schema.py."
        ),
        **schema,
        "allOf": [
            {
                "if": {"properties": {"status": {"const": STATUS_OK}}},
                "then": {"properties": {"reason": {"type": "null"}, "scans": {"type": "object"},
                                        "signals_schema_version": {"type": "integer"}}},
                "else": {"properties": {"reason": {"type": "string"}, "scans": {"type": "null"},
                                        "signals_schema_version": {"type": "null"}}},
            },
        ],
    }


def render() -> str:
    return json.dumps(build_schema(), indent=2) + "\n"


if __name__ == "__main__":
    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(render(), encoding="utf-8")
    print(f"wrote {SCHEMA_PATH}")
