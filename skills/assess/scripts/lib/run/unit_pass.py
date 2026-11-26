"""One monorepo unit's scan pass as a module-level function a worker process can run (#1382).

``lib.unit_readings.read_units`` runs the scope-sensitive scans again for each unit. Those
scans are mostly pure Python (the promissory regions parse, the doc graph), so threads only
contend for the interpreter lock: on this repository's 24 units a thread fan-out made the
unit pass slower than the serial loop. Under ``--jobs`` above 1 the units' passes therefore run
in worker processes (``scan_pool.fan_out(..., processes=True)``), and a worker can only run a
function it can import by name: :func:`run_unit_pass`.

``run_unit_pass(values, scope)`` runs the bound unit table (``unit_readings.bound_table``)
through ``scan_registry.run_scans`` with the repository-wide ``values`` and the unit's ``scope`` (a one-folder ``Scope``),
and returns the context it filled plus the private values the table's entries left in the
inputs (a unit block may be a private entry's value). The registry is imported inside the call,
not at the top: the registry imports this module to bind it, and a spawned worker that unpickles
the function first imports this module, then the registry, which binds the pass. At ``--jobs`` 1
the same function runs in the calling process, one unit after another.
Tests: ``tests/test_unit_readings.py``.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from lib.run.run_scope import Scope

UnitPass = tuple[dict[str, Any], dict[str, Any]]


def run_unit_pass(values: Mapping[str, Any], scope: Scope | None) -> UnitPass:
    """The unit's run context and its table's private values (module docstring)."""
    from lib.run.scan_registry import ScanSpec, run_scans
    from lib.unit_readings import bound_table

    specs = cast("tuple[ScanSpec, ...]", bound_table()[0])  # the registry's own specs, bound by it
    inputs: dict[str, Any] = {**values, "scope": scope}
    ctx: dict[str, Any] = {}
    run_scans(ctx, inputs, None, specs)
    return ctx, {spec.key: inputs[spec.key] for spec in specs if spec.key in inputs}
