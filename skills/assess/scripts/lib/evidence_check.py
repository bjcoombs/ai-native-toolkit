"""Deterministic re-check of the evidence a layer verdict cites.

The layer scorer is a model; the claims it cites as evidence ("CLAUDE.md
exists", "no workflow calls scripts/check-x.sh") are facts about the filesystem
that a model can get wrong. This module re-checks each claim with ``exists()``
or a literal substring search - no model, no heuristics - and splits the list
into the entries that hold (``evidence``) and the entries that do not
(``evidence_rejected``).

Evidence entry format (a flat JSON array of objects):

- ``layer``: integer 0-8, the layer whose verdict cites the entry.
- ``kind``: one of ``path_exists``, ``path_absent``, ``referenced_in``,
  ``not_referenced_in``, ``file_contains``.
- ``path``: relative to the repository root under check. For the two reference
  kinds it names one file or a directory searched recursively; for
  ``file_contains`` it names one file.
- ``needle``: the literal searched for (reference kinds and ``file_contains``).

Keys the checker does not know pass through unchanged. A rejected entry keeps
its kind and arguments and gains a ``reason`` string.

CLI (run from ``skills/assess/scripts``)::

    uv run python -m lib.evidence_check <repo_root> <evidence.json> --json <out.json>

Exit 0 when every entry verifies, 1 when any is rejected, 2 when the input is
not a JSON array (no output is written then).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

KINDS = ("path_exists", "path_absent", "referenced_in", "not_referenced_in", "file_contains")
_NEEDLE_KINDS = frozenset({"referenced_in", "not_referenced_in", "file_contains"})

# VCS metadata is not repository content: a needle found only in .git/ (a
# commit message, a reflog) is not a reference an agent or CI would follow.
_SKIP_DIRS = frozenset({".git"})


def _resolve(repo_root: Path, rel: str) -> Path | None:
    """Resolve ``rel`` under ``repo_root``; None when it escapes the root or
    cannot be resolved (an embedded NUL, a symlink loop)."""
    if "\0" in rel:
        return None
    try:
        root = Path(repo_root).resolve()
        target = (root / rel).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if target != root and not target.is_relative_to(root):
        return None
    return target


def _in_git_metadata(rel: str) -> bool:
    return any(part in _SKIP_DIRS for part in Path(rel).parts)


def _file_has(path: Path, needle: bytes) -> bool | None:
    """True/False for a completed read; None when the file cannot be read."""
    try:
        return needle in path.read_bytes()
    except OSError:
        return None


def _search(repo_root: Path | str, needle: str, path: str) -> bool | None:
    """True when found; False when a complete search found nothing; None when
    nothing was found but some file or directory could not be searched."""
    if not needle or _in_git_metadata(path):
        return False
    target = _resolve(Path(repo_root), path)
    if target is None:
        return False
    raw = needle.encode("utf-8")
    if target.is_file():
        return _file_has(target, raw)
    if not target.is_dir():
        return False
    errors: list[OSError] = []
    for dirpath, dirnames, filenames in os.walk(target, onerror=errors.append):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for name in sorted(filenames):
            hit = _file_has(Path(dirpath) / name, raw)
            if hit:
                return True
            if hit is None:
                errors.append(OSError(name))
    return None if errors else False


def is_referenced_in(repo_root: Path | str, needle: str, path: str) -> bool:
    """True when the literal ``needle`` occurs in the file at ``path``, or in
    any file under the directory ``path`` (searched recursively).

    ``path`` is relative to ``repo_root``. A path that does not exist, that
    resolves outside ``repo_root``, or that lies inside ``.git/`` holds no
    reference and returns False, as does a search that found nothing because
    a file or directory could not be read. Symlinked directories are not
    followed; ``.git/`` is skipped.
    """
    return _search(repo_root, needle, path) is True


def check_entry(repo_root: Path | str, entry: Any) -> str | None:
    """Return None when ``entry`` holds, else the reason it is rejected."""
    if not isinstance(entry, dict):
        return "entry is not an object"
    kind = entry.get("kind")
    if kind not in KINDS:
        return f"unknown kind {kind!r}"
    rel = entry.get("path")
    if not isinstance(rel, str) or not rel:
        return "missing path"
    raw_needle = entry.get("needle")
    needle = raw_needle if isinstance(raw_needle, str) else ""
    if kind in _NEEDLE_KINDS and not needle:
        return "missing needle"
    target = _resolve(Path(repo_root), rel)
    if target is None:
        return "path resolves outside the repository root or cannot be resolved"

    if kind == "path_exists":
        return None if target.exists() else "path does not exist"
    if kind == "path_absent":
        return "path exists" if target.exists() else None
    if kind == "file_contains":
        if not target.is_file():
            return "path is not a file"
        hit = _file_has(target, needle.encode("utf-8"))
        if hit is None:
            return "file could not be read"
        return None if hit else "needle not found in file"
    # referenced_in / not_referenced_in: the place searched must exist, or the
    # claim is about nothing (use path_absent to claim the place is missing).
    if _in_git_metadata(rel):
        return "path is inside .git/, which the reference search does not enter"
    if not target.exists():
        return "path does not exist"
    found = _search(repo_root, needle, rel)
    if found is True:
        return None if kind == "referenced_in" else "needle found under path"
    if found is None:
        return "part of path could not be searched, so the result is incomplete"
    return "needle not found under path" if kind == "referenced_in" else None


def check_evidence(repo_root: Path | str, entries: list[Any]) -> dict[str, list[Any]]:
    """Split ``entries`` into ``evidence`` (verified) and ``evidence_rejected``.

    Input order is kept in both lists. Verified entries are returned as given;
    rejected entries are copies with a ``reason`` added. The input is not
    mutated.
    """
    verified: list[Any] = []
    rejected: list[Any] = []
    for entry in entries:
        reason = check_entry(repo_root, entry)
        if reason is None:
            verified.append(entry)
        elif isinstance(entry, dict):
            rejected.append({**entry, "reason": reason})
        else:
            rejected.append({"entry": entry, "reason": reason})
    return {"evidence": verified, "evidence_rejected": rejected}


def describe(entry: dict[str, Any]) -> str:
    """One-line name for an entry: kind, path, and needle where it has one."""
    if "kind" not in entry:
        return repr(entry.get("entry", entry))
    parts = [str(entry.get("kind")), str(entry.get("path"))]
    if "needle" in entry:
        parts.append(repr(entry["needle"]))
    return " ".join(parts)


def main() -> int:
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="Re-check /assess evidence entries.")
    ap.add_argument("repo_root", type=Path)
    ap.add_argument("evidence", type=Path, help="JSON file holding a flat array of entries")
    ap.add_argument("--json", type=Path, help="write {evidence, evidence_rejected} here")
    args = ap.parse_args()

    try:
        entries = json.loads(args.evidence.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"evidence_check: cannot read {args.evidence}: {exc}", file=sys.stderr)
        return 2
    if not isinstance(entries, list):
        print("evidence_check: input must be a flat JSON array of entries", file=sys.stderr)
        return 2

    result = check_evidence(args.repo_root, entries)
    if args.json:
        args.json.write_text(json.dumps(result, indent=2) + "\n")

    print(f"verified {len(result['evidence'])}, rejected {len(result['evidence_rejected'])}")
    for entry in result["evidence_rejected"]:
        print(f"  rejected: {describe(entry)} ({entry['reason']})")
    return 1 if result["evidence_rejected"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
