#!/usr/bin/env python3
"""Verifier spawn chokepoint - the single code path for cold verification.

This module is the ONLY code path in the toolkit that may (a) write the
provenance side-channel `.taskmaster/contract/<run-id>.provenance.json` and
(b) write verifier results / `verifier_provenance` into a completion record.
That exclusivity is not a convention - `tests/contract/test_custody.py` derives
it from disk with an AST scan (PRD C4, criterion 11): any other `.py` under
`scripts/` that constructs a `verifier_provenance` / `criteria_results` record
key, or writes a `*.provenance.json` file, fails the invariant.

Custody has two mechanisms (PRD C4):

- **Prevention.** All spawns go through :func:`spawn_verifier`, whose CLI
  interface accepts exactly two positional inputs - the frozen contract path
  and the assembled product path (criterion 11). The run identifier is derived
  from the contract filename (`<run-id>.contract.md`); there is no third
  parameter and no prompt-shaping input, so composing a verifier prompt that
  leaks the implementation's seam vocabulary is inexpressible through the
  interface. The prompt is a FIXED template holding nothing but the frozen
  contract content, its hash, and the product path.
- **Detection.** Each spawn mints a fresh random token (:mod:`secrets`) and
  writes `{run_id, token, issued_at, contract_hash}` to the side-channel that
  only this module writes. `validate_completion.py` requires the completion
  record's `verifier_provenance.token` to exactly match the side-channel entry
  for this run; absent, forged, copied, or stale tokens all stamp
  `DEGRADED-custody` and cannot certify `PASS` (criteria 11 & 15).

Seam for task 6 (the verifier logic itself - hash re-check, couldnt_drive,
PARTIAL/PASS - ships in a separate, dependent PR):

    spawn = spawn_verifier(contract_path, product_path)   # side-channel written
    #   ... task 6 drives the product with spawn.prompt, re-hashes the contract
    #       against spawn.contract_hash, and produces its observations ...
    observations = VerifierObservations(
        criteria_results=[...],     # per-criterion {id, tier, result, observation}
        couldnt_drive=[...],        # criteria the verifier could not execute (C3)
        tier3_escalations=[...],    # operator-observable artifacts (C2)
        abort_events=[...],         # e.g. a hash-mismatch abort task 6 detected (C5)
    )
    record = ingest_verifier_results(spawn, observations)  # token-stamped + written

The chokepoint owns spawn + custody + record-writing; task 6 owns the driving
and the pass/fail *decisions*. :func:`ingest_verifier_results` is the single
ingestion point where task 6's observed results come back: it stamps them with
this spawn's token, merges them into the run's completion record without
disturbing the fields other stages own (`freeze_evidence`, `readiness_verdict`,
`operator_signoff`), and persists it. It refuses to stamp a spawn the
side-channel has already superseded (a newer verification run issued a fresher
token), so a stale spawn cannot quietly re-certify a moved target.

The module is import-clean and stdlib-only, matching `validate_completion.py`:
pure functions plus a CLI (`python spawn_verifier.py <contract> <product>`)
that mints the token, writes the side-channel, and prints the composed prompt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Shared artifact location (mirrors validate_completion.DEFAULT_PROVENANCE_DIR).
DEFAULT_CONTRACT_DIR = Path(".taskmaster/contract")

CONTRACT_SUFFIX = ".contract.md"
PROVENANCE_SUFFIX = ".provenance.json"
COMPLETION_SUFFIX = ".completion.json"

# Marker recorded in the completion record's verifier_provenance so a reader can
# see which component stamped the results.
SPAWNED_BY = "spawn_verifier"

# 32 bytes -> 64 hex chars. Far beyond guessing; secrets is a CSPRNG.
_TOKEN_BYTES = 32


class ChokepointError(RuntimeError):
    """Raised when the chokepoint refuses to spawn or ingest.

    Distinct from ``ValueError`` (a malformed interface input, e.g. a contract
    path that is not `<run-id>.contract.md`) so callers can tell an interface
    misuse from a custody refusal (a superseded/stale spawn).
    """


# --------------------------------------------------------------------------- #
# Run-id derivation and validation.
# --------------------------------------------------------------------------- #


def _reject_bad_run_id(run_id: str) -> None:
    """Refuse a run_id that would escape the contract dir when interpolated.

    Mirrors ``validate_completion.check_token``: run_id keys a filesystem path
    (`<run-id>.provenance.json`), so a value with path separators or ``..``
    could point writes outside the contract dir. Reject before building paths.
    """
    if (
        not run_id
        or "/" in run_id
        or "\\" in run_id
        or ".." in run_id
    ):
        raise ValueError("invalid run_id %r: path separators are not allowed" % run_id)


def run_id_from_contract(contract_path: Any) -> str:
    """Derive the run identifier from a `<run-id>.contract.md` filename.

    Only the basename is used, so a directory-laden path cannot smuggle a
    traversal into the run_id; the derived id is validated regardless.
    """
    name = Path(contract_path).name
    if not name.endswith(CONTRACT_SUFFIX):
        raise ValueError(
            "contract path %r must be named <run-id>%s" % (name, CONTRACT_SUFFIX)
        )
    run_id = name[: -len(CONTRACT_SUFFIX)]
    _reject_bad_run_id(run_id)
    return run_id


# --------------------------------------------------------------------------- #
# Hashing, token, timestamp, prompt.
# --------------------------------------------------------------------------- #


def hash_contract(contract_path: Any) -> str:
    """Return the sha256 of the frozen contract file, prefixed ``sha256:``."""
    data = Path(contract_path).read_bytes()
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _new_token() -> str:
    return "tok-" + secrets.token_hex(_TOKEN_BYTES)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# The FIXED verifier prompt. It carries nothing but the frozen contract content,
# its hash, and the product path (PRD C4 prevention). The contract's own
# per-criterion action/observation ARE the product goal; the chokepoint forwards
# the frozen artifact verbatim and adds no seam vocabulary of its own.
VERIFIER_PROMPT_TEMPLATE = """\
You are a cold, non-implementing acceptance verifier. You did not build this
product. Do not read its source for hints - drive it and observe its behaviour.

Frozen acceptance contract (sha256 {contract_hash}):
--- BEGIN CONTRACT ---
{contract_content}
--- END CONTRACT ---

Assembled product to verify: {product_path}

For each criterion in the contract's yaml block:
- Perform its `action` against the product.
- Observe whether its `observation` holds, and record pass or fail from what
  you actually observed - never from the product's source or your expectations.
- If you cannot execute the action at all, record the criterion as undriven.
  Do not guess a result.
- Before finishing, re-hash the contract file. If it no longer matches
  {contract_hash}, abort - do not certify against a contract that moved mid-run.

Report only what you observed. The spawning chokepoint stamps your results for
custody; do not write the provenance or completion files yourself.
"""


def compose_prompt(contract_content: str, contract_hash: str, product_path: str) -> str:
    """Compose the fixed verifier prompt. No caller-supplied shaping is possible."""
    return VERIFIER_PROMPT_TEMPLATE.format(
        contract_hash=contract_hash,
        contract_content=contract_content,
        product_path=product_path,
    )


# --------------------------------------------------------------------------- #
# Spawn handle and side-channel write.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class VerifierSpawn:
    """Everything a spawn produces. Handed to :func:`ingest_verifier_results`.

    ``token`` is the custody secret for this run; ``prompt`` is what task 6
    drives the cold verifier with; the paths are where the side-channel and
    completion record for this run live.
    """

    run_id: str
    contract_path: Path
    product_path: Path
    contract_hash: str
    token: str
    issued_at: str
    prompt: str
    provenance_path: Path
    completion_path: Path


def _write_provenance(
    path: Path, run_id: str, token: str, issued_at: str, contract_hash: str
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "token": token,
        "issued_at": issued_at,
        "contract_hash": contract_hash,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def spawn_verifier(
    contract_path: Any, product_path: Any, *, contract_dir: Optional[Any] = None
) -> VerifierSpawn:
    """Spawn a cold verifier for one run: the SINGLE spawn code path.

    Reads and hashes the frozen contract, mints a fresh custody token, writes
    the provenance side-channel, and composes the fixed verifier prompt. The
    only positional inputs are the contract path and the product path (the CLI
    exposes exactly these); ``contract_dir`` is a keyword-only artifact-location
    override for testing and defaults to the shared `.taskmaster/contract`.
    """
    contract_path = Path(contract_path)
    product_path = Path(product_path)
    run_id = run_id_from_contract(contract_path)
    cdir = Path(contract_dir) if contract_dir is not None else DEFAULT_CONTRACT_DIR

    contract_content = contract_path.read_text(encoding="utf-8")
    contract_hash = hash_contract(contract_path)
    token = _new_token()
    issued_at = _now_iso()
    prompt = compose_prompt(contract_content, contract_hash, str(product_path))

    provenance_path = cdir / (run_id + PROVENANCE_SUFFIX)
    completion_path = cdir / (run_id + COMPLETION_SUFFIX)
    _write_provenance(provenance_path, run_id, token, issued_at, contract_hash)

    return VerifierSpawn(
        run_id=run_id,
        contract_path=contract_path,
        product_path=product_path,
        contract_hash=contract_hash,
        token=token,
        issued_at=issued_at,
        prompt=prompt,
        provenance_path=provenance_path,
        completion_path=completion_path,
    )


# --------------------------------------------------------------------------- #
# Results ingestion - the seam task 6 builds against.
# --------------------------------------------------------------------------- #


@dataclass
class VerifierObservations:
    """What task 6's cold verifier hands back for the chokepoint to stamp.

    Deliberately NOT a `{"criteria_results": ...}` record fragment: task 6 hands
    back raw observations and the chokepoint (only it) assembles the record
    fields. That keeps the record-key writes inside this module, which is what
    the custody invariant asserts.
    """

    criteria_results: List[Dict[str, Any]] = field(default_factory=list)
    couldnt_drive: List[str] = field(default_factory=list)
    tier3_escalations: List[Dict[str, Any]] = field(default_factory=list)
    abort_events: List[Dict[str, Any]] = field(default_factory=list)


def _read_side_channel(path: Path) -> Dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ChokepointError("provenance side-channel %s is not a JSON object" % path)
    return data


def _assert_current_token(spawn: VerifierSpawn) -> None:
    """Refuse to stamp a spawn the side-channel has superseded.

    A second :func:`spawn_verifier` for the same run overwrites the side-channel
    with a fresh token, so an older spawn is stale. Stamping it would echo a
    token the validator now treats as stale anyway; refusing here fails loudly
    instead of writing a record that can never certify.
    """
    if not spawn.provenance_path.exists():
        raise ChokepointError(
            "no provenance side-channel for run %r: spawn_verifier did not run "
            "or its artifact was removed" % spawn.run_id
        )
    side = _read_side_channel(spawn.provenance_path)
    if side.get("run_id") != spawn.run_id or side.get("token") != spawn.token:
        raise ChokepointError(
            "spawn for run %r is superseded: the side-channel holds a newer "
            "token (a later verification run started)" % spawn.run_id
        )


def _load_or_init_completion(path: Path, run_id: str) -> Dict[str, Any]:
    """Load the run's completion record, or start a minimal one.

    Merging (rather than clobbering) preserves the fields other stages own -
    ``freeze_evidence`` (freeze.py), ``readiness_verdict`` (the start gate),
    ``operator_signoff`` (the operator).
    """
    if path.exists():
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            data["run_id"] = run_id
            return data
    return {"run_id": run_id}


def _write_completion(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def ingest_verifier_results(
    spawn: VerifierSpawn, observations: VerifierObservations
) -> Dict[str, Any]:
    """Stamp task 6's observations with the custody token and persist them.

    The single ingestion point (criterion 11): only here are ``criteria_results``
    and ``verifier_provenance`` written into a completion record. Returns the
    written record so a caller can hand it straight to
    ``validate_completion.validate``.
    """
    _assert_current_token(spawn)
    record = _load_or_init_completion(spawn.completion_path, spawn.run_id)

    record["contract_hash"] = spawn.contract_hash
    record["criteria_results"] = list(observations.criteria_results)
    record["couldnt_drive"] = list(observations.couldnt_drive)
    if observations.tier3_escalations:
        record["tier3_escalations"] = list(observations.tier3_escalations)
    if observations.abort_events:
        existing = record.get("abort_events")
        record["abort_events"] = (list(existing) if isinstance(existing, list) else []) + list(
            observations.abort_events
        )
    record["verifier_provenance"] = {
        "token": spawn.token,
        "run_id": spawn.run_id,
        "spawned_by": SPAWNED_BY,
    }

    _write_completion(spawn.completion_path, record)
    return record


# --------------------------------------------------------------------------- #
# CLI - exactly two positional inputs (criterion 11).
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    """The chokepoint CLI: exactly {contract path, product path}, no extras.

    No option shapes the prompt or the run_id; the run identifier is derived
    from the contract filename. Argparse rejects a third positional and any
    unknown option, so the C4 prevention property holds at the interface.
    """
    parser = argparse.ArgumentParser(
        description="Spawn a cold acceptance verifier for one run (the single "
        "custody chokepoint). Mints a provenance token, writes the side-channel, "
        "and prints the fixed verifier prompt.",
    )
    parser.add_argument("contract_path", help="path to <run-id>.contract.md (the frozen contract)")
    parser.add_argument("product_path", help="path to the assembled product to verify")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    spawn = spawn_verifier(args.contract_path, args.product_path)
    # The prompt is the artifact a caller drives the cold verifier with; the
    # side-channel is already written. Emit the prompt on stdout.
    print(spawn.prompt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
