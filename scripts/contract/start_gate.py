#!/usr/bin/env python3
"""Fail-closed start gate for an acceptance-contract run.

`start_gate.py <run-id>` is the entry-side executable enforcement of the
acceptance contract (PRD A1, success criterion 4). Each work-source command's
marathon-start step invokes it, and the run may not proceed unless one of two
doors is open:

- **Freeze evidence** recorded in `.taskmaster/contract/<run-id>.completion.json`
  (the contract's sha256 frozen before decomposition, with a passing kill test)
  -> exit 0. The run has a real contract to verify against at exit.
- **A signed operator skip** (`operator_signoff` recorded before the run starts)
  but no valid freeze -> exit 0 WITH a loud UNVERIFIED warning. The skip is
  *capped, not free*: `validate_completion.py` permanently caps such a run at
  UNVERIFIED - it can never certify PASS. The warning says so.
- **Neither** -> non-zero exit with an actionable message. The refusal is an
  executable code path, not skill prose.

Consistency with the exit side is deliberate: the freeze check delegates to
`validate_completion.freeze_evidence_valid`, so freeze evidence whose kill test
failed (or that lacks the frozen contract hash) is no more valid as *start*
evidence than it is as *exit* evidence. The two gates cannot drift apart.

The contract directory defaults to `.taskmaster/contract` (relative to the run
cwd) and can be overridden with `--contract-dir` or the
`ACCEPTANCE_CONTRACT_DIR` environment variable, mirroring how
`validate_completion` resolves its provenance dir - so the gate is testable
against a tmp dir.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import List, Optional

import validate_completion as vc

# Default location of the per-run contract artifacts, relative to the run cwd.
DEFAULT_CONTRACT_DIR = Path(".taskmaster/contract")
ENV_CONTRACT_DIR = "ACCEPTANCE_CONTRACT_DIR"

_RULE = "=" * 70


def resolve_contract_dir(cli_value: Optional[str]) -> Path:
    """Resolve the contract dir: CLI flag, then env var, then the default."""
    if cli_value:
        return Path(cli_value)
    env = os.environ.get(ENV_CONTRACT_DIR)
    if env:
        return Path(env)
    return DEFAULT_CONTRACT_DIR


def _load_record(contract_dir: Path, run_id: str) -> Optional[dict]:
    """Load the run's completion record, or None if absent/unreadable/malformed.

    A record we cannot read cannot back a start; the caller treats None as
    "no evidence" and refuses.
    """
    path = contract_dir / ("%s.completion.json" % run_id)
    try:
        with path.open(encoding="utf-8") as fh:
            record = json.load(fh)
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def start_gate(run_id: str, contract_dir: Path) -> int:
    """Return 0 if the run may start, non-zero otherwise. Warnings go to stderr."""
    record = _load_record(contract_dir, run_id)

    if record is not None:
        freeze_valid, freeze_reason = vc.freeze_evidence_valid(record)
        if freeze_valid:
            print("start gate: OK - freeze evidence present for run %r." % run_id)
            return 0

        if vc.is_signed(record):
            print(
                "\n".join(
                    [
                        _RULE,
                        "UNVERIFIED: run %r is starting WITHOUT a frozen acceptance" % run_id,
                        "contract. A signed operator skip is recorded, so the run may",
                        "proceed - but it is permanently CAPPED at UNVERIFIED and can",
                        "NEVER certify PASS. validate_completion.py enforces this cap at",
                        "exit; the completion record records why.",
                        _RULE,
                    ]
                ),
                file=sys.stderr,
            )
            return 0

    # No valid freeze and no signed skip: refuse, with an actionable message.
    reason = "no completion record found" if record is None else freeze_reason
    print(
        "start gate: REFUSED - run %r cannot start: %s.\n"
        "  Open one of the two doors before decomposition:\n"
        "    1. Freeze a contract (scripts/contract/freeze.py) so freeze_evidence\n"
        "       with a passing kill test is recorded, or\n"
        "    2. Record an operator_signoff skip in the completion record - a\n"
        "       capped skip that can never certify PASS (UNVERIFIED)." % (run_id, reason),
        file=sys.stderr,
    )
    return 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail-closed start gate for an acceptance-contract run."
    )
    parser.add_argument("run_id", help="run identifier (Task Master tag or issue-queue slug)")
    parser.add_argument(
        "--contract-dir",
        default=None,
        help="directory holding <run-id>.completion.json (default: %s, or $%s)"
        % (DEFAULT_CONTRACT_DIR, ENV_CONTRACT_DIR),
    )
    args = parser.parse_args(argv)
    return start_gate(args.run_id, resolve_contract_dir(args.contract_dir))


if __name__ == "__main__":
    sys.exit(main())
