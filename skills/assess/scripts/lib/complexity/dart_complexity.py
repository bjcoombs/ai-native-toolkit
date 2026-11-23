"""Approximate per-function cyclomatic complexity for Dart (issue #364).

lizard 1.23.0 has no Dart reader, so scc scores Dart at file level only and a
Dart hotspot carries no worst-function figure. This module fills that gap with
a brace-and-keyword scanner: no parser and no new dependency, which is why the
treemap registers it as the ``dart-scanner`` backend with ``approximate: true``.

What it counts, per function body: ``if``, ``for``, ``while``, ``case``,
``catch``, ``&&``, ``||``, ``??`` / ``??=`` and a ternary ``?`` (a ``?`` with
whitespace before it, so ``int?`` and ``a?.b`` do not count). Complexity is one
plus that count. ``else if`` counts once, through its ``if``.

What it recognises as a function: a ``{`` or ``=>`` body after a parameter list
whose ``(`` follows a plain identifier (``name(...)``, ``name<T>(...)``, with
``async`` / ``sync*`` allowed before the body), and a getter (``get name {`` or
``get name =>``). An anonymous closure (a parameter list after ``=``, ``(`` or
``,``) is folded into the function that encloses it, so its decision points
count toward the parent. A closure (``{`` or ``=>`` body) outside any function, a constructor body
after an initializer list (``: super(x) {``) and an ``operator`` overload are
each scored on their own as ``<anonymous>``.

Everything inside ``//`` and ``/* */`` comments (which nest in Dart) and inside
single-, double-, triple-quoted and raw strings is skipped; ``${...}``
interpolations are scanned as code. The scanner is one forward pass over at
most ``_READ_BYTES`` of each file. Every regex is a character class or a
literal alternation, at most behind one ``\\s*`` and with no nested
quantifier, so a pathological input (deep nesting, one very long line, an
unterminated string or comment) costs time linear in its length and cannot
backtrack.

The pass has two paths (#967). A run of plain code up to the next quote,
``/``, ``?`` or raw-string prefix, outside any interpolation, is tokenised by
one ``findall`` call and handled inline; everything else (comments, strings,
``?``, and code inside ``${...}``) goes one token at a time through
``_CODE_TOKEN``. Both paths see the same tokens, so the output is the same
either way; the fast path only drops the per-token match object and call.

Approximate per-function cyclomatic complexity for Dart (issue #364), because lizard
has no Dart reader. `scan_dart_functions` is a brace-and-keyword scanner: one forward
pass that skips `//` and nesting `/* */` comments and single-, double-, triple-quoted
and raw strings (scanning `${...}` interpolations as code), and counts `if`, `for`,
`while`, `case`, `catch`, `&&`, `||`, `??` and a ternary `?` per function body.
Functions are `name(...)` / `name<T>(...)` bodies (`{` or `=>`) and getters; anonymous
closures fold into their enclosing function. `dart_function_scores` reads at most 1 MB
of a file (the `generated_files.py` bound) and returns the per-function values and the
worst function's name. The treemap's `collect` runs it on scc-scored `.dart` files and
registers it in `FN_BACKENDS` as `dart-scanner` with `approximate: true`. No regex has a
nested quantifier, so a pathological file costs linear time. The pass has two paths
(#967): a run of plain code up to the next quote, `/`, `?` or raw-string prefix,
outside any `${...}`, is tokenised by one `findall` call and handled inline, and
everything else goes one token at a time through `_CODE_TOKEN`. Both see the same
tokens, so the output does not depend on the path; the fast path cuts the per-token
cost of the common case, which keeps the pathological-input budget in
`test_dart_complexity.py` on the Windows runner. Functions sort by the ordinal of their
opening, which is source order. A `}` closes the `=>` bodies opened inside it before its
own function, so a close always pops the innermost frame and deep nesting stays linear
(#929); `test_dart_complexity.py` asserts that each pathological input's scan time at
most quadruples when the input quadruples. Pure stdlib.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# Backend name recorded in the stats file (`fn_ccn.source`,
# `fn_ccn.backend_by_language`). The treemap registers it as approximate.
BACKEND_NAME = "dart-scanner"

# Upper bound on bytes read per file, the same bound lib.complexity.generated_files uses.
# A file past it is scored on its first megabyte.
_READ_BYTES = 1024 * 1024

_DECISION_WORDS = frozenset({"if", "for", "while", "case", "catch"})
_DECISION_OPS = frozenset({"&&", "||", "??", "??="})
# A parameter list after one of these heads opens a block, not a function.
_NOT_A_NAME = frozenset({
    "if", "for", "while", "switch", "catch", "return", "super", "this",
    "assert", "await", "yield", "throw", "new", "const", "in", "is", "as",
})
# Heads whose parameter list is a condition, never a function's.
_CONTROL_HEADS = frozenset({"if", "for", "while", "switch", "catch"})
# Tokens allowed between a parameter list's `)` and its body.
_BODY_MODIFIERS = frozenset({"async", "sync", "*"})

# One code token with the whitespace before it folded into the same match, so
# a run of whitespace costs no loop iteration of its own (#967). `eof` takes
# trailing whitespace; `\s*` never backtracks, since `op` matches any other
# character.
_CODE_TOKEN = re.compile(
    r"\s*(?:"
    r"(?P<eof>\Z)"
    r"|(?P<lc>//)"
    r"|(?P<bc>/\*)"
    r"|(?P<raw>r(?:'''|\"\"\"|'|\"))"
    r"|(?P<str>'''|\"\"\"|'|\")"
    r"|(?P<id>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"|(?P<num>[0-9][0-9A-Za-z_]*)"
    r"|(?P<op>=>|&&|\|\||\?\?=|\?\?|\?\.|.))",
    re.DOTALL,
)
_BLOCK_COMMENT_EDGE = re.compile(r"/\*|\*/")

# The fast path (#967) scans a run of code up to the next character that can
# open a string or a comment, or that needs the per-token path: a quote, a
# `/`, a `?` (a ternary counts only after whitespace) or an `r` that starts a
# raw string (an `r` at a token start, directly before a quote).
_RUN_END = re.compile(r"['\"/?]|(?<![A-Za-z0-9_$])r(?=['\"])")
# The tokens such a run can hold, the `id`, `num` and `op` branches of
# `_CODE_TOKEN` without a group per kind, so `findall` returns plain strings.
# `\Z` takes trailing whitespace as an empty match.
_RUN_TOKEN = re.compile(
    r"\s*([A-Za-z_$][A-Za-z0-9_$]*|[0-9][0-9A-Za-z_]*|=>|&&|\|\||.|\Z)",
    re.DOTALL,
)
_WORD_START = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$")
_DIGITS = frozenset("0123456789")
_WS_CHAR = re.compile(r"\s")


def _string_end_pattern(quote: str, raw: bool) -> re.Pattern[str]:
    """What can end or interrupt a string opened by ``quote``."""
    parts = [re.escape(quote)]
    if len(quote) == 1:
        parts.append(r"\n")  # an unterminated one-line string ends at the line
    if not raw:
        parts += [r"\\", r"\$\{"]
    return re.compile("|".join(parts))


_STRING_END = {
    (q, raw): _string_end_pattern(q, raw)
    for q in ("'", '"', "'''", '"""') for raw in (False, True)
}


class _Fn:
    __slots__ = ("name", "order", "count")

    def __init__(self, name: str, order: int) -> None:
        self.name = name
        self.order = order  # ordinal of its opening: source order
        self.count = 0


def scan_dart_functions(text: str) -> list[tuple[str, float]]:
    """Return ``[(name, ccn)]`` for every function in ``text``, in source order.

    ``ccn`` is one plus the function's decision points; see the module
    docstring for what counts and what is recognised as a function.
    """
    return _Scanner(text).run()


class _Scanner:
    """One forward pass over a Dart source text; see ``scan_dart_functions``."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0
        self.done: list[_Fn] = []
        self.fns: list[_Fn] = []  # open functions, innermost last
        # One entry per open `{`: the function it opened, or None for a block.
        self.braces: list[_Fn | None] = []
        self.parens: list[str] = []  # the head token before each open `(`
        # Open named `=>` bodies: (function, brace depth, paren depth).
        self.arrows: list[tuple[_Fn, int, int]] = []
        # Open `${` interpolations: [string quote, raw flag, braces inside].
        # A mutable list (the brace count is bumped in place), so its slots
        # are heterogeneous: str, bool, int.
        self.interps: list[list[Any]] = []
        self.string: tuple[str, bool] | None = None  # (quote, raw) inside one
        self.prev = ""          # last significant code token
        self.prev_ws = False    # whitespace directly before the current token
        self.generic_name = ""  # identifier before the last `<` (name<T>(...))
        self.closed_head: str | None = None  # head of a just-closed `)`
        self.getter: str | None = None       # name after `get`, if pending
        self.opened = 0  # functions opened so far: the next one's ordinal

    def run(self) -> list[tuple[str, float]]:
        text, n = self.text, len(self.text)
        run_end = _RUN_END.search
        while self.pos < n:
            if self.string is not None:
                self._in_string()
            elif self.interps:
                self._code_token()  # a `}` may end the interpolation
            else:
                e = run_end(text, self.pos)
                end = n if e is None else e.start()
                if end > self.pos:
                    self._code_run(end)
                else:
                    self._code_token()
        self.done.extend(self.fns)  # unterminated bodies (truncated input)
        self.done.sort(key=lambda f: f.order)
        return [(f.name, float(1 + f.count)) for f in self.done]

    # -- lexing ------------------------------------------------------------

    def _in_string(self) -> None:
        assert self.string is not None
        m = _STRING_END[self.string].search(self.text, self.pos)
        if m is None:
            self.pos = len(self.text)
            return
        self.pos = m.end()
        edge = m.group()
        if edge == "\\":
            self.pos += 1  # skip the escaped character
            return
        if edge == "${":
            self.interps.append([*self.string, 0])
        # A closing quote, the newline ending a one-line string, or `${`.
        self.string = None
        self._reset("'")

    def _code_run(self, end: int) -> None:
        """Scan the code in ``text[pos:end]``, which holds no quote, slash,
        ``?`` or raw-string prefix and lies outside any interpolation.

        The hot path (#967): ``findall`` hands back every token as a plain
        string in one call, so a token costs no match object, and the word,
        parenthesis and brace cases, the commonest, are handled inline. Nothing in
        such a run can move the position or end the run early.
        """
        on_op, fns = self._on_op, self.fns
        parens, braces = self.parens, self.braces
        for tok in _RUN_TOKEN.findall(self.text, self.pos, end):
            first = tok[:1]
            if first in _WORD_START:
                if tok in _DECISION_WORDS and fns:
                    fns[-1].count += 1
                self.getter = tok if self.prev == "get" and _is_name(tok) \
                    else None
                if tok not in _BODY_MODIFIERS:
                    self.closed_head = None
                self.prev = tok
            elif first == "(":
                parens.append(
                    self.generic_name if self.prev == ">" else self.prev)
                self.prev, self.closed_head, self.getter = "(", None, None
            elif first == ")":
                head = parens.pop() if parens else ""
                if self.arrows:
                    self._close_arrows()
                self.prev, self.closed_head, self.getter = ")", head, None
            elif first == "{":
                name = self._body_name()
                braces.append(self._open(name) if name else None)
                self.prev, self.closed_head, self.getter = "{", None, None
            elif first in _DIGITS:
                self.prev, self.closed_head, self.getter = tok, None, None
            elif tok:  # the empty match at `end` is not a token
                on_op(tok, False)
        self.pos = end
        # Whitespace right before the next token, for a ternary `?`.
        self.prev_ws = _WS_CHAR.match(self.text, end - 1) is not None

    def _code_token(self) -> None:
        """Scan one code token at ``pos``, with any whitespace before it."""
        m = _CODE_TOKEN.match(self.text, self.pos)
        assert m is not None  # `eof` or the op branch always matches
        kind = m.lastgroup
        assert kind is not None
        tok = m.group(kind)
        self.pos = m.end()
        ws_before = self.prev_ws or m.start(kind) > m.start()
        self.prev_ws = False
        if kind == "id":
            self._on_word(tok)
        elif kind == "op":
            self._on_op(tok, ws_before)
        elif kind == "num":
            self._reset(tok)
        elif kind == "lc":
            nl = self.text.find("\n", self.pos)
            self.pos = len(self.text) if nl < 0 else nl
            self.prev_ws = True
        elif kind == "bc":
            self._skip_block_comment()
            self.prev_ws = True
        elif kind in ("raw", "str"):
            self.string = (tok[1:] if kind == "raw" else tok, kind == "raw")

    def _skip_block_comment(self) -> None:
        depth = 1  # Dart block comments nest
        while depth:
            e = _BLOCK_COMMENT_EDGE.search(self.text, self.pos)
            if e is None:
                self.pos = len(self.text)
                return
            depth += 1 if e.group() == "/*" else -1
            self.pos = e.end()

    # -- tokens ------------------------------------------------------------

    def _on_word(self, tok: str) -> None:
        if tok in _DECISION_WORDS:
            self._count()
        self.getter = tok if self.prev == "get" and _is_name(tok) else None
        if tok not in _BODY_MODIFIERS:
            self.closed_head = None
        self.prev = tok

    def _on_op(self, tok: str, ws_before: bool) -> None:
        if tok in _DECISION_OPS or (tok == "?" and ws_before):
            self._count()
        if tok == ")":
            head = self.parens.pop() if self.parens else ""
            if self.arrows:
                self._close_arrows()
            self.prev, self.closed_head, self.getter = ")", head, None
            return
        if tok == "}" and self.interps and self.interps[-1][2] == 0:
            quote, raw, _ = self.interps.pop()
            self.string = (quote, raw)  # the interpolation ended
            self._reset("'")
            return
        handler = _OP_HANDLERS.get(tok)
        if handler is not None:
            handler(self)
        keep = self.closed_head if tok in _BODY_MODIFIERS else None
        self.prev, self.closed_head, self.getter = tok, keep, None

    def _on_lt(self) -> None:
        self.generic_name = self.prev if _is_name(self.prev) else ""

    def _on_open_paren(self) -> None:
        self.parens.append(self.generic_name if self.prev == ">" else self.prev)

    def _on_open_brace(self) -> None:
        if self.interps:
            self.interps[-1][2] += 1
        name = self._body_name()
        self.braces.append(self._open(name) if name else None)

    def _on_close_brace(self) -> None:
        if self.interps:
            self.interps[-1][2] -= 1
        opened = self.braces.pop() if self.braces else None
        # Close the `=>` bodies opened inside this brace first: they sit above
        # its function on `fns`, so closing that function first would take the
        # O(depth) `remove` path on every `}` (#929).
        self._close_arrows()
        if opened is not None:
            self._close(opened)

    def _on_arrow(self) -> None:
        name = self._body_name()
        if name is not None:
            fn = self._open(name)
            self.arrows.append((fn, len(self.braces), len(self.parens)))

    def _on_semicolon(self) -> None:
        self._close_arrows(at_semicolon=True)

    # -- function frames ---------------------------------------------------

    def _count(self) -> None:
        if self.fns:
            self.fns[-1].count += 1

    def _reset(self, prev: str) -> None:
        self.prev, self.closed_head, self.getter = prev, None, None

    def _body_name(self) -> str | None:
        """The function name a `{` or `=>` body opened now would take, or None
        for a block or a closure folded into its enclosing function."""
        if self.getter is not None:
            return self.getter
        head = self.closed_head
        if head is not None and _is_name(head):
            return head
        if head is not None and not self.fns and head not in _CONTROL_HEADS:
            # A closure, a constructor body after an initializer list or an
            # operator overload, outside any function.
            return "<anonymous>"
        return None

    def _open(self, name: str) -> _Fn:
        # Functions open in source order, so an ordinal sorts like a position.
        fn = _Fn(name, self.opened)
        self.opened += 1
        self.fns.append(fn)
        return fn

    def _close(self, fn: _Fn) -> None:
        if self.fns and self.fns[-1] is fn:  # the usual case; O(1) when deep
            self.fns.pop()
        elif fn in self.fns:
            self.fns.remove(fn)
        self.done.append(fn)

    def _close_arrows(self, at_semicolon: bool = False) -> None:
        """Close named `=>` bodies whose expression ended: a `;` at the
        arrow's own depth, or a `)` / `}` that leaves that depth."""
        while self.arrows:
            _fn, b, p = self.arrows[-1]
            braces, parens = len(self.braces), len(self.parens)
            if not (braces < b or (braces == b and (
                    parens < p or (parens == p and at_semicolon)))):
                return
            self._close(self.arrows.pop()[0])


_OP_HANDLERS = {
    "<": _Scanner._on_lt,
    "(": _Scanner._on_open_paren,
    "{": _Scanner._on_open_brace,
    "}": _Scanner._on_close_brace,
    "=>": _Scanner._on_arrow,
    ";": _Scanner._on_semicolon,
}


def _is_name(tok: str) -> bool:
    return bool(tok) and (tok[0].isalpha() or tok[0] in "_$") \
        and tok not in _NOT_A_NAME


def dart_function_scores(path: Path) -> tuple[list[float], str | None]:
    """Score one Dart file: ``(per-function ccns, worst function's name)``.

    The name is the first function with the highest complexity, the rule
    ``lizard_scores`` uses; it is None when the file has no function. An
    unreadable file scores as having none.
    """
    try:
        with path.open("rb") as fh:
            data = fh.read(_READ_BYTES)
    except OSError:
        return [], None
    fns = scan_dart_functions(data.decode("utf-8", errors="replace"))
    if not fns:
        return [], None
    ccns = [c for _n, c in fns]
    return ccns, fns[ccns.index(max(ccns))][0]
