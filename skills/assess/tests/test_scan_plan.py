"""The scan plan a --profile or --levels run executes (lib/scan_plan.py, #1268).

Pins that a citizen run executes exactly the planned scans (by spy and by the ``scan_plan``
stamp) and gives the level a full run under the same profile gives; that a section whose
scan did not run reads "not run under profile citizen", never zero; that the default model
and ``--all-scans`` run every scan with no ``scan_plan`` key; that a collector can extend a
plan; and that ``/fleet --levels`` asks every repository for the same planned scans.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import lib.scan_registry as sr
from lib.scoring.level_signals import signal_path_validator
from lib.scoring.level_tally import default_model, model_blocks
from lib.scoring.levels import load_levels
from lib.profiles import ModelChoice, profile_path, resolve, single_repo_choice
from lib.scan_plan import (NOT_RUN_CODE, ScanPlan, ScanPlanError, fleet_scans, frame_keys, model_plan,
                           not_run_text, plan_scans, run_plan)
from lib.scan_registry import SCANS
from lib.signals import known_scans
from test_headline import ci2  # noqa: F401 - ci2 is a fixture

DAY = "2026-11-24"


def _citizen() -> ModelChoice:
    return single_repo_choice("citizen", None, signal_path_validator())


def _spy(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    called: list[str] = []
    real = sr._call

    def spy(spec: sr.ScanSpec, args: tuple[Any, ...]) -> tuple[Any, bool]:
        called.append(spec.key)
        return real(spec, args)

    monkeypatch.setattr(sr, "_call", spy)
    return called


def _core(repo: Path, *argv: str) -> dict[str, Any]:
    from assess_core import main

    assert main([str(repo), "--non-interactive", *argv]) == 0
    ctx: dict[str, Any] = json.loads((repo / ".assess" / "run-context.json").read_text(encoding="utf-8"))
    return ctx


# --- the plan -----------------------------------------------------------------


def test_the_citizen_plan_is_its_blocks_their_reads_and_the_frame() -> None:
    plan = run_plan(_citizen())
    assert plan is not None and plan.label == "profile citizen"
    # the collectors of the four signals' blocks, and the owner exclusions the level reads
    assert {"layer_grades", "ci_checks", "secrets_guard", "owner_exclusions"} <= plan.scans
    # what those read, transitively, private entries included
    assert {"nested_instructions", "instruction_grading", "gitlab_merged_ci", "external_ci",
            "merged_sample", "github_secret_scanning"} <= plan.scans
    # the headline and the Step gates: the level, the headline and what changed since the last run
    assert {"level", "headline", "since_last_run", "instruction_placement"} <= plan.scans
    assert frame_keys(SCANS) <= plan.scans
    # nothing the model does not read
    for skipped in ("keyhole", "doc_staleness", "promissory", "unit_readings", "unit_rollup", "liveness",
                    "ai_usage"):
        assert skipped not in plan.scans and skipped in plan.not_run
    assert set(plan.not_run) | plan.scans == {s.key for s in SCANS}


def test_a_spread_block_plans_its_entry_and_what_it_reads() -> None:
    scans = plan_scans(["structure"], SCANS)  # a keyhole spread key
    assert {"keyhole_blocks", "keyhole", "doc_staleness", "liveness_blocks", "doc_graph"} <= scans


def test_a_collector_can_extend_the_plan(tmp_path: Path) -> None:
    model = _citizen().model
    base = model_plan(model, "profile citizen")
    extended = model_plan(model, "profile citizen", extra_signals=["ai_usage.share"],
                          extra_scans=["agent_outcome"])
    assert extended.scans - base.scans == {"ai_usage", "agent_outcome"}
    with pytest.raises(ScanPlanError, match="nope"):
        model_plan(model, "x", extra_signals=["nope.field"])
    with pytest.raises(ScanPlanError, match="no_scan"):
        plan_scans([], SCANS, extra_scans=["no_scan"])


def test_the_default_model_and_all_scans_plan_nothing_out() -> None:
    assert run_plan(None) is None
    assert run_plan(resolve(None, None)) is None
    assert run_plan(ModelChoice(default_model(), "file", "copy.toml")) is None  # the default model's own file
    assert run_plan(_citizen(), all_scans=True) is None


def test_a_skipped_marker_reads_not_run_never_zero() -> None:
    plan = ScanPlan("profile citizen", frozenset({"a"}), ("b",))
    assert plan.marker() == {"available": False, "reason": f"{NOT_RUN_CODE}: not run under profile citizen"}
    assert not_run_text(plan.marker()) == "not run under profile citizen"
    for other in (None, 0, [], {"available": False, "reason": "network: x"}, {"available": True}):
        assert not_run_text(other) is None
    assert plan.stamp() == {"model": "profile citizen", "ran": ["a"], "not_run": ["b"]}


# --- a single-repository run ----------------------------------------------------


def test_a_citizen_run_executes_only_the_plan_and_gives_the_full_runs_level(
        ci2: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    plan = run_plan(_citizen())
    assert plan is not None
    called = _spy(monkeypatch)
    narrow = _core(ci2, "--profile", "citizen")
    assert set(called) == plan.scans  # by spy
    assert narrow["scan_plan"] == plan.stamp()  # by the stamp
    called.clear()
    full = _core(ci2, "--profile", "citizen", "--all-scans")
    assert set(called) == {s.key for s in SCANS}
    assert "scan_plan" not in full
    assert narrow["level"] == full["level"]
    assert narrow["headline"]["lines"] == full["headline"]["lines"]


def test_every_skipped_block_reads_not_run_and_keeps_its_place(ci2: Path) -> None:  # noqa: F811
    narrow = _core(ci2, "--profile", "citizen")
    full = _core(ci2, "--profile", "citizen", "--all-scans")
    # every key keeps its place; an optional spread key a full run may leave out is a marker here
    assert [k for k in narrow if k in full] == list(full)
    assert all(not_run_text(narrow[k]) for k in narrow if k not in full and k != "scan_plan")
    published = {name: spec.key for spec in SCANS
                 for name in (spec.spread or (() if spec.private else (spec.key,)))}
    for name, key in published.items():
        if key in narrow["scan_plan"]["not_run"]:
            assert not_run_text(narrow[name]) == "not run under profile citizen", name


def test_a_section_whose_scan_did_not_run_reads_not_run_never_zero(ci2: Path) -> None:  # noqa: F811
    from assess_report import render_report

    report = render_report(_core(ci2, "--profile", "citizen"), "ci2")
    # the top card (#1252) reads only blocks the plan ran, and says the rest were not run
    card = report.split("## Catalogue Coverage", 1)[0]
    assert ("_Scans this model does not read were not run under profile citizen; their sections say so. "
            "Run with `--all-scans` for every scan._") in card
    assert "not known" not in card
    assert "Scans this model" not in render_report(_core(ci2, "--profile", "citizen", "--all-scans"), "ci2")
    keyhole = report.split("## Keyhole Readiness", 1)[1].split("\n## ", 1)[0]
    assert "_Not run under profile citizen._" in keyhole
    assert "No cross-practice findings" not in keyhole and " 0 " not in keyhole
    measured = report.split("## How this was measured", 1)[1]
    skipped = measured.split("### Skipped", 1)[1]
    assert "- `ai_usage`: not run under profile citizen." in skipped
    ran = measured.split("### Ran", 1)[1].split("### Skipped", 1)[0]
    assert "`ai_usage`" not in ran and "`secrets_guard`" in ran


def test_a_narrowed_run_keeps_the_committed_findings_snapshot(ci2: Path) -> None:  # noqa: F811
    _core(ci2)
    snapshot = ci2 / ".assess" / "findings.json"
    snapshot.write_text('{"assess_method_version": 1, "findings": [{"name": "kept"}]}', encoding="utf-8")
    _core(ci2, "--profile", "citizen")
    assert "kept" in snapshot.read_text(encoding="utf-8")


def test_the_default_run_runs_every_scan_and_records_no_plan(
        ci2: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    called = _spy(monkeypatch)
    ctx = _core(ci2)
    assert set(called) == {s.key for s in SCANS}
    assert "scan_plan" not in ctx
    assert not any(not_run_text(v) for v in ctx.values())


# --- /fleet --levels --------------------------------------------------------------


def test_fleet_plans_the_models_blocks_for_every_repository() -> None:
    model = load_levels(profile_path("citizen"))
    assert fleet_scans(model, known_scans()) == sorted({*model_blocks(model), "owner_exclusions"})
    assert fleet_scans(None, known_scans()) is None
    assert fleet_scans(default_model(), known_scans()) is None
    assert fleet_scans(model, known_scans(), all_scans=True) is None


def test_fleet_profile_runs_the_plan_and_stamps_it(tmp_path: Path) -> None:
    from test_fleet import make_repo
    from test_fleet_cli import fleet_cli

    root = tmp_path / "froot"
    make_repo(root, "grpa/svc1", files={"CLAUDE.md": "Run make test.\n"})
    out = tmp_path / "out"
    assert fleet_cli.main([str(root), "--profile", "citizen", "--out", str(out), "--no-history",
                           "--no-diagrams", "--jobs", "1"]) == 0
    stamp = json.loads((out / "run.json").read_text(encoding="utf-8"))
    model = load_levels(profile_path("citizen"))
    assert stamp["scans"] == fleet_scans(model, known_scans())
    row = json.loads((out / "repos.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert set(row["scans"]) == set(stamp["scans"])
    full = tmp_path / "full"
    assert fleet_cli.main([str(root), "--profile", "citizen", "--all-scans", "--out", str(full),
                           "--no-history", "--no-diagrams", "--jobs", "1"]) == 0
    assert json.loads((full / "run.json").read_text(encoding="utf-8"))["scans"] is None


# --- readers of a skipped block (review on #1279) ----------------------------------


def test_the_gate_says_the_findings_were_not_checked_never_clean(ci2: Path) -> None:  # noqa: F811
    from assess_gate import evaluate, format_verdict

    ctx = _core(ci2, "--profile", "citizen")
    verdict = evaluate(ctx, {"enabled": True, "fail_on": [], "warn_on": []})
    out = format_verdict(verdict)
    assert verdict["findings_not_run"] == "not run under profile citizen"
    assert "warn findings not checked: not run under profile citizen; run /assess with --all-scans" in out
    assert "clean snapshot" not in out
    assert "findings_not_run" not in evaluate(_core(ci2, "--profile", "citizen", "--all-scans"),
                                              {"enabled": True, "fail_on": [], "warn_on": []})


def test_a_hotspot_page_says_the_marker_scan_did_not_run(ci2: Path) -> None:  # noqa: F811
    stats = ci2 / ".assess" / "complexity-stats.json"
    data = json.loads(stats.read_text(encoding="utf-8"))
    data["top_hotspots"] = [{"path": "README.md", "loc": 3, "ccn": 1, "churn": 2, "score": 1.0}]
    stats.write_text(json.dumps(data), encoding="utf-8")
    _core(ci2, "--profile", "citizen")
    pages = list((ci2 / ".assess" / "hotspots").glob("*.md"))
    assert pages, "the fixture's hotspot gets a page"
    text = pages[0].read_text(encoding="utf-8")
    assert "Stale promissory markers: not run under profile citizen." in text


def test_a_method_change_never_lists_findings_a_plan_left_out_as_cleared() -> None:
    from lib.findings_snapshot import method_change

    prior = {"assess_method_version": 1, "findings": [{"name": "lying_map", "paths": ["a.md"]}]}
    ctx: dict[str, Any] = {"prior_findings_snapshot": prior, "assess_method_version": 2,
                           "derived_findings": ScanPlan("profile citizen", frozenset(), ()).marker()}
    assert method_change(ctx) is None
    ctx["derived_findings"] = []
    change = method_change(ctx)
    assert change is not None and change["cleared"] == [{"finding": "lying_map", "path": "a.md"}]


def test_fleet_unit_rows_under_a_plan_match_all_scans(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from test_fleet_cli import fleet_cli
    from test_unit_readings import MONOREPO, _write

    mono = _write(tmp_path / "root" / "grp" / "mono" / "mono-main", MONOREPO)
    monkeypatch.chdir(tmp_path)
    args = [str(mono.parents[2]), "--profile", "handover", "--monorepo-units", "--no-history", "--no-diagrams"]
    assert fleet_cli.main([*args, "--out", "plan"]) == 0
    assert fleet_cli.main([*args, "--out", "full", "--all-scans"]) == 0

    def units(out: str) -> Any:
        (repo,) = json.loads((tmp_path / out / "fleet-data.json").read_text(encoding="utf-8"))["repos"]
        return repo["unit_rows"]

    assert json.loads((tmp_path / "plan" / "run.json").read_text(encoding="utf-8"))["scans"] is not None
    assert units("plan") and units("plan") == units("full")


@pytest.mark.parametrize("gate_toml", ['fail_on = ["lying_map"]', "containment_min = 0.5"])
def test_a_findings_threshold_fails_the_gate_on_a_narrowed_run(
        ci2: Path, gate_toml: str, capsys: pytest.CaptureFixture[str]) -> None:  # noqa: F811
    from assess_gate import main as gate_main

    _core(ci2, "--profile", "citizen")
    (ci2 / ".assess" / "config.toml").write_text(f"[gate]\n{gate_toml}\n", encoding="utf-8")
    capsys.readouterr()
    assert gate_main([str(ci2)]) == 1
    out = capsys.readouterr().out
    threshold = gate_toml.split(" ", 1)[0]
    assert (f"FAIL scans not run: the findings scans were not run under profile citizen, and the gate "
            f"needs the scans its {threshold} threshold reads; re-run /assess with --all-scans, or "
            "without --profile / --levels") in out
    assert "RESULT: FAIL" in out and "clean snapshot" not in out
    # the same threshold over a full run under the profile reads the findings and passes
    _core(ci2, "--profile", "citizen", "--all-scans")
    capsys.readouterr()
    assert gate_main([str(ci2)]) == 0
    assert "scans not run" not in capsys.readouterr().out


def test_a_gate_without_a_findings_threshold_only_warns_on_a_narrowed_run(
        ci2: Path, capsys: pytest.CaptureFixture[str]) -> None:  # noqa: F811
    from assess_gate import main as gate_main

    _core(ci2, "--profile", "citizen")
    (ci2 / ".assess" / "config.toml").write_text('[gate]\nwarn_on = ["lying_map"]\nccn_p95_max = 99\n',
                                                 encoding="utf-8")
    capsys.readouterr()
    assert gate_main([str(ci2)]) == 0
    out = capsys.readouterr().out
    assert "warn findings not checked: not run under profile citizen" in out
    assert "FAIL" not in out and "RESULT: PASS" in out


# --- per-threshold narrowed-run check (#1281) ----------------------------------------

STRUCTURE_MODEL = """version = "custom-structure-2026.11.24"

[[rows]]
id = "layer-2-design"
title = "Code design (module structure)"
scope = "team"
aggregation = "min"

  [[rows.levels]]
  level = 1
  truth = ["structure"]
  criteria = [{ signal = "structure.modularity_q", op = "gte", value = 0.3 }]
"""


def test_a_levels_plan_that_ran_the_findings_scans_evaluates_fail_on(
        ci2: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:  # noqa: F811
    from assess_gate import evaluate
    from assess_gate import main as gate_main

    model = tmp_path / "structure.toml"
    model.write_text(STRUCTURE_MODEL, encoding="utf-8")
    ctx = _core(ci2, "--levels", str(model))
    assert "keyhole_blocks" in ctx["scan_plan"]["ran"] and ctx["scan_plan"]["not_run"]
    (ci2 / ".assess" / "config.toml").write_text('[gate]\nfail_on = ["lying_map"]\n', encoding="utf-8")
    capsys.readouterr()
    assert gate_main([str(ci2)]) == 0
    out = capsys.readouterr().out
    assert "scans not run" not in out and "RESULT: PASS" in out
    # the threshold is evaluated, not waved through: a fired finding fails it as a full run does
    ctx["derived_findings"] = [{"name": "lying_map", "paths": ["README.md"]}]
    verdict = evaluate(ctx, {"enabled": True, "fail_on": ["lying_map"], "warn_on": []})
    assert verdict["failed"] and verdict["failures"][0]["finding"] == "lying_map"
    assert verdict["threshold_breaches"] == []


def _narrowed(not_run: object, **blocks: object) -> dict[str, Any]:
    ctx: dict[str, Any] = {"scan_plan": {"model": "model custom", "ran": [], "not_run": not_run},
                           "derived_findings": [], "keyhole_summary": {}}
    ctx.update(blocks)
    return ctx


@pytest.mark.parametrize("ctx", [
    _narrowed(["keyhole_blocks"]),  # the plan left the publisher out
    _narrowed(None),  # an unreadable plan fails closed
    _narrowed([], derived_findings=ScanPlan("model custom", frozenset(), ()).marker(),
              keyhole_summary=ScanPlan("model custom", frozenset(), ()).marker()),  # a marker
])
def test_a_threshold_whose_scans_did_not_run_fails(ctx: dict[str, Any]) -> None:
    from assess_gate import check_narrowed_run

    gate: Any = {"fail_on": ["lying_map"], "containment_min": 0.5}
    (breach,) = check_narrowed_run(ctx, gate)
    assert breach["metric"] == "scans_not_run" and breach["paths"] == ["fail_on", "containment_min"]


def test_each_threshold_reads_only_its_own_block() -> None:
    from assess_gate import check_narrowed_run

    marker = ScanPlan("model custom", frozenset(), ()).marker()
    gate: Any = {"fail_on": ["lying_map"], "containment_min": 0.5}
    assert check_narrowed_run(_narrowed(["ci_checks"]), gate) == []
    (only_fail_on,) = check_narrowed_run(_narrowed([], derived_findings=marker), gate)
    assert only_fail_on["paths"] == ["fail_on"]
    (only_containment,) = check_narrowed_run(_narrowed([], keyhole_summary=marker), gate)
    assert only_containment["paths"] == ["containment_min"]
