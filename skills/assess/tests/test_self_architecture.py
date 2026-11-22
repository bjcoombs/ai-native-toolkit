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
import re
from pathlib import Path

# tests/ -> assess/ ; the deterministic core and its orchestrators live under scripts/.
SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
LIB_DIR = SCRIPTS_DIR / "lib"

# #917 moves lib modules into subpackages. Each old path keeps a ``sys.modules``
# alias shim for one release. ``fleet.py`` and ``counts_only.py`` share a name with
# their new package, so that package's ``__init__.py`` holds a lazy ``__getattr__``
# forward instead (decision 1 on #917). Every other package ``__init__.py`` is empty:
# an eager import there would turn the lazy cycles between lib modules into
# import-time ones.
FORWARDS = {"fleet": "lib.fleet.runner", "counts_only": "lib.counts_only.content_free"}
FORWARD_BODY = '''
import importlib
from typing import Any


def __getattr__(name: str) -> Any:
    return getattr(importlib.import_module("{target}"), name)
'''
# Packages that predate #917, whose ``__init__.py`` is a documented facade and whose
# README entry is the package heading (``**`test_pressure/`**``).
FACADE_PACKAGES = {"test_pressure"}
SHIM_DOC = re.compile(r"Moved to ``(lib(?:\.\w+)+)``")
REMOVAL_RELEASE = "4.0.0"


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


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _sets_sys_modules(node: ast.stmt) -> bool:
    """``sys.modules[__name__] = ...``: the alias that makes a path a shim."""
    return isinstance(node, ast.Assign) and any(
        isinstance(t, ast.Subscript) and ast.unparse(t) == "sys.modules[__name__]"
        for t in node.targets
    )


def _is_shim(tree: ast.Module) -> bool:
    return any(_sets_sys_modules(node) for node in ast.walk(tree))


def _module_file(dotted: str) -> Path:
    return LIB_DIR.parent.joinpath(*dotted.split(".")).with_suffix(".py")


def _is_main_guard(node: ast.stmt) -> bool:
    return isinstance(node, ast.If) and ast.unparse(node.test) == "__name__ == '__main__'"


def _import_binds(node: ast.stmt, target: str) -> set[str] | None:
    """The names a top-level shim import binds to ``target``, or None when the
    statement imports anything else. ``sys``, ``runpy`` and ``from __future__ import
    annotations`` (a compiler directive, as in a package ``__init__.py``) bind no alias."""
    if ast.unparse(node) == "from __future__ import annotations":
        return set()
    if isinstance(node, ast.Import):
        if all(a.name in {"sys", "runpy"} and a.asname is None for a in node.names):
            return set()
        if all(a.name == target for a in node.names):
            return {a.asname or a.name for a in node.names}
    elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
        if all(f"{node.module}.{a.name}" == target for a in node.names):
            return {a.asname or a.name for a in node.names}
    return None


def _runpy_call(target: str) -> str:
    """The only statement a shim's ``__main__`` branch may hold."""
    return ast.dump(ast.parse(f"runpy.run_module({target!r}, run_name='__main__')").body[0])


def _shim_problems(path: Path, tree: ast.Module) -> list[str]:
    """A shim holds the documented form and nothing else: a docstring naming the
    new dotted path and the removal release, imports of ``sys``, ``runpy``, the
    new module and ``__future__`` annotations only, ``sys.modules[__name__]`` set
    to that module at top level (the normal import path), and at most one
    ``__main__`` branch whose whole body is
    ``runpy.run_module(target, run_name="__main__")``. That branch is required
    when the new module has a ``__main__`` block."""
    rel = path.relative_to(LIB_DIR.parent).as_posix()
    doc = ast.get_docstring(tree) or ""
    found = SHIM_DOC.search(doc)
    if not found or REMOVAL_RELEASE not in doc:
        return [f"{rel}: shim docstring must name the new path and {REMOVAL_RELEASE}"]
    target = found.group(1)
    new = _module_file(target)
    if not new.is_file() or _is_shim(_parse(new)):
        return [f"{rel}: shim target {target} is not a real module"]
    problems = []
    body = tree.body[1:]
    imports = [n for n in body if isinstance(n, (ast.Import, ast.ImportFrom))]
    aliases: set[str] = set()
    for node in imports:
        bound = _import_binds(node, target)
        if bound is None:
            problems.append(f"{rel}: a shim imports only sys, runpy and {target}, not `{ast.unparse(node)}`")
        else:
            aliases |= bound
    top_alias = [n for n in body if _sets_sys_modules(n)]
    guards = [n for n in body if _is_main_guard(n)]
    if len(imports) + len(top_alias) + len(guards) != len(body) or len(guards) > 1:
        problems.append(f"{rel}: a shim holds no code beyond the alias and the __main__ branch")
    nested_alias = [n for n in ast.walk(tree) if isinstance(n, ast.stmt) and _sets_sys_modules(n)
                    and n not in top_alias]
    if not top_alias or nested_alias or any(ast.unparse(n.value) not in aliases for n in top_alias):
        problems.append(f"{rel}: sys.modules[__name__] is not set to the imported {target} at top level")
    if any(guard.orelse or [ast.dump(s) for s in guard.body] != [_runpy_call(target)] for guard in guards):
        problems.append(f"{rel}: the __main__ branch must be exactly runpy.run_module({target!r}, run_name=\"__main__\")")
    if any(_is_main_guard(n) for n in _parse(new).body) and not guards:
        problems.append(f"{rel}: {target} runs as a script, so its shim needs a runpy __main__ branch")
    return problems


def _package_init_problems(init: Path) -> list[str]:
    """A package ``__init__.py`` is empty apart from a docstring, or holds exactly
    the lazy forward for a package in ``FORWARDS``."""
    package = init.parent.relative_to(LIB_DIR).as_posix()
    if package.split("/")[0] in FACADE_PACKAGES:
        return []
    tree = _parse(init)
    doc = ast.get_docstring(tree)
    body = [n for n in tree.body[1 if doc is not None else 0:]
            if ast.unparse(n) != "from __future__ import annotations"]
    target = FORWARDS.get(package)
    rel = init.relative_to(LIB_DIR.parent).as_posix()
    if target is None:
        return [f"{rel}: a package __init__.py holds no code"] if body else []
    expected = ast.parse(FORWARD_BODY.format(target=target)).body
    problems = []
    if ast.dump(ast.Module(body=body, type_ignores=[])) != ast.dump(ast.Module(body=expected, type_ignores=[])):
        problems.append(f"{rel}: holds code beyond the lazy __getattr__ forward to {target}")
    if not doc or target not in doc or REMOVAL_RELEASE not in doc:
        problems.append(f"{rel}: docstring must name {target} and {REMOVAL_RELEASE}")
    return problems


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
        "(dependencies point inward - see docs/contributing/assess-architecture.md):\n  "
        + "\n  ".join(violations)
    )

    # The #917 subpackage rules: every package __init__.py stays empty (or holds
    # only its lazy forward), and every old path left behind is a shim of the
    # documented form, so no import edge hides in either.
    structure = [p for init in sorted(LIB_DIR.glob("*/**/__init__.py"))
                 for p in _package_init_problems(init)]
    structure += [p for module in _lib_modules()
                  if _is_shim(tree := _parse(module)) for p in _shim_problems(module, tree)]
    assert not structure, "lib subpackage rules (#917) broken:\n  " + "\n  ".join(structure)


def test_every_lib_module_has_a_readme_entry() -> None:
    """`lib/README.md` is the per-module reference `CLAUDE.md` points agents at.
    A module merged without an entry is invisible to an agent that trusts the
    map (issue #412: `decline_markers.py` and `interactivity.py` were missing).
    Every ``lib/`` module, at any depth, except ``__init__.py`` files and #917
    shims must have an entry heading in the README's form: a line of its own
    reading **`name.py`**, or **`pkg/name.py`** inside a subpackage, optionally
    followed by an italic note such as *(co-change hotspot)*. A facade package
    (``test_pressure/``) has one **`pkg/`** heading instead. A mention in prose,
    a bullet or a code example, or a name inside a longer filename (``stats.py``
    in ``complexity_stats.py``), does not count."""
    readme = (LIB_DIR / "README.md").read_text(encoding="utf-8")
    # A fenced code example is not the map, even when a line in it looks like
    # an entry heading.
    readme = re.sub(r"^```.*?^```", "", readme, flags=re.MULTILINE | re.DOTALL)
    entries = set(
        re.findall(r"^\*\*`([\w./]+(?:\.py|/))`\*\*(?:[ \t]+\*\([^)\n]*\)\*)?[ \t]*$",
                   readme, flags=re.MULTILINE)
    )
    modules = [p.relative_to(LIB_DIR) for p in _lib_modules()
               if p.name != "__init__.py" and not _is_shim(_parse(p))]
    assert any(len(m.parts) > 1 for m in modules), "the scan must reach lib subpackages"
    missing = [m.as_posix() for m in modules
               if m.parts[0] not in FACADE_PACKAGES and m.as_posix() not in entries]
    missing += [f"{pkg}/" for pkg in sorted(FACADE_PACKAGES) if f"{pkg}/" not in entries]
    assert not missing, f"lib/README.md has no entry for: {missing}"
