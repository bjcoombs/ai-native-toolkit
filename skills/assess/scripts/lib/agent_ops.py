"""Scan encoded agent-operations guardrails (Layer 8 workflow-maturity evidence).

A team that runs agents in parallel or autonomously needs the operational
guardrails encoded in the repo, not in one operator's head: pre-approved
permission allowlists, hooks that intercept tool calls, sandbox/deny rules, and
routine/loop definitions that make repeated agent work a committed artifact.
This module scans the repo-observable subset of those guardrails:

- ``.claude/settings.json`` / ``.claude/settings.local.json`` - permission
  ``allow`` / ``deny`` / ``ask`` entry counts, hook events, sandbox config.
- ``.claude/hooks/`` - hook scripts on disk.
- ``.claude/workflows/`` and ``.claude/routines/`` - routine/loop definitions
  (evidence of repeated, encoded agent work cycles).

Only **git-tracked** artifacts count toward the summary booleans, mirroring the
Layer 0 rule: a settings file present on disk but uncommitted reaches no clone,
so it is reported (``tracked: false``) but never credited. ``.claude/agents/``
and ``.claude/skills/`` are deliberately NOT scanned here - they already feed
Layer 0; this block feeds Layer 8 only, so the two never double-count.

Pure stdlib. ``scan_agent_ops`` never raises (callers wrap in ``_safe`` anyway).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lib.git_churn import tracked_files

# Candidate settings files, repo-root-relative. Order is the report order.
_SETTINGS_PATHS = (
    ".claude/settings.json",
    ".claude/settings.local.json",
)

# Directories whose committed contents evidence encoded routine/loop work.
_ROUTINE_DIRS = (
    ".claude/workflows",
    ".claude/routines",
)

_HOOKS_DIR = ".claude/hooks"


def _is_tracked(path: Path, tracked: frozenset[Path] | None) -> bool:
    """Whether ``path`` is git-tracked; untracked when tracking is unknowable."""
    if tracked is None:
        return False
    try:
        return path.resolve() in tracked
    except OSError:
        return False


def _count_list(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _scan_settings_file(
    repo_root: Path, rel: str, tracked: frozenset[Path] | None
) -> dict[str, Any] | None:
    """Parse one settings file into its guardrail counts, or None when absent."""
    path = repo_root / rel
    if not path.is_file():
        return None
    entry: dict[str, Any] = {
        "path": rel,
        "tracked": _is_tracked(path, tracked),
        "parse_ok": False,
        "allow_count": 0,
        "deny_count": 0,
        "ask_count": 0,
        "hook_events": 0,
        "sandbox_configured": False,
    }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return entry
    if not isinstance(data, dict):
        return entry
    entry["parse_ok"] = True
    permissions = data.get("permissions")
    if isinstance(permissions, dict):
        entry["allow_count"] = _count_list(permissions.get("allow"))
        entry["deny_count"] = _count_list(permissions.get("deny"))
        entry["ask_count"] = _count_list(permissions.get("ask"))
    hooks = data.get("hooks")
    if isinstance(hooks, dict):
        entry["hook_events"] = len(hooks)
    entry["sandbox_configured"] = "sandbox" in data
    return entry


def _scan_dir(
    repo_root: Path, rel: str, tracked: frozenset[Path] | None
) -> dict[str, Any]:
    """Count files (and how many are tracked) directly under ``rel``."""
    root = repo_root / rel
    file_count = 0
    tracked_count = 0
    if root.is_dir():
        try:
            for p in sorted(root.rglob("*")):
                if not p.is_file() or p.name.startswith("."):
                    continue
                file_count += 1
                if _is_tracked(p, tracked):
                    tracked_count += 1
        except OSError:
            pass
    return {
        "path": rel,
        "present": root.is_dir(),
        "file_count": file_count,
        "tracked_count": tracked_count,
    }


def scan_agent_ops(repo_root: Path) -> dict[str, Any]:
    """Build the run-context ``agent_ops`` block.

    Returns ``settings`` (one entry per settings file found), ``hooks_dir``,
    ``routine_dirs``, and a ``summary`` of three booleans - each True only on
    **tracked** evidence:

    - ``permissions_encoded``: a tracked settings file carries at least one
      permission ``allow`` / ``deny`` / ``ask`` entry.
    - ``hooks_present``: a tracked settings file configures hook events, or
      ``.claude/hooks/`` holds at least one tracked script.
    - ``routines_present``: a routine dir holds at least one tracked file.
    """
    repo_root = repo_root.resolve()
    tracked = tracked_files(repo_root)

    settings = [
        entry
        for rel in _SETTINGS_PATHS
        if (entry := _scan_settings_file(repo_root, rel, tracked)) is not None
    ]
    hooks_dir = _scan_dir(repo_root, _HOOKS_DIR, tracked)
    routine_dirs = [_scan_dir(repo_root, rel, tracked) for rel in _ROUTINE_DIRS]

    tracked_settings = [s for s in settings if s["tracked"]]
    permissions_encoded = any(
        s["allow_count"] + s["deny_count"] + s["ask_count"] > 0
        for s in tracked_settings
    )
    hooks_present = (
        any(s["hook_events"] > 0 for s in tracked_settings)
        or hooks_dir["tracked_count"] > 0
    )
    routines_present = any(d["tracked_count"] > 0 for d in routine_dirs)

    return {
        "available": True,
        "settings": settings,
        "hooks_dir": hooks_dir,
        "routine_dirs": routine_dirs,
        "summary": {
            "permissions_encoded": permissions_encoded,
            "hooks_present": hooks_present,
            "routines_present": routines_present,
        },
    }
