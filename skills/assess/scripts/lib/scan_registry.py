"""Declared table of run-context scans, and the loop that runs them.

``assess_core.build_run_context`` grew by one import and one assignment per
scan until it was the most complex function in the repository, and the "a
broken scan must never block the assessment" rule held only where the author
remembered to wrap the call. Here a scan is declared once - its run-context
key, its callable, what it reads, how it degrades - and one loop runs the
table, so the degrade wrapper applies by construction and adding a scan does
not edit ``build_run_context``. The shape follows ``_DEAD_CODE_TOOLS`` in
``liveness_scan``: spec entries plus a single driving loop.

The table is validated when this module is imported. A duplicate key, an unknown
stage, a read of a name that neither the core provides nor an earlier scan
produces, a read of a key produced at a later stage, a callable that cannot take
as many positional arguments as it declares reads, or a scan that opts out of
degrading without a ``gate_reason`` raises ``ScanRegistryError`` before any run
starts.

Migration is incremental. ``stage`` names the point in ``build_run_context``
where an entry runs, which keeps the key order of ``run-context.json``
unchanged while scans move here in batches; the stages collapse once the
hand-wired assignments between them are gone.
"""
from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from functools import partial
from typing import Any

from lib.agent_ops import scan_agent_ops
from lib.config_drift import scan_config_drift
from lib.gate_cost import estimate_gate_cost
from lib.instruction_claims import scan_instruction_claims
from lib.review_reality import scan_review_reality

# Names the core passes to ``run_scans``. A spec may read these, or the key of
# any spec declared before it.
PROVIDED_INPUTS = frozenset({"repo_root", "instruction_files"})

STAGE_READ_SIDE = "read_side"
STAGE_POST_OFFERS = "post_offers"
STAGES = (STAGE_READ_SIDE, STAGE_POST_OFFERS)


class ScanRegistryError(ValueError):
    """The scan table is malformed (duplicate key, unknown stage or read)."""


@dataclass(frozen=True)
class ScanSpec:
    """One run-context block.

    ``fn`` is called with the values of ``reads``, positionally and in order.
    ``degrade`` routes the call through ``safe``; a scan opts out only by
    setting ``gate_reason``, because a gate is the one kind of scan whose
    failure should stop the run.
    """

    key: str
    fn: Callable[..., Any]
    reads: tuple[str, ...]
    stage: str
    degrade: bool = True
    gate_reason: str = ""


def safe(label: str, fn: Callable[[], Any]) -> Any:
    """Run a read-side scan, degrading to an unavailable marker on any failure.

    Read-side signals are additive context for the LLM, never gates - a broken
    scan must never block the assessment (PRD: "never block").
    """
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - intentional catch-all; degrade, don't crash
        return {"available": False, "reason": f"{label} scan failed: {e}"}


def _accepts(fn: Callable[..., Any], count: int) -> bool:
    """True when ``fn`` can be called with ``count`` positional arguments.

    A callable whose signature cannot be inspected is given the benefit of the
    doubt; the mismatch this guards against is a declared table entry, which is
    always a plain function.
    """
    try:
        inspect.signature(fn).bind(*[None] * count)
    except TypeError:
        return False
    except ValueError:
        return True
    return True


def validate(specs: Iterable[ScanSpec], provided: Iterable[str] = PROVIDED_INPUTS) -> None:
    """Raise ``ScanRegistryError`` unless every spec can run in table order.

    Stages run in ``STAGES`` order, so a read must name a provided input or a key
    whose producer is declared earlier *and* runs at the same or an earlier stage.
    """
    produced_at: dict[str, int] = {name: -1 for name in provided}
    for spec in specs:
        if spec.key in produced_at:
            raise ScanRegistryError(f"scan key {spec.key!r} is declared twice or shadows a provided input")
        if spec.stage not in STAGES:
            raise ScanRegistryError(f"scan {spec.key!r} names unknown stage {spec.stage!r}")
        stage_index = STAGES.index(spec.stage)
        missing = [name for name in spec.reads if name not in produced_at]
        if missing:
            raise ScanRegistryError(
                f"scan {spec.key!r} reads {missing}, which no earlier scan produces "
                f"and the core does not provide"
            )
        late = [name for name in spec.reads if produced_at[name] > stage_index]
        if late:
            raise ScanRegistryError(
                f"scan {spec.key!r} runs at stage {spec.stage!r} but reads {late}, "
                f"produced at a later stage"
            )
        if not _accepts(spec.fn, len(spec.reads)):
            raise ScanRegistryError(
                f"scan {spec.key!r} declares {len(spec.reads)} read(s) but its callable "
                f"cannot be called with that many positional arguments"
            )
        if not spec.degrade and not spec.gate_reason:
            raise ScanRegistryError(f"scan {spec.key!r} opts out of degrading without a gate_reason")
        produced_at[spec.key] = stage_index


def _resolve(spec: ScanSpec, name: str, inputs: Mapping[str, Any], ctx: Mapping[str, Any]) -> Any:
    if name in inputs:
        return inputs[name]
    if name in ctx:
        return ctx[name]
    raise ScanRegistryError(
        f"scan {spec.key!r} reads {name!r}, which is neither in the inputs the core "
        f"passed nor in the run context yet"
    )


def run_scans(
    ctx: dict[str, Any],
    inputs: Mapping[str, Any],
    stage: str,
    specs: Iterable[ScanSpec] | None = None,
) -> None:
    """Run every spec of ``stage`` in table order, assigning ``ctx[spec.key]``.

    A read resolves against ``inputs`` first, then against ``ctx``. Reads are
    resolved outside the degrade wrapper: an unresolvable read is a wiring error
    in the table or the core, so it raises ``ScanRegistryError`` and stops the
    run, while a failure inside the scan itself degrades.
    """
    for spec in SCANS if specs is None else specs:
        if spec.stage != stage:
            continue
        args = tuple(_resolve(spec, name, inputs, ctx) for name in spec.reads)
        if spec.degrade:
            ctx[spec.key] = safe(spec.key, partial(spec.fn, *args))
        else:
            ctx[spec.key] = spec.fn(*args)


SCANS: tuple[ScanSpec, ...] = (
    # Agent-operations guardrails (permission allowlists, hooks, sandbox rules,
    # routine definitions): Layer 8 workflow-maturity evidence. Tracked-only
    # credit - an uncommitted settings file reaches no clone. Deliberately
    # excludes .claude/agents/ and .claude/skills/ (Layer 0's evidence) so the
    # two layers never double-count the same artifact.
    ScanSpec("agent_ops", scan_agent_ops, ("repo_root",), STAGE_READ_SIDE),
    # Configuration drift (Layer 5 lying signal): tracked ruleset and
    # branch-protection snapshots diffed against the live GitHub setting via
    # `gh`. Optional: no remote, no `gh`, no auth or a refused read degrades to
    # available: false with the reason, never a clean result.
    ScanSpec("config_drift", scan_config_drift, ("repo_root",), STAGE_READ_SIDE),
    ScanSpec("review_reality", scan_review_reality, ("repo_root",), STAGE_READ_SIDE),
    ScanSpec("gate_cost_estimate", estimate_gate_cost, ("repo_root",), STAGE_READ_SIDE),
    # Checkable claims in the graded instruction files ("`x.sh` is enforced in
    # CI", "Node 20.11.0 is pinned in `.nvmrc`"), verified against the repo; a
    # failed claim is a Layer 0 lying signal. Zeros when none.
    ScanSpec(
        "instruction_claims", scan_instruction_claims,
        ("repo_root", "instruction_files"), STAGE_POST_OFFERS,
    ),
)

validate(SCANS)
