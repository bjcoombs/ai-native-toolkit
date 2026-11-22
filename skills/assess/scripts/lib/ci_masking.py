"""Which CI commands cannot fail their job? (issue #775)

A check that runs in CI but whose exit code cannot fail the job is masked: it
runs, but it enforces nothing. ``unmasked_lines(text)`` returns one CI file's
lines with every masked shell statement removed, so any line classifier (the
``ci_checks`` check and lint rules today) counts only what can fail the job.
Every line keeps its index; a line whose statements are all masked is empty.

The rules read the text line by line with an indentation model, with no YAML
parser (the core has no YAML dependency). Only literal values count.

- Same line. In a statement (the text between top-level ``;``), a top-level
  ``||`` followed by a no-op (``true``, ``/bin/true``, ``:`` or ``exit 0``)
  masks every segment before it: ``cd x && pytest || true`` is masked; in
  ``pytest || true; ruff check .`` only ``pytest`` is, and in
  ``mkdir -p out || true && pytest`` ``pytest`` still counts. Quoted text and command
  substitutions (``$(...)``, backticks) hold no top-level operator, so
  ``OUT="$(ruff check . || true)"`` is not masked. Lines continued with ``\\``
  are joined first.
- Capture, not mask. A statement that sends the check's output to a file
  (``> out.txt``, ``&> out.txt``, ``| tee out.txt``; ``/dev/null`` and
  ``$GITHUB_STEP_SUMMARY`` do not count) is a capture when a later line of the
  same job names that file and it, or a line after it, exits non-zero
  (``exit 1``, ``exit $status``): the job checks the captured result itself
  (the grafana ``lint:circular`` pattern).
- GitHub Actions ``continue-on-error: true`` and GitLab ``allow_failure: true``
  mask every line of the mapping that holds the key and of the mappings below
  it: a step's keys (before or after ``run:``), a job's keys (beside
  ``runs-on:`` / ``script:``). A trailing ``# comment`` is ignored. An
  expression (``${{ matrix.experimental }}``), ``false`` or the GitLab map form
  (``allow_failure: {exit_codes: [...]}``) does not mask.
- Exit-zero flags (#919). A segment run with ``--exit-zero`` (ruff, flake8,
  pylint) or ``--issues-exit-code=0`` / ``--issues-exit-code 0`` (golangci-lint)
  is masked on its own: ``ruff check . --exit-zero && pytest`` keeps ``pytest``.
- A later ``exit 0`` (#919), in a GitHub ``run:`` body read with errexit off. A
  statement that is ``exit 0`` alone, at the top level of the body (not inside
  ``if`` / ``fi``, a loop, ``case``, ``{ }``, a heredoc or a multi-line quote such
  as ``python -c "..."``), masks the statements before it that ran with errexit
  off, unless the statement or one between it and the ``exit 0`` re-raises a
  status (``pytest || exit 1; exit 0`` keeps ``pytest``). Errexit is off after
  ``set +e``, under ``shell: pwsh`` / ``powershell`` / ``cmd`` / ``python`` (named,
  or as the first word of a custom command such as ``powershell -NoProfile -File
  {0}``), a custom ``bash`` / ``sh`` / ``dash`` / ``zsh`` command without ``-e``
  (``bash {0}``), or on a Windows ``runs-on`` with no ``shell:`` (``pwsh`` is the
  default there). Nesting is read with bash grammar only in a bash or sh body; in
  a pwsh, powershell, cmd or python body only the ``{ }`` / ``( )`` nesting is
  read, so an ``exit 0`` inside ``if (...) { }`` masks nothing; the step's ``shell:``, then
  the job's and the workflow's ``defaults.run.shell`` are read (``run_errexit``).
  GitHub's default ``bash -e`` and ``sh -e`` stop at the failing check before
  ``exit 0``, so ``pytest; exit 0`` there is enforced. GitLab checks each
  ``script:`` item's status, so it has no ``exit 0`` rule. ``x && exit 0``,
  ``echo exit 0`` and an early-exit guard (``if nothing-changed; then exit 0;
  fi``) mask nothing. A ``runs-on`` expression (``${{ matrix.os }}``) is not read.
- A literal never-true ``if:`` (#919): ``if: false``, ``if: 'false'`` or
  ``if: ${{ false }}`` on a step or job masks that mapping and everything under
  it, like ``continue-on-error: true``: it never runs. Any other ``if:``
  expression (``${{ 'false' }}`` is a non-empty string, so true) is not read.
- ``set +e`` in one script body (a block scalar such as ``run: |``, or one
  ``script:`` / ``before_script:`` list) masks the statements after it until a
  ``set -e``, unless a later statement of that body re-raises a status: it
  exits or returns a non-zero literal, a variable or ``$?`` (``exit 1``,
  ``exit $status``), as ``.github/workflows/floor.yml``'s retry loop does.

Known limits: ``|| echo ...`` is not a no-op here, so it stays credited; a pipe
without ``pipefail`` (``pytest | tee log``) is credited; an ``if pytest; then``
wrapper is credited; ``allow_failure`` inherited through ``default:``,
``extends:`` or a YAML anchor is not resolved; a GitLab ``set +e`` in
``before_script:`` does not reach ``script:``; reusable-workflow and
composite-action internals are not read. A check that is the last statement
after ``set +e`` is masked by design (issue #775 scope): bash would exit with
its status, but any trailing line would hide it.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath
from dataclasses import dataclass

from lib.command_index import ci_line_text

_LEAD = re.compile(r"^\s*(?:-\s+)?(?:run:|script:)?\s*")
_KEY = re.compile(r"^([\w.-]+)\s*:(?:\s+(.*))?$")
_BLOCK_INDICATOR = re.compile(r"^[|>][-+0-9]*$")
_MASK_KEYS = frozenset({"continue-on-error", "allow_failure"})
_BODY_KEYS = frozenset({"run", "script", "before_script", "after_script"})
_NOOPS = frozenset({"true", "/bin/true", ":", "exit 0"})
# A literal never-true ``if:`` (``false``, ``'false'``, ``${{ false }}``): the step or job never runs.
_NEVER = re.compile(r"""^(["']?)(?:false|\$\{\{\s*false\s*\}\})\1$""", re.IGNORECASE)
# Flags that make a linter exit 0 whatever it finds (``ruff check --exit-zero``,
# ``golangci-lint run --issues-exit-code=0``): the check runs but cannot fail.
_EXIT_ZERO_FLAG = re.compile(r"(?:^|\s)(?:--exit-zero|--issues-exit-code(?:=|\s+)0)(?=\s|$)")
# Shell words that open and close a compound command, read at the start of a segment.
_OPENERS = frozenset({"if", "while", "until", "for", "case", "select"})
_CLOSERS = frozenset({"fi", "done", "esac"})
_PREFIXES = frozenset({"then", "do", "else", "!"})
_EXIT_NONZERO = re.compile(r"\bexit\s+(?:[1-9]\d*|\"?\$\{?\w+\}?\"?)")
# A status re-raised: ``exit`` / ``return`` with a non-zero literal, a variable or ``$?``
# (``exit $?``, ``rc=$?; ... exit $rc``, ``[ $? -eq 0 ] || exit 1``). Reading ``$?`` alone
# (``echo "code $?"``) re-raises nothing.
_RERAISE = re.compile(r"\b(?:exit|return)\s+(?:[1-9]\d*|\"?\$(?:\?|\{?\w+\}?)\"?)")
_REDIRECT_TARGET = re.compile(r"(?:^|\s)(?:&|\d)?>>?\s*([^\s&;|<>]+)")
_TEE_TARGET = re.compile(r"^tee(?:\s+-\w+)*\s+([^\s;|]+)")
# Sinks no later step reads back: output sent there is not a capture.
_NOT_CAPTURED = re.compile(r"^(?:/dev/null|\$\{?GITHUB_STEP_SUMMARY\}?)$")


@dataclass(frozen=True)
class YamlLine:
    """One YAML line: ``indent`` is the first non-space column, ``level`` its key column."""

    indent: int
    level: int
    dash: bool
    key: str
    value: str
    structural: bool


def parse_lines(lines: list[str]) -> list[YamlLine]:
    parsed: list[YamlLine] = []
    owner_level = -1
    for raw in lines:
        stripped = raw.strip()
        indent = len(raw) - len(raw.lstrip(" "))
        if owner_level >= 0 and (not stripped or indent > owner_level):
            parsed.append(YamlLine(indent, indent, False, "", stripped, False))
            continue
        owner_level = -1
        if not stripped or stripped.startswith("#"):
            parsed.append(YamlLine(indent, indent, False, "", "", False))
            continue
        body = stripped
        dash = body == "-" or body.startswith("- ")
        level = indent
        if dash:
            body = body[1:].lstrip(" ")
            level = indent + (len(stripped) - len(body))
        match = _KEY.match(body)
        key, value = (match.group(1), strip_comment(match.group(2) or "")) if match else ("", body)
        if _BLOCK_INDICATOR.match(value):
            owner_level = level
        parsed.append(YamlLine(indent, level, dash, key, value, True))
    return parsed


def strip_comment(value: str) -> str:
    return re.sub(r"(?:^|\s+)#.*$", "", value).strip()


def _masks(line: YamlLine) -> bool:
    """``continue-on-error: true`` / ``allow_failure: true``, or a never-true ``if:`` (#919)."""
    if line.key in _MASK_KEYS:
        return line.value.lower() == "true"
    return line.key == "if" and _NEVER.match(line.value) is not None


class IndentTree:
    """Indentation scopes over one file's parsed lines, built in one pass."""

    def __init__(self, parsed: list[YamlLine]) -> None:
        self.lines = parsed
        self.owner: list[int | None] = []
        self.mapping_of: dict[int, int] = {}
        self.siblings: list[list[int]] = []
        self.parent: list[int | None] = []
        current: int | None = None
        stack: list[tuple[int, int]] = []
        last_key: dict[int, int] = {}
        for i, line in enumerate(parsed):
            if not line.structural:
                self.owner.append(current)
                continue
            self.owner.append(None)
            current = i if _BLOCK_INDICATOR.match(line.value) else None
            limit = line.indent if line.dash else line.level
            while stack and stack[-1][0] > limit:
                stack.pop()
            if not line.dash and stack and stack[-1][0] == line.level:
                mapping = stack[-1][1]
            else:
                mapping = len(self.siblings)
                self.siblings.append([])
                self.parent.append(last_key[stack[-1][1]] if stack else None)
                stack.append((line.level, mapping))
            self.siblings[mapping].append(i)
            self.mapping_of[i] = mapping
            last_key[mapping] = i
        self.masked: list[bool] = []
        for mapping, keys in enumerate(self.siblings):
            parent = self.parent[mapping]
            inherited = parent is not None and self.masked[self.mapping_of[parent]]
            self.masked.append(inherited or any(_masks(parsed[k]) for k in keys))

    def children(self, i: int) -> list[int]:
        """The key lines of the mappings directly under line ``i`` (each item of a sequence)."""
        return [k for m, keys in enumerate(self.siblings) if self.parent[m] == i for k in keys]

    def items(self, i: int) -> list[list[int]]:
        """The mappings directly under line ``i``, one list of key lines each."""
        return [keys for m, keys in enumerate(self.siblings) if self.parent[m] == i]

    def _node(self, i: int) -> int:
        owner = self.owner[i]
        return owner if owner is not None else i

    def ancestors(self, i: int) -> list[int]:
        """The parent key lines of line ``i``, innermost first."""
        out: list[int] = []
        parent = self.parent[self.mapping_of[self._node(i)]] if self._node(i) in self.mapping_of else None
        while parent is not None:
            out.append(parent)
            parent = self.parent[self.mapping_of[parent]]
        return out

    def subtree(self, i: int) -> range:
        """Line ``i`` and every line nested under it."""
        level = self.lines[i].level
        end = len(self.lines)
        for j in range(i + 1, len(self.lines)):
            line = self.lines[j]
            if line.structural and (line.indent < level or (line.indent == level and not line.dash)):
                end = j
                break
        return range(i, end)

    def step_or_job_masked(self, i: int) -> bool:
        node = self._node(i)
        return node in self.mapping_of and self.masked[self.mapping_of[node]]

    def body(self, i: int) -> int:
        """The line naming line ``i``'s shell body: a block owner, a ``script:`` key, or ``i``."""
        owner = self.owner[i]
        if owner is not None:
            return owner
        ancestors = self.ancestors(i)
        if ancestors and self.lines[ancestors[0]].key in _BODY_KEYS and not self.lines[ancestors[0]].value:
            return ancestors[0]
        return i

    def job(self, i: int) -> range:
        """The lines of line ``i``'s job: the mapping under ``jobs:``, else its top-level key."""
        ancestors = self.ancestors(i)
        for below, above in zip(ancestors, ancestors[1:]):
            if self.lines[above].key == "jobs" and self.lines[above].level == 0:
                return self.subtree(below)
        return self.subtree(ancestors[-1]) if ancestors else range(len(self.lines))


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


def _noop_cut(statement: list[tuple[str, str]]) -> int:
    """The index of the last ``|| <no-op>`` segment, or -1: it masks every segment before it."""
    cut = -1
    for k, (op, seg) in enumerate(statement):
        if op == "||" and " ".join(seg.split()) in _NOOPS:
            cut = k
    return cut


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


def _errexit(statement: list[tuple[str, str]], current: bool) -> bool:
    for _, seg in statement:
        words = seg.split()
        if not words or words[0] != "set":
            continue
        for prev, word in zip(words, words[1:]):
            if word in ("-o", "+o"):
                continue
            if word == "errexit":
                current = prev == "-o"
            elif word[:1] == "-" and "e" in word[1:]:
                current = True
            elif word[:1] == "+" and "e" in word[1:]:
                current = False
    return current


def _join(statement: list[tuple[str, str]]) -> str:
    return " ".join(f"{op} {seg}" if op else seg for op, seg in statement)


def _logical_lines(lines: list[str]) -> list[range]:
    """Physical line ranges, joining a line that ends in ``\\`` with the next."""
    out: list[range] = []
    i = 0
    while i < len(lines):
        end = i
        while end + 1 < len(lines) and lines[end].rstrip().endswith("\\"):
            end += 1
        out.append(range(i, end + 1))
        i = end + 1
    return out


def _joined(lines: list[str], span: range) -> str:
    parts = [ci_line_text(lines[span[0]])] + [lines[j].strip() for j in span[1:]]
    return " ".join(p.removesuffix("\\").strip() for p in parts)


def _drop_exit_zero(statement: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """``statement`` without the segments run with an exit-zero flag (``ruff check . --exit-zero``)."""
    kept = [(op, seg) for op, seg in statement if not _EXIT_ZERO_FLAG.search(seg)]
    return [("", kept[0][1]), *kept[1:]] if kept else []


def _depths(statements: list[list[tuple[str, str]]], keywords: bool = True) -> list[int]:
    """Per statement of one script body: how many compound commands it sits in.

    ``if`` / loops / ``case`` open on their first word and ``fi`` / ``done`` / ``esac``
    close; ``{ }`` and ``( )`` are counted across the whole segment, so a group that
    closes on the same line (``{ echo; }``, ``( cd sub && make build )``) nets to zero.
    Inside ``case`` a pattern's ``)`` is not counted. With ``keywords`` false (a pwsh,
    powershell, cmd or python body) only the ``{ }`` / ``( )`` delta is read, so
    ``if ($env:X) {`` ... ``}`` nests and a one-line ``if (...) { ... }`` nets to zero.
    """
    stack: list[str] = []
    out: list[int] = []
    for statement in statements:
        out.append(len(stack))
        for _, seg in statement:
            words = seg.split()
            while words and words[0] in _PREFIXES:
                words = words[1:]
            if keywords and words and words[0] in _CLOSERS and stack:
                stack.pop()
            elif keywords and words and words[0] in _OPENERS:
                stack.append(words[0])
            delta = seg.count("{") - seg.count("}")
            if not stack or stack[-1] != "case":
                delta += seg.count("(") - seg.count(")")
            for _ in range(delta):
                stack.append("{")
            for _ in range(-delta):   # a group closes only a group frame, never a keyword's
                if stack and stack[-1] == "{":
                    stack.pop()
    return out


def _exit_zero(statement: list[tuple[str, str]]) -> bool:
    """A statement that is ``exit 0`` alone: run unconditionally at the top level, it ends the
    body with success."""
    return len(statement) == 1 and " ".join(statement[0][1].split()) == "exit 0"


# Shells GitHub Actions runs without errexit: ``pwsh`` / ``powershell`` (a Windows runner's
# default), ``cmd`` and ``python``. ``bash`` and ``sh`` run with ``-e``.
_NO_ERREXIT_SHELLS = frozenset({"pwsh", "powershell", "cmd", "python"})
_POSIX_SHELLS = frozenset({"bash", "sh", "dash", "zsh"})
_ERREXIT_FLAG = re.compile(r"(?:^|\s)(?:-[a-zA-Z]*e[a-zA-Z]*|-o\s+errexit)(?=\s|$)")
_HEREDOC = re.compile(r"(?<!<)<<-?\s*(['\"]?)([A-Za-z_]\w*)\1")


def _shell_errexit(shell: str | None, windows: bool) -> tuple[bool, bool]:
    """``(errexit, bash grammar)`` for a ``run:`` body. ``shell`` is the step's, job's or
    workflow's ``shell:`` value, None when none is set.

    Unset: bash with errexit, unless ``runs-on`` names Windows (default ``pwsh``).
    ``bash`` / ``sh``: errexit. A shell in ``_NO_ERREXIT_SHELLS``, named or as the first
    word of a custom command (``powershell -NoProfile -File {0}``, a path and ``.exe``
    stripped): no errexit and not bash grammar. A custom ``bash`` / ``sh`` / ``dash`` /
    ``zsh`` command: errexit only with ``-e`` or ``-o errexit`` (``bash {0}`` has none).
    An expression or another program: errexit (not read).
    """
    if shell is None:
        return (False, False) if windows else (True, True)
    value = shell.strip().strip("'\"")
    words = value.split()
    head = PurePosixPath(words[0].replace("\\", "/")).name.lower().removesuffix(".exe") if words else ""
    if head in _NO_ERREXIT_SHELLS:
        return False, False
    if "{0}" in value and head in _POSIX_SHELLS:
        return _ERREXIT_FLAG.search(" ".join(words[1:]).replace("{0}", "")) is not None, True
    return True, True


def _child(tree: IndentTree, line: int, *path: str) -> int | None:
    """The key line under ``line`` reached by the key names in ``path``, or None."""
    for name in path:
        found = [k for k in tree.children(line) if tree.lines[k].key == name]
        if not found:
            return None
        line = found[0]
    return line


def _defaults_shell(tree: IndentTree, line: int) -> str | None:
    shell = _child(tree, line, "defaults", "run", "shell")
    return tree.lines[shell].value if shell is not None else None


def _runs_on_windows(tree: IndentTree, job: int) -> bool:
    runs_on = _child(tree, job, "runs-on")
    if runs_on is None:
        return False
    values = [tree.lines[runs_on].value] + [tree.lines[k].value for k in tree.children(runs_on)]
    text = " ".join(values).lower()
    return "${{" not in text and "windows" in text


def run_errexit(tree: IndentTree, body: int) -> tuple[bool, bool]:
    """``(errexit, bash grammar)`` for the GitHub ``run:`` body at line ``body`` (#919).

    Reads the step's ``shell:``, then the job's and the workflow's ``defaults.run.shell``,
    then the job's ``runs-on`` (a Windows runner defaults to ``pwsh``).
    """
    step = tree.siblings[tree.mapping_of[body]] if body in tree.mapping_of else []
    shell = next((tree.lines[k].value for k in step if tree.lines[k].key == "shell"), None)
    ancestors = tree.ancestors(body)
    job = next((below for below, above in zip(ancestors, ancestors[1:])
                if tree.lines[above].key == "jobs" and tree.lines[above].level == 0), None)
    if shell is None and job is not None:
        shell = _defaults_shell(tree, job)
    if shell is None:
        top = [i for i, line in enumerate(tree.lines)
               if line.structural and line.level == 0 and line.key == "defaults"]
        found = _child(tree, top[0], "run", "shell") if top else None
        shell = tree.lines[found].value if found is not None else None
    return _shell_errexit(shell, job is not None and _runs_on_windows(tree, job))


def _not_code(texts: list[str]) -> list[bool]:
    """Per line of one script body: does it start inside a heredoc or an open multi-line
    quote (``python -c "`` ... ``"``)? Such a line is data, not a shell statement."""
    out: list[bool] = []
    delimiter = ""
    quote = ""
    for text in texts:
        if delimiter:
            out.append(True)
            if text.strip() == delimiter:
                delimiter = ""
            continue
        out.append(bool(quote))
        i = 0
        while i < len(text):
            ch = text[i]
            if quote:
                if ch == "\\" and quote == '"':
                    i += 1
                elif ch == quote:
                    quote = ""
            elif ch in "'\"":
                quote = ch
            elif ch == "#" and (i == 0 or text[i - 1].isspace()):
                break
            i += 1
        match = _HEREDOC.search(text) if not quote else None
        if match:
            delimiter = match.group(2)
    return out


def _masked_in_body(refs: list[tuple[int, int]], parsed: dict[int, list[list[tuple[str, str]]]],
                    texts: dict[int, str], run_shell: tuple[bool, bool] | None) -> list[tuple[int, int]]:
    """The statements of one script body masked by ``set +e`` or by a later top-level ``exit 0``.

    ``refs`` are ``(first line, statement index)`` in order. A re-raise (``exit 1``,
    ``exit $?``) after a statement un-masks it; for ``exit 0`` its own re-raise does too.
    ``run_shell`` is ``(errexit, bash grammar)`` for a GitHub ``run:`` body, None for any
    other body. In a pwsh, powershell, cmd or python body only ``{ }`` / ``( )`` nesting is
    read (no bash keywords): GitHub's pwsh wrapper ends with ``exit $LASTEXITCODE``, so an
    ``exit 0`` inside ``if (...) { }`` is a guard, not a mask. Otherwise only a ``run:`` body read with errexit off (``set +e``, ``pwsh``, a Windows
    runner, ``shell: bash {0}``) is masked by a later ``exit 0``. With errexit on the failing
    check stops the step first, and GitLab checks each ``script:`` item's status.
    """
    errexit = True
    shell_errexit = True if run_shell is None else run_shell[0]
    bash = run_shell is None or run_shell[1]
    state: list[bool] = []
    off: list[bool] = []
    for first, k in refs:
        errexit = _errexit(parsed[first][k], errexit)
        shell_errexit = _errexit(parsed[first][k], shell_errexit)
        state.append(not errexit)
        off.append(run_shell is not None and not shell_errexit)
    data = dict(zip(texts, _not_code(list(texts.values())), strict=True))
    code = [not data.get(first, False) for first, _ in refs]
    depths = _depths([parsed[first][k] if keep else [] for (first, k), keep in zip(refs, code)],
                     keywords=bash)
    out: list[tuple[int, int]] = []
    reraised = False
    exited = False   # a later top-level ``exit 0`` ends the body with success
    exit_reraised = False
    for idx in range(len(refs) - 1, -1, -1):
        first, k = refs[idx]
        own = bool(_RERAISE.search(_join(parsed[first][k])))
        if (state[idx] and not reraised) or (exited and off[idx] and not (exit_reraised or own)):
            out.append((first, k))
        if code[idx] and depths[idx] == 0 and _exit_zero(parsed[first][k]):
            exited, exit_reraised = True, False
        reraised = reraised or own
        exit_reraised = exit_reraised or own
    return out


def unmasked_lines(text: str) -> list[str]:
    """``text``'s lines with every masked statement removed (an all-masked line is empty)."""
    lines = text.splitlines()
    tree = IndentTree(parse_lines(lines))
    spans = _logical_lines(lines)
    parsed = {span[0]: split_statements(_joined(lines, span)) for span in spans}
    span_at = {span[0]: span for span in spans}
    masked: dict[int, set[int]] = {}
    changed: set[int] = set()
    bodies: dict[int, list[tuple[int, int]]] = {}
    for span in spans:
        first = span[0]
        statements = parsed[first]
        if tree.step_or_job_masked(first):
            masked[first] = set(range(len(statements)))
            continue
        for k, statement in enumerate(statements):
            cut = _noop_cut(statement)
            if cut >= 0:
                targets = _capture_targets(statement[:cut])
                later = [lines[j] for j in tree.job(first) if j > span[-1]]
                if not (targets and _checked_later(targets, later)):
                    statement = statement[cut + 1:]
            left = _drop_exit_zero(statement)
            if left == statements[k]:
                continue
            changed.add(first)
            if left:
                statements[k] = left
            else:
                masked.setdefault(first, set()).add(k)
        bodies.setdefault(tree.body(first), []).extend((first, k) for k in range(len(statements)))
    for body, refs in bodies.items():
        run_shell = run_errexit(tree, body) if tree.lines[body].key == "run" else None
        texts = {first: _joined(lines, span_at[first]) for first in dict.fromkeys(f for f, _ in refs)}
        for first, k in _masked_in_body(refs, parsed, texts, run_shell):
            masked.setdefault(first, set()).add(k)
    out = list(lines)
    for span in spans:
        drop = masked.get(span[0], set())
        if not drop and span[0] not in changed:
            continue
        kept = [_join(s) for k, s in enumerate(parsed[span[0]]) if k not in drop]
        lead = _LEAD.match(lines[span[0]])
        out[span[0]] = (lead.group(0) if lead else "") + "; ".join(kept) if kept else ""
        for j in span[1:]:
            out[j] = ""
    return out
