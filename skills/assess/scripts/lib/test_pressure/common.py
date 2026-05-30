"""Shared file walking for the test-pressure detectors.

Every helper here is best-effort: a broken symlink, an unreadable file, or an
EXCLUDE_DIRS hit degrades to "skip this path", never an exception that aborts
the whole scan.
"""
from __future__ import annotations

import re
from pathlib import Path

from lib.doc_graph import EXCLUDE_DIRS

# Per-heuristic cap so a pathological repo can't bloat the run-context block.
MAX_FINDINGS = 50

_PY_TEST_RE = re.compile(r"(^test_.*\.py$|.*_test\.py$)")
_TS_TEST_RE = re.compile(r".*\.(test|spec)\.(ts|tsx|js|jsx|mjs|cjs)$")
_GO_TEST_RE = re.compile(r".*_test\.go$")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _iter_files(repo_root: Path, exts: set[str] | None = None) -> list[Path]:
    """Files under repo_root with EXCLUDE_DIRS pruned. Best-effort, never raises."""
    out: list[Path] = []
    try:
        walker = repo_root.rglob("*")
    except OSError:  # pragma: no cover - defensive
        return out
    for path in walker:
        try:
            if not path.is_file():
                continue
            rel = path.relative_to(repo_root)
        except OSError:  # pragma: no cover - broken symlink etc.
            continue
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        if exts is not None and path.suffix.lower() not in exts:
            continue
        out.append(path)
    return out


def _is_test_file(path: Path) -> bool:
    name = path.name
    return bool(
        _PY_TEST_RE.match(name) or _TS_TEST_RE.match(name) or _GO_TEST_RE.match(name)
    )


def _rel(repo_root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:  # pragma: no cover
        return str(path)
