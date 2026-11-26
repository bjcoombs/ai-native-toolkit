"""Scale doc staleness to a base doc's own directory (issue #822).

A nearest-ancestor base doc owns every code file below its directory down to
the next base doc, so a root ``AGENTS.md`` can own twenty thousand files. Its
staleness ratio counts subject commits since the doc's last change, and on a
large, busy tree that passes the threshold within days of an edit: the stale
share then measures how busy the repository is, not how far its docs drifted.

The rule (maintainer decision on #822, recorded in the block as ``normalisation``):

  - **Direct children.** A nearest-ancestor base doc is measured against the code
    files directly in its own directory (``direct_children``), not its whole
    subtree. Ownership is unchanged: ``subject_code_count`` and ``subject_paths``
    still describe the whole subtree, and the complexity join still reads it. The
    measured count is ``measured_code_count``. A base doc with no code beside it
    has nothing to measure and leaves ``summary.measured_docs``.
  - **Renames.** A rename, authored after the doc's last content change, of a
    path directly in the doc's directory whose old name the doc's text still
    names, makes the doc stale (``renamed_children_named``, the count of such
    old paths). A rename deeper in the tree does not.
  - **Measured-count floor.** Below ``MIN_MEASURED_DOCS`` measured docs the share
    is a percentage over a handful of docs, so ``summary.stale_pct`` is null with
    ``stale_pct_reason`` ``too_few_measured_docs`` and the doc-freshness condition
    reads not measured with that reason.

Tests: ``tests/test_doc_staleness.py`` (the #822 section).
"""
from __future__ import annotations

import re
import subprocess
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import TypedDict

from lib.history.change_coupling import repo_top
from lib.history.git_churn import GIT_TIMEOUT_SECONDS

DIRECT_CHILDREN = "direct_children"
MIN_MEASURED_DOCS = 20
TOO_FEW_MEASURED_DOCS = "too_few_measured_docs"


class DocStalenessNormalisation(TypedDict):
    """``doc_staleness.normalisation``: the rule that scales staleness (#822)."""

    base_doc_subject: str
    rename_scope: str
    min_measured_docs: int


def normalisation() -> DocStalenessNormalisation:
    """The normalisation rule this module applies, as the block records it."""
    return {"base_doc_subject": DIRECT_CHILDREN, "rename_scope": DIRECT_CHILDREN,
            "min_measured_docs": MIN_MEASURED_DOCS}


def _parent(rel: str) -> str:
    parent = PurePosixPath(rel).parent.as_posix()
    return "" if parent == "." else parent


def direct_children(subject_rels: list[str], base_dir: str) -> list[str]:
    """The subject paths that sit directly in ``base_dir`` (``""`` is the root)."""
    return [r for r in subject_rels if _parent(r) == base_dir]


# One rename: (author epoch, old repo-relative path, new repo-relative path).
RenameEvent = tuple[int, str, str]


@lru_cache(maxsize=4)
def rename_events(repo_root: Path, head: str | None) -> tuple[RenameEvent, ...]:
    """Every rename git detects in the history, keyed per (repo, HEAD).

    Paths are relative to ``repo_root``; a rename outside it is dropped. Empty
    outside a git repository or when the log cannot be read, so a failure never
    makes a doc stale.
    """
    top = repo_top(repo_root)
    if top is None:
        return ()
    cmd = ["git", "-c", "core.quotepath=false", "-C", top, "log", "-M",
           "--diff-filter=R", "--name-status", "--pretty=format:\x1e%at"]
    try:
        raw = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", check=True,
                             timeout=GIT_TIMEOUT_SECONDS).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return ()
    top_path, root = Path(top).resolve(), repo_root.resolve()
    events: list[RenameEvent] = []
    for chunk in raw.split("\x1e"):
        lines = chunk.splitlines()
        if not lines or not lines[0].strip().isdigit():
            continue
        epoch = int(lines[0].strip())
        for line in lines[1:]:
            parts = line.split("\t")
            if len(parts) != 3 or not parts[0].startswith("R"):
                continue
            try:
                old = (top_path / parts[1]).relative_to(root).as_posix()
                new = (top_path / parts[2]).relative_to(root).as_posix()
            except ValueError:
                continue
            events.append((epoch, old, new))
    return tuple(events)


def _names(text: str, rel: str) -> bool:
    """True when ``text`` names ``rel``'s file name as a whole token (bare, or
    as the last part of a path such as ``./helper.py`` or ``src/helper.py``)."""
    name = PurePosixPath(rel).name
    return re.search(rf"(?<![\w.-]){re.escape(name)}(?![\w-])", text) is not None


def renamed_children_named(
    doc: Path, base_dir: str, events: tuple[RenameEvent, ...], after: int | None,
) -> int:
    """Distinct old paths directly in ``base_dir`` renamed after ``after`` (the
    doc's last content change) whose name the doc still carries. 0 when the
    doc's change time is unknown or its text cannot be read."""
    if after is None:
        return 0
    olds = {old for epoch, old, _ in events if epoch > after and _parent(old) == base_dir}
    if not olds:
        return 0
    try:
        text = doc.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    return sum(1 for old in olds if _names(text, old))
