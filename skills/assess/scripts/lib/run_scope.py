"""Resolve a run's ``--scope`` and the artifact directory it writes to.

`resolve_scope` turns a `--scope` argument into the absolute path, the repo-relative
path and the artifact-directory slug, raising `ValueError` for a missing or
outside-repo path; a whole-repo run returns `(None, None, "")`. `assess_dir_for` names
`.assess/` or `.assess/<slug>/`, and `load_current_stats` reads that directory's
`complexity-stats.json`, or an empty snapshot when none was written. The empty snapshot
carries `available: false` and `NO_STATS_REASON` (`no_complexity_stats`, #959), and
`run_setup.run_header` turns it into an unavailable `stats_summary` with `files_scored`
null, so a run whose treemap step wrote nothing reads not measured
(`no_complexity_stats`), never the measured empty set (`no_scored_files`). With `scc` on
PATH (it reads every language) the treemap writes a zero-file sidecar when nothing is
scorable (`complexity-treemap.py`, still exit 1), so the run reads `no_scored_files`;
without `scc` zero files is not a measurement and no sidecar is written. Tests:
`tests/test_scope.py`, `tests/test_run_setup.py`, `tests/test_complexity_treemap.py`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lib.complexity.stats_diff import load_stats


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
    rel = scope_abs.relative_to(repo_root).as_posix()
    return scope_abs, rel, rel.replace("/", "-")


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
    ``no_scored_files``). Its ``files_scored`` stays 0 for the readers that count it
    (the wiki log)."""
    return load_stats(assess_dir / "complexity-stats.json") or {
        "available": False, "reason": NO_STATS_REASON,
        "files_scored": 0, "top_hotspots": [], "top_complex": [], "top_large": [],
        "loc": {}, "ccn": {},
    }
