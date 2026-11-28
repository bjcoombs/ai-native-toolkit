"""Tests for the declared scan table and the loop that runs it."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from lib.run import scan_registry as reg
from lib.run.scan_registry import ScanRegistryError, ScanSpec
from lib.run.scan_plan import ScanPlan
from lib.run.scan_timings import TIMINGS_KEY

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
    # only the pool size may be passed besides the context and inputs (#1381)
    assert len(calls[0].args) == 2 and [k.arg for k in calls[0].keywords] == ["jobs"]


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
    # the timings diagnostic (#1380) follows every declared key
    assert list(ctx) == [key for key in declared if key in ctx] + [TIMINGS_KEY]
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
    from lib.run import core_scans

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
    from lib.run import core_scans

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
    from lib.scoring.condition_evidence import EVIDENCE_BLOCKS
    from lib.scoring.level_tally import DEFAULT_BLOCKS, EXCLUSIONS_BLOCK, default_model, model_blocks

    spec = next(s for s in reg.SCANS if s.key == "level")
    # the model's blocks, then the evidence-only blocks (#855), then the exclusions
    assert spec.reads == (*DEFAULT_BLOCKS, *EVIDENCE_BLOCKS, EXCLUSIONS_BLOCK, "maturity_model", "pack")
    assert DEFAULT_BLOCKS == model_blocks(default_model())
    assert spec.stage == reg.STAGE_POST_OFFERS and not spec.network


def test_a_spread_key_its_entry_leaves_out_reads_none():
    spread = ScanSpec("some", lambda: {"a": 1}, (), reg.STAGE_READ_SIDE, spread=("a", "b"))
    reader = ScanSpec("pair", lambda a, b: [a, b], ("a", "b"), reg.STAGE_POST_OFFERS)
    ctx: dict = {}
    reg.run_scans(ctx, {}, specs=(spread, reader))
    assert ctx == {"a": 1, "pair": [1, None]}


def test_timings_go_to_the_inputs_never_to_the_context():
    """#1380: each stage and each called scan is timed, a raising one too; a scan the plan
    skips has no entry; the context run_scans fills carries none of it."""
    def boom(_root):
        raise RuntimeError("kaput")

    specs = (_spec("first", lambda root: 1), _spec("second", boom),
             _spec("skipped", lambda root: 3), _spec("late", lambda root: 4, stage=reg.STAGE_FINAL))
    plan = ScanPlan("profile test", frozenset({"first", "second", "late"}), ("skipped",))
    ctx: dict = {}
    inputs: dict = {"repo_root": "r"}
    reg.run_scans(ctx, inputs, specs=specs, plan=plan)

    assert TIMINGS_KEY not in ctx
    block = inputs[TIMINGS_KEY]
    assert list(block["stages"]) == list(reg.STAGES)
    assert list(block["scans"]) == ["first", "second", "late"]
    assert all(isinstance(v, float) and v >= 0 for v in [*block["stages"].values(), *block["scans"].values()])
    assert block["total_seconds"] == round(sum(block["stages"].values()), 3)


def test_timings_block_is_written_and_left_out_of_counts_only(tmp_path, capsys):
    """#1380: run-context.json carries per-stage and per-scan wall time; the counts-only
    copy carries none, and stdout stays the diff while stderr gets the one-line summary."""
    import json

    from assess_core import main
    from assess_core_helpers import _minimal_repo
    from lib.counts_only.content_free import COUNTS_ONLY_FILE

    repo = _minimal_repo(tmp_path)
    assert main([str(repo), "--non-interactive", "--counts-only"]) == 0
    out, err = capsys.readouterr()
    json.loads(out)  # stdout is still only the diff JSON
    assert "timings: scans total " in err and "; slowest scans: " in err
    ctx = json.loads((repo / ".assess" / "run-context.json").read_text(encoding="utf-8"))
    block = ctx[TIMINGS_KEY]
    assert set(block) == {"total_seconds", "stages", "scans"}
    assert list(block["stages"]) == list(reg.STAGES)
    assert {"run_header", "level", "anomalies"} <= set(block["scans"])
    assert block["total_seconds"] >= max(block["scans"].values()) >= 0

    content_free = json.loads((repo / ".assess" / COUNTS_ONLY_FILE).read_text(encoding="utf-8"))
    assert TIMINGS_KEY not in content_free
    assert "total_seconds" not in json.dumps(content_free)


def _fixture_repos(root: Path) -> list[Path]:
    """The golden fixture repositories, each its own committed git repository with an empty
    stats sidecar, plus the minimal repository the core tests use."""
    import shutil
    import subprocess

    from assess_core_helpers import _minimal_repo, _seed_assess

    fixtures = Path(__file__).resolve().parent / "fixtures"
    repos = [_minimal_repo(root)]
    for name in ("golden-doc-repo", "golden-svg-repo", "structure_golden/repo", "ts_workspace"):
        repo = root / name.replace("/", "-")
        shutil.copytree(fixtures / name, repo)
        for args in (("init", "-q"), ("add", "-A"),
                     ("-c", "user.email=a@example.com", "-c", "user.name=A", "commit", "-qm", "init")):
            subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                           encoding="utf-8", errors="replace")
        _seed_assess(repo)
        repos.append(repo)
    return repos


def test_default_jobs_matches_jobs_1(tmp_path, monkeypatch):
    """#1381: run-context bytes are the same under --jobs 1, --jobs 4 and the default on every
    fixture repository, timings removed; the run id is pinned, as it is fresh on every run."""
    import json
    import uuid

    import lib.run.run_setup as rs
    from assess_core import build_run_context
    from lib.run.scan_pool import default_jobs

    class _Clock:
        @staticmethod
        def now():
            import datetime
            return datetime.datetime(2026, 11, 25, 12, 0, 0)

    monkeypatch.setattr(rs, "datetime", _Clock)
    monkeypatch.setattr(uuid, "uuid4", lambda: uuid.UUID(int=0))
    for repo in _fixture_repos(tmp_path):
        runs = {}
        for jobs in (1, 4, default_jobs()):
            ctx = build_run_context(repo_root=repo, run_date="2026-11-25", non_interactive=True, jobs=jobs)
            assert set(ctx.pop(TIMINGS_KEY)["scans"]) >= {"run_header", "level"}
            runs[jobs] = json.dumps(ctx, indent=2, default=str)
        assert runs[4] == runs[1], repo.name
        assert runs[default_jobs()] == runs[1], repo.name


def test_signals_document_is_the_same_under_jobs_4(tmp_path):
    """#1381: the headless runner's blocks do not depend on --jobs either."""
    import json

    from lib.run.signals import build_document

    repo = _fixture_repos(tmp_path)[1]
    one, four = (build_document(repo, jobs=jobs)["scans"] for jobs in (1, 4))
    assert json.dumps(four, indent=2, default=str) == json.dumps(one, indent=2, default=str)


def test_a_pooled_stage_writes_keys_and_timings_in_table_order():
    """#1381: a slow first entry finishes last, yet the context and the timings list it first;
    its reader waits for it, and a spread entry's keys keep their returned order."""
    import time

    def slow(_root):
        time.sleep(0.2)
        return "slow"

    specs = (_spec("slow", slow), _spec("fast", lambda root: "fast"),
             ScanSpec("pair", lambda root: {"p2": 2, "p1": 1}, ("repo_root",), reg.STAGE_READ_SIDE,
                      spread=("p1", "p2")),
             _spec("reader", lambda s: s + "!", reads=("slow",)))
    ctx: dict = {"before": 0}
    inputs: dict = {"repo_root": "r"}
    reg.run_scans(ctx, inputs, reg.STAGE_READ_SIDE, specs, jobs=4)
    assert list(ctx.items()) == [("before", 0), ("slow", "slow"), ("fast", "fast"),
                                 ("p2", 2), ("p1", 1), ("reader", "slow!")]
    assert list(inputs[TIMINGS_KEY]["scans"]) == ["slow", "fast", "pair", "reader"]


def test_a_pooled_gate_failure_raises_after_the_earlier_entries_are_written():
    """#1381: a non-degrading entry's raise stops the run as under --jobs 1, and is timed."""
    def gate(_root):
        raise ValueError("bad scope")

    specs = (_spec("first", lambda root: 1),
             _spec("gate", gate, degrade=False, gate_reason="test"),
             _spec("after", lambda root: 3))
    ctx: dict = {}
    inputs: dict = {"repo_root": "r"}
    with pytest.raises(ValueError, match="bad scope"):
        reg.run_scans(ctx, inputs, reg.STAGE_READ_SIDE, specs, jobs=4)
    assert ctx["first"] == 1 and "gate" in inputs[TIMINGS_KEY]["scans"]


def test_assess_core_takes_jobs(tmp_path, capsys):
    from assess_core import main
    from assess_core_helpers import _minimal_repo

    repo = _minimal_repo(tmp_path)
    assert main([str(repo), "--non-interactive", "--jobs", "1"]) == 0
    with pytest.raises(SystemExit):
        main([str(repo), "--jobs", "0"])
    assert "--jobs" in capsys.readouterr().err
