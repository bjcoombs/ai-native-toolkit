"""Tests for tier assignment (A4) and tier-3 escalation handling.

Covers PRD A4 (class tier defaults, conservative unknown default) and C2 /
success criterion 7 (a tier-3 criterion produces a real artifact file at the
recorded path; the run stays uncertified until operator sign-off, then certifies).

Two layers:

- **Pure logic (`tiers.py`).** Class-default assignment, path-safe artifact-path
  derivation, and escalation-entry assembly - all functions that RETURN data and
  write nothing.
- **Round-trip through the REAL chokepoint + validator.** The chokepoint
  (`spawn_verifier.ingest_verifier_results`) is the single writer: it
  materializes the tier-3 artifact file and stamps the escalation, and the real
  validator gates certification on the operator sign-off.

No AI, no network - the acceptance floor's deterministic layer.

PRD criteria coverage (auditable map; entrypoint ``pytest tests/contract/``):
- **Criterion 7 (artifact-file half):** a tier-3 escalation produces a REAL file
  at the canonical path recorded in ``tier3_escalations[]`` -
  ``test_tier3_criterion_materializes_artifact_file_at_recorded_path``; the run
  stays uncertified until sign-off, which then certifies -
  ``test_tier3_escalation_without_signoff_not_certified_then_signoff_certifies``
  (the validator-only view of the same behaviour is in
  ``test_completion_record.py``).
- **A4 tier defaults** underpinning the interactive tier-3 floor (criterion 13):
  ``test_class_tier_defaults_match_a4``,
  ``test_unknown_class_defaults_to_two_conservative``.
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
import tiers  # noqa: E402
import validate_completion as vc  # noqa: E402


# --------------------------------------------------------------------------- #
# Class tier defaults (A4).
# --------------------------------------------------------------------------- #


def test_class_tier_defaults_match_a4():
    """The importable A4 table: refactor->2, cli->1, interactive->3, report->2
    (report's conservative faithfulness default; citation-resolution tier-1 is
    the prose-guarded special case)."""
    assert tiers.CLASS_TIER_DEFAULTS == {
        "cli": 1,
        "refactor": 2,
        "report": 2,
        "interactive": 3,
    }


def test_default_tier_for_known_classes():
    assert tiers.default_tier_for_class("cli") == 1
    assert tiers.default_tier_for_class("refactor") == 2
    assert tiers.default_tier_for_class("report") == 2
    assert tiers.default_tier_for_class("interactive") == 3


@pytest.mark.parametrize("unknown", ["quantum", "", "CLI", "widget"])
def test_unknown_class_defaults_to_two_conservative(unknown):
    """An unrecognised class defaults to tier-2 (A4, conservative): never claim a
    stronger machine ceiling than we can justify for a class we do not model."""
    assert tiers.default_tier_for_class(unknown) == tiers.UNKNOWN_CLASS_DEFAULT_TIER == 2


# --------------------------------------------------------------------------- #
# Artifact-path derivation (path-safe).
# --------------------------------------------------------------------------- #


def test_tier3_artifact_path_shape(tmp_path):
    p = tiers.tier3_artifact_path("my-run", "C3", tmp_path)
    assert p == tmp_path / "my-run" / "tier3-C3.artifact"


def test_tier3_artifact_path_default_contract_dir():
    p = tiers.tier3_artifact_path("run", "c1")
    assert p == Path(".taskmaster/contract") / "run" / "tier3-c1.artifact"


@pytest.mark.parametrize("bad", ["../evil", "a/b", "x\\y", "..", ""])
def test_tier3_artifact_path_rejects_bad_run_id(bad):
    with pytest.raises(ValueError):
        tiers.tier3_artifact_path(bad, "C1")


@pytest.mark.parametrize("bad", ["../evil", "a/b", "x\\y", "..", ""])
def test_tier3_artifact_path_rejects_bad_criterion_id(bad):
    with pytest.raises(ValueError):
        tiers.tier3_artifact_path("run", bad)


# --------------------------------------------------------------------------- #
# Escalation-entry assembly (RETURNS data).
# --------------------------------------------------------------------------- #


def test_build_tier3_escalation_shape(tmp_path):
    entry = tiers.build_tier3_escalation("C3", "operator plays it", "run", tmp_path)
    assert entry == {
        "criterion_id": "C3",
        "artifact_path": str(tmp_path / "run" / "tier3-C3.artifact"),
        "observation": "operator plays it",
        "awaiting_signoff": True,
    }


def test_artifact_contents_names_run_and_criterion_and_observation():
    body = tiers.tier3_artifact_contents("run-x", "C3", "the motion feels right")
    assert "run-x" in body
    assert "C3" in body
    assert "the motion feels right" in body
    assert "operator_signoff" in body


# --------------------------------------------------------------------------- #
# Round-trip: chokepoint materializes the artifact; validator gates on sign-off.
# --------------------------------------------------------------------------- #

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


def _write_contract(tmp_path, name="run.contract.md"):
    contract = tmp_path / name
    contract.write_text(_INTERACTIVE_CONTRACT, encoding="utf-8")
    return contract


def _seed_freeze_and_readiness(cdir, run_id, contract_hash):
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


def _tier3_observations():
    """A verifier observation set: I1 passes, I2 escalates. The agent's proposed
    artifact path is deliberately non-canonical to prove the chokepoint replaces
    it with the canonical, path-safe location."""
    return sv.VerifierObservations(
        criteria_results=[
            {"id": "I1", "tier": 1, "result": "pass", "observation": "phase READY"},
            {"id": "I2", "tier": 3, "result": "escalated", "observation": "operator must play it"},
        ],
        tier3_escalations=[
            {
                "criterion_id": "I2",
                "artifact_path": "agent-proposed-anywhere.gif",
                "observation": "operator must play it",
            }
        ],
    )


def test_tier3_criterion_materializes_artifact_file_at_recorded_path(tmp_path):
    """Criterion 7 (structural half): a tier-3 escalation produces a REAL artifact
    file at the path recorded in tier3_escalations[], and the recorded path is the
    canonical path-safe one, not the agent's proposed value."""
    contract = _write_contract(tmp_path)
    cdir = tmp_path / "ct"
    spawn = sv.spawn_verifier(contract, tmp_path / "prod", contract_dir=cdir)
    _seed_freeze_and_readiness(cdir, "run", spawn.contract_hash)

    record = sv.ingest_verifier_results(spawn, _tier3_observations())

    entry = record["tier3_escalations"][0]
    assert entry["criterion_id"] == "I2"
    assert entry["awaiting_signoff"] is True
    recorded = Path(entry["artifact_path"])
    assert recorded.name == "tier3-I2.artifact"
    assert recorded.parent.name == "run"  # canonical <cdir>/<run-id>/, not agent path
    assert recorded.exists()  # a real file at the recorded path
    assert recorded.read_text(encoding="utf-8").strip()  # non-empty operator brief


def test_tier3_escalation_without_signoff_not_certified_then_signoff_certifies(tmp_path):
    """Criterion 7 (behavioural half): the run stays uncertified while the tier-3
    escalation awaits sign-off; recording operator_signoff certifies it."""
    contract = _write_contract(tmp_path)
    cdir = tmp_path / "ct"
    spawn = sv.spawn_verifier(contract, tmp_path / "prod", contract_dir=cdir)
    _seed_freeze_and_readiness(cdir, "run", spawn.contract_hash)

    record = sv.ingest_verifier_results(spawn, _tier3_observations())

    awaiting = vc.validate(record, cdir)
    assert awaiting.verdict == vc.VERDICT_AWAITING_TIER3
    assert not awaiting.certified

    record["operator_signoff"] = {"operator": "ben", "scope": "tier3", "note": "played it, feels right"}
    signed = vc.validate(record, cdir)
    assert signed.verdict == vc.VERDICT_PASS
    assert signed.certified


def test_reingest_preserves_existing_artifact_file(tmp_path):
    """A second verification run must not clobber an artifact the operator has
    already observed into: an existing artifact file is left untouched."""
    contract = _write_contract(tmp_path)
    cdir = tmp_path / "ct"
    spawn = sv.spawn_verifier(contract, tmp_path / "prod", contract_dir=cdir)
    _seed_freeze_and_readiness(cdir, "run", spawn.contract_hash)
    record = sv.ingest_verifier_results(spawn, _tier3_observations())

    recorded = Path(record["tier3_escalations"][0]["artifact_path"])
    recorded.write_text("OPERATOR: launched, played 2 min, motion reads correctly.", encoding="utf-8")

    spawn2 = sv.spawn_verifier(contract, tmp_path / "prod", contract_dir=cdir)
    sv.ingest_verifier_results(spawn2, _tier3_observations())
    assert recorded.read_text(encoding="utf-8").startswith("OPERATOR:")
