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
  failing arm keeps them credited: ``|| false``, ``|| exit 1``, ``|| exit $?``, a bare
  ``|| exit`` (it exits with the check's status), ``|| return 1`` or an arm group holding
  one (``|| { echo failed; exit 1; }``). An arm is judged by the commands that run, never
  by its words (#1061): each later ``||`` / ``&&`` runs or is skipped by the status so far,
  so ``|| echo false``, ``|| echo exit`` and ``|| echo failed || exit 1`` (the ``exit 1``
  never runs) can pass, and ``|| echo failed && exit 1`` fails. An arm group that runs on past its line (``|| {``
  then the body on the lines below) is not read, so the check stays credited as before.
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
    pipefail off (``pytest | tee log``) is masked by ``ci_masking`` (#1061), not read here.
  - a ``{ }`` group whose closing brace, or a function whose call, sits before ``&&`` /
    ``||`` or in a condition (``group_call_unsure``): errexit is ignored inside it, so
    ``{ pytest; echo done; } && echo ok`` and ``f() { pytest; echo done; }; f && echo
    ok`` exit 0. ``{ pytest; }; echo next`` and a plain call ``f`` stay credited (both
    exit 1).

Conservative nulls (known limits): a ``{ }`` group or function called before ``&&`` reads
null even where its last command is the check and nothing follows (``{ pytest; } && echo
ok`` exits 1); a call or closer that re-raises (``f || exit 1``) stays credited; an arm
group followed on its line by a re-raise (``pytest || { echo x; }; exit 1``) reads null.
``uncalled_functions`` names the body statements of a function no statement outside it
calls, which ``ci_masking`` masks (#1061). Not read: a check before ``&&`` inside a
subshell or function with a later statement there (it stays credited), and a formatter gate whose
``git diff --exit-code`` has a fallback that can pass (``lib.ci_lint_gates`` reads it as
no gate, false, not null).

Inward-only imports: stdlib, ``lib.ci_statements`` and ``lib.ci_exempt``; never
``lib.ci_masking`` (which calls it) or an orchestrator.
Tests: ``tests/test_ci_may_pass.py``.
"""
from __future__ import annotations

import re

from lib.ci_exempt import Statement, exempt_segments, unquoted
from lib.ci_statements import _NOOPS, _join, _pick, or_arms, split_statements

# A status that fails the step: ``false``, ``exit`` / ``return`` with a non-zero literal,
# a variable or ``$?``, or a bare ``exit`` (it exits with the failing check's status).
_FAILS = re.compile(
    r"(?:^|[\s;{(])(?:false|(?:exit|return)\s+(?:[1-9]\d*|\"?\$(?:\?|\{?\w+\}?)\"?)"
    r"|exit(?=\s*(?:$|[;})])))(?=$|[\s;})])")
_FUNCTION = re.compile(r"^(?:function\s+([\w-]+)(?:\s*\(\))?|([\w-]+)\s*\(\))\s*\{?\s*$")


def or_arm_unsure(statement: Statement, tail: str = "", pipefail: bool = True) -> set[int]:
    """The segments a ``||`` arm that can pass leaves unsure: those before it, back to the
    start of its ``( )`` group. ``tail`` is the rest of the line after this statement, read
    when the arm opens a group that runs past it (``|| { echo failed; exit 1; }``).

    An arm is judged by the commands that run, not by its words (#1061): from the failing
    check on, each ``||`` / ``&&`` arm runs or is skipped by the status so far, so ``pytest ||
    echo false`` and ``pytest || echo failed || exit 1`` (the ``echo`` succeeds, so ``exit 1``
    never runs) can pass, while ``pytest || false`` and ``pytest || echo failed && exit 1`` fail.
    ``pipefail`` is the body's setting: with it off, a pipeline member but the last loses its
    status and its ``exit`` (``pytest || exit 1 | tee log`` can pass)."""
    out: set[int] = set()
    for span, arm in or_arms(statement):
        if arm in _NOOPS:
            continue
        if _chain_fails(statement, span.stop - 1, tail, pipefail) is False:
            out.update(range(span.start, span.stop - 1))
    return out


def _chain_fails(statement: Statement, start: int, tail: str, pipefail: bool = True) -> bool | None:
    """Does the step fail when the command before segment ``start`` (a ``||`` arm) fails?
    None when a group arm runs on past its line: not read, so the check stays credited."""
    result = _run(statement[start:], True, tail, pipefail)
    return None if result is None else result[1]


def _run(segments: Statement, failing: bool, tail: str = "",
         pipefail: bool = True) -> tuple[bool, bool] | None:
    """``(ended, failing)`` after running ``segments`` from a status (``failing``): ``ended``
    when an ``exit`` / ``return`` ends the step with that status. Each ``&&`` / ``||``
    pipeline runs or is skipped by the status so far; a pipeline member but the last runs in
    a subshell, so its ``exit`` ends only that member, and its status counts only with
    ``pipefail`` (a failing member then fails the pipeline). A ``{ }`` /
    ``( )`` group is read from its body, then the operators after its closer. None when a
    group does not close on this line (``tail`` is the rest of the line)."""
    runs = True
    piped_failed = False   # an earlier member of this pipeline failed (counts under pipefail)
    for j, (op, seg) in enumerate(segments):
        if op in ("&&", "||"):
            runs = (op == "&&") != failing   # && runs on success, || on failure
        elif op != "|":
            runs = True
        if op != "|":
            piped_failed = False
        if not runs:
            continue
        piped = j + 1 < len(segments) and segments[j + 1][0] == "|"
        if seg.lstrip().startswith(("{", "(")):
            found = _group_body(_join(_pick(segments, list(range(j, len(segments))))) + " ; " + tail)
            if found is None:
                return None
            body, rest = found
            ended, failing = _run_body(body, pipefail)
            if ended and seg.lstrip()[0] == "{" and not piped:
                return True, failing
            rest = rest.lstrip()
            after = split_statements(rest) if rest.startswith(("&&", "||", "|")) else []
            return _run(after[0][1:], failing, "", pipefail) if after else (False, failing)
        status = _status(seg, failing)
        if piped:
            member = status if status is not None else _fails_status(seg)
            piped_failed = piped_failed or (pipefail and member)
            continue
        if status is not None:
            return True, status
        failing = _fails_status(seg) or piped_failed
    return False, failing


def _status(seg: str, failing: bool) -> bool | None:
    """True / False when ``seg`` ends the step (``exit 1`` / ``exit 0``), else None.
    A bare ``exit``, or ``exit $?``, exits with the status so far."""
    words = seg.strip().strip("(){} ").split()
    if not words or words[0] not in ("exit", "return"):
        return None
    if len(words) == 1 or words[1].strip('"') in ("$?", "${?}"):
        return failing
    return words[1].strip('"\'') != "0"


def _fails_status(seg: str) -> bool:
    """``false`` fails; any other command may pass."""
    words = seg.strip().strip("(){} ").split()
    return words[:1] in (["false"], ["/bin/false"])


def _group_body(text: str) -> tuple[str, str] | None:
    """``(body, rest)``: the text inside the ``{ }`` / ``( )`` group ``text`` opens and the text
    after its closer, or None when the group does not close on this line. Quotes, ``$(...)``
    and ``${...}`` open no group."""
    bare = unquoted(text.lstrip())
    stack: list[str] = []
    i = 0
    while i < len(bare):
        if bare.startswith(("$(", "${"), i):
            stack.append("$")
            i += 2
            continue
        ch = bare[i]
        if ch in "({":
            stack.append(ch)
        elif ch in ")}" and stack:
            top = stack.pop()
            if not stack:
                return (bare[1:i], bare[i + 1:]) if top == bare[0] else None
        i += 1
    return None


def _run_body(body: str, pipefail: bool = True) -> tuple[bool, bool]:
    """``(ended, failing)`` for a group body: an ``exit`` ends it; a statement that ends
    failing fails it (errexit applies to the last command of a list); else the last status."""
    failing = False
    for statement in split_statements(body):
        result = _run(statement, False, "", pipefail)
        if result is None:
            return False, False   # a nested group not read: it may pass
        if result[0]:
            return result
        failing = result[1]
        if failing:
            return False, True
    return False, failing


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
        if (brace is not None and not _reraises(closer)
                and (brace in exempt_segments(closer, True) or conditions[start])):
            out.update(range(start, end + 1))
    for i, statement in enumerate(statements):
        exempt = exempt_segments(statement, True)
        for k, (_, seg) in enumerate(statement):
            words = seg.split()
            while words and words[0] in ("!", "if", "elif", "while", "until", "then", "do"):
                words = words[1:]
            body = functions.get(words[0]) if words else None
            if (body is not None and i not in body and not _reraises(statement)
                    and (k in exempt or conditions[i])):
                out.update(body)
    return out


# Words a call can follow at the start of a command: keywords, wrappers and a group opener.
_CALL_LEADS = frozenset({"!", "if", "elif", "while", "until", "then", "do", "else", "{", "(",
                         "time", "exec", "command", "builtin", "nohup", "env", "sudo"})
# Commands whose arguments are text, never run (``echo f``).
_TEXT_COMMANDS = frozenset({"echo", "printf", ":"})
_ASSIGN = re.compile(r"^[A-Za-z_]\w*=")
_LEAD = re.compile(r"^\s*(?:function\s+[\w.-]+(?:\s*\(\s*\))?|[\w.-]+\s*\(\s*\))\s*\{")


def _call_words(segment: str) -> list[str]:
    """The first word of each command ``segment`` runs: its own (past keywords, wrappers,
    ``X=1`` assignments, a ``case`` pattern), and, in a plain assignment, the one after each
    ``$(`` / backtick."""
    out: list[str] = []
    pieces = re.split(r"\$\(|`", segment)
    # A substitution's text ends at its closer, and runs as the command only in a plain
    # assignment (``out=$(f)`` fails under errexit; ``echo "$(f)"`` does not). A function lead
    # (``g() { f``) opens a body.
    assigns = all(_ASSIGN.match(w) for w in pieces[0].split()) and pieces[0].rstrip().endswith("=")
    subs = [p.split(")", 1)[0] for p in pieces[1:]] if assigns else []
    for piece in [pieces[0], *subs]:
        piece = _LEAD.sub("", piece)
        words = piece.replace("(", " ( ").split()
        if words[:1] == ["case"]:
            words = words[words.index("in") + 1:] if "in" in words else []
        while words and (words[0] in _CALL_LEADS or _ASSIGN.match(words[0]) or words[0].endswith(")")):
            words = words[1:]
        if words:
            out.append(words[0].rstrip(";`"))
    return out


def uncalled_functions(statements: list[Statement]) -> tuple[set[int], set[int]]:
    """``(uncalled, maybe)``: the body statements of each function nothing outside it calls,
    and of each one named outside it only as another command's argument (#1061).

    ``f() { pytest; }`` alone defines ``f`` and runs nothing, so its check cannot fail the step:
    ``ci_masking`` masks that body. A call is the name as the command a segment runs (``f``,
    ``} && f``, ``time f``, ``X=1 f``, ``out=$(f)``, a ``case`` arm). The name inside echoed text
    (``echo f``) is no call. The name as another command's argument (``retry 3 f``, ``npm run
    build`` beside a ``run`` function) may or may not run it, so that body reads null, never
    credit. A mention inside another uncalled function's body is no call (``f() { pytest; };
    g() { f; }``), so the reading repeats until no more bodies drop out."""
    bodies = {name: range(start, end + 1) for start, end, name in _groups(statements) if name}
    # A closing statement's segments after its ``}`` run outside any body (``f() { pytest; } && f``).
    outside_closer = {body[-1]: [seg for _, seg in statements[body[-1]]
                                 if not seg.lstrip().startswith("}")] for body in bodies.values()}
    out: set[int] = set()
    maybe: set[int] = set()
    uncalled: set[str] = set()
    changed = True
    while changed:
        changed = False
        for name, body in bodies.items():
            if name in uncalled:
                continue
            segments = [seg for i, statement in enumerate(statements) if i not in body and i not in out
                        for _, seg in statement]
            segments += [seg for segs in outside_closer.values() for seg in segs]
            if any(name in _call_words(seg) for seg in segments):
                continue
            word = re.compile(r"(?<![\w.-])" + re.escape(name) + r"(?![\w.-])")
            named = [seg for seg in segments if word.search(unquoted(seg))]
            if any((_call_words(seg)[:1] or [""])[0] not in _TEXT_COMMANDS for seg in named):
                maybe.update(body)
                continue
            uncalled.add(name)
            out.update(body)
            changed = True
    return out, maybe - out


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
        opens = text.count("(")
        closes = text.count(")") - text.count("\0")
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


def _reraises(statement: Statement) -> bool:
    """``f || exit 1``, ``{ ...; } || exit 1``: the call's failure still fails the step."""
    return bool(_FAILS.search(unquoted(" ".join(seg for _, seg in statement))))


def _head(statement: Statement) -> str:
    words = statement[0][1].split() if statement else []
    return words[0] if words else ""
