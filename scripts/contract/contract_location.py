#!/usr/bin/env python3
"""Where the acceptance-contract artifacts live: the one resolver.

Every script under `scripts/contract/` that reads or writes a per-run artifact
(`<run-id>.contract.md`, `<run-id>.completion.json`, `<run-id>.provenance.json`,
`<run-id>/tier3-*.artifact`) asks this module for the folder, so the gates, the
freeze, the readiness recorder, the verifier chokepoint and the validator can
never disagree about it.

Resolution order, first match wins:

1. an explicit value (the script's `--contract-dir` flag, or a `contract_dir=`
   keyword argument);
2. the `ACCEPTANCE_CONTRACT_DIR` environment variable;
3. the default, `.claude/contracts/`, relative to the current working directory.

The default has one fallback for repos set up before the default moved: when
`.claude/contracts/` does not exist but the legacy Task Master folder does, the
legacy folder is used. When both exist the new default wins; when neither
exists, writers create `.claude/contracts/`.

Resolution happens when a function is called, never at import, because the
fallback depends on the cwd and the environment at that moment.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

DEFAULT_CONTRACT_DIR = Path(".claude/contracts")
# Pre-1.92.0 default; read only when it exists and the new default does not.
LEGACY_CONTRACT_DIR = Path(".taskmaster/contract")
ENV_CONTRACT_DIR = "ACCEPTANCE_CONTRACT_DIR"

# One-line description of the resolution for argparse help text.
HELP_DEFAULT = "default: $%s, else %s/ relative to the cwd" % (
    ENV_CONTRACT_DIR,
    DEFAULT_CONTRACT_DIR,
)


def default_contract_dir() -> Path:
    """The cwd-relative default: `.claude/contracts`, or the legacy folder when
    only the legacy folder exists."""
    if not DEFAULT_CONTRACT_DIR.is_dir() and LEGACY_CONTRACT_DIR.is_dir():
        return LEGACY_CONTRACT_DIR
    return DEFAULT_CONTRACT_DIR


def resolve_contract_dir(explicit: Optional[Any] = None) -> Path:
    """Resolve the contract dir: explicit value, then env var, then the default."""
    if explicit is not None and str(explicit) != "":
        return Path(explicit)
    env = os.environ.get(ENV_CONTRACT_DIR)
    if env:
        return Path(env)
    return default_contract_dir()
