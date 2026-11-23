"""How a shell and make read their own options, for ``lib.check_command`` (issue #955).

``shell_args(words)`` is the script path and arguments a shell runs after its own flags:
``bash -e scripts/test.sh`` runs ``scripts/test.sh`` (``-e`` takes no value), ``bash -o
pipefail x.sh`` and ``bash --rcfile f x.sh`` skip the option's value, and a short cluster
ending in ``o`` / ``O`` takes one too (``-eo pipefail``). It is None for ``bash -c '...'``:
only a single-dash cluster holding ``c`` is one, so ``--norc`` is not.
``shell_string(words)`` is the command a ``-c`` string runs when it is one plain command
(``bash -c "make test"`` -> ``make test``), else ``[]``: a list or pipeline (``a && b``,
``pytest || true``, which CI masking cannot see inside the quotes), a redirection, a ``$(...)``
or backtick substitution, or a string that cannot be split,
which ``check_command`` reads as a command it does not read (null), never false.
``make_args(args)`` drops make's option values: ``-o``, ``-W``, ``-E`` and their long forms
always take one, and ``-j`` / ``-l`` take the next word only when it is a number (``make -j 4
build`` runs ``build``; ``make -j build`` also runs ``build``, with unlimited jobs). Each rule
was checked against bash 3.2 and 5.3, dash and GNU make. Tests: ``tests/test_check_command.py``.
"""
from __future__ import annotations

import re
import shlex

from lib.command_resolver import command_words

SHELLS = frozenset({"bash", "sh", "zsh", "dash"})
# Shell options that take the next word (``bash -o pipefail x.sh``, ``bash --rcfile f x.sh``);
# a short cluster ending in ``o`` / ``O`` does too (``-eo pipefail``). ``-e`` takes none (#955).
_SHELL_VALUED = frozenset({"--rcfile", "--init-file"})


def _shell_flags_end(words: list[str]) -> tuple[int, bool]:
    """Where a shell's own flags end, and whether one of them is a ``-c`` cluster (single dash
    only: ``--norc`` and ``--rcfile`` are not ``-c``)."""
    i, command = 1, False
    while i < len(words) and words[i][:1] in ("-", "+") and len(words[i]) > 1 and words[i] != "--":
        flag = words[i]
        command = command or (flag[0] == "-" and not flag.startswith("--") and "c" in flag[1:])
        valued = flag in _SHELL_VALUED or (not flag.startswith("--") and flag[-1] in "oO")
        i += 2 if valued else 1
    return i + (1 if words[i:i + 1] == ["--"] else 0), command


def shell_args(words: list[str]) -> list[str] | None:
    """A shell's script path and arguments after its own flags (``bash -e x.sh`` -> ``x.sh``),
    or None for ``bash -c '...'``, whose program is a string (``shell_string``)."""
    end, command = _shell_flags_end(words)
    return None if command else words[end:]


def shell_string(words: list[str]) -> list[str]:
    """The words of ``bash -c "<cmd>"``'s string when it is one plain command (``make test``),
    else ``[]``: a list or pipeline (``a && b``, ``pytest || true``), a redirection, a substitution, or a
    string that cannot be split reads as a command this scan does not read (null)."""
    end, _ = _shell_flags_end(words)
    string = words[end] if end < len(words) else ""
    try:
        lexer = shlex.shlex(string, posix=True, punctuation_chars=True)
        tokens = list(lexer)
    except ValueError:
        return []
    if not tokens or "$(" in string or "`" in string or any(set(t) <= set("&|;()<>") for t in tokens):
        return []
    return command_words(string)


# GNU make options that always take the next word, and the two whose number is optional
# (``make -j test`` runs ``test`` with unlimited jobs; ``make -j 4 test`` runs it with four).
_MAKE_VALUED = frozenset({"-o", "-W", "--old-file", "--assume-old", "--what-if", "--new-file",
                          "--assume-new", "-E", "--eval"})
_MAKE_OPTIONAL_NUMBER = frozenset({"-j", "--jobs", "-l", "--load-average", "--max-load"})


def make_args(args: list[str]) -> list[str]:
    """``args`` with make's option values dropped (``-j 4``, ``-l 2.5``, ``-o file``)."""
    out: list[str] = []
    i = 0
    while i < len(args):
        word = args[i]
        nxt = args[i + 1] if i + 1 < len(args) else ""
        if word in _MAKE_VALUED or (word in _MAKE_OPTIONAL_NUMBER and re.fullmatch(r"\d+(\.\d+)?", nxt)):
            i += 2
            continue
        out.append(word)
        i += 1
    return out
