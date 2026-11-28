"""The cumulative Step gates of the shipped models (#1641, parent #1351).

Each Step of the default model and of the ``citizen`` and ``handover`` profiles includes every
gate below it and gates on the evidence that makes its delegation safe:

- Step 2, "Supervised agent changes": ``layer-0-instructions.2`` (the instruction file names a
  check command, no instruction claim fails, the 200-line budget);
- Step 3, "Unattended agent changes": Step 2 plus ``layer-5-ci.1``, ``layer-7-review.1``,
  ``layer-3-linters.1`` and ``secrets-guard.1``;
- Step 4, "Autonomous loops": Step 3 plus ``layer-6-coverage.1``, ``layer-7-review.2``,
  ``layer-8-agent-ops.1`` and ``layer-4-architecture.1``.

The first six tests are the Success Criteria fixtures of #1351. The signals are invented block
shapes as the scanners write them; no fixture names a real repository.
"""
from __future__ import annotations

import copy
import subprocess
from pathlib import Path
from typing import Any

import pytest

from lib.fleet.fleet_data import Privacy, build_fleet_data
from lib.fleet.fleet_rescore import rescore
from lib.fleet.fleet_units import build_trees, empty_map
from lib.fleet.runner import FLEET_ROW_VERSION, FleetRow
from lib.profiles import profile_path
from lib.scoring.headline import GATE_CHANGE, GATE_NOUN, headline, lone_condition
from lib.scoring.level_tally import default_model, level_result
from lib.scoring.levels import MaturityModel, load_levels
from lib.scoring.step_reading import step_reading
from lib.top_card import GATE_GAP, GATE_TASK
from fleet_data_fixture import STAMP

MODEL = default_model()
REPO = Path(__file__).resolve().parents[3]

STEP2 = ["layer-0-instructions.1", "layer-0-instructions.2"]
STEP3 = [*STEP2, "layer-5-ci.1", "layer-7-review.1", "layer-3-linters.1", "secrets-guard.1"]
STEP4 = [*STEP3, "layer-6-coverage.1", "layer-7-review.2", "layer-8-agent-ops.1", "layer-4-architecture.1"]


def _step3_signals() -> dict[str, Any]:
    """Every Step 3 gate met; no coverage gate, so Step 4 waits on it."""
    return {
        "layer_grades": {"instruction_file_present": True},
        "agent_environment": {"available": True, "check": {"named_total": 1}},
        "instruction_claims": {"total": 3, "verified": 3, "failed": 0},
        "nested_instructions": {"available": True, "budget": {"claude_code": {"files": ["CLAUDE.md"], "lines": 40}}},
        "ci_checks": {"enforced_check_runs": True, "enforced_basis": "required_checks", "lint_runs": True},
        "review_evidence": {"present": True},
        "secrets_guard": {"configured": True, "configured_reason": None},
        "coverage_gate": {"enforced_meets_minimum": False, "enforced_basis": "no_gate"},
    }


def _step4_signals() -> dict[str, Any]:
    signals = _step3_signals()
    signals["coverage_gate"] = {"enforced_meets_minimum": True, "enforced_basis": "required_check"}
    signals["review_reality"] = {"reviewed_share": 0.9, "approved_share": 0.7}
    signals["agent_ops"] = {"summary": {"permissions_encoded": True}}
    signals["structure_drift"] = {"tier_0": {"boundary_patterns": 3}}
    return signals


def resolve_profile(name: str) -> MaturityModel:
    return load_levels(profile_path(name))


def _states(level: Any) -> dict[str, str]:
    return {c["id"]: c["state"] for c in level["conditions"]}


# --- the six Success Criteria fixtures of #1351 -------------------------------------------------


def test_instructions_and_ci_without_review_evidence_read_step_2() -> None:
    signals = _step3_signals()
    signals["review_evidence"] = {"present": False}
    level = level_result(MODEL, signals)
    assert level["step"] == 2
    assert level["next_step"] is not None and level["next_step"]["step"] == 3
    assert [(b["gate"], b["state"]) for b in level["next_step"]["blockers"]] == [("review", "unmet")]
    assert headline(level, MODEL)["summary"] == "Step 2 · next: review evidence"


def test_an_instruction_file_without_a_check_command_reads_step_1() -> None:
    signals = _step3_signals()
    signals["agent_environment"] = {"available": True, "check": {"named_total": 0}}
    level = level_result(MODEL, signals)
    assert level["step"] == 1
    assert level["next_step"] is not None
    assert [(b["gate"], b["condition"], b["state"]) for b in level["next_step"]["blockers"]] == [
        ("instructions", "layer-0-instructions.2", "unmet")]


def test_a_secrets_guard_behind_an_unread_ci_include_reads_step_3_not_measured() -> None:
    signals = _step3_signals()
    signals["secrets_guard"] = {"configured": None, "configured_reason": "ci_includes_unresolved"}
    level = level_result(MODEL, signals)
    assert level["step"] == 2
    assert step_reading(level) == "Step 2, Step 3 not measured"
    assert level["next_step"] is not None
    assert [(b["gate"], b["state"], b.get("reason")) for b in level["next_step"]["blockers"]] == [
        ("secrets_guard", "not_measured", "ci_includes_unresolved")]


def test_unresolved_ci_includes_read_step_3_not_measured_never_step_3_or_a_gate_not_held() -> None:
    signals = _step3_signals()
    signals["ci_checks"] = {"enforced_check_runs": None, "enforced_basis": "ci_includes_unresolved",
                            "lint_runs": None, "lint_basis": "ci_includes_unresolved"}
    signals["secrets_guard"] = {"configured": None, "configured_reason": "ci_includes_unresolved"}
    signals["coverage_gate"] = {"enforced_meets_minimum": None, "enforced_basis": "ci_includes_unresolved"}
    level = level_result(MODEL, signals)
    assert level["step"] == 2 and level["step"] != 3
    assert step_reading(level) == "Step 2, Step 3 not measured"
    assert level["next_step"] is not None
    blockers = level["next_step"]["blockers"]
    assert {b["gate"] for b in blockers} == {"ci_runs_tests", "linters", "secrets_guard"}
    assert all(b["state"] == "not_measured" for b in blockers)
    assert headline(level, MODEL)["summary"].startswith("Step 2, Step 3 not measured")


def test_every_step_3_gate_met_without_a_coverage_gate_reads_step_3() -> None:
    level = level_result(MODEL, _step3_signals())
    assert level["step"] == 3
    assert level["next_step"] is not None and level["next_step"]["step"] == 4
    assert level["next_step"]["blockers"][0]["gate"] == "coverage_gate"
    assert all(_states(level)[cid] == "met" for cid in STEP3)


def _row(repo: str, scans: dict[str, Any]) -> FleetRow:
    return {"fleet_row_version": FLEET_ROW_VERSION, "repo": repo, "group": repo.rsplit("/", 1)[0],  # type: ignore[typeddict-item]  # a partial block map
            "status": "ok", "reason": None, "signals_schema_version": 1, "active": True, "scans": scans}


def _old_model() -> MaturityModel:
    """The default model with the Steps it shipped before #1641: one gate added per Step."""
    old = copy.deepcopy(MODEL)
    instructions = {"id": "instructions", "row": "layer-0-instructions", "level": 1}
    ci = {"id": "ci_runs_tests", "row": "layer-5-ci", "level": 1}
    cov = {"id": "coverage_gate", "row": "layer-6-coverage", "level": 1}
    old["steps"] = [{"step": 1, "name": "Assisted", "gates": []},
                    {"step": 2, "name": "Supervised agent PRs", "gates": [instructions]},
                    {"step": 3, "name": "Unattended agent PRs", "gates": [instructions, ci]},
                    {"step": 4, "name": "Autonomous loops", "gates": [instructions, ci, cov]}]
    old["version"] = "default-before-1641"
    return old


def test_rescoring_a_stored_fleet_run_reproduces_the_steps_of_a_fresh_run() -> None:
    no_review = {**_step3_signals(), "review_evidence": {"present": False}}
    no_command = {**_step3_signals(), "agent_environment": {"available": True, "check": {"named_total": 0}}}
    rows = [_row("grp/a", _step3_signals()), _row("grp/b", no_review), _row("grp/c", no_command),
            _row("grp/d", _step4_signals())]
    keys = sorted(r["repo"] for r in rows)
    trees = build_trees(keys, empty_map(), {}, {})

    def data(model: MaturityModel) -> Any:
        stamp = {**STAMP, "levels_version": model["version"]}
        return build_fleet_data(stamp, rows, model, trees)  # type: ignore[arg-type]  # the stamp fixture

    stored, fresh = data(_old_model()), data(MODEL)
    again = rescore(stored, rows, MODEL, Privacy().key)
    assert again is not None
    steps = {r["key"]: r["step"] for r in again["repos"]}
    assert steps == {r["key"]: r["step"] for r in fresh["repos"]}
    assert steps == {"grp/a": 3, "grp/b": 2, "grp/c": 1, "grp/d": 4}
    # the stored run read Step 4 for all but the one without its coverage gate: every move is the rules'
    assert {r["key"]: r["step"] for r in stored["repos"]} == {"grp/a": 3, "grp/b": 3, "grp/c": 3, "grp/d": 4}
    assert again["repos"] == fresh["repos"] and again["views"] == fresh["views"]


# --- the gates as the model writes them ---------------------------------------------------------


def _gate_conditions(model: MaturityModel) -> list[list[str]]:
    return [sorted({f"{g['row']}.{n}" for g in s["gates"] for n in range(1, g["level"] + 1)})
            for s in model["steps"]]


def test_the_default_steps_are_cumulative_foundations() -> None:
    steps = MODEL["steps"]
    assert [s["name"] for s in steps] == ["Assisted", "Supervised agent changes", "Unattended agent changes",
                                         "Autonomous loops"]
    assert _gate_conditions(MODEL) == [[], sorted(STEP2), sorted(STEP3), sorted(STEP4)]
    assert [g["id"] for g in steps[3]["gates"]] == [
        "instructions", "ci_runs_tests", "review", "linters", "secrets_guard",
        "coverage_gate", "wide_review", "agent_permissions", "boundaries"]


@pytest.mark.parametrize("name", ["citizen", "handover"])
def test_each_profile_takes_the_default_gates_and_the_rows_they_read(name: str) -> None:
    profile = resolve_profile(name)
    defaults = {s["step"]: s for s in MODEL["steps"]}
    assert all(step == defaults[step["step"]] for step in profile["steps"])
    held = {f"{r['id']}.{lv['level']}" for r in profile["rows"] for lv in r["levels"]}
    assert set(c for gates in _gate_conditions(profile) for c in gates) <= held


@pytest.mark.parametrize("name", ["citizen", "handover"])
def test_a_profile_reaches_its_step_3_only_with_every_new_gate_met(name: str) -> None:
    profile = resolve_profile(name)
    assert level_result(profile, _step3_signals())["step"] == 3
    for path, value in (("agent_environment", {"available": True, "check": {"named_total": 0}}),
                        ("review_evidence", {"present": False}),
                        ("ci_checks", {"enforced_check_runs": True, "enforced_basis": "required_checks",
                                       "lint_runs": False}),
                        ("secrets_guard", {"configured": False, "configured_reason": None})):
        assert level_result(profile, {**_step3_signals(), path: value})["step"] == (1 if path == "agent_environment" else 2), path


def test_handover_reaches_step_4_only_with_every_step_4_gate_met() -> None:
    profile = resolve_profile("handover")
    assert level_result(profile, _step4_signals())["step"] == 4
    for path, value in (("review_reality", {"reviewed_share": 0.5, "approved_share": 0.7}),
                        ("agent_ops", {"summary": {"permissions_encoded": False}}),
                        ("structure_drift", {"tier_0": {"boundary_patterns": 0}})):
        assert level_result(profile, {**_step4_signals(), path: value})["step"] == 3, path


# --- the wording ----------------------------------------------------------------------------------


def test_every_shipped_gate_has_its_words() -> None:
    gates = {g["id"] for s in MODEL["steps"] for g in s["gates"]}
    for table in (GATE_CHANGE, GATE_NOUN, GATE_TASK, GATE_GAP):
        assert gates <= set(table), sorted(gates - set(table))


def test_one_change_is_named_only_when_one_condition_is_left() -> None:
    """``lone_condition`` under the new gates: two open Step 3 gates are never one change."""
    signals = _step3_signals()
    signals["review_evidence"] = {"present": False}
    signals["ci_checks"] = {**signals["ci_checks"], "lint_runs": False}
    two = level_result(MODEL, signals)
    assert lone_condition(two, MODEL["steps"]) is None
    assert "one change" not in headline(two, MODEL)["summary"]
    # Step 1 on the check command alone, with every Step 3 gate held: one change takes it to Step 3
    one = level_result(MODEL, {**_step3_signals(), "agent_environment": {"available": True, "check": {"named_total": 0}}})
    assert lone_condition(one, MODEL["steps"]) == ("layer-0-instructions.2", "instructions")
    assert headline(one, MODEL)["summary"] == "Step 1 · one change from Step 3: instruction file with a check command"


def test_no_shipped_model_doc_or_golden_names_agent_prs() -> None:
    listed = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z"], check=True, capture_output=True,
                            encoding="utf-8", errors="surrogateescape").stdout.split("\0")
    # the change notes and version histories record the old names on purpose
    skipped = ("skills/assess/changes/", ".assess/", "skills/assess/tests/test_cumulative_gates.py")
    named = [path for path in listed if path and not path.startswith(skipped)
             and (REPO / path).is_file() and (REPO / path).suffix in {".toml", ".md", ".svg", ".json", ".html", ".py"}
             and "agent PRs" in (REPO / path).read_text(encoding="utf-8", errors="replace")]
    assert named == []
