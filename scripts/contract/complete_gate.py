#!/usr/bin/env python3
"""Fail-closed complete gate for an acceptance-contract run.

`complete_gate.py <run-id>` is the exit mirror of the start gate (PRD C1,
success criterion 16). Marathon's completion step invokes it, and a run may not
be marked complete unless `validate_completion.py` certifies its record PASS.
It delegates the accept/refuse decision entirely to the validator - it never
re-implements it - so exactly one component decides what "done" means.

It exits non-zero when the run has:
- no completion record,
- a record without verifier results (the validator stamps DEGRADED-custody:
  no chokepoint-issued token), or
- a record the validator rejects for any reason (FAIL, PARTIAL, ABORTED,
  UNVERIFIED skip, schema violation, ...).

It exits zero only when the validator certifies PASS. A marathon that never
spawned the cold-exit verifier therefore cannot pass completion in code.

The contract directory holds both the completion record and the provenance
side-channel the validator checks tokens against. It is resolved by
`contract_location.resolve_contract_dir`: `--contract-dir`, then the
`ACCEPTANCE_CONTRACT_DIR` environment variable, then `.claude/contracts`
relative to the run cwd, so the gate is testable against a tmp dir.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

# Flat sibling modules (no package): put this directory on the path first.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import validate_completion as vc  # noqa: E402
from contract_location import HELP_DEFAULT, resolve_contract_dir  # noqa: E402


def complete_gate(run_id: str, contract_dir: Path) -> int:
    """Return 0 iff the validator certifies the run's record PASS, else non-zero.

    The provenance side-channel lives in the same contract dir, so the validator
    is pointed there for its token-authenticity check.
    """
    record_path = contract_dir / ("%s.completion.json" % run_id)
    result = vc.validate_path(record_path, provenance_dir=contract_dir)

    if result.certified:
        print("complete gate: OK - run %r certifies PASS." % run_id)
        return 0

    detail = "; ".join(result.reasons) if result.reasons else "record does not certify PASS"
    print(
        "complete gate: REFUSED - run %r cannot complete (verdict %s): %s."
        % (run_id, result.verdict, detail),
        file=sys.stderr,
    )
    return 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail-closed complete gate for an acceptance-contract run."
    )
    parser.add_argument("run_id", help="run identifier (Task Master tag or issue-queue slug)")
    parser.add_argument(
        "--contract-dir",
        default=None,
        help="directory holding <run-id>.completion.json and <run-id>.provenance.json "
        "(%s)" % HELP_DEFAULT,
    )
    args = parser.parse_args(argv)
    return complete_gate(args.run_id, resolve_contract_dir(args.contract_dir))


if __name__ == "__main__":
    sys.exit(main())
