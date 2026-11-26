"""The paths a commit changed in content, read from ``git log --raw``.

A commit that only renames a file (``git mv`` with no edit) moves no content,
so it is not churn and not a content change (issue #425, contract I425-2): with
rename detection off (``--no-renames``, so the result does not depend on the
reader's ``diff.renames``) git lists it as a delete of the old name and an add
of the new one carrying the same blob. Once both names fold to the file's
current name through the rename map, that pair is dropped; a rename that also
edits the file changes the blob and still counts.

Pure stdlib; imports no orchestrator.

The paths one commit changed in content, from `git log --raw --no-abbrev --no-renames`
plus `--full-diff` (`RAW_LOG_ARGS`), folded to current names through the rename map.
`--full-diff` lets a rename from outside a pathspec (a scope, a sub-directory root, the
clock's doc globs) pair up; `within`, the walk's pathspec as a predicate, keeps the
counted paths to those the commit touched inside it. A delete and an add of
the same blob under two names that fold together is a pure rename and is dropped; a
rename that also edits changes the blob and counts. `git_churn_scores`,
`commit_epochs_by_file` and `content_commit_clock` all read it. Tests:
`tests/test_content_paths.py`, `tests/test_rename_churn.py`.
Shallow history (issue #544): `content_commit_clock` reuses its
`--is-shallow-repository` probe, and on a shallow clone reads the boundary commits
from `git rev-parse --git-path shallow`. The clock's `truncated`, `boundary_epoch`
(newest boundary, author time) and `lower_bound` (docs whose newest visible change is
a boundary commit) feed `doc_staleness`. Only boundaries reachable from HEAD count
(issue #633): `$GIT_DIR/shallow` also lists the boundaries of other shallow-fetched
refs, so a boundary outside the clock's walk is kept only when
`merge-base --is-ancestor` confirms it (or cannot say). A boundary commit lists every file like a
root commit, so it is never counted as bulk. A failed probe is unknown, not
truncated. Tests: `tests/test_shallow_history.py`.
`window_from_churn_label` turns a `pick_churn_window` label (`"commits (last 12mo)"`)
into the `history_window` scan input (`"12mo"`, None for all-time).
Commit-anchored windows (issue #773): `CHURN_WINDOWS` holds window tokens (`12mo`,
`24mo`, `5y`, None), and `git_churn_scores`, `commit_epochs_by_file` and
`pick_churn_window` take `window` plus an optional `anchor_epoch` (HEAD's committer time
when omitted, read through `commit_anchor`), so each walk passes an absolute
`--since=@<epoch>`. `ContentClock.anchor_epoch` is HEAD's committer time, read inside
`content_commit_clock` before any early return, and `ContentClock.days` counts to it,
so `last_commit_days` and `freshness_days` are a function of the commit. No HEAD gives
an empty churn map, a None epoch map and None days: never a fallback to today.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping

# The ``git log`` arguments whose per-file lines :func:`content_paths` reads.
# ``--full-diff``: under a pathspec (a scope, a sub-directory root, the doc
# globs) git otherwise lists only the matching side of a rename, so a file moved
# in from outside the pathspec reads as an add and counts. Callers filter the
# listed paths to their own scope.
RAW_LOG_ARGS: tuple[str, ...] = ("--raw", "--no-abbrev", "--no-renames", "--full-diff")


def content_paths(lines: Iterable[str],
                  renames: Mapping[str, str] | None = None,
                  within: Callable[[str], bool] | None = None) -> set[str]:
    """Current-name paths (relative to the git top) one commit changed in content.

    ``lines`` are the commit's ``--raw`` lines (``:<mode> <mode> <blob> <blob>
    <status>\\t<path>``); anything else is ignored. ``renames`` maps historical
    paths to current ones. A path whose only entries are a delete and an add of
    one blob under two names that fold together is a pure rename and is left
    out. ``within`` is the caller's pathspec as a predicate on the listed
    path: every line still pairs a rename, but a path is returned only when
    the commit touched it under a name inside the pathspec, as the walk saw it
    before ``--full-diff``.
    """
    entries: dict[str, list[tuple[str, str]]] = {}
    inside: set[str] = set()
    for line in lines:
        meta, tab, path = line.strip().partition("\t")
        fields = meta.split()
        if not tab or not path or len(fields) != 5 or not meta.startswith(":"):
            continue
        status = fields[4][:1]
        blob = fields[2] if status == "D" else fields[3]
        current = renames.get(path, path) if renames else path
        entries.setdefault(current, []).append((status, blob))
        if within is None or within(path):
            inside.add(current)
    return {p for p, e in entries.items() if p in inside and not _pure_rename(e)}


def _pure_rename(entries: list[tuple[str, str]]) -> bool:
    """A delete and an add of the same blob: the content moved, unchanged."""
    if len(entries) != 2:
        return False
    (s1, b1), (s2, b2) = entries
    return {s1, s2} == {"A", "D"} and b1 == b2
