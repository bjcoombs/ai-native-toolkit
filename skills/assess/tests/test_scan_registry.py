"""Tests for the declared scan table and the loop that runs it."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from lib import scan_registry as reg
from lib.scan_registry import ScanRegistryError, ScanSpec

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _spec(key, fn, reads=("repo_root",), stage=reg.STAGE_READ_SIDE, **kw):
    return ScanSpec(key, fn, tuple(reads), stage, **kw)


def test_shipped_table_is_valid():
    reg.validate(reg.SCANS)


def test_raising_scan_degrades_and_the_run_continues():
    def boom(_root):
        raise RuntimeError("kaput")

    ctx: dict = {}
    specs = (_spec("first", boom), _spec("second", lambda root: {"root": root}))
    reg.run_scans(ctx, {"repo_root": "r"}, reg.STAGE_READ_SIDE, specs)

    assert ctx["first"] == {"available": False, "reason": "first scan failed: kaput"}
    assert ctx["second"] == {"root": "r"}


def test_read_of_a_missing_key_fails_at_registration():
    specs = (_spec("needs_x", lambda x: x, reads=("x",)),)
    with pytest.raises(ScanRegistryError, match=r"needs_x.*\['x'\]"):
        reg.validate(specs)


def test_a_scan_may_read_an_earlier_scans_key_but_not_a_later_one():
    a = _spec("a", lambda root: 1)
    b = _spec("b", lambda a_value: a_value + 1, reads=("a",))
    reg.validate((a, b))
    with pytest.raises(ScanRegistryError, match="'b'"):
        reg.validate((b, a))

    ctx: dict = {}
    reg.run_scans(ctx, {"repo_root": "r"}, reg.STAGE_READ_SIDE, (a, b))
    assert ctx == {"a": 1, "b": 2}


def test_read_of_a_key_produced_at_a_later_stage_is_rejected():
    late = _spec("late", lambda root: 1, stage=reg.STAGE_POST_OFFERS)
    early_reader = _spec("reader", lambda v: v, reads=("late",))
    with pytest.raises(ScanRegistryError, match="later stage"):
        reg.validate((late, early_reader))

    same_stage_reader = _spec("reader", lambda v: v, reads=("late",), stage=reg.STAGE_POST_OFFERS)
    reg.validate((late, same_stage_reader))


def test_an_input_the_core_did_not_pass_stops_the_run_instead_of_degrading():
    ctx: dict = {}
    with pytest.raises(ScanRegistryError, match=r"'a'.*'repo_root'"):
        reg.run_scans(ctx, {}, reg.STAGE_READ_SIDE, (_spec("a", lambda root: 1),))
    assert ctx == {}


def test_duplicate_key_and_shadowed_input_are_rejected():
    a = _spec("a", lambda root: 1)
    with pytest.raises(ScanRegistryError, match="declared twice"):
        reg.validate((a, a))
    with pytest.raises(ScanRegistryError, match="shadows"):
        reg.validate((_spec("repo_root", lambda root: 1),))


def test_a_callable_that_cannot_take_its_declared_reads_is_rejected():
    def two(_root, _files):
        return 1

    with pytest.raises(ScanRegistryError, match=r"'short'.*1 read"):
        reg.validate((_spec("short", two),))
    with pytest.raises(ScanRegistryError, match=r"'long'.*2 read"):
        reg.validate((_spec("long", lambda root: 1, reads=("repo_root", "instruction_files")),))

    def with_default(_root, _now=None):
        return 1

    reg.validate((_spec("ok", with_default),))


def test_unknown_stage_is_rejected():
    with pytest.raises(ScanRegistryError, match="unknown stage"):
        reg.validate((_spec("a", lambda root: 1, stage="nowhere"),))


def test_opting_out_of_degrade_needs_a_gate_reason():
    with pytest.raises(ScanRegistryError, match="gate_reason"):
        reg.validate((_spec("g", lambda root: 1, degrade=False),))

    def boom(_root):
        raise RuntimeError("stop")

    gate = _spec("g", boom, degrade=False, gate_reason="a failed gate must stop the run")
    reg.validate((gate,))
    with pytest.raises(RuntimeError, match="stop"):
        reg.run_scans({}, {"repo_root": "r"}, reg.STAGE_READ_SIDE, (gate,))


def test_only_the_named_stage_runs():
    early = _spec("early", lambda root: "e")
    late = _spec("late", lambda root: "l", stage=reg.STAGE_POST_OFFERS)
    ctx: dict = {}
    reg.run_scans(ctx, {"repo_root": "r"}, reg.STAGE_POST_OFFERS, (early, late))
    assert ctx == {"late": "l"}


def test_a_scan_is_added_without_touching_assess_core():
    """A new spec runs through the loop alone; the core names no table scan."""
    ctx: dict = {}
    reg.run_scans(ctx, {"repo_root": "r"}, reg.STAGE_READ_SIDE, (_spec("throwaway", lambda root: 7),))
    assert ctx == {"throwaway": 7}

    core = ast.parse((SCRIPTS / "assess_core.py").read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(core)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    table_fns = {spec.fn.__name__ for spec in reg.SCANS}
    assert not table_fns & imported


def test_the_core_passes_exactly_the_provided_inputs():
    """PROVIDED_INPUTS is what validate trusts; the core's literal must match it."""
    core = ast.parse((SCRIPTS / "assess_core.py").read_text(encoding="utf-8"))
    literals = [
        node.value
        for node in ast.walk(core)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "scan_inputs" for t in node.targets)
    ]
    assert len(literals) == 1 and isinstance(literals[0], ast.Dict)
    keys = {k.value for k in literals[0].keys if isinstance(k, ast.Constant)}
    assert keys == set(reg.PROVIDED_INPUTS)


def test_the_core_drives_every_stage():
    """A spec at a stage the core never runs would validate and then vanish."""
    core = ast.parse((SCRIPTS / "assess_core.py").read_text(encoding="utf-8"))
    driven = {
        getattr(reg, node.args[2].id)
        for node in ast.walk(core)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "run_scans"
        and len(node.args) >= 3
        and isinstance(node.args[2], ast.Name)
    }
    assert driven == set(reg.STAGES)


def test_every_table_scan_degrades():
    """instruction_claims ran outside the wrapper before it moved here."""
    assert all(spec.degrade for spec in reg.SCANS)
    assert "instruction_claims" in {spec.key for spec in reg.SCANS}
