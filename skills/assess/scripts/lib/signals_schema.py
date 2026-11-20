"""The published JSON schema of the headless signals document (issue #545).

Generated from the ``SignalsDocument`` / ``SignalsStamp`` TypedDicts in
``run_context_types`` so the checked-in file cannot drift from the type mypy
checks: ``tests/test_signals.py`` fails when ``SCHEMA_PATH`` differs from
``build_schema()``. Regenerate after changing either TypedDict:

    cd skills/assess && uv run --locked python scripts/lib/signals_schema.py

The envelope is strict (every stamp field, no extra top-level keys). The
``scans`` map names only the scans the runner knows (``signals.known_scans``),
so the schema publishes the block namespace rubric signal paths are checked
against (``lib/level_signals.py``); regenerate after adding a scan. Per-scan
blocks stay open objects - each scan owns its own shape - except that a block
reporting ``"available": false`` must carry a string ``reason``.
"""
from __future__ import annotations

import json
import sys
import types
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Union, get_args, get_origin, get_type_hints, is_typeddict

if __package__ in (None, ""):  # run as a script: make ``lib`` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.run_context_types import JsonDict, SignalsDocument  # noqa: E402
from lib.scan_registry import ScanSpec  # noqa: E402
from lib.signals import SIGNAL_SCANS, known_scans  # noqa: E402

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "references" / "signals.schema.json"
SCHEMA_ID = "https://github.com/bjcoombs/ai-native-toolkit/skills/assess/references/signals.schema.json"

_SCALARS: dict[Any, str] = {str: "string", int: "integer", bool: "boolean", type(None): "null"}

# A scan block: any object, but an unavailable one names its reason.
SCAN_BLOCK: JsonDict = {
    "type": "object",
    "if": {"properties": {"available": {"const": False}}, "required": ["available"]},
    "then": {"properties": {"reason": {"type": "string"}}, "required": ["reason"]},
}


def type_schema(tp: Any) -> JsonDict:
    """JSON schema for one annotation: scalars, ``X | None``, TypedDicts, ``dict[str, ...]``."""
    if tp in _SCALARS:
        return {"type": _SCALARS[tp]}
    origin = get_origin(tp)
    if origin in (Union, types.UnionType):
        members = [type_schema(arg) for arg in get_args(tp)]
        if all(set(m) == {"type"} and isinstance(m["type"], str) for m in members):
            return {"type": [m["type"] for m in members]}
        return {"anyOf": members}
    if is_typeddict(tp):
        hints = get_type_hints(tp)
        return {
            "type": "object",
            "properties": {name: type_schema(hint) for name, hint in hints.items()},
            "required": sorted(tp.__required_keys__),
            "additionalProperties": False,
        }
    if origin is dict:
        return {"type": "object", "additionalProperties": SCAN_BLOCK}
    raise TypeError(f"no JSON schema mapping for {tp!r}")


def build_schema(specs: Sequence[ScanSpec] = SIGNAL_SCANS) -> JsonDict:
    """The full document schema, ready to serialise (``specs``: the scan table)."""
    document = type_schema(SignalsDocument)
    # Sorted, so a scan added anywhere in the registry adds one line here.
    document["properties"]["scans"]["propertyNames"] = {"enum": sorted(known_scans(specs))}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": "assess signals document",
        "description": (
            "Output of skills/assess/scripts/assess_signals.py: a run stamp plus one block "
            "per scan. Generated from lib/run_context_types.py by lib/signals_schema.py."
        ),
        **document,
    }


def render() -> str:
    return json.dumps(build_schema(), indent=2) + "\n"


if __name__ == "__main__":
    SCHEMA_PATH.write_text(render(), encoding="utf-8")
    print(f"wrote {SCHEMA_PATH}")
