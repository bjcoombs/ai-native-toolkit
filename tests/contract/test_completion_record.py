"""Tests for the acceptance-contract completion-record validator.

Covers the seven test-strategy cases plus the forged/copied/stale token cases
that criterion 15 (token authenticity) demands. No AI, no network - the
acceptance floor's deterministic layer.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# scripts/contract/ is not on the import path when pytest runs from the repo
# root (root has no pyproject). Insert it so `import validate_completion` works
# both locally and in the `plugin contract pytest` CI job.
REPO = Path(__file__).resolve().parents[2]
CONTRACT_DIR = REPO / "scripts" / "contract"
sys.path.insert(0, str(CONTRACT_DIR))

import validate_completion as vc  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixtures / builders
# --------------------------------------------------------------------------- #

RUN_ID = "acceptance-contract"
GOOD_TOKEN = "tok-abc123-thisrun"


def write_provenance(prov_dir: Path, run_id: str, token: str) -> None:
    prov_dir.mkdir(parents=True, exist_ok=True)
    (prov_dir / ("%s.provenance.json" % run_id)).write_text(
        json.dumps({"run_id": run_id, "token": token}), encoding="utf-8"
    )


def base_record(**overrides):
    """A fully valid, PASS-worthy record. Override fields per test."""
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
            {"id": "c1", "tier": 1, "result": "pass", "observation": "exit 0 on real input"},
            {"id": "c2", "tier": 1, "result": "pass", "observation": "output matches"},
        ],
        "couldnt_drive": [],
        "stamps": [],
        "abort_events": [],
        "tier3_escalations": [],
        "verifier_provenance": {"token": GOOD_TOKEN, "run_id": RUN_ID, "spawned_by": "chokepoint"},
    }
    record.update(overrides)
    return record


@pytest.fixture
def prov_dir(tmp_path):
    d = tmp_path / "contract"
    write_provenance(d, RUN_ID, GOOD_TOKEN)
    return d


# --------------------------------------------------------------------------- #
# The seven test-strategy cases
# --------------------------------------------------------------------------- #


def test_1_no_freeze_no_signoff_rejected(prov_dir):
    """(1) no freeze_evidence + no signoff -> REJECTED."""
    rec = base_record()
    del rec["freeze_evidence"]
    result = vc.validate(rec, prov_dir)
    assert result.verdict == vc.VERDICT_REJECTED
    assert not result.certified


def test_2_signoff_no_freeze_unverified(prov_dir):
    """(2) signoff + no freeze_evidence -> UNVERIFIED + contract-skipped, never PASS."""
    rec = base_record(operator_signoff={"operator": "ben", "scope": "skip"})
    del rec["freeze_evidence"]
    result = vc.validate(rec, prov_dir)
    assert result.verdict == vc.VERDICT_UNVERIFIED
    assert not result.certified
    assert vc.STAMP_CONTRACT_SKIPPED in result.stamps


def test_3_token_mismatch_degraded_custody(prov_dir):
    """(3) token mismatch -> DEGRADED-custody, no PASS."""
    rec = base_record()
    rec["verifier_provenance"]["token"] = "tok-wrong"
    result = vc.validate(rec, prov_dir)
    assert result.verdict == vc.VERDICT_DEGRADED_CUSTODY
    assert not result.certified
    assert vc.STAMP_DEGRADED_CUSTODY in result.stamps


def test_4_source_none_degraded_stamp(prov_dir):
    """(4) readiness source none -> DEGRADED stamp."""
    rec = base_record(readiness_verdict={"verdict": "ready", "source": "none"})
    result = vc.validate(rec, prov_dir)
    assert vc.STAMP_DEGRADED in result.stamps


def test_4b_source_human_no_degraded_stamp(prov_dir):
    """(4, negative half of criterion 8) source human -> no DEGRADED stamp, still PASS."""
    rec = base_record(readiness_verdict={"verdict": "ready", "source": "human"})
    result = vc.validate(rec, prov_dir)
    assert vc.STAMP_DEGRADED not in result.stamps
    assert result.verdict == vc.VERDICT_PASS


def test_5_couldnt_drive_partial(prov_dir):
    """(5) couldnt_drive non-empty -> PARTIAL, never PASS."""
    rec = base_record(couldnt_drive=["c2"])
    result = vc.validate(rec, prov_dir)
    assert result.verdict == vc.VERDICT_PARTIAL
    assert not result.certified
    assert vc.STAMP_PARTIAL in result.stamps


def test_6_tier3_escalation_without_signoff_awaiting(prov_dir):
    """(6) tier3 escalation without signoff -> AWAITING-TIER3-SIGNOFF."""
    rec = base_record(
        tier3_escalations=[
            {"criterion_id": "c3", "artifact_path": "artifacts/playthrough.gif", "observation": "launched"}
        ]
    )
    result = vc.validate(rec, prov_dir)
    assert result.verdict == vc.VERDICT_AWAITING_TIER3
    assert not result.certified


def test_6b_tier3_escalation_with_signoff_certifies(prov_dir):
    """(6, positive) recording operator sign-off certifies the tier-3 run."""
    rec = base_record(
        tier3_escalations=[
            {"criterion_id": "c3", "artifact_path": "artifacts/playthrough.gif", "observation": "launched"}
        ],
        operator_signoff={"operator": "ben", "scope": "tier3", "note": "played, feels right"},
    )
    result = vc.validate(rec, prov_dir)
    assert result.verdict == vc.VERDICT_PASS
    assert result.certified


def test_7_fully_valid_record_pass(prov_dir):
    """(7) fully valid record -> PASS with no blocking stamps."""
    result = vc.validate(base_record(), prov_dir)
    assert result.verdict == vc.VERDICT_PASS
    assert result.certified
    assert vc.STAMP_DEGRADED_CUSTODY not in result.stamps
    assert vc.STAMP_PARTIAL not in result.stamps


# --------------------------------------------------------------------------- #
# Criterion 15: token authenticity - forged / copied / stale
# --------------------------------------------------------------------------- #


def test_15_forged_token_rejected(prov_dir):
    """A token that was never issued (no side-channel match) cannot certify."""
    rec = base_record()
    rec["verifier_provenance"]["token"] = "tok-forged-not-in-sidechannel"
    result = vc.validate(rec, prov_dir)
    assert result.verdict == vc.VERDICT_DEGRADED_CUSTODY
    assert not result.certified


def test_15_empty_token_rejected(prov_dir):
    """A non-empty token is NOT sufficient; an absent token must fail too."""
    rec = base_record()
    rec["verifier_provenance"]["token"] = ""
    result = vc.validate(rec, prov_dir)
    assert result.verdict == vc.VERDICT_DEGRADED_CUSTODY
    assert not result.certified


def test_15_any_nonempty_token_not_trusted(tmp_path):
    """A validator accepting any non-empty token fails criterion 15: with NO
    side-channel entry at all, a plausible token must still be refused."""
    empty_dir = tmp_path / "contract"
    empty_dir.mkdir()
    rec = base_record()
    rec["verifier_provenance"]["token"] = "looks-real-but-unissued"
    result = vc.validate(rec, empty_dir)
    assert result.verdict == vc.VERDICT_DEGRADED_CUSTODY
    assert not result.certified


def test_15_copied_from_another_run_rejected(tmp_path):
    """A token authentically issued to run B, pasted into run A's record.
    Run A's side-channel holds A's own token, so the copy mismatches."""
    prov = tmp_path / "contract"
    write_provenance(prov, "run-a", "tok-a-authentic")
    write_provenance(prov, "run-b", "tok-b-authentic")
    rec = base_record(run_id="run-a")
    rec["verifier_provenance"] = {"token": "tok-b-authentic", "run_id": "run-a"}
    result = vc.validate(rec, prov)
    assert result.verdict == vc.VERDICT_DEGRADED_CUSTODY
    assert not result.certified


def test_15_stale_token_rejected(tmp_path):
    """A token valid in an earlier verification run, reused after a newer run
    issued a fresh token. The side-channel now holds the newer token."""
    prov = tmp_path / "contract"
    write_provenance(prov, RUN_ID, "tok-new-after-rerun")  # newest issued token
    rec = base_record()
    rec["verifier_provenance"]["token"] = "tok-old-stale"  # reused prior token
    result = vc.validate(rec, prov)
    assert result.verdict == vc.VERDICT_DEGRADED_CUSTODY
    assert not result.certified


def test_15_exact_matching_token_certifies(prov_dir):
    """Only the exact side-channel-matching token for THIS run certifies."""
    result = vc.validate(base_record(), prov_dir)
    assert result.verdict == vc.VERDICT_PASS
    assert result.certified


# --------------------------------------------------------------------------- #
# Additional coverage: schema, tier-1 gate, tier-2 non-blocking, abort, CLI
# --------------------------------------------------------------------------- #


def test_schema_violation_rejected(prov_dir):
    """A structurally invalid record is refused before any logic runs."""
    rec = base_record()
    rec["readiness_verdict"]["source"] = "baboon"  # not in the enum
    result = vc.validate(rec, prov_dir)
    assert result.verdict == vc.VERDICT_REJECTED
    assert result.schema_errors


def test_missing_run_id_schema_rejected(prov_dir):
    rec = base_record()
    del rec["run_id"]
    result = vc.validate(rec, prov_dir)
    assert result.verdict == vc.VERDICT_REJECTED


def test_tier1_fail_does_not_pass(prov_dir):
    """A tier-1 failure (the jet-fighters broken-build shape) is detected, not PASS."""
    rec = base_record(
        criteria_results=[
            {"id": "launch", "tier": 1, "result": "fail", "observation": "input never wired"},
        ]
    )
    result = vc.validate(rec, prov_dir)
    assert result.verdict == vc.VERDICT_FAIL
    assert not result.certified


def test_tier2_fail_reports_but_does_not_block(prov_dir):
    """Tier-2 is unarmed in v1: a tier-2 fail without calibration must not hard-fail."""
    rec = base_record(
        criteria_results=[
            {"id": "c1", "tier": 1, "result": "pass", "observation": "ok"},
            {"id": "equiv", "tier": 2, "result": "fail", "observation": "diverged"},
        ]
    )
    result = vc.validate(rec, prov_dir)
    assert result.verdict == vc.VERDICT_PASS
    assert result.certified
    assert any("tier-2" in r for r in result.reasons)


def test_abort_events_block_certification(prov_dir):
    """A mid-run contract edit (hash mismatch) records an abort; no certification."""
    rec = base_record(abort_events=[{"kind": "hash-mismatch", "detail": "contract moved mid-run"}])
    result = vc.validate(rec, prov_dir)
    assert result.verdict == vc.VERDICT_ABORTED
    assert not result.certified


def test_cli_exit_codes(tmp_path, prov_dir, capsys):
    """CLI: exit 0 on PASS (accepted), non-zero otherwise (refused)."""
    good = tmp_path / "good.completion.json"
    good.write_text(json.dumps(base_record()), encoding="utf-8")
    assert vc.main([str(good), "--provenance-dir", str(prov_dir)]) == 0

    bad = tmp_path / "bad.completion.json"
    rec = base_record()
    del rec["freeze_evidence"]
    bad.write_text(json.dumps(rec), encoding="utf-8")
    assert vc.main([str(bad), "--provenance-dir", str(prov_dir)]) != 0


def test_cli_missing_file_refused(tmp_path):
    assert vc.main([str(tmp_path / "nope.json")]) != 0


def test_schema_file_is_valid_json_and_self_consistent():
    """completion.schema.json loads and the validator's default schema is it."""
    schema = vc.load_schema()
    assert schema["type"] == "object"
    assert "run_id" in schema["required"]
    # A trivially valid record satisfies the schema.
    assert vc.validate_against_schema({"run_id": "x"}, schema) == []


# --------------------------------------------------------------------------- #
# Hardening: boolean tier, run_id path traversal, unreadable record file.
# --------------------------------------------------------------------------- #


def test_boolean_tier_rejected_by_schema(prov_dir):
    """`tier: true` must not sneak past the schema. Python's `in` treats
    True == 1, so an enum-only check accepts a boolean; the explicit integer
    type is what refuses it."""
    rec = base_record(
        criteria_results=[
            {"id": "c1", "tier": True, "result": "pass", "observation": "boolean tier"},
        ]
    )
    result = vc.validate(rec, prov_dir)
    assert result.verdict == vc.VERDICT_REJECTED
    assert result.schema_errors
    assert any("tier" in err for err in result.schema_errors)


def test_run_id_path_traversal_not_authentic(tmp_path):
    """A run_id with path separators must be rejected before the provenance
    path is built, so a forged record cannot point at an attacker-chosen
    side-channel file outside the provenance dir."""
    prov = tmp_path / "contract"
    # Plant an attacker-controlled provenance file one level up from prov.
    evil_run = "../evil"
    write_provenance(tmp_path, "evil", "tok-attacker")
    (tmp_path / "evil.provenance.json").write_text(
        json.dumps({"run_id": evil_run, "token": "tok-attacker"}), encoding="utf-8"
    )
    prov.mkdir(parents=True, exist_ok=True)

    rec = base_record(run_id=evil_run)
    rec["verifier_provenance"] = {"token": "tok-attacker", "run_id": evil_run}

    authentic, reason = vc.check_token(rec, prov)
    assert authentic is False
    assert "run_id" in reason

    # And the full validation cannot certify.
    result = vc.validate(rec, prov)
    assert not result.certified


def test_unreadable_record_file_rejected(tmp_path):
    """A record path that exists but cannot be read as a file (here, a
    directory) yields a clean REJECTED, not a crash."""
    record_path = tmp_path / "record.completion.json"
    record_path.mkdir()  # a directory where a file is expected
    result = vc.validate_path(record_path)
    assert result.verdict == vc.VERDICT_REJECTED
    assert not result.certified
