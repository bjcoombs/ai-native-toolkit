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
    old paths). A rename deeper in the tree does not, nor does a rename of the
    doc itself or of a path that exists again at HEAD. The name match is by file
    name, so a generic name (``__init__.py``) mentioned for another directory
    also matches. The renames come from the rename map's walk
    (``RenameMap.events``); when that history cannot be read there are none,
    and every row already carries confidence ``low``.
  - **Measured-count floor.** Below ``MIN_MEASURED_DOCS`` measured docs the share
    is a percentage over a handful of docs, so ``summary.stale_pct`` is null with
    ``stale_pct_reason`` ``too_few_measured_docs`` and the doc-freshness condition
    reads not measured with that reason.

Tests: ``tests/test_doc_staleness.py`` (the #822 section).
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any, TypedDict

from lib.docs.doc_ownership import NEAREST_ANCESTOR
from lib.history.change_coupling import repo_top

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


def nothing_beside(row: Mapping[str, Any]) -> bool:
    """A base doc with no code beside it, no renamed child it names and no source
    verdict: nothing to measure it against, so it leaves ``measured_docs`` and the
    doc-drift map. A row from before #822 (no ``measured_code_count``) is measured."""
    provenance = row.get("provenance")
    verdict = provenance.get("source_newer") if isinstance(provenance, Mapping) else None
    return (row.get("subject_method") == NEAREST_ANCESTOR
            and row.get("measured_code_count") == 0
            and not row.get("renamed_children_named") and verdict is None)


def _parent(rel: str) -> str:
    parent = PurePosixPath(rel).parent.as_posix()
    return "" if parent == "." else parent


def direct_children(subject_rels: list[str], base_dir: str) -> list[str]:
    """The subject paths that sit directly in ``base_dir`` (``""`` is the root)."""
    return [r for r in subject_rels if _parent(r) == base_dir]


# One rename: (author epoch, old repo-relative path, new repo-relative path).
RenameEvent = tuple[int, str, str]


def rename_events(
    repo_root: Path, events: tuple[RenameEvent, ...],
) -> tuple[RenameEvent, ...]:
    """The rename map's events (``RenameMap.events``, git-top relative) as paths
    relative to ``repo_root``, dropping a rename outside it. The walk is the one
    the content clock already ran, so no second history read. An unreadable
    history gives no events, and the rows then carry confidence ``low``."""
    top = repo_top(repo_root) if events else None
    if top is None:
        return ()
    top_path, root = Path(top).resolve(), repo_root.resolve()
    out: list[RenameEvent] = []
    for epoch, old, new in events:
        try:
            out.append((epoch, (top_path / old).relative_to(root).as_posix(),
                        (top_path / new).relative_to(root).as_posix()))
        except ValueError:
            continue
    return tuple(out)


def _names(text: str, rel: str) -> bool:
    """True when ``text`` names ``rel``'s file name as a whole token (bare, or
    as the last part of a path such as ``./helper.py`` or ``src/helper.py``)."""
    name = PurePosixPath(rel).name
    return re.search(rf"(?<![\w.-]){re.escape(name)}(?![\w-])", text) is not None


def _former_names(doc_rel: str, events: tuple[RenameEvent, ...]) -> set[str]:
    """Every path the doc itself had, following its renames back from ``doc_rel``."""
    names = {doc_rel}
    while True:
        more = {old for _, old, new in events if new in names} - names
        if not more:
            return names
        names |= more


def renamed_children_named(
    doc: Path, doc_rel: str, base_dir: str, events: tuple[RenameEvent, ...],
    after: int | None, repo_root: Path,
) -> int:
    """Distinct old paths directly in ``base_dir`` renamed after ``after`` (the
    doc's last content change) whose name the doc still carries. The doc's own
    former names do not count (a rename is not a content change, so a doc that
    mentions its old name would read stale for good), nor does an old path that
    exists again at HEAD (the doc names a real file). 0 when the doc's change
    time is unknown or its text cannot be read."""
    if after is None:
        return 0
    own = _former_names(doc_rel, events)
    olds = {old for epoch, old, _ in events if epoch > after and _parent(old) == base_dir
            and old not in own and not (repo_root / old).exists()}
    if not olds:
        return 0
    try:
        text = doc.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    return sum(1 for old in olds if _names(text, old))
