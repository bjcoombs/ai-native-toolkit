"""Resolve a run's ``--scope`` and the artifact directory it writes to."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lib.stats_diff import load_stats


def resolve_scope(
    repo_root: Path, scope: Path | None
) -> tuple[Path | None, str | None, str]:
    """Resolve a ``--scope`` argument into (absolute path, repo-relative, slug).

    A whole-repo run (``scope`` is None) returns ``(None, None, "")`` so the
    caller keeps ``repo_root/.assess`` and every output is byte-identical to a
    pre-scope run. A scoped run validates the path exists and is under
    ``repo_root``; the slug replaces path separators with hyphens so artifacts
    land under ``.assess/<slug>/``. Raises ``ValueError`` on a missing or
    outside-repo path so the CLI can report it cleanly (a non-zero exit).
    """
    if scope is None:
        return None, None, ""
    scope_abs = (scope if scope.is_absolute() else repo_root / scope).resolve()
    if not scope_abs.exists():
        raise ValueError(f"scope path does not exist: {scope_abs}")
    if not scope_abs.is_relative_to(repo_root):
        raise ValueError(f"scope path is not under repo root {repo_root}: {scope_abs}")
    rel = scope_abs.relative_to(repo_root)
    slug = str(rel).replace("/", "-").replace("\\", "-")
    return scope_abs, str(rel), slug


def assess_dir_for(repo_root: Path, scope_slug: str) -> Path:
    """The artifact directory: .assess/, or .assess/<slug>/ for a scoped run."""
    return repo_root / ".assess" / scope_slug if scope_slug else repo_root / ".assess"


def load_current_stats(assess_dir: Path) -> dict[str, Any]:
    """This run's complexity stats, or an empty snapshot when none was written."""
    return load_stats(assess_dir / "complexity-stats.json") or {
        "files_scored": 0, "top_hotspots": [], "top_complex": [], "top_large": [],
        "loc": {}, "ccn": {},
    }
