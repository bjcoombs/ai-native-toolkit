"""Join a CI line continued with ``\\`` onto its first line (issue #799).

``lib.ci.ci_checks`` classifies each CI line on its own, so a command split over lines
(``mvn ${MVN_ARGS} -T8 \\`` / ``-DskipTests \\`` / ``verify``) used to be read from its first
line only, and the goal on the last line was never seen. ``join_continued(text)`` returns the
text with each such command on its first physical line and the lines it continued onto left
blank, so every line keeps its index: the masking (``lib.ci.ci_masking``), trigger
(``lib.ci.ci_triggers``) and count readers still line up. The join is idempotent.

A line continues when it ends in an odd run of backslashes (``\\\\`` is a literal backslash)
and is not a comment. It takes the next line only when that line is not blank and sits at
the first line's indent or deeper: a shallower line is the next YAML key, never shell. When the
first line is a ``key: value \\`` plain scalar, the next line must sit deeper than the key, so a
sibling ``continue-on-error: true`` or ``env:`` is never swallowed.

YAML folds two scalar forms into one line, and a command split over them is one command
(#893: a GitLab ``- >`` / ``ruff`` / ``--version`` item runs ``ruff --version``, no lint).
``join_folded(text)``, which ``join_continued`` applies after the backslash joins, puts each
on its first physical line and blanks the rest:

- a folded block scalar (``run: >``, ``- >-``): adjacent non-blank lines at the block's own
  indentation, as ``lib.ci.ci_masking`` folds them; a blank or more-indented line keeps its
  break. A literal block (``run: |``) keeps every line: each is its own shell line.
- a plain multi-line scalar (``- ruff`` then ``--version`` deeper, ``run: ruff`` then a line
  deeper than ``run``): continuation lines sit deeper than the item's dash or the key, and
  stop at a blank line, a comment, a list item or a ``key:`` line. A value that opens with a
  quote, a flow bracket, an alias, an anchor or a tag, or holds a comment, is not joined.

Lines inside a block scalar are never read as YAML keys, so a heredoc in ``run: |`` is left
alone.

Inward-only imports: stdlib only. Tests: ``tests/test_ci_continuations.py``.
"""
from __future__ import annotations

import re

# A ``key:`` lead (``run: mvn \\``, ``- run: mvn \\``): its plain scalar continues only deeper.
_KEY_LEAD = re.compile(r"^(\s*(?:-\s+)?)[\w.-]+\s*:\s")
# One YAML line: indent, an optional ``- `` and ``key:``, and the value after them.
_LINE = re.compile(r"^( *)(-(?: +|$))?(?:([\w.-]+) *:(?: +|$))?(.*)$")
_BLOCK = re.compile(r"^[|>][-+0-9]*$")
_NOT_PLAIN = tuple("\"'{[&*!|>%@`#")

def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _continues(line: str) -> bool:
    """Ends in an odd run of backslashes and is not a comment line."""
    body = line.rstrip()
    if body.lstrip().startswith("#"):
        return False
    run = len(body) - len(body.rstrip("\\"))
    return run % 2 == 1


def join_continued(text: str) -> str:
    """``text`` with each ``\\``-continued command on its first line (module docstring)."""
    lines = text.splitlines()
    out = list(lines)
    i = 0
    while i < len(lines):
        if not _continues(lines[i]):
            i += 1
            continue
        joined, depth, j = lines[i].rstrip(), _indent(lines[i]), i + 1
        lead = _KEY_LEAD.match(lines[i])
        if lead:
            depth = len(lead.group(1)) + 1   # a sibling key at the key's column is not shell
        while _continues(joined) and j < len(lines) and lines[j].strip() and _indent(lines[j]) >= depth:
            joined = joined[:-1].rstrip() + " " + lines[j].strip()
            out[j] = ""
            j += 1
        out[i] = joined
        i = j
    return join_folded("\n".join(out) + ("\n" if text.endswith("\n") else ""))


def _structural(line: str) -> bool:
    """A list item or a ``key:`` line, which ends a plain scalar's continuation."""
    stripped = line.strip()
    return stripped == "-" or stripped.startswith("- ") or _KEY_LEAD.match(line + " ") is not None


def _fold_block(lines: list[str], out: list[str], body: range) -> None:
    """Fold a ``>`` block's adjacent non-blank lines at its base indentation. A ``#`` line
    there is content, not a YAML comment: ``pytest`` / ``# note`` / ``ruff check .`` folds to
    ``pytest # note ruff check .``, whose shell runs pytest alone."""
    base = next((_indent(lines[j]) for j in body if lines[j].strip()), -1)
    first = -1
    for j in body:
        if lines[j].strip() and _indent(lines[j]) == base:
            if first < 0:
                first = j
            else:
                out[first] = out[first].rstrip() + " " + lines[j].strip()
                out[j] = ""
        else:
            first = -1


def join_folded(text: str) -> str:
    """``text`` with each folded or plain multi-line scalar on its first line (module docstring)."""
    lines = text.splitlines()
    out = list(lines)
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        match = _LINE.match(lines[i])
        if not stripped or stripped.startswith("#") or match is None or not (match.group(2) or match.group(3)):
            i += 1
            continue
        indent, dash, key, value = len(match.group(1)), match.group(2), match.group(3), match.group(4)
        column = indent + len(dash or "") if key else indent   # deeper than this continues
        head = value.split(" #", 1)[0].strip()
        j = i + 1
        if _BLOCK.match(head):
            while j < len(lines) and (not lines[j].strip() or _indent(lines[j]) > column):
                j += 1
            if head.startswith(">"):
                _fold_block(lines, out, range(i + 1, j))
        elif head and not head.startswith(_NOT_PLAIN) and head == value.strip():
            while (j < len(lines) and lines[j].strip() and _indent(lines[j]) > column
                   and not lines[j].strip().startswith("#") and not _structural(lines[j])):
                out[i] = out[i].rstrip() + " " + lines[j].strip()
                out[j] = ""
                j += 1
        i = max(j, i + 1)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")
