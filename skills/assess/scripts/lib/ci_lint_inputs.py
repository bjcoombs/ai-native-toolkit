"""Does a known lint action's step lint, and can it fail its job? (issue #893)

``lib.ci_lines.lint_action`` credits a ``uses:`` step of a known lint action from the
``uses:`` line alone (#782). Two actions document a ``with:`` input that installs the linter
and runs nothing, and the reviewdog actions never fail by default, so
``read_lint_inputs(text)`` reads each such step's ``with:`` block:

- ``astral-sh/ruff-action`` and ``chartboost/ruff-action`` run ``ruff <args> <src>``
  (``args`` defaults to ``check``). ``args: --version`` is the install-only form the
  astral README documents. ``args`` is read as the line ``ruff <args>``: a version or
  usage request (``check_command.is_info_invocation``: ``--version``, ``-V``, ``--help``)
  or the ``version`` / ``help`` subcommand is not a lint run. An empty ``args`` runs
  ``ruff <src>``, whose reading this module does not settle.
- ``golangci/golangci-lint-action`` reads ``install-only`` with ``core.getBooleanInput``
  from v9.0.0 on: ``true`` / ``True`` / ``TRUE`` installs and returns, ``false`` / ``False`` /
  ``FALSE`` lints, any other value fails the step before it lints. Before v9 the input is
  not declared, so the runner warns and the action lints. The ref decides: a ``v9`` or later
  tag reads the input, a ``v8`` or earlier tag ignores it, and a SHA or branch is not read.
  ``install-mode: none`` (use a golangci-lint already on the path) and ``only-new-issues``
  still lint, so they keep the credit.
- ``reviewdog/action-*`` (apart from action-setup and action-suggester, which
  ``lint_action`` never credits) passes ``-fail-level`` and ``-fail-on-error`` to reviewdog.
  Every listed action defaults to ``fail_level: none`` (always exit 0) and the deprecated
  ``fail_on_error: false``; older releases have only ``fail_on_error``. A literal
  ``fail_level`` of ``any``, ``info``, ``warning`` or ``error``, or ``fail_on_error: true``,
  can fail the step and keeps the credit. Anything else (the defaults, ``none``, ``false``,
  an expression, an unknown value) is a lint run whose failure may not fail the job.

Each step comes back in one of three index sets of its ``uses:`` line:

- ``install_only``: a literal input makes the step run no linter. ``lib.ci_checks`` does not
  count the line for ``lint_runs``.
- ``may_pass``: a reviewdog step that may not fail its job. ``lib.ci_checks`` counts it as
  a lint line on the ``check_may_pass`` side, like a ``ruff check . || echo failed`` line
  (``lib.ci_unsure``): ``lint_runs`` reads null with ``lint_basis: check_may_pass`` unless
  another line settles it. A reviewdog posting a GitHub check run may still block a merge
  through that check, which this module cannot see, so the reading is null, not false.
  Masking and triggers apply as to any step.
- ``unsure``: the deciding input is set, but not to a value this module settles (a
  ``${{ }}`` expression, an empty ``args``, an ``install-only: true`` on a SHA or branch ref,
  an ``install-only`` value that is neither true nor false). ``lib.ci_checks`` counts the
  line as one that may lint, so ``lint_runs`` reads null with ``lint_basis: check_target``
  when no other line settles it: never credit, never a false 0. A masked or off-trigger
  unsure step is false either way, and since it is not a lint line it reads
  ``no_lint_command``, not ``not_enforced``.

A deciding input whose only expression is one ``${{ matrix.<key> }}`` (``args: ${{ matrix.a }}``,
``args: check --select ${{ matrix.rule }}``) is read once per value of that axis
of the job's literal ``strategy.matrix`` (``_axis_values``: a flow or block list of scalars,
plus the values ``include`` entries set; an ``exclude`` that names the key, a non-literal
matrix or a key that is no axis is not read). Each leg is read as above: any leg that lints
keeps the credit, every leg install only reads install only, and anything else is unsure.

A step with no deciding input keeps the #782 credit. A ``with:`` this module cannot read
keeps it too, as the issue asks: a merge key (``<<: *defaults``) or quoted key that may set
the input, an alias value, or a flow mapping that is not plain ``key: value`` items
(``with: {args: --version}`` and ``{src: "./src"}`` are read).
The structure comes from ``lib.ci_masking``'s indentation model, run on the final workflow
text ``lib.ci_checks`` tallies, so indexes match. Inward-only imports: ``lib.check_command``,
``lib.ci_lines``, ``lib.ci_masking`` and ``lib.ci_matrix`` (``scalar``). Tests: ``tests/test_ci_lint_inputs.py``.
"""
from __future__ import annotations

import re
import shlex
from collections.abc import Callable
from functools import partial
from typing import NamedTuple

from lib.check_command import is_info_invocation
from lib.ci_lines import lint_action
from lib.ci_masking import IndentTree, parse_lines
from lib.ci_matrix import scalar

_RUFF_ACTIONS = frozenset({"astral-sh/ruff-action", "chartboost/ruff-action"})
_GOLANGCI = "golangci/golangci-lint-action"
_GOLANGCI_INSTALL_ONLY_SINCE = 9   # install-only is declared from v9.0.0
_TRUE = frozenset({"true", "True", "TRUE"})
_FALSE = frozenset({"false", "False", "FALSE"})
_RUFF_INFO_COMMANDS = frozenset({"version", "help"})
_REVIEWDOG = "reviewdog/action-"
_FAILING_LEVELS = frozenset({"any", "info", "warning", "error"})
_USES_VALUE = re.compile(r"^([\w.-]+/[\w.-]+)(?:/[^@\s]*)?@(\S+)$")
_TAG = re.compile(r"^v(\d+)(?:\.\d+){0,2}$")
_FLOW_ITEM = re.compile(r"^\s*([\w.-]+)\s*:\s*(.*?)\s*$")
_MATRIX_REF = re.compile(r"\$\{\{\s*matrix\.([\w-]+)\s*\}\}")


class LintInputs(NamedTuple):
    """The ``uses:`` line indexes of install-only, unsettled and may-pass lint steps."""

    install_only: frozenset[int]
    unsure: frozenset[int]
    may_pass: frozenset[int] = frozenset()


NO_INPUTS = LintInputs(frozenset(), frozenset())


class _Unread:
    """An input whose value this module does not read (an alias, a merge, a bad flow map)."""


_UNREAD = _Unread()
Value = str | _Unread | None   # None: the input is absent


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _flow_inputs(value: str) -> dict[str, Value] | None:
    """A flow-style ``{a: 1, b: 2}`` mapping of plain items, or None when it is not one."""
    inner = value.strip()
    if not (inner.startswith("{") and inner.endswith("}")):
        return None
    body = inner[1:-1]
    if any(c in body for c in "{}[]"):
        return None
    out: dict[str, Value] = {}
    for item in filter(str.strip, body.split(",")):
        match = _FLOW_ITEM.match(item)
        if match is None or item.count('"') % 2 or item.count("'") % 2:
            return None   # a quoted key, or a comma inside a quoted value
        flow_value = match.group(2).strip()
        out[match.group(1)] = _UNREAD if flow_value.startswith("*") else _unquote(flow_value)
    return out


def _with_inputs(tree: IndentTree, raw: list[str], with_line: int) -> dict[str, Value]:
    """The step's ``with:`` inputs by name; ``*`` marks a merge key, a quoted key or an
    unread flow value, any of which may set the deciding input."""
    value = tree.lines[with_line].value.strip()
    if value:
        flow = _flow_inputs(value)
        return {"*": _UNREAD} if flow is None else flow
    out: dict[str, Value] = {}
    for k in tree.children(with_line):
        line = tree.lines[k]
        text = line.value.strip()
        if not line.key:   # a merge key (<<: *x) or a quoted key: not read
            out["*"] = _UNREAD
        elif text.startswith("*"):
            out[line.key] = _UNREAD
        elif text[:1] in "|>" and text:
            owned = [raw[j].strip() for j in range(k + 1, len(raw)) if tree.owner[j] == k]
            out[line.key] = " ".join(w for w in owned if w)
        else:
            out[line.key] = _unquote(text)
    return out


def _input(inputs: dict[str, Value], name: str) -> Value:
    """One input's value: unread when only a merge key or alias could set it."""
    if name in inputs:
        return inputs[name]
    return _UNREAD if "*" in inputs else None


def _ruff_reading(args: Value) -> str:
    """``lint``, ``install_only`` or ``unsure`` for a ruff action's ``args``."""
    if args is None or isinstance(args, _Unread):
        return "lint"   # the default is check; an unread with: keeps today's answer
    if "${{" in args:
        return "unsure"
    try:
        words = shlex.split(args)
    except ValueError:
        return "unsure"
    if not words:
        return "unsure"   # ruff <src>: not settled here
    if words[0] in _RUFF_INFO_COMMANDS or is_info_invocation(shlex.join(["ruff", *words])):
        return "install_only"
    return "lint"


def _golangci_reading(ref: str, install_only: Value) -> str:
    """``lint``, ``install_only`` or ``unsure`` for golangci-lint-action's ``install-only``."""
    if install_only is None or isinstance(install_only, _Unread) or install_only in _FALSE:
        return "lint"   # absent, unread (today's answer) or false: the action lints
    tag = _TAG.match(ref)
    if tag is not None and int(tag.group(1)) < _GOLANGCI_INSTALL_ONLY_SINCE:
        return "lint"   # an undeclared input: the runner warns and the action lints
    if tag is None or install_only not in _TRUE:
        return "unsure"   # a SHA or branch ref, an expression or a value getBooleanInput refuses
    return "install_only"


def _list_values(tree: IndentTree, key: int) -> list[str] | None:
    """A matrix axis's scalars: a one-line flow list without quoted commas, or a block list."""
    value = tree.lines[key].value.strip()
    if value:
        if not (value.startswith("[") and value.endswith("]")):
            return None
        items = [scalar(v) for v in value[1:-1].split(",") if v.strip()]
    else:
        lines = tree.children(key)
        if not lines or any(tree.lines[k].key or not tree.lines[k].dash for k in lines):
            return None
        items = [scalar(tree.lines[k].value) for k in lines]
    return None if not items or any(v is None for v in items) else [v for v in items if v is not None]


def _axis_values(tree: IndentTree, uses: int, name: str) -> list[str] | None:
    """The values ``${{ matrix.<name> }}`` takes in the job holding line ``uses``, or None."""
    job = next((a for a in tree.ancestors(uses) if (p := tree.parent[tree.mapping_of[a]]) is not None
                and tree.lines[p].key == "jobs" and tree.lines[p].level == 0), None)
    strategy = next((k for k in tree.children(job) if tree.lines[k].key == "strategy"), None) \
        if job is not None else None
    matrix = next((k for k in tree.children(strategy) if tree.lines[k].key == "matrix"), None) \
        if strategy is not None else None
    if matrix is None or tree.lines[matrix].value:
        return None
    values: list[str] | None = None
    included: list[str] = []
    for k in tree.children(matrix):
        key = tree.lines[k].key
        if key in ("include", "exclude"):
            if tree.lines[k].value:
                return None
            for item in tree.items(k):
                entry: dict[str, str] = {}
                for j in item:
                    leg = scalar(tree.lines[j].value)
                    if not tree.lines[j].key or leg is None or tree.children(j):
                        return None
                    entry[tree.lines[j].key] = leg
                if name in entry and key == "exclude":
                    return None   # an exclude may drop this axis's legs
                if name in entry:
                    included.append(entry[name])
        elif key == name:
            values = _list_values(tree, k)
            if values is None:
                return None
    return None if values is None else values + included


def _put(value: str, leg: str) -> str:
    """``value`` with its matrix reference replaced by ``leg``, which is repo text: a
    function replacement, so a backslash in it (``E\\d``, ``\\1``) is never a regex template."""
    return _MATRIX_REF.sub(lambda _match: leg, value)


def _matrix_reading(tree: IndentTree, uses: int, value: Value, read: Callable[[Value], str]) -> str:
    """``read`` once per matrix value when ``value`` is one ``${{ matrix.<key> }}`` expression."""
    reading = read(value)
    if reading != "unsure" or not isinstance(value, str):
        return reading
    keys = set(_MATRIX_REF.findall(value))
    if len(keys) != 1 or "${{" in _MATRIX_REF.sub("", value):
        return reading
    legs = _axis_values(tree, uses, keys.pop())
    if not legs:
        return reading
    readings = {read(_put(value, leg)) for leg in legs}
    if "lint" in readings:
        return "lint"
    return "install_only" if readings == {"install_only"} else "unsure"


def _reviewdog_reading(fail_level: Value, fail_on_error: Value) -> str:
    """``lint`` when a literal input lets reviewdog fail the step, else ``may_pass``."""
    if isinstance(fail_level, _Unread) or isinstance(fail_on_error, _Unread):
        return "lint"   # an unread with: keeps today's answer
    if fail_level is not None and fail_level.lower() in _FAILING_LEVELS:
        return "lint"
    if fail_on_error is not None and fail_on_error in _TRUE:
        return "lint"
    return "may_pass"


def read_lint_inputs(text: str) -> LintInputs:
    """Sort the known lint action steps of one workflow file (module docstring)."""
    raw = text.splitlines()
    tree = IndentTree(parse_lines(raw))
    install_only: set[int] = set()
    unsure: set[int] = set()
    may_pass: set[int] = set()
    for i, line in enumerate(tree.lines):
        if not (line.structural and line.key == "uses" and lint_action(raw[i])):
            continue
        match = _USES_VALUE.match(_unquote(line.value))
        if match is None:
            continue
        name, ref = match.group(1).lower(), match.group(2)
        if name not in _RUFF_ACTIONS and name != _GOLANGCI and not name.startswith(_REVIEWDOG):
            continue
        step = tree.siblings[tree.mapping_of[i]]
        with_line = next((k for k in step if tree.lines[k].key == "with"), None)
        inputs = {} if with_line is None else _with_inputs(tree, raw, with_line)
        reading = (_matrix_reading(tree, i, _input(inputs, "args"), _ruff_reading) if name in _RUFF_ACTIONS
                   else _matrix_reading(tree, i, _input(inputs, "install-only"),
                                        partial(_golangci_reading, ref)) if name == _GOLANGCI
                   else _reviewdog_reading(_input(inputs, "fail_level"), _input(inputs, "fail_on_error")))
        {"install_only": install_only, "unsure": unsure, "may_pass": may_pass}.get(reading, set()).add(i)
    return LintInputs(frozenset(install_only), frozenset(unsure), frozenset(may_pass))
