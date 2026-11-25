"""Read the command inside a compound-command lead on one CI line (issue #1061).

``lib.command_resolver.extract_commands`` splits a line into segments and reads each segment's
first word as its program, so a segment that opens a compound command was never a check:
``if ! pytest`` reads ``if``, ``{ pytest`` reads ``{``, ``f() { pytest`` reads ``f()`` and
``(pytest)`` reads the single word ``(pytest)``. ``compound_core(text)`` rewrites one CI
line's text so each segment starts at the command it runs, and ``lib.ci.ci_lint_gates.line_commands``
(the per-line reader ``lib.ci.ci_lines`` and ``lib.ci.ci_checks`` share) reads the rewrite:

- the keywords ``if``, ``elif``, ``while``, ``until``, ``then``, ``do`` and ``else`` are dropped,
  and a ``!`` right after a condition keyword (``if ! pytest``);
- a ``{`` group opener and a function definition's lead (``f() {``, ``function f {``);
- one enclosing ``( )`` subshell around the whole segment (``(pytest)``, ``( ruff check . )``).
  A group split over segments (``(cd sub && pytest)``) is already read by ``extract_commands``.

Whether the command's failure reaches the step is not decided here: ``lib.ci_masking`` reads
the same statements first, so ``if pytest; then echo ok; fi`` reads null (``check_may_pass``)
and ``if ! pytest; then exit 1; fi`` stays credited. A ``!`` that is not after a condition
keyword is kept (``! pytest`` inverts the status, so the step passes on a failing check), and
a function whose body no statement of its ``run:`` body calls is masked there
(``lib.ci_unsure.uncalled_functions``). Instruction files are not read this way: only the CI
reader calls ``compound_core``.

Inward-only imports: stdlib and ``lib.ci_statements``. Tests: ``tests/test_ci_line_reader.py``.
"""
from __future__ import annotations

import re

from lib.ci_statements import _join, split_statements

_KEYWORDS = frozenset({"if", "elif", "while", "until", "then", "do", "else"})
_CONDITIONS = frozenset({"if", "elif", "while", "until"})
# ``f() {``, ``f () {``, ``function f {``, ``function f() {`` at the start of a segment.
_FUNCTION_LEAD = re.compile(r"^(?:function\s+[\w.-]+(?:\s*\(\s*\))?|[\w.-]+\s*\(\s*\))\s*\{(?=\s|$)")


def _enclosing_parens(text: str) -> bool:
    """``text`` is one ``( )`` group: the ``(`` that opens it closes at its last character."""
    if not text.startswith("(") or not text.endswith(")"):
        return False
    depth, quote = 0, ""
    for i, ch in enumerate(text):
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "'\"`":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i == len(text) - 1
    return False


def segment_core(segment: str) -> str:
    """One segment with its compound-command lead dropped (module docstring)."""
    text = segment.strip()
    while text:
        head, _, rest = text.partition(" ")
        if head in _KEYWORDS:
            rest = rest.lstrip()
            if head in _CONDITIONS and rest.startswith("! "):
                rest = rest[2:].lstrip()
            text = rest
        elif head == "{":
            text = rest.lstrip()
        elif (lead := _FUNCTION_LEAD.match(text)) is not None:
            text = text[lead.end():].lstrip()
        elif _enclosing_parens(text):
            text = text[1:-1].strip()
        else:
            break
    return text


def compound_core(text: str) -> str:
    """One CI line's text with each segment's compound-command lead dropped."""
    statements = []
    for statement in split_statements(text):
        kept = [(op, core) for op, seg in statement if (core := segment_core(seg))]
        if kept:
            statements.append(_join([("", kept[0][1]), *kept[1:]]))
    return "; ".join(statements)
