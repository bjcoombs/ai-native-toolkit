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
breaks out (``lib.ci.ci_unsure.until_break_flags`` reads that loop as not measured, #1008).

Which segments of one shell statement errexit does not stop on (issue #936), read by
`ci_masking` on a GitHub `run:` body with errexit on, per the bash manual's `set -e`
text. `exempt_segments(statement, pipefail)` takes `split_statements` output and returns
the indices of the segments in every pipeline before the final `&&` / `||`, of a `!`
pipeline, and, with pipefail off, of the last pipeline but its last command;
`condition_flags(statements)` marks the statements of an `if` / `elif` / `while`
condition, up to its `then` / `do` (an `until` condition loops until the check passes,
so it is not read as exempt); `pipefail_after` reads `set -o pipefail` / `set -euo
pipefail` / `set +o pipefail`; `shell_pipefail` reads a custom shell's flags;
`piped_members(statement)` is every pipeline member but the last, and `piped_groups(statements)`
every segment of a `{ }` / `( )` group piped on (#1061: with pipefail off their status is lost,
so `ci_masking` masks them); `unquoted(text)` drops quoted spans and escapes, so `_depths` and `_not_code` read no
`{` or `<<EOF` inside quotes. Tests: `tests/test_ci_false_credit.py`.
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


def piped_members(statement: Statement) -> frozenset[int]:
    """The segments of one statement piped into a later command (``pytest`` in ``pytest | tee
    log``): with ``pipefail`` off a pipeline's status is its last command's, so theirs is lost
    (#1061)."""
    return frozenset(k for k in range(len(statement) - 1) if statement[k + 1][0] == "|")


# ``$(...)`` and ``${...}`` open no group (one level is enough for a CI line).
_SUBST = re.compile(r"\$\([^()]*\)|\$\{[^{}]*\}")


def _group_marks(segment: str) -> tuple[int, int]:
    """``(opens, closes)``: the ``( )`` / ``{ }`` group delimiters a segment holds."""
    bare = _SUBST.sub("", unquoted(segment))
    words = bare.replace("(", " ( ").replace(")", " ) ").split()
    return (sum(w in ("(", "{") for w in words), sum(w in (")", "}") for w in words))


def piped_groups(statements: list[Statement]) -> dict[int, frozenset[int]]:
    """Per statement index, the segments inside a ``{ }`` / ``( )`` group whose closer is piped
    into a later command (``{ pytest; } | tee log``, ``(pytest; echo x) | tee log``): the group
    runs in a pipeline subshell, so with pipefail off its status is lost (#1061)."""
    out: dict[int, set[int]] = {}
    stack: list[tuple[int, int]] = []
    order = [(i, k) for i, statement in enumerate(statements) for k in range(len(statement))]
    for pos, (i, k) in enumerate(order):
        opens, closes = _group_marks(statements[i][k][1])
        stack.extend([(pos, pos)] * opens)
        for _ in range(closes):
            if not stack:
                break
            start, _ = stack.pop()
            if k + 1 < len(statements[i]) and statements[i][k + 1][0] == "|":
                for si, sk in order[start:pos + 1]:
                    out.setdefault(si, set()).add(sk)
    return {i: frozenset(ks) for i, ks in out.items()}


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
