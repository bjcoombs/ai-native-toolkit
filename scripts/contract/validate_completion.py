#!/usr/bin/env python3
"""Validate an acceptance-contract completion record.

This module is the SINGLE component that decides whether a per-run completion
record (`.taskmaster/contract/<run-id>.completion.json`) may claim ``PASS``.
Everything else in the acceptance-contract machinery (start gate, chokepoint,
complete gate, canaries) either feeds this validator or shells out to it.

Design (see prd-acceptance-contract.md F1, C2-C5, A1/A3, success criteria
4, 7, 8, 15):

- No freeze evidence and no operator sign-off  -> REJECTED.
- No freeze evidence but a signed skip         -> UNVERIFIED + ``contract-skipped``;
  a skip can never certify PASS (criterion 4).
- Verifier-results token not authentically issued by the chokepoint for THIS
  run (forged / copied-from-another-run / stale) -> ``DEGRADED-custody``, no PASS.
  ONLY the exact side-channel-matching token certifies (criterion 15). A
  validator that trusts any non-empty token is wrong by construction.
- Tier-3 escalation recorded without operator sign-off -> AWAITING-TIER3-SIGNOFF;
  recording the sign-off certifies (criterion 7).
- Readiness ``source: none`` -> ``DEGRADED: no decorrelated review`` stamp;
  ``source: human`` (or ``non-claude-model``) -> no such stamp (criterion 8).
- Non-empty ``couldnt_drive`` -> PARTIAL, never PASS (C3). A per-criterion
  ``result: undriven`` is the same signal and forces PARTIAL identically.
- Freeze evidence is checked by CONTENT, not presence: a freeze whose kill test
  failed (``null_artifact_all_fail`` not true, or ``sabotage_rejected`` false) or
  that lacks the frozen ``contract_hash`` is not valid freeze evidence -> REJECTED.
- A criterion with ``result: escalated`` but no matching ``tier3_escalations``
  entry is an internally-inconsistent record -> REJECTED.
- Any schema violation -> REJECTED.
- Tier-2 results REPORT but never BLOCK in v1 (unarmed by design, C2): a tier-2
  fail without a calibration record is noted in the output, not hard-failed.

The module is import-clean: ``validate()`` is a pure function over a record dict
plus the provenance-side-channel directory. A CLI entrypoint
(``python validate_completion.py <record-path>``) exits 0 when the record
certifies PASS (accepted) and non-zero otherwise (refused), so downstream gate
scripts can both import ``validate`` and shell out to the CLI.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_PATH = Path(__file__).resolve().parent / "completion.schema.json"

# Default location of the provenance side-channel, relative to the run cwd.
# The chokepoint writes `<provenance-dir>/<run-id>.provenance.json`.
DEFAULT_PROVENANCE_DIR = Path(".taskmaster/contract")

# Stamp strings (kept as literals so downstream greps and tests can match them).
STAMP_DEGRADED = "DEGRADED: no decorrelated review"
STAMP_DEGRADED_CUSTODY = "DEGRADED-custody: non-independent verification"
STAMP_PARTIAL = "PARTIAL"
STAMP_UNVERIFIED = "UNVERIFIED"
STAMP_CONTRACT_SKIPPED = "contract-skipped"

# Verdicts. PASS is the only certifying verdict; every other verdict refuses.
VERDICT_PASS = "PASS"
VERDICT_PARTIAL = "PARTIAL"
VERDICT_UNVERIFIED = "UNVERIFIED"
VERDICT_REJECTED = "REJECTED"
VERDICT_ABORTED = "ABORTED"
VERDICT_FAIL = "FAIL"
VERDICT_DEGRADED_CUSTODY = "DEGRADED-custody"
VERDICT_AWAITING_TIER3 = "AWAITING-TIER3-SIGNOFF"


@dataclass
class ValidationResult:
    """The verdict for one completion record.

    ``certified`` is true only for ``PASS``; the CLI maps it to exit code 0.
    """

    verdict: str
    certified: bool
    stamps: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    schema_errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "certified": self.certified,
            "stamps": self.stamps,
            "reasons": self.reasons,
            "schema_errors": self.schema_errors,
        }


# --------------------------------------------------------------------------- #
# Dependency-free JSON-Schema-subset validator.
#
# The completion schema is enforced with a small structural checker rather than
# the `jsonschema` package so this module stays pure stdlib: the root pytest job
# runs `uv run --with pytest`, adding no other dependency, and the acceptance
# floor is meant to be deterministic and model-free. The checker supports the
# subset used by completion.schema.json: type (incl. unions), required,
# properties, items, enum, minLength.
# --------------------------------------------------------------------------- #

_JSON_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
}


def _type_matches(value: Any, json_type: str) -> bool:
    if json_type == "integer":
        # bool is a subclass of int in Python; exclude it.
        return isinstance(value, int) and not isinstance(value, bool)
    if json_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    py = _JSON_TYPES.get(json_type)
    if py is None:
        # Unknown type keyword: don't spuriously reject.
        return True
    if py is dict or py is list:
        return isinstance(value, py)
    if py is str:
        return isinstance(value, str)
    return isinstance(value, py)


def _check_type(value: Any, spec: Any, path: str, errors: List[str]) -> bool:
    type_spec = spec.get("type")
    if type_spec is None:
        return True
    types = type_spec if isinstance(type_spec, list) else [type_spec]
    if any(_type_matches(value, t) for t in types):
        return True
    errors.append("%s: expected type %s, got %s" % (path, types, type(value).__name__))
    return False


def _validate_node(value: Any, spec: Dict[str, Any], path: str, errors: List[str]) -> None:
    if not _check_type(value, spec, path, errors):
        return  # type is wrong; deeper checks would be noise.

    enum = spec.get("enum")
    if enum is not None and value not in enum:
        errors.append("%s: %r not in allowed values %s" % (path, value, enum))

    if isinstance(value, str) and "minLength" in spec and len(value) < spec["minLength"]:
        errors.append("%s: shorter than minLength %d" % (path, spec["minLength"]))

    if isinstance(value, dict):
        for req in spec.get("required", []):
            if req not in value:
                errors.append("%s: missing required property %r" % (path, req))
        props = spec.get("properties", {})
        for key, sub_spec in props.items():
            if key in value:
                _validate_node(value[key], sub_spec, "%s.%s" % (path, key), errors)

    if isinstance(value, list):
        item_spec = spec.get("items")
        if item_spec:
            for i, item in enumerate(value):
                _validate_node(item, item_spec, "%s[%d]" % (path, i), errors)


def load_schema() -> Dict[str, Any]:
    with SCHEMA_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def validate_against_schema(record: Any, schema: Optional[Dict[str, Any]] = None) -> List[str]:
    """Return a list of schema-violation strings (empty == valid)."""
    if schema is None:
        schema = load_schema()
    errors: List[str] = []
    _validate_node(record, schema, "$", errors)
    return errors


# --------------------------------------------------------------------------- #
# Token authenticity against the provenance side-channel.
# --------------------------------------------------------------------------- #


def _resolve_provenance_dir(provenance_dir: Optional[Any]) -> Path:
    if provenance_dir is None:
        return DEFAULT_PROVENANCE_DIR
    return Path(provenance_dir)


def check_token(record: Dict[str, Any], provenance_dir: Optional[Any] = None) -> Tuple[bool, str]:
    """Return (authentic, reason).

    Authentic only when the record's verifier-results token exactly matches the
    chokepoint-written side-channel entry for THIS run. Forged (absent from the
    side-channel), copied (side-channel run_id differs), and stale (side-channel
    holds a newer token) all return False.
    """
    prov = record.get("verifier_provenance") or {}
    token = prov.get("token")
    if not token:
        return False, "no chokepoint-issued provenance token (record has no verifier results)"

    run_id = record.get("run_id")
    # run_id is interpolated into a filesystem path; a value containing path
    # separators or ".." would escape the provenance dir and let a forged
    # record point at an attacker-chosen side-channel file, defeating the
    # criterion-15 token-authenticity guarantee. Reject those before building
    # the path.
    if (
        not isinstance(run_id, str)
        or not run_id
        or "/" in run_id
        or "\\" in run_id
        or ".." in run_id
    ):
        return False, "invalid run_id: path separators are not allowed"
    path = _resolve_provenance_dir(provenance_dir) / ("%s.provenance.json" % run_id)
    if not path.exists():
        return False, "no provenance side-channel entry for run %r (forged or unissued token)" % run_id
    try:
        with path.open(encoding="utf-8") as fh:
            side = json.load(fh)
    except (OSError, ValueError) as exc:
        return False, "provenance side-channel unreadable: %s" % exc

    if side.get("run_id") != run_id:
        return False, "provenance run_id mismatch (token copied from another run)"
    if side.get("token") != token:
        return False, "token does not match current side-channel entry (forged, copied, or stale)"
    return True, "token authentic for this run"


# --------------------------------------------------------------------------- #
# Core validation.
# --------------------------------------------------------------------------- #


def _is_signed(record: Dict[str, Any]) -> bool:
    signoff = record.get("operator_signoff")
    return signoff not in (None, False, {}, "")


def _tier_failures(record: Dict[str, Any], tier: int) -> List[str]:
    return [
        c.get("id", "<unnamed>")
        for c in record.get("criteria_results", [])
        if c.get("tier") == tier and c.get("result") == "fail"
    ]


def _criteria_by_result(record: Dict[str, Any], result_value: str) -> List[str]:
    """IDs of every criterion whose ``result`` equals ``result_value`` (any tier)."""
    return [
        c.get("id", "<unnamed>")
        for c in record.get("criteria_results", [])
        if c.get("result") == result_value
    ]


def _freeze_valid(freeze: Any) -> Tuple[bool, str]:
    """Return (valid, reason) for freeze evidence.

    Presence alone is not enough (A1/B1/B2): freeze evidence only counts when it
    carries the frozen contract hash AND its kill-test outcome indicates success
    - the null artifact failed every criterion and any authored sabotage was
    rejected. A freeze whose kill test failed proves nothing (the contract does
    not even reject a null artifact), so it cannot back a PASS. ``sabotage_rejected``
    is ``null`` when no sabotage was authored (acceptable); only an explicit
    ``False`` marks a kill-test failure.
    """
    if not isinstance(freeze, dict):
        return False, "freeze_evidence is not an object: not valid freeze evidence"
    if not freeze.get("contract_hash"):
        return False, "freeze_evidence lacks contract_hash: not a valid freeze"
    kill = freeze.get("kill_test")
    if not isinstance(kill, dict):
        return False, "freeze_evidence has no kill_test record: kill test not run"
    if kill.get("null_artifact_all_fail") is not True:
        return False, "kill test failed: null artifact did not fail every criterion"
    if kill.get("sabotage_rejected") is False:
        return False, "kill test failed: authored sabotage was not rejected"
    return True, "freeze evidence valid: kill test passed"


def _orphan_escalations(record: Dict[str, Any]) -> List[str]:
    """IDs of criteria marked ``result: escalated`` with no matching
    ``tier3_escalations`` entry.

    The schema enum allows ``escalated``, but an escalated criterion with no
    escalation artifact recorded is an internally-inconsistent record: it claims
    a criterion was escalated yet gives the operator nothing to observe. That is
    a lying map of intent, refused outright (REJECTED) rather than parked in
    AWAITING, which would imply a legitimate escalation merely awaiting sign-off.
    """
    tier3 = record.get("tier3_escalations") or []
    tier3_ids = {e.get("criterion_id") for e in tier3 if isinstance(e, dict)}
    return [cid for cid in _criteria_by_result(record, "escalated") if cid not in tier3_ids]


def _refusal_gate(
    record: Dict[str, Any], signed: bool, freeze: Any
) -> Optional[ValidationResult]:
    """Hard refusals that short-circuit before any stamp/verdict logic.

    Returns a refusing ``ValidationResult`` when the record cannot even be scored
    (missing/invalid freeze evidence, an abort, or an inconsistent escalation),
    else ``None`` to continue to certification.
    """
    # Freeze evidence / signed-skip cap (A1, criterion 4).
    if not freeze:
        if signed:
            return ValidationResult(
                VERDICT_UNVERIFIED,
                False,
                stamps=[STAMP_CONTRACT_SKIPPED],
                reasons=["no freeze evidence; signed skip capped at UNVERIFIED, can never certify PASS"],
            )
        return ValidationResult(
            VERDICT_REJECTED, False, reasons=["no freeze evidence and no operator sign-off"]
        )

    # Freeze-evidence content (A1/B1/B2). Presence is not enough: a freeze whose
    # kill test failed, or that lacks the frozen contract hash, cannot back PASS.
    freeze_valid, freeze_reason = _freeze_valid(freeze)
    if not freeze_valid:
        return ValidationResult(
            VERDICT_REJECTED, False, reasons=["invalid freeze evidence: %s" % freeze_reason]
        )

    # Aborts (e.g. mid-run contract-hash mismatch, C5): cannot certify a moved target.
    if record.get("abort_events"):
        return ValidationResult(
            VERDICT_ABORTED, False, reasons=["abort_events recorded: run aborted, not certified"]
        )

    # Per-criterion `result` aggregate consistency (C3, criterion 7).
    orphan = _orphan_escalations(record)
    if orphan:
        return ValidationResult(
            VERDICT_REJECTED,
            False,
            reasons=[
                "criterion(s) marked result=escalated with no matching tier3_escalations "
                "entry: %s" % ", ".join(map(str, orphan))
            ],
        )
    return None


def _certify(
    record: Dict[str, Any], signed: bool, provenance_dir: Optional[Any]
) -> ValidationResult:
    """Score a record that cleared the hard-refusal gates into its final verdict."""
    stamps: List[str] = []
    reasons: List[str] = []

    # Decorrelation source stamp (A3, criterion 8). Non-blocking.
    source = (record.get("readiness_verdict") or {}).get("source")
    if source == "none":
        stamps.append(STAMP_DEGRADED)
        reasons.append("readiness source is none: no decorrelated review")

    # Token authenticity / custody (C4, criteria 11 & 15). Blocking.
    token_ok, token_reason = check_token(record, provenance_dir)
    if not token_ok:
        stamps.append(STAMP_DEGRADED_CUSTODY)
        reasons.append(token_reason)

    # Tier-3 escalations require operator sign-off (C2, criterion 7).
    tier3_awaiting = bool(record.get("tier3_escalations")) and not signed
    if tier3_awaiting:
        reasons.append("tier-3 escalation recorded without operator sign-off")

    # Couldn't-drive honesty (C3). Blocking (PARTIAL, never PASS). A criterion
    # whose `result` is `undriven` is the per-criterion twin of a couldnt_drive
    # entry, so it forces PARTIAL identically (a pass with undriven criteria is
    # PARTIAL, never PASS).
    couldnt = record.get("couldnt_drive") or []
    undriven_ids = _criteria_by_result(record, "undriven")
    partial = bool(couldnt) or bool(undriven_ids)
    if partial:
        stamps.append(STAMP_PARTIAL)
    if couldnt:
        reasons.append("verifier could not drive: %s" % ", ".join(map(str, couldnt)))
    if undriven_ids:
        reasons.append("criterion(s) reported result=undriven: %s" % ", ".join(map(str, undriven_ids)))

    # Tier-1 hard gate; tier-2 reports but never blocks in v1 (C2).
    tier1_fail = _tier_failures(record, 1)
    tier2_fail = _tier_failures(record, 2)
    if tier2_fail:
        reasons.append(
            "tier-2 fail(s) reported (%s) but tier-2 is unarmed in v1: reports, does not block"
            % ", ".join(tier2_fail)
        )

    # Verdict by precedence. Custody undermines trust in every result, so it
    # ranks first; then real failures, then awaiting sign-off, then partial.
    if not token_ok:
        verdict = VERDICT_DEGRADED_CUSTODY
    elif tier1_fail:
        reasons.append("tier-1 criterion(s) failed: %s" % ", ".join(tier1_fail))
        verdict = VERDICT_FAIL
    elif tier3_awaiting:
        verdict = VERDICT_AWAITING_TIER3
    elif partial:
        verdict = VERDICT_PARTIAL
    else:
        verdict = VERDICT_PASS

    return ValidationResult(verdict, verdict == VERDICT_PASS, stamps, reasons)


def freeze_evidence_valid(record: Dict[str, Any]) -> Tuple[bool, str]:
    """Public: is this record's ``freeze_evidence`` valid freeze evidence?

    Thin wrapper over the same content check the certification path uses
    (``_freeze_valid``), exposed so the start gate can decide "may this run
    start?" with the identical rule the validator applies at exit - freeze
    evidence whose kill test failed, or that lacks the frozen contract hash, is
    not valid start evidence any more than it is valid exit evidence. Reusing
    this keeps the two gates from drifting apart.
    """
    return _freeze_valid(record.get("freeze_evidence"))


def is_signed(record: Dict[str, Any]) -> bool:
    """Public: does this record carry an operator sign-off / signed skip?

    Exposes the same predicate the validator uses so the start gate recognizes a
    capped skip by the identical rule (A1).
    """
    return _is_signed(record)


def validate(record: Dict[str, Any], provenance_dir: Optional[Any] = None) -> ValidationResult:
    """Validate a completion record. Pure over (record, provenance_dir)."""
    # Schema. A malformed record is untrustworthy; refuse before logic.
    schema_errors = validate_against_schema(record)
    if schema_errors:
        return ValidationResult(
            verdict=VERDICT_REJECTED,
            certified=False,
            reasons=["schema violation: record does not satisfy completion.schema.json"],
            schema_errors=schema_errors,
        )

    signed = _is_signed(record)
    refusal = _refusal_gate(record, signed, record.get("freeze_evidence"))
    if refusal is not None:
        return refusal

    return _certify(record, signed, provenance_dir)


def validate_path(record_path: Any, provenance_dir: Optional[Any] = None) -> ValidationResult:
    """Load a record from disk and validate it. Malformed JSON -> REJECTED."""
    path = Path(record_path)
    try:
        with path.open(encoding="utf-8") as fh:
            record = json.load(fh)
    except FileNotFoundError:
        return ValidationResult(
            VERDICT_REJECTED, False, reasons=["completion record not found: %s" % path]
        )
    except OSError as exc:
        # PermissionError, IsADirectoryError, broken symlinks, etc. An
        # unreadable record cannot be trusted, so refuse cleanly rather than
        # crash. FileNotFoundError is handled above for a clearer message.
        return ValidationResult(
            VERDICT_REJECTED, False, reasons=["completion record could not be read: %s" % exc]
        )
    except ValueError as exc:
        return ValidationResult(
            VERDICT_REJECTED, False, reasons=["completion record is not valid JSON: %s" % exc]
        )
    if not isinstance(record, dict):
        return ValidationResult(
            VERDICT_REJECTED, False, reasons=["completion record must be a JSON object"]
        )
    return validate(record, provenance_dir)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate an acceptance-contract completion record.")
    parser.add_argument("record", help="path to <run-id>.completion.json")
    parser.add_argument(
        "--provenance-dir",
        default=None,
        help="directory holding <run-id>.provenance.json (default: .taskmaster/contract)",
    )
    args = parser.parse_args(argv)

    result = validate_path(args.record, args.provenance_dir)
    print(json.dumps(result.to_dict(), indent=2))
    # Exit 0 == accepted (certifies PASS); non-zero == refused.
    return 0 if result.certified else 1


if __name__ == "__main__":
    sys.exit(main())
