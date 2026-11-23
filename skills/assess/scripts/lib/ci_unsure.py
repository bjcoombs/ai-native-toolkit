"""Which CI shell statements run a check whose failure may not fail the job? (#1008)

``lib.ci_masking`` drops a masked check (one that can never fail its job) and keeps an
enforced one. Between them sit the forms below: the check runs and can fail, but the
step may still pass, depending on what runs after it. They read null with the reason
``check_may_pass``, never credited and never false (the #1008 triage decision, as
#1002, #959 and #884 did for other unknowns). Each was checked against real ``bash -e``
(GitHub's default), ``bash --noprofile --norc -eo pipefail`` (``shell: bash``) and dash
``-e`` (``shell: sh``), with a ``pytest`` that always exits 1.

- A ``||`` arm that can pass (``or_arm_unsure``, every shell body, GitLab's included):
  ``pytest || echo failed`` exits 0. The segments before the ``||`` (back to the start
  of its ``( )`` group) read null. A no-op arm masks them instead (``|| true``), and a
  failing arm keeps them credited: ``|| false``, ``|| exit 1``, ``|| exit $?``,
  ``|| return 1`` or an arm group holding one (``|| { echo failed; exit 1; }``).
- In a GitHub ``run:`` body with errexit on and bash grammar (``ci_masking``'s backward
  walk; a later top-level ``exit 0`` masks these instead, #936):
  - an ``if`` / ``elif`` / ``while`` condition (``if pytest; then echo ok; fi`` exits 0),
    unless that statement or a later one re-raises a status (``if ! pytest; then exit 1;
    fi`` and ``if pytest; then :; else exit 1; fi`` stay credited);
  - an ``until`` condition whose loop body holds a ``break`` (``until_break_flags``:
    ``until pytest; do break; done`` exits 0); without one the loop runs until the check
    passes and stays credited;
  - a check before ``&&`` / ``||`` with a later statement in the body (``pytest && echo
    ok; echo next`` exits 0). As the last statement it stays credited: bash exits with
    the list's status (``pytest && echo ok`` exits 1). A non-last pipeline member with
    pipefail off (``pytest | tee log``) is not read here.
  - a ``{ }`` group whose closing brace, or a function whose call, sits before ``&&`` /
    ``||`` or in a condition (``group_call_unsure``): errexit is ignored inside it, so
    ``{ pytest; echo done; } && echo ok`` and ``f() { pytest; echo done; }; f && echo
    ok`` exit 0. ``{ pytest; }; echo next`` and a plain call ``f`` stay credited (both
    exit 1).

Inward-only imports: stdlib, ``lib.ci_statements`` and ``lib.ci_exempt``; never
``lib.ci_masking`` (which calls it) or an orchestrator.
Tests: ``tests/test_ci_may_pass.py``.
"""
from __future__ import annotations

import re

from lib.ci_exempt import Statement, exempt_segments, unquoted
from lib.ci_statements import _NOOPS, or_arms

# A status that fails the step: ``false``, or ``exit`` / ``return`` with a non-zero
# literal, a variable or ``$?``.
_FAILS = re.compile(
    r"(?:^|[\s;{(])(?:false|(?:exit|return)\s+(?:[1-9]\d*|\"?\$(?:\?|\{?\w+\}?)\"?))(?=$|[\s;})])")
_FUNCTION = re.compile(r"^(?:function\s+([\w-]+)(?:\s*\(\))?|([\w-]+)\s*\(\))\s*\{?\s*$")


def or_arm_unsure(statement: Statement, tail: str = "") -> set[int]:
    """The segments a ``||`` arm that can pass leaves unsure: those before it, back to the
    start of its ``( )`` group. ``tail`` is the rest of the line after this statement, read
    when the arm opens a group that runs past it (``|| { echo failed; exit 1; }``)."""
    out: set[int] = set()
    for span, arm in or_arms(statement):
        if arm in _NOOPS:
            continue
        rest = " ".join(seg for _, seg in statement[span.stop - 1:])
        if statement[span.stop - 1][1].lstrip().startswith(("{", "(")):
            rest += " " + tail
        if not _FAILS.search(unquoted(rest)):
            out.update(range(span.start, span.stop - 1))
    return out


def until_break_flags(statements: list[Statement]) -> list[bool]:
    """Per statement of one script body: is it an ``until`` condition whose loop body,
    up to its ``done``, holds a ``break``?"""
    out = [False] * len(statements)
    for i, statement in enumerate(statements):
        if _head(statement) != "until":
            continue
        depth = 0
        for j in range(i, len(statements)):
            words = " ".join(seg for _, seg in statements[j]).split()
            if "break" in words:
                out[i] = True
            depth += sum(w in ("do",) for w in words) - sum(w == "done" for w in words)
            if j > i and depth <= 0:
                break
    return out


def group_call_unsure(statements: list[Statement], conditions: list[bool]) -> set[int]:
    """The statements inside a ``{ }`` group whose closing brace, or a function whose call,
    sits in an exempt position: before ``&&`` / ``||`` or in a condition."""
    out: set[int] = set()
    functions: dict[str, range] = {}
    for start, end, name in _groups(statements):
        if name:
            functions[name] = range(start, end + 1)
            continue
        closer = statements[end]
        brace = next((k for k, (_, seg) in enumerate(closer) if seg.lstrip().startswith("}")), None)
        if brace is not None and (brace in exempt_segments(closer, True) or conditions[start]):
            out.update(range(start, end + 1))
    for i, statement in enumerate(statements):
        exempt = exempt_segments(statement, True)
        for k, (_, seg) in enumerate(statement):
            words = seg.split()
            while words and words[0] in ("!", "if", "elif", "while", "until", "then", "do"):
                words = words[1:]
            body = functions.get(words[0]) if words else None
            if body is not None and i not in body and (k in exempt or conditions[i]):
                out.update(body)
    return out


def opaque_flags(statements: list[Statement]) -> list[bool]:
    """Per statement: does it sit in, or open, a ``( )`` subshell or a function body? Those
    return their status as one command, so errexit stops at the call (``f() { pytest &&
    echo ok; }; f; echo next`` exits 1), while a ``{ }`` group, an ``if`` or a loop body
    passes an exempt failure through (``{ pytest && echo ok; }; echo next`` exits 0)."""
    out = [False] * len(statements)
    for start, end, name in _groups(statements):
        if name:
            out[start:end + 1] = [True] * (end + 1 - start)
    depth = 0
    for i, statement in enumerate(statements):
        text = unquoted(" ".join(seg for _, seg in statement)).replace("$(", "\0")
        opens = text.count("(") - text.count("\0")
        closes = text.count(")")
        out[i] = out[i] or depth > 0 or opens > 0
        depth = max(0, depth + opens - closes)
    return out


def _groups(statements: list[Statement]) -> list[tuple[int, int, str]]:
    """``(first, last statement, function name or "")`` per top-level ``{ }`` group."""
    out: list[tuple[int, int, str]] = []
    depth = 0
    start, name = 0, ""
    for i, statement in enumerate(statements):
        text = unquoted(" ".join(seg for _, seg in statement)).replace("${", "")
        if depth == 0 and "{" in text:
            start = i
            match = _FUNCTION.match(statement[0][1].strip().split("{")[0].strip() + " {")
            name = (match.group(1) or match.group(2)) if match else ""
        depth += text.count("{") - text.count("}")
        if depth <= 0 and ("{" in text or "}" in text):
            out.append((start, i, name))
            depth = 0
    return out


def _head(statement: Statement) -> str:
    words = statement[0][1].split() if statement else []
    return words[0] if words else ""
