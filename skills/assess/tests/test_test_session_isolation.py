"""Tests that one test cannot change another through process-wide state (#831, #1056).

- #1056: the bare module name ``conftest`` means whichever conftest was
  imported first when the skills/assess and root ``tests/`` trees run in one
  process, so no test imports from it, and the shared helper modules have
  names no other test tree uses.
- #831: a stand-in for numpy, lizard or matplotlib left in ``sys.modules``
  changes every later test in the worker. ``stub_heavy_deps`` scopes them to a
  ``MonkeyPatch``, and the conftest guard built on ``leaked_stand_ins`` fails
  a module that leaves one behind. That guard snapshots ``sys.modules`` when a
  module's tests start, after collection has imported every test module, so a
  write at import time is out of its reach: a lint bans those instead.
"""
from __future__ import annotations

import ast
import sys
import types
from pathlib import Path

import pytest
from assess_test_support import (
    HEAVY_DEPS,
    StubNumpy,
    leaked_stand_ins,
    stand_in_snapshot,
    stub_heavy_deps,
)

REPO = Path(__file__).resolve().parents[3]
TEST_TREES = (REPO / "skills" / "assess" / "tests", REPO / "scripts" / "tests", REPO / "tests")


def _test_sources() -> list[Path]:
    """Every Python file in the test trees, nested packages included, minus data."""
    return sorted(
        path for root in TEST_TREES for path in root.rglob("*.py")
        if not {"fixtures", "__pycache__"} & set(path.relative_to(root).parts)
    )


def _conftest_imports(tree: ast.Module) -> list[int]:
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "conftest" and node.level == 0:
            lines.append(node.lineno)
        elif isinstance(node, ast.Import) and any(a.name == "conftest" for a in node.names):
            lines.append(node.lineno)
    return lines


def test_the_conftest_import_check_sees_both_forms() -> None:
    source = "import conftest\nfrom conftest import x\nfrom .conftest import y\nfrom assess_test_support import z\n"
    assert _conftest_imports(ast.parse(source)) == [1, 2]


def test_no_test_imports_from_the_bare_conftest_name() -> None:
    """#1056: import shared helpers from ``assess_test_support`` instead."""
    found = [
        f"{path.relative_to(REPO).as_posix()}:{line}"
        for path in _test_sources()
        for line in _conftest_imports(ast.parse(path.read_text(encoding="utf-8")))
    ]
    assert found == []


def _is_sys_modules(node: ast.expr) -> bool:
    return ast.unparse(node) == "sys.modules"


def _import_time_sys_modules_writes(tree: ast.Module) -> list[int]:
    """Lines that write ``sys.modules`` while the module is imported.

    Function bodies run later, under a fixture or a test, so they are skipped;
    class bodies run at import, so they are not.
    """
    lines: list[int] = []
    pending: list[ast.AST] = list(tree.body)
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        targets = (node.targets if isinstance(node, ast.Assign)
                   else [node.target] if isinstance(node, (ast.AugAssign, ast.AnnAssign)) else [])
        if any(isinstance(t, ast.Subscript) and _is_sys_modules(t.value) for t in targets):
            lines.append(node.lineno)  # type: ignore[attr-defined]
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("setdefault", "update") and _is_sys_modules(node.func.value)):
            lines.append(node.lineno)
        pending.extend(ast.iter_child_nodes(node))
    return sorted(lines)


def test_the_import_time_check_sees_each_write_form() -> None:
    source = (
        "import sys\n"
        "sys.modules['a'] = object()\n"
        "sys.modules.setdefault('b', object())\n"
        "sys.modules.update({'c': object()})\n"
        "if True:\n"
        "    sys.modules['d'] = object()\n"
        "class K:\n"
        "    sys.modules['e'] = object()\n"
        "def f(mp):\n"
        "    sys.modules['f'] = object()\n"
        "    mp.setitem(sys.modules, 'g', object())\n"
    )
    assert _import_time_sys_modules_writes(ast.parse(source)) == [2, 3, 4, 6, 8]


def test_no_test_module_writes_sys_modules_at_import_time() -> None:
    """#831: a stand-in installed while a module is collected is already in the
    guard's "before" snapshot, so the guard cannot see it leak. Install stand-ins
    inside a fixture with ``stub_heavy_deps(monkeypatch)`` instead."""
    found = [
        f"{path.relative_to(REPO).as_posix()}:{line}"
        for path in _test_sources()
        for line in _import_time_sys_modules_writes(ast.parse(path.read_text(encoding="utf-8")))
    ]
    assert found == []


def test_test_tree_module_names_do_not_collide() -> None:
    """Each tree puts its own directory on ``sys.path``, so a module name two trees
    share resolves to whichever was imported first (#1056)."""
    seen: dict[str, str] = {}
    clashes = []
    for root in TEST_TREES:
        for path in sorted(root.glob("*.py")):
            if path.name in ("__init__.py", "conftest.py"):
                continue
            rel = path.relative_to(REPO).as_posix()
            if path.name in seen:
                clashes.append(f"{seen[path.name]} and {rel}")
            seen.setdefault(path.name, rel)
    assert clashes == []


def test_a_new_stand_in_is_a_leak() -> None:
    real = types.ModuleType("numpy")
    real.__spec__ = object()  # type: ignore[assignment]
    assert leaked_stand_ins({"numpy": None}, {"numpy": StubNumpy("numpy")}) == ["numpy"]
    assert leaked_stand_ins({"numpy": real}, {"numpy": StubNumpy("numpy")}) == ["numpy"]


def test_a_real_import_a_removal_or_no_change_is_not_a_leak() -> None:
    real = types.ModuleType("lizard")
    real.__spec__ = object()  # type: ignore[assignment]
    stub = types.ModuleType("lizard")
    assert leaked_stand_ins({"lizard": None}, {"lizard": real}) == []
    assert leaked_stand_ins({"lizard": stub}, {"lizard": None}) == []
    assert leaked_stand_ins({"lizard": stub}, {"lizard": stub}) == []


def test_stub_heavy_deps_restores_sys_modules_on_exit() -> None:
    before = stand_in_snapshot()
    with pytest.MonkeyPatch.context() as mp:
        stub_heavy_deps(mp)
        assert all(sys.modules.get(name) is not None for name in HEAVY_DEPS)
        assert hasattr(sys.modules["numpy"], "percentile")
        assert sys.modules["matplotlib"].pyplot is sys.modules["matplotlib.pyplot"]
    assert stand_in_snapshot() == before


def test_stub_numpy_percentile_is_linear() -> None:
    assert StubNumpy.percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.5
    assert StubNumpy.percentile([], 95) == 0.0
