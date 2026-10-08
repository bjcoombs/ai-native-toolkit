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

import sys
from pathlib import Path
from typing import Any

from lib.assess_config import load_excludes
from lib.keyhole_signals import apply_config_excludes, assemble_findings, finding_products
from lib.untrusted_hotspot import untrusted_hotspot_paths

# Run-context keys rewritten from ``finding_products``, in the order the default
# run writes them.
_PRODUCT_KEYS = (
    "attention", "attention_low_signal", "findings_markdown",
    "keyhole_summary", "prescribed_actions",
)

# Key in ``excluded_by_config`` naming the paths a mutation pass added, so a
# later pass can take them back out when E1 no longer names them.
_PASS_ADDED_KEY = "added_by_mutation_pass"


def _rebuilt_findings(findings: list[Any], untrusted: list[str]) -> list[dict]:
    """``findings`` in ``FINDING_ORDER`` with ``untrusted_hotspot`` replaced.

    Stored actions are kept (the ``unactioned_intent`` action carries the stale
    threshold the default run appended). Only ``untrusted_hotspot`` is inserted
    when an older run-context lacks it; any other finding missing from the
    stored list stays missing, since this pass never computed it.
    """
    stored = [f for f in findings if isinstance(f, dict) and "name" in f]
    paths_by_name = {f["name"]: list(f.get("paths") or []) for f in stored}
    paths_by_name["untrusted_hotspot"] = untrusted
    actions = {f["name"]: f["action"] for f in stored if f.get("action")}
    rebuilt = [f for f in assemble_findings(paths_by_name) if f["name"] in paths_by_name]
    for f in rebuilt:
        f["action"] = actions.get(f["name"], f["action"])
    return rebuilt


def _merge_config_exclusions(ctx: dict[str, Any], excluded: list[str]) -> None:
    """Rebuild the ``excluded_by_config`` paths with this pass's E1 exclusions.

    Paths an earlier pass added are removed first, so the disclosure never
    names a suppressed E1 path this pass did not suppress. A path the default
    run already disclosed is never recorded as added, so it is never removed.
    """
    block = ctx.get("excluded_by_config")
    if not isinstance(block, dict):
        return
    prior_added = set(block.pop(_PASS_ADDED_KEY, None) or [])
    if not excluded and not prior_added:
        return
    base = set(block.get("affected_finding_paths") or []) - prior_added
    paths = sorted(base | set(excluded))
    block["affected_finding_paths"] = paths
    block["count"] = len(paths)
    added = sorted(set(excluded) - base)
    if added:
        block[_PASS_ADDED_KEY] = added


def recorded_excludes(ctx: dict[str, Any], repo_root: Path) -> tuple[set[str], list[str]]:
    """The config excludes the default run applied, as its disclosure records them.

    Filtering E1 with these, not a fresh read of ``.assess/config.toml``, keeps
    the pass applying exactly the filter ``excluded_by_config`` names. Falls back
    to ``load_excludes`` when the run-context carries no well-formed block.
    """
    block = ctx.get("excluded_by_config")
    if isinstance(block, dict):
        dirs, patterns = block.get("dirs"), block.get("patterns")
        if isinstance(dirs, list) and isinstance(patterns, list):
            return set(dirs), list(patterns)
    return load_excludes(repo_root)


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
    ``_PRODUCT_KEYS`` and the ``excluded_as_archive`` disclosure, and rebuilds
    ``excluded_by_config`` with this pass's config-excluded E1 paths (recorded
    under ``added_by_mutation_pass`` so a later pass can retract them).

    Returns False and leaves ``ctx`` untouched when it carries no
    ``derived_findings`` list (a run-context older than the keyhole findings):
    there is nothing to rank against, and inventing the other findings would
    misreport them. A failure while recomputing also returns False with ``ctx``
    untouched, after a warning on stderr naming the error, so the caller still
    writes the refreshed ``test_pressure`` and the stale findings are not silent.
    """
    findings = ctx.get("derived_findings")
    if not isinstance(findings, list):
        return False
    try:
        rebuilt, excluded, products = _recompute(
            ctx, findings, complexity_stats, exclude_dirs, exclude_patterns,
        )
    except Exception as exc:  # noqa: BLE001 - keep the mutation result, never crash
        print(f"warning: keyhole findings not refreshed after the mutation pass "
              f"({type(exc).__name__}: {exc}); derived_findings keep the default "
              f"run's values", file=sys.stderr)
        return False
    ctx["derived_findings"] = rebuilt
    for key in _PRODUCT_KEYS:
        ctx[key] = products[key]
    archived = products["archived_finding_paths"]
    ctx["excluded_as_archive"] = {
        "affected_finding_paths": archived, "count": len(archived),
    }
    _merge_config_exclusions(ctx, excluded)
    return True


def _recompute(
    ctx: dict[str, Any], findings: list[Any], complexity_stats: dict,
    exclude_dirs: set[str], exclude_patterns: list[str],
) -> tuple[list[dict], list[str], dict]:
    """The rebuilt findings, newly excluded E1 paths and finding products."""
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
    return rebuilt, excluded, products
