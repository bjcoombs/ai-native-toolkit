"""The run-context ``test_pressure`` block shape and the Layer 6 mutation cap.

Shared by the default read-only scan and the opt-in mutation re-run, so both
write the same block shape and derive the cap from it the same way.
"""

from __future__ import annotations

from typing import Any


def normalize_test_pressure(test_pressure: Any) -> dict[str, Any]:
    """Normalize a ``scan_test_pressure`` result into the run-context block shape.

    A failed (or malformed) scan must not read as "no mutation setup": that
    would mis-score Layer 1 just as a failed liveness scan would mis-score
    observability. On a bad result, carry an explicit unavailable marker with a
    null ``mutation_config_present`` and empty heuristic buckets so the LLM sees
    "not assessed", never a false negative. Keys mirror the real block's
    ``cheap_heuristics`` schema (assertion_on_internal / untested_boundaries /
    duplicate_truth) so the consumer's shape doesn't change on the failure path.

    Shared by the default read-only scan (``build_run_context``) and the opt-in
    mutation re-run (``run_opt_in_mutation``) so both write an identical shape.
    """
    if (isinstance(test_pressure, dict)
            and "mutation_config_present" in test_pressure
            and "cheap_heuristics" in test_pressure):
        return test_pressure
    return {
        "available": False,
        "reason": (test_pressure.get("reason")
                   if isinstance(test_pressure, dict)
                   else "test_pressure scan unavailable"),
        "mutation_config_present": None,
        "cheap_heuristics": {
            "assertion_on_internal": [],
            "untested_boundaries": [],
            "duplicate_truth": [],
        },
    }


# The annotation the LLM must attach to Layer 6 when mutation testing never
# ran. Layer 6 (truth pressure) asks whether the suite *proves* behaviour, not
# merely visits it - a claim only a mutation run can substantiate. Absent that
# run, the strongest honest verdict is Partial; a Present claim would be an
# unproven self-description, exactly the guardrail-erosion failure /assess
# exists to catch. assess_finalize enforces the cap deterministically.
MUTATION_NOT_RUN_ANNOTATION = "truth-pressure unproven (mutation not run)"


def mutation_not_run_cap(test_pressure_block: dict[str, Any]) -> dict[str, Any]:
    """The Layer 6 cap the LLM reads: does mutation evidence exist this run?

    ``mutation_run`` is True only when the (opt-in, code-executing) bounded
    mutation pass actually ran - coverage-config detection alone leaves it
    False. When it is False, Layer 6 cannot be scored above Partial and the
    ``annotation`` must be attached; assess_finalize rejects a finalize-input
    that violates this.

    The flag alone is not trusted: a block that claims ``mutation_run`` but
    carries no parsed mutant record in ``per_file`` is evidence-free, so the cap
    stays applied (#317).
    """
    per_file = (
        test_pressure_block.get("per_file")
        if isinstance(test_pressure_block, dict) else None
    )
    mutation_run = bool(
        isinstance(test_pressure_block, dict)
        and test_pressure_block.get("mutation_run", False)
        and isinstance(per_file, list)
        and any(isinstance(rec, dict) for rec in per_file)
    )
    return {
        "applies": not mutation_run,
        "mutation_run": mutation_run,
        "max_layer6_band": "Present" if mutation_run else "Partial",
        "annotation": None if mutation_run else MUTATION_NOT_RUN_ANNOTATION,
    }
