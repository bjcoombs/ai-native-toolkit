"""The published JSON schemas of a fleet ``repos.jsonl`` row (#567) and of ``run.json`` (#569).

Generated from the ``FleetRow`` TypedDict in ``lib/fleet/runner.py`` with the same
mapping ``signals_schema`` uses, so the checked-in file cannot drift from the
type mypy checks: ``tests/test_fleet.py`` fails when ``SCHEMA_PATH`` differs
from ``render()`` (``tests/test_fleet_run_stamp.py`` likewise for
``STAMP_SCHEMA_PATH``, built from ``FleetStamp`` in ``lib/fleet/fleet_stamp.py``).
``FRESHNESS_SCHEMA_PATH`` is built from ``FreshnessRow`` in
``lib/fleet/fleet_doc_freshness.py`` (``tests/test_fleet_doc_freshness.py``).
Regenerate all three after changing any of the types:

    cd skills/assess && uv run --locked python scripts/lib/fleet/fleet_schema.py

The envelope is strict. ``scans`` holds the ``assess_signals`` blocks
(``skills/assess/references/signals.schema.json``), so a block reporting
``"available": false`` carries a ``reason``. ``review_automation`` and the blocks in
``lib/scan_block_schemas.py`` (``ai_usage``, ``ci_checks``, ``delivery``) are described
field by field, and ``structure`` names its reason codes (``signals_schema.described_blocks``).

Generates `skills/fleet/references/repos.schema.json` from `FleetRow` through
`signals_schema.type_schema` (with `review_automation` and the `scan_block_schemas` blocks
described under `scans`), with a `status` enum, a reason-token pattern and an
`if`/`then` tying `ok` to non-null `scans`; and `run.schema.json` from `FleetStamp`
(`build_stamp_schema`, mapping the `scans` name list itself); and
`doc_freshness.schema.json` from `fleet_doc_freshness.FreshnessRow`
(`build_freshness_schema`, status and reason enums, `scope`/`commits` lists mapped itself). `tests/test_fleet.py` and
`tests/test_fleet_run_stamp.py` fail when a checked-in file differs; regenerate both with
`uv run --locked python scripts/lib/fleet/fleet_schema.py` from `skills/assess`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import get_type_hints

if __package__ in (None, ""):  # run as a script: make ``lib`` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lib.fleet.runner import (  # noqa: E402
    DIRTY_REASONS, DIRTY_UNKNOWN_REASONS, FAILURE_REASONS, STATUS_FAILED, STATUS_OK, SUBMODULE_DIRTY, FleetRow,
)
from lib.fleet.fleet_doc_freshness import (  # noqa: E402
    METHOD,
    REASONS,
    STATUSES,
    SUBJECTS_CAP,
    CommitRef,
    FreshnessRow,
)
from lib.fleet.fleet_method import CollectorRevision  # noqa: E402
from lib.fleet.fleet_stamp import FleetStamp  # noqa: E402
from lib.history_window import WINDOW_PATTERN  # noqa: E402
from lib.review_automation import KINDS  # noqa: E402
from lib.run_context_types import JsonDict  # noqa: E402
from lib.signals import known_scans  # noqa: E402
from lib.signals_schema import described_blocks, type_schema  # noqa: E402

# skills/assess/scripts/lib/fleet -> skills/
SCHEMA_PATH = Path(__file__).resolve().parents[4] / "fleet" / "references" / "repos.schema.json"
SCHEMA_ID = "https://github.com/bjcoombs/ai-native-toolkit/skills/fleet/references/repos.schema.json"
STAMP_SCHEMA_PATH = SCHEMA_PATH.with_name("run.schema.json")
STAMP_SCHEMA_ID = "https://github.com/bjcoombs/ai-native-toolkit/skills/fleet/references/run.schema.json"
FRESHNESS_SCHEMA_PATH = SCHEMA_PATH.with_name("doc_freshness.schema.json")
FRESHNESS_SCHEMA_ID = ("https://github.com/bjcoombs/ai-native-toolkit/skills/fleet/references/"
                       "doc_freshness.schema.json")
REASON_PATTERN = "^(" + "|".join(FAILURE_REASONS) + r"|scan_exit_-?[0-9]+)$"


def build_schema() -> JsonDict:
    """The row schema, ready to serialise."""
    schema = type_schema(FleetRow)
    props = schema["properties"]
    props["status"] = {"enum": [STATUS_OK, STATUS_FAILED]}
    props["reason"] = {"anyOf": [{"type": "string", "pattern": REASON_PATTERN}, {"type": "null"}]}
    props["dirty_reason"] = {"anyOf": [{"enum": list(DIRTY_REASONS)}, {"type": "null"}]}
    # The repository's "AI review in CI: yes / no" (#696), described where a reader looks first.
    scans = next(branch for branch in props["scans"]["anyOf"] if branch.get("type") == "object")
    scans["properties"] = {"review_automation": {
        "type": "object",
        "description": ("AI review in CI (lib/review_automation.py): present is true when a "
                        "git-tracked CI step, GitLab component or include, or AI review app config "
                        "runs an AI review; kinds names which (ci_action, ci_component, config_file)."),
        "properties": {"present": {"type": "boolean"},
                       "kinds": {"type": "array", "items": {"enum": list(KINDS)}}},
    }, **described_blocks(known_scans())}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": "fleet repository row",
        "description": (
            "One line of repos.jsonl written by skills/fleet/scripts/fleet.py. A failed row "
            "has a reason token and null signals_schema_version, scans, active, head_sha, dirty and "
            "dirty_reason. An ok "
            "row's active says whether its HEAD was committed inside the run's window; head_sha is the "
            "HEAD it measured and dirty says whether tracked files differed from it, in which case HEAD's "
            "committed tree was scanned (untracked files, .assess/ outputs and a submodule's own edits never "
            "count). dirty is null on an ok row only when the state is unknown, and dirty_reason then says why: "
            "no_commit (scanned in place), status_error, index_read_error, sparse_checkout, skip_worktree or "
            "assume_unchanged (git status never compares those entries); with a HEAD, an unknown state is "
            "scanned from the committed tree like a dirty one. A clean row's dirty_reason is submodule_dirty "
            "when a submodule held uncommitted tracked edits: the checkout was scanned in place, those edits "
            "included. Generated from "
            "lib/fleet/runner.py by lib/fleet/fleet_schema.py."
        ),
        **schema,
        "allOf": [
            {
                "if": {"properties": {"status": {"const": STATUS_OK}}},
                "then": {"properties": {"reason": {"type": "null"}, "scans": {"type": "object"},
                                        "signals_schema_version": {"type": "integer"}}},
                "else": {"properties": {"reason": {"type": "string"}, "scans": {"type": "null"},
                                        "signals_schema_version": {"type": "null"},
                                        "active": {"type": "null"}, "head_sha": {"type": "null"},
                                        "dirty": {"type": "null"}, "dirty_reason": {"type": "null"}}},
            },
            # The reason pairs with dirty (#1017): an unknown reason only on a null dirty,
            # submodule_dirty only on a clean one.
            {
                "if": {"properties": {"dirty_reason": {"enum": list(DIRTY_UNKNOWN_REASONS)}},
                       "required": ["dirty_reason"]},
                "then": {"properties": {"dirty": {"type": "null"}}},
            },
            {
                "if": {"properties": {"dirty_reason": {"const": SUBMODULE_DIRTY}}, "required": ["dirty_reason"]},
                "then": {"properties": {"dirty": {"const": False}}},
            },
        ],
    }


def build_stamp_schema() -> JsonDict:
    """The ``run.json`` schema. ``scans`` (a name list) and ``collectors`` (#952) are mapped here;
    ``type_schema`` has no list mapping."""
    hints = get_type_hints(FleetStamp)
    lists = ("scans", "collectors")
    props: JsonDict = {name: type_schema(hint) for name, hint in hints.items() if name not in lists}
    props["scans"] = {"anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "null"}]}
    props["collectors"] = {"anyOf": [{"type": "array", "items": type_schema(CollectorRevision)}, {"type": "null"}]}
    props["scan_date"] = {"type": "string", "pattern": r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"}
    props["window"] = {"type": "string", "pattern": f"^{WINDOW_PATTERN}$"}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": STAMP_SCHEMA_ID,
        "title": "fleet run stamp",
        "description": (
            "run.json written by skills/fleet/scripts/fleet.py beside repos.jsonl: what produced "
            "the run and its repository counts. Generated from lib/fleet/fleet_stamp.py by lib/fleet/fleet_schema.py."
        ),
        "type": "object",
        "properties": {name: props[name] for name in hints},
        "required": sorted(FleetStamp.__required_keys__),
        "additionalProperties": False,
    }


def build_freshness_schema() -> JsonDict:
    """The ``doc_freshness.jsonl`` row schema (#664), from ``FreshnessRow``."""
    hints = get_type_hints(FreshnessRow)
    lists = ("scope", "commits")  # ``type_schema`` has no list mapping
    props: JsonDict = {name: type_schema(hint) for name, hint in hints.items() if name not in lists}
    props["scope"] = {"type": "array", "items": {"type": "string"}}
    props["commits"] = {"anyOf": [{"type": "array", "maxItems": SUBJECTS_CAP,
                                   "items": type_schema(CommitRef)}, {"type": "null"}]}
    props["method"] = {"const": METHOD}
    props["status"] = {"enum": list(STATUSES)}
    props["reason"] = {"enum": [*REASONS, None]}
    props["confidence"] = {"enum": ["high", "low", None]}
    props["doc_last_change"] = {"anyOf": [
        {"type": "string", "pattern": r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"}, {"type": "null"}]}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": FRESHNESS_SCHEMA_ID,
        "title": "fleet cross-repo doc freshness row",
        "description": (
            "One line of doc_freshness.jsonl written by skills/fleet/scripts/fleet.py: one declared "
            "[[describes]] mapping. A measured row (current, minor_drift, behind) has a null reason, "
            "a confidence and a count; any other status has a reason token. commits is null in "
            "counts-only output. Generated from lib/fleet/fleet_doc_freshness.py by lib/fleet/fleet_schema.py."
        ),
        "type": "object",
        "properties": {name: props[name] for name in hints},
        "required": sorted(FreshnessRow.__required_keys__),
        "additionalProperties": False,
    }


def render_freshness() -> str:
    return json.dumps(build_freshness_schema(), indent=2) + "\n"


def render() -> str:
    return json.dumps(build_schema(), indent=2) + "\n"


def render_stamp() -> str:
    return json.dumps(build_stamp_schema(), indent=2) + "\n"


if __name__ == "__main__":
    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    for path, text in ((SCHEMA_PATH, render()), (STAMP_SCHEMA_PATH, render_stamp()),
                       (FRESHNESS_SCHEMA_PATH, render_freshness())):
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {path}")
