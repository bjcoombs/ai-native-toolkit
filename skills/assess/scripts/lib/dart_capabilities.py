"""Dart capability entries for the detect-or-propose flow (issue #352).

The JVM flow (``jvm_capabilities.py``) proved the capability-driven model on
one ecosystem. This module applies it to Dart and Flutter for two capabilities,
using the same entry fields (``state``, ``candidate_tool``, ``gloss``, ``note``,
and ``served_by`` when credited) so the scorer reads both the same way:

  * ``linting``  - ``credited`` when an ``analysis_options.yaml`` configures the
                   analyzer for a package (in the package directory or an
                   ancestor, the analyzer's own lookup), served by ``dart analyze``
                   or ``flutter analyze``; ``honest_degrade`` naming
                   ``dart analyze`` otherwise.
  * ``liveness`` - always ``honest_degrade``. The candidate is the analyzer's
                   built-in ``unused_*`` diagnostics. The scan neither runs the
                   analyzer nor reads its output, so it credits nothing and feeds
                   no candidates into ``dead_code``. No third-party package is
                   named: the one commonly suggested, ``dart_code_metrics``, is
                   discontinued for Dart 3.

A repository is Dart when it holds a ``pubspec.yaml`` outside the shared and
user-supplied excludes. The result surfaces in ``run-context.json`` under
``language_capabilities.dart``, a sibling of the JVM-only ``capability_offers``.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from lib.doc_graph import is_excluded_path

DART_CAPABILITIES = ("linting", "liveness")

_LINTING_CANDIDATE = "dart analyze / flutter analyze"
LIVENESS_CANDIDATE = "dart analyze (unused_* lints)"

_CAPABILITY_GLOSS = {
    "linting": "style and bug-pattern static analysis",
    "liveness": "unused private declarations, imports and locals",
}

# What the liveness candidate would provide, and what it would not. Shared by the
# capability note and the dead_code.tools entry so the two never drift apart.
LIVENESS_REASON = (
    "Dart liveness is unserved: the scan does not run the Dart analyzer. Its "
    "built-in unused_* diagnostics (unused_element, unused_field, unused_import, "
    "unused_local_variable) would report unused private declarations, imports "
    "and locals; a public member no code calls is not reported."
)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _scan_dart_tree(repo_root: Path,
                    extra_exclude_dirs: set[str] | None = None,
                    extra_exclude_patterns: list[str] | None = None,
                    ) -> tuple[list[str], set[Path]]:
    """One walk returning ``(pubspec_files, dirs_holding_analysis_options)``,
    both relative to ``repo_root`` and both outside the excludes."""
    from lib.assess_config import is_user_excluded
    extra_dirs = extra_exclude_dirs or set()
    extra_pats = extra_exclude_patterns or []
    pubspecs: list[str] = []
    option_dirs: set[Path] = set()
    for dirpath, dirnames, filenames in os.walk(repo_root):
        rel_dir = Path(dirpath).relative_to(repo_root)
        dirnames[:] = [
            d for d in dirnames
            if not is_excluded_path(rel_dir / d)
            and not is_user_excluded(rel_dir / d, extra_dirs, [])
        ]
        for name in ("pubspec.yaml", "analysis_options.yaml"):
            if name not in filenames:
                continue
            rel = rel_dir / name
            if is_excluded_path(rel) or is_user_excluded(rel, extra_dirs, extra_pats):
                continue
            if name == "pubspec.yaml":
                pubspecs.append(rel.as_posix())
            else:
                option_dirs.add(rel_dir)
    return sorted(pubspecs), option_dirs


def _configured_packages(pubspecs: list[str], option_dirs: set[Path]) -> list[str]:
    """Pubspecs whose package directory, or an ancestor within the repository,
    holds an ``analysis_options.yaml``."""
    configured = []
    for rel in pubspecs:
        pkg = Path(rel).parent
        if any(d == pkg or d in pkg.parents for d in option_dirs):
            configured.append(rel)
    return configured


def _analyzer_command(repo_root: Path, pubspecs: list[str]) -> str:
    """``flutter analyze`` when a package depends on the Flutter SDK, else
    ``dart analyze``."""
    for rel in pubspecs:
        if "sdk: flutter" in _read(repo_root / rel):
            return "flutter analyze"
    return "dart analyze"


def _linting(repo_root: Path, pubspecs: list[str], configured: list[str]) -> dict:
    cap: dict[str, Any] = {
        "candidate_tool": _LINTING_CANDIDATE,
        "gloss": _CAPABILITY_GLOSS["linting"],
    }
    if configured:
        cap.update({
            "state": "credited",
            "served_by": [_analyzer_command(repo_root, configured)],
            "note": (f"analysis_options.yaml configures the analyzer for "
                     f"{len(configured)} of {len(pubspecs)} Dart package(s); "
                     "`dart analyze` / `flutter analyze` apply it. Detected and "
                     "credited, not re-offered. The file's presence is what is "
                     "detected, not whether CI runs the analyzer."),
        })
    else:
        cap.update({
            "state": "honest_degrade",
            "note": ("No analysis_options.yaml configures the analyzer. "
                     "Without one, `dart analyze` (or `flutter analyze`) reports "
                     "errors and default warnings but enables no lint rules; a "
                     "file that includes package:lints or package:flutter_lints "
                     "would turn them on."),
        })
    return cap


def _liveness() -> dict:
    return {
        "state": "honest_degrade",
        "candidate_tool": LIVENESS_CANDIDATE,
        "gloss": _CAPABILITY_GLOSS["liveness"],
        "note": LIVENESS_REASON,
    }


def scan_dart_capabilities(repo_root: Path, *,
                           extra_exclude_dirs: set[str] | None = None,
                           extra_exclude_patterns: list[str] | None = None,
                           ) -> dict:
    """Capability-driven Dart scan. Read-only: runs no tool.

    Returns ``{"available": False, "pubspec_files": []}`` for a repository with
    no in-scope ``pubspec.yaml``, else ``available``, ``pubspec_files`` and a
    ``capabilities`` dict keyed by ``DART_CAPABILITIES``.
    """
    repo_root = repo_root.resolve()
    pubspecs, option_dirs = _scan_dart_tree(
        repo_root,
        extra_exclude_dirs=extra_exclude_dirs,
        extra_exclude_patterns=extra_exclude_patterns,
    )
    if not pubspecs:
        return {"available": False, "pubspec_files": []}
    configured = _configured_packages(pubspecs, option_dirs)
    return {
        "available": True,
        "pubspec_files": pubspecs,
        "capabilities": {
            "linting": _linting(repo_root, pubspecs, configured),
            "liveness": _liveness(),
        },
    }
