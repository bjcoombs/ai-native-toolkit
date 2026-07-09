"""Tests for the acceptance-contract complete gate.

`scripts/contract/complete_gate.py <run-id>` is the exit mirror of the start
gate (criterion 16). It delegates its accept/refuse decision to
`validate_completion.py`: it exits non-zero when the run has no completion
record, a record without verifier results, or a record the validator rejects;
it exits zero only when the validator certifies PASS.

No AI, no network - the acceptance floor's deterministic layer.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CONTRACT_DIR = REPO / "scripts" / "contract"
sys.path.insert(0, str(CONTRACT_DIR))

import complete_gate as cg  # noqa: E402

RUN_ID = "acceptance-contract"
GOOD_TOKEN = "tok-abc123-thisrun"


def write_provenance(contract_dir: Path, run_id: str, token: str) -> None:
    contract_dir.mkdir(parents=True, exist_ok=True)
    (contract_dir / ("%s.provenance.json" % run_id)).write_text(
        json.dumps({"run_id": run_id, "token": token}), encoding="utf-8"
    )


def write_record(contract_dir: Path, run_id: str, record: dict) -> None:
    contract_dir.mkdir(parents=True, exist_ok=True)
    (contract_dir / ("%s.completion.json" % run_id)).write_text(
        json.dumps(record), encoding="utf-8"
    )


def pass_record(**overrides):
    """A fully valid, PASS-worthy completion record (verifier ran, token issued)."""
    record = {
        "run_id": RUN_ID,
        "contract_hash": "sha256:deadbeef",
        "freeze_evidence": {
            "contract_hash": "sha256:deadbeef",
            "frozen_before_decomposition": True,
            "kill_test": {"null_artifact_all_fail": True, "sabotage_rejected": True},
        },
        "readiness_verdict": {"verdict": "ready", "source": "human"},
        "criteria_results": [
            {"id": "c1", "tier": 1, "result": "pass", "observation": "exit 0"},
        ],
        "couldnt_drive": [],
        "abort_events": [],
        "tier3_escalations": [],
        "verifier_provenance": {"token": GOOD_TOKEN, "run_id": RUN_ID, "spawned_by": "chokepoint"},
    }
    record.update(overrides)
    return record


@pytest.fixture
def contract_dir(tmp_path):
    d = tmp_path / "contract"
    write_provenance(d, RUN_ID, GOOD_TOKEN)
    return d


# --------------------------------------------------------------------------- #
# The four test-strategy cases (criterion 16).
# --------------------------------------------------------------------------- #


def test_no_record_refused(contract_dir):
    """No completion record -> exit non-zero."""
    rc = cg.main(["no-such-run", "--contract-dir", str(contract_dir)])
    assert rc != 0


def test_record_without_verifier_results_refused(contract_dir):
    """A record with freeze evidence but no verifier results (no provenance
    token) cannot pass completion: the validator stamps DEGRADED-custody."""
    rec = pass_record()
    del rec["criteria_results"]
    del rec["verifier_provenance"]
    write_record(contract_dir, RUN_ID, rec)
    rc = cg.main([RUN_ID, "--contract-dir", str(contract_dir)])
    assert rc != 0


def test_validator_rejected_record_refused(contract_dir):
    """A record the validator rejects (here: a tier-1 failure -> FAIL) does not
    pass the complete gate."""
    rec = pass_record(
        criteria_results=[{"id": "c1", "tier": 1, "result": "fail", "observation": "broken"}]
    )
    write_record(contract_dir, RUN_ID, rec)
    rc = cg.main([RUN_ID, "--contract-dir", str(contract_dir)])
    assert rc != 0


def test_validator_accepted_record_passes(contract_dir):
    """A record the validator certifies PASS -> exit 0."""
    write_record(contract_dir, RUN_ID, pass_record())
    rc = cg.main([RUN_ID, "--contract-dir", str(contract_dir)])
    assert rc == 0


# --------------------------------------------------------------------------- #
# Delegation + hardening.
# --------------------------------------------------------------------------- #


def test_skip_record_refused(contract_dir):
    """A signed-skip record (UNVERIFIED, can never certify) fails the complete
    gate - the exit mirror of the start gate's capped skip."""
    rec = {"run_id": RUN_ID, "operator_signoff": {"operator": "ben", "scope": "skip"}}
    write_record(contract_dir, RUN_ID, rec)
    rc = cg.main([RUN_ID, "--contract-dir", str(contract_dir)])
    assert rc != 0


def test_forged_token_refused(contract_dir):
    """A record whose token was never issued by the chokepoint fails the gate."""
    rec = pass_record()
    rec["verifier_provenance"]["token"] = "tok-forged"
    write_record(contract_dir, RUN_ID, rec)
    rc = cg.main([RUN_ID, "--contract-dir", str(contract_dir)])
    assert rc != 0


def test_contract_dir_env_var_override(contract_dir, monkeypatch):
    """The contract dir (holding both the record and the provenance
    side-channel) can be overridden by env var."""
    write_record(contract_dir, RUN_ID, pass_record())
    monkeypatch.setenv("ACCEPTANCE_CONTRACT_DIR", str(contract_dir))
    rc = cg.main([RUN_ID])
    assert rc == 0
