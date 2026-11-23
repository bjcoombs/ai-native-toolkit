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
  are joined first. Inside a ``( )`` subshell the same rule masks back to the group's
  start (#1008): ``(cd sub && pytest || true)`` exits 0 and is masked, ``pytest && (echo x
  || true)`` keeps ``pytest``. The statement rules live in ``lib.ci_statements``.
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
  as ``python -c "..."``; a ``{``, ``(`` or ``<<EOF`` inside quotes opens nothing), masks the statements before it that ran with errexit
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

- Errexit-exempt positions (#936, ``lib.ci_exempt``). With errexit on, bash does not stop
  at a failing command on the left of ``&&`` / ``||``, in a pipeline but its last command
  (pipefail off: the default ``bash -e {0}`` and ``sh``; on for ``shell: bash``, a custom
  ``-o pipefail`` and after ``set -o pipefail`` in the body), in an ``if`` / ``elif`` /
  ``while`` condition or after ``!``. A later top-level ``exit 0`` masks those segments
  alone, with the same re-raise rule: ``pytest && echo ok; exit 0`` and ``pytest | tee
  log; exit 0`` are masked, ``cd sub && pytest; exit 0`` and ``if ! pytest; then exit 1;
  fi; exit 0`` are not. After an exit-zero segment that ends its pipeline, the ``||``
  pipelines up to the next ``&&`` never run: ``ruff check . --exit-zero || pytest`` drops
  ``pytest``.
- A folded block scalar (``run: >``) is read as YAML folds it: adjacent non-blank lines at
  the block's indentation are one line, so ``pytest`` and ``|| true`` on two lines are one
  masked statement, and ``pytest`` then ``exit 0`` is the one command ``pytest exit 0``.

- Not measured (#1008, ``lib.ci_unsure``). ``mask_lines(text)`` returns ``(unmasked,
  unsure)``: the forms whose failure the step may hide are left out of the unmasked lines
  and returned on their own, so ``ci_checks`` and ``coverage_ci`` read them null
  (``check_may_pass``), never credit: a ``||`` fallback that can pass (``pytest || echo
  failed``), an ``if`` / ``while`` condition with no re-raise, an ``until`` loop that
  breaks, a check before ``&&`` / ``||`` with a later statement in the body (in a ``{ }``
  group, an ``if`` or a loop body too; not inside a subshell or a function, which return
  the status), and a ``{ }`` group or function called before ``&&`` / ``||`` or in a
  condition. ``unmasked_lines(text)`` is ``mask_lines(text)[0]``.
- A pipe without pipefail (#1061). In a GitHub bash or sh ``run:`` body with pipefail off
  (the default ``bash -e {0}``, ``shell: sh``, no ``set -o pipefail`` before it), every
  pipeline member but the last is masked (``ci_exempt.piped_members``), and so is every
  segment of a ``{ }`` / ``( )`` group piped on (``piped_groups``: ``{ pytest; } | tee log``,
  ``(pytest; echo x) | tee log``, which run in a pipeline subshell): the pipeline takes
  the last command's status, so ``pytest | tee log`` exits 0 on a failing check. A capture
  (above: a later line of the job names the file and exits non-zero) stays credited. When the
  statement or a later one reads ``PIPESTATUS`` or a later one re-raises a status (``pytest
  | tee log; exit ${PIPESTATUS[0]}``, a capture checked later), it reads null instead. With
  pipefail on (``shell: bash``, a custom ``-o pipefail``) it stays credited; GitLab runs its
  script with pipefail, so it is not read there.
- A function no statement of its GitHub bash or sh ``run:`` body calls (#1061,
  ``ci_unsure.uncalled_functions``): its body is masked, since ``f() { pytest; }`` alone runs
  nothing. A call is the name as the command a segment runs (``f``, ``} && f``, ``time f``,
  ``out=$(f)``); the name in echoed text (``echo f``) is none. Named only as another command's
  argument (``retry 3 f``), the body reads null. GitLab joins ``before_script:`` and
  ``script:``, so it is not read there.

Known limits: a check before ``&&`` inside a subshell or function with
a later statement there is credited; a ``{ }`` group or function called before ``&&`` reads
null even where its last command is the check and nothing follows (bash would exit 1); a
GitLab body is read for the ``||`` fallback only; a compound command in an exempt position
(``fi || true``) does not exempt its body; ``allow_failure`` inherited through
``default:``, ``extends:`` or a YAML anchor is not resolved; a GitLab ``set +e`` in
``before_script:`` does not reach ``script:``; reusable-workflow and composite-action
internals are not read. A check that is the last statement after ``set +e`` is masked by
design (issue #775 scope): bash would exit with its status, but any trailing line would
hide it.

Which CI shell statements cannot fail their job (issue #775), with no YAML parser
(an indentation model over the lines). `unmasked_lines(text) -> list[str]` is the
result shape other CI classifiers build on: one entry per input line, same index,
with every masked statement removed (`pytest || true; ruff check .` becomes
`ruff check .`) and a line whose statements are all masked replaced by `""`. A
classifier counts what can fail the job by running its line test over this list,
and the masked count as its count over the original lines minus that.
`split_statements(text)` splits one shell line into top-level `;` statements of
`(operator before, segment)` pairs; quotes, `$(...)` and backticks hold no
operator. Rules: a top-level `|| true` / `|| :` / `|| /bin/true` / `|| exit 0`
masks the segments before it in its statement (`mkdir -p x || true && pytest`
keeps `pytest`), unless they send output to a file (`>`, `&>`, `| tee`; not
`/dev/null` or `$GITHUB_STEP_SUMMARY`) that a later line of the same job names,
with a non-zero exit at or after it (a capture, the grafana `lint:circular`
pattern); `continue-on-error: true` or `allow_failure:
true` (literal, a trailing comment ignored) masks the mapping that holds it and
everything under it (a step or a job); `set +e` masks the rest of its script body
(a block scalar, or one `script:` list) until `set -e`, unless a later statement
exits or returns a non-zero literal, a variable or `$?` (`exit $?`, `rc=$?; exit $rc`,
`[ $? -eq 0 ] || exit 1`; `echo "code $?"` alone re-raises nothing). Issue #919 adds
three: a segment with an exit-zero flag (`--exit-zero`, `--issues-exit-code=0` or `0` as
the next word) is masked on its own; in a GitHub `run:` body with errexit off (`set +e`,
`shell: pwsh` / `powershell` / `cmd` / `python`, named or as a custom command's first
word, a custom `bash` / `sh` / `dash` / `zsh` command without `-e` such as `bash {0}`, or
a Windows `runs-on` with no `shell:`; `run_errexit(tree, body)` reads the
step's `shell:`, then the job's and workflow's `defaults.run.shell`), a top-level `exit 0`
statement (outside `if`, loops, `case`, `{ }`, `( )`, heredocs and multi-line quotes, with bash
grammar read only in a bash or sh body and only `{ }` / `( )` nesting in a pwsh,
powershell, cmd or python body; `_depths`, `_not_code`; a `{`, `(` or `<<EOF` inside
quotes opens nothing) masks the statements before it, unless one of them or a statement
between re-raises a status. Issue #936 adds the errexit-exempt positions
(`ci_exempt.py`): with errexit on, a later top-level `exit 0` masks the segments bash
does not stop on (the left of `&&` / `||`, a pipeline but its last command with pipefail
off, an `if` / `elif` / `while` condition, a `!` pipeline). `run_errexit` returns
`(errexit, bash grammar, pipefail)`: pipefail is on for `shell: bash` and a custom
`-o pipefail`, off for the default `bash -e {0}` and `sh`, and `set -o pipefail` /
`set +o pipefail` in the body move it; a statement in or opening a `( )` / `{ }` group
is not read (`_depths` second value). After an exit-zero segment that ends its
pipeline, the `||` pipelines up to the next `&&` are dropped (`ruff check . --exit-zero
|| pytest` drops `pytest`), and a folded block scalar (`run: >`) is folded into one
line before any rule reads it. The default `bash -e` stops at the failing check first, so
`pytest; exit 0` there is enforced, and GitLab, which checks each `script:` item, has no
`exit 0` rule; a literal `if: false`,
`if: 'false'` or `if: ${{ false }}` masks its step or job like `continue-on-error: true`.
`\\` continuations are joined first.
Issue #1008 adds `mask_lines(text) -> (unmasked, unsure)`, the forms that read null
(`lib.ci_unsure`). Issue #1061 masks a pipeline member but the last with pipefail off
(null with a later `PIPESTATUS` read or re-raise) and an uncalled function's body in a
GitHub bash or sh body. Known limits (module docstring): an exempt check inside a subshell or function with a later statement there, `allow_failure` through `default:` / `extends:` / anchors, a
`before_script:` `set +e` reaching `script:`, reusable-workflow internals. Tests:
`tests/test_ci_checks.py`. `IndentTree` / `parse_lines` (the indentation model) are
shared with `ci_triggers.py`. Tests for the #919 and #936 rules: `tests/test_ci_false_credit.py`.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import PurePosixPath
from dataclasses import dataclass

from lib.ci_exempt import (
    condition_flags, exempt_segments, pipefail_after, piped_groups, piped_members, shell_pipefail, unquoted)
from lib.ci_statements import (
    _capture_targets, _checked_later, _join, _pick, _same_line_keep, split_statements)
from lib.ci_unsure import (
    group_call_unsure, opaque_flags, or_arm_unsure, uncalled_functions, until_break_flags)
from lib.command_index import ci_line_text

__all__ = ["IndentTree", "parse_lines", "split_statements", "strip_comment", "unmasked_lines"]

_LEAD = re.compile(r"^\s*(?:-\s+)?(?:run:|script:)?\s*")
_KEY = re.compile(r"^([\w.-]+)\s*:(?:\s+(.*))?$")
_BLOCK_INDICATOR = re.compile(r"^[|>][-+0-9]*$")
_MASK_KEYS = frozenset({"continue-on-error", "allow_failure"})
_BODY_KEYS = frozenset({"run", "script", "before_script", "after_script"})
# A literal never-true ``if:`` (``false``, ``'false'``, ``${{ false }}``): the step or job never runs.
_NEVER = re.compile(r"""^(["']?)(?:false|\$\{\{\s*false\s*\}\})\1$""", re.IGNORECASE)
# Shell words that open and close a compound command, read at the start of a segment.
_OPENERS = frozenset({"if", "while", "until", "for", "case", "select"})
_CLOSERS = frozenset({"fi", "done", "esac"})
_PREFIXES = frozenset({"then", "do", "else", "!"})
# A status re-raised: ``exit`` / ``return`` with a non-zero literal, a variable or ``$?``
# (``exit $?``, ``rc=$?; ... exit $rc``, ``[ $? -eq 0 ] || exit 1``). Reading ``$?`` alone
# (``echo "code $?"``) re-raises nothing.
_RERAISE = re.compile(r"\b(?:exit|return)\s+(?:[1-9]\d*|\"?\$(?:\?|\{?\w+\}?)\"?)")


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


def _folds(tree: IndentTree, lines: list[str]) -> list[bool]:
    """Per line: does a folded block scalar (``run: >``, ``>-``) join it with the next?

    YAML folds two adjacent non-blank lines at the block's own indentation into one line;
    a blank line or a more-indented line keeps its line break (#936).
    """
    out = [False] * len(lines)
    base: dict[int, int] = {}
    for i in range(len(lines) - 1):
        owner = tree.owner[i]
        if owner is None or not tree.lines[owner].value.startswith(">") or tree.owner[i + 1] != owner:
            continue
        if not lines[i].strip():
            continue
        indent = base.setdefault(owner, tree.lines[i].indent)
        out[i] = bool(lines[i + 1].strip()) and tree.lines[i].indent == tree.lines[i + 1].indent == indent
    return out


def _logical_lines(lines: list[str], tree: IndentTree) -> list[range]:
    """Physical line ranges, joining a line that ends in ``\\``, or that a folded block
    scalar folds, with the next."""
    folds = _folds(tree, lines)
    out: list[range] = []
    i = 0
    while i < len(lines):
        end = i
        while end + 1 < len(lines) and (lines[end].rstrip().endswith("\\") or folds[end]):
            end += 1
        out.append(range(i, end + 1))
        i = end + 1
    return out


def _joined(lines: list[str], span: range) -> str:
    parts = [ci_line_text(lines[span[0]])] + [lines[j].strip() for j in span[1:]]
    return " ".join(p.removesuffix("\\").strip() for p in parts)


def _depths(statements: list[list[tuple[str, str]]], keywords: bool = True) -> tuple[list[int], list[bool]]:
    """Per statement of one script body: how many compound commands it sits in, and whether
    it sits in, or opens, a ``{ }`` / ``( )`` group (a subshell or a function body).

    ``if`` / loops / ``case`` open on their first word and ``fi`` / ``done`` / ``esac``
    close; ``{ }`` and ``( )`` are counted across the whole segment, so a group that
    closes on the same line (``{ echo; }``, ``( cd sub && make build )``) nets to zero.
    Inside ``case`` a pattern's ``)`` is not counted. With ``keywords`` false (a pwsh,
    powershell, cmd or python body) only the ``{ }`` / ``( )`` delta is read, so
    ``if ($env:X) {`` ... ``}`` nests and a one-line ``if (...) { ... }`` nets to zero.
    ``$(`` and ``${`` open no group for the second value.
    """
    stack: list[str] = []
    out: list[int] = []
    grouped: list[bool] = []
    for statement in statements:
        out.append(len(stack))
        own = unquoted(_join(statement)).replace("$(", "").replace("${", "")
        grouped.append("{" in stack or "(" in own or "{" in own)
        for _, seg in statement:
            words = seg.split()
            while words and words[0] in _PREFIXES:
                words = words[1:]
            if keywords and words and words[0] in _CLOSERS and stack:
                stack.pop()
            elif keywords and words and words[0] in _OPENERS:
                stack.append(words[0])
            code = unquoted(seg, escapes=keywords)   # ``echo "{"`` opens nothing (#936)
            delta = code.count("{") - code.count("}")
            if not stack or stack[-1] != "case":
                delta += code.count("(") - code.count(")")
            for _ in range(delta):
                stack.append("{")
            for _ in range(-delta):   # a group closes only a group frame, never a keyword's
                if stack and stack[-1] == "{":
                    stack.pop()
    return out, grouped


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


def _shell_errexit(shell: str | None, windows: bool) -> tuple[bool, bool, bool]:
    """``(errexit, bash grammar, pipefail)`` for a ``run:`` body. ``shell`` is the step's,
    job's or workflow's ``shell:`` value, None when none is set.

    Unset: ``bash -e {0}`` (errexit, no pipefail), unless ``runs-on`` names Windows
    (default ``pwsh``). ``bash``: ``bash --noprofile --norc -eo pipefail {0}``; ``sh``:
    ``sh -e {0}`` (no pipefail). A shell in ``_NO_ERREXIT_SHELLS``, named or as the first
    word of a custom command (``powershell -NoProfile -File {0}``, a path and ``.exe``
    stripped): no errexit and not bash grammar. A custom ``bash`` / ``sh`` / ``dash`` /
    ``zsh`` command: errexit only with ``-e`` or ``-o errexit`` (``bash {0}`` has none),
    pipefail only with ``-o pipefail``. An expression or another program: errexit and
    pipefail (not read, so nothing more is masked).
    """
    if shell is None:
        return (False, False, False) if windows else (True, True, False)
    value = shell.strip().strip("'\"")
    words = value.split()
    head = PurePosixPath(words[0].replace("\\", "/")).name.lower().removesuffix(".exe") if words else ""
    if head in _NO_ERREXIT_SHELLS:
        return False, False, False
    if "{0}" in value and head in _POSIX_SHELLS:
        flags = " ".join(words[1:]).replace("{0}", "")
        return _ERREXIT_FLAG.search(flags) is not None, True, shell_pipefail(flags)
    return True, True, value != "sh"


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


def run_errexit(tree: IndentTree, body: int) -> tuple[bool, bool, bool]:
    """``(errexit, bash grammar, pipefail)`` for the GitHub ``run:`` body at line ``body``
    (#919, #936).

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
        heredoc = None
        i = 0
        while i < len(text):
            ch = text[i]
            if not quote and heredoc is None and ch == "<":
                heredoc = _HEREDOC.match(text, i)   # only outside quotes (#936)
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
        if heredoc and not quote:
            delimiter = heredoc.group(2)
    return out


Parsed = dict[int, list[list[tuple[str, str]]]]


def _masked_in_body(refs: list[tuple[int, int]], parsed: Parsed, raw: Parsed, texts: dict[int, str],
                    run_shell: tuple[bool, bool, bool] | None,
                    captured: frozenset[tuple[int, int]] = frozenset(),
                    ) -> tuple[list[tuple[int, int]], Segments, Segments]:
    """The statements of one script body masked by ``set +e`` or by a later top-level ``exit 0``,
    and the segments a later ``exit 0`` masks in a body with errexit on.

    ``refs`` are ``(first line, statement index)`` in order. A re-raise (``exit 1``,
    ``exit $?``) after a statement un-masks it; for ``exit 0`` its own re-raise does too.
    ``run_shell`` is ``(errexit, bash grammar, pipefail)`` for a GitHub ``run:`` body, None
    for any other body. In a pwsh, powershell, cmd or python body only ``{ }`` / ``( )``
    nesting is read (no bash keywords): GitHub's pwsh wrapper ends with ``exit $LASTEXITCODE``,
    so an ``exit 0`` inside ``if (...) { }`` is a guard, not a mask. Otherwise only a ``run:``
    body read with errexit off (``set +e``, ``pwsh``, a Windows runner, ``shell: bash {0}``)
    has whole statements masked by a later ``exit 0``. With errexit on the failing check stops
    the step first, except in an errexit-exempt position (``lib.ci_exempt``, #936): those
    segments, indexed into the unrewritten ``raw`` statement, come back in the second value.
    Not inside a ``( )`` / ``{ }`` group: a subshell or a function returns the failing
    status as one command, and errexit stops there.
    GitLab checks each ``script:`` item's status.
    """
    errexit = True
    shell_errexit = True if run_shell is None else run_shell[0]
    bash = run_shell is None or run_shell[1]
    pipefail = run_shell is None or run_shell[2]
    state: list[bool] = []
    off: list[bool] = []
    pipes: list[bool] = []
    before: list[bool] = []   # pipefail as each statement starts (#1061)
    for first, k in refs:
        errexit = _errexit(parsed[first][k], errexit)
        shell_errexit = _errexit(parsed[first][k], shell_errexit)
        before.append(pipefail)
        pipefail = pipefail_after(raw[first][k], pipefail)
        state.append(not errexit)
        off.append(run_shell is not None and not shell_errexit)
        pipes.append(pipefail)
    data = dict(zip(texts, _not_code(list(texts.values())), strict=True))
    code = [not data.get(first, False) for first, _ in refs]
    depths, grouped = _depths([parsed[first][k] if keep else [] for (first, k), keep in zip(refs, code)],
                     keywords=bash)
    body = [raw[first][k] if keep else [] for (first, k), keep in zip(refs, code)]
    conditions = condition_flags(body)
    readable = run_shell is not None and bash   # the #1008 forms: a GitHub bash or sh body
    until = until_break_flags(body) if readable else [False] * len(refs)
    in_group = group_call_unsure(body, conditions) if readable else set()
    uncalled, named = uncalled_functions(body) if readable else (set(), set())   # #1061
    grouped_pipes = piped_groups(body) if readable else {}   # { pytest; } | tee log (#1061)
    opaque = opaque_flags(body)
    out: list[tuple[int, int]] = []
    exempt: Segments = {}
    unsure: Segments = {}
    later_code = False   # a later statement runs after this one (not a lone closer)
    pipestatus = False   # a later statement reads ${PIPESTATUS[...]}
    reraised = False
    exited = False   # a later top-level ``exit 0`` ends the body with success
    exit_reraised = False
    for idx in range(len(refs) - 1, -1, -1):
        first, k = refs[idx]
        own = bool(_RERAISE.search(_join(parsed[first][k])))
        later_exit = exited and not (exit_reraised or own)
        if (state[idx] and not reraised) or (later_exit and off[idx]) or idx in uncalled:
            out.append((first, k))
        elif later_exit and run_shell is not None and code[idx] and not grouped[idx]:
            statement = raw[first][k]
            drop = (frozenset(range(len(statement))) if conditions[idx] and bash
                    else exempt_segments(statement, pipes[idx]))
            if drop:
                exempt[(first, k)] = drop
        elif readable and code[idx] and not (state[idx] or off[idx] or reraised or own):
            statement = raw[first][k]
            whole = frozenset(range(len(statement)))
            # A ``{ }`` group or a compound body passes an exempt failure on; a subshell
            # or a function returns it (``lib.ci_unsure.opaque_flags``).
            maybe = (whole if idx in in_group or idx in named or conditions[idx] or until[idx]
                     else exempt_segments(statement, True) if (later_code or later_exit) and not opaque[idx]
                     else frozenset())
            if maybe:
                unsure[(first, k)] = maybe
        if readable and code[idx] and not (state[idx] or off[idx]) and (first, k) not in captured:
            # pytest | tee log without pipefail: tee's status is the pipeline's (#1061). A later
            # re-raise or PIPESTATUS read may carry the check's status out, so that reads null.
            group = frozenset() if before[idx] else grouped_pipes.get(idx, frozenset())
            piped = _piped_without_pipefail(raw[first][k], before[idx]) | group
            if group and raw[first][k][min(group)][0] == "||":
                # pytest || { ...; exit 1; } | tee log: the arm's pipeline takes tee's status, so
                # the check before it may pass (an arm group over several lines too).
                arm = frozenset(range(min(group)))
                unsure[(first, k)] = unsure.get((first, k), frozenset()) | arm
            text = _join(raw[first][k])
            target = unsure if reraised or pipestatus or "PIPESTATUS" in text else exempt
            if piped:
                target[(first, k)] = target.get((first, k), frozenset()) | piped
        if code[idx] and "PIPESTATUS" in _join(raw[first][k]):
            pipestatus = True
        if code[idx] and any(seg for _, seg in raw[first][k]) and _head(raw[first][k]) not in _ENDS:
            later_code = True
        if code[idx] and depths[idx] == 0 and _exit_zero(parsed[first][k]):
            exited, exit_reraised = True, False
        reraised = reraised or own
        exit_reraised = exit_reraised or own
    return out, exempt, unsure


def _piped_without_pipefail(statement: list[tuple[str, str]], pipefail: bool) -> frozenset[int]:
    """The piped members of ``statement`` (``ci_exempt.piped_members``) that run with pipefail
    off, read in command order: ``set -o pipefail && pytest | tee log`` keeps ``pytest``,
    ``pytest | tee log && set -o pipefail`` does not."""
    piped = piped_members(statement)
    out: set[int] = set()
    for k, segment in enumerate(statement):
        if k in piped and not pipefail:
            out.add(k)
        pipefail = pipefail_after([segment], pipefail)
    return frozenset(out)


# A statement that only closes a compound command runs nothing after the one before it.
_ENDS = frozenset({"fi", "done", "esac", "}", "else", "elif"})
Segments = dict[tuple[int, int], frozenset[int]]


def _head(statement: list[tuple[str, str]]) -> str:
    words = statement[0][1].split() if statement else []
    return words[0] if words else ""


def _move_unsure(unsure: Segments, masked: dict[int, set[int]], kept: dict[tuple[int, int], list[int]],
                 raw: Parsed, count: int) -> list[str]:
    """Take the unsure segments out of ``kept`` (``unmasked_lines``) and return them per line."""
    out = [""] * count
    for (first, k), skip in unsure.items():
        if k in masked.get(first, set()):
            continue
        base = kept.get((first, k), list(range(len(raw[first][k]))))
        moved = [i for i in base if i in skip]
        if moved:
            kept[(first, k)] = [i for i in base if i not in skip]
            out[first] = "; ".join(filter(None, (out[first], _join(_pick(raw[first][k], moved)))))
    return out


def _later_lines(lines: list[str], tree: IndentTree, first: int, last: int) -> Callable[[], list[str]]:
    """The job's lines after ``last``, built once and only when a rule asks for them."""
    cache: list[list[str]] = []

    def later() -> list[str]:
        if not cache:
            cache.append([lines[j] for j in tree.job(first) if j > last])
        return cache[0]
    return later


def unmasked_lines(text: str) -> list[str]:
    """``text``'s lines with every masked statement removed (an all-masked line is empty)."""
    return mask_lines(text)[0]


def mask_lines(text: str) -> tuple[list[str], list[str]]:
    """``(unmasked, unsure)``: ``unmasked_lines(text)`` with the #1008 forms
    (``lib.ci_unsure``) left out too, and per line the text of those forms alone (empty
    where none), so a classifier reads them as not measured, never as credit."""
    lines = text.splitlines()
    tree = IndentTree(parse_lines(lines))
    spans = _logical_lines(lines, tree)
    parsed = {span[0]: split_statements(_joined(lines, span)) for span in spans}
    raw = {first: list(statements) for first, statements in parsed.items()}
    span_at = {span[0]: span for span in spans}
    masked: dict[int, set[int]] = {}
    kept: dict[tuple[int, int], list[int]] = {}
    unsure: Segments = {}
    bodies: dict[int, list[tuple[int, int]]] = {}
    captured: set[tuple[int, int]] = set()   # output a later line of the job checks (a capture)
    for span in spans:
        first = span[0]
        statements = parsed[first]
        if tree.step_or_job_masked(first):
            masked[first] = set(range(len(statements)))
            continue
        later = _later_lines(lines, tree, first, span[-1])
        body = tree.body(first)
        # The body's pipefail for the fallback-arm reading; set -o pipefail inside it is not
        # read there, so a piped arm reads null (never credit) where it may matter.
        arm_pipefail = run_errexit(tree, body)[2] if tree.lines[body].key == "run" else True
        for k, statement in enumerate(statements):
            keep = _same_line_keep(statement, later)
            targets = _capture_targets(statement)
            if targets and _checked_later(targets, later()):
                captured.add((first, k))
            arm = or_arm_unsure(statement, " ; ".join(_join(s) for s in statements[k + 1:]), arm_pipefail)
            if arm:
                unsure[(first, k)] = frozenset(arm)
            if len(keep) < len(statement):
                kept[(first, k)] = keep
                statements[k] = _pick(statement, keep)
        bodies.setdefault(tree.body(first), []).extend((first, k) for k in range(len(statements)))
    for body, refs in bodies.items():
        run_shell = run_errexit(tree, body) if tree.lines[body].key == "run" else None
        texts = {first: _joined(lines, span_at[first]) for first in dict.fromkeys(f for f, _ in refs)}
        whole, exempt, body_unsure = _masked_in_body(refs, parsed, raw, texts, run_shell, frozenset(captured))
        for first, k in whole:
            masked.setdefault(first, set()).add(k)
        for (first, k), skip in exempt.items():
            base = kept.get((first, k), list(range(len(raw[first][k]))))
            if any(i in skip for i in base):
                kept[(first, k)] = [i for i in base if i not in skip]
        for ref, more in body_unsure.items():
            unsure[ref] = unsure.get(ref, frozenset()) | more
    unsure_out = _move_unsure(unsure, masked, kept, raw, len(lines))
    for (first, k), keep in kept.items():
        if keep:
            parsed[first][k] = _pick(raw[first][k], keep)
        else:
            masked.setdefault(first, set()).add(k)
    return _render(lines, spans, parsed, masked, {first for first, _ in kept}), unsure_out


def _render(lines: list[str], spans: list[range], parsed: Parsed, masked: dict[int, set[int]],
            changed: set[int]) -> list[str]:
    """``lines`` with each logical line rewritten to its kept statements (empty when none)."""
    out = list(lines)
    for span in spans:
        drop = masked.get(span[0], set())
        if not drop and span[0] not in changed:
            continue
        kept_text = [_join(s) for k, s in enumerate(parsed[span[0]]) if k not in drop]
        lead = _LEAD.match(lines[span[0]])
        out[span[0]] = (lead.group(0) if lead else "") + "; ".join(kept_text) if kept_text else ""
        for j in span[1:]:
            out[j] = ""
    return out
