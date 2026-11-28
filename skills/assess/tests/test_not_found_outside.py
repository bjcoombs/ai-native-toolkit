"""#1662: practices a team can set outside the repository read "not found in the repository".

An unmet agent-permission or secrets-guard reading only shows that the scan found no trace in
the commit; managed settings or the host's push protection can still hold the practice. The
wording lives in ``lib/scoring/next_step_guidance_outside.py`` and reaches the top card's Blocks
line (``headline.gate_note``), the fleet team page (``fleet_next_steps``) and the fleet report's
absence line (``fleet_not_measured.absence_line``). Fixtures are invented repositories with no
settings file, no pre-commit config and no CI.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

import lib.fleet.fleet_next_steps as ns
import lib.scoring.level_tally as lt
from lib.agents.agent_ops import scan_agent_ops
from lib.fleet.fleet_not_measured import InPlace, absence_line
from lib.scoring.headline import gate_change, gate_note
from lib.scoring.levels import MaturityModel
from lib.scoring.next_step_guidance_outside import (
    AGENT_PERMISSIONS_ELSEWHERE, ELSEWHERE_CONDITIONS, ELSEWHERE_ROWS, ELSEWHERE_SIGNALS, NOT_FOUND,
    SECRETS_GUARD_ELSEWHERE, not_found_note)
from lib.secrets_guard import scan_secrets_guard

PERMISSIONS = "agent_ops.summary.permissions_encoded"
CONFIGURED = "secrets_guard.configured"
ENFORCED = "secrets_guard.enforcement.present"


def _bare_repo(root: Path) -> Path:
    """An invented repository holding one README and nothing else: no trace of either practice."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text("# invented-service\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    return root


def _gated_model() -> MaturityModel:
    """The default model with one Step gating each practice, so each unmet reading is a blocker."""
    model: Any = dict(lt.default_model())
    model["steps"] = [
        {"step": 1, "name": "Assisted", "gates": []},
        {"step": 2, "name": "Guarded", "gates": [
            {"id": "agent_permissions", "row": "layer-8-agent-ops", "level": 1},
            {"id": "secrets_guard", "row": "secrets-guard", "level": 2}]},
    ]
    return model


def _scans(repo: Path) -> dict[str, Any]:
    return {"agent_ops": scan_agent_ops(repo), "secrets_guard": scan_secrets_guard(repo)}


# --- the wording -----------------------------------------------------------------------


def test_each_practice_names_where_else_it_can_be_set():
    assert NOT_FOUND == "not found in the repository"
    assert ELSEWHERE_SIGNALS == {PERMISSIONS: AGENT_PERMISSIONS_ELSEWHERE, CONFIGURED: SECRETS_GUARD_ELSEWHERE,
                                 ENFORCED: SECRETS_GUARD_ELSEWHERE}
    assert "managed settings" in AGENT_PERMISSIONS_ELSEWHERE and "--managed-settings" in AGENT_PERMISSIONS_ELSEWHERE
    assert "push protection" in SECRETS_GUARD_ELSEWHERE


def test_the_secrets_wording_never_asks_for_a_platform_read():
    """An unmet secrets guard on GitHub means --platform already read push protection off; elsewhere
    there is no such read. Either way, advice to run it would mislead (#1688 review)."""
    assert "--platform" not in SECRETS_GUARD_ELSEWHERE
    assert not_found_note(AGENT_PERMISSIONS_ELSEWHERE) == f"{NOT_FOUND}; {AGENT_PERMISSIONS_ELSEWHERE}"


def test_the_condition_and_row_keys_name_default_model_ids():
    ids = {c["id"] for c in lt.level_result(lt.default_model(), {})["conditions"]}
    assert set(ELSEWHERE_CONDITIONS) <= ids
    rows = {row["id"] for row in lt.default_model()["rows"]}
    assert set(ELSEWHERE_ROWS) <= rows


# --- the issue's success criterion: a fixture with no repository evidence for each row --


def test_a_bare_repository_reads_both_practices_unmet(tmp_path: Path):
    """Chesterton check: the fixture holds no evidence, and both rows read unmet, not unknown."""
    result = lt.level_result(lt.default_model(), _scans(_bare_repo(tmp_path / "svc")))
    states = {c["id"]: c["state"] for c in result["conditions"]}
    assert {cid: states[cid] for cid in ELSEWHERE_CONDITIONS} == dict.fromkeys(ELSEWHERE_CONDITIONS, "unmet")


def test_the_blocks_line_says_not_found_in_the_repository_for_each_row(tmp_path: Path):
    result = lt.level_result(_gated_model(), _scans(_bare_repo(tmp_path / "svc")))
    assert result["next_step"] is not None
    blockers = {b["gate"]: b for b in result["next_step"]["blockers"]}
    assert set(blockers) == {"agent_permissions", "secrets_guard"}
    assert gate_note(blockers["agent_permissions"]) == f" ({NOT_FOUND}; {AGENT_PERMISSIONS_ELSEWHERE})"
    assert gate_note(blockers["secrets_guard"]) == f" ({NOT_FOUND}; {SECRETS_GUARD_ELSEWHERE})"
    assert gate_change(blockers["secrets_guard"]).endswith(f"({NOT_FOUND}; {SECRETS_GUARD_ELSEWHERE})")


def test_a_not_measured_blocker_keeps_its_not_measured_note():
    """#1350's not-measured readings stand: the new wording is for an unmet reading only."""
    blocker: Any = {"kind": "gate", "gate": "secrets_guard", "condition": "secrets-guard.1",
                    "state": "not_measured", "reason": "ci_includes_unresolved"}
    assert NOT_FOUND not in gate_note(blocker)
    assert gate_note(blocker).startswith(" (not measured here:")


def test_an_unmet_gate_on_another_row_keeps_no_note():
    blocker: Any = {"kind": "gate", "gate": "ci_runs_tests", "condition": "layer-5-ci.1", "state": "unmet"}
    assert gate_note(blocker) == ""


# --- the fleet views -------------------------------------------------------------------


@pytest.mark.parametrize("signal, where", [(PERMISSIONS, AGENT_PERMISSIONS_ELSEWHERE),
                                           (CONFIGURED, SECRETS_GUARD_ELSEWHERE),
                                           (ENFORCED, SECRETS_GUARD_ELSEWHERE)])
def test_the_team_page_lists_a_false_reading_as_not_found_and_names_where_else(signal: str, where: str):
    outcome: Any = {"signal": signal, "op": "eq", "value": True, "status": "unmet", "actual": False}
    assert ns.holding_entry("grp/svc", outcome, []) == f"`grp/svc` (now false: {NOT_FOUND})"
    assert ns.elsewhere_lines(signal) == [f"   - Can also be set outside the repository: {where}."]


def test_the_team_page_keeps_other_signals_as_they_were():
    outcome: Any = {"signal": "ci_checks.enforced_check_runs", "op": "gte", "value": 1,
                    "status": "unmet", "actual": 0}
    assert ns.holding_entry("grp/svc", outcome, []) == "`grp/svc` (now 0)"
    assert ns.elsewhere_lines("ci_checks.enforced_check_runs") == []


def test_the_fleet_absence_line_says_not_found_in_the_repository_for_the_secrets_guard():
    p: InPlace = {"row": "secrets-guard", "at_top": 0, "measured": 3, "not_measured": 0, "not_measured_reasons": []}
    assert absence_line(p, "Secrets guard") == (
        f"- Secrets guard: {NOT_FOUND} at its top level in any of the 3 measured repositories; "
        f"{SECRETS_GUARD_ELSEWHERE}.")


def test_the_fleet_absence_line_keeps_not_measured_counts_for_the_secrets_guard():
    p: InPlace = {"row": "secrets-guard", "at_top": 0, "measured": 2, "not_measured": 1,
                  "not_measured_reasons": [{"reason": "ci_includes_unresolved", "count": 1}]}
    line = absence_line(p, "Secrets guard")
    assert "1 not measured (`ci_includes_unresolved` 1)" in line and line.endswith(f"{SECRETS_GUARD_ELSEWHERE}.")
