"""The report header's sentences naming the evidence classes a run read and the ones it did not (#1642).

``evidence_line(coverage)`` words a level result's ``evidence_coverage``
(``lib/scoring/evidence_classes.py``) as the end of the report's "Generated" header line, so it
stays in the run header and off the top card's first screen:
``Evidence read: static (files and history in source control). Not read: runtime (needs a full
run), practice (needs GitHub or GitLab access).``. Each class read carries ``CLASS_WORDS``; each
class not read carries its reason code in the headline's words (``headline.reason_word``), with
``NOT_COVERED_WORDS`` for the two codes only coverage gives. With every class read the line has
no "Not read" part; with none read, no "Evidence read" part. A run context written before #1642,
or one whose coverage is malformed, gives ``""``, so the header renders as it did.

``assess_report.py`` appends it to the deterministic report's "Generated" line, and the
``assess-findings`` report template copies it from there verbatim.
Tests: ``tests/test_evidence_classes.py``.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from lib.scoring.headline import reason_word

# What each evidence class reads, in the reader's words.
CLASS_WORDS = {
    "static": "files and history in source control",
    "runtime": "results of running the code",
    "practice": "how the team works, from GitHub or GitLab",
}

# The reason codes only evidence coverage gives (lib.scoring.evidence_classes.evidence_coverage).
NOT_COVERED_WORDS = {
    "no_condition": "no check of the model reads it",
    "not_read": "its checks were set aside",
}


def evidence_line(coverage: Any) -> str:
    """The header line for ``coverage``; ``""`` when it is not an evidence coverage block."""
    if not isinstance(coverage, Mapping):
        return ""
    covered, missing = coverage.get("covered"), coverage.get("not_covered")
    if not isinstance(covered, list) or not isinstance(missing, list):
        return ""
    read = ", ".join(f"{c} ({CLASS_WORDS.get(c, c)})" for c in covered if isinstance(c, str))
    unread = ", ".join(
        f"{e['evidence_class']} ({NOT_COVERED_WORDS.get(str(e.get('reason')), reason_word(str(e.get('reason', 'no_value'))))})"
        for e in missing if isinstance(e, Mapping) and isinstance(e.get("evidence_class"), str))
    parts = [f"Evidence read: {read}." if read else "", f"Not read: {unread}." if unread else ""]
    return " ".join(p for p in parts if p)
