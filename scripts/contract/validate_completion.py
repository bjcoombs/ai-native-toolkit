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
- Non-empty ``couldnt_drive`` -> PARTIAL, never PASS (C3).
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


def validate(record: Dict[str, Any], provenance_dir: Optional[Any] = None) -> ValidationResult:
    """Validate a completion record. Pure over (record, provenance_dir)."""
    # 1. Schema. A malformed record is untrustworthy; refuse before logic.
    schema_errors = validate_against_schema(record)
    if schema_errors:
        return ValidationResult(
            verdict=VERDICT_REJECTED,
            certified=False,
            reasons=["schema violation: record does not satisfy completion.schema.json"],
            schema_errors=schema_errors,
        )

    stamps: List[str] = []
    reasons: List[str] = []

    # 2. Freeze evidence / signed-skip cap (A1, criterion 4).
    freeze = record.get("freeze_evidence")
    signed = _is_signed(record)
    if not freeze:
        if signed:
            stamps.append(STAMP_CONTRACT_SKIPPED)
            reasons.append(
                "no freeze evidence; signed skip capped at UNVERIFIED, can never certify PASS"
            )
            return ValidationResult(VERDICT_UNVERIFIED, False, stamps, reasons)
        reasons.append("no freeze evidence and no operator sign-off")
        return ValidationResult(VERDICT_REJECTED, False, stamps, reasons)

    # 3. Aborts (e.g. mid-run contract-hash mismatch, C5). A run that aborted
    #    cannot certify against a moved target.
    if record.get("abort_events"):
        reasons.append("abort_events recorded: run aborted, not certified")
        return ValidationResult(VERDICT_ABORTED, False, stamps, reasons)

    # 4. Decorrelation source stamp (A3, criterion 8). Non-blocking.
    source = (record.get("readiness_verdict") or {}).get("source")
    if source == "none":
        stamps.append(STAMP_DEGRADED)
        reasons.append("readiness source is none: no decorrelated review")

    # 5. Token authenticity / custody (C4, criteria 11 & 15). Blocking.
    token_ok, token_reason = check_token(record, provenance_dir)
    if not token_ok:
        stamps.append(STAMP_DEGRADED_CUSTODY)
        reasons.append(token_reason)

    # 6. Tier-3 escalations require operator sign-off (C2, criterion 7).
    tier3 = record.get("tier3_escalations") or []
    tier3_awaiting = bool(tier3) and not signed
    if tier3_awaiting:
        reasons.append("tier-3 escalation recorded without operator sign-off")

    # 7. Couldn't-drive honesty (C3). Blocking (PARTIAL, never PASS).
    couldnt = record.get("couldnt_drive") or []
    if couldnt:
        stamps.append(STAMP_PARTIAL)
        reasons.append("verifier could not drive: %s" % ", ".join(map(str, couldnt)))

    # 8. Tier-1 hard gate; tier-2 reports but never blocks in v1 (C2).
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
    elif couldnt:
        verdict = VERDICT_PARTIAL
    else:
        verdict = VERDICT_PASS

    return ValidationResult(verdict, verdict == VERDICT_PASS, stamps, reasons)


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
