"""Rebuild the test-pressure-dependent keyhole products after the opt-in mutation pass.

The default ``/assess`` run never mutates, so the E1 ``untrusted_hotspot``
finding is always empty there. The consent-gated opt-in pass
(``assess_core --opt-in-mutation``) rewrites ``test_pressure`` with per-file
survivor counts; without this module every block derived from it would still
read the mutation-off values, and E1 could never fire in a real run.

Of the derived findings, only ``untrusted_hotspot`` reads ``test_pressure``; the
others come from git history, docs and the scans the pass does not repeat, so
they are kept as stored. The finding is recomputed through the same functions
``keyhole_signals.integrate`` uses, then the attention list and every report
product are rebuilt through ``keyhole_signals.finding_products``, so a refreshed
run ranks and renders exactly as a default run with the same inputs would.
"""

from __future__ import annotations

from typing import Any

from lib.keyhole_signals import apply_config_excludes, assemble_findings, finding_products
from lib.untrusted_hotspot import untrusted_hotspot_paths

# Run-context keys rewritten from ``finding_products``, in the order the default
# run writes them.
_PRODUCT_KEYS = (
    "attention", "attention_low_signal", "findings_markdown",
    "keyhole_summary", "prescribed_actions",
)


def _rebuilt_findings(findings: list[Any], untrusted: list[str]) -> list[dict]:
    """``findings`` in ``FINDING_ORDER`` with ``untrusted_hotspot`` replaced.

    Stored actions are kept (the ``unactioned_intent`` action carries the stale
    threshold the default run appended); a finding missing from an older
    run-context comes back with its default action.
    """
    stored = [f for f in findings if isinstance(f, dict) and "name" in f]
    paths_by_name = {f["name"]: list(f.get("paths") or []) for f in stored}
    paths_by_name["untrusted_hotspot"] = untrusted
    actions = {f["name"]: f["action"] for f in stored if f.get("action")}
    rebuilt = assemble_findings(paths_by_name)
    for f in rebuilt:
        f["action"] = actions.get(f["name"], f["action"])
    return rebuilt


def _merge_config_exclusions(ctx: dict[str, Any], excluded: list[str]) -> None:
    """Add newly excluded E1 paths to the ``excluded_by_config`` disclosure."""
    block = ctx.get("excluded_by_config")
    if not excluded or not isinstance(block, dict):
        return
    paths = sorted(set(block.get("affected_finding_paths") or []) | set(excluded))
    block["affected_finding_paths"] = paths
    block["count"] = len(paths)


def refresh_mutation_findings(
    ctx: dict[str, Any],
    complexity_stats: dict,
    exclude_dirs: set[str],
    exclude_patterns: list[str],
) -> bool:
    """Recompute E1 and its downstream products in ``ctx`` from ``test_pressure``.

    ``complexity_stats`` is this run's ``complexity-stats.json`` (scoped when the
    run was); ``exclude_dirs`` / ``exclude_patterns`` are the config excludes the
    default run applied. Rewrites ``derived_findings``, the products in
    ``_PRODUCT_KEYS`` and the ``excluded_as_archive`` disclosure, and adds any
    config-excluded E1 path to ``excluded_by_config``.

    Returns False and leaves ``ctx`` untouched when it carries no
    ``derived_findings`` list (a run-context older than the keyhole findings):
    there is nothing to rank against, and inventing the other findings would
    misreport them.
    """
    findings = ctx.get("derived_findings")
    if not isinstance(findings, list):
        return False
    paths = untrusted_hotspot_paths(complexity_stats, ctx.get("test_pressure"))
    filtered, excluded = apply_config_excludes(
        [{"name": "untrusted_hotspot", "paths": paths}], exclude_dirs, exclude_patterns,
    )
    rebuilt = _rebuilt_findings(findings, filtered[0]["paths"])
    promissory = ctx.get("promissory_markers")
    behaviour = ctx.get("behaviour")
    products = finding_products(
        rebuilt, complexity_stats,
        promissory if isinstance(promissory, dict) else None,
        behaviour if isinstance(behaviour, dict) else {},
    )
    ctx["derived_findings"] = rebuilt
    for key in _PRODUCT_KEYS:
        ctx[key] = products[key]
    archived = products["archived_finding_paths"]
    ctx["excluded_as_archive"] = {"affected_finding_paths": archived, "count": len(archived)}
    _merge_config_exclusions(ctx, excluded)
    return True
