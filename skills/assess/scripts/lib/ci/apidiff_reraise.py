"""How the apidiff reader decides that a shell branch re-raises (issues #1612, #1617, theme 8 of #826).

Split out of ``lib.ci.api_compat`` to keep it under the file-size ratchet. Nothing is executed:
the shell text is read as words.

- ``reraises(cmd)``: the command is ``exit`` / ``return`` with a non-zero code or ``false``, as
  the command itself, never an argument (``echo false``).
- ``and_chain(rest)`` / ``branch_reraises(rest)``: the ``&&`` chain or ``||`` fallback a test
  opens, and whether it re-raises past a later passing ``||`` on the line. ``false`` is rescued by
  ``|| true``; an ``exit N`` (``exits``) the branch always reaches is not, because it ends the
  shell first.
- ``line_ends(text)``: a ``then``-branch line whose ``exit N`` runs whenever the line runs
  (``echo broke; exit 1``, ``echo broke && exit 1``), never one behind ``||``, a test or a
  subshell (``SUBSHELL``: a command that opens with ``(``, not a quoted ``(``).
- ``backgrounded(text)``: a line that sends a job to the background with a bare ``&`` outside
  quotes (``[ -s o.txt ] && exit 1 &``, ``fi &``): its ``exit 1`` cannot fail the job (#1634).
  ``&&``, ``2>&1``, ``&>`` and ``|&`` are not one.

The rules are described with the apidiff check in ``lib.ci.api_compat``'s reference. Inward-only imports; never an orchestrator. Tests:
``tests/test_api_compat_condition.py`` and ``tests/test_apidiff_reraise.py``.
"""
from __future__ import annotations

import re
from itertools import dropwhile

from lib.ci.ci_job_commands import OPERATOR, fallback
from lib.instructions.command_resolver import command_words

SUBSHELL = re.compile(r"\s*(?:(?:then|else|do|\{|!)\s+)*\(")   # a command that opens ``( ... )``, not a quoted ``(``
_ECHO = (["echo"], ["printf"])   # commands that cannot fail before a chained ``exit``
_BACKGROUND = re.compile(r"(?<![&>|])&(?![&>])")   # ``... && exit 1 &``: the exit runs in a background job
_QUOTED = re.compile(r"'[^']*'|\"(?:\\.|[^\"\\])*\"")


def reraises(cmd: list[str], held: frozenset[str] = frozenset()) -> bool:
    """A command that ends its script red: ``exit`` or ``return`` with a non-zero literal or a
    variable other than ``$?`` or one ``held`` (assigned ``$?``), or ``false``, as the command itself
    (after a ``then``, ``else``, ``do`` or ``{``), never an argument (``echo false``, ``echo exit 1``)."""
    head = list(dropwhile(lambda w: w in ("then", "else", "do", "{"), cmd))
    code = head[1].strip('"') if len(head) > 1 else ""
    zero = code in ("", "0", "$?") or code.strip("${}") in held   # ``$?`` after apidiff is its 0
    return (head[:1] in (["exit"], ["return"]) and not zero) or head[:1] == ["false"]


def words(part: str) -> list[str]:
    """The words of one operator-split part, the group's braces and parentheses at its ends dropped."""
    return command_words(part.strip().strip("(){} "))


def and_chain(rest: list[str]) -> list[str]:
    """The commands of the ``&&`` chain that opens ``rest`` (``rest[0]`` is ``&&``), braces kept
    together, up to the next ``||``, ``;`` or ``|`` outside them: in ``[ -s f ] && echo x || exit 1``
    the ``exit 1`` runs only when the test fails, so it is not in the chain."""
    out: list[str] = []
    depth = 0
    for k, part in enumerate(rest[1:]):
        if k % 2:
            if depth <= 0 and part != "&&":
                break
            continue
        depth += part.count("{") + part.count("(") - part.count("}") - part.count(")")
        out.append(part)
    return out


def branch_reraises(rest: list[str], held: frozenset[str] = frozenset()) -> bool:
    """Whether the ``&&`` chain or ``||`` fallback opening ``rest`` re-raises past any later passing ``||`` (lib/docs)."""
    def ends(m: int) -> bool:   # ``exit N`` the test reaches unconditionally: chained, or in its ``{ }``
        ops, group = rest[0:m:2], "".join(rest[1:m + 1])
        return exits(words(rest[m])) and not any(map(SUBSHELL.match, rest[1:m + 1:2])) and (
            all(o == "&&" for o in ops) and all(words(p)[:1] in _ECHO for p in rest[1:m:2])
            or ops == ["||"] or (rest[m - 1] == ";" and command_words(rest[1])[:1] == ["{"]
                                                            and group.count("{") == 1 and "}" not in group))
    branch = and_chain(rest) if rest[0] == "&&" else OPERATOR.split(fallback(rest[1:]))[::2]
    tails = [branch] + [OPERATOR.split(fallback(rest[k + 1:]))[::2] for k in range(2, len(rest), 2) if rest[k] == "||"]
    raised = [any(reraises(words(p), held) for p in parts) for parts in tails]   # the branch, then each later fallback
    return raised[0] and (all(raised) or any(ends(m) for m in range(1, 2 * len(branch), 2)))


def exits(cmd: list[str]) -> bool:
    """``exit N``, N a non-zero literal: it ends the shell before a later ``|| true`` runs."""
    return cmd[:1] == ["exit"] and len(cmd) > 1 and cmd[1].strip('"').isdigit() and int(cmd[1].strip('"')) > 0


def line_ends(text: str) -> bool:
    """Whether a ``then``-branch line runs an ``exit N`` whenever it runs: the first ``exit N``
    follows only ``;`` or an ``echo`` / ``printf`` ``&&`` chain, and no subshell opens before it."""
    parts = OPERATOR.split(re.sub(r"^\s*then\s+", "", text))
    m = next((m for m in range(0, len(parts), 2) if exits(words(parts[m]))), None)
    if m is None or any(map(SUBSHELL.match, parts[:m + 1:2])):
        return False
    k = m - 1
    while k > 0 and parts[k] == "&&" and words(parts[k - 1])[:1] in _ECHO:
        k -= 2
    return k < 0 or parts[k] == ";"


def backgrounded(text: str) -> bool:
    """Whether the shell line ``text`` sends a job to the background: a bare ``&`` outside quotes."""
    return bool(_BACKGROUND.search(_QUOTED.sub("", text)))
