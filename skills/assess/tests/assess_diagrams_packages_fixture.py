"""The fixture structure blocks and golden renders of the package map (#942).

``SMALL`` is a six-package graph with every cell kind but unknown: imports, imports with a
front-door bypass, no imports and the diagonal; one package name is long and holds markup
characters. ``LARGE`` has 230 packages (the block keeps 200, the map draws 20), imports to
packages past the top, imports the block does not record and the 400-pair cap applied, so
cells between drawn packages with no recorded pair are hatched. ``test_assess_diagrams_packages.py``
fails when a committed golden differs from a fresh render. Regenerate with
``uv run --locked python tests/assess_diagrams_packages_fixture.py`` from ``skills/assess``,
then review the diff.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib.assess_diagrams import diagrams_section
from lib.assess_diagrams_packages import package_diagrams, render_package_map
from lib.run_context_types import JsonDict

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "assess_diagrams"


def _edge(source: str, target: str, imports: int, bypass: int = 0) -> JsonDict:
    return {"from": source, "to": target, "imports": imports, "bypass": bypass}


_SMALL_PACKAGES = [("app", 4200, 31), ("services/billing/internal/ledger<&>core", 2900, 18),
                   ("lib", 2100, 12), ("web", 900, 9), ("cli", 300, 4), ("tools", 120, 2)]
_SMALL_EDGES = [
    _edge("app", "lib", 70, 3), _edge("app", "services/billing/internal/ledger<&>core", 12),
    _edge("web", "app", 9, 9), _edge("cli", "app", 5), _edge("web", "lib", 3, 1),
    _edge("services/billing/internal/ledger<&>core", "lib", 2), _edge("tools", "cli", 1),
]
SMALL: JsonDict = {
    "available": True,
    "packages": [{"id": p, "loc": loc, "modules": m} for p, loc, m in _SMALL_PACKAGES],
    "package_count": len(_SMALL_PACKAGES),
    "package_edges": sorted(_SMALL_EDGES, key=lambda e: (-e["imports"], e["from"], e["to"])),
    "package_edge_count": len(_SMALL_EDGES),
    "cross_package_edges": sum(e["imports"] for e in _SMALL_EDGES),
    "burrow_edge_count": sum(e["bypass"] for e in _SMALL_EDGES),
}

_LARGE_IDS = [f"pkg/p{i:03d}" for i in range(200)]
_LARGE_EDGES = [
    *(_edge(_LARGE_IDS[i], _LARGE_IDS[(i + 1) % 20], 40 - i, 1 if i % 4 == 0 else 0) for i in range(20)),
    *(_edge(_LARGE_IDS[i], _LARGE_IDS[i + 3], 2) for i in range(10)),
    *(_edge(_LARGE_IDS[i], _LARGE_IDS[30 + i], 6, 2) for i in range(5)),
]
LARGE: JsonDict = {
    "available": True,
    "packages": [{"id": pid, "loc": 10000 - 40 * i, "modules": 5} for i, pid in enumerate(_LARGE_IDS)],
    "package_count": 230,
    "package_edges": sorted(_LARGE_EDGES, key=lambda e: (-e["imports"], e["from"], e["to"])),
    "package_edge_count": 512,
    "cross_package_edges": sum(e["imports"] for e in _LARGE_EDGES) + 177,
    "burrow_edge_count": sum(e["bypass"] for e in _LARGE_EDGES) + 23,
}


def goldens() -> dict[str, str]:
    """The golden renders: ``expected-<file>`` -> text."""
    [small] = package_diagrams({"structure": SMALL})
    assert small["svg"] is not None
    return {"expected-package-map.svg": small["svg"],
            "expected-package-map-large.svg": render_package_map(LARGE),
            "expected-package-section.md": diagrams_section([small]) + "\n"}


if __name__ == "__main__":
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for name, content in goldens().items():
        (FIXTURES / name).write_text(content, encoding="utf-8", newline="\n")
