"""Deterministic re-check of the evidence a layer verdict cites.

The layer scorer is a model; the claims it cites as evidence ("CLAUDE.md
exists", "no workflow calls scripts/check-x.sh") are facts about the filesystem
that a model can get wrong. This module re-checks each claim with ``exists()``
or a literal substring search - no model, no heuristics - and splits the list
into the entries that hold (``evidence``) and the entries that do not
(``evidence_rejected``).

Evidence entry format (a flat JSON array of objects):

- ``layer``: integer 0-8, the layer whose verdict cites the entry. Carried
  through unchecked: this module checks facts about the filesystem, and the
  caller owns the rest of the schema.
- ``kind``: one of ``path_exists``, ``path_absent``, ``referenced_in``,
  ``not_referenced_in``, ``file_contains``.
- ``path``: relative to the repository root under check. For the two reference
  kinds it names one file or a directory searched recursively; for
  ``file_contains`` it names one file.
- ``needle``: the literal searched for (reference kinds and ``file_contains``).

Keys the checker does not know pass through unchanged. A rejected entry keeps
its kind and arguments and gains a ``reason`` string.

Every check fails closed: a malformed entry (including a needle that is not
encodable text) is rejected, not raised on, and a claim whose check could not
read everything it needed - ``referenced_in``, ``not_referenced_in`` or
``file_contains`` - is rejected as incomplete rather than decided on the part
that was read.

CLI (run from ``skills/assess/scripts``)::

    uv run python -m lib.evidence_check <repo_root> <evidence.json> --json <out.json>

Exit 0 when every entry verifies, 1 when any is rejected, 2 when the evidence
file cannot be read, is not UTF-8 JSON, or is not a JSON array, or ``repo_root`` is not
a directory (no output is written then), or the ``--json`` file cannot be written.
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
# This applies to the two reference kinds only: file_contains is a claim about
# one named file, so it may read a path inside .git/ (e.g. .git/config).
_GIT_DIR = ".git"
# Directories the recursive walk does not enter. .assess/ holds this tool's own
# previous output, which quotes repository paths in prose; reading it as
# evidence would let one run's report decide the next (lib/doc_graph.py and
# lib/structure_graph.py exclude it for the same reason). Only the walk skips
# these: a path that names .assess/ directly is still searched.
_SKIP_DIRS = frozenset({_GIT_DIR, ".assess"})


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


def _under(path: Path, top: Path) -> bool:
    return path == top or path.is_relative_to(top)


def _in_git_metadata(rel: str, repo_root: Path | str) -> bool:
    """True when ``rel`` names ``.git/`` or anything in it, literally or through
    a symlink that resolves there."""
    if _GIT_DIR in Path(rel).parts:
        return True
    target = _resolve(Path(repo_root), rel)
    return target is not None and _under(target, Path(repo_root).resolve() / _GIT_DIR)


def _encode(needle: str) -> bytes | None:
    """UTF-8 bytes of ``needle``; None when it holds a lone surrogate (which
    ``json.loads`` accepts from a ``\\udXXX`` escape)."""
    try:
        return needle.encode("utf-8")
    except UnicodeEncodeError:
        return None


_CHUNK = 1 << 20  # read files in 1 MiB chunks so one large asset cannot set peak memory


def _file_has(path: Path, needle: bytes) -> bool | None:
    """True/False for a completed read; None when the file cannot be read.

    Reads in chunks, keeping the last ``len(needle) - 1`` bytes of each chunk
    so a needle that spans a chunk boundary is still found.
    """
    keep = len(needle) - 1
    tail = b""
    try:
        with path.open("rb") as fh:
            while chunk := fh.read(_CHUNK):
                window = tail + chunk
                if needle in window:
                    return True
                tail = window[-keep:] if keep else b""
    except OSError:
        return None
    return False


def _link_target(root: Path, link: Path) -> Path | None:
    """Where a symlink met in the walk leads, when that is repository content;
    None for a link out of the root, a dangling one, or one into a directory the
    walk does not enter (``.git/``, ``.assess/``) - none of those is content the
    walk would read. A link that cannot be resolved (a loop) is raised as OSError."""
    try:
        dest = link.resolve(strict=True)
    except FileNotFoundError:
        return None
    except RuntimeError as exc:  # symlink loop on older Pythons
        raise OSError(str(link)) from exc
    if not _under(dest, root) or any(_under(dest, root / d) for d in _SKIP_DIRS):
        return None
    return dest


def _walk(root: Path, target: Path, raw: bytes) -> bool | None:
    """Search the directory ``target`` recursively; see ``_search``.

    Nothing met in the walk is skipped silently unless it is not repository
    content. A symlink out of the root, or a dangling one, is skipped. A symlink
    to a file inside the root is read at its target. A symlink to a directory
    inside the root, unless that directory is already under ``target``, and any
    FIFO, socket or device, is content this search did not read, so it marks the
    result incomplete.
    """
    errors: list[OSError] = []
    for dirpath, dirnames, filenames in os.walk(target, onerror=errors.append):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for name in list(dirnames):
            child = Path(dirpath) / name
            if not child.is_symlink():
                continue  # os.walk descends into it
            dirnames.remove(name)  # os.walk does not follow it; decide here
            try:
                dest = _link_target(root, child)
            except OSError as exc:
                errors.append(exc)
                continue
            if dest is not None and not (dest == target or dest.is_relative_to(target)):
                errors.append(OSError(str(child)))
        for name in sorted(filenames):
            child = Path(dirpath) / name
            read = child
            if child.is_symlink():
                try:
                    dest = _link_target(root, child)
                except OSError as exc:
                    errors.append(exc)
                    continue
                if dest is None:
                    continue
                read = dest
            # Regular files only: a FIFO would block the read forever.
            if not read.is_file():
                errors.append(OSError(str(child)))
                continue
            hit = _file_has(read, raw)
            if hit:
                return True
            if hit is None:
                errors.append(OSError(name))
    return None if errors else False


def _search(repo_root: Path | str, needle: str, path: str) -> bool | None:
    """True when found; False when a complete search found nothing; None when
    nothing was found but some file or directory could not be searched."""
    if not needle or _in_git_metadata(path, repo_root):
        return False
    raw = _encode(needle)
    target = _resolve(Path(repo_root), path)
    if raw is None or target is None:
        return False
    if target.is_file():
        return _file_has(target, raw)
    if not target.exists():
        return False
    if not target.is_dir():
        # A FIFO, socket or device named directly: never opened (a FIFO would
        # block the read), so the search is incomplete, not empty.
        return None
    return _walk(Path(repo_root).resolve(), target, raw)


def is_referenced_in(repo_root: Path | str, needle: str, path: str) -> bool:
    """True when the literal ``needle`` occurs in the file at ``path``, or in
    any file under the directory ``path`` (searched recursively).

    ``path`` is relative to ``repo_root``. A path that does not exist, that
    resolves outside ``repo_root``, or that lies inside ``.git/`` holds no
    reference and returns False, as does a search that found nothing because
    something under ``path`` could not be read. The directory walk does not
    enter ``.git/`` or ``.assess/``, skips symlinks that lead out of the root,
    reads a symlinked file inside the root at its target, and treats a FIFO,
    socket, device or symlinked directory it did not search as unread.
    """
    return _search(repo_root, needle, path) is True


def _malformed(repo_root: Path | str, entry: Any) -> str | None:
    """Reason an entry cannot be checked at all, or None when it is well formed."""
    if not isinstance(entry, dict):
        return "entry is not an object"
    if not Path(repo_root).is_dir():
        return "repository root is not a directory"
    kind = entry.get("kind")
    if kind not in KINDS:
        return f"unknown kind {kind!r}"
    rel = entry.get("path")
    if not isinstance(rel, str) or not rel:
        return "missing path"
    needle = entry.get("needle")
    if kind in _NEEDLE_KINDS:
        if not isinstance(needle, str) or not needle:
            return "missing needle"
        if _encode(needle) is None:
            return "needle is not valid text (a lone surrogate cannot be encoded)"
    return None


def _check_reference(repo_root: Path | str, kind: str, needle: str, rel: str, target: Path) -> str | None:
    # The place searched must exist, or the claim is about nothing (use
    # path_absent to claim the place is missing).
    if _in_git_metadata(rel, repo_root):
        return "path is inside .git/, which the reference search does not enter"
    if not target.exists():
        return "path does not exist"
    found = _search(repo_root, needle, rel)
    if found is True:
        return None if kind == "referenced_in" else "needle found under path"
    if found is None:
        return "part of path could not be searched, so the result is incomplete"
    return "needle not found under path" if kind == "referenced_in" else None


def check_entry(repo_root: Path | str, entry: Any) -> str | None:
    """Return None when ``entry`` holds, else the reason it is rejected."""
    reason = _malformed(repo_root, entry)
    if reason is not None:
        return reason
    kind: str = entry["kind"]
    rel: str = entry["path"]
    needle: str = entry.get("needle") or ""
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
        hit = _file_has(target, needle.encode("utf-8"))  # encodable: _malformed checked
        if hit is None:
            return "file could not be read"
        return None if hit else "needle not found in file"
    return _check_reference(repo_root, kind, needle, rel, target)


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
        entries = json.loads(args.evidence.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"evidence_check: cannot read {args.evidence}: {exc}", file=sys.stderr)
        return 2
    if not isinstance(entries, list):
        print("evidence_check: input must be a flat JSON array of entries", file=sys.stderr)
        return 2

    if not args.repo_root.is_dir():
        print(f"evidence_check: repo_root {args.repo_root} is not a directory", file=sys.stderr)
        return 2

    result = check_evidence(args.repo_root, entries)
    if args.json:
        try:
            args.json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            # Exit 1 means "an entry was rejected"; a failed write must not read as that.
            print(f"evidence_check: cannot write {args.json}: {exc}", file=sys.stderr)
            return 2

    print(f"verified {len(result['evidence'])}, rejected {len(result['evidence_rejected'])}")
    for entry in result["evidence_rejected"]:
        print(f"  rejected: {describe(entry)} ({entry['reason']})")
    return 1 if result["evidence_rejected"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
