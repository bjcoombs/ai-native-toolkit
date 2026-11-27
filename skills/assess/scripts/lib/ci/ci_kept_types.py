"""The shapes doc staleness and the docs-kept-true reader share (issue #852).

``DocsKeptTrueBlock`` is the ``doc_staleness.kept_true`` block and ``KeptCheck`` one of its
``checks``; ``KeptTrueBy`` is the ``doc_staleness.docs[].kept_true_by`` record: the kind and CI file of the
check that keeps a doc's subtree true (``lib.ci.docs_kept_true``). It lives apart from that
module so ``lib.docs.doc_staleness`` can name them without importing the CI reader.
"""
from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

KeptKind = Literal["regen_diff", "verify_pair", "doc_tests"]


class KeptTrueBy(TypedDict):
    """``doc_staleness.docs[].kept_true_by``: the check that keeps a doc's subtree true."""

    kind: KeptKind
    path: str


class KeptCheck(TypedDict):
    """One check that keeps docs or generated files true."""

    path: str          # the CI file that runs it
    kind: KeptKind
    covers: list[str]  # literal paths the check names (subtrees doc staleness leaves alone)


class DocsKeptTrueBlock(TypedDict):
    """``doc_staleness.kept_true`` (module docstring)."""

    files_read: int
    regen_diff: int
    verify_pair: int
    doc_tests: int
    present: bool | None
    present_reason: NotRequired[str]
    checks: list[KeptCheck]
