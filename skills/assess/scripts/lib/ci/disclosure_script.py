"""A Python or JavaScript step's AI-disclosure check, read through its syntax tree (issue #1599).

Python goes through ``ast``; JavaScript (an ``actions/github-script`` ``script:`` or a ``node``
script) goes through a small tokenizer and statement reader. Code that does not parse reads as
no check: an unsettled reading never counts. Both readers answer the same two questions as
``lib.ci.disclosure_shell``:

- ``py_binds`` / ``js_binds``: the names that come to hold what was read (``is_read`` on the
  expression, or a name already held): an assignment's targets, a ``for`` target, a ``with``
  alias, and in JavaScript ``const``/``let``/``var`` (destructuring too, ``const { body } =
  context.payload.pull_request``) and a ``for (const c of commits)`` variable. A string equal to
  a held name counts as naming it, so ``os.environ["BODY"]``, ``os.getenv("BODY")`` and
  ``open("body.json")`` reach the ``env:`` key or the file. The caller iterates to a fixpoint.
- ``py_acts`` / ``js_acts``: the acts that follow from a marker test. A **marker test** is a
  literal naming an AI marker used as a test's pattern against a held name: Python ``"X" in
  body``, ``not in``, ``==``, ``re.search(r"X", body)`` (raw and byte strings are plain
  literals to ``ast``), ``re.compile(...)`` patterns held in a name, ``body.startswith("X")``;
  JavaScript ``body.includes('X')``, ``.match``, ``.search``, ``.startsWith``, ``.indexOf``,
  ``/X/.test(body)``. A name assigned from a marker test is one too (``ok = "X" in body``).
  An **act** is Python ``sys.exit`` / ``exit`` / ``raise SystemExit`` with a non-zero or
  message argument (``sys.exit(0)``, ``sys.exit()`` and a bare ``raise SystemExit`` end the
  run green), or a call passing ``--add-label``; JavaScript ``core.setFailed(...)``, ``throw``,
  ``process.exit(N)`` with N non-zero, or ``addLabels(...)``. The act **follows** when it is a
  statement of the branches of the ``if`` the test is the condition of, or an unconditional
  statement later in the same block when one of those branches leaves the flow first, or in
  JavaScript the operand right after the test in a ``||``/``&&`` chain (``body.includes('X') ||
  core.setFailed('x')``) (a guard
  clause: Python ``sys.exit``, ``exit``, ``quit``, ``raise`` or ``return``; JavaScript
  ``return``, ``throw`` or ``process.exit``, with any code). A branch that only logs is no guard. An act nested under another statement
  in the branch belongs to that statement. Function bodies (Python ``def``, JavaScript
  ``function`` and ``async function``) are read only when the code calls the function.

Inward-only imports: stdlib and ``lib.ci.disclosure_terms``. Tests:
``tests/test_ai_governance_condition.py``, ``tests/test_disclosure_readers.py``.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Union

from lib.ci.disclosure_terms import EXIT, LABEL, has_marker, is_read

_PY_SEARCH = frozenset({"search", "match", "fullmatch", "findall", "finditer"})
_PY_METHODS = frozenset({"startswith", "endswith", "find", "index", "count"})
_PY_EXITS = frozenset({"sys.exit", "exit", "quit"})


# --- Python ---------------------------------------------------------------------------------


def py_parse(code: str) -> ast.Module | None:
    try:
        return ast.parse(code)
    except (SyntaxError, ValueError):
        return None


def _text(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Constant) and isinstance(node.value, bytes):
        return node.value.decode("latin-1")
    return None


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _dotted(node.value) + "." + node.attr
    return ""


class _Py:
    def __init__(self, tree: ast.Module, code: str, names: frozenset[str]) -> None:
        self.tree, self.code, self.names = tree, code, names
        self.patterns: set[str] = set()  # names holding a marker pattern
        self.results: set[str] = set()  # names holding a marker test's result
        self.called = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        for _ in range(3):
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    targets = {t.id for t in node.targets if isinstance(t, ast.Name)}
                    if self._pattern(node.value):
                        self.patterns |= targets
                    if self.marker(node.value):
                        self.results |= targets

    def refers(self, node: ast.AST) -> bool:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and sub.id in self.names:
                return True
            if _text(sub) in self.names:
                return True
        return is_read(ast.get_source_segment(self.code, node) or "")

    def _pattern(self, node: ast.AST) -> bool:
        """A marker literal, a name holding one, or ``re.compile`` of one."""
        text = _text(node)
        if text is not None:
            return has_marker(text)
        if isinstance(node, ast.Name):
            return node.id in self.patterns
        return (isinstance(node, ast.Call) and _dotted(node.func).endswith("compile") and bool(node.args)
                and self._pattern(node.args[0]))

    def _test(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Compare):
            sides = [node.left, *node.comparators]
            for k, op in enumerate(node.ops):
                left, right = sides[k], sides[k + 1]
                if isinstance(op, (ast.In, ast.NotIn)) and self._pattern(left) and self.refers(right):
                    return True
                if isinstance(op, (ast.Eq, ast.NotEq)) and ((self._pattern(left) and self.refers(right))
                                                            or (self._pattern(right) and self.refers(left))):
                    return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr, owner, args = node.func.attr, node.func.value, node.args
            if attr in _PY_SEARCH and _dotted(owner) == "re" and len(args) >= 2:
                return self._pattern(args[0]) and self.refers(args[1])
            if attr in _PY_SEARCH and self._pattern(owner):
                return bool(args) and self.refers(args[0])
            if attr in _PY_METHODS and args:
                return self._pattern(args[0]) and self.refers(owner)
        return isinstance(node, ast.Name) and node.id in self.results

    def marker(self, expr: ast.AST) -> bool:
        return any(self._test(n) for n in ast.walk(expr))

    def act(self, stmt: ast.stmt) -> set[str]:
        call: ast.AST | None = None
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            call = stmt.value
            if _dotted(call.func) in _PY_EXITS:
                return {EXIT} if call.args and _red(call.args[0]) else set()
            if any((t := _text(n)) is not None and "--add-label" in t for n in ast.walk(call)):
                return {LABEL}
        if isinstance(stmt, ast.Raise) and isinstance(stmt.exc, ast.Call) and _dotted(stmt.exc.func) == "SystemExit":
            return {EXIT} if stmt.exc.args and _red(stmt.exc.args[0]) else set()
        return set()

    def block(self, body: list[ast.stmt]) -> set[str]:
        found: set[str] = set()
        armed = False
        for stmt in body:
            if isinstance(stmt, ast.If):
                armed |= self.marker(stmt.test) and (_py_ends(stmt.body) or _py_ends(stmt.orelse))
                found |= self._if(stmt)
                continue
            if armed:
                found |= self.act(stmt)
            for sub in _blocks(stmt, self.called):
                found |= self.block(sub)
        return found

    def _if(self, node: ast.If, marked: bool = False) -> set[str]:
        """An ``if``'s acts; ``marked`` when an ``if`` it is the ``elif`` of had the marker test."""
        marked = marked or self.marker(node.test)
        found = self.block(node.body)
        if marked:
            found |= {a for s in node.body for a in self.act(s)}
            found |= {a for s in node.orelse for a in self.act(s)}
        if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
            return found | self._if(node.orelse[0], marked)
        return found | self.block(node.orelse)


def _py_ends(stmts: list[ast.stmt]) -> bool:
    """Whether a branch leaves the flow: ``sys.exit``, ``exit`` or ``quit`` with any code, a
    ``raise`` or a ``return``, so a later act runs only on the other branch (a guard clause)."""
    return any(isinstance(s, (ast.Raise, ast.Return)) or (
        isinstance(s, ast.Expr) and isinstance(s.value, ast.Call) and _dotted(s.value.func) in _PY_EXITS) for s in stmts)


def _red(arg: ast.expr) -> bool:
    """Whether an exit argument ends the run red: a non-zero number or a message."""
    if isinstance(arg, ast.JoinedStr):
        return True
    if isinstance(arg, ast.Constant):
        value = arg.value
        if isinstance(value, bool) or value is None:
            return bool(value)
        return isinstance(value, str) or (isinstance(value, int) and value != 0)
    return False


def _blocks(stmt: ast.stmt, called: set[str]) -> list[list[ast.stmt]]:
    if isinstance(stmt, (ast.For, ast.While, ast.With)):
        return [stmt.body]
    if isinstance(stmt, ast.Try):
        return [stmt.body, stmt.orelse, stmt.finalbody]
    if isinstance(stmt, ast.FunctionDef) and stmt.name in called:
        return [stmt.body]
    return []


def py_binds(tree: ast.Module, code: str, names: frozenset[str]) -> set[str]:
    reader = _Py(tree, code, names)
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and node.value is not None:
            if reader.refers(node.value):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                out.update(n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name))
        elif isinstance(node, ast.For) and reader.refers(node.iter):
            out.update(n.id for n in ast.walk(node.target) if isinstance(n, ast.Name))
        elif isinstance(node, ast.With):
            for item in node.items:
                if isinstance(item.optional_vars, ast.Name) and reader.refers(item.context_expr):
                    out.add(item.optional_vars.id)
    return out


def py_acts(tree: ast.Module, code: str, names: frozenset[str]) -> set[str]:
    return _Py(tree, code, names).block(tree.body)


# --- JavaScript -----------------------------------------------------------------------------

_JS = re.compile(r"(?P<nl>\n)|(?P<ws>[ \t\r]+)|(?P<comment>//[^\n]*|/\*.*?\*/)"
                 r"|(?P<str>'(?:\\.|[^'\\\n])*'|\"(?:\\.|[^\"\\\n])*\"|`(?:\\.|[^`\\])*`)"
                 r"|(?P<id>[A-Za-z_$][\w$]*)|(?P<num>\d+(?:\.\d+)?)"
                 r"|(?P<op>===|!==|==|!=|&&|\|\||\?\.|=>|[{}()\[\];,.!=<>+\-*%&|?:~^/])", re.DOTALL)
_JS_REGEX = re.compile(r"/((?:\\.|\[(?:\\.|[^\]\\\n])*\]|[^/\\\n\[])+)/[a-z]*")
_REGEX_AFTER = frozenset({"(", ",", "=", ":", "[", "!", "&&", "||", "?", "{", "}", ";", "==", "===", "!=", "!=="})
_JS_METHODS = frozenset({"includes", "match", "search", "startsWith", "endsWith", "indexOf", "test"})
_CONTINUES = frozenset({"=", "+", "&&", "||", ",", "(", "[", "?", ":", ".", "?.", "=>", "!"})


@dataclass(frozen=True)
class Tok:
    kind: str  # nl, str, re, id, num, op
    text: str


def js_tokens(code: str) -> list[Tok] | None:
    out: list[Tok] = []
    i = 0
    while i < len(code):
        prev = next((t for t in reversed(out) if t.kind != "nl"), None)
        if code[i] == "/" and (prev is None or (prev.kind == "op" and prev.text in _REGEX_AFTER)
                               or (prev.kind == "id" and prev.text in ("return", "typeof"))):
            found = _JS_REGEX.match(code, i)
            if found:
                out.append(Tok("re", found.group(1)))
                i = found.end()
                continue
        m = _JS.match(code, i)
        if not m or m.lastgroup is None:
            return None
        kind = m.lastgroup
        if kind == "str":
            out.append(Tok("str", m.group(0)[1:-1]))
        elif kind not in ("ws", "comment"):
            out.append(Tok(kind, m.group(0)))
        i = m.end()
    return out


@dataclass(frozen=True)
class JIf:
    cond: tuple[Tok, ...]
    body: tuple["JStmt", ...]
    orelse: tuple["JStmt", ...]


@dataclass(frozen=True)
class JBlock:
    body: tuple["JStmt", ...]
    var: str | None = None  # a for...of or for...in variable
    head: tuple[Tok, ...] = ()  # what that loop iterates
    function: str | None = None  # a function declaration's name: its body is read only when called


@dataclass(frozen=True)
class JSimple:
    toks: tuple[Tok, ...]


JStmt = Union[JIf, JBlock, JSimple]


class JsParseError(ValueError):
    pass


class _JsParser:
    def __init__(self, toks: list[Tok]) -> None:
        self.toks, self.i = toks, 0

    def peek(self, skip_nl: bool = True) -> Tok | None:
        j = self.i
        while skip_nl and j < len(self.toks) and self.toks[j].kind == "nl":
            j += 1
        return self.toks[j] if j < len(self.toks) else None

    def take(self, text: str) -> bool:
        tok = self.peek()
        if tok is not None and tok.kind in ("op", "id") and tok.text == text:
            while self.toks[self.i] is not tok:
                self.i += 1
            self.i += 1
            return True
        return False

    def block(self, close: bool) -> tuple[JStmt, ...]:
        out: list[JStmt] = []
        while True:
            tok = self.peek()
            if tok is None:
                if close:
                    raise JsParseError("unclosed {")
                return tuple(out)
            if close and tok.kind == "op" and tok.text == "}":
                self.take("}")
                return tuple(out)
            if tok.kind == "op" and tok.text == ";":
                self.take(";")
                continue
            out.append(self.statement())

    def parens(self) -> tuple[Tok, ...]:
        if not self.take("("):
            raise JsParseError("expected (")
        start, depth = self.i, 1
        while self.i < len(self.toks):
            tok = self.toks[self.i]
            self.i += 1
            if tok.kind == "op" and tok.text in "([{":
                depth += 1
            elif tok.kind == "op" and tok.text in ")]}":
                depth -= 1
                if depth == 0:
                    return tuple(t for t in self.toks[start:self.i - 1] if t.kind != "nl")
        raise JsParseError("unclosed (")

    def statement(self) -> JStmt:
        if self.take("if"):
            cond = self.parens()
            body = self._body()
            orelse = self._body() if self.take("else") else ()
            return JIf(cond, body, orelse)
        if self.take("{"):
            return JBlock(self.block(close=True))
        if self.take("for") or self.take("while"):
            head = self.parens()
            names = [t.text for t in head if t.kind == "id"]
            var = names[1] if len(names) > 2 and names[0] in ("const", "let", "var") and names[2] in ("of", "in") else None
            return JBlock(self._body(), var, head[3:] if var else ())
        if self._function():
            tok = self.peek()
            if tok is None or tok.kind != "id":
                raise JsParseError("function without a name")
            self.take(tok.text)
            self.parens()
            if not self.take("{"):
                raise JsParseError("function without a body")
            return JBlock(self.block(close=True), function=tok.text)
        if self.take("try"):
            parts = list(self._body())
            if self.take("catch"):
                if (tok := self.peek()) is not None and tok.text == "(":
                    self.parens()
                self._body()
            if self.take("finally"):
                parts.extend(self._body())
            return JBlock(tuple(parts))
        return self._simple()

    def _function(self) -> bool:
        """Take ``function`` or ``async function`` when the statement opens with it."""
        j = self._skip_nl(self.i)
        if j < len(self.toks) and self.toks[j].text == "async":
            j = self._skip_nl(j + 1)
        if j < len(self.toks) and self.toks[j].kind == "id" and self.toks[j].text == "function":
            self.i = j + 1
            return True
        return False

    def _skip_nl(self, j: int) -> int:
        while j < len(self.toks) and self.toks[j].kind == "nl":
            j += 1
        return j

    def _body(self) -> tuple[JStmt, ...]:
        stmt = self.statement()
        return (stmt.body if isinstance(stmt, JBlock) and stmt.var is None and not stmt.head and stmt.function is None
                else (stmt,))

    def _simple(self) -> JSimple:
        out: list[Tok] = []
        depth = 0
        while self.i < len(self.toks):
            tok = self.toks[self.i]
            if depth == 0 and tok.kind == "op" and tok.text in (";", "}"):
                if tok.text == ";":
                    self.i += 1
                break
            if tok.kind == "nl":
                nxt = self.peek()
                if depth == 0 and out and out[-1].text not in _CONTINUES and not (
                        nxt is not None and nxt.kind == "op" and nxt.text in (".", "?.", "&&", "||", "?", ":")):
                    break
                self.i += 1
                continue
            if tok.kind == "op" and tok.text in "([{":
                depth += 1
            elif tok.kind == "op" and tok.text in ")]}":
                depth -= 1
            out.append(tok)
            self.i += 1
        if depth or not out:
            raise JsParseError("unbalanced statement")
        return JSimple(tuple(out))


def js_parse(code: str) -> tuple[JStmt, ...] | None:
    toks = js_tokens(code)
    if toks is None:
        return None
    try:
        return _JsParser(toks).block(close=False)
    except JsParseError:
        return None


def _js_walk(stmts: tuple[JStmt, ...]) -> list[JStmt]:
    out: list[JStmt] = []
    for stmt in stmts:
        out.append(stmt)
        if isinstance(stmt, JIf):
            out.extend(_js_walk(stmt.body) + _js_walk(stmt.orelse))
        elif isinstance(stmt, JBlock):
            out.extend(_js_walk(stmt.body))
    return out


def _js_refers(toks: tuple[Tok, ...], names: frozenset[str]) -> bool:
    return (any(t.kind == "id" and t.text in names for t in toks) or any(t.kind == "str" and t.text in names for t in toks)
            or is_read("".join(t.text for t in toks)))


def js_binds(stmts: tuple[JStmt, ...], names: frozenset[str]) -> set[str]:
    out: set[str] = set()
    for stmt in _js_walk(stmts):
        if isinstance(stmt, JBlock) and stmt.var and _js_refers(stmt.head, names):
            out.add(stmt.var)
        if not isinstance(stmt, JSimple):
            continue
        toks = stmt.toks[1:] if stmt.toks[0].text in ("const", "let", "var") else stmt.toks
        eq = next((k for k, t in enumerate(toks) if t.kind == "op" and t.text == "="), None)
        if eq is None:
            continue
        lhs, rhs = toks[:eq], toks[eq + 1:]
        if len(lhs) == 1 and lhs[0].kind == "id" and _js_refers(rhs, names):
            out.add(lhs[0].text)
        elif lhs and lhs[0].text == "{":
            source = "".join(t.text for t in rhs)
            for k, tok in enumerate(lhs):
                if tok.kind == "id" and (k + 1 == len(lhs) or lhs[k + 1].text != ":"):
                    key = lhs[k - 2].text if k >= 2 and lhs[k - 1].text == ":" else tok.text
                    if is_read(source + "." + key) or _js_refers(rhs, names):
                        out.add(tok.text)
    return out


def _receiver(toks: tuple[Tok, ...], dot: int) -> tuple[Tok, ...]:
    """The chain before ``toks[dot]`` (``a.b(c).d``), read back over names, dots and brackets."""
    j, depth = dot - 1, 0
    while j >= 0:
        tok = toks[j]
        if tok.kind == "op" and tok.text in ")]":
            depth += 1
        elif tok.kind == "op" and tok.text in "([":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and not (tok.kind in ("id", "str", "re") or tok.text in (".", "?.")):
            break
        j -= 1
    return toks[j + 1:dot]


def _call_args(toks: tuple[Tok, ...], open_at: int) -> tuple[Tok, ...]:
    depth = 0
    for k in range(open_at, len(toks)):
        if toks[k].text in "([{" and toks[k].kind == "op":
            depth += 1
        elif toks[k].text in ")]}" and toks[k].kind == "op":
            depth -= 1
            if depth == 0:
                return toks[open_at + 1:k]
    return toks[open_at + 1:]


class _Js:
    def __init__(self, stmts: tuple[JStmt, ...], names: frozenset[str]) -> None:
        self.names = names
        self.results: set[str] = set()
        self.called = {s.toks[k].text for s in _js_walk(stmts) if isinstance(s, JSimple)
                       for k in range(len(s.toks) - 1) if s.toks[k].kind == "id" and s.toks[k + 1].text == "("}
        for _ in range(3):
            for stmt in _js_walk(stmts):
                if isinstance(stmt, JSimple) and len(stmt.toks) > 2:
                    toks = stmt.toks[1:] if stmt.toks[0].text in ("const", "let", "var") else stmt.toks
                    if toks[0].kind == "id" and toks[1].text == "=" and self.marker(toks[2:]):
                        self.results.add(toks[0].text)

    def marker(self, toks: tuple[Tok, ...]) -> bool:
        for k in range(len(toks) - 2):
            if toks[k].text not in (".", "?.") or toks[k + 1].text not in _JS_METHODS or toks[k + 2].text != "(":
                continue
            receiver, args = _receiver(toks, k), _call_args(toks, k + 2)
            patterned = [t for t in receiver if t.kind in ("str", "re")]
            if patterned and len(receiver) == 1 and has_marker(patterned[0].text) and _js_refers(args, self.names):
                return True
            first = args[0] if args else None
            if (first is not None and first.kind in ("str", "re") and has_marker(first.text)
                    and _js_refers(receiver, self.names)):
                return True
        return any(t.kind == "id" and t.text in self.results for t in toks)

    def act(self, stmt: JStmt) -> set[str]:
        if not isinstance(stmt, JSimple):
            return set()
        toks = stmt.toks
        texts = [t.text for t in toks]
        if texts[0] == "throw":
            return {EXIT}
        start = 1 if texts[0] == "await" else 0
        for k in range(start, len(toks) - 1):
            if toks[k].kind == "id" and toks[k + 1].text == "(":
                if toks[k].text == "setFailed":
                    return {EXIT}
                if toks[k].text == "addLabels":
                    return {LABEL}
                if toks[k].text == "exit" and k >= 2 and texts[k - 2] == "process":
                    arg = _call_args(toks, k + 1)
                    return {EXIT} if len(arg) == 1 and arg[0].kind == "num" and float(arg[0].text) != 0 else set()
        return set()

    def block(self, stmts: tuple[JStmt, ...]) -> set[str]:
        found: set[str] = set()
        armed = False
        for stmt in stmts:
            if isinstance(stmt, JIf):
                marked = self.marker(stmt.cond)
                armed |= marked and (_js_ends(stmt.body) or _js_ends(stmt.orelse))
                found |= self.block(stmt.body) | self.block(stmt.orelse)
                if marked:
                    found |= {a for s in stmt.body + stmt.orelse for a in self.act(s)}
                continue
            if armed:
                found |= self.act(stmt)
            if isinstance(stmt, JSimple):
                found |= self._short_circuit(stmt.toks)
            if isinstance(stmt, JBlock) and (stmt.function is None or stmt.function in self.called):
                found |= self.block(stmt.body)
        return found

    def _short_circuit(self, toks: tuple[Tok, ...]) -> set[str]:
        """The act right after a marker test in a ``||``/``&&`` chain (``body.includes('X') ||
        core.setFailed('x')``), as the shell reader reads one."""
        parts: list[tuple[Tok, ...]] = []
        depth, start = 0, 0
        for k, tok in enumerate(toks):
            if tok.kind == "op" and tok.text in "([{":
                depth += 1
            elif tok.kind == "op" and tok.text in ")]}":
                depth -= 1
            elif depth == 0 and tok.kind == "op" and tok.text in ("||", "&&"):
                parts.append(toks[start:k])
                start = k + 1
        parts.append(toks[start:])
        return {a for k in range(len(parts) - 1) if self.marker(parts[k]) and parts[k + 1]
                for a in self.act(JSimple(parts[k + 1]))}


def _js_ends(stmts: tuple[JStmt, ...]) -> bool:
    """Whether a branch leaves the flow: ``return``, ``throw`` or ``process.exit``, with any code."""
    for stmt in stmts:
        if isinstance(stmt, JSimple):
            texts = [t.text for t in stmt.toks]
            if texts[0] in ("return", "throw") or any(texts[k:k + 3] == ["process", ".", "exit"] for k in range(len(texts))):
                return True
    return False


def js_acts(stmts: tuple[JStmt, ...], names: frozenset[str]) -> set[str]:
    return _Js(stmts, names).block(stmts)
