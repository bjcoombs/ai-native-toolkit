"""No signal producer reads the wall clock (issue #773).

The level is a function of the assessed commit (#740), so every git-history
window and every "days since" figure is counted from HEAD's committer time, not
from today. Two kinds of read would break that, and each has a guard here:

- a Python clock read (``datetime.now()``, ``date.today()``, ``time.time()``),
  allowed only in the run-metadata and network functions listed in ``ALLOWED``,
  each with its reason;
- a relative git date (``--since="12 months ago"``), which git resolves against
  its own clock. The static arm rejects the literal; the runtime arm records
  every git call of a headless run and requires each date bound to be an
  absolute ``@<epoch>``, however the string was built.

Pure stdlib AST scan, like ``test_self_architecture.py``; the runtime arm runs
``assess_signals.main`` once on the dated fixture repository.
"""
from __future__ import annotations

import ast
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import assess_signals

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"

# (path under scripts/, enclosing function) -> why the clock read is allowed.
ALLOWED: dict[tuple[str, str], str] = {
    ("lib/signals.py", "build_inputs"):
        "run_date is run identity; its one signal consumer is the network merged sample",
    ("lib/signals.py", "build_stamp"): "scan_date is a stamp field, not a signal",
    ("assess_core.py", "main"): "run_date is run identity: wiki dates and supersede detection",
    ("lib/run_setup.py", "new_run_id"): "run id minting",
    ("complexity-treemap.py", "_new_run_id"): "run id minting",
    ("lib/report/wiki_writer.py", "_build_log_heading"): "wiki log heading time",
    ("lib/hosting/review_reality.py", "summarize"):
        "network scan: forge state is not a function of the commit (unavailable headless)",
    ("lib/hosting/review_reality.py", "scan_gitlab_review_reality"):
        "network scan: forge state is not a function of the commit (unavailable headless)",
    ("lib/hosting/review_reality.py", "scan_review_reality"):
        "network scan: forge state is not a function of the commit (unavailable headless)",
    ("lib/report/gate_cost.py", "estimate_gate_cost"):
        "network scan: a monthly Actions-cost estimate, not a level signal",
}

_CLOCK_SUFFIXES = (".now", ".today", ".utcnow")
_CLOCK_NAMES = {"time.time", "time.time_ns", "now", "today", "utcnow"}
# A git approxidate token that resolves against the reader's clock.
_RELATIVE_DATE = re.compile(r"(?:^|\s)(?:ago|yesterday)(?:\s|$)")
_DATE_BOUNDS = ("--since=", "--until=", "--after=", "--before=")


def _sources() -> list[Path]:
    return sorted(SCRIPTS_DIR.rglob("*.py"))


def _dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    if isinstance(node, ast.Call):
        return _dotted(node.func) + "()"
    return "?"


def _functions(tree: ast.AST) -> Iterator[tuple[str, ast.AST]]:
    """``(name, node)`` for the module (name ``<module>``) and every function."""
    yield "<module>", tree
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node.name, node


def _own_nodes(scope: ast.AST) -> Iterator[ast.AST]:
    """Nodes of ``scope`` not inside a nested function (those are their own scope);
    a lambda's body belongs to the function that holds it."""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            stack.extend(ast.iter_child_nodes(node))


def _aliases(tree: ast.AST) -> dict[str, str]:
    """Each name an import binds to the dotted name it stands for: ``import time as t``
    binds ``t`` to ``time``, ``from time import time_ns as tn`` binds ``tn`` to
    ``time.time_ns`` (#861). An import anywhere in the module counts, so an alias
    bound inside a function is still resolved."""
    bound: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    bound[alias.asname] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                bound[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return bound


def _resolved(dotted: str, bound: dict[str, str]) -> str:
    head, dot, rest = dotted.partition(".")
    return bound.get(head, head) + dot + rest


def _clock_calls(tree: ast.AST) -> dict[str, list[int]]:
    """``function -> [line, ...]`` of every wall-clock call in one module."""
    bound = _aliases(tree)
    found: dict[str, list[int]] = {}
    for name, scope in _functions(tree):
        for node in _own_nodes(scope):
            if not isinstance(node, ast.Call):
                continue
            dotted = _dotted(node.func)
            full = _resolved(dotted, bound)
            if any(d.endswith(_CLOCK_SUFFIXES) or d in _CLOCK_NAMES for d in (dotted, full)):
                found.setdefault(name, []).append(node.lineno)
    return found


def _clock_reads() -> dict[tuple[str, str], list[int]]:
    """``(path, function) -> [line, ...]`` of every wall-clock call under scripts/."""
    found: dict[tuple[str, str], list[int]] = {}
    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(SCRIPTS_DIR).as_posix()
        for name, lines in _clock_calls(tree).items():
            found[(rel, name)] = lines
    return found


@pytest.mark.parametrize("source", [
    "import time\ndef f():\n    return time.time()",
    "from time import time\ndef f():\n    return time()",
    "from time import time_ns\ndef f():\n    return time_ns()",
    "from time import time as clock\ndef f():\n    return clock()",
    "import time as t\ndef f():\n    return t.time_ns()",
    "import datetime as dt\ndef f():\n    return dt.datetime.now()",
    "from datetime import date as D\ndef f():\n    return D.today()",
])
def test_the_clock_guard_sees_an_aliased_or_bare_read(source: str) -> None:
    assert _clock_calls(ast.parse(source)) == {"f": [3]}


def test_the_clock_guard_ignores_a_local_function_named_like_a_clock() -> None:
    assert _clock_calls(ast.parse("def time():\n    return 0\ndef f():\n    return time()")) == {}


def test_no_signal_producer_reads_the_wall_clock() -> None:
    offenders = [f"{path}:{line} {func}"
                 for (path, func), lines in sorted(_clock_reads().items())
                 if (path, func) not in ALLOWED for line in lines]
    assert not offenders, (
        "wall-clock reads outside the run-metadata allowlist; count from the "
        "assessed commit (lib/history/history_window.anchored_since) instead:\n" + "\n".join(offenders))


def test_allowlist_entries_still_exist() -> None:
    reads = _clock_reads()
    stale = [key for key in ALLOWED if key not in reads]
    assert not stale, f"ALLOWED names functions that no longer read the clock: {stale}"


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def test_no_relative_git_date_strings() -> None:
    offenders = []
    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        docstrings = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and id(node) not in docstrings and _RELATIVE_DATE.search(node.value)):
                offenders.append(f"{path.relative_to(SCRIPTS_DIR).as_posix()}:{node.lineno}"
                                 f" {node.value!r}")
    assert not offenders, "relative git dates resolve against the clock:\n" + "\n".join(offenders)


def test_every_git_since_is_absolute(dated_git_repo: Path, tmp_path: Path,
                                     monkeypatch: pytest.MonkeyPatch) -> None:
    bounds: list[str] = []
    real_run = subprocess.run

    def recording_run(cmd: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(cmd, (list, tuple)) and cmd and Path(str(cmd[0])).name == "git":
            bounds.extend(str(a) for a in cmd if str(a).startswith(_DATE_BOUNDS))
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", recording_run)
    assert assess_signals.main([str(dated_git_repo), "--out", str(tmp_path / "s.json")]) == 0
    assert bounds, "the run passed no date bound to git, so the check proved nothing"
    relative = [b for b in bounds if not b.partition("=")[2].startswith("@")]
    assert not relative, f"git date bounds not anchored to an epoch: {relative}"
