"""Synthetic repo validator for the flawed-instruction-file fixture.

This stands in for a real repo's CI count gate. The fixture's CLAUDE.md tells a
cold-start agent to update the component count in a *strict subset* of the
surfaces this validator enforces (the count-surface trap). An agent that trusts
the checklist updates the listed surfaces, leaves the unlisted-but-enforced
surface stale, and this validator fails - exactly the HIGH accuracy defect
skill-forge's Fidelity accuracy sub-check must catch.

ENFORCED_COUNT_SURFACES is the ground truth. The fixture's CLAUDE.md checklist
names only a subset of it; ``tests`` in the plugin contract suite assert that the
checklist is a proper subset and that this validator reports a stale surface when
only the checklist surfaces are updated.
"""
from __future__ import annotations

# Every surface the CI count gate enforces. The component count must match across
# all of them or the build fails. The fixture's CLAUDE.md omits one of these.
ENFORCED_COUNT_SURFACES: tuple[str, ...] = (
    "README.md",      # the "N components" badge line
    "catalog.json",   # the "total" field
    "docs/index.md",  # the catalog footer count - the surface the checklist omits
)


def stale_surfaces(surface_counts: dict[str, int], expected: int) -> list[str]:
    """Return the enforced surfaces whose recorded count != *expected*.

    A non-empty result is a build break: a surface the gate enforces is out of
    sync. A surface missing from *surface_counts* counts as stale (never updated).
    """
    return [
        surface
        for surface in ENFORCED_COUNT_SURFACES
        if surface_counts.get(surface) != expected
    ]


def build_passes(surface_counts: dict[str, int], expected: int) -> bool:
    """The CI count gate passes only when no enforced surface is stale."""
    return not stale_surfaces(surface_counts, expected)
