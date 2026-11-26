"""Resolve a run's ``--scope`` and the artifact directory it writes to.

`Scope` (#1110) is the union of folders a run measures: one or more ``--scope`` values,
each a path or a glob (``skills/*-review``) matched from the repository root. Its
`roots` are the absolute folders (sorted, a root inside another dropped), `contains(path)`
tells whether a path lies under one of them, and `slug` names the artifact directory: the
path with separators hyphenated for one plain folder (as before #1110), else ``scope-``
and a stable hash over the sorted patterns. `label` is the repo-relative text the run
context and the signals stamp carry: the folder, or the sorted patterns joined by ``,``.
`scope_roots(scope, root)` is where a walk starts: the scope's roots, or the root.
A root inside another is dropped from `roots` but its pattern stays in `patterns`, so the
label and slug name what was asked: ``a`` plus ``a/b`` measures ``a`` under a hashed slug.
`resolve_scope` turns the ``--scope`` arguments into `(Scope, label, slug)`, raising
`ValueError` for a missing or outside-repo path or a pattern that matches nothing
(naming it); a whole-repo run returns `(None, None, "")`. `assess_dir_for` names
`.assess/` or `.assess/<slug>/`, and `load_current_stats` reads that directory's
`complexity-stats.json`, or an empty snapshot when none was written. The empty snapshot
carries `available: false` and `NO_STATS_REASON` (`no_complexity_stats`, #959), and
`run_setup.run_header` turns it into an unavailable `stats_summary` with `files_scored`
null, so a run whose treemap step wrote nothing reads not measured
(`no_complexity_stats`), never the measured empty set (`no_source_files`, #820). With `scc` on
PATH (it reads every language) the treemap writes a zero-file sidecar when nothing is
scorable (`complexity-treemap.py`, still exit 1), so the run reads `no_source_files`;
without `scc` zero files is not a measurement and no sidecar is written. Tests:
`tests/test_scope.py`, `tests/test_run_scope.py`, `tests/test_run_setup.py`, `tests/test_complexity_treemap.py`.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.complexity.stats_diff import load_stats

_GLOB_CHARS = frozenset("*?[")


@dataclass(frozen=True)
class Scope:
    """The folders a scoped run measures: ``patterns`` as given (sorted, repo-relative
    POSIX), ``roots`` the absolute folders they name (sorted, nested roots dropped)."""

    repo_root: Path
    patterns: tuple[str, ...]
    roots: tuple[Path, ...]

    @classmethod
    def of(cls, repo_root: Path, folder: Path) -> Scope:
        """A one-folder scope (a monorepo unit, or a caller holding one path)."""
        root = repo_root.resolve()
        path = (folder if folder.is_absolute() else root / folder).resolve()
        rel = path.relative_to(root).as_posix() if path.is_relative_to(root) else path.as_posix()
        return cls(root, (rel,), (path,))

    def contains(self, path: Path) -> bool:
        """True when ``path`` (absolute, or relative to the repository) lies under a root."""
        resolved = (path if path.is_absolute() else self.repo_root / path).resolve()
        return any(resolved.is_relative_to(r) for r in self.roots)

    @property
    def label(self) -> str:
        """The folder, or the sorted patterns joined by ``,``."""
        return ",".join(self.patterns)

    @property
    def slug(self) -> str:
        """``.assess/<slug>/``: the hyphenated folder for one plain path, else a hash."""
        if len(self.patterns) == 1 and not _GLOB_CHARS & set(self.patterns[0]):
            return self.patterns[0].replace("/", "-")
        digest = hashlib.sha256("\n".join(self.patterns).encode("utf-8")).hexdigest()
        return f"scope-{digest[:12]}"


# ``--scope`` as a caller passes it (one path, several, or an already-resolved ``Scope``),
# and ``resolve_scope``'s (Scope, label, slug) triple.
RequestedScope = Path | Sequence[Path] | Scope | None
ResolvedScope = tuple[Scope | None, str | None, str]


def scope_roots(scope: Scope | None, root: Path) -> tuple[Path, ...]:
    """The folders a walk starts from: the scope's roots, or ``root`` resolved."""
    return scope.roots if scope is not None else (root.resolve(),)


def _matches(repo_root: Path, raw: Path) -> tuple[str, list[Path]]:
    """One ``--scope`` value as (its pattern text, the absolute paths it names)."""
    text = raw.as_posix()
    if raw.is_absolute() or not _GLOB_CHARS & set(text):
        path = (raw if raw.is_absolute() else repo_root / raw).resolve()
        if not path.exists():
            raise ValueError(f"scope path does not exist: {path}")
        if not path.is_relative_to(repo_root):
            raise ValueError(f"scope path is not under repo root {repo_root}: {path}")
        return path.relative_to(repo_root).as_posix(), [path]
    found = sorted({m.resolve() for m in repo_root.glob(text)
                    if m.resolve().is_relative_to(repo_root)})
    if not found:
        raise ValueError(f"scope pattern matches nothing: {text}")
    return text.rstrip("/"), found


def resolve_scope(repo_root: Path, scope: RequestedScope) -> ResolvedScope:
    """Resolve the ``--scope`` arguments into (Scope, repo-relative label, slug).

    A whole-repo run (``scope`` is None or empty) returns ``(None, None, "")`` so the
    caller keeps ``repo_root/.assess`` and every output is byte-identical to a
    pre-scope run. Each value is a path (it must exist under ``repo_root``) or a glob
    matched from ``repo_root``; a one-folder scope keeps the pre-#1110 label and slug,
    so its artifacts land under ``.assess/<slug>/`` as before. Raises ``ValueError``
    on a missing or outside-repo path or a pattern that matches nothing, naming it, so
    the CLI can report it cleanly (exit 2).
    """
    if isinstance(scope, Scope):
        return scope, scope.label, scope.slug
    raws = [scope] if isinstance(scope, Path) else list(scope or [])
    if not raws:
        return None, None, ""
    root = repo_root.resolve()
    patterns: set[str] = set()
    paths: set[Path] = set()
    for raw in raws:
        pattern, found = _matches(root, Path(raw))
        patterns.add(pattern)
        paths.update(found)
    roots = tuple(p for p in sorted(paths)
                  if not any(p != q and p.is_relative_to(q) for q in paths))
    resolved = Scope(root, tuple(sorted(patterns)), roots)
    return resolved, resolved.label, resolved.slug


def assess_dir_for(repo_root: Path, scope_slug: str) -> Path:
    """The artifact directory: .assess/, or .assess/<slug>/ for a scoped run."""
    return repo_root / ".assess" / scope_slug if scope_slug else repo_root / ".assess"


# The reason the run context's ``stats_summary`` gives when no sidecar was written (#959).
NO_STATS_REASON = ("no_complexity_stats: the run wrote no complexity-stats.json, so it is not "
                   "known how many files would be scored")


def load_current_stats(assess_dir: Path) -> dict[str, Any]:
    """This run's complexity stats, or an empty snapshot when none was written.

    The empty snapshot is marked ``available: false`` with ``NO_STATS_REASON``, so the
    run context reads "not measured" rather than a measured zero: a missing sidecar means
    the treemap step did not run or did not finish, or scored nothing without ``scc``
    (with ``scc`` on PATH it writes a zero-file sidecar), not a repository with no source (``files_scored`` 0 would fire the empty-set rule,
    ``no_source_files``). Its ``files_scored`` stays 0 for the readers that count it
    (the wiki log)."""
    return load_stats(assess_dir / "complexity-stats.json") or {
        "available": False, "reason": NO_STATS_REASON,
        "files_scored": 0, "top_hotspots": [], "top_complex": [], "top_large": [],
        "loc": {}, "ccn": {},
    }
