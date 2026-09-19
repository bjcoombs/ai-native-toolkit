"""Dart capability entries for the detect-or-propose flow (issue #352).

The JVM flow (``jvm_capabilities.py``) proved the capability-driven model on
one ecosystem. This module applies it to Dart and Flutter for two capabilities,
using the same entry fields (``state``, ``candidate_tool``, ``gloss``, ``note``,
and ``served_by`` when credited) so the scorer reads both the same way:

  * ``linting``  - ``credited`` when a package's nearest ``analysis_options.yaml``
                   (the package directory or the closest ancestor, the analyzer's
                   own lookup) enables lint rules, through a top-level ``include:``
                   or a ``linter: rules:`` section, served by ``dart analyze`` or
                   ``flutter analyze``; ``honest_degrade`` naming ``dart analyze``
                   otherwise. Dart lints are opt-in, so a file that only sets
                   ``analyzer: exclude:`` credits nothing.
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
import re
from pathlib import Path
from typing import Any

from lib.doc_graph import is_excluded_path

_LINTING_CANDIDATE = "dart analyze / flutter analyze"
LIVENESS_CANDIDATE = "dart analyze (unused_* lints)"

_CAPABILITY_GLOSS = {
    "linting": "style and bug-pattern static analysis",
    "liveness": "unused private declarations, imports and locals",
}

# Top-level keys that enable lint rules: ``include:`` pulls in a rule set such as
# package:lints; ``linter:`` followed by an indented ``rules:`` lists rules
# directly. ``analyzer: errors:`` only changes the severity of enabled rules.
_INCLUDE_RE = re.compile(r"^include\s*:\s*\S", re.MULTILINE)
_LINTER_KEY_RE = re.compile(r"linter\s*:")
_RULES_KEY_RE = re.compile(r"rules\s*:")
_COMMENT_RE = re.compile(r"(?m)^\s*#.*$|\s+#.*$")

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
                    ) -> tuple[list[str], dict[Path, bool]]:
    """One walk returning ``(pubspec_files, options)``, where ``options`` maps
    each directory holding an ``analysis_options.yaml`` to whether that file
    enables lint rules. Paths are relative to ``repo_root``, outside the
    excludes."""
    from lib.assess_config import is_user_excluded
    extra_dirs = extra_exclude_dirs or set()
    extra_pats = extra_exclude_patterns or []
    pubspecs: list[str] = []
    options: dict[Path, bool] = {}
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
                options[rel_dir] = _enables_lint_rules(_read(repo_root / rel))
    return sorted(pubspecs), options


def _enables_lint_rules(text: str) -> bool:
    """True when an ``analysis_options.yaml`` text enables lint rules."""
    text = _COMMENT_RE.sub("", text)
    return bool(_INCLUDE_RE.search(text)) or _linter_has_rules(text)


def _linter_has_rules(text: str) -> bool:
    """True when a top-level ``linter:`` block holds an indented ``rules:`` key.

    A line scan, not a regex: one pass, no backtracking, so an arbitrary file
    cannot stall the walk. Blank lines stay inside the block; the next
    unindented line ends it."""
    in_linter = False
    for line in text.splitlines():
        if not line.strip():
            continue
        if line[0] not in " \t":
            in_linter = _LINTER_KEY_RE.match(line) is not None
        elif in_linter and _RULES_KEY_RE.match(line.lstrip()):
            return True
    return False


def _configured_packages(pubspecs: list[str], options: dict[Path, bool]) -> list[str]:
    """Pubspecs whose nearest ``analysis_options.yaml`` (the package directory,
    else the closest ancestor within the repository) enables lint rules. The
    analyzer reads only the nearest file, so a nearer file that enables nothing
    shadows an ancestor that does."""
    configured = []
    for rel in pubspecs:
        pkg = Path(rel).parent
        nearest = next((d for d in (pkg, *pkg.parents) if d in options), None)
        if nearest is not None and options[nearest]:
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
            "note": (f"analysis_options.yaml enables lint rules for "
                     f"{len(configured)} of {len(pubspecs)} Dart package(s); "
                     "`dart analyze` / `flutter analyze` apply it. Detected and "
                     "credited, not re-offered. The file's presence is what is "
                     "detected, not whether CI runs the analyzer."),
        })
    else:
        cap.update({
            "state": "honest_degrade",
            "note": ("No package's nearest analysis_options.yaml enables lint "
                     "rules (the file is absent, or enables no lint rules). "
                     "`dart analyze` (or `flutter analyze`) then reports errors "
                     "and default warnings only; an `include:` of package:lints "
                     "or package:flutter_lints, or a `linter: rules:` list, would "
                     "turn lints on."),
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
    ``capabilities`` dict with ``linting`` and ``liveness`` entries.
    """
    repo_root = repo_root.resolve()
    pubspecs, options = _scan_dart_tree(
        repo_root,
        extra_exclude_dirs=extra_exclude_dirs,
        extra_exclude_patterns=extra_exclude_patterns,
    )
    if not pubspecs:
        return {"available": False, "pubspec_files": []}
    configured = _configured_packages(pubspecs, options)
    return {
        "available": True,
        "pubspec_files": pubspecs,
        "capabilities": {
            "linting": _linting(repo_root, pubspecs, configured),
            "liveness": _liveness(),
        },
    }
