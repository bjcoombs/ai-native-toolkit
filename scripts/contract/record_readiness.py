#!/usr/bin/env python3
"""Record an acceptance-contract readiness verdict into a completion record.

The readiness check (PRD ``prd-acceptance-contract.md`` A2/A3) is one cold
document-only agent, one pass: it reads the spec + drafted contract and returns a
``ready | needs-work`` verdict together with the decorrelation *source* - who
supplied the independent read (a non-Claude model, a human, or nobody). This
script is the thin recorder that stamps that ``{verdict, source}`` into the run's
completion record ``.taskmaster/contract/<run-id>.completion.json``.

Deliberately minimal, and it decides nothing:

- ``source: none`` is a *legitimate* input (a run with no decorrelated review). It
  is NOT this recorder's job to refuse it - the consequence (a
  ``DEGRADED: no decorrelated review`` stamp, and never a ``PASS``) is stamped by
  ``validate_completion.py`` at validation time. This recorder only writes the
  fact down honestly.
- It never clobbers other fields. An existing completion record (freeze evidence,
  verifier results, escalations) is loaded, has only ``readiness_verdict`` set,
  and is written back. A missing record is created carrying just ``run_id`` and
  the verdict, ready for later gates to fill.

``record_readiness()`` is import-clean and pure over its arguments; a CLI
entrypoint (``python record_readiness.py <run-id> --verdict <v> --source <s>``)
wraps it.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Default location of the per-run completion record, relative to the run cwd.
# Shared with the rest of the acceptance-contract machinery (F1).
DEFAULT_CONTRACT_DIR = Path(".taskmaster/contract")

# The readiness verdict values (PRD A2). `ready` only when every criterion is
# cold-executable and nothing material is missing; otherwise `needs-work`.
VERDICTS = ("ready", "needs-work")

# The decorrelation sources (PRD A3), mirroring completion.schema.json's enum.
# `none` is legitimate input; the DEGRADED consequence is the validator's job.
SOURCES = ("non-claude-model", "human", "none")


def _validate_run_id(run_id: str) -> None:
    """Reject a run_id that would escape the contract dir when interpolated into
    the record filename. Mirrors validate_completion.check_token's guard so a
    caller cannot write ``<run-id>.completion.json`` outside the intended dir."""
    if not run_id or "/" in run_id or "\\" in run_id or ".." in run_id:
        raise ValueError("invalid run_id %r: path separators are not allowed" % run_id)


def record_readiness(
    run_id: str,
    verdict: str,
    source: str,
    contract_dir: Optional[Any] = None,
) -> Path:
    """Write ``readiness_verdict: {verdict, source}`` into the run's completion
    record without clobbering other fields. Returns the record path.

    Creates the record (and its directory) if absent; merges into it if present.
    """
    _validate_run_id(run_id)
    if verdict not in VERDICTS:
        raise ValueError("invalid verdict %r: expected one of %s" % (verdict, VERDICTS))
    if source not in SOURCES:
        raise ValueError("invalid source %r: expected one of %s" % (source, SOURCES))

    directory = Path(contract_dir) if contract_dir is not None else DEFAULT_CONTRACT_DIR
    path = directory / ("%s.completion.json" % run_id)

    record: Dict[str, Any] = {}
    if path.exists():
        with path.open(encoding="utf-8") as fh:
            loaded = json.load(fh)
        if not isinstance(loaded, dict):
            raise ValueError("existing completion record is not a JSON object: %s" % path)
        record = loaded

    # run_id is a keying invariant; set it if absent, and refuse to silently
    # overwrite a record that belongs to a different run.
    existing_run_id = record.get("run_id")
    if existing_run_id is not None and existing_run_id != run_id:
        raise ValueError(
            "completion record at %s is for run %r, not %r" % (path, existing_run_id, run_id)
        )
    record["run_id"] = run_id
    record["readiness_verdict"] = {"verdict": verdict, "source": source}

    directory.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return path


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Record an acceptance-contract readiness verdict into the run's completion record."
    )
    parser.add_argument("run_id", help="run identifier (Task Master tag or issue-queue slug)")
    parser.add_argument("--verdict", required=True, choices=VERDICTS, help="readiness verdict")
    parser.add_argument(
        "--source",
        required=True,
        choices=SOURCES,
        help="decorrelation source; 'none' is legitimate and stamps DEGRADED at validation",
    )
    parser.add_argument(
        "--contract-dir",
        default=None,
        help="directory holding <run-id>.completion.json (default: .taskmaster/contract)",
    )
    args = parser.parse_args(argv)

    try:
        path = record_readiness(args.run_id, args.verdict, args.source, args.contract_dir)
    except (ValueError, OSError) as exc:
        print("record_readiness: %s" % exc, file=sys.stderr)
        return 1
    print("recorded readiness verdict=%s source=%s -> %s" % (args.verdict, args.source, path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
