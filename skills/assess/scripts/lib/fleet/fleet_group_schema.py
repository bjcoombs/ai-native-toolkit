"""The published JSON schema of one fleet ``groups.jsonl`` row (issue #568).

Generated from the ``GroupRow`` TypedDict in ``lib/fleet/fleet_groups.py`` so the
checked-in file cannot drift from the type mypy checks: ``tests/test_fleet_groups.py``
fails when ``SCHEMA_PATH`` differs from ``render()``. Regenerate after
changing ``GroupRow`` or ``RowLevel``:

    cd skills/assess && uv run --locked python scripts/lib/fleet/fleet_group_schema.py

Objects are strict except ``settings``, whose keys are ghreport's setting
names: each one is ``{value, reason}`` or ``{available: false, reason}``.

Generates `skills/fleet/references/groups.schema.json` from `GroupRow` and
`RowLevel` (lists and floats included, `settings` values as `{value, reason}` or
`{available: false, reason}`; a `NotRequired` key, such as `top_level`, is left out of
`required`). `tests/test_fleet_groups.py` fails when the checked-in
file differs; regenerate with `uv run --locked python scripts/lib/fleet/fleet_group_schema.py`
from `skills/assess`.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any, Literal, NotRequired, Union, get_args, get_origin, get_type_hints, is_typeddict

if __package__ in (None, ""):  # run as a script: make ``lib`` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lib.fleet.fleet_groups import GroupRow  # noqa: E402
from lib.run_context_types import JsonDict  # noqa: E402

# skills/assess/scripts/lib/fleet -> skills/
SCHEMA_PATH = Path(__file__).resolve().parents[4] / "fleet" / "references" / "groups.schema.json"
SCHEMA_ID = "https://github.com/bjcoombs/ai-native-toolkit/skills/fleet/references/groups.schema.json"

_SCALARS: dict[Any, str] = {str: "string", int: "integer", float: "number", bool: "boolean",
                            type(None): "null"}
SETTING: JsonDict = {
    "type": "object",
    "oneOf": [
        {"required": ["value", "reason"], "properties": {"value": {}, "reason": {"type": ["string", "null"]}},
         "additionalProperties": False},
        {"required": ["available", "reason"],
         "properties": {"available": {"const": False}, "reason": {"type": "string"}},
         "additionalProperties": False},
    ],
}


def type_schema(tp: Any) -> JsonDict:
    """JSON schema for scalars, ``Literal`` tokens (an ``enum``), unions, TypedDicts,
    ``list[...]`` and ``dict[str, ...]``."""
    if tp in _SCALARS:
        return {"type": _SCALARS[tp]}
    origin = get_origin(tp)
    if origin is Literal:
        return {"enum": list(get_args(tp))}
    if origin in (Union, types.UnionType):
        members = [type_schema(arg) for arg in get_args(tp)]
        if all(set(m) == {"type"} and isinstance(m["type"], str) for m in members):
            return {"type": [m["type"] for m in members]}
        return {"anyOf": members}
    if is_typeddict(tp):
        hints = get_type_hints(tp)
        # ``__required_keys__`` misses ``NotRequired`` under postponed annotations; read the
        # marker from the resolved hints instead.
        optional = {name for name, hint in get_type_hints(tp, include_extras=True).items()
                    if get_origin(hint) is NotRequired}
        return {
            "type": "object",
            "properties": {name: type_schema(hint) for name, hint in hints.items()},
            "required": sorted(set(hints) - optional),
            "additionalProperties": False,
        }
    if origin is list:
        return {"type": "array", "items": type_schema(get_args(tp)[0])}
    if origin is dict:
        return {"type": "object", "additionalProperties": SETTING}
    raise TypeError(f"no JSON schema mapping for {tp!r}")


def build_schema() -> JsonDict:
    schema = type_schema(GroupRow)
    level = schema["properties"]["levels"]["items"]["properties"]
    level["scope"] = {"enum": ["org", "team"]}
    level["status"] = {"enum": ["level", "unknown"]}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": "fleet group row",
        "description": (
            "One line of groups.jsonl written by skills/fleet/scripts/fleet.py: a group's "
            "repository counts, distinct shared CI sources and marketplaces, joined ghreport "
            "settings and each maturity model row's whole level. Generated from lib/fleet/fleet_groups.py "
            "by lib/fleet/fleet_group_schema.py."
        ),
        **schema,
    }


def render() -> str:
    return json.dumps(build_schema(), indent=2) + "\n"


if __name__ == "__main__":
    SCHEMA_PATH.write_text(render(), encoding="utf-8", newline="\n")
    print(f"wrote {SCHEMA_PATH}")
