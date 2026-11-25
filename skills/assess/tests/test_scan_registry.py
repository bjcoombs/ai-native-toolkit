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
        reg.validate((_spec("long", lambda root: 1, reads=("repo_root", "run_date")),))

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
        if (isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "scan_inputs" for t in node.targets))
        or (isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name) and node.target.id == "scan_inputs")
    ]
    assert len(literals) == 1 and isinstance(literals[0], ast.Dict)
    keys = {k.value for k in literals[0].keys if isinstance(k, ast.Constant)}
    assert keys == set(reg.PROVIDED_INPUTS)


def test_the_core_runs_the_whole_table_in_one_call():
    """One run_scans call with no stage: every stage runs, so a spec cannot
    validate and then vanish (#558)."""
    core = ast.parse((SCRIPTS / "assess_core.py").read_text(encoding="utf-8"))
    calls = [
        node for node in ast.walk(core)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "run_scans"
    ]
    assert len(calls) == 1
    assert len(calls[0].args) == 2 and not calls[0].keywords


def test_a_stageless_run_runs_every_stage_in_stage_order():
    seen: list[str] = []
    specs = tuple(
        _spec(stage, lambda root, s=stage: seen.append(s), stage=stage)
        for stage in reversed(reg.STAGES)
    )
    reg.validate(specs)
    reg.run_scans({}, {"repo_root": "r"}, specs=specs)
    assert seen == list(reg.STAGES)


def test_every_scan_that_degraded_before_the_migration_still_does():
    """instruction_claims ran outside the wrapper before it moved here; the
    scans _safe wrapped in build_run_context keep their degrade and label."""
    by_key = {spec.key: spec for spec in reg.SCANS}
    for key in (
        "agent_ops", "config_drift", "review_reality", "gate_cost_estimate",
        "coverage_gate", "instruction_claims", "claude_config", "nested_instructions",
        "agent_harness", "agent_environment", "doc_graph", "archetype", "liveness",
        "structure_scan", "test_pressure_scan", "promissory",
    ):
        assert by_key[key].degrade, key
    labels = {key: by_key[key].label or key for key in ("liveness", "structure_scan",
                                                         "test_pressure_scan", "promissory")}
    assert labels == {"liveness": "liveness", "structure_scan": "structure",
                      "test_pressure_scan": "test_pressure", "promissory": "promissory_markers"}


def test_every_gate_names_its_reason():
    assert all(spec.degrade or spec.gate_reason for spec in reg.SCANS)


# -- private and spread entries -------------------------------------------------

def test_a_private_value_is_readable_but_never_published():
    specs = (
        _spec("raw", lambda root: {"n": 1}, private=True),
        _spec("block", lambda raw: raw["n"] + 1, reads=("raw",)),
    )
    reg.validate(specs)
    ctx: dict = {}
    inputs = {"repo_root": "r"}
    reg.run_scans(ctx, inputs, reg.STAGE_READ_SIDE, specs)
    assert ctx == {"block": 2}
    assert inputs["raw"] == {"n": 1}


def test_a_spread_entry_copies_its_keys_in_returned_order():
    spec = _spec("group", lambda root: {"b": 2, "a": 1}, spread=("a", "b", "optional"))
    reg.validate((spec,))
    ctx: dict = {"first": 0}
    inputs = {"repo_root": "r"}
    reg.run_scans(ctx, inputs, reg.STAGE_READ_SIDE, (spec,))
    assert list(ctx.items()) == [("first", 0), ("b", 2), ("a", 1)]
    assert inputs["group"] == {"b": 2, "a": 1}


def test_a_spread_entry_may_not_return_an_undeclared_key_or_a_non_mapping():
    with pytest.raises(ScanRegistryError, match=r"undeclared key\(s\) \['c'\]"):
        reg.run_scans({}, {"repo_root": "r"}, reg.STAGE_READ_SIDE,
                      (_spec("g", lambda root: {"c": 1}, spread=("a",)),))
    with pytest.raises(ScanRegistryError, match="not a mapping"):
        reg.run_scans({}, {"repo_root": "r"}, reg.STAGE_READ_SIDE,
                      (_spec("g", lambda root: [1], spread=("a",)),))


def test_a_degraded_spread_entry_marks_every_declared_key():
    def boom(_root):
        raise RuntimeError("kaput")

    ctx: dict = {}
    reg.run_scans(ctx, {"repo_root": "r"}, reg.STAGE_READ_SIDE,
                  (_spec("g", boom, spread=("a", "b"), label="grouped"),))
    marker = {"available": False, "reason": "grouped scan failed: kaput"}
    assert ctx == {"a": marker, "b": marker}


def test_a_private_spread_or_clashing_spread_key_is_rejected():
    with pytest.raises(ScanRegistryError, match="both private and spread"):
        reg.validate((_spec("g", lambda root: {}, private=True, spread=("a",)),))
    with pytest.raises(ScanRegistryError, match=r"\['a'\] written elsewhere"):
        reg.validate((_spec("a", lambda root: 1), _spec("g", lambda root: {}, spread=("a",))))
    # A spread key may share a name with an input: the run header publishes
    # the relative `scope` while scans read the absolute one.
    reg.validate((_spec("g", lambda root: {}, spread=("repo_root",)),))


def test_the_core_assigns_no_run_context_key():
    """Every block is a table entry (#558): build_run_context assigns nothing."""
    core = ast.parse((SCRIPTS / "assess_core.py").read_text(encoding="utf-8"))
    build = next(n for n in ast.walk(core)
                 if isinstance(n, ast.FunctionDef) and n.name == "build_run_context")
    assigned = [
        target for node in ast.walk(build) if isinstance(node, ast.Assign)
        for target in node.targets if isinstance(target, ast.Subscript)
    ]
    assert assigned == []


def test_the_doc_staleness_keys_are_table_entries_in_their_old_order():
    """doc_staleness, stale_hubs and churn_degenerate follow doc_graph, as they
    did when the core assigned them by hand; the doc join feeds the keyhole."""
    keys = [spec.key for spec in reg.SCANS]
    at = keys.index("doc_graph")
    assert keys[at + 1:at + 4] == ["doc_staleness", "stale_hubs", "churn_degenerate"]
    by_key = {spec.key: spec for spec in reg.SCANS}
    assert by_key["doc_staleness"].degrade and not by_key["doc_staleness"].label
    assert by_key["doc_join"].private and by_key["doc_join"].degrade
    assert keys.index("doc_join") < keys.index("keyhole")
    assert "doc_join" in by_key["keyhole"].reads


def test_the_run_context_carries_only_declared_keys_in_table_order(tmp_path):
    """No private value leaks into run-context.json, and the key order is the
    table's, stage by stage."""
    from assess_core import build_and_publish_run
    from assess_core_helpers import _minimal_repo

    ctx = build_and_publish_run(repo_root=_minimal_repo(tmp_path), run_date="2026-11-20",
                            non_interactive=True)
    declared: list[str] = []
    for stage in reg.STAGES:
        for spec in reg.SCANS:
            if spec.stage == stage and not spec.private:
                declared.extend(spec.spread or (spec.key,))
    assert list(ctx) == [key for key in declared if key in ctx]
    spread_keys = {key for spec in reg.SCANS for key in spec.spread}
    unpublished = {spec.key for spec in reg.SCANS if spec.private or spec.spread} - spread_keys
    assert not unpublished & set(ctx)


def test_network_defaults_off_so_positional_entries_stay_compatible():
    spec = _spec("plain", lambda root: 1)
    assert spec.network is False and spec.offline_fn is None


def test_offline_fn_requires_a_network_scan_and_matching_reads():
    with pytest.raises(ScanRegistryError, match="not a network scan"):
        reg.validate((_spec("x", lambda root: 1, offline_fn=lambda root: 2),))
    with pytest.raises(ScanRegistryError, match="offline_fn"):
        reg.validate((_spec("x", lambda root: 1, network=True, offline_fn=lambda: 2),))
    reg.validate((_spec("x", lambda root: 1, network=True, offline_fn=lambda root: 2),))


def test_a_raising_doc_staleness_scan_degrades_its_keys_and_the_run_continues(
    tmp_path, monkeypatch,
):
    """doc_staleness ran through ``safe`` when hand-wired; as a table entry it
    still degrades under its own label, and its readers see the marker."""
    from assess_core import build_and_publish_run
    from assess_core_helpers import _minimal_repo
    from lib import core_scans

    def boom(*_a, **_k):
        raise RuntimeError("kaput")

    monkeypatch.setattr(core_scans, "analyze_doc_staleness", boom)
    ctx = build_and_publish_run(repo_root=_minimal_repo(tmp_path), run_date="2026-11-20",
                            non_interactive=True)
    assert ctx["doc_staleness"] == {"available": False,
                                    "reason": "doc_staleness scan failed: kaput"}
    assert ctx["stale_hubs"] == []
    assert ctx["churn_degenerate"] is False


def test_a_raising_doc_join_degrades_only_the_documentation_block(tmp_path, monkeypatch):
    from assess_core import build_and_publish_run
    from assess_core_helpers import _minimal_repo
    from lib import core_scans

    def boom(*_a, **_k):
        raise RuntimeError("kaput")

    monkeypatch.setattr(core_scans, "analyze_doc_complexity_join", boom)
    ctx = build_and_publish_run(repo_root=_minimal_repo(tmp_path), run_date="2026-11-20",
                            non_interactive=True)
    documentation = ctx["documentation"]
    assert documentation["available"] is False
    assert documentation["reason"] == "documentation scan failed: kaput"
    assert documentation["freshness_by_doc"] == {}
    assert ctx["doc_staleness"]["available"] is True


def test_a_spread_key_is_readable_from_its_stage_on():
    """The ``level`` entry (#744) reads ``structure`` and ``stats_summary``, spread keys."""
    spread = ScanSpec("both", lambda: {"a": 1, "b": 2}, (), reg.STAGE_READ_SIDE, spread=("a", "b"))
    reader = ScanSpec("sum", lambda a, b: a + b, ("a", "b"), reg.STAGE_POST_OFFERS)
    reg.validate((spread, reader))
    ctx: dict = {}
    reg.run_scans(ctx, {}, specs=(spread, reader))
    assert ctx == {"a": 1, "b": 2, "sum": 3}
    early = ScanSpec("early", lambda a: a, ("a",), reg.STAGE_SETUP)
    with pytest.raises(ScanRegistryError, match="later stage"):
        reg.validate((spread, early))


def test_the_level_entry_reads_the_default_models_blocks():
    from lib.scoring.level_tally import DEFAULT_BLOCKS, EXCLUSIONS_BLOCK, default_model, model_blocks

    spec = next(s for s in reg.SCANS if s.key == "level")
    assert spec.reads == (*DEFAULT_BLOCKS, EXCLUSIONS_BLOCK, "maturity_model", "pack")
    assert DEFAULT_BLOCKS == model_blocks(default_model())
    assert spec.stage == reg.STAGE_POST_OFFERS and not spec.network


def test_a_spread_key_its_entry_leaves_out_reads_none():
    spread = ScanSpec("some", lambda: {"a": 1}, (), reg.STAGE_READ_SIDE, spread=("a", "b"))
    reader = ScanSpec("pair", lambda a, b: [a, b], ("a", "b"), reg.STAGE_POST_OFFERS)
    ctx: dict = {}
    reg.run_scans(ctx, {}, specs=(spread, reader))
    assert ctx == {"a": 1, "pair": [1, None]}
