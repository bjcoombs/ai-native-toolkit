#!/usr/bin/env python3
"""Tier assignment (A4) and tier-3 escalation assembly - pure logic that RETURNS data.

Two concerns, both stdlib-only pure functions so this module is import-clean and
fully pytest-able, and - crucially - so it can be the shared home for A4 tier
logic without ever writing a completion record or the provenance side-channel
(those writes are the chokepoint's alone, PRD C4; `tests/contract/test_custody.py`
asserts it by AST scan). Nothing here writes any file: every function RETURNS
data. The chokepoint (`spawn_verifier.py`) is the single component that
materializes the tier-3 artifact FILE and writes the escalation into the record.

- **Class tier defaults (PRD A4).** :data:`CLASS_TIER_DEFAULTS` is the
  authoritative, importable statement of the observation-ceiling default tier per
  class, and :func:`default_tier_for_class` answers it (unknown class -> tier-2,
  conservative). `freeze.py` derives its machine-enforced downgrade table from
  this single source, so the A4 table and the freeze gate cannot drift apart.

- **Tier-3 escalation assembly (PRD C2, criterion 7).** A tier-3 criterion is a
  perceptual residue a cold agent structurally cannot observe, so it escalates to
  the operator with a human-observable artifact. :func:`tier3_artifact_path`
  derives the canonical, path-safe location of that artifact
  (`<contract-dir>/<run-id>/tier3-<criterion-id>.artifact`);
  :func:`build_tier3_escalation` assembles the escalation entry
  `{criterion_id, artifact_path, observation, awaiting_signoff}`; and
  :func:`tier3_artifact_contents` is the placeholder body the operator fills. The
  run cannot certify until `operator_signoff` is recorded (enforced by
  `validate_completion.py`); ``awaiting_signoff`` is the per-entry honest marker
  that it has not been.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

# --------------------------------------------------------------------------- #
# Class tier defaults (PRD A4).
# --------------------------------------------------------------------------- #

# The A4 observation-ceiling default tier per class - the single, importable
# source of truth for "what tier does this class default to":
#
#   cli/tool    -> 1  binary do-and-observe (exit code + output on real input) a
#                     cold agent fully verifies.
#   refactor    -> 2  behavioural equivalence, judged with calibration
#                     (ab-equivalence); not a binary check.
#   report      -> 2  SPLIT (A4): citation-resolution is tier-1 ONLY when the
#                     anchor is a machine-resolvable locator; citation
#                     faithfulness is tier-2 judged, never tier-1. The
#                     conservative default is faithfulness's tier-2 - a tier-1
#                     citation-resolution criterion is the permissive special
#                     case, prose-guarded per A4's honest-narrowness note.
#   interactive -> 3  perceptual residue a cold agent structurally cannot
#                     observe; the human launch is mandatory.
#
# NOTE ON ENFORCEMENT (deliberately narrower than this table): freeze machine-
# enforces the downgrade-justification rule only for `cli` and `refactor` (see
# freeze.DOWNGRADE_DEFAULT_TIER). `interactive` is backstopped structurally
# instead (criterion 13: an interactive contract must CONTAIN >=1 tier-3, because
# its headless-drivable checks legitimately sit at tier-1), and report-class
# faithfulness is prose-guarded only (A4). This table states the defaults; it is
# not itself a per-criterion refusal rule.
CLASS_TIER_DEFAULTS: Dict[str, int] = {
    "cli": 1,
    "refactor": 2,
    "report": 2,
    "interactive": 3,
}

# An unrecognised class defaults to tier-2 (A4, conservative): never claim a
# stronger machine ceiling than can be justified for a class we do not model. A
# class the contract parser does not recognise never reaches freeze (it is a
# parse error), but this helper answers conservatively for any caller that asks.
UNKNOWN_CLASS_DEFAULT_TIER = 2


def default_tier_for_class(cls: str) -> int:
    """Return the A4 observation-ceiling default tier for ``cls``.

    Known classes come from :data:`CLASS_TIER_DEFAULTS`; anything else is tier-2
    (conservative).
    """
    return CLASS_TIER_DEFAULTS.get(cls, UNKNOWN_CLASS_DEFAULT_TIER)


# --------------------------------------------------------------------------- #
# Tier-3 escalation assembly (PRD C2, criterion 7).
# --------------------------------------------------------------------------- #

ARTIFACT_SUFFIX = ".artifact"

# Default artifact-root, relative to the run cwd. Mirrors the contract-dir the
# chokepoint and validator share; the per-run artifacts live in a `<run-id>/`
# subdirectory beside the `<run-id>.completion.json` / `<run-id>.provenance.json`
# files.
DEFAULT_CONTRACT_DIR = Path(".taskmaster/contract")


def _reject_bad_component(name: str, kind: str) -> None:
    """Refuse a path component that would escape the contract dir.

    ``run_id`` and ``criterion_id`` are both interpolated into a filesystem path,
    so a value with path separators or ``..`` could point a write outside the
    contract dir. Mirrors the same guard in `spawn_verifier` and
    `validate_completion`; reject before building any path.
    """
    if not isinstance(name, str) or not name or "/" in name or "\\" in name or ".." in name:
        raise ValueError("invalid %s %r: path separators are not allowed" % (kind, name))


def tier3_artifact_path(
    run_id: str, criterion_id: str, contract_dir: Any = DEFAULT_CONTRACT_DIR
) -> Path:
    """Return the canonical, path-safe location of a tier-3 escalation artifact.

    ``<contract-dir>/<run-id>/tier3-<criterion-id>.artifact``. Both ``run_id`` and
    ``criterion_id`` are validated against path traversal first.
    """
    _reject_bad_component(run_id, "run_id")
    _reject_bad_component(criterion_id, "criterion_id")
    return Path(contract_dir) / run_id / ("tier3-%s%s" % (criterion_id, ARTIFACT_SUFFIX))


def tier3_artifact_contents(run_id: str, criterion_id: str, observation: str) -> str:
    """The placeholder body of a freshly-materialized tier-3 artifact.

    A real file the operator observes into: a cold agent cannot produce the
    perceptual evidence, so this states what the operator must observe and record
    (their finding + `operator_signoff`) before the run can certify.
    """
    want = observation.strip() if observation and observation.strip() else (
        "(see the frozen contract criterion %r)" % criterion_id
    )
    return (
        "# Tier-3 escalation artifact\n\n"
        "run: %s\n"
        "criterion: %s\n\n"
        "A cold agent cannot observe this perceptual (tier-3) property. The "
        "operator must launch the assembled product, observe the behaviour "
        "described below, record the finding here (paste output / attach a "
        "recording), and record operator_signoff in the completion record. The "
        "run does not certify until that sign-off is recorded (PRD C2, "
        "criterion 7).\n\n"
        "Observation required: %s\n"
    ) % (run_id, criterion_id, want)


def build_tier3_escalation(
    criterion_id: str,
    observation: str,
    run_id: str,
    contract_dir: Any = DEFAULT_CONTRACT_DIR,
) -> Dict[str, Any]:
    """Assemble one tier-3 escalation entry - RETURNS data, writes nothing.

    The recorded ``artifact_path`` is the canonical location
    :func:`tier3_artifact_path` derives; ``awaiting_signoff`` is ``True`` because
    at assembly time the operator has not yet signed off.
    """
    return {
        "criterion_id": criterion_id,
        "artifact_path": str(tier3_artifact_path(run_id, criterion_id, contract_dir)),
        "observation": observation,
        "awaiting_signoff": True,
    }
