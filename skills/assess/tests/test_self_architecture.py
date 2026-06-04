"""Executable architecture contract for the /assess deterministic core.

`CLAUDE.md` states the layering: the deterministic core in
`skills/assess/scripts/lib/` does all the data work, and the orchestrator
scripts in `skills/assess/scripts/` (assess_core, assess_finalize, ...) call
into it and assemble `run-context.json`. Dependencies point **inward**: a lib
module may import other lib modules and third-party libraries, but it must never
import an orchestrator. That keeps the core independently testable and reusable -
the property the decomposition-parity test relies on.

This was a convention enforced only by review (the L4 "Partial" the tool's own
self-assessment flagged). This test makes it a contract: an `ast` scan over every
module in `lib/` asserts none imports an orchestrator. The forbidden set is
*derived* from disk - every `.py` directly under `scripts/` (not in `lib/`) - so
a newly added orchestrator is forbidden automatically, with no edit here.

Pure stdlib (`ast`, `pathlib`); no import side effects, so it is safe and fast.
"""
from __future__ import annotations

import ast
from pathlib import Path

# tests/ -> assess/ ; the deterministic core and its orchestrators live under scripts/.
SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
LIB_DIR = SCRIPTS_DIR / "lib"


def _orchestrator_modules() -> set[str]:
    """Importable module names of the orchestrator layer: every ``*.py`` directly
    under ``scripts/`` (excluding the ``lib/`` package). Hyphenated run-only
    scripts (``complexity-treemap.py``) are kept for completeness - their stems
    are not valid identifiers, so they can never appear as an import anyway."""
    return {p.stem for p in SCRIPTS_DIR.glob("*.py")}


def _lib_modules() -> list[Path]:
    """Every Python module in the deterministic core, recursively (includes the
    ``test_pressure/`` subpackage)."""
    return sorted(LIB_DIR.rglob("*.py"))


def _imported_names(tree: ast.AST) -> set[str]:
    """Collect the dotted module path of every ``import`` / ``from ... import``
    in a parsed module (absolute imports only; relative imports stay in-package
    by construction and cannot reach an orchestrator)."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            # level > 0 is a relative import (``from . import x``) - in-package,
            # never an orchestrator. Only absolute imports can cross the boundary.
            if node.level == 0 and node.module:
                names.add(node.module)
    return names


def test_fixture_paths_resolve() -> None:
    """Guard against a path bug making the boundary test vacuously pass."""
    orchestrators = _orchestrator_modules()
    libs = _lib_modules()
    assert LIB_DIR.is_dir(), f"lib package not found at {LIB_DIR}"
    assert "assess_core" in orchestrators, "expected assess_core.py among the orchestrators"
    assert libs, "expected at least one module in the deterministic core"


def test_core_does_not_import_orchestrators() -> None:
    """No `lib/` module may import an orchestrator script - dependencies point
    inward. A failure means the deterministic core grew an upward dependency."""
    orchestrators = _orchestrator_modules()
    violations: list[str] = []

    for module in _lib_modules():
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        for imported in _imported_names(tree):
            # Match an orchestrator stem appearing as any dotted component, so
            # both ``import assess_core`` and ``import scripts.assess_core`` are
            # caught.
            hit = next((o for o in orchestrators if o in imported.split(".")), None)
            if hit:
                rel = module.relative_to(LIB_DIR.parent)
                violations.append(f"{rel} imports orchestrator '{hit}' (via '{imported}')")

    assert not violations, (
        "deterministic core must not import the orchestrator layer "
        "(dependencies point inward - see CLAUDE.md /assess architecture):\n  "
        + "\n  ".join(violations)
    )
