"""Token regions of a Python source: where its strings, docstrings and comments sit.

``promissory_markers`` asks this module whether a marker hit in a ``.py`` file
is comment text or data. A line-local heuristic cannot tell a string literal
holding marker text (a scanner's own test fixture) from a comment carrying the
same text; ``tokenize`` can, because it emits COMMENT and STRING tokens with
exact positions.

A string literal that is a statement of its own (a module, class or function
docstring, or a bare string statement) is classed ``docstring``: it is prose
about the code. Any other string literal, f-strings and t-strings included, is
``string``. A source that does not tokenize yields ``None`` so the caller can
fall back to its line-based filters; nothing here raises on bad input.

Pure stdlib. No LLM calls.
"""

from __future__ import annotations

import io
import tokenize
from dataclasses import dataclass
from pathlib import Path

# A region is one of these: what a column on a line is part of.
COMMENT, DOCSTRING, STRING, CODE = "comment", "docstring", "string", "code"

Span = tuple[tuple[int, int], tuple[int, int], str]

# f-strings tokenize as START / MIDDLE / END from Python 3.12 and t-strings from
# 3.14; before that each is one STRING token. Built from what this interpreter
# has, so the same code runs on 3.11 through 3.14.
_FSTRING_START = {
    getattr(tokenize, n) for n in ("FSTRING_START", "TSTRING_START")
    if hasattr(tokenize, n)
}
_FSTRING_END = {
    getattr(tokenize, n) for n in ("FSTRING_END", "TSTRING_END")
    if hasattr(tokenize, n)
}
_STATEMENT_START = {tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT}
_STATEMENT_END = {tokenize.NEWLINE, tokenize.ENDMARKER}


@dataclass
class PyRegions:
    """Where the string literals and comments of one Python source sit.

    ``spans`` holds each string literal as (start, end, kind) in tokenize's
    (row, col) coordinates, end exclusive, rows 1-based. ``comments`` maps a
    row to the column its comment opens at.
    """

    spans: list[Span]
    comments: dict[int, int]

    def region(self, row: int, col: int) -> str:
        """The region the character at (row, col) belongs to."""
        opens = self.comments.get(row)
        if opens is not None and col >= opens:
            return COMMENT
        for start, end, kind in self.spans:
            if start <= (row, col) < end:
                return kind
        return CODE


def _string_kind(sig: list[tokenize.TokenInfo], first: int, last: int) -> str:
    """``docstring`` when tokens first..last form a statement of their own."""
    before = sig[first - 1].type if first > 0 else tokenize.NEWLINE
    after = sig[last + 1].type if last + 1 < len(sig) else tokenize.ENDMARKER
    if before in _STATEMENT_START and after in _STATEMENT_END:
        return DOCSTRING
    return STRING


def _literal_runs(sig: list[tokenize.TokenInfo]) -> list[list[int]]:
    """Each string literal as a [first, last] index run into ``sig``.

    An f-string or t-string spans its START..END tokens. Adjacent literals
    (implicit concatenation) merge into one run, so two triple-quoted literals
    alone on a line are one docstring, as Python reads them.
    """
    runs: list[list[int]] = []
    depth, first = 0, 0
    for i, t in enumerate(sig):
        if t.type in _FSTRING_START:
            first = i if depth == 0 else first
            depth += 1
            continue
        if t.type in _FSTRING_END:
            depth -= 1
            if depth:
                continue
        elif depth or t.type != tokenize.STRING:
            continue
        else:
            first = i
        if runs and runs[-1][1] == first - 1:
            runs[-1][1] = i
        else:
            runs.append([first, i])
    return runs


def python_regions(source: str) -> PyRegions | None:
    """Tokenize a Python source; None when it does not tokenize."""
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, SyntaxError):
        return None
    comments = {t.start[0]: t.start[1] for t in toks if t.type == tokenize.COMMENT}
    # Comments and blank-line NL tokens sit outside the statement structure, so
    # skipping them lets a docstring with a trailing comment still read as one.
    sig = [t for t in toks if t.type not in (tokenize.NL, tokenize.COMMENT)]
    runs = _literal_runs(sig)
    spans: list[Span] = [
        (sig[a].start, sig[b].end, _string_kind(sig, a, b)) for a, b in runs
    ]
    return PyRegions(spans, comments)


def load_regions(
    repo_root: Path, path: str, cache: dict[str, PyRegions | None]
) -> PyRegions | None:
    """Per-file memo of ``python_regions``; None for an unreadable file.

    ``utf-8-sig`` drops a BOM, as rg does, so columns on row 1 line up.
    """
    if path not in cache:
        try:
            source = (repo_root / path).read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            cache[path] = None
        else:
            cache[path] = python_regions(source)
    return cache[path]
