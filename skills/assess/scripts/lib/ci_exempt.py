"""Which commands of a shell statement errexit does not stop on (issue #936).

Under ``set -e`` bash does not exit when the failing command is (bash manual, the
``set`` builtin, ``-e``): part of the condition after ``if``, ``elif``, ``while`` or
``until``; any command of an ``&&`` / ``||`` list except the one after the final
``&&`` / ``||``; any command of a pipeline but the last; or a pipeline inverted with
``!``. With ``pipefail`` on, a pipeline's status is its last failing command's, so the
last pipeline of a list fails the step and only the earlier pipelines are exempt.

``ci_masking`` reads these on a GitHub ``run:`` body with errexit on: a check in an
exempt position runs on past its failure, so a later top-level ``exit 0`` masks it
(``pytest && echo ok; exit 0``, ``pytest | tee log; exit 0`` without pipefail,
``if pytest; then :; fi; exit 0``).

An ``until`` condition is not read as exempt: ``until pytest; do sleep 5; done`` loops
until the check passes, so it never reaches the ``exit 0`` on a failure unless the loop
breaks out (a known limit: a ``break`` in the loop body is not read).
"""
from __future__ import annotations

import re

Statement = list[tuple[str, str]]

# Words that open a condition read with errexit off, and the words that end it.
_CONDITION_OPENERS = frozenset({"if", "elif", "while"})
_CONDITION_CLOSERS = frozenset({"then", "do"})
# ``-o pipefail`` alone or at the end of a flag cluster (``-eo pipefail``).
_PIPEFAIL_FLAG = re.compile(r"(?:^|\s)-[a-zA-Z]*o\s+pipefail(?=\s|$)")


def exempt_segments(statement: Statement, pipefail: bool) -> frozenset[int]:
    """The indices of the segments of one ``;`` statement that run errexit-exempt.

    ``statement`` is ``split_statements`` output: ``(operator before, segment)`` pairs.
    ``&`` starts a new list; ``&&`` / ``||`` a new pipeline; ``|`` continues one.
    """
    out: set[int] = set()
    lists: list[list[list[int]]] = []
    for k, (op, _) in enumerate(statement):
        if not lists or op == "&":
            lists.append([[k]])
        elif op == "|":
            lists[-1][-1].append(k)
        else:
            lists[-1].append([k])
    for pipelines in lists:
        for pipeline in pipelines[:-1]:
            out.update(pipeline)
        last = pipelines[-1]
        if statement[last[0]][1].split()[:1] == ["!"]:
            out.update(last)
        elif not pipefail:
            out.update(last[:-1])
    return frozenset(out)


def condition_flags(statements: list[Statement]) -> list[bool]:
    """Per statement of one script body: is it part of an ``if`` / ``elif`` / ``while``
    condition (from the keyword up to the ``then`` / ``do`` statement)?"""
    out: list[bool] = []
    inside = False
    for statement in statements:
        words = statement[0][1].split() if statement else []
        head = words[0] if words else ""
        if inside and head in _CONDITION_CLOSERS:
            inside = False
        elif not inside and head in _CONDITION_OPENERS:
            inside = True
        out.append(inside)
    return out


def pipefail_after(statement: Statement, current: bool) -> bool:
    """``pipefail`` after a statement: ``set -o pipefail`` / ``set -euo pipefail`` turn it
    on, ``set +o pipefail`` off; any other statement leaves ``current``."""
    for _, seg in statement:
        words = seg.split()
        if words[:1] != ["set"]:
            continue
        for prev, word in zip(words, words[1:]):
            if word == "pipefail" and prev[:1] in ("-", "+") and "o" in prev[1:]:
                current = prev[0] == "-"
    return current


def shell_pipefail(flags: str) -> bool:
    """Does a custom shell command's flag text (``--noprofile --norc -eo pipefail``) set
    ``pipefail``?"""
    return _PIPEFAIL_FLAG.search(flags) is not None


def unquoted(text: str, escapes: bool = True) -> str:
    """``text`` with its quoted spans and backslash-escaped characters removed, so a
    ``{`` or ``<<EOF`` inside ``"..."`` or ``'...'`` is not read as shell grammar.
    With ``escapes`` false (a pwsh, powershell, cmd or python body) a backslash is an
    ordinary character (``"C:\\dir\\"`` closes its quote)."""
    out: list[str] = []
    quote = ""
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\" and quote == '"' and escapes:
                i += 1
            elif ch == quote:
                quote = ""
        elif ch == "\\" and escapes:
            i += 1
        elif ch in "'\"":
            quote = ch
        else:
            out.append(ch)
        i += 1
    return "".join(out)
