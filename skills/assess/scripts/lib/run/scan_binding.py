"""Bind each manifest read to the scan parameter of the same name (#1049).

A scan's callable takes its manifest ``reads`` positionally and in order, so a
reordered ``reads`` list in ``checks/core/<key>.toml`` would hand each value to
the wrong parameter with no error. ``mismatch`` closes that: read ``i`` must
name parameter ``i`` of the callable, so the order in the manifest cannot drift
from the order in the code. A read binds to a parameter when the names are
equal, when the parameter is the read with a leading underscore (an argument
the callable takes and ignores, such as an offline stub's ``_repo_root``), or
when ``READ_ALIASES`` maps the read to the parameter's name. The alias map is
explicit and keyed by read, so every name that differs is listed in one place.

Binding stops at a ``*args`` parameter: a callable that takes its reads as a
sequence zips them with a code tuple, and ``scan_registry.pinned_reads`` checks
that order instead. A callable whose signature cannot be inspected is not
checked. ``binding_error`` words the first mismatch of a scan's callable or
offline callable, naming the scan, the position, the read and the parameter;
``scan_registry.spec_from_manifest`` raises it as ``ScanRegistryError``, so the
check runs at import for every manifest-built scan.
"""
from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping, Sequence
from typing import Any

# Read -> the parameter name a scan callable gives it, where the two differ.
READ_ALIASES: Mapping[str, str] = {
    "accretion_ratchet": "accretion",
    "accretion_scan": "scan",
    "coverage_data": "coverage",
    "coverage_report": "coverage",
    "doc_graph": "doc_graph_block",
    "doc_join": "join",
    "doc_staleness": "staleness",
    "first_flagged": "first_flagged_map",
    "gitlab_include_renames": "include_renames",
    "gitlab_merged_ci": "merged_ci",
    "gitlab_note_facts": "notes",
    "hot_test_index": "index",
    "instruction_files": "files",
    "instruction_grading": "grading",
    "keyhole": "integration",
    "level": "level_block",
    "liveness_blocks": "liveness",
    "maturity_model": "choice",
    "measured_commit": "measured",
    "merged_sample": "sample",
    "nested_instructions": "nested",
    "run_header": "header",
    "run_scope": "scope",
    "since_last_run": "since_block",
    "structure_scan": "structure",
    "test_pressure_scan": "raw",
    "unit_levels": "levels",
    "unit_readings": "readings",
    "unit_rollup": "rollup",
    "units": "units_block",
    "working_notes": "notes",
}

_POSITIONAL = (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)


def binds(read: str, param: str) -> bool:
    """True when the read ``read`` may be passed as the parameter ``param``."""
    return param in (read, f"_{read}") or READ_ALIASES.get(read) == param


def mismatch(fn: Callable[..., Any], reads: Sequence[str]) -> tuple[int, str, str] | None:
    """``(position, read, parameter)`` for the first read that does not bind to the
    parameter at its position, or None when every positional parameter matches."""
    try:
        params = list(inspect.signature(fn).parameters.values())
    except (TypeError, ValueError):
        return None
    for position, (read, param) in enumerate(zip(reads, params)):
        if param.kind not in _POSITIONAL:
            return None
        if not binds(read, param.name):
            return position, read, param.name
    return None


def binding_error(key: str, reads: Sequence[str], fn: Callable[..., Any],
                  offline_fn: Callable[..., Any] | None = None) -> str | None:
    """The error for scan ``key``'s first read that does not bind by name, or None."""
    for role, each in (("callable", fn), ("offline_fn", offline_fn)):
        bad = None if each is None else mismatch(each, reads)
        if bad is not None:
            return (f"scan {key!r} position {bad[0]}: the manifest read {bad[1]!r} does not "
                    f"bind to parameter {bad[2]!r} of its {role}")
    return None
