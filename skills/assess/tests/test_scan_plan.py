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
from lib.level_signals import signal_path_validator
from lib.level_tally import default_model, model_blocks
from lib.levels import load_levels
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
    for skipped in ("keyhole", "doc_staleness", "promissory", "unit_readings", "liveness", "ai_usage"):
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
