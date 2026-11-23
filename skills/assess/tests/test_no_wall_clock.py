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
    ("lib/wiki_writer.py", "_build_log_heading"): "wiki log heading time",
    ("lib/hosting/review_reality.py", "summarize"):
        "network scan: forge state is not a function of the commit (unavailable headless)",
    ("lib/hosting/review_reality.py", "scan_gitlab_review_reality"):
        "network scan: forge state is not a function of the commit (unavailable headless)",
    ("lib/hosting/review_reality.py", "scan_review_reality"):
        "network scan: forge state is not a function of the commit (unavailable headless)",
    ("lib/gate_cost.py", "estimate_gate_cost"):
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


def _clock_reads() -> dict[tuple[str, str], list[int]]:
    """``(path, function) -> [line, ...]`` of every wall-clock call under scripts/."""
    found: dict[tuple[str, str], list[int]] = {}
    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(SCRIPTS_DIR).as_posix()
        for name, scope in _functions(tree):
            for node in _own_nodes(scope):
                if not isinstance(node, ast.Call):
                    continue
                dotted = _dotted(node.func)
                if dotted.endswith(_CLOCK_SUFFIXES) or dotted in _CLOCK_NAMES:
                    found.setdefault((rel, name), []).append(node.lineno)
    return found


def test_no_signal_producer_reads_the_wall_clock() -> None:
    offenders = [f"{path}:{line} {func}"
                 for (path, func), lines in sorted(_clock_reads().items())
                 if (path, func) not in ALLOWED for line in lines]
    assert not offenders, (
        "wall-clock reads outside the run-metadata allowlist; count from the "
        "assessed commit (lib/history_window.anchored_since) instead:\n" + "\n".join(offenders))


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
