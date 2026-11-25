"""Exact and near-exact duplicate issues for the refinement script (issue #1320).

Pairs are compared when at least one side is open: open with open, and open
with each closed issue inside the window the caller fetched (90 days).

- **Normalised** text is lower-cased, Markdown punctuation replaced by spaces,
  and whitespace collapsed.
- **Exact**: normalised title and normalised body both equal.
- **Near**: normalised titles equal with different bodies (basis `title`), or
  body word-shingle Jaccard similarity at least `NEAR_BODY_SIMILARITY` (basis
  `body`). Near pairs are for review and are never closable.

The **survivor** is the issue created first, unless that issue was closed as
`not_planned` or `duplicate`, in which case the other one survives. An exact
pair is **closable** when both issues are open: the refiner may close only
exact duplicates, linking the survivor (#1317). An open issue that repeats a
closed one is reported but not closable: it may be a regression of a fixed
issue, which a reader should decide.

Python 3.8 compatible and stdlib only.
"""
from __future__ import annotations

import re
from typing import Dict, FrozenSet, List, Optional, Tuple

from refine_types import DuplicatePair, Issue

# Word 3-shingle Jaccard similarity at or above which two bodies are near
# duplicates. Issues here share the six template headings, so a lower bar
# would pair unrelated issues that only share their skeleton.
NEAR_BODY_SIMILARITY = 0.85
SHINGLE_WORDS = 3
# Bodies shorter than this many words are too short to compare by shingles.
MIN_BODY_WORDS = 20

_MARKDOWN_PUNCT = re.compile(r"[`*_#>~\[\]()!|=+\-:]")
_WS = re.compile(r"\s+")
_LOSING_REASONS = ("not_planned", "duplicate")


def normalise(text: str) -> str:
    """Lower-case, Markdown punctuation stripped, whitespace collapsed."""
    return _WS.sub(" ", _MARKDOWN_PUNCT.sub(" ", text.lower())).strip()


def shingles(normalised: str) -> FrozenSet[Tuple[str, ...]]:
    words = normalised.split()
    if len(words) < MIN_BODY_WORDS:
        return frozenset()
    return frozenset(tuple(words[i:i + SHINGLE_WORDS]) for i in range(len(words) - SHINGLE_WORDS + 1))


def jaccard(a: FrozenSet[Tuple[str, ...]], b: FrozenSet[Tuple[str, ...]]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def survivor(a: Issue, b: Issue) -> Tuple[Issue, Issue]:
    """(kept, duplicate) for a pair."""
    first, second = sorted((a, b), key=lambda i: (i["created_at"], i["number"]))
    if first["state"] == "closed" and first["state_reason"] in _LOSING_REASONS:
        return second, first
    return first, second


class _Prepared:
    __slots__ = ("issue", "title", "body", "grams")

    def __init__(self, issue: Issue) -> None:
        self.issue = issue
        self.title = normalise(issue["title"])
        self.body = normalise(issue["body"])
        self.grams = shingles(self.body)


def _match(a: _Prepared, b: _Prepared) -> Optional[Tuple[str, str, float]]:
    """(kind, basis, similarity) when the pair is a duplicate, else None."""
    if a.title == b.title and a.body == b.body:
        return "exact", "title_and_body", 1.0
    similarity = round(jaccard(a.grams, b.grams), 3)
    if a.title == b.title and a.title:
        return "near", "title", similarity
    if similarity >= NEAR_BODY_SIMILARITY:
        return "near", "body", similarity
    return None


def find_duplicates(open_issues: List[Issue], closed: List[Issue]) -> List[DuplicatePair]:
    """Every duplicate pair with at least one open side, sorted by (duplicate, kept)."""
    opened = [_Prepared(i) for i in sorted(open_issues, key=lambda i: i["number"])]
    others = [_Prepared(i) for i in sorted(closed, key=lambda i: i["number"])]
    pairs: List[DuplicatePair] = []
    for idx, a in enumerate(opened):
        for b in opened[idx + 1:] + others:
            found = _match(a, b)
            if found is None:
                continue
            kind, basis, similarity = found
            kept, dup = survivor(a.issue, b.issue)
            pairs.append(DuplicatePair(
                kept=kept["number"], duplicate=dup["number"], kind=kind, basis=basis,
                similarity=similarity, closable=kind == "exact" and dup["state"] == "open" and kept["state"] == "open",
            ))
    return sorted(pairs, key=lambda p: (p["duplicate"], p["kept"]))


def by_issue(pairs: List[DuplicatePair]) -> Dict[int, List[DuplicatePair]]:
    """Each pair under both of its issue numbers."""
    index: Dict[int, List[DuplicatePair]] = {}
    for pair in pairs:
        index.setdefault(pair["kept"], []).append(pair)
        index.setdefault(pair["duplicate"], []).append(pair)
    return index
