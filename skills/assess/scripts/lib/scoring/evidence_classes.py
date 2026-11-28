"""The evidence class of each condition and the classes a run covered, derived from the input classes (#1642).

Three evidence classes (issue #1344, requirement 1) say what kind of evidence a check rests on:

- ``static``: files, configuration and history in source control. The input classes
  ``config_text``, ``head_text``, ``head_structure``, ``git_history`` and ``ci_executed``.
  ``ci_executed`` reads a CI file that CI runs on every change; the scan reads the file, not a
  run's outcome, so it is static evidence however strong its grade;
- ``runtime``: the output of running the code (CI and build outcomes): ``execution_artifact``,
  such as the opt-in mutation run;
- ``practice``: how the team works, read from GitHub or GitLab: ``platform`` (review and
  issue data).

``INPUT_EVIDENCE`` is the one table, keyed by ``lib.scoring.signal_grades.InputClass``; no check
carries a hand-set tag (maintainer decision on #1344). ``criteria_classes(criteria)`` gives a
condition's classes: those of its criteria's signals (``SIGNAL_INPUTS``), once each, in
``EVIDENCE_CLASSES`` order; a signal with no input class (a ``--pack`` check) adds none.
``lib.scoring.level_tally`` sets it on every condition as ``evidence_classes``.

``evidence_coverage(conditions)`` is the level result's ``evidence_coverage``: ``covered``, the
classes at least one condition read a value of (state ``met`` or ``unmet``), and ``not_covered``,
every other class with a reason code: the first not-measured reason among its conditions, in
model order, or ``no_condition`` when no condition of the model reads it, or ``not_read`` when
its conditions were excluded, declined or not applicable. A headless run without ``--platform``
so names ``practice``. Every value is a class token or a reason code, so both survive
counts-only output. The report header line is ``lib/report/evidence_header.py``.

Tests: ``tests/test_evidence_classes.py``.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from lib.scoring.level_result_types import ConditionResult, EvidenceCoverage, NotCovered
from lib.scoring.levels import Criterion
from lib.scoring.signal_grades import SIGNAL_INPUTS, InputClass

EvidenceClass = Literal["static", "runtime", "practice"]
EVIDENCE_CLASSES: tuple[EvidenceClass, ...] = ("static", "runtime", "practice")

INPUT_EVIDENCE: dict[InputClass, EvidenceClass] = {
    "config_text": "static",
    "head_text": "static",
    "head_structure": "static",
    "git_history": "static",
    "ci_executed": "static",
    "execution_artifact": "runtime",
    "platform": "practice",
}

_READ_STATES = frozenset({"met", "unmet"})


def criteria_classes(criteria: Sequence[Criterion]) -> list[str]:
    """The evidence classes of a condition's criteria, once each, in ``EVIDENCE_CLASSES`` order."""
    found = {INPUT_EVIDENCE[SIGNAL_INPUTS[c["signal"]]] for c in criteria if c["signal"] in SIGNAL_INPUTS}
    return [e for e in EVIDENCE_CLASSES if e in found]


def evidence_coverage(conditions: Sequence[ConditionResult]) -> EvidenceCoverage:
    """The classes the conditions read a value of, and every other class with its reason code."""
    covered: list[str] = []
    not_covered: list[NotCovered] = []
    for ec in EVIDENCE_CLASSES:
        mine = [c for c in conditions if ec in c.get("evidence_classes", [])]
        if any(c["state"] in _READ_STATES for c in mine):
            covered.append(ec)
            continue
        reason = next((c.get("reason", "no_value") for c in mine if c["state"] == "not_measured"),
                      "not_read" if mine else "no_condition")
        not_covered.append({"evidence_class": ec, "reason": reason})
    return {"covered": covered, "not_covered": not_covered}
