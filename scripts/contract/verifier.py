#!/usr/bin/env python3
"""Cold verifier logic: parse the frozen contract, assemble driven results.

This module is the verifier's DECISION logic - it does NOT write the completion
record or the provenance side-channel. Those writes belong exclusively to the
chokepoint (`spawn_verifier.py`, PRD C4); `tests/contract/test_custody.py`
asserts that invariant with an AST scan of every `.py` under `scripts/`. This
module therefore only READS the frozen contract and RETURNS a
`spawn_verifier.VerifierObservations`, which the caller hands to
`spawn_verifier.ingest_verifier_results` for token-stamped persistence. Keeping
every write on the far side of that seam is what lets this second stage add the
drive/observe logic without becoming a second custody writer.

Responsibilities (PRD C3, C5, success criteria 6 & 9):

- **Hash re-check at exit (C5, criterion 9).** Before assembling any result, the
  verifier re-hashes the frozen contract. If it no longer matches the hash the
  chokepoint recorded at spawn, the contract moved mid-run: the run ABORTS -
  ``abort_events`` is non-empty and NO criteria results are emitted, so a moved
  target can never be graded (and `validate_completion` stamps the record
  ABORTED, refusing certification).
- **Per-criterion assembly against the frozen list.** The authoritative set of
  criteria (ids and TIERS) comes from the frozen contract, not from the driving
  agent: the agent reports the goal-level outcome it observed per criterion, and
  the verifier stamps each with the contract's tier. An agent cannot relabel a
  tier-1 hard gate as tier-3 to dodge it.
- **Couldn't-drive honesty (C3, criterion 6).** Every criterion the agent could
  not execute - reported ``undriven`` OR silently omitted from its report - is
  listed in ``couldnt_drive``. A pass with a non-empty ``couldnt_drive`` is
  stamped PARTIAL by the validator, never PASS; a run where every criterion
  drives and passes yields PASS with an empty ``couldnt_drive``.
- **Goal, not seams.** The verifier consumes each criterion's ``action`` /
  ``observation`` (the product goal). It never reads the implementation's seam
  vocabulary; the chokepoint prompt forwards the frozen contract verbatim.

The contract-file format is defined in `tests/canaries/README.md`: exactly one
fenced ``yaml`` block with ``class`` and a non-empty ``criteria`` list, each
criterion an ``{id, tier, action, observation}`` mapping. It is parsed here with
the standard library only - no PyYAML: the contract-test CI job runs
``uv run --with pytest`` with no other dependency, matching
`validate_completion.py`'s stdlib-only stance.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# `spawn_verifier` is a flat sibling script (no package), so put this directory
# on the path before importing it. `tests/contract/test_verifier.py` already
# inserts the same directory; this keeps the module importable standalone too.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import spawn_verifier as sv  # noqa: E402


class ContractParseError(ValueError):
    """The frozen contract could not be parsed into criteria."""


class VerifierError(RuntimeError):
    """The verifier logic was handed inconsistent input it cannot assemble."""


# Per-criterion outcomes the driving agent may report. These mirror the
# completion schema's `result` enum exactly, so the assembled record validates.
OUTCOME_PASS = "pass"
OUTCOME_FAIL = "fail"
OUTCOME_UNDRIVEN = "undriven"
OUTCOME_ESCALATED = "escalated"
VALID_OUTCOMES = frozenset(
    {OUTCOME_PASS, OUTCOME_FAIL, OUTCOME_UNDRIVEN, OUTCOME_ESCALATED}
)

_ALLOWED_CLASSES = frozenset({"cli", "interactive", "report", "refactor"})
_ALLOWED_TIERS = frozenset({1, 2, 3})
_CRITERION_FIELDS = ("id", "tier", "action", "observation")


# --------------------------------------------------------------------------- #
# Data model.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Criterion:
    """One frozen contract criterion. ``tier`` is authoritative (A4)."""

    id: str
    tier: int
    action: str
    observation: str


@dataclass(frozen=True)
class Contract:
    """The parsed frozen contract: a class and its non-empty criteria list."""

    cls: str
    criteria: List[Criterion]


@dataclass(frozen=True)
class DrivenResult:
    """One criterion's raw outcome as reported by the cold driving agent.

    ``outcome`` is what the agent OBSERVED, or that it could not drive the
    criterion: ``pass`` / ``fail`` / ``undriven`` / ``escalated``. The agent
    supplies no tier - the verifier reads the authoritative tier from the frozen
    contract. ``artifact_path`` is required for an ``escalated`` (tier-3)
    outcome: the operator-observable artifact the escalation points at.
    """

    criterion_id: str
    outcome: str
    observation: str = ""
    artifact_path: Optional[str] = None


# --------------------------------------------------------------------------- #
# Contract parsing (stdlib-only YAML subset for the fixed contract format).
# --------------------------------------------------------------------------- #

_YAML_BLOCK_RE = re.compile(r"```yaml\s*\n(.*?)\n```", re.DOTALL)


def extract_yaml_block(text: str) -> str:
    """Return the single fenced ``yaml`` block's body, or raise.

    The format mandates exactly one such block (README structural rules); zero
    or several is a malformed contract, not a silently-picked-first one.
    """
    blocks = _YAML_BLOCK_RE.findall(text)
    if len(blocks) != 1:
        raise ContractParseError(
            "expected exactly one fenced yaml block, found %d" % len(blocks)
        )
    return blocks[0]


def _parse_scalar(raw: str) -> str:
    """Unquote a scalar value. Handles double- and single-quoted strings.

    A quoted value may contain the ``: `` that would otherwise split a key from
    a value (e.g. ``action: "Run: python3 ..."``); the split happens on the
    first colon only, so the remainder is unquoted here intact.
    """
    s = raw.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("\"", "'"):
        inner = s[1:-1]
        if s[0] == "\"":
            return inner.replace("\\\"", "\"").replace("\\\\", "\\")
        return inner.replace("''", "'")
    return s


def _split_kv(text: str) -> Tuple[str, str]:
    key, sep, value = text.partition(":")
    if not sep:
        raise ContractParseError("expected 'key: value', got %r" % text)
    return key.strip(), value.strip()


def _build_criterion(raw: Dict[str, str]) -> Criterion:
    missing = [k for k in _CRITERION_FIELDS if k not in raw]
    if missing:
        raise ContractParseError(
            "criterion %r missing field(s): %s"
            % (raw.get("id", "<no-id>"), ", ".join(missing))
        )
    try:
        tier = int(raw["tier"])
    except (TypeError, ValueError):
        raise ContractParseError(
            "criterion %r tier is not an integer: %r" % (raw["id"], raw["tier"])
        )
    if tier not in _ALLOWED_TIERS:
        raise ContractParseError("criterion %r tier %d not in {1,2,3}" % (raw["id"], tier))
    return Criterion(
        id=raw["id"], tier=tier, action=raw["action"], observation=raw["observation"]
    )


class _BlockParser:
    """Line-oriented parser for the contract's ``class`` + ``criteria`` block.

    A deliberately small state machine over the fixed format - not a general
    YAML parser. Top-level keys sit at indent 0; criteria are ``- ``-prefixed
    list items whose fields are more-indented ``key: value`` lines.
    """

    def __init__(self) -> None:
        self.cls: Optional[str] = None
        self.raw_criteria: List[Dict[str, str]] = []
        self.current: Optional[Dict[str, str]] = None
        self.in_criteria = False

    def feed(self, raw_line: str) -> None:
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            return
        if line[:1] not in (" ", "\t"):
            self._top_level(stripped)
        else:
            self._criteria_line(line, stripped)

    def _top_level(self, stripped: str) -> None:
        self.in_criteria = False
        self.current = None
        key, value = _split_kv(stripped)
        if key == "class":
            self.cls = _parse_scalar(value)
        elif key == "criteria":
            self.in_criteria = True
        else:
            raise ContractParseError("unexpected top-level key %r" % key)

    def _criteria_line(self, line: str, stripped: str) -> None:
        if not self.in_criteria:
            raise ContractParseError("indented line outside criteria: %r" % line)
        if stripped.startswith("-"):
            self.current = {}
            self.raw_criteria.append(self.current)
            item = stripped[1:].strip()
            if item:
                self._add_kv(item)
        elif self.current is None:
            raise ContractParseError("criterion field before any '-' item: %r" % line)
        else:
            self._add_kv(stripped)

    def _add_kv(self, text: str) -> None:
        key, value = _split_kv(text)
        # `current` is a fresh dict set on the '- ' item line before any field.
        assert self.current is not None
        self.current[key] = _parse_scalar(value)

    def finish(self) -> Contract:
        if self.cls is None:
            raise ContractParseError("contract yaml block has no 'class'")
        if self.cls not in _ALLOWED_CLASSES:
            raise ContractParseError(
                "contract class %r not in %s" % (self.cls, sorted(_ALLOWED_CLASSES))
            )
        if not self.raw_criteria:
            raise ContractParseError("contract yaml block has an empty 'criteria' list")
        return Contract(
            cls=self.cls, criteria=[_build_criterion(c) for c in self.raw_criteria]
        )


def parse_contract(text: str) -> Contract:
    """Parse a ``contract.md`` into a :class:`Contract`. Raises on malformed input."""
    parser = _BlockParser()
    for raw_line in extract_yaml_block(text).splitlines():
        parser.feed(raw_line)
    return parser.finish()


# --------------------------------------------------------------------------- #
# Result assembly.
# --------------------------------------------------------------------------- #


def _result_for(dr: Optional[DrivenResult]) -> Tuple[str, str]:
    """The (result, observation) for a criterion given the agent's report.

    A criterion the agent never reported is treated as ``undriven`` - the honest
    couldnt_drive signal (C3), never a silent pass.
    """
    if dr is None:
        return OUTCOME_UNDRIVEN, "criterion not reported by the verifier; treated as undriven"
    return dr.outcome, dr.observation


def _escalation_entry(crit: Criterion, dr: Optional[DrivenResult]) -> Dict[str, Any]:
    """A ``tier3_escalations`` entry for an escalated criterion.

    An escalation with no operator-observable artifact is a lying map of intent
    (it claims a criterion went to the operator but hands them nothing to
    observe), so it is refused loudly rather than assembled into a record the
    validator would later reject as an orphan escalation.
    """
    if dr is None or not dr.artifact_path:
        raise VerifierError(
            "tier-3 criterion %r escalated without an operator artifact_path" % crit.id
        )
    return {
        "criterion_id": crit.id,
        "artifact_path": dr.artifact_path,
        "observation": dr.observation,
    }


def assemble_observations(
    contract: Contract, driven: Iterable[DrivenResult]
) -> sv.VerifierObservations:
    """Assemble the agent's driven results against the frozen contract.

    Iterates the frozen contract's authoritative criteria (not the agent's
    report), stamps each with the contract's tier, and derives ``couldnt_drive``
    and ``tier3_escalations``. Writes nothing; returns raw observations for the
    chokepoint to stamp and persist.
    """
    by_id: Dict[str, DrivenResult] = {}
    for d in driven:
        if d.outcome not in VALID_OUTCOMES:
            raise VerifierError(
                "criterion %r has invalid outcome %r (expected one of %s)"
                % (d.criterion_id, d.outcome, sorted(VALID_OUTCOMES))
            )
        by_id[d.criterion_id] = d

    criteria_results: List[Dict[str, Any]] = []
    couldnt_drive: List[str] = []
    tier3: List[Dict[str, Any]] = []

    for crit in contract.criteria:
        dr = by_id.get(crit.id)
        result, observation = _result_for(dr)
        criteria_results.append(
            {"id": crit.id, "tier": crit.tier, "result": result, "observation": observation}
        )
        if result == OUTCOME_UNDRIVEN:
            couldnt_drive.append(crit.id)
        elif result == OUTCOME_ESCALATED:
            tier3.append(_escalation_entry(crit, dr))

    return sv.VerifierObservations(
        criteria_results=criteria_results,
        couldnt_drive=couldnt_drive,
        tier3_escalations=tier3,
        abort_events=[],
    )


# --------------------------------------------------------------------------- #
# Hash re-check at exit (C5) and top-level entry points.
# --------------------------------------------------------------------------- #


def _hash_mismatch_event(spawn: sv.VerifierSpawn, observed_hash: str) -> Dict[str, Any]:
    return {
        "kind": "contract_hash_mismatch",
        "run_id": spawn.run_id,
        "frozen_hash": spawn.contract_hash,
        "observed_hash": observed_hash,
        "detail": (
            "the frozen contract was edited mid-run: its hash no longer matches "
            "the hash the chokepoint recorded at spawn. Aborting rather than "
            "certifying against a moved target (PRD C5)."
        ),
    }


def run_verifier(
    spawn: sv.VerifierSpawn, driven: Iterable[DrivenResult]
) -> sv.VerifierObservations:
    """Run the cold verifier logic for one spawn and RETURN its observations.

    Re-hashes the frozen contract (C5, criterion 9). On mismatch the run aborts
    with a non-empty ``abort_events`` and NO criteria results - a moved target is
    never graded. Otherwise assembles the driven results against the frozen
    contract. Writes nothing: hand the return value to
    ``spawn_verifier.ingest_verifier_results`` for token-stamped persistence.
    """
    observed_hash = sv.hash_contract(spawn.contract_path)
    if observed_hash != spawn.contract_hash:
        return sv.VerifierObservations(
            criteria_results=[],
            couldnt_drive=[],
            tier3_escalations=[],
            abort_events=[_hash_mismatch_event(spawn, observed_hash)],
        )
    contract = parse_contract(spawn.contract_path.read_text(encoding="utf-8"))
    return assemble_observations(contract, driven)


def verify_and_ingest(
    spawn: sv.VerifierSpawn, driven: Iterable[DrivenResult]
) -> Dict[str, Any]:
    """Run the verifier logic and persist via the chokepoint's ingestion seam.

    A thin convenience over :func:`run_verifier` + the chokepoint: the verifier
    stays a pure returner and all record/provenance writes stay inside
    `spawn_verifier.py` (the single custody writer). Returns the written record,
    ready for ``validate_completion.validate``.
    """
    return sv.ingest_verifier_results(spawn, run_verifier(spawn, driven))
