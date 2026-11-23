"""Read the command a GitHub Actions step takes from its job's matrix (issue #923).

``lib.ci_checks`` reads a workflow line by line, so ``run: ${{ matrix.cmd }}`` with
``cmd: ['ruff check .', 'pytest']`` used to read as no command at all. ``expand_matrix(text)``
returns the text with each such step copied once per value, so each copy keeps its own
masking (``continue-on-error``, ``|| true``, the step's ``shell:``), as each matrix job does:

- **Command position only.** A step is expanded when a ``${{ matrix.<key> }}`` expression
  sits where a command starts in its ``run:`` value (or a ``run`` / ``script`` / ``command``
  input under ``with:``): the start of a line, or after ``;``, ``&``, ``|``, ``(``, ``{``,
  ``then``, ``do`` or ``else``. Every literal ``${{ matrix.<key> }}`` of the run value is
  substituted in the copies. A reference only as an argument (``-DmoduleSet=${{ matrix.m }}``,
  ``tox -e ${{ matrix.env }}``) leaves the step as it was, so a line is never counted once
  per value.
- **Literal values.** The job's ``strategy.matrix`` axes as a flow list (``[a, 'b c']``) or
  a block list of scalars, then ``exclude:`` and ``include:`` entries of scalar values,
  combined as GitHub does: the product of the axes, less each combination an ``exclude``
  entry matches, then each ``include`` entry added to every combination whose original
  values it does not overwrite, or appended as its own combination. A value is substituted
  as written, quotes removed; equal copies are kept once, and a combination that sets no
  value for a command key makes no copy.
- **Unknown, never 0.** When the command-position expression cannot be expanded (no
  ``strategy.matrix``, a matrix, axis, ``include`` or ``exclude`` given as an expression or
  a non-scalar, a key no combination sets, ``${{ matrix.cfg.cmd }}`` or any other matrix
  expression), the step stays as it was and its run-value line indexes come back in
  ``unknown``. ``lib.ci_checks`` reads them as a command it cannot settle: ``check_target``
  (null) for the three CI signals when no other line settles them. Masked, the step reads
  false for ``enforced_check_runs`` and ``lint_runs``; ``test_or_lint_configured`` stays null.
  A matrix of more than ``MAX_COMBOS`` (256) combinations is not built and reads unknown.
- **Consumed axes.** A block-list axis whose values a copy now runs has its ``- value``
  lines blanked, so ``- ruff check .`` under ``matrix:`` is not counted again outside the
  step, where the step's masking cannot reach it.

The structure comes from ``lib.ci_masking``'s indentation model; there is no YAML parser.
Only GitHub workflow text is read (GitLab's ``parallel: matrix:`` sets variables, not
expressions). Inward-only imports: ``lib.ci_action_inputs``, ``lib.ci_masking``.
Tests: ``tests/test_ci_matrix.py``, ``tests/test_ci_composite_matrix.py``.
"""
from __future__ import annotations

import itertools
import re
from typing import NamedTuple

import math

from lib.ci_action_inputs import INPUT_KEYS
from lib.ci_masking import IndentTree, parse_lines

# A ``${{ ... }}`` expression where a command starts: the inner text is group 1.
CMD_EXPR = re.compile(r"""(?:^|[;&|({]\s*|\b(?:then|do|else)\s+)["']?\$\{\{([^}]*)\}\}""")
_MATRIX_REF = re.compile(r"\$\{\{\s*matrix\.([\w-]+)\s*\}\}")
_QUOTED_RUN = re.compile(r"""^(\s*(?:-\s+)?[\w.-]+:\s+)(["'])(.*\$\{\{\s*matrix\..*)\2\s*$""")
_KEY_ONLY = re.compile(r"^\s*matrix\.([\w-]+)\s*$")

Combo = dict[str, str]
# The most combinations read before the product is built: GitHub caps a matrix at 256 jobs.
MAX_COMBOS = 256


class Expanded(NamedTuple):
    """The workflow text with matrix commands copied out, and the run-value lines left unknown."""

    text: str
    unknown: frozenset[int]


class _Matrix(NamedTuple):
    combos: list[Combo] | None   # None: not literal
    axis_items: dict[str, list[int]]   # block-list value lines per axis


def scalar(value: str) -> str | None:
    """A plain or quoted scalar's text, or None for a flow collection or an expression."""
    value = value.strip()
    if not value or value[0] in "[{&*!|>" or "${{" in value:
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def _flow_list(value: str) -> list[str] | None:
    """The scalars of a one-line flow list (``[a, 'b, c']``), or None."""
    if not (value.startswith("[") and value.endswith("]")):
        return None
    items: list[str] = []
    current, quote = "", ""
    for char in value[1:-1]:
        if quote:
            current += char
            quote = "" if char == quote else quote
        elif char in "'\"":
            current, quote = current + char, char
        elif char == ",":
            items.append(current)
            current = ""
        else:
            current += char
    if quote:
        return None
    if current.strip():
        items.append(current)
    scalars = [scalar(item) for item in items]
    return None if any(s is None for s in scalars) else [s for s in scalars if s is not None]


def _entries(tree: IndentTree, key: int) -> list[Combo] | None:
    """An ``include`` / ``exclude`` block list of scalar mappings, or None."""
    if tree.lines[key].value:
        return None
    out: list[Combo] = []
    for item in tree.items(key):
        entry: Combo = {}
        for k in item:
            line = tree.lines[k]
            value = scalar(line.value)
            if not line.key or value is None or tree.children(k):
                return None
            entry[line.key] = value
        out.append(entry)
    return out


def _axis(tree: IndentTree, key: int) -> tuple[list[str] | None, list[int]]:
    """One axis's values and its block-list item lines."""
    value = tree.lines[key].value
    if value:
        return _flow_list(value), []
    lines = [k for k in tree.children(key)]
    values = [scalar(tree.lines[k].value) for k in lines]
    if not lines or any(tree.lines[k].key or not tree.lines[k].dash or v is None for k, v in zip(lines, values, strict=True)):
        return None, []
    return [v for v in values if v is not None], lines


def _combine(axes: dict[str, list[str]], exclude: list[Combo], include: list[Combo]) -> list[Combo]:
    combos = [dict(zip(axes, values, strict=True)) for values in itertools.product(*axes.values())] if axes else []
    combos = [c for c in combos if not any(all(c.get(k) == v for k, v in e.items()) for e in exclude)]
    originals = list(combos)   # an include merges only into the axis product, never another include
    for entry in include:
        matched = False
        for combo in originals:
            if all(combo.get(k, v) == v for k, v in entry.items() if k in axes):
                combo.update(entry)
                matched = True
        if not matched:
            combos.append(dict(entry))
    return combos


def _matrix(tree: IndentTree, job: int) -> _Matrix:
    strategy = next((k for k in tree.children(job) if tree.lines[k].key == "strategy"), None)
    matrix = next((k for k in tree.children(strategy) if tree.lines[k].key == "matrix"), None) \
        if strategy is not None else None
    if matrix is None or tree.lines[matrix].value:
        return _Matrix(None, {})
    axes: dict[str, list[str]] = {}
    items: dict[str, list[int]] = {}
    extra: dict[str, list[Combo]] = {"include": [], "exclude": []}
    for k in tree.children(matrix):
        name = tree.lines[k].key
        if name in extra:
            entries = _entries(tree, k)
            if entries is None:
                return _Matrix(None, {})
            extra[name] = entries
            continue
        values, lines = _axis(tree, k)
        if values is None:
            return _Matrix(None, {})
        axes[name], items[name] = values, lines
    if math.prod(len(v) for v in axes.values()) + len(extra["include"]) > MAX_COMBOS:
        return _Matrix(None, {})
    return _Matrix(_combine(axes, extra["exclude"], extra["include"]), items)


def _body_lines(tree: IndentTree, item: list[int]) -> list[int]:
    """The run-value lines of one step: its ``run:`` and any shell-like ``with:`` input."""
    keys = [k for k in item if tree.lines[k].key == "run"]
    for k in item:
        if tree.lines[k].key == "with":
            keys += [c for c in tree.children(k) if tree.lines[c].key in INPUT_KEYS]
    return [j for k in keys for j in tree.subtree(k)]


def _value(tree: IndentTree, lines: list[str], i: int) -> str:
    return tree.lines[i].value if tree.lines[i].structural else lines[i].strip()


def _command_keys(texts: list[str]) -> set[str] | None:
    """The matrix keys a run value runs as a command; None when one is not ``matrix.<key>``."""
    keys: set[str] = set()
    for text in texts:
        for match in CMD_EXPR.finditer(text):
            inner = match.group(1)
            key = _KEY_ONLY.match(inner)
            if key:
                keys.add(key.group(1))
            elif "matrix" in inner:
                return None
    return keys


def _substitute(line: str, combo: Combo) -> str:
    """``line`` with each ``${{ matrix.<key> }}`` that ``combo`` sets replaced by its value; a
    quoted ``run: "${{ matrix.cmd }}"`` scalar loses its quotes, as YAML reads it."""
    line = _QUOTED_RUN.sub(r"\1\3", line)
    return _MATRIX_REF.sub(lambda m: combo.get(m.group(1), m.group(0)), line)


def _variants(lines: list[str], span: range, body: set[int], combos: list[Combo],
              needed: set[str]) -> list[list[str]]:
    """One copy of the step lines per combination that sets every ``needed`` key, deduplicated."""
    out: list[list[str]] = []
    for combo in combos:
        if not needed <= combo.keys():
            continue
        copy = [(_substitute(lines[j], combo) if j in body else lines[j]) for j in span]
        if copy not in out:
            out.append(copy)
    return out


def _jobs(tree: IndentTree) -> list[int]:
    tops = [i for i, line in enumerate(tree.lines) if line.structural and line.level == 0 and line.key == "jobs"]
    return [k for top in tops for k in tree.children(top)]


def expand_matrix(text: str, unknown_in: frozenset[int] = frozenset()) -> Expanded:
    """Copy each matrix-command step once per value (module docstring). ``unknown_in`` are
    line indexes of ``text`` already unknown (``lib.ci_composite``); they come back moved."""
    lines = text.splitlines()
    tree = IndentTree(parse_lines(lines))
    replace: dict[int, tuple[int, list[list[str]]]] = {}   # step start -> (end, copies)
    unknown: set[int] = set(unknown_in)   # original indexes
    blank: set[int] = set()
    for job in _jobs(tree):
        steps = next((k for k in tree.children(job) if tree.lines[k].key == "steps"), None)
        if steps is None:
            continue
        matrix: _Matrix | None = None
        for item in tree.items(steps):
            body = _body_lines(tree, item)
            needed = _command_keys([_value(tree, lines, j) for j in body])
            if needed == set():
                continue
            matrix = matrix or _matrix(tree, job)
            span = range(item[0], max(tree.subtree(k).stop for k in item))
            copies = (_variants(lines, span, set(body), matrix.combos, needed)
                      if needed is not None and matrix.combos is not None else [])
            if needed is None or not copies:
                unknown.update(j for j in body if lines[j].strip())
                continue
            replace[span.start] = (span.stop, copies)
            blank.update(j for key in needed for j in matrix.axis_items.get(key, []))
    out: list[str] = []
    found: set[int] = set()
    i = 0
    while i < len(lines):
        if i in replace:
            end, copies = replace[i]
            for copy in copies:   # an unknown line (a composite input) stays unknown in each copy
                found.update(len(out) + r for r in range(end - i) if i + r in unknown)
                out.extend(copy)
            i = end
            continue
        if i in unknown:
            found.add(len(out))
        out.append("" if i in blank else lines[i])
        i += 1
    return Expanded("\n".join(out) + ("\n" if text.endswith("\n") else ""), frozenset(found))
