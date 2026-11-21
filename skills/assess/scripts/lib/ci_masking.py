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
- ``set +e`` in one script body (a block scalar such as ``run: |``, or one
  ``script:`` / ``before_script:`` list) masks the statements after it until a
  ``set -e``, unless a later statement of that body re-raises a status: it
  reads ``$?`` or ``PIPESTATUS``, or exits non-zero (``exit 1``,
  ``exit $status``), as ``.github/workflows/floor.yml``'s retry loop does.

Known limits: ``|| echo ...`` is not a no-op here, so it stays credited; a pipe
without ``pipefail`` (``pytest | tee log``) is credited; an ``if pytest; then``
wrapper is credited; ``allow_failure`` inherited through ``default:``,
``extends:`` or a YAML anchor is not resolved; a GitLab ``set +e`` in
``before_script:`` does not reach ``script:``; reusable-workflow and
composite-action internals are not read.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from lib.command_index import ci_line_text

_LEAD = re.compile(r"^\s*(?:-\s+)?(?:run:|script:)?\s*")
_KEY = re.compile(r"^([\w.-]+)\s*:(?:\s+(.*))?$")
_BLOCK_INDICATOR = re.compile(r"^[|>][-+0-9]*$")
_MASK_KEYS = frozenset({"continue-on-error", "allow_failure"})
_BODY_KEYS = frozenset({"run", "script", "before_script", "after_script"})
_NOOPS = frozenset({"true", "/bin/true", ":", "exit 0"})
_EXIT_NONZERO = re.compile(r"\bexit\s+(?:[1-9]\d*|\"?\$\{?\w+\}?\"?)")
_STATUS_READ = re.compile(r"\$\?|PIPESTATUS|" + _EXIT_NONZERO.pattern)
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
            self.masked.append(inherited or any(
                parsed[k].key in _MASK_KEYS and parsed[k].value.lower() == "true" for k in keys))

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
    """A later line names a captured file, and that line or one after it exits non-zero."""
    for idx, line in enumerate(later):
        if any(t in line for t in targets):
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


def unmasked_lines(text: str) -> list[str]:
    """``text``'s lines with every masked statement removed (an all-masked line is empty)."""
    lines = text.splitlines()
    tree = IndentTree(parse_lines(lines))
    spans = _logical_lines(lines)
    parsed = {span[0]: split_statements(_joined(lines, span)) for span in spans}
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
            if cut < 0:
                continue
            targets = _capture_targets(statement[:cut])
            if targets and _checked_later(targets, [lines[j] for j in tree.job(first) if j > span[-1]]):
                continue
            tail = statement[cut + 1:]
            changed.add(first)
            if tail:
                statements[k] = [("", tail[0][1]), *tail[1:]]
            else:
                masked.setdefault(first, set()).add(k)
        bodies.setdefault(tree.body(first), []).extend((first, k) for k in range(len(statements)))
    for refs in bodies.values():
        errexit = True
        state: list[bool] = []
        for first, k in refs:
            errexit = _errexit(parsed[first][k], errexit)
            state.append(not errexit)
        reraised = False
        for idx in range(len(refs) - 1, -1, -1):
            first, k = refs[idx]
            if state[idx] and not reraised:
                masked.setdefault(first, set()).add(k)
            reraised = reraised or bool(_STATUS_READ.search(_join(parsed[first][k])))
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
