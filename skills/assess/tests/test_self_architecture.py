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

Pure stdlib (`ast`, `pathlib`, `subprocess`); no import side effects. The #917 shim
check also loads each script shim by its old file path in a child interpreter.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
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
# Packages that predate #917, whose ``__init__.py`` is a documented facade that
# re-exports the package's names (and holds its module docstring, #981).
FACADE_PACKAGES = {"test_pressure"}
SHIM_DOC = re.compile(r"Moved to ``(lib(?:\.\w+)+)``")
REMOVAL_RELEASE = "5.0.0"
# A shim whose target runs as a script puts ``scripts/`` on ``sys.path`` before it
# imports ``lib``, so ``python scripts/lib/<stem>.py`` (where Python puts ``lib/``
# first instead) still runs until the shim goes in 5.0.0. Only that line is allowed.
SHIM_BOOTSTRAP = "sys.path.insert(0, str(Path(__file__).resolve().parents[1]))"


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
    if ast.unparse(node) in {"from __future__ import annotations", "from pathlib import Path"}:
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
    when the new module has a ``__main__`` block, and so is ``SHIM_BOOTSTRAP``
    before the new module's import (with ``pathlib.Path`` imported), which no
    other shim may hold."""
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
    bootstraps = [n for n in body if ast.unparse(n) == SHIM_BOOTSTRAP]
    if (len(imports) + len(top_alias) + len(guards) + len(bootstraps) != len(body)
            or len(guards) > 1 or len(bootstraps) > 1):
        problems.append(f"{rel}: a shim holds no code beyond the alias and the __main__ branch")
    # The alias reads sys and the branch calls runpy: each name is imported, and
    # every import comes first, or the shim raises NameError instead of aliasing.
    # The bootstrap, when present, sits among the imports, just before the new module's.
    imported = {a.name for n in imports if isinstance(n, ast.Import) for a in n.names}
    imported |= {a.name for n in imports if isinstance(n, ast.ImportFrom) for a in n.names}
    needed = {"sys"} | ({"runpy"} if guards else set()) | ({"Path"} if bootstraps else set())
    head = body[:len(imports) + len(bootstraps)]
    if needed - imported or [n for n in head if n not in bootstraps] != imports:
        problems.append(f"{rel}: a shim imports {', '.join(sorted(needed))} before it uses them")
    for boot in bootstraps:
        after = body[body.index(boot) + 1:]
        if not any(_import_binds(n, target) for n in after if isinstance(n, (ast.Import, ast.ImportFrom))):
            problems.append(f"{rel}: the sys.path bootstrap must come before the import of {target}")
    nested_alias = [n for n in ast.walk(tree) if isinstance(n, ast.stmt) and _sets_sys_modules(n)
                    and n not in top_alias]
    if not top_alias or nested_alias or any(ast.unparse(n.value) not in aliases for n in top_alias):
        problems.append(f"{rel}: sys.modules[__name__] is not set to the imported {target} at top level")
    if any(guard.orelse or [ast.dump(s) for s in guard.body] != [_runpy_call(target)] for guard in guards):
        problems.append(f"{rel}: the __main__ branch must be exactly runpy.run_module({target!r}, run_name=\"__main__\")")
    runs_as_script = any(_is_main_guard(n) for n in _parse(new).body)
    if runs_as_script and not guards:
        problems.append(f"{rel}: {target} runs as a script, so its shim needs a runpy __main__ branch")
    if runs_as_script != bool(bootstraps):
        problems.append(f"{rel}: a shim holds `{SHIM_BOOTSTRAP}` exactly when {target} runs as a script")
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

    # A script shim still loads by its old file path until 5.0.0: run as
    # ``python scripts/lib/<stem>.py``, Python puts lib/ first on sys.path, not
    # scripts/, and the bootstrap adds scripts/ back. Each shim is loaded by path
    # from lib/ (not as __main__, because the fleet schema scripts rewrite the
    # published schemas), and the two keyhole scripts also answer --help.
    script_shims = sorted(m for m in LIB_DIR.glob("*.py") if _is_shim(tree := _parse(m))
                          and any(ast.unparse(n) == SHIM_BOOTSTRAP for n in tree.body))
    assert len(script_shims) >= 8, script_shims
    for shim in script_shims:
        load = subprocess.run([sys.executable, "-c", f"import runpy; runpy.run_path({shim.name!r})"],
                              cwd=LIB_DIR, capture_output=True, encoding="utf-8", errors="replace", check=False)
        assert load.returncode == 0, f"{shim.name} does not load by path: {load.stderr[-400:]}"
    # Each package forward (decision 1) is lazy, and every public name of the moved
    # module reads through it as the same object, so ``import lib.<pkg>`` keeps the
    # old module's attributes until 5.0.0. A fresh interpreter, so no earlier import
    # hides an eager one.
    for package, target in FORWARDS.items():
        code = (f"import importlib, sys; import lib.{package} as p; "
                f"assert {target!r} not in sys.modules, 'imported eagerly'; "
                f"t = importlib.import_module({target!r}); "
                "bad = [n for n in dir(t) if not n.startswith('_') and getattr(p, n) is not getattr(t, n)]; "
                "assert not bad, bad")
        forward = subprocess.run([sys.executable, "-c", code], cwd=SCRIPTS_DIR, capture_output=True,
                                 encoding="utf-8", errors="replace", check=False)
        assert forward.returncode == 0, f"lib.{package} forward: {forward.stderr[-400:]}"
    for stem in ("promissory_markers", "accretion_ratchet"):
        run = subprocess.run([sys.executable, str(LIB_DIR / f"{stem}.py"), "--help"],
                             capture_output=True, encoding="utf-8", errors="replace", check=False)
        assert run.returncode == 0 and "usage" in run.stdout.lower(), f"{stem}.py --help: {run.stderr[-400:]}"


# A summary line is one sentence: it ends a sentence (optionally followed by a
# parenthesised issue reference) and no sentence ends before that.
_ONE_SENTENCE = re.compile(r"^[^\s].*[.?!](?: \([^()]*\))?$")
_SENTENCE_BREAK = re.compile(r"[.?!] +[A-Z]")
# A module too long to hold its whole reference keeps a summary and points at
# its own file under lib/docs/ (one file per module, so no shared hotspot).
_POINTER = re.compile(r"Full reference: lib/docs/([\w.-]+\.md)")


def _docstring_problem(path: Path, docs_dir: Path = LIB_DIR / "docs") -> str | None:
    """Why ``path`` fails the docstring rule, or None when it passes. An
    ``__init__.py`` holding nothing but a docstring marks a package and is exempt.
    A ``Full reference: lib/docs/<name>.md`` pointer must name a file in ``docs_dir``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    doc = ast.get_docstring(tree)
    if path.name == "__init__.py" and all(
        (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))
        or (isinstance(n, ast.ImportFrom) and n.module == "__future__")
        for n in tree.body
    ):
        return None
    if not doc or not doc.strip():
        return "no module docstring"
    first = doc.splitlines()[0]
    if not _ONE_SENTENCE.match(first) or _SENTENCE_BREAK.search(first[:-1]):
        return f"first line is not a single sentence: {first!r}"
    missing = [name for name in _POINTER.findall(doc) if not (docs_dir / name).is_file()]
    if missing:
        return f"points at a missing reference: lib/docs/{missing[0]}"
    return None


def test_every_lib_module_has_a_docstring() -> None:
    """Each ``lib/`` module documents itself in its module docstring (issue #981,
    replacing the per-module index once kept in ``lib/README.md``, which every
    feature PR edited and so serialised merges). Every module at any depth needs a
    non-empty docstring whose first line is a one-sentence summary, followed by
    the details. A #917 shim is exempt: ``_shim_problems`` owns its docstring. An agent that trusts the map finds a module's contract beside
    its code (issue #412 was two modules merged with no entry)."""
    modules = [p for p in _lib_modules() if not _is_shim(_parse(p))]
    assert any(len(p.relative_to(LIB_DIR).parts) > 1 for p in modules), "the scan must reach lib subpackages"
    problems = [f"{p.relative_to(LIB_DIR).as_posix()}: {why}" for p in modules
                if (why := _docstring_problem(p)) is not None]
    assert not problems, "lib modules need a one-sentence docstring summary:\n  " + "\n  ".join(problems)


def test_every_lib_docs_file_is_one_modules_reference() -> None:
    """Each ``lib/docs/<name>.md`` is the full reference of exactly one module,
    whose docstring points at it, so no file there is orphaned or shared."""
    pointed: dict[str, list[str]] = {}
    for module in _lib_modules():
        for name in _POINTER.findall(ast.get_docstring(_parse(module)) or ""):
            pointed.setdefault(name, []).append(module.relative_to(LIB_DIR).as_posix())
    docs = _module_references()
    assert docs, "expected the long module references under lib/docs/"
    assert [d for d in docs if len(pointed.get(d, [])) != 1] == [], pointed


def _module_references() -> list[str]:
    """The long module references under ``lib/docs/``: every ``.md`` there but the
    folder's own ``README.md``, which describes the folder rather than one module."""
    return sorted(p.name for p in (LIB_DIR / "docs").glob("*.md") if p.name != "README.md")


def test_every_lib_docs_reference_is_linked_from_the_lib_readme() -> None:
    """``lib/README.md`` links every module reference under ``lib/docs/`` (issue
    #1212). A docstring pointer is plain text, so without the link a reference is an
    orphan in the doc graph and a reader starting at ``docs/index.md`` never reaches it."""
    readme = (LIB_DIR / "README.md").read_text(encoding="utf-8")
    linked = set(re.findall(r"\]\(docs/([\w.-]+\.md)\)", readme))
    assert [d for d in _module_references() if d not in linked] == []


def test_lib_readme_subpackage_file_lists_match_disk() -> None:
    """Each subpackage bullet in ``lib/README.md`` lists exactly the ``.py`` files in
    that package (issue #1220): 21 modules had gone missing from the lists unnoticed."""
    readme = (LIB_DIR / "README.md").read_text(encoding="utf-8")
    bullets = re.findall(r"^- \*\*`(\w+)/`\*\*(.*?)Files: ((?:`[\w.]+`[,.]\s*)+)", readme, re.M | re.S)
    assert bullets, "expected the subpackage bullets in lib/README.md"
    drift = {}
    for pkg, _, files in bullets:
        listed = sorted(re.findall(r"`([\w.]+)`", files))
        on_disk = sorted(p.name for p in (LIB_DIR / pkg).glob("*.py"))
        if listed != on_disk:
            drift[pkg] = sorted(set(listed) ^ set(on_disk))
    packages = sorted(p.parent.name for p in LIB_DIR.glob("*/__init__.py"))
    assert sorted(pkg for pkg, _, _ in bullets) == packages
    assert drift == {}


def test_the_docstring_check_fails_on_a_missing_or_run_on_summary(tmp_path: Path) -> None:
    """The rule bites: no docstring, an empty one, a wrapped summary and two
    sentences on the first line each fail; a package marker passes."""
    cases = {
        "none.py": "x = 1\n",
        "empty.py": '""" """\nx = 1\n',
        "wrapped.py": '"""Parse a report into the shape\nthe scan reads."""\n',
        "two.py": '"""Parse a report. Then grade it."""\n',
    }
    for name, body in cases.items():
        (tmp_path / name).write_text(body, encoding="utf-8")
        assert _docstring_problem(tmp_path / name) is not None, name
    good = {
        "ok.py": '"""Parse a report into the shape the scan reads.\n\nDetails."""\n',
        "question.py": '"""Does CI run the coverage gate? (issue #921)"""\n',
    }
    for name, body in good.items():
        (tmp_path / name).write_text(body, encoding="utf-8")
        assert _docstring_problem(tmp_path / name) is None, name
    pointer = tmp_path / "pointer.py"
    pointer.write_text('"""Parse a report.\n\nFull reference: lib/docs/pointer.md."""\n', encoding="utf-8")
    assert _docstring_problem(pointer, tmp_path) == "points at a missing reference: lib/docs/pointer.md"
    (tmp_path / "pointer.md").write_text("# pointer\n", encoding="utf-8")
    assert _docstring_problem(pointer, tmp_path) is None
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    assert _docstring_problem(pkg / "__init__.py") is None
    (pkg / "__init__.py").write_text("X = 1\n", encoding="utf-8")
    assert _docstring_problem(pkg / "__init__.py") == "no module docstring"
