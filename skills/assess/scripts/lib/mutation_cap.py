"""The run-context ``test_pressure`` block shape and the Layer 6 mutation cap.

Shared by the default read-only scan and the opt-in mutation re-run, so both
write the same block shape and derive the cap from it the same way.
"""

from __future__ import annotations

from typing import Any, TypedDict, cast

from lib.run_context_types import TestPressureBlock, TestPressureUnavailable

# The block as ``normalize_test_pressure`` hands it on: a scan that ran, or the
# explicit unavailable marker.
TestPressure = TestPressureBlock | TestPressureUnavailable


class MutationNotRunCap(TypedDict):
    """``run-context.json`` ``mutation_not_run_cap``."""

    applies: bool
    mutation_run: bool
    partial: bool
    max_layer6_band: str
    annotation: str | None


def normalize_test_pressure(test_pressure: Any) -> TestPressure:
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
        # The scan's own block: the two keys mark it as one that ran.
        return cast(TestPressureBlock, test_pressure)
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


# The annotation the LLM must attach to Layer 6 when mutation ran but every group
# that produced records stopped at the time budget. mutmut tests the fastest
# mutants first, so a partial figure is a biased sample, not proof; Layer 6
# stays at Partial until one group completes. assess_finalize enforces it.
MUTATION_PARTIAL_ANNOTATION = "truth-pressure partial (mutation stopped at budget)"


def _partial_only(test_pressure_block: TestPressure) -> bool:
    """True when every mutation group that produced records is ``partial``.

    A group with no records (``mutation_run`` false) adds no evidence either
    way, so it is ignored. One complete group lifts the hold: its files carry
    full mutation evidence, and a partial group beside it is more evidence,
    not less, than a group that never ran, which does not hold the cap today.
    A block with no ``mutation_groups`` (an older run-context, or a pass other
    than mutmut 3) has no partial marker and is never partial-only.
    """
    groups = test_pressure_block.get("mutation_groups")
    if not isinstance(groups, list):
        return False
    ran = [g for g in groups if isinstance(g, dict) and g.get("mutation_run")]
    return bool(ran) and all(g.get("partial") is True for g in ran)


def mutation_not_run_cap(test_pressure_block: TestPressure) -> MutationNotRunCap:
    """The Layer 6 cap the LLM reads: does mutation evidence exist this run?

    ``mutation_run`` is True only when the (opt-in, code-executing) bounded
    mutation pass actually ran - coverage-config detection alone leaves it
    False. When it is False, Layer 6 cannot be scored above Partial and the
    ``annotation`` must be attached; assess_finalize rejects a finalize-input
    that violates this.

    The flag alone is not trusted: a block that claims ``mutation_run`` but
    carries no parsed mutant record in ``per_file`` is evidence-free, so the cap
    stays applied (#317).

    ``partial`` is True when mutation ran but every group with records stopped
    at the budget (``_partial_only``). ``applies`` stays False then, because
    mutation did run and the report's "not run" caveat keys on it, but
    ``max_layer6_band`` holds at Partial with ``MUTATION_PARTIAL_ANNOTATION``.
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
    partial = mutation_run and _partial_only(test_pressure_block)
    if not mutation_run:
        annotation: str | None = MUTATION_NOT_RUN_ANNOTATION
    else:
        annotation = MUTATION_PARTIAL_ANNOTATION if partial else None
    return {
        "applies": not mutation_run,
        "mutation_run": mutation_run,
        "partial": partial,
        "max_layer6_band": "Present" if mutation_run and not partial else "Partial",
        "annotation": annotation,
    }


def layer6_cap_violation(ctx: dict[str, Any], layer6: float | None) -> str | None:
    """Why a Layer 6 score breaks the run's cap, or None when it does not.

    Reads ``mutation_not_run_cap`` from the run-context; one that predates the
    block falls back to ``test_pressure.mutation_run``, and one that predates
    the ``partial`` key reads as not partial, as before. A Present verdict
    (score above 0.5) is refused when mutation did not run, or when it ran on
    partial evidence only. A missing score (a legacy input with no
    ``layer_scores``) has nothing to check.
    """
    cap = ctx.get("mutation_not_run_cap")
    if isinstance(cap, dict):
        mutation_ran = bool(cap.get("mutation_run", False))
        partial = cap.get("partial") is True
    else:
        tp = ctx.get("test_pressure")
        mutation_ran = bool(isinstance(tp, dict) and tp.get("mutation_run", False))
        partial = False
    if layer6 is None or layer6 <= 0.5 or (mutation_ran and not partial):
        return None
    if not mutation_ran:
        return ("Layer 6 cannot exceed Partial when mutation testing was not run "
                f"(scored {layer6}). Annotation required: "
                f"'{MUTATION_NOT_RUN_ANNOTATION}'")
    return ("Layer 6 cannot exceed Partial when every mutation group stopped at "
            f"the time budget (scored {layer6}); partial figures cover only the "
            f"fastest mutants. Annotation required: '{MUTATION_PARTIAL_ANNOTATION}'")
