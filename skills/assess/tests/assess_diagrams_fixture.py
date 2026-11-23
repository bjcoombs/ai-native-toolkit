"""The fixture level result and golden renders under ``tests/fixtures/assess_diagrams/`` (#942).

``LEVEL`` is a hand-built ``LevelResult`` with every waffle category, including the
``declined`` and ``not_applicable`` states no rule produces yet, and not-measured
conditions with a plain and a ``requires:`` reason, at Step 3 with Step 4 next under the
shipped model's Steps (the Step ladder, #1102). ``test_assess_diagrams.py`` fails
when a committed golden differs from a fresh render. Regenerate the goldens with
``uv run --locked python tests/assess_diagrams_fixture.py`` from ``skills/assess``, then
review the diff.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib.assess_diagrams import LADDER_FILE, WAFFLE_FILE, build_diagrams, diagrams_section  # noqa: E402
from lib.level_result_types import ConditionResult, LevelResult  # noqa: E402
from lib.run_context_types import JsonDict  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "assess_diagrams"

MODEL_ROWS: list[JsonDict] = [
    {"id": "layer-0-instructions", "title": "Layer 0: agent instructions"},
    {"id": "layer-5-ci", "title": "Layer 5: CI and the agent's local check"},
    {"id": "guardrails", "title": "Guardrails stated in the instruction files & <docs> beside them"},
    {"id": "review", "title": "Review on merged changes"},
]


def _c(cid: str, state: str, grade: str | None, reason: str | None = None) -> ConditionResult:
    row, _, level = cid.rpartition(".")
    out: ConditionResult = {"id": cid, "row": row, "level": int(level), "grade": grade,
                            "counted": grade in ("measured", "verified"), "state": state}
    if reason is not None:
        out["reason"] = reason
    return out


CONDITIONS: list[ConditionResult] = [
    _c("layer-0-instructions.1", "met", "declared"),
    _c("layer-0-instructions.2", "met", "measured"),
    _c("layer-0-instructions.3", "unmet", "measured"),
    _c("layer-5-ci.1", "met", "verified"),
    _c("layer-5-ci.2", "met", "config_only"),
    _c("layer-5-ci.3", "not_measured", "verified", "needs_platform"),
    _c("layer-5-ci.4", "not_measured", "verified", "requires:layer-5-ci.3"),
    _c("guardrails.1", "declined", "declared"),
    _c("guardrails.2", "not_applicable", "measured"),
    _c("guardrails.3", "met", None),
    _c("review.1", "not_measured", "verified", "needs_platform"),
    _c("review.2", "met", "verified"),
    _c("review.3", "unmet", "verified"),
]

LEVEL: LevelResult = {
    "level_result_version": 1,
    "levels_version": "fixture",
    "tally": {"met": 3, "measured": 5, "not_measured": 3},
    "counted": ["layer-0-instructions.2", "layer-5-ci.1", "review.2"],
    "listed": ["layer-0-instructions.1", "layer-5-ci.2", "guardrails.3"],
    "not_measured": [{"id": c["id"], "reason": c["reason"]} for c in CONDITIONS if "reason" in c],
    "conditions": CONDITIONS,
    "step": 3,
    "next_step": {"step": 4, "threshold": 5,
                  "blockers": [{"kind": "gate", "gate": "coverage_gate",
                                "condition": "layer-6-coverage.1", "state": "unmet"}]},
}


def goldens() -> dict[str, str]:
    """The golden renders: ``expected-<file>`` -> text."""
    diagrams = build_diagrams({"level": LEVEL}, MODEL_ROWS)
    files = {f"expected-{d['file']}": d["svg"] for d in diagrams if d["svg"] is not None}
    assert f"expected-{WAFFLE_FILE}" in files and f"expected-{LADDER_FILE}" in files
    return {**files, "expected-section.md": diagrams_section(diagrams) + "\n"}


if __name__ == "__main__":
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for name, content in goldens().items():
        (FIXTURES / name).write_text(content, encoding="utf-8", newline="\n")
