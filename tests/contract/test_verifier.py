"""Cold verifier logic tests (PRD C3, C5, success criteria 6 & 9).

Three strategy scenarios, plus parser and assembly units:

1. **Couldn't-drive honesty (criterion 6), positive and negative together.** A
   run with an undrivable criterion certifies ``PARTIAL`` with a non-empty
   ``couldnt_drive``; an otherwise-identical run where every criterion drives and
   passes certifies ``PASS`` with an empty ``couldnt_drive``.
2. **Hash check at exit (criterion 9).** Editing the frozen contract after spawn
   makes the exit re-hash mismatch: the run aborts with a non-empty
   ``abort_events`` and cannot certify.
3. **Custody (criterion 11).** Verifier output that reaches a completion record
   without a chokepoint-issued provenance token is ``DEGRADED-custody`` and
   cannot certify.

The round-trips run through the REAL chokepoint (`spawn_verifier`) and the REAL
validator (`validate_completion`): no AI, no network - the deterministic floor.

PRD criteria coverage (auditable map; entrypoint ``pytest tests/contract/``):
- **Criterion 6:** PARTIAL-with-non-empty-couldnt_drive AND
  PASS-with-empty-couldnt_drive asserted together -
  ``test_partial_and_pass_asserted_together``.
- **Criterion 9:** a mid-run contract edit makes the exit re-hash mismatch and
  aborts (``abort_events`` non-empty, no certification) -
  ``test_mid_run_contract_edit_aborts``; the no-edit control is
  ``test_no_edit_does_not_abort``.
- **Criterion 11 (behavioural, verifier seam):** verifier output that reaches a
  record without a chokepoint token is ``DEGRADED-custody`` -
  ``test_verifier_output_without_token_is_rejected``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CONTRACT_DIR = REPO / "scripts" / "contract"
sys.path.insert(0, str(CONTRACT_DIR))

import spawn_verifier as sv  # noqa: E402
import validate_completion as vc  # noqa: E402
import verifier as vf  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixtures / helpers.
# --------------------------------------------------------------------------- #

_CLI_CONTRACT = (
    "# Acceptance contract\n\n"
    "Prose the parser ignores.\n\n"
    "```yaml\n"
    "class: cli\n"
    "criteria:\n"
    "  - id: C1\n"
    "    tier: 1\n"
    '    action: "Run: python3 tool.py < input.txt ; capture exit code."\n'
    '    observation: "Exit code is 0."\n'
    "  - id: C2\n"
    "    tier: 1\n"
    '    action: "Run the same command and capture stdout."\n'
    '    observation: "stdout matches expected_stdout.txt."\n'
    "```\n"
)

_INTERACTIVE_CONTRACT = (
    "# Interactive contract\n\n"
    "```yaml\n"
    "class: interactive\n"
    "criteria:\n"
    "  - id: I1\n"
    "    tier: 1\n"
    '    action: "Launch and read getState()."\n'
    '    observation: "phase is READY."\n'
    "  - id: I2\n"
    "    tier: 3\n"
    '    action: "Operator plays it briefly."\n'
    '    observation: "It reads and feels like the intended toy."\n'
    "```\n"
)


def _write_contract(tmp_path: Path, body: str = _CLI_CONTRACT, name: str = "run.contract.md") -> Path:
    contract = tmp_path / name
    contract.write_text(body, encoding="utf-8")
    return contract


def _seed_freeze_and_readiness(cdir: Path, run_id: str, contract_hash: str) -> None:
    """Pre-write the fields other stages own, so the round-trip can reach PASS."""
    cdir.mkdir(parents=True, exist_ok=True)
    record = {
        "run_id": run_id,
        "freeze_evidence": {
            "contract_hash": contract_hash,
            "frozen_before_decomposition": True,
            "kill_test": {"null_artifact_all_fail": True, "sabotage_rejected": True},
        },
        "readiness_verdict": {"verdict": "ready", "source": "human"},
    }
    (cdir / (run_id + ".completion.json")).write_text(json.dumps(record), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Contract parsing.
# --------------------------------------------------------------------------- #


def test_parse_cli_contract():
    contract = vf.parse_contract(_CLI_CONTRACT)
    assert contract.cls == "cli"
    assert [c.id for c in contract.criteria] == ["C1", "C2"]
    assert all(c.tier == 1 for c in contract.criteria)
    # A quoted value containing ': ' is preserved intact (split-on-first-colon).
    assert contract.criteria[0].action.startswith("Run: python3")


def test_parse_real_known_good_fixture():
    fixture = REPO / "tests" / "canaries" / "known-good" / "contract.md"
    contract = vf.parse_contract(fixture.read_text(encoding="utf-8"))
    assert contract.cls == "cli"
    assert [c.id for c in contract.criteria] == ["KG1", "KG2", "KG3"]


def test_parse_real_interactive_fixture_keeps_tiers():
    fixture = REPO / "tests" / "canaries" / "jet-fighters" / "contract.md"
    contract = vf.parse_contract(fixture.read_text(encoding="utf-8"))
    assert contract.cls == "interactive"
    tiers = {c.id: c.tier for c in contract.criteria}
    assert tiers["JF1"] == 1 and tiers["JF5"] == 3


def test_parse_rejects_zero_yaml_blocks():
    with pytest.raises(vf.ContractParseError):
        vf.parse_contract("# no fenced yaml here\njust prose\n")


def test_parse_rejects_two_yaml_blocks():
    doubled = _CLI_CONTRACT + "\n" + _CLI_CONTRACT
    with pytest.raises(vf.ContractParseError):
        vf.parse_contract(doubled)


def test_parse_rejects_missing_field():
    body = (
        "```yaml\nclass: cli\ncriteria:\n"
        "  - id: C1\n    tier: 1\n"
        '    action: "do it"\n```\n'  # no observation
    )
    with pytest.raises(vf.ContractParseError):
        vf.parse_contract(body)


def test_parse_rejects_bad_tier():
    body = (
        "```yaml\nclass: cli\ncriteria:\n"
        "  - id: C1\n    tier: 9\n"
        '    action: "do it"\n    observation: "saw it"\n```\n'
    )
    with pytest.raises(vf.ContractParseError):
        vf.parse_contract(body)


def test_parse_rejects_unknown_class():
    body = (
        "```yaml\nclass: quantum\ncriteria:\n"
        "  - id: C1\n    tier: 1\n"
        '    action: "do it"\n    observation: "saw it"\n```\n'
    )
    with pytest.raises(vf.ContractParseError):
        vf.parse_contract(body)


# --------------------------------------------------------------------------- #
# Assembly units.
# --------------------------------------------------------------------------- #


def test_tier_comes_from_contract_not_agent():
    """The frozen contract's tier is authoritative; the agent supplies none."""
    contract = vf.parse_contract(_INTERACTIVE_CONTRACT)
    obs = vf.assemble_observations(
        contract,
        [
            vf.DrivenResult("I1", vf.OUTCOME_PASS, "phase READY"),
            vf.DrivenResult("I2", vf.OUTCOME_ESCALATED, "handed to operator", artifact_path="art.txt"),
        ],
    )
    tiers = {c["id"]: c["tier"] for c in obs.criteria_results}
    assert tiers == {"I1": 1, "I2": 3}


def test_unreported_criterion_is_couldnt_drive():
    contract = vf.parse_contract(_CLI_CONTRACT)
    obs = vf.assemble_observations(contract, [vf.DrivenResult("C1", vf.OUTCOME_PASS)])
    assert obs.couldnt_drive == ["C2"]
    c2 = next(c for c in obs.criteria_results if c["id"] == "C2")
    assert c2["result"] == vf.OUTCOME_UNDRIVEN


def test_escalated_criterion_produces_tier3_entry():
    contract = vf.parse_contract(_INTERACTIVE_CONTRACT)
    obs = vf.assemble_observations(
        contract,
        [
            vf.DrivenResult("I1", vf.OUTCOME_PASS),
            vf.DrivenResult("I2", vf.OUTCOME_ESCALATED, "operator output", artifact_path="rec.gif"),
        ],
    )
    assert obs.tier3_escalations == [
        {"criterion_id": "I2", "artifact_path": "rec.gif", "observation": "operator output"}
    ]


def test_escalated_without_artifact_is_rejected():
    contract = vf.parse_contract(_INTERACTIVE_CONTRACT)
    with pytest.raises(vf.VerifierError):
        vf.assemble_observations(
            contract,
            [
                vf.DrivenResult("I1", vf.OUTCOME_PASS),
                vf.DrivenResult("I2", vf.OUTCOME_ESCALATED, "no artifact"),
            ],
        )


def test_invalid_outcome_is_rejected():
    contract = vf.parse_contract(_CLI_CONTRACT)
    with pytest.raises(vf.VerifierError):
        vf.assemble_observations(contract, [vf.DrivenResult("C1", "maybe")])


# --------------------------------------------------------------------------- #
# Scenario 1: couldn't-drive honesty (criterion 6), PARTIAL and PASS together.
# --------------------------------------------------------------------------- #


def test_partial_and_pass_asserted_together(tmp_path):
    contract_file = _write_contract(tmp_path)
    cdir = tmp_path / "ct"

    # (a) An undrivable criterion -> PARTIAL + non-empty couldnt_drive.
    spawn_a = sv.spawn_verifier(contract_file, tmp_path / "prod", contract_dir=cdir)
    _seed_freeze_and_readiness(cdir, "run", spawn_a.contract_hash)
    record_a = vf.verify_and_ingest(
        spawn_a,
        [
            vf.DrivenResult("C1", vf.OUTCOME_PASS, "exit 0"),
            vf.DrivenResult("C2", vf.OUTCOME_UNDRIVEN, "no runtime to drive it"),
        ],
    )
    result_a = vc.validate(record_a, cdir)
    assert result_a.verdict == vc.VERDICT_PARTIAL
    assert not result_a.certified
    assert record_a["couldnt_drive"] == ["C2"]

    # (b) Every criterion drives and passes -> PASS + empty couldnt_drive.
    spawn_b = sv.spawn_verifier(contract_file, tmp_path / "prod", contract_dir=cdir)
    record_b = vf.verify_and_ingest(
        spawn_b,
        [
            vf.DrivenResult("C1", vf.OUTCOME_PASS, "exit 0"),
            vf.DrivenResult("C2", vf.OUTCOME_PASS, "stdout matched"),
        ],
    )
    result_b = vc.validate(record_b, cdir)
    assert result_b.verdict == vc.VERDICT_PASS
    assert result_b.certified
    assert record_b["couldnt_drive"] == []


# --------------------------------------------------------------------------- #
# Scenario 2: hash check at exit (criterion 9).
# --------------------------------------------------------------------------- #


def test_mid_run_contract_edit_aborts(tmp_path):
    contract_file = _write_contract(tmp_path)
    cdir = tmp_path / "ct"
    spawn = sv.spawn_verifier(contract_file, tmp_path / "prod", contract_dir=cdir)
    _seed_freeze_and_readiness(cdir, "run", spawn.contract_hash)

    # Tamper with the frozen contract after spawn recorded its hash.
    contract_file.write_text(
        _CLI_CONTRACT.replace("Exit code is 0.", "Exit code is anything."), encoding="utf-8"
    )

    obs = vf.run_verifier(spawn, [vf.DrivenResult("C1", vf.OUTCOME_PASS)])
    assert obs.abort_events, "a mid-run contract edit must record an abort event"
    assert obs.criteria_results == []  # a moved target is never graded

    record = sv.ingest_verifier_results(spawn, obs)
    result = vc.validate(record, cdir)
    assert result.verdict == vc.VERDICT_ABORTED
    assert not result.certified


def test_no_edit_does_not_abort(tmp_path):
    contract_file = _write_contract(tmp_path)
    cdir = tmp_path / "ct"
    spawn = sv.spawn_verifier(contract_file, tmp_path / "prod", contract_dir=cdir)
    obs = vf.run_verifier(spawn, [vf.DrivenResult("C1", vf.OUTCOME_PASS)])
    assert obs.abort_events == []
    assert [c["id"] for c in obs.criteria_results] == ["C1", "C2"]


# --------------------------------------------------------------------------- #
# Scenario 3: custody - verifier output without a provenance token is rejected.
# --------------------------------------------------------------------------- #


def test_verifier_output_without_token_is_rejected(tmp_path):
    contract_file = _write_contract(tmp_path)
    cdir = tmp_path / "ct"
    spawn = sv.spawn_verifier(contract_file, tmp_path / "prod", contract_dir=cdir)
    _seed_freeze_and_readiness(cdir, "run", spawn.contract_hash)

    # The verifier assembles results, but they reach a record WITHOUT going
    # through the chokepoint's token-stamping seam (no verifier_provenance).
    obs = vf.run_verifier(
        spawn,
        [vf.DrivenResult("C1", vf.OUTCOME_PASS), vf.DrivenResult("C2", vf.OUTCOME_PASS)],
    )
    hand_written = {
        "run_id": "run",
        "freeze_evidence": {
            "contract_hash": spawn.contract_hash,
            "frozen_before_decomposition": True,
            "kill_test": {"null_artifact_all_fail": True, "sabotage_rejected": True},
        },
        "readiness_verdict": {"verdict": "ready", "source": "human"},
        "criteria_results": obs.criteria_results,
        # No verifier_provenance: results bypassed the chokepoint.
    }
    result = vc.validate(hand_written, cdir)
    assert result.verdict == vc.VERDICT_DEGRADED_CUSTODY
    assert not result.certified
    assert vc.STAMP_DEGRADED_CUSTODY in result.stamps
