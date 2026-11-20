"""The paths a commit changed in content, read from ``git log --raw``.

A commit that only renames a file (``git mv`` with no edit) moves no content,
so it is not churn and not a content change (issue #425, contract I425-2): with
rename detection off (``--no-renames``, so the result does not depend on the
reader's ``diff.renames``) git lists it as a delete of the old name and an add
of the new one carrying the same blob. Once both names fold to the file's
current name through the rename map, that pair is dropped; a rename that also
edits the file changes the blob and still counts.

Pure stdlib; imports no orchestrator.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping

# The ``git log`` arguments whose per-file lines :func:`content_paths` reads.
RAW_LOG_ARGS: tuple[str, ...] = ("--raw", "--no-abbrev", "--no-renames")


def content_paths(lines: Iterable[str],
                  renames: Mapping[str, str] | None = None) -> set[str]:
    """Current-name paths (relative to the git top) one commit changed in content.

    ``lines`` are the commit's ``--raw`` lines (``:<mode> <mode> <blob> <blob>
    <status>\\t<path>``); anything else is ignored. ``renames`` maps historical
    paths to current ones. A path whose only entries are a delete and an add of
    one blob under two names that fold together is a pure rename and is left
    out.
    """
    entries: dict[str, list[tuple[str, str]]] = {}
    for line in lines:
        meta, tab, path = line.strip().partition("\t")
        fields = meta.split()
        if not tab or not path or len(fields) != 5 or not meta.startswith(":"):
            continue
        status = fields[4][:1]
        blob = fields[2] if status == "D" else fields[3]
        current = renames.get(path, path) if renames else path
        entries.setdefault(current, []).append((status, blob))
    return {p for p, e in entries.items() if not _pure_rename(e)}


def _pure_rename(entries: list[tuple[str, str]]) -> bool:
    """A delete and an add of the same blob: the content moved, unchanged."""
    if len(entries) != 2:
        return False
    (s1, b1), (s2, b2) = entries
    return {s1, s2} == {"A", "D"} and b1 == b2
