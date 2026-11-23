"""One shell line's statements, and the same-line masking rules read on them (#775, #919, #1008).

Split out of ``lib.ci_masking`` (#1008), which applies these rules per line and re-exports
``split_statements``; the rules themselves are documented in its docstring.
``split_statements(text)`` splits one shell line into top-level ``;`` statements of
``(operator before, segment)`` pairs; quotes, ``$(...)`` and backticks hold no operator.
``_same_line_keep(statement, later)`` is the segment indices the same-line rules keep: a
``|| <no-op>`` (``true``, ``/bin/true``, ``:``, ``exit 0``) masks the segments before it,
unless they are a capture checked on a ``later`` line, and an exit-zero flag masks its
segment and the ``||`` pipelines it skips.

``( )`` subshells (#1008). A ``(`` that opens a segment, and a ``)`` that ends one, are
read as a subshell group. A ``|| <no-op>`` inside a group masks the segments before it back
to the group's start only, as the same text at top level masks back to the statement's
start: ``(cd sub && pytest || true)`` and ``(pytest || :)`` are masked, ``(pytest)``,
``(pytest && echo ok)`` and ``x=$(pytest)`` are not, and ``pytest && (echo x || true)``
keeps ``pytest`` (bash returns its status). A group closed by its no-op (``(pytest ||
true)``) exits 0, so a ``||`` no-op that ends the group masks it as a whole command would
be. Known limit: a group spanning ``;`` statements is read one statement at a time, so in
``(ruff; pytest) || true`` only ``pytest`` is masked, though bash ignores errexit inside a
group on the left of ``||``.

Inward-only imports: stdlib only; never ``lib.ci_masking`` or an orchestrator.
Tests: ``tests/test_ci_subshell_noop.py``, ``tests/test_ci_checks.py``,
``tests/test_ci_false_credit.py``.
"""
from __future__ import annotations

import re
from collections.abc import Callable

_NOOPS = frozenset({"true", "/bin/true", ":", "exit 0"})
_QUOTED = re.compile(r"'[^']*'|\"(?:\\.|[^\"\\])*\"")
# Flags that make a linter exit 0 whatever it finds (``ruff check --exit-zero``,
# ``golangci-lint run --issues-exit-code=0``): the check runs but cannot fail.
_EXIT_ZERO_FLAG = re.compile(r"(?:^|\s)(?:--exit-zero|--issues-exit-code(?:=|\s+)0)(?=\s|$)")
_EXIT_NONZERO = re.compile(r"\bexit\s+(?:[1-9]\d*|\"?\$\{?\w+\}?\"?)")
_REDIRECT_TARGET = re.compile(r"(?:^|\s)(?:&|\d)?>>?\s*([^\s&;|<>]+)")
_TEE_TARGET = re.compile(r"^tee(?:\s+-\w+)*\s+([^\s;|]+)")
# Sinks no later step reads back: output sent there is not a capture.
_NOT_CAPTURED = re.compile(r"^(?:/dev/null|\$\{?GITHUB_STEP_SUMMARY\}?)$")


def split_statements(text: str) -> list[list[tuple[str, str]]]:
    """Top-level ``;`` statements, each as ``(operator before, segment)`` pairs.

    Quotes, ``$(...)`` and backticks hold no operator; a shell comment ends the text.
    """
    statements: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    buf: list[str] = []
    op = ""
    quote = ""
    depth = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\" and quote == '"':
                buf.append(text[i:i + 2])
                i += 2
                continue
            quote = "" if ch == quote else quote
        elif ch in "'\"`":
            quote = ch
        elif text.startswith("$(", i):
            depth += 1
            buf.append("$(")
            i += 2
            continue
        elif ch == ")" and depth:
            depth -= 1
        elif depth:
            pass
        elif ch == "#" and (i == 0 or text[i - 1].isspace()):
            break
        elif ch == ";":
            current.append((op, "".join(buf).strip()))
            statements.append(current)
            current, buf, op = [], [], ""
            i += 1
            continue
        elif ch in "|&" and not _redirect_amp(text, i):
            current.append((op, "".join(buf).strip()))
            op = text[i:i + 2] if text[i:i + 2] in ("&&", "||") else ch
            buf = []
            i += len(op)
            continue
        buf.append(ch)
        i += 1
    current.append((op, "".join(buf).strip()))
    statements.append(current)
    return [s for s in statements if any(seg for _, seg in s)]


def _redirect_amp(text: str, i: int) -> bool:
    """The ``&`` of ``2>&1`` / ``&>`` joins streams; it is not an operator."""
    return text[i] == "&" and ((i > 0 and text[i - 1] == ">") or text[i + 1:i + 2] == ">")


def _noop_spans(statement: list[tuple[str, str]]) -> list[range]:
    """The segments each ``|| <no-op>`` masks, itself included: back to the start of the
    ``( )`` group it sits in (#1008), or to the statement's start at top level."""
    return [span for span, arm in or_arms(statement) if arm in _NOOPS]


def or_arms(statement: list[tuple[str, str]]) -> list[tuple[range, str]]:
    """Each ``||`` of one statement as ``(span, arm)``: ``span`` runs from the start of the
    ``( )`` group the ``||`` sits in (or the statement's start at top level) to the arm's
    segment, and ``arm`` is that segment's text with group parentheses and spacing
    stripped. A ``(`` that opens a segment opens a group and a ``)`` that ends one closes
    it; ``$(`` never opens one (``split_statements`` keeps a command substitution inside
    its segment)."""
    arms: list[tuple[range, str]] = []
    groups: list[int] = []   # the segment index each open group starts at
    for k, (op, seg) in enumerate(statement):
        text = seg.strip()
        core = text.lstrip("(")
        start = groups[-1] if groups else 0   # the group the operator before ``seg`` is in
        groups.extend([k] * (len(text) - len(core)))
        # Only the ``)`` no ``(`` / ``$(`` in this segment opened close a group:
        # ``(cd $(dirname x)`` stays open.
        bare = _QUOTED.sub("", core)
        closed = max(0, bare.count(")") - bare.count("("))
        if op == "||":
            arms.append((range(start, k + 1), " ".join(core.rstrip(")").split())))
        del groups[max(0, len(groups) - closed):]
    return arms


def _capture_targets(statement: list[tuple[str, str]]) -> list[str]:
    """Files the statement writes its output to (``> f``, ``&> f``, ``| tee f``), sinks excluded."""
    found = [m.group(1) for _, seg in statement for m in _REDIRECT_TARGET.finditer(seg)]
    found += [m.group(1) for op, seg in statement if op == "|" and (m := _TEE_TARGET.match(seg))]
    targets = [t.strip("'\"") for t in found if not t.startswith("&")]
    return [t for t in targets if t and not _NOT_CAPTURED.match(t)]


def _checked_later(targets: list[str], later: list[str]) -> bool:
    """A later line names a captured file, and that line or one after it exits non-zero.

    The file must appear as a whole path token: ``out`` does not match ``layout``.
    """
    named = [re.compile(r"(?<![\w./-])" + re.escape(t) + r"(?![\w./-])") for t in targets]
    for idx, line in enumerate(later):
        if any(n.search(line) for n in named):
            return any(_EXIT_NONZERO.search(rest) for rest in later[idx:])
    return False


def _join(statement: list[tuple[str, str]]) -> str:
    return " ".join(f"{op} {seg}" if op else seg for op, seg in statement)


def _drop_exit_zero(statement: list[tuple[str, str]], keep: list[int]) -> list[int]:
    """``keep`` without the segments run with an exit-zero flag (``ruff check . --exit-zero``)
    and, when that segment ends its pipeline, the ``||`` pipelines after it up to the next
    ``&&``: they never run (``ruff check . --exit-zero || pytest`` drops ``pytest``, #936)."""
    out: list[int] = []
    state = ""   # "zero": just after an exit-zero segment; "skip": in a pipeline that never runs
    for i in keep:
        op, seg = statement[i]
        if op == "|":
            if state != "skip" and not _EXIT_ZERO_FLAG.search(seg):
                out.append(i)
            if state != "skip":
                state = ""   # an earlier command of the pipeline sets its status under pipefail
            continue
        if op == "||" and state:
            state = "skip"
            continue
        state = ""
        if _EXIT_ZERO_FLAG.search(seg):
            state = "zero"
            continue
        out.append(i)
    return out


def _pick(statement: list[tuple[str, str]], keep: list[int]) -> list[tuple[str, str]]:
    """The segments of ``statement`` at ``keep``, the first with no operator before it."""
    return [("", statement[keep[0]][1]), *(statement[i] for i in keep[1:])] if keep else []


def _same_line_keep(statement: list[tuple[str, str]], later: Callable[[], list[str]]) -> list[int]:
    """The segment indices of one statement the same-line rules keep: a ``|| <no-op>`` masks
    the segments before it unless they are a capture checked on a ``later`` line of the job,
    and an exit-zero flag masks its segment and the ``||`` pipelines it skips."""
    masked: set[int] = set()
    for span in _noop_spans(statement):
        targets = _capture_targets(statement[span.start:span.stop - 1])
        if not (targets and _checked_later(targets, later())):
            masked.update(span)
    return _drop_exit_zero(statement, [k for k in range(len(statement)) if k not in masked])
