"""A shell step's AI-disclosure check, read through its structure rather than its lines (issue #1599).

``parse(code)`` tokenizes a ``run:`` body or a script with ``shlex`` and builds a small tree:
lists of ``&&``/``||`` chains of pipelines, each command a simple command, an ``if``/``elif``/
``else``/``fi``, a ``{ }`` or ``( )`` group, or a ``for``/``while``/``until`` loop, each with
its trailing redirects (a loop's ``done < body.txt`` or ``done <<< "$BODY"`` input joins what it
iterates, and a ``while read -r line`` variable holds it). Comments,
``\\`` continuations and heredoc bodies are dropped first, and ``${{ }}`` expressions are kept
as one word. Anything it cannot place (a ``case``, a function definition, an unclosed quote or
``if``) makes ``parse`` return None, and the caller reads the code as no check at all: an
unsettled reading never counts.

``binds(tree, names)`` is the names that come to hold what was read: an assignment
(``BODY=...``, ``export BODY=...``), a stdout redirect or a ``tee`` target (``> body.json``;
never ``2>``, ``&>`` or ``/dev/*``), or a ``for`` variable, on a pipeline that reads
(``lib.ci.disclosure_terms.is_read``) or names a name already held. The caller iterates it to a
fixpoint, so ``B="$BODY"`` then a test of ``$B`` is read.

``acts(tree, names)`` is the acts (``EXIT``, ``LABEL``) that follow from a marker test:

- a **marker test** is a pipeline with a ``grep``, ``egrep``, ``fgrep`` or ``rg`` whose pattern
  names an AI marker and whose other words name what holds the read (``printf '%s' "$BODY" |
  grep -qi 'Assisted-by'``, ``grep -q 'AI' body.json``, ``<<< "$BODY"``), or a ``[[ ]]``,
  ``[ ]`` or ``test`` comparing such a name with ``==``, ``=``, ``!=`` or ``=~`` to a marker
  (``[[ "$BODY" == *"Assisted-by"* ]]``);
- an **act** is ``exit N`` with N non-zero, or a ``gh`` command with ``--add-label``;
- the act **follows** from the test when it is the pipeline right after it in its ``||``/``&&``
  chain, or the one after a message in between (``&& echo ok || { echo missing; exit 1; }``; a
  ``{ }`` group counts by its own unconditional commands); an unconditional command of the
  branches of the ``if`` it is the condition of; or an unconditional command later in the same
  list when the test's branch leaves the run first (a guard clause: ``if ... grep -q
  'Assisted-by'; then exit 0; fi`` then ``exit 1``, or ``&& exit 0``). A branch that only writes
  a message is no guard, so a later label does not follow from it; only the next line's
  ``exit 1`` after ``|| echo "..."`` does (the message-then-exit idiom). An act inside a command
  nested in the branch (``then npm test || exit 1``) belongs to that command and does not
  follow.

``heredocs(code)`` lists each heredoc opened outside quotes with the line that opens it, so the
caller can read a ``python3 - <<'PY'`` body as Python.

Inward-only imports: stdlib and ``lib.ci.disclosure_terms``. Tests:
``tests/test_ai_governance_condition.py``, ``tests/test_disclosure_readers.py``.
"""
from __future__ import annotations

import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from typing import Union

from lib.ci.disclosure_terms import EXIT, LABEL, has_marker, is_read, refers

_PUNCT = "();<>|&\n"
_OPS = ("<<<", "&&", "||", ";;", "<<", ">>", ">&", "&>", ";", "|", "&", "(", ")", "<", ">", "\n")
_REDIRECTS = frozenset({"<<<", "<<", ">>", ">&", "&>", "<", ">"})
_SEPARATORS = frozenset({";", "\n", "&"})
_HEREDOC = re.compile(r"(?<!<)<<(?!<)-?\s*(['\"]?)([A-Za-z_]\w*)\1")
_EXPR = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)
_ASSIGN = re.compile(r"([A-Za-z_]\w*)\+?=")
_GREPS = frozenset({"grep", "egrep", "fgrep", "rg"})
_GREP_ARG_OPTIONS = frozenset({"-f", "-m", "-A", "-B", "-C", "--max-count", "--file"})
_COMPARE = frozenset({"==", "=", "!=", "=~"})
_FDS = frozenset(str(d) for d in range(10))
_SAYS = frozenset({"echo", "printf", ":", "true"})
_DECLARE = frozenset({"export", "local", "declare", "readonly", "typeset"})


class ShellParseError(ValueError):
    pass


@dataclass(frozen=True)
class Simple:
    words: tuple[str, ...]
    redirects: tuple[tuple[str, str], ...]  # (operator, target)


@dataclass(frozen=True)
class Pipeline:
    commands: tuple["Command", ...]


@dataclass(frozen=True)
class Chain:
    pipelines: tuple[Pipeline, ...]  # joined by && or ||


@dataclass(frozen=True)
class If:
    clauses: tuple[tuple[tuple[Chain, ...], tuple[Chain, ...]], ...]  # (condition, branch)
    orelse: tuple[Chain, ...]


@dataclass(frozen=True)
class Group:
    body: tuple[Chain, ...]


@dataclass(frozen=True)
class Loop:
    var: str | None  # a for loop's variable
    words: tuple[str, ...]  # what a for loop iterates
    head: tuple[Chain, ...]  # a while or until loop's condition
    body: tuple[Chain, ...]


Command = Union[Simple, If, Group, Loop]
Seq = tuple[Chain, ...]


def _clean(code: str) -> str:
    """The code with continuations joined, ``${{ }}`` made one word, heredoc bodies and comments dropped."""
    code = _EXPR.sub(lambda m: "${{" + re.sub(r"\s+", "", m.group(1)) + "}}", code.replace("\\\n", " "))
    kept: list[str] = []
    end: str | None = None
    for line in code.split("\n"):
        if end is not None:
            end = None if line.strip() == end else end
            continue
        end = _heredoc(line)
        kept.append(line)
    return _strip_comments("\n".join(kept))


def _heredoc(line: str) -> str | None:
    """The terminator of a heredoc the line opens outside quotes (``<<EOF``, ``<<-'EOF'``), or None."""
    for found in _HEREDOC.finditer(line):
        before = line[:found.start()]
        if before.count("'") % 2 == 0 and (before.count('"') - before.count('\\"')) % 2 == 0:
            return found.group(2)
    return None


def _strip_comments(code: str) -> str:
    out: list[str] = []
    quote, i = "", 0
    while i < len(code):
        ch = code[i]
        if quote:
            if ch == "\\" and quote == '"':
                out.append(code[i:i + 2])
                i += 2
                continue
            quote = "" if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif ch == "\\":
            out.append(code[i:i + 2])
            i += 2
            continue
        elif ch == "#" and (i == 0 or code[i - 1] in " \t\n;&|("):
            while i < len(code) and code[i] != "\n":
                i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _split_ops(token: str) -> list[str]:
    out: list[str] = []
    while token:
        op = next(o for o in _OPS if token.startswith(o))
        out.append(op)
        token = token[len(op):]
    return out


def tokens(code: str) -> list[tuple[bool, str]]:
    """(is an operator, text) pairs; raises ``ShellParseError`` on an unclosed quote."""
    lex = shlex.shlex(_clean(code), posix=True, punctuation_chars=_PUNCT)
    lex.whitespace, lex.whitespace_split, lex.commenters = " \t\r", True, ""
    try:
        raw = list(lex)
    except ValueError as exc:
        raise ShellParseError(str(exc)) from exc
    out: list[tuple[bool, str]] = []
    for tok in raw:
        if tok and all(c in _PUNCT for c in tok):
            out.extend((True, op) for op in _split_ops(tok))
        else:
            out.append((False, tok))
    return out


class _Parser:
    def __init__(self, toks: list[tuple[bool, str]]) -> None:
        self.toks, self.i = toks, 0

    def peek(self) -> tuple[bool, str] | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def word(self, *texts: str) -> bool:
        tok = self.peek()
        return tok is not None and not tok[0] and tok[1] in texts

    def op(self, *texts: str) -> bool:
        tok = self.peek()
        return tok is not None and tok[0] and tok[1] in texts

    def expect_word(self, text: str) -> None:
        if not self.word(text):
            raise ShellParseError(f"expected {text}")
        self.i += 1

    def skip_newlines(self) -> None:
        while self.op("\n"):
            self.i += 1

    def seq(self, stops: frozenset[str] = frozenset(), close: bool = False) -> Seq:
        items: list[Chain] = []
        while True:
            while self.op(*_SEPARATORS):
                self.i += 1
            tok = self.peek()
            if tok is None or (tok[0] and tok[1] == ")" and close) or (not tok[0] and tok[1] in stops):
                return tuple(items)
            items.append(self.chain())

    def chain(self) -> Chain:
        pipelines = [self.pipeline()]
        while self.op("&&", "||"):
            self.i += 1
            self.skip_newlines()
            pipelines.append(self.pipeline())
        return Chain(tuple(pipelines))

    def pipeline(self) -> Pipeline:
        if self.word("!"):
            self.i += 1
        commands = [self.command()]
        while self.op("|"):
            self.i += 1
            self.skip_newlines()
            commands.append(self.command())
        return Pipeline(tuple(commands))

    def command(self) -> Command:
        tok = self.peek()
        if tok is None:
            raise ShellParseError("unexpected end")
        if tok == (True, "("):
            self.i += 1
            body = self.seq(close=True)
            self._close(")")
            return self._redirected(Group(body))
        if tok[0]:
            raise ShellParseError(f"unexpected {tok[1]!r}")
        handlers: dict[str, Callable[[], Command]] = {"if": self._if, "{": self._brace, "for": self._for,
                                                      "while": self._while, "until": self._while}
        if tok[1] in handlers:
            return self._redirected(handlers[tok[1]]())
        if tok[1] in {"then", "elif", "else", "fi", "do", "done", "}", "case", "esac", "function", "select"}:
            raise ShellParseError(f"cannot place {tok[1]}")
        return self._simple()

    def _redirected(self, command: Command) -> Command:
        """A compound command with its trailing redirects (``done < body.txt``, ``} > log``) read:
        a loop's input joins what it iterates, and a ``while read`` variable holds it."""
        inputs: list[str] = []
        while self.op(*_REDIRECTS):
            op = self.toks[self.i][1]
            self.i += 1
            target = self.peek()
            if target is None or target[0]:
                raise ShellParseError("redirect without a target")
            self.i += 1
            if op in ("<", "<<<"):
                inputs.append(target[1])
        if not isinstance(command, Loop) or not inputs:
            return command
        var = command.var or next((_program(c.words)[2][-1] for p in _pipelines(command.head) for c in _simples(p)
                                   if _program(c.words)[1] == "read" and _program(c.words)[2]), None)
        return Loop(var, command.words + tuple(inputs), command.head, command.body)

    def _close(self, text: str) -> None:
        if not self.op(text):
            raise ShellParseError(f"expected {text}")
        self.i += 1

    def _if(self) -> If:
        self.i += 1
        clauses: list[tuple[Seq, Seq]] = []
        while True:
            cond = self.seq(frozenset({"then"}))
            self.expect_word("then")
            branch = self.seq(frozenset({"elif", "else", "fi"}))
            clauses.append((cond, branch))
            if self.word("elif"):
                self.i += 1
                continue
            break
        orelse: Seq = ()
        if self.word("else"):
            self.i += 1
            orelse = self.seq(frozenset({"fi"}))
        self.expect_word("fi")
        return If(tuple(clauses), orelse)

    def _brace(self) -> Group:
        self.i += 1
        body = self.seq(frozenset({"}"}))
        self.expect_word("}")
        return Group(body)

    def _for(self) -> Loop:
        self.i += 1
        tok = self.peek()
        if tok is None or tok[0]:
            raise ShellParseError("for without a name")
        var, words = tok[1], list[str]()
        self.i += 1
        if self.word("in"):
            self.i += 1
            while (tok := self.peek()) is not None and not tok[0]:
                words.append(tok[1])
                self.i += 1
        return Loop(var, tuple(words), (), self._do())

    def _while(self) -> Loop:
        self.i += 1
        head = self.seq(frozenset({"do"}))
        return Loop(None, (), head, self._do())

    def _do(self) -> Seq:
        while self.op(*_SEPARATORS):
            self.i += 1
        self.expect_word("do")
        body = self.seq(frozenset({"done"}))
        self.expect_word("done")
        return body

    def _simple(self) -> Simple:
        words: list[str] = []
        redirects: list[tuple[str, str]] = []
        depth = 0
        while (tok := self.peek()) is not None:
            is_op, text = tok
            if is_op and depth == 0 and text in _REDIRECTS:
                if text.startswith(">") and words and words[-1] in _FDS:
                    text = words.pop() + text  # 2> or 2>>: shlex keeps the fd as the word before
                self.i += 1
                target = self.peek()
                if target is None or target[0]:
                    raise ShellParseError("redirect without a target")
                redirects.append((text, target[1]))
                self.i += 1
                continue
            if is_op and text == "(":
                if self.i + 1 < len(self.toks) and self.toks[self.i + 1] == (True, ")"):
                    raise ShellParseError("function definition")
                depth += 1
            elif is_op and text == ")":
                if depth == 0:
                    break
                depth -= 1
            elif is_op and depth == 0:
                break
            if not words and not is_op and text == "[[":
                return self._double_bracket()
            words.append(text)
            self.i += 1
        if depth:
            raise ShellParseError("unclosed (")
        return Simple(tuple(words), tuple(redirects))

    def _double_bracket(self) -> Simple:
        words: list[str] = []
        while (tok := self.peek()) is not None:
            words.append(tok[1])
            self.i += 1
            if not tok[0] and tok[1] == "]]":
                return Simple(tuple(words), ())
        raise ShellParseError("unclosed [[")


def parse(code: str) -> Seq | None:
    """The code's tree, or None when the reader cannot settle it (module docstring)."""
    try:
        parser = _Parser(tokens(code))
        tree = parser.seq()
    except (ShellParseError, RecursionError):  # deep nesting in untrusted code is unsettled, not a crash
        return None
    return tree if parser.peek() is None else None


def _children(command: Command) -> list[Seq]:
    if isinstance(command, If):
        return [part for clause in command.clauses for part in clause] + [command.orelse]
    if isinstance(command, Group):
        return [command.body]
    if isinstance(command, Loop):
        return [command.head, command.body]
    return []


def _pipelines(seq: Seq) -> list[Pipeline]:
    """Every pipeline in the tree, nested ones included."""
    out: list[Pipeline] = []
    for chain in seq:
        for pipeline in chain.pipelines:
            out.append(pipeline)
            for command in pipeline.commands:
                for child in _children(command):
                    out.extend(_pipelines(child))
    return out


def _simples(pipeline: Pipeline) -> list[Simple]:
    return [c for c in pipeline.commands if isinstance(c, Simple)]


def _program(words: tuple[str, ...]) -> tuple[int, str, tuple[str, ...]]:
    """A simple command's leading assignment count, program and arguments."""
    i = 0
    while i < len(words) and _ASSIGN.match(words[i]):
        i += 1
    return (i, words[i], words[i + 1:]) if i < len(words) else (i, "", ())


def _reads(command: Simple, names: frozenset[str]) -> bool:
    words = command.words + tuple(t for op, t in command.redirects if op.startswith("<"))
    return is_read(" ".join(command.words)) or any(refers(w, names) for w in words)


def binds(tree: Seq, names: frozenset[str]) -> set[str]:
    """The names a pipeline that reads, or names a held name, assigns or writes to (module docstring)."""
    out: set[str] = set()
    for pipeline in _pipelines(tree):
        if any(_reads(c, names) for c in _simples(pipeline)):
            for command in _simples(pipeline):
                out.update(_targets(command))
    for pipeline in _pipelines(tree):
        for loop in pipeline.commands:
            if isinstance(loop, Loop) and loop.var and any(refers(w, names) for w in loop.words):
                out.add(loop.var)
    return out


def _targets(command: Simple) -> set[str]:
    lead, program, args = _program(command.words)
    out = {m.group(1) for w in command.words[:lead] if (m := _ASSIGN.match(w))}
    if program in _DECLARE:
        out.update(m.group(1) for w in args if (m := _ASSIGN.match(w)))
    if program == "tee":
        out.update(a for a in args if not a.startswith("-"))
    out.update(t for op, t in command.redirects if op in (">", ">>") and not t.startswith("/dev/"))
    return out


def _grep_patterns(args: tuple[str, ...]) -> tuple[list[str], list[str]]:
    """A grep's patterns and its other arguments (the files it reads)."""
    patterns: list[str] = []
    rest: list[str] = []
    i, explicit = 0, False
    while i < len(args):
        arg = args[i]
        if arg in ("-e", "--regexp") or (arg.startswith("-") and not arg.startswith("--") and arg.endswith("e")
                                         and len(arg) > 2):
            if i + 1 < len(args):
                patterns.append(args[i + 1])
            explicit, i = True, i + 2
            continue
        if arg.startswith("--regexp="):
            patterns.append(arg.split("=", 1)[1])
            explicit = True
        elif arg in _GREP_ARG_OPTIONS:
            i += 1
        elif arg == "--":
            if not explicit and i + 1 < len(args):
                patterns.append(args[i + 1])
                i += 1
            explicit = True
        elif arg.startswith("-") and arg != "-":
            pass
        elif not explicit:
            patterns.append(arg)
            explicit = True
        else:
            rest.append(arg)
        i += 1
    return patterns, rest


def _marker_test(pipeline: Pipeline, names: frozenset[str]) -> bool:
    simples = _simples(pipeline)
    for command in simples:
        _, program, args = _program(command.words)
        if program in _GREPS:
            patterns, files = _grep_patterns(args)
            if not any(has_marker(p) for p in patterns):
                continue
            inputs = [t for op, t in command.redirects if op.startswith("<")] + files
            others = [c for c in simples if c is not command]
            if any(refers(w, names) for w in inputs) or any(_reads(c, names) for c in others):
                return True
        elif program in ("[[", "[", "test"):
            for k in range(1, len(args) - 1):
                if args[k] in _COMPARE and ((refers(args[k - 1], names) and has_marker(args[k + 1]))
                                            or (refers(args[k + 1], names) and has_marker(args[k - 1]))):
                    return True
    return False


def _act(command: Command) -> set[str]:
    if not isinstance(command, Simple):
        return set()
    _, program, args = _program(command.words)
    if program == "exit" and args and args[0].isdigit() and int(args[0]) != 0:
        return {EXIT}
    if program == "gh" and any(a == "--add-label" or a.startswith("--add-label=") for a in args):
        return {LABEL}
    return set()


def _direct(pipeline: Pipeline) -> set[str]:
    """The acts a pipeline performs itself: a lone act, or a group's unconditional acts."""
    if len(pipeline.commands) != 1:
        return set()
    command = pipeline.commands[0]
    if isinstance(command, Group):
        return _unconditional(command.body)
    return _act(command)


def _unconditional(seq: Seq) -> set[str]:
    return {a for chain in seq if len(chain.pipelines) == 1 for a in _direct(chain.pipelines[0])}


def _site(seq: Seq, names: frozenset[str]) -> bool:
    return any(_marker_test(p, names) for chain in seq for p in chain.pipelines)


def _ends(seq: Seq) -> bool:
    """Whether a branch ends the run: an unconditional ``exit``, with any code."""
    for chain in seq:
        if len(chain.pipelines) == 1 and len(chain.pipelines[0].commands) == 1:
            command = chain.pipelines[0].commands[0]
            if isinstance(command, Simple) and _program(command.words)[1] == "exit":
                return True
            if isinstance(command, Group) and _ends(command.body):
                return True
    return False


def _says(pipeline: Pipeline) -> bool:
    """Whether a pipeline only writes a message or does nothing (``echo``, ``printf``, ``:``, ``true``)."""
    return len(pipeline.commands) == 1 and isinstance(pipeline.commands[0], Simple) and _program(
        pipeline.commands[0].words)[1] in _SAYS


def _chain_acts(chain: Chain, names: frozenset[str]) -> tuple[set[str], bool, bool]:
    """A chain's acts that follow from a marker test in it, whether it is a guard (the branch after the
    test exits) and whether only messages follow the test (``|| echo "..."``, then a line ``exit 1``)."""
    found: set[str] = set()
    guard = said = False
    for k, pipeline in enumerate(chain.pipelines):
        if not _marker_test(pipeline, names):
            continue
        rest = chain.pipelines[k + 1:]
        said = bool(rest) and all(_says(p) for p in rest)
        # The act right after the test, or after a message in between (`&& echo ok || { ...; exit 1; }`).
        for step in rest[:2]:
            found |= _direct(step)
            single = step.commands[0] if len(step.commands) == 1 else None
            guard |= isinstance(single, Group) and _ends(single.body) or (
                isinstance(single, Simple) and _program(single.words)[1] == "exit")
            if not _says(step):
                break
    return found, guard, said


def acts(tree: Seq, names: frozenset[str]) -> set[str]:
    """The acts that follow from a marker test anywhere in the tree (module docstring)."""
    found: set[str] = set()
    armed = said = False
    for chain in tree:
        chained, guard, says = _chain_acts(chain, names)
        found |= chained
        for pipeline in chain.pipelines:
            for command in pipeline.commands:
                if isinstance(command, If):
                    found |= _if_acts(command, names)
                    guard |= _if_guard(command, names)
                elif isinstance(command, (Group, Loop)):
                    for child in _children(command):
                        found |= acts(child, names)
        if len(chain.pipelines) == 1:
            direct = _direct(chain.pipelines[0])
            found |= direct if armed else direct & {EXIT} if said else set()
        armed |= guard
        said = says
    return found


def _if_guard(node: If, names: frozenset[str]) -> bool:
    """Whether an ``if`` with a marker test ends the run on one of the branches the test leads to."""
    marked = False
    for cond, branch in node.clauses:
        marked |= _site(cond, names)
        if marked and _ends(branch):
            return True
    return marked and _ends(node.orelse)


def _if_acts(node: If, names: frozenset[str]) -> set[str]:
    found: set[str] = set()
    marked = False
    for cond, branch in node.clauses:
        marked |= _site(cond, names)
        found |= acts(cond, names) | acts(branch, names)
        if marked:
            found |= _unconditional(branch)
    found |= acts(node.orelse, names)
    if marked:
        found |= _unconditional(node.orelse)
    return found


def heredocs(code: str) -> list[tuple[str, str]]:
    """Each heredoc as (the line that opens it, its body), so a ``python3 - <<'PY'`` body can be read."""
    out: list[tuple[str, str]] = []
    opener, end, body = "", None, list[str]()
    for line in code.split("\n"):
        if end is not None:
            if line.strip() == end:
                out.append((opener, "\n".join(body)))
                end = None
            else:
                body.append(line)
            continue
        found = _heredoc(line)
        if found:
            opener, end, body = line, found, []
    return out
