"""The published JSON schema of the headless signals document (issue #545).

Generated from the ``SignalsDocument`` / ``SignalsStamp`` TypedDicts in
``run_context_types`` so the checked-in file cannot drift from the type mypy
checks: ``tests/test_signals.py`` fails when ``SCHEMA_PATH`` differs from
``build_schema()``. Regenerate after changing either TypedDict:

    cd skills/assess && uv run --locked python scripts/lib/signals_schema.py

The envelope is strict (every stamp field, no extra top-level keys). The
``scans`` map names only the scans the runner knows (``signals.known_scans``),
so the schema publishes the block namespace maturity model signal paths are checked
against (``lib/level_signals.py``); regenerate after adding a scan. Per-scan
blocks stay open objects - each scan owns its own shape - except that a block
reporting ``"available": false`` must carry a string ``reason``. The blocks in
``lib/scan_block_schemas.py`` (``ai_usage``, ``ci_checks``, ``delivery``; #779)
also name and describe their fields. Each named block's ``reason`` description is
generated from the check manifest (``reason_description``, #795): the codes its
collector declares and how counts-only output keeps it, so the ``structure`` codes
are described under ``structure`` only.

Generates the published `references/signals.schema.json` from the `SignalsDocument` and
`SignalsStamp` TypedDicts in `run_context_types.py`: a strict envelope, a `scans` map
whose names are limited to `signals.known_scans` (sorted; `build_schema(specs)` for a
custom table, and the namespace `level_signals.py` checks paths against), open per-scan
blocks, and an `if`/`then` rule that an `"available": false` block carries a string
`reason`. The `ai_usage`, `ci_checks`, `coverage_gate`, `delivery`, `guardrails` and `agent_consistency` blocks (`scan_block_schemas.py`, #779, #781, #767, #768)
also name and describe their fields (`described_block`), loosely: no required field, and a
string list may be its counts-only length. Each block's `reason` description is generated
from the check manifest (`reason_description`, #795): the codes its collector declares in
`unknown_reasons` (carried in `reason`, for that block) with what each means, then
`REASON_KEPT`: a counts-only signals document keeps an unavailable block's reason as its
leading code, or `unavailable` (`counts_only.reason_code`), for every block. `described_blocks`
adds every known block that declares codes (`structure`), so the structure codes
(`no_grimp` and the rest) are described under `structure` only and the shared text of
every other block names no code. `fleet_schema` uses the same blocks for `repos.schema.json`.
"""
from __future__ import annotations

import json
import sys
import types
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Union, get_args, get_origin, get_type_hints, is_typeddict

if __package__ in (None, ""):  # run as a script: make ``lib`` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.manifest.check_manifest import core_manifest, reason_codes  # noqa: E402
from lib.run_context_types import JsonDict, SignalsDocument  # noqa: E402
from lib.scan_block_schemas import BLOCK_SCHEMAS  # noqa: E402
from lib.scan_registry import ScanSpec  # noqa: E402
from lib.signals import SIGNAL_SCANS, known_scans  # noqa: E402

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "references" / "signals.schema.json"
SCHEMA_ID = "https://github.com/bjcoombs/ai-native-toolkit/skills/assess/references/signals.schema.json"

_SCALARS: dict[Any, str] = {str: "string", int: "integer", bool: "boolean", type(None): "null"}

# How a counts-only document keeps an unavailable block's ``reason``: every block, as
# ``counts_only.counts_only_scans`` reduces it with ``reason_code`` (the leading code
# before a colon, else ``unavailable``); the sentence never survives.
REASON_KEPT = ("A counts-only document keeps only the leading code, or unavailable when the "
               "reason has no code.")


def reason_description(block: str | None = None) -> str:
    """The ``reason`` description of one block, generated from the check manifest (#795).

    A block whose collector declares reason codes (``unknown_reasons`` carried in
    ``reason``) lists them with what each means; ``None`` is the shared text of every
    other block. So one block's codes never appear under another block. Every text ends
    with :data:`REASON_KEPT`: the signals document's counts-only reduction is the same for
    every block, whatever the run-context privacy class of ``<block>.reason``.
    """
    if block is None:
        return ("Why the block is unavailable, usually 'code: sentence'; only blocks with a "
                f"named entry list their codes. {REASON_KEPT}")
    codes = reason_codes(core_manifest(), block)
    text = ("'code: sentence'. The codes name the missing piece: "
            + "; ".join(f"{r['code']} ({r['means'][:1].lower()}{r['means'][1:].rstrip('.')})" for r in codes) + "."
            if codes else "Why the block is unavailable.")
    return f"{text} {REASON_KEPT}"


def scan_block(block: str | None = None) -> JsonDict:
    """A scan block: any object, but an unavailable one names its reason."""
    return {
        "type": "object",
        "if": {"properties": {"available": {"const": False}}, "required": ["available"]},
        "then": {"properties": {"reason": {
            "type": "string", "description": reason_description(block),
        }}, "required": ["reason"]},
    }


SCAN_BLOCK: JsonDict = scan_block()


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


def described_block(name: str) -> JsonDict:
    """The block's reason rule plus its described fields (``lib/scan_block_schemas.py``)."""
    return {**scan_block(name), **BLOCK_SCHEMAS.get(name, {})}


def described_blocks(known: Iterable[str]) -> dict[str, JsonDict]:
    """The blocks a published schema names: every ``BLOCK_SCHEMAS`` block, then each known
    block whose collector declares reason codes (``structure``), with its own reason text."""
    known = set(known)
    coded = sorted(b for b in known - set(BLOCK_SCHEMAS) if reason_codes(core_manifest(), b))
    return {name: described_block(name) for name in [*BLOCK_SCHEMAS, *coded] if name in known}


def build_schema(specs: Sequence[ScanSpec] = SIGNAL_SCANS) -> JsonDict:
    """The full document schema, ready to serialise (``specs``: the scan table)."""
    document = type_schema(SignalsDocument)
    # Sorted, so a scan added anywhere in the registry adds one line here.
    known = sorted(known_scans(specs))
    scans = document["properties"]["scans"]
    scans["propertyNames"] = {"enum": known}
    # The blocks a fleet reader compares (#779) name their fields; each keeps the reason rule.
    scans["properties"] = described_blocks(known)
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
