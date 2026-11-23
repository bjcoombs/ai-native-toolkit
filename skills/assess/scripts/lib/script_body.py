"""Does a committed CI script's body provably run an enforced check? (issue #1033)

``body_runs(text, errexit=, pipefail=, test=)`` reads one script body and is true only when a
statement that ``test`` accepts (``check_command.is_check_command`` for Layer 5,
``is_lint_run`` for Layer 3) would fail the script when the check fails:

- with errexit on (the start state, or after ``set -e`` / ``set -o errexit`` / a flag
  cluster holding ``e``), the check segment is not errexit-exempt (``ci_exempt``: left of
  ``&&`` / ``||``, a non-last pipeline element without pipefail, after ``!``), so
  ``pytest || true`` and ``pytest | tee log`` (no pipefail) do not count;
- with errexit off, the check is the final segment of the last top-level statement, so the
  script's exit status is the check's (``pytest; echo done`` does not count);
- in either state, the check is followed by ``|| exit N`` with ``N`` not a literal ``0``
  (``pytest || exit 1``, ``cd x && pytest || exit $?``), as the last element of its pipeline.

Reading stops at the first top-level ``exit``: what follows never runs, and an ``exit``
after a check with errexit off is the last statement, so it hides the check. A body with
anything this reader does not model is not read (false): a compound command or keyword
(``if``, ``for``, ``while``, ``case``, ``function``, braces, subshells), a heredoc, a line
continuation, a function definition, ``eval``, ``exec``, ``trap``, ``return`` or a
background ``&``, any of these as a later ``;`` statement on a line (``echo; trap 'exit 0'
ERR``), a nested ``.`` / ``source`` of anything but an environment file (``. ./lib.sh`` may
``set +e``; ``. venv/bin/activate`` is fine), and an ``exit`` behind ``&&`` / ``||`` other than a
non-zero re-raise (``[ -z "$RUN" ] && exit 0``). A check behind a guard (``[ -n "$RUN" ] &&
pytest``) is credited as a CI line with the same guard is: it fails the script when it runs.
False means "not shown", never "no check": callers only upgrade a
reading with it (``lib.ci_scripts.ScriptTree``), so a name rule's True or null stands.

``start_state(form, body)`` gives the errexit and pipefail a script starts with:
``executed`` (``./x``, ``bin/x``) follows the shebang's flags (``#!/bin/bash -e``,
``#!/usr/bin/env -S bash -eo pipefail``; a shebang naming no shell, ``#!/usr/bin/make -f``,
is not read at all), ``shell`` (``bash x``, ``sh -e x``) follows the command's own flags, and
``sourced`` (``. x``, ``source x``) starts with both off, because it inherits the step shell,
which a single line does not show. A sourced body is read with ``sourced=True``: its own
``set -e`` is not trusted, because bash ignores errexit in a file sourced from an exempt
position (``. ./x.sh && echo ok``), so only the last-statement and ``|| exit N`` rules credit
it. A line that leaves a quote open, and a ``set`` inside a compound statement
(``[ -n "$STRICT" ] && set -e``), make the body unread. Each rule was checked against
bash 5 under ``bash -e``, ``bash -eo pipefail`` and ``sh -e`` (``tests/test_script_body.py``
runs the fixtures under a real shell where one is available).
"""
from __future__ import annotations

import re
from collections.abc import Callable

from lib.ci_exempt import exempt_segments, pipefail_after
from lib.ci_masking import split_statements
from lib.named_check import sourced_env

_UNMODELLED = frozenset({
    "if", "then", "elif", "else", "fi", "for", "while", "until", "do", "done", "case", "esac",
    "select", "function", "{", "}", "(", ")", "eval", "exec", "trap", "return", "[[",
})
_FUNCTION = re.compile(r"^[\w.-]+\s*\(\s*\)")
_SHELLS = frozenset({"bash", "sh", "dash", "zsh", "ksh"})


def _errexit_after(segment: str, current: bool) -> bool:
    """``set -e``, ``set -eu``, ``set -o errexit`` turn errexit on; ``set +e`` off."""
    words = segment.split()
    if words[:1] != ["set"]:
        return current
    for prev, word in zip(words, words[1:]):
        if word[:1] in ("-", "+") and len(word) > 1 and word[1:].isalpha() and "e" in word[1:]:
            current = word[0] == "-"
        elif word == "errexit" and prev[:1] in ("-", "+") and "o" in prev[1:]:
            current = prev[0] == "-"
    return current


def _unmodelled(line: str) -> bool:
    words = line.split()
    return (line.endswith("\\") or "<<" in line or bool(_FUNCTION.match(line))
            or any(w in _UNMODELLED for w in words[:1]) or line.startswith(("(", "{"))
            or _open_quote(line))


def _open_quote(line: str) -> bool:
    """A quote this line leaves open (``echo "Usage:``): the next lines are its text."""
    quote = ""
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and quote != "'":
            i += 2
            continue
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "'\"`":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1].isspace()):
            break
        i += 1
    return bool(quote)


def _reraised(statement: list[tuple[str, str]], k: int) -> bool:
    """``pytest || exit 1``: the next pipeline exits non-zero when the check fails."""
    if k + 1 >= len(statement) or statement[k + 1][0] != "||" or statement[k][1].startswith("!"):
        return False
    words = statement[k + 1][1].split()
    return len(words) == 2 and words[0] == "exit" and words[1] != "0"


def _nested_source(segment: str) -> bool:
    """``. ./lib.sh`` inside a body: the sourced file may ``set +e`` or redefine the check, so the
    body is not read; an environment file (``. venv/bin/activate``, ``source .env``) is fine."""
    words = segment.split()
    return words[:1] in (["."], ["source"]) and not sourced_env(" ".join(["source", *words[1:]]))


def _guarded_exit(statement: list[tuple[str, str]]) -> bool:
    """An ``exit`` after ``&&`` / ``||`` other than a non-zero re-raise (``[ -z "$RUN" ] && exit
    0``): the script may stop before the check runs, so the body is not read."""
    for op, seg in statement[1:]:
        words = seg.split()
        if words[:1] == ["exit"] and not (op == "||" and len(words) == 2 and words[1] != "0"):
            return True
    return False


def body_runs(text: str, *, errexit: bool, pipefail: bool, test: Callable[[str], bool],
              sourced: bool = False) -> bool:
    """True when ``text`` provably fails on a failing ``test`` command (module docstring)."""
    statements = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if _unmodelled(line):
            return False
        for statement in split_statements(line):
            if any(op == "&" for op, _ in statement) or any(
                    _unmodelled(seg) or _nested_source(seg) for _, seg in statement) or (
                    _guarded_exit(statement)) or (
                    len(statement) > 1 and any(seg.split()[:1] == ["set"] for _, seg in statement)):
                return False   # [ -n "$CI" ] && set -e: errexit is not known after it
            statements.append(statement)
    credited = False
    for n, statement in enumerate(statements):
        if statement[0][1].split()[:1] == ["exit"]:
            return credited
        exempt = exempt_segments(statement, pipefail)
        last = n == len(statements) - 1
        for k, (_, seg) in enumerate(statement):
            if test(seg) and (_reraised(statement, k) or k not in exempt and (
                    errexit or (last and k == len(statement) - 1))):
                credited = True
            errexit = errexit if sourced else _errexit_after(seg, errexit)
        pipefail = pipefail_after(statement, pipefail)
    return credited


def start_state(form: str, body: str, flags: list[str]) -> tuple[bool, bool] | None:
    """``(errexit, pipefail)`` a script starts with (module docstring), or None for an
    executed file whose shebang names no shell (``#!/usr/bin/make -f``): not a shell body.
    ``flags`` are the shell's own flags for the ``shell`` form; the shebang's for ``executed``."""
    if form == "sourced":
        return False, False
    if form == "executed":
        first = body.split("\n", 1)[0]
        if not first.startswith("#!"):
            return False, False
        words = first[2:].split()
        names = [w.rsplit("/", 1)[-1] for w in words]
        shell = next((i for i, n in enumerate(names) if n in _SHELLS), None)
        if shell is None:
            return None
        flags = words[shell + 1:]   # #!/usr/bin/env -S bash -eo pipefail -> -eo pipefail
    text = " ".join(flags)
    errexit = _errexit_after("set " + text, False)
    return errexit, pipefail_after([("", "set " + text)], False)
