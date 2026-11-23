"""Attribute fleet comparison moves to a scanner change or to a signal one run lacks (issue #774).

``lib/fleet_compare.py`` compares the stored scans of two runs. When the toolkit
that wrote them changed between the runs, a move can come from the scanner code
and not from the repository. This module gives such a move its own ``cause``, so
it is never presented as a repository slip (or gain).

**Convention first.** A scanner change that alters what a signal means ships
under a new signal name or a ``signals_schema_version`` bump; an earlier run then
reads the new signal as absent (unknown), not as a false repository move. The
backstop below catches a change that kept the name.

**Scanner diff** (``scanner_diff``), from the two run stamps:

- the same ``toolkit_commit`` on both sides (or none on both at one
  ``plugin_version``): ``same``, and git is never run;
- both commits null (installs that are not git checkouts) and the
  ``plugin_version`` differs: ``unavailable``;
- one commit null and the other set: ``unavailable`` at any ``plugin_version``,
  since a git checkout and an install can hold different scanner code under one
  version;
- a commit that is not 7 to 40 hex characters: ``unavailable``, and git never
  sees it;
- two different commits: ``git diff --name-only A B -- skills/assess/scripts/lib``
  in the toolkit checkout running the comparison. When git cannot answer (no
  checkout, a commit missing from it, a shallow clone) the diff is ``unavailable``.

**Producers** (``producers``): each block the headless runner publishes
(``lib.signals.SIGNAL_SCANS``, spread keys included) maps to the module that
defines its callable (``fn`` and ``offline_fn``, partials unwrapped) and that
module's static ``lib`` import closure (a package such as ``lib/test_pressure/``
included, with its relative imports). A module defining more than one spec's
callable (``core_scans``, ``run_setup``, the headless tables) is a dispatch
module: its spec maps to the dispatch module plus the closure of the ``lib``
modules the function body names (following the module's own functions and
constants). A block also takes the producers of every spec it ``reads``, so a
headless entry over the core's private ``structure_scan`` maps to the import
graph modules. ``run_context_types`` (types only) is left out. The map is built from the code running the comparison; a change outside
``lib/`` (``assess_signals.py``) is not seen.

**Row attribution** (``attribute``), for a team row present in both runs that
moved with cause ``level`` or ``set_and_level``:

1. ``set`` with ``absent_signals`` (measured-set movement, never level movement):
   the unknown side's ``missing_signals`` name a path absent from that side's
   stored scans of a repository in the group while the other side holds it (a
   scanner added, or dropped, the signal; a block reporting ``available: false``
   is a degraded scan and does not count). It wins over the causes below.
2. ``toolkit_changed_unknown_scope`` when the scanner diff is unavailable;
   ``notes`` gets one line saying so.
3. ``scanner_changed`` with ``scanner`` (each block and its changed producer
   modules) when a changed producer's block is a block the row reads (the
   ``signal`` of its ``fleet-data.json`` cells in either run; without that file,
   any block) and a repository in the group changed a signal in it.

A ``set_and_level`` row given one of these causes also carries ``set_moved: true``,
so the measured-set movement stays reported. Org rows read the group's settings,
not scans, and keep their cause.

Delta attribution for scanner changes (issue #774), read by `fleet_compare.compare`.

Signal meaning convention: a scanner change that alters a signal's meaning ships under a new
signal name or a `signals_schema_version` bump (`lib/signals.py` `SIGNALS_SCHEMA_VERSION`). An
earlier run then reads the new signal as absent (unknown), never as a false repository move.
A pure rename is aliased instead (`signal_aliases.SIGNAL_ALIASES`).

The backstop catches a change that kept the name. `scanner_diff` compares the stamps'
`toolkit_commit`. Equal commits are `same`, and git never runs (two null commits only at one
`plugin_version`). One null commit, or two null commits at different `plugin_version`s, is
`unavailable`. Two different commits run `git diff --name-only`
over `skills/assess/scripts/lib` in the running toolkit checkout; when git cannot answer, the
result is `unavailable`. `producers` maps each block in `lib.signals.SIGNAL_SCANS` to its
producer modules:

- the defining module of `fn` / `offline_fn` and its static `lib` import closure (a package
  such as `lib/test_pressure/` included, with its relative imports);
- for a dispatch module (one defining several specs), the modules its function body names;
- the producers of every spec it `reads`;
- `run_context_types` is left out.

`changed_blocks` intersects the map with the diff. `attribute` orders a moved team row's cause:

1. `set` with `absent_signals`: the unknown side's `missing_signals` name a path that side's
   stored scans lack while the other run holds it. This is measured-set movement, never level
   movement; the achievements replay already treats an unknown side as neutral. A path under
   any block or sub-block reporting `available: false` (`agent_environment.ci_duration`, walked
   as `levels.resolve_signal` walks it, #959) is a degraded scan, never an absent signal.
2. `toolkit_changed_unknown_scope` when the diff is unavailable. `unavailable_note` adds one
   `notes` line saying the scanner diff was unavailable.
3. `scanner_changed` with `scanner` (each block and its changed modules) when a block the row
   reads (`row_signals`, else any) changed producers and a repository in the group changed a
   signal in it.

A `set_and_level` row given one of these causes also carries `set_moved: true`, so the
measured-set movement stays reported. Org rows keep their cause. Limits: the map comes from the running code, a change outside
`lib/` is not seen, and the import closure over-attributes rather than misses.
Tests: `tests/test_fleet_scanner_diff.py`.
"""
from __future__ import annotations

import ast
import re
import functools
import inspect
import subprocess
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, NotRequired, TypedDict

from lib.fleet_stamp import FleetStamp

CAUSE_SCANNER_CHANGED = "scanner_changed"
CAUSE_UNKNOWN_SCOPE = "toolkit_changed_unknown_scope"
LIB_PATH = "skills/assess/scripts/lib"
_COMMIT = re.compile(r"[0-9a-f]{7,40}")  # a stamped toolkit_commit; anything else never reaches git
TYPES_ONLY = frozenset({"run_context_types"})

LIB_DIR = Path(__file__).resolve().parent
TOOLKIT_ROOT = LIB_DIR.parents[3]

DiffStatus = Literal["same", "changed", "unavailable"]


class ScannerDiff(TypedDict):
    status: DiffStatus
    changed: list[str]  # changed files under lib/, toolkit-relative posix paths; empty unless "changed"
    reason: str | None  # why the diff is unavailable; None otherwise


class ScannerCause(TypedDict):
    block: str  # the signal prefix, e.g. "ci_checks"
    modules: list[str]  # its changed producer modules, toolkit-relative posix paths


class Attribution(TypedDict):
    cause: str
    scanner: NotRequired[list[ScannerCause]]
    absent_signals: NotRequired[list[str]]
    set_moved: NotRequired[bool]  # the incoming cause was set_and_level: the measured set moved too


GitDiff = Callable[[Path, str, str], list[str] | None]


# --- scanner diff ------------------------------------------------------------


def git_changed_files(root: Path, earlier: str, later: str) -> list[str] | None:
    """Files under ``lib/`` that differ between two commits of ``root``; None when git cannot say."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "diff", "--name-only", "--end-of-options", earlier, later,
             "--", LIB_PATH],
            capture_output=True, text=True, encoding="utf-8", errors="surrogateescape",
            check=True, timeout=60,
        )
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return None
    return sorted(line.strip() for line in out.stdout.splitlines() if line.strip())


def scanner_diff(earlier: FleetStamp, later: FleetStamp, root: Path = TOOLKIT_ROOT,
                 diff: GitDiff = git_changed_files) -> ScannerDiff:
    """How the scanner code moved between the toolkits that wrote two runs (see the module doc)."""
    then, now = earlier.get("toolkit_commit"), later.get("toolkit_commit")
    if then == now and (then is not None or earlier.get("plugin_version") == later.get("plugin_version")):
        return {"status": "same", "changed": [], "reason": None}
    if then is None and now is None:  # equal versions returned "same" above
        return {"status": "unavailable", "changed": [],
                "reason": "neither run recorded a toolkit_commit and plugin_version changed "
                          f"{earlier.get('plugin_version')!r}->{later.get('plugin_version')!r}"}
    if then is None or now is None:
        side = "earlier" if then is None else "later"
        return {"status": "unavailable", "changed": [],
                "reason": f"the {side} run recorded no toolkit_commit (plugin_version "
                          f"{earlier.get('plugin_version')!r}->{later.get('plugin_version')!r})"}
    if not (_COMMIT.fullmatch(then) and _COMMIT.fullmatch(now)):
        return {"status": "unavailable", "changed": [],
                "reason": "a run's toolkit_commit is not a commit hash (7 to 40 hex characters)"}
    changed = diff(root, then, now)
    if changed is None:
        return {"status": "unavailable", "changed": [],
                "reason": f"git could not diff toolkit commits {then[:12]}..{now[:12]} in this checkout"}
    return {"status": "changed" if changed else "same", "changed": changed, "reason": None}


UNAVAILABLE_PREFIX = "scanner diff unavailable"


def unavailable_note(diff: ScannerDiff) -> str:
    return (f"{UNAVAILABLE_PREFIX}: {diff['reason']}; every moved team row is tagged "
            f"{CAUSE_UNKNOWN_SCOPE}, not read as a repository move")


# --- producers ---------------------------------------------------------------


def _file(module: str) -> Path | None:
    """The source file of a dotted module name under ``lib`` (a package is its ``__init__.py``)."""
    base = LIB_DIR.joinpath(*module.split("."))
    for path in (base.with_suffix(".py"), base / "__init__.py"):
        if path.is_file():
            return path
    return None


def _target(module: str, name: str) -> str:
    """``from <module> import <name>``: the submodule when ``name`` is one, else ``module``."""
    return f"{module}.{name}" if _file(f"{module}.{name}") is not None else module


def _imports(tree: ast.Module, current: str) -> dict[str, str]:
    """Each name bound by a ``lib`` import (absolute, or relative inside a package) in the
    module ``current`` to the dotted lib module it comes from."""
    path = _file(current)
    package = current if path is not None and path.name == "__init__.py" else current.rpartition(".")[0]
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".") if package else []
                parts = parts[:len(parts) - (node.level - 1)] if node.level > 1 else parts
                base = ".".join([*parts, *([node.module] if node.module else [])])
            elif node.module and node.module.split(".")[0] == "lib":
                base = node.module.partition(".")[2]
            else:
                continue
            for alias in node.names:
                target = _target(base, alias.name) if base else alias.name
                names[alias.asname or alias.name] = target
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == "lib" and len(parts) > 1:
                    names[alias.asname or alias.name] = ".".join(parts[1:])
    return {name: module for name, module in names.items() if module and _file(module) is not None}


@functools.cache
def _parsed(module: str) -> ast.Module | None:
    path = _file(module)
    if path is None:
        return None
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None


def _path(module: str) -> str:
    """A dotted lib module as a toolkit-relative posix path."""
    path = _file(module)
    assert path is not None
    return f"{LIB_PATH}/{path.relative_to(LIB_DIR).as_posix()}"


def _closure(start: Iterable[str]) -> set[str]:
    seen: set[str] = set()
    todo = list(start)
    while todo:
        module = todo.pop()
        if module in seen or module in TYPES_ONLY:
            continue
        tree = _parsed(module)
        if tree is None:
            continue
        seen.add(module)
        todo.extend(_imports(tree, module).values())
    return seen


def _body_modules(module: str, function: str) -> set[str]:
    """The lib modules a dispatch module's function body names, following the module's own
    functions and constants it names."""
    tree = _parsed(module)
    if tree is None:
        return set()
    bound = _imports(tree, module)
    local: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            local[node.name] = node
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                if isinstance(target, ast.Name):
                    local[target.id] = node
    found: set[str] = set()
    seen: set[str] = set()
    todo = [function]
    while todo:
        name = todo.pop()
        if name in seen or name not in local:
            continue
        seen.add(name)
        for sub in ast.walk(local[name]):
            if isinstance(sub, ast.Name):
                if sub.id in bound:
                    found.add(bound[sub.id])
                elif sub.id in local:
                    todo.append(sub.id)
    return found


def _callable_module(fn: Callable[..., Any]) -> tuple[str, str] | None:
    while isinstance(fn, functools.partial):
        fn = fn.func
    module = inspect.getmodule(fn)
    name = module.__name__ if module is not None else ""
    if not name.startswith("lib."):
        return None
    return name.partition(".")[2], getattr(fn, "__name__", "")


def producers(specs: Sequence[Any] | None = None) -> dict[str, frozenset[str]]:
    """Each published block to its producer modules, toolkit-relative posix paths (module doc)."""
    if specs is None:
        from lib.signals import SIGNAL_SCANS  # the headless table; heavy, imported on use
        specs = SIGNAL_SCANS
    sources: list[tuple[Any, list[tuple[str, str]]]] = []
    defined: dict[str, int] = {}
    for spec in specs:
        found = [m for fn in (spec.fn, spec.offline_fn) if fn is not None
                 if (m := _callable_module(fn)) is not None]
        sources.append((spec, found))
        for module in {m for m, _ in found}:
            defined[module] = defined.get(module, 0) + 1
    own: dict[str, set[str]] = {}
    reads: dict[str, tuple[str, ...]] = {}
    for spec, found in sources:
        modules: set[str] = set()
        for module, function in found:
            if defined[module] > 1:
                modules |= {module} | _closure(_body_modules(module, function))
            else:
                modules |= _closure([module])
        for block in (spec.key, *spec.spread):
            own.setdefault(block, set()).update(modules)
            reads[block] = tuple(spec.reads)

    @functools.cache
    def full(block: str) -> frozenset[str]:
        modules = set(own.get(block, ()))
        for name in reads.get(block, ()):
            if name != block:
                modules |= full(name)
        return frozenset(modules)

    return {block: frozenset(_path(m) for m in full(block))
            for spec, _ in sources if not spec.private for block in (spec.spread or (spec.key,))}


def changed_blocks(diff: ScannerDiff, producer_map: Mapping[str, frozenset[str]]) -> dict[str, list[str]]:
    """Each block whose producer modules changed, to those modules sorted."""
    changed = set(diff["changed"])
    return {block: sorted(paths & changed) for block, paths in sorted(producer_map.items())
            if paths & changed}


# --- row attribution ---------------------------------------------------------


def _get(scans: Any, path: str) -> bool:
    node = scans
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return True


def absent_signals(missing: Sequence[str], unknown_side: Sequence[Any], other_side: Sequence[Any]) -> list[str]:
    """The ``missing`` paths absent from a repository's scans on the unknown side, held on the other.

    ``unknown_side`` and ``other_side`` are the same repositories' ``scans`` maps, paired by index.
    A block or sub-block on the path that reports itself unavailable (``available: false``) is a
    degraded scan, not a signal the scanner lacked, so its paths never count as absent."""
    return sorted({path for path in missing for then, now in zip(unknown_side, other_side)
                   if not _get(then, path) and _get(now, path) and not _degraded(then, path)})


def _degraded(scans: Any, path: str) -> bool:
    """A node on ``path`` reports ``available: false``, walked as ``levels.resolve_signal`` walks it."""
    node = scans
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
        if isinstance(node, dict) and node.get("available") is False:
            return True
    return False


def attribute(cause: str, *, absent: Sequence[str], diff: ScannerDiff | None,
              blocks_changed: Mapping[str, list[str]], row_blocks: frozenset[str] | None,
              moved_blocks: set[str]) -> Attribution:
    """A moved team row's cause, present in both runs, from its compare cause (module doc order).

    When the incoming cause is ``set_and_level`` and this replaces it, ``set_moved`` is true so
    the measured-set movement stays reported."""
    result = _attribute(cause, absent=absent, diff=diff, blocks_changed=blocks_changed,
                        row_blocks=row_blocks, moved_blocks=moved_blocks)
    if cause == "set_and_level" and result["cause"] != cause:
        result["set_moved"] = True
    return result


def _attribute(cause: str, *, absent: Sequence[str], diff: ScannerDiff | None,
               blocks_changed: Mapping[str, list[str]], row_blocks: frozenset[str] | None,
               moved_blocks: set[str]) -> Attribution:
    if cause not in ("level", "set_and_level"):
        return {"cause": cause}
    if absent:
        return {"cause": "set", "absent_signals": sorted(absent)}
    if diff is None or diff["status"] == "same":
        return {"cause": cause}
    if diff["status"] == "unavailable":
        return {"cause": CAUSE_UNKNOWN_SCOPE}
    reads = moved_blocks if row_blocks is None else row_blocks & moved_blocks
    hit: list[ScannerCause] = [{"block": b, "modules": blocks_changed[b]}
                               for b in sorted(reads) if b in blocks_changed]
    if hit:
        return {"cause": CAUSE_SCANNER_CHANGED, "scanner": hit}
    return {"cause": cause}


def row_signals(fleet_data: Any) -> dict[str, frozenset[str]] | None:
    """Each row id to the signals its ``fleet-data.json`` cells name; None when there is no such data."""
    if not isinstance(fleet_data, dict) or not isinstance(fleet_data.get("repos"), list):
        return None
    out: dict[str, set[str]] = {}
    for repo in fleet_data["repos"]:
        for cell in (repo.get("rows") or []) if isinstance(repo, dict) else []:
            if isinstance(cell, dict) and isinstance(cell.get("row"), str):
                bucket = out.setdefault(cell["row"], set())
                if isinstance(cell.get("signal"), str):
                    bucket.add(cell["signal"])
    return {row: frozenset(signals) for row, signals in out.items()}
