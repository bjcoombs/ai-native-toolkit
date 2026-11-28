"""One CI step's AI-disclosure check: its code split by language and read by structure (issue #1599).

``step_units(text)`` splits a GitHub Actions step into the code it runs, each a ``Unit`` with
its language: a ``run:`` value (shell, or Python under ``shell: python``; with no ``shell:``
it is read both ways, as a block that parses as one rarely parses as the other) and a
``script:`` value (JavaScript, ``actions/github-script``). Text with neither key is read as
shell. ``env_names(text)`` is the ``env:`` keys whose value reads the pull request body or the
trailers (``lib.ci.disclosure_terms.is_read``).

``step_acts(units, names)`` parses each unit (``lib.ci.disclosure_shell``,
``lib.ci.disclosure_script``), grows ``names`` to a fixpoint over all of them (a name bound in
one unit, such as a file the shell writes, reaches a script that opens it), then returns the
acts (``EXIT``, ``LABEL``) that follow from a marker test on a held name. A unit that does not
parse adds nothing, so what the readers cannot settle never counts.

Inward-only imports: stdlib and the ``lib.ci.disclosure_*`` readers. Tests:
``tests/test_ai_governance_condition.py``, ``tests/test_disclosure_readers.py``.
"""
from __future__ import annotations

import re
import textwrap
from collections.abc import Callable
from functools import partial
from typing import NamedTuple

from lib.ci import disclosure_script as script
from lib.ci import disclosure_shell as shell
from lib.ci.disclosure_terms import is_read

SHELL = "shell"
PYTHON = "python"
JS = "js"
_MAX_ROUNDS = 8
_KEY = re.compile(r"^(\s*(?:-\s+)?)(run|script|shell)\s*:\s*(.*)$")
_ENV = re.compile(r"^(\s*(?:-\s+)?)env\s*:\s*$")
_ENTRY = re.compile(r"^\s*([A-Za-z_]\w*)\s*:\s*(.+)$")


class Unit(NamedTuple):
    language: str  # SHELL, PYTHON or JS
    code: str


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _value(lines: list[str], i: int, column: int, value: str) -> tuple[str, int]:
    """A key's value and the index after it: a ``|`` or ``>`` block's lines dedented, or one line."""
    if value[:1] in ("|", ">"):
        j = i + 1
        while j < len(lines) and (not lines[j].strip() or _indent(lines[j]) > column):
            j += 1
        return textwrap.dedent("\n".join(lines[i + 1:j])), j
    if len(value) > 1 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1].replace("''", "'") if value[0] == "'" else value[1:-1]
    return value, i + 1


def step_units(text: str) -> list[Unit]:
    """The code one step runs, by language (module docstring)."""
    lines = text.splitlines()
    found: list[tuple[str, str]] = []
    runner = ""
    i = 0
    while i < len(lines):
        m = _KEY.match(lines[i])
        if not m:
            i += 1
            continue
        value, i = _value(lines, i, len(m.group(1)), m.group(3).strip())
        if m.group(2) == "shell":
            runner = value.strip().split(" ")[0]
        else:
            found.append((m.group(2), value))
    if not found:
        return [Unit(SHELL, text)]
    run_languages = ([PYTHON] if runner.startswith("python") else [SHELL] if runner in ("bash", "sh")
                     else [SHELL, PYTHON] if not runner else [])
    return [Unit(lang, code) for key, code in found for lang in (run_languages if key == "run" else [JS])]


def env_names(text: str) -> frozenset[str]:
    """The ``env:`` keys in ``text`` whose value reads the body or the trailers."""
    lines = text.splitlines()
    out: set[str] = set()
    for i, line in enumerate(lines):
        m = _ENV.match(line)
        if not m:
            continue
        for entry in lines[i + 1:]:
            if entry.strip() and _indent(entry) <= len(m.group(1)):
                break
            found = _ENTRY.match(entry)
            if found and is_read(found.group(2)):
                out.add(found.group(1))
    return frozenset(out)


class _Parsed(NamedTuple):
    binds: Callable[[frozenset[str]], set[str]]
    acts: Callable[[frozenset[str]], set[str]]


def _parse(unit: Unit) -> _Parsed | None:
    if unit.language == SHELL:
        seq = shell.parse(unit.code)
        return None if seq is None else _Parsed(partial(shell.binds, seq), partial(shell.acts, seq))
    if unit.language == PYTHON:
        module = script.py_parse(unit.code)
        return None if module is None else _Parsed(partial(script.py_binds, module, unit.code),
                                                    partial(script.py_acts, module, unit.code))
    stmts = script.js_parse(unit.code)
    return None if stmts is None else _Parsed(partial(script.js_binds, stmts), partial(script.js_acts, stmts))


def step_acts(units: list[Unit], names: frozenset[str] = frozenset()) -> set[str]:
    """The acts that follow from a marker test on what the step read (module docstring)."""
    parsed = [p for p in (_parse(u) for u in units) if p is not None]
    for _ in range(_MAX_ROUNDS):
        grown = names.union(*(p.binds(names) for p in parsed))
        if grown == names:
            break
        names = grown
    return set[str]().union(*(p.acts(names) for p in parsed))
