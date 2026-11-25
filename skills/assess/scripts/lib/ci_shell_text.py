"""What a CI shell command runs, with the text it only names blanked (#1249).

``command_text`` turns a ``run:`` or ``script:`` command into the text the shell runs, so a
sign search on it does not credit a name the command only mentions:

- a quoted argument (``git commit -m "..."``) becomes ``_``, unless it is itself the command
  (the whole value, or after ``sh -c``), when it is read the same way;
- a ``$(...)`` or backtick span inside double quotes runs, so its command is kept on its own
  line, read the same way (``OUT="$(claude plugin eval .)"``, ``echo "$(...)"``); single quotes
  keep nothing;
- a ``'`` with no later ``'`` in the text (the apostrophe of ``don't`` in a heredoc body) ends
  at the end of its line, so it does not blank the commands after it; a later ``'``, even inside
  double quotes, still pairs with it, and a closed single-quoted span may run over lines;
- a ``#`` comment is cut, and an ``echo`` or ``printf`` is dropped with its arguments.

Used by ``lib.ci_model_jobs`` for the ``claude plugin eval`` sign. Tests:
``tests/test_ci_model_jobs_plugin_eval.py``.
"""
from __future__ import annotations

import re

_ECHO = re.compile(r"(?:^|(?<=[;&|(\n]))[ \t]*(?:echo|printf)\b[^;&|\n]*", re.M)
_SHELL_C = re.compile(r"(?:^|[;&|(\n])\s*$|\s-c\s*$")


def _single_quoted(text: str, i: int) -> int:
    """The index closing the single-quoted string opened at ``text[i]``. With no closing quote (an
    apostrophe, as in a heredoc's ``don't``), the index of the end of its line instead."""
    end = text.find("'", i + 1)
    if end >= 0:
        return end
    end = text.find("\n", i + 1)
    return len(text) if end < 0 else end


def _paren_end(text: str, j: int) -> int:
    """The index of the ``)`` closing the ``(`` at ``text[j]``, past quotes and nested parentheses."""
    depth = 0
    while j < len(text):
        ch = text[j]
        if ch == "\\":
            j += 1
        elif ch == "'":
            j = _single_quoted(text, j)
        elif ch == '"':
            j = _double_quoted(text, j)[0]
        elif ch in "()":
            depth += 1 if ch == "(" else -1
            if not depth:
                return j
        j += 1
    return len(text)


def _double_quoted(text: str, i: int) -> tuple[int, list[str]]:
    """The index closing the double-quoted string opened at ``text[i]``, and the commands its
    ``$(...)`` and backtick spans run (the shell runs those inside the quotes)."""
    spans: list[str] = []
    j = i + 1
    while j < len(text) and text[j] != '"':
        if text[j] == "`":
            end = text.find("`", j + 1)
            end = len(text) if end < 0 else end
            spans.append(text[j + 1:end])
            j = end
        elif text.startswith("$(", j):
            end = _paren_end(text, j + 1)
            spans.append(text[j + 2:end])
            j = end
        j += 2 if text[j:j + 1] == "\\" else 1
    return j, spans


def command_text(text: str) -> str:
    """``text`` with what the shell does not run blanked: a quoted argument (``-m "..."``) becomes
    ``_`` unless it is itself a command (the whole value, or after ``sh -c``), a ``#`` comment
    is cut, and an ``echo`` or ``printf`` is dropped with its arguments. A ``$(...)`` or backtick
    span inside double quotes runs, so it is read on its own line (``OUT="$(claude ...)"``,
    ``echo "$(claude ...)"``)."""
    out: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in "'\"":
            end, spans = _double_quoted(text, i) if ch == '"' else (_single_quoted(text, i), [])
            if _SHELL_C.search("".join(out)):
                out.append(command_text(text[i + 1:end]))
            else:
                out.append("".join(f"\n{command_text(span)}\n" for span in spans) + "_")
            i = end + 1 if text[end:end + 1] == ch else end  # an unclosed ' keeps its newline
            continue
        if ch == "#" and (i == 0 or text[i - 1].isspace()):
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
            continue
        out.append(ch)
        i += 1
    return _ECHO.sub(" ", "".join(out))
