"""The producer input class and grade of every default maturity-model signal (#783).

Pure data with no imports from ``lib``, so the tally engine (``lib/level_tally.py``)
and the run context's ``level`` entry can read it without reaching the scanners.
``lib.level_signals`` re-exports every name and documents the classes and rulings.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

DEFAULT_LEVELS = Path(__file__).resolve().parents[2] / "levels" / "default.toml"

InputClass = Literal["config_text", "head_text", "head_structure", "ci_executed",
                     "git_history", "execution_artifact", "platform"]
Grade = Literal["config_only", "declared", "measured", "verified"]

INPUT_GRADES: dict[InputClass, Grade] = {
    "config_text": "config_only",
    "head_text": "declared",
    "head_structure": "measured",
    "git_history": "measured",
    "execution_artifact": "measured",
    "ci_executed": "verified",
    "platform": "verified",
}
LISTED_GRADES: frozenset[str] = frozenset({"config_only", "declared"})

# One entry per exact signal path; a new criterion signal needs its own line.
SIGNAL_INPUTS: dict[str, InputClass] = {
    "layer_grades.instruction_file_present": "head_text",
    "agent_environment.check.named_total": "head_text",
    "agent_environment.pinning.unlocked_total": "head_text",
    "instruction_claims.failed": "head_structure",
    "nested_instructions.budget.claude_code.lines": "head_structure",
    "observability.rung": "head_text",
    "structure.modularity_q": "head_structure",
    "structure.cross_package_edges": "head_structure",
    "structure.front_door_ratio": "head_structure",
    "promissory_markers.families.suppression.stale": "git_history",
    "promissory_markers.aging_reliable": "git_history",
    "structure_drift.tier_0.boundary_patterns": "config_text",
    "stats_summary.loc.p95": "head_structure",
    "ci_checks.enforced_check_runs": "ci_executed",
    "ci_checks.lint_runs": "ci_executed",
    "coverage_gate.enforced_meets_minimum": "ci_executed",
    "test_pressure.mutation_run": "execution_artifact",
    "review_evidence.present": "head_text",
    "review_reality.reviewed_share": "platform",
    "review_reality.approved_share": "platform",
    "agent_ops.summary.permissions_encoded": "config_text",
    "agent_ops.summary.hooks_present": "config_text",
    "agent_ops.summary.routines_present": "config_text",
    "doc_staleness.summary.stale_pct": "git_history",
    "guardrails.stated_pct": "head_structure",
    "agent_consistency.conflicts": "head_structure",
    "agent_consistency.unlinked": "head_structure",
}


# Signal -> (the value meaning "could not be measured", the tally's reason code).
UNMEASURED_VALUES: dict[str, tuple[bool, str]] = {
    "test_pressure.mutation_run": (False, "needs_full_run"),
    "promissory_markers.aging_reliable": (False, "degenerate_history"),
}
