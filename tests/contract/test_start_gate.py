"""Tests for the acceptance-contract start gate.

`scripts/contract/start_gate.py <run-id>` fails closed: it refuses to let a run
start (non-zero exit) unless the run has valid freeze evidence or a signed
operator skip, mirroring the fail-closed complete gate on the exit side
(criterion 4). The freeze check reuses `validate_completion`'s content check, so
freeze evidence whose kill test failed is not valid start evidence.

No AI, no network - the acceptance floor's deterministic layer.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# scripts/contract/ is not on the import path when pytest runs from the repo
# root (root has no pyproject). Insert it so `import start_gate` works both
# locally and in the `plugin contract pytest` CI job.
REPO = Path(__file__).resolve().parents[2]
CONTRACT_DIR = REPO / "scripts" / "contract"
sys.path.insert(0, str(CONTRACT_DIR))

import start_gate as sg  # noqa: E402

RUN_ID = "acceptance-contract"


def valid_freeze_record(**overrides):
    """A record carrying valid freeze evidence (frozen before decomposition,
    kill test passed). At start time it has no verifier results yet - that is
    the exit side's concern."""
    record = {
        "run_id": RUN_ID,
        "contract_hash": "sha256:deadbeef",
        "freeze_evidence": {
            "contract_hash": "sha256:deadbeef",
            "frozen_before_decomposition": True,
            "kill_test": {"null_artifact_all_fail": True, "sabotage_rejected": True},
        },
    }
    record.update(overrides)
    return record


def write_record(contract_dir: Path, run_id: str, record: dict) -> None:
    contract_dir.mkdir(parents=True, exist_ok=True)
    (contract_dir / ("%s.completion.json" % run_id)).write_text(
        json.dumps(record), encoding="utf-8"
    )


@pytest.fixture
def contract_dir(tmp_path):
    return tmp_path / "contract"


# --------------------------------------------------------------------------- #
# The three test-strategy cases.
# --------------------------------------------------------------------------- #


def test_no_evidence_refused(contract_dir):
    """No completion record at all -> no freeze, no skip -> non-zero exit."""
    contract_dir.mkdir(parents=True, exist_ok=True)
    rc = sg.main([RUN_ID, "--contract-dir", str(contract_dir)])
    assert rc != 0


def test_freeze_evidence_starts(contract_dir):
    """Valid freeze evidence -> exit 0."""
    write_record(contract_dir, RUN_ID, valid_freeze_record())
    rc = sg.main([RUN_ID, "--contract-dir", str(contract_dir)])
    assert rc == 0


def test_signed_skip_starts_with_unverified_warning(contract_dir, capsys):
    """A signed skip (operator_signoff, no freeze) -> exit 0 WITH a loud
    UNVERIFIED warning that says the run can never certify PASS."""
    rec = {"run_id": RUN_ID, "operator_signoff": {"operator": "ben", "scope": "skip"}}
    write_record(contract_dir, RUN_ID, rec)
    rc = sg.main([RUN_ID, "--contract-dir", str(contract_dir)])
    assert rc == 0
    err = capsys.readouterr().err
    assert "UNVERIFIED" in err
    assert "never" in err.lower() and "pass" in err.lower()


# --------------------------------------------------------------------------- #
# Consistency with the validator + hardening.
# --------------------------------------------------------------------------- #


def test_missing_record_refused(contract_dir):
    """A run with no completion record file cannot start."""
    contract_dir.mkdir(parents=True, exist_ok=True)
    rc = sg.main(["never-created", "--contract-dir", str(contract_dir)])
    assert rc != 0


def test_freeze_with_failed_kill_test_refused(contract_dir):
    """Freeze evidence whose kill test failed is NOT valid start evidence
    (consistent with validate_completion): refuse when there is no signed skip
    to fall back on."""
    rec = valid_freeze_record()
    rec["freeze_evidence"]["kill_test"]["null_artifact_all_fail"] = False
    write_record(contract_dir, RUN_ID, rec)
    rc = sg.main([RUN_ID, "--contract-dir", str(contract_dir)])
    assert rc != 0


def test_freeze_without_contract_hash_refused(contract_dir):
    """Freeze evidence lacking the frozen contract_hash is not a valid freeze."""
    rec = valid_freeze_record()
    del rec["freeze_evidence"]["contract_hash"]
    write_record(contract_dir, RUN_ID, rec)
    rc = sg.main([RUN_ID, "--contract-dir", str(contract_dir)])
    assert rc != 0


def test_invalid_freeze_with_signed_skip_starts_uncertifiable(contract_dir, capsys):
    """A broken freeze alone refuses, but with a signed operator skip the run may
    start. The warning must be verdict-accurate: a present-but-invalid freeze is
    REJECTED at exit (not merely capped at UNVERIFIED like a no-freeze skip), so
    the message names REJECT and still states the run can never certify PASS."""
    rec = valid_freeze_record(operator_signoff={"operator": "ben", "scope": "skip"})
    rec["freeze_evidence"]["kill_test"]["null_artifact_all_fail"] = False
    write_record(contract_dir, RUN_ID, rec)
    rc = sg.main([RUN_ID, "--contract-dir", str(contract_dir)])
    assert rc == 0
    err = capsys.readouterr().err
    assert "REJECT" in err
    assert "never" in err.lower() and "pass" in err.lower()
    # It must NOT mislabel this present-but-invalid-freeze path as UNVERIFIED,
    # which is the no-freeze-skip verdict.
    assert "UNVERIFIED" not in err


def test_malformed_record_refused(contract_dir):
    """A completion record that is not valid JSON cannot back a start."""
    contract_dir.mkdir(parents=True, exist_ok=True)
    (contract_dir / ("%s.completion.json" % RUN_ID)).write_text("{not json", encoding="utf-8")
    rc = sg.main([RUN_ID, "--contract-dir", str(contract_dir)])
    assert rc != 0


def test_contract_dir_env_var_override(contract_dir, monkeypatch):
    """The contract dir can be overridden by env var (mirroring how
    validate_completion resolves provenance_dir)."""
    write_record(contract_dir, RUN_ID, valid_freeze_record())
    monkeypatch.setenv("ACCEPTANCE_CONTRACT_DIR", str(contract_dir))
    rc = sg.main([RUN_ID])
    assert rc == 0
