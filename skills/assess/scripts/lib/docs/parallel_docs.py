"""The ``parallel-docs-tree`` ownership match: which code a ``docs/`` page names (#817).

A doc under a ``docs/`` tree that no base doc owns describes the code named
like it. ``ParallelIndex`` indexes the discovered code once per run and
``ParallelIndex.match`` picks one root for a doc by this stated rule:

1. **Production code only.** A candidate is a code file or the directory that
   directly holds one, and only when ``lib.path_classes.path_class`` does not
   read it as ``test`` or ``fixture`` (``tests/models/``,
   ``tests/fixtures/models/``). A doc whose name matches only test code has no
   parallel match, so it falls through to its explicit links or the baseline.
2. **Stem before parent name.** The doc's stem (``cohere`` for
   ``docs/api/models/cohere.md``) matches a module file of that stem
   (``pkg/models/cohere.py``) or a directory of that name. Only when nothing
   matches the stem does the doc's parent directory name (``models``) match a
   directory. A base-doc name (``index.md``, ``README.md``) has a generic stem,
   so it matches by its parent name alone (``docs/payments/index.md`` ->
   ``payments``).
3. **Longest shared path suffix, then shallowest, then path order.** Among
   candidates of the same key, the one sharing the longest path suffix with the
   doc wins (``docs/api/models/cohere.md`` shares ``models/cohere`` with
   ``pkg/models/cohere.py`` and only ``cohere`` with
   ``pkg/providers/cohere.py``). A file's suffix compares its stem.

The match records which key and which tie-break decided it as one token,
``<key>:<rule>``: key ``stem`` or ``parent-name``, rule ``only-match``,
``longest-suffix``, ``shallowest`` or ``path-order``. It rides on the
``doc_staleness.docs`` row as ``subject_match``. Tests:
``tests/test_parallel_docs.py``.
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import NamedTuple

from lib.path_classes import path_class

# Classes a doc never maps to: test code and the data it reads.
NON_SUBJECT_CLASSES = frozenset({"test", "fixture"})
# Doc stems that name the directory they sit in, not a module.
GENERIC_DOC_STEMS = frozenset({"readme", "index", "_index"})

STEM = "stem"
PARENT_NAME = "parent-name"
ONLY_MATCH = "only-match"
LONGEST_SUFFIX = "longest-suffix"
SHALLOWEST = "shallowest"
PATH_ORDER = "path-order"


class ParallelMatch(NamedTuple):
    """The matched root (repo-relative POSIX) and its ``<key>:<rule>`` token."""

    root: str
    match: str


class _Candidate(NamedTuple):
    rel: str
    parts: tuple[str, ...]  # lowercased; a file's last part is its stem


def _suffix_len(a: tuple[str, ...], b: tuple[str, ...]) -> int:
    n = 0
    while n < min(len(a), len(b)) and a[-1 - n] == b[-1 - n]:
        n += 1
    return n


class ParallelIndex:
    """Production code files and their directories, by lowercased name."""

    def __init__(self, code_rels: Iterable[str]) -> None:
        self._files: dict[str, list[_Candidate]] = {}
        self._dirs: dict[str, list[_Candidate]] = {}
        seen_dirs: set[str] = set()
        for rel in sorted(set(code_rels)):
            if path_class(rel) in NON_SUBJECT_CLASSES:
                continue
            path = PurePosixPath(rel)
            parts = tuple(p.lower() for p in path.parts)
            stem = path.stem.lower()
            self._files.setdefault(stem, []).append(_Candidate(rel, (*parts[:-1], stem)))
            parent = path.parent.as_posix()
            if parent != "." and parent not in seen_dirs:
                seen_dirs.add(parent)
                self._dirs.setdefault(parts[-2], []).append(_Candidate(parent, parts[:-1]))

    def match(self, doc_rel: str) -> ParallelMatch | None:
        """The root a ``docs/`` page maps to by the module docstring's rule, or None."""
        doc = PurePosixPath(doc_rel)
        dirs = tuple(p.lower() for p in doc.parts[:-1])
        if "docs" not in dirs:
            return None
        stem = doc.stem.lower()
        if stem not in GENERIC_DOC_STEMS:
            found = self._pick(STEM, (*dirs, stem),
                               self._files.get(stem, []) + self._dirs.get(stem, []))
            if found is not None:
                return found
        if not dirs:
            return None
        return self._pick(PARENT_NAME, dirs, self._dirs.get(dirs[-1], []))

    @staticmethod
    def _pick(key: str, doc_parts: tuple[str, ...],
              candidates: list[_Candidate]) -> ParallelMatch | None:
        if not candidates:
            return None
        ranked = sorted(candidates, key=lambda c: (
            -_suffix_len(doc_parts, c.parts), len(c.parts), c.rel))
        best = ranked[0]
        if len(ranked) == 1:
            rule = ONLY_MATCH
        else:
            runner = ranked[1]
            if _suffix_len(doc_parts, best.parts) > _suffix_len(doc_parts, runner.parts):
                rule = LONGEST_SUFFIX
            elif len(best.parts) < len(runner.parts):
                rule = SHALLOWEST
            else:
                rule = PATH_ORDER
        return ParallelMatch(best.rel, f"{key}:{rule}")


def parallel_index(code_files: Iterable[Path], repo_root: Path) -> ParallelIndex:
    """Index the discovered code files under ``repo_root`` (outside paths are skipped)."""
    rels: list[str] = []
    for c in code_files:
        try:
            rels.append(c.relative_to(repo_root).as_posix())
        except ValueError:
            continue
    return ParallelIndex(rels)
