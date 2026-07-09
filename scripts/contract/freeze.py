#!/usr/bin/env python3
"""Freeze an acceptance contract, or refuse with a named reason.

`freeze.py` is the freeze-time gate (PRD prd-acceptance-contract.md B1/B3, A4,
success criteria 3, 13, 14). It refuses to freeze a contract unless all of:

1. **B1 red-contract-first.** Every criterion FAILS against the class's null
   artifact. Any criterion that PASSES against the null artifact is vacuous
   (satisfiable by absence - the jet-fighters pathology) and the contract is
   kicked back to authoring, naming the offending criterion. The null-artifact
   execution is agent-driven in a real run (a cold agent drives each criterion
   against the class's null artifact); its per-criterion outcomes enter this gate
   through an explicit, validated *kill-test results* input - never a
   self-attested default. When the results are absent, the gate FAILS CLOSED: it
   cannot prove red-contract-first, so it refuses.
2. **Structural class floor (criterion 13).** An interactive/visual contract with
   zero tier-3 criteria is refused with the reason named - a cold agent cannot
   observe the perceptual residue, so the human launch cannot be tier-1'd away.
3. **Downgrade justification (A4).** A criterion assigned a tier below its class
   default requires written justification inside the contract file; upgrading is
   free.

On success it records freeze evidence - contract sha256, `frozen_at`, and the
kill-test outcome - into `.taskmaster/contract/<run-id>.completion.json` in the
exact shape `validate_completion.py` accepts (`freeze_evidence.contract_hash` +
`freeze_evidence.kill_test.{null_artifact_all_fail, sabotage_rejected}`). Freeze
evidence is this gate's ONLY write into the completion record; every other field
is owned by later stages and is preserved untouched when the record already
exists.

Discrimination is by REAL logic, never by fixture identity: the gate parses the
contract and consumes kill-test results, and never looks at a fixture name. The
canary harness (task 8) feeds it per-run generated blind contract pairs; this
module has no allow-list to match.

Pure, pytest-able surface (deterministic layer): `parse_contract_text`,
`parse_contract_file`, `structural_floor_reasons`, `downgrade_reasons`,
`evaluate_kill_test`, `contract_sha256`, `write_freeze_evidence`, and the
orchestrating `freeze`. Only the agent-driven kill-test outcomes cross the
boundary as data.

The parser is stdlib-only (a small purpose-built reader for the constrained
contract grammar documented in `tests/canaries/README.md`) so behaviour is
identical locally and in the `plugin contract pytest` CI job, which runs
`uv run --with pytest pytest tests/` with no PyYAML available.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# The four contract classes (PRD A4 / tests/canaries/README.md).
CLASS_NAMES = ("cli", "interactive", "report", "refactor")
VALID_TIERS = (1, 2, 3)

# Class-default tiers whose downgrade is machine-enforced here (A4). Only classes
# whose default sits above the tier-1 machine floor appear: a refactor criterion
# is behavioural-equivalence-judged (tier-2) by default, so calling it tier-1
# claims a binary check for something that needs equivalence judging - a real
# weakening that must carry written justification. `cli` is listed at its tier-1
# floor for completeness (nothing can sit below it, so it never flags).
#
# `interactive` and `report` are intentionally ABSENT: interactive's tier-3
# default is machine-backstopped structurally instead (criterion 13 - an
# interactive contract must CONTAIN a tier-3, rather than every criterion being
# tier-3, because its headless-drivable checks legitimately sit at tier-1); and
# report-class faithfulness is prose-guarded only per the PRD's honest-narrowness
# note, not machine-enforced. Encoding those as per-criterion downgrades would
# false-refuse the sound interactive/report contracts that carry legitimate
# tier-1 criteria.
DOWNGRADE_DEFAULT_TIER = {"cli": 1, "refactor": 2}

# Default completion-record / provenance location, relative to the run cwd.
DEFAULT_CONTRACT_DIR = Path(".taskmaster/contract")

_YAML_BLOCK = re.compile(r"^```ya?ml[ \t]*\n(.*?)^```[ \t]*$", re.DOTALL | re.MULTILINE)


class ContractParseError(ValueError):
    """The contract file is not well-formed against the documented grammar."""


class KillTestError(ValueError):
    """The kill-test results input is missing, malformed, or does not bind."""


@dataclass
class Criterion:
    id: str
    tier: int
    action: str
    observation: str
    justification: Optional[str] = None


@dataclass
class Contract:
    cls: str
    criteria: List[Criterion]


@dataclass
class FreezeResult:
    """Outcome of a freeze attempt. `frozen` maps to CLI exit code 0."""

    frozen: bool
    reasons: List[str] = field(default_factory=list)
    contract_hash: Optional[str] = None
    record_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "frozen": self.frozen,
            "reasons": self.reasons,
            "contract_hash": self.contract_hash,
            "record_path": self.record_path,
        }


# --------------------------------------------------------------------------- #
# Contract parsing (stdlib-only reader for the documented grammar).
# --------------------------------------------------------------------------- #


def _extract_yaml_block(text: str) -> str:
    matches = _YAML_BLOCK.findall(text)
    if len(matches) != 1:
        raise ContractParseError(
            "expected exactly one fenced yaml block, found %d" % len(matches)
        )
    return matches[0]


def _split_key_val(line: str) -> Tuple[str, str]:
    if ":" not in line:
        raise ContractParseError("expected 'key: value', got %r" % line)
    key, _, val = line.partition(":")
    return key.strip(), val.strip()


def _parse_scalar(raw: str) -> str:
    val = raw.strip()
    if len(val) >= 2 and val[0] in "\"'" and val[-1] == val[0]:
        return val[1:-1]
    return val


def _parse_field(criterion: Dict[str, Any], key: str, raw: str) -> None:
    scalar = _parse_scalar(raw)
    if key == "tier":
        try:
            criterion["tier"] = int(scalar)
        except ValueError as exc:
            raise ContractParseError("tier is not an integer: %r" % scalar) from exc
    else:
        criterion[key] = scalar


def _consume_line(state: Dict[str, Any], raw_line: str) -> None:
    stripped = raw_line.strip()
    indent = len(raw_line) - len(raw_line.lstrip(" "))
    if indent == 0:
        key, val = _split_key_val(stripped)
        if key == "class":
            state["cls"] = _parse_scalar(val)
            state["in_criteria"] = False
        elif key == "criteria":
            state["in_criteria"] = True
        else:
            raise ContractParseError("unexpected top-level key %r" % key)
        return
    if not state["in_criteria"]:
        raise ContractParseError("indented line outside criteria: %r" % raw_line)
    if stripped.startswith("-"):
        current: Dict[str, Any] = {}
        state["criteria"].append(current)
        item = stripped[1:].strip()
        if item:
            key, val = _split_key_val(item)
            _parse_field(current, key, val)
        return
    if not state["criteria"]:
        raise ContractParseError("criterion field before any list item: %r" % raw_line)
    key, val = _split_key_val(stripped)
    _parse_field(state["criteria"][-1], key, val)


def _build_criterion(raw: Dict[str, Any]) -> Criterion:
    for required in ("id", "tier", "action", "observation"):
        if required not in raw:
            raise ContractParseError(
                "criterion %r missing required field %r" % (raw.get("id", "<unnamed>"), required)
            )
    tier = raw["tier"]
    if tier not in VALID_TIERS:
        raise ContractParseError("criterion %r has invalid tier %r" % (raw["id"], tier))
    justification = raw.get("justification")
    return Criterion(
        id=str(raw["id"]),
        tier=int(tier),
        action=str(raw["action"]),
        observation=str(raw["observation"]),
        justification=str(justification) if justification is not None else None,
    )


def parse_contract_text(text: str) -> Contract:
    """Parse a contract markdown string into a `Contract`.

    Raises `ContractParseError` on any structural violation of the grammar in
    `tests/canaries/README.md` (missing block, missing keys, bad tier/class,
    duplicate criterion id).
    """
    block = _extract_yaml_block(text)
    state: Dict[str, Any] = {"cls": None, "criteria": [], "in_criteria": False}
    for raw_line in block.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        _consume_line(state, raw_line)

    cls = state["cls"]
    if cls not in CLASS_NAMES:
        raise ContractParseError("class must be one of %s, got %r" % (list(CLASS_NAMES), cls))
    raw_criteria = state["criteria"]
    if not raw_criteria:
        raise ContractParseError("criteria is empty: a contract needs at least one criterion")

    criteria = [_build_criterion(raw) for raw in raw_criteria]
    seen: Dict[str, int] = {}
    for crit in criteria:
        seen[crit.id] = seen.get(crit.id, 0) + 1
    dupes = sorted(cid for cid, n in seen.items() if n > 1)
    if dupes:
        raise ContractParseError("duplicate criterion id(s): %s" % ", ".join(dupes))
    return Contract(cls=cls, criteria=criteria)


def parse_contract_file(path: Any) -> Contract:
    return parse_contract_text(Path(path).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Structural checks (pure, deterministic).
# --------------------------------------------------------------------------- #


def structural_floor_reasons(contract: Contract) -> List[str]:
    """Criterion 13 (PRD B3): an interactive/visual contract must contain at
    least one tier-3 criterion or freeze refuses with the reason named."""
    if contract.cls == "interactive" and not any(c.tier == 3 for c in contract.criteria):
        return [
            "interactive/visual contract has zero tier-3 criteria: a cold agent "
            "cannot observe the perceptual residue, so the human launch cannot be "
            "tier-1'd away (PRD B3 / criterion 13). Add at least one tier-3 criterion."
        ]
    return []


def downgrade_reasons(contract: Contract) -> List[str]:
    """A4: a criterion assigned a tier below its class default requires written
    justification inside the contract file; upgrading is free."""
    default = DOWNGRADE_DEFAULT_TIER.get(contract.cls)
    if default is None:
        return []
    reasons = []
    for crit in contract.criteria:
        justified = bool(crit.justification and crit.justification.strip())
        if crit.tier < default and not justified:
            reasons.append(
                "criterion %s downgrades below the %s class default "
                "(tier %d < tier %d) without written justification (A4)"
                % (crit.id, contract.cls, crit.tier, default)
            )
    return reasons


# --------------------------------------------------------------------------- #
# Kill-test results (agent-driven input; validated, never defaulted).
# --------------------------------------------------------------------------- #


def load_kill_test(path: Any) -> Dict[str, Any]:
    """Load and JSON-parse a kill-test results file. Raises `KillTestError` when
    missing, unreadable, or not a JSON object - the gate fails closed on any of
    these rather than assuming a passing kill test."""
    p = Path(path)
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise KillTestError("kill-test results unreadable: %s" % exc) from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise KillTestError("kill-test results are not valid JSON: %s" % exc) from exc
    if not isinstance(data, dict):
        raise KillTestError("kill-test results must be a JSON object")
    return data


def _result_map(kill_test: Dict[str, Any]) -> Dict[str, Any]:
    results = kill_test.get("results")
    if not isinstance(results, list):
        raise KillTestError("kill-test results.results must be a list")
    out: Dict[str, Any] = {}
    for entry in results:
        if not isinstance(entry, dict) or "id" not in entry:
            raise KillTestError("each kill-test result needs an 'id'")
        rid = str(entry["id"])
        if rid in out:
            raise KillTestError(
                "duplicate kill-test result id %r: a later entry would silently "
                "override an earlier one (e.g. a vacuous pass masking a fail)" % rid
            )
        out[rid] = entry
    return out


def evaluate_kill_test(
    contract: Contract, kill_test: Dict[str, Any], contract_hash: str
) -> Tuple[bool, List[str], Optional[bool]]:
    """Return (ok, reasons, sabotage_rejected).

    Enforces B1 red-contract-first from agent-driven outcomes:

    - The results must be bound to THIS contract by `contract_sha256`; a mismatch
      means stale or mismatched results and refuses (no self-attested default).
    - Every criterion must carry an explicit boolean `passed_against_null`;
      missing coverage or a non-boolean value refuses (fail closed on an
      unproven criterion).
    - Any criterion that PASSED against the null artifact is vacuous and refuses,
      naming it (B1 / criterion 3).
    - Optional B2 sabotage: if authored and not rejected, refuses.

    `sabotage_rejected` is `True`/`False` when a sabotage sketch was authored,
    else `None` (recorded verbatim into freeze evidence).
    """
    reasons: List[str] = []
    declared = kill_test.get("contract_sha256")
    if declared != contract_hash:
        reasons.append(
            "kill-test results are not bound to this contract (contract_sha256 "
            "%r != %r): stale or mismatched results" % (declared, contract_hash)
        )
        return False, reasons, None

    result_by_id = _result_map(kill_test)
    contract_ids = {c.id for c in contract.criteria}
    extra = sorted(set(result_by_id) - contract_ids)
    if extra:
        reasons.append("kill-test results reference unknown criterion id(s): %s" % ", ".join(extra))

    vacuous: List[str] = []
    for crit in contract.criteria:
        entry = result_by_id.get(crit.id)
        if entry is None:
            reasons.append("no kill-test result for criterion %s (untested against null)" % crit.id)
            continue
        passed = entry.get("passed_against_null")
        if not isinstance(passed, bool):
            reasons.append(
                "criterion %s kill-test result lacks a boolean passed_against_null" % crit.id
            )
            continue
        if passed:
            vacuous.append(crit.id)
    if vacuous:
        reasons.append(
            "vacuous criterion(s) pass against the class null artifact (B1, "
            "satisfiable by absence): %s" % ", ".join(vacuous)
        )

    sabotage_rejected = _sabotage_outcome(kill_test, reasons)
    return (not reasons), reasons, sabotage_rejected


def _sabotage_outcome(kill_test: Dict[str, Any], reasons: List[str]) -> Optional[bool]:
    sabotage = kill_test.get("sabotage")
    if not isinstance(sabotage, dict) or not sabotage.get("authored"):
        return None
    rejected = sabotage.get("rejected")
    if not isinstance(rejected, bool):
        reasons.append("sabotage sketch authored but 'rejected' is not a boolean (B2)")
        return None
    if not rejected:
        reasons.append("authored sabotage sketch was NOT rejected by the contract (B2)")
    return rejected


# --------------------------------------------------------------------------- #
# Evidence recording.
# --------------------------------------------------------------------------- #


def contract_sha256(path: Any) -> str:
    """sha256 hex digest of the contract file's raw bytes. The exit verifier
    re-hashes the same bytes (C5); a mid-run edit is a mismatch."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_freeze_evidence(
    record_path: Any, run_id: str, contract_hash: str, sabotage_rejected: Optional[bool]
) -> Dict[str, Any]:
    """Record freeze evidence into the completion record, preserving any fields
    a later stage already wrote. Freeze evidence is this gate's only write."""
    path = Path(record_path)
    record: Dict[str, Any] = {}
    if path.exists():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            record = loaded
    record["run_id"] = run_id
    record["contract_hash"] = contract_hash
    record["freeze_evidence"] = {
        "contract_hash": contract_hash,
        "frozen_before_decomposition": True,
        "frozen_at": _now_iso(),
        "kill_test": {
            "null_artifact_all_fail": True,
            "sabotage_rejected": sabotage_rejected,
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


# --------------------------------------------------------------------------- #
# Orchestration.
# --------------------------------------------------------------------------- #


def _default_record_path(run_id: str, contract_dir: Any) -> Path:
    return Path(contract_dir) / ("%s.completion.json" % run_id)


def freeze(
    contract_path: Any,
    run_id: str,
    kill_test_path: Optional[Any],
    record_path: Optional[Any] = None,
    contract_dir: Any = DEFAULT_CONTRACT_DIR,
) -> FreezeResult:
    """Attempt to freeze the contract. Refuses (frozen=False) with named reasons
    on any structural, downgrade, or kill-test failure, and fails closed when
    kill-test results are absent. On success records freeze evidence and returns
    frozen=True."""
    try:
        contract = parse_contract_file(contract_path)
    except (ContractParseError, OSError) as exc:
        return FreezeResult(False, ["contract could not be parsed: %s" % exc])

    structural = structural_floor_reasons(contract) + downgrade_reasons(contract)
    if structural:
        return FreezeResult(False, structural)

    contract_hash = contract_sha256(contract_path)

    if kill_test_path is None:
        return FreezeResult(
            False,
            [
                "kill-test results required: cannot prove red-contract-first (B1). "
                "Refusing (fail closed) - freeze is impossible without a validated "
                "null-artifact kill test."
            ],
            contract_hash=contract_hash,
        )
    try:
        kill_test = load_kill_test(kill_test_path)
    except KillTestError as exc:
        return FreezeResult(False, [str(exc)], contract_hash=contract_hash)

    try:
        ok, reasons, sabotage_rejected = evaluate_kill_test(contract, kill_test, contract_hash)
    except KillTestError as exc:
        return FreezeResult(False, [str(exc)], contract_hash=contract_hash)
    if not ok:
        return FreezeResult(False, reasons, contract_hash=contract_hash)

    if record_path is None:
        record_path = _default_record_path(run_id, contract_dir)
    write_freeze_evidence(record_path, run_id, contract_hash, sabotage_rejected)
    return FreezeResult(
        True, [], contract_hash=contract_hash, record_path=str(record_path)
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Freeze an acceptance contract (or refuse).")
    parser.add_argument("contract", help="path to the <run-id>.contract.md file to freeze")
    parser.add_argument("--run-id", required=True, help="run identifier (TM tag / issue-queue slug)")
    parser.add_argument(
        "--kill-test-results",
        default=None,
        help="path to the agent-driven kill-test results JSON (required to freeze; "
        "absence fails closed)",
    )
    parser.add_argument(
        "--record",
        default=None,
        help="completion-record path (default: <contract-dir>/<run-id>.completion.json)",
    )
    parser.add_argument(
        "--contract-dir",
        default=str(DEFAULT_CONTRACT_DIR),
        help="directory for the completion record (default: .taskmaster/contract)",
    )
    args = parser.parse_args(argv)

    result = freeze(
        args.contract,
        args.run_id,
        args.kill_test_results,
        record_path=args.record,
        contract_dir=args.contract_dir,
    )
    print(json.dumps(result.to_dict(), indent=2))
    # Exit 0 == frozen; non-zero == refused.
    return 0 if result.frozen else 1


if __name__ == "__main__":
    sys.exit(main())
