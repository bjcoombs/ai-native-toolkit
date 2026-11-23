"""Does a known lint action's step lint, or only install the linter? (issue #893)

``lib.ci_lines.lint_action`` credits a ``uses:`` step of a known lint action from the
``uses:`` line alone (#782). Two actions document a ``with:`` input that installs the linter
and runs nothing, so ``read_lint_inputs(text)`` reads each such step's ``with:`` block:

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

Each step comes back in one of two index sets of its ``uses:`` line:

- ``install_only``: a literal input makes the step run no linter. ``lib.ci_checks`` does not
  count the line for ``lint_runs``.
- ``unsure``: the deciding input is set, but not to a value this module settles (a
  ``${{ }}`` expression, an empty ``args``, an ``install-only: true`` on a SHA or branch ref,
  an ``install-only`` value that is neither true nor false). ``lib.ci_checks`` counts the
  line as one that may lint, so ``lint_runs`` reads null with ``lint_basis: check_target``
  when no other line settles it: never credit, never a false 0. A masked or off-trigger
  unsure step is false either way, and since it is not a lint line it reads
  ``no_lint_command``, not ``not_enforced``.

A step with no deciding input keeps the #782 credit. A ``with:`` this module cannot read
keeps it too, as the issue asks: a merge key (``<<: *defaults``) or quoted key that may set
the input, an alias value, or a flow mapping that is not plain ``key: value`` items
(``with: {args: --version}`` and ``{src: "./src"}`` are read).
The structure comes from ``lib.ci_masking``'s indentation model, run on the final workflow
text ``lib.ci_checks`` tallies, so indexes match. Inward-only imports: ``lib.check_command``,
``lib.ci_lines`` and ``lib.ci_masking``. Tests: ``tests/test_ci_lint_inputs.py``.
"""
from __future__ import annotations

import re
import shlex
from typing import NamedTuple

from lib.check_command import is_info_invocation
from lib.ci_lines import lint_action
from lib.ci_masking import IndentTree, parse_lines

_RUFF_ACTIONS = frozenset({"astral-sh/ruff-action", "chartboost/ruff-action"})
_GOLANGCI = "golangci/golangci-lint-action"
_GOLANGCI_INSTALL_ONLY_SINCE = 9   # install-only is declared from v9.0.0
_TRUE = frozenset({"true", "True", "TRUE"})
_FALSE = frozenset({"false", "False", "FALSE"})
_RUFF_INFO_COMMANDS = frozenset({"version", "help"})
_USES_VALUE = re.compile(r"^([\w.-]+/[\w.-]+)(?:/[^@\s]*)?@(\S+)$")
_TAG = re.compile(r"^v(\d+)(?:\.\d+){0,2}$")
_FLOW_ITEM = re.compile(r"^\s*([\w.-]+)\s*:\s*(.*?)\s*$")


class LintInputs(NamedTuple):
    """The ``uses:`` line indexes of install-only lint steps and of unsettled ones."""

    install_only: frozenset[int]
    unsure: frozenset[int]


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
        out[match.group(1)] = _unquote(match.group(2))
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


def read_lint_inputs(text: str) -> LintInputs:
    """Sort the known lint action steps of one workflow file (module docstring)."""
    raw = text.splitlines()
    tree = IndentTree(parse_lines(raw))
    install_only: set[int] = set()
    unsure: set[int] = set()
    for i, line in enumerate(tree.lines):
        if not (line.structural and line.key == "uses" and lint_action(raw[i])):
            continue
        match = _USES_VALUE.match(_unquote(line.value))
        if match is None:
            continue
        name, ref = match.group(1).lower(), match.group(2)
        if name not in _RUFF_ACTIONS and name != _GOLANGCI:
            continue
        step = tree.siblings[tree.mapping_of[i]]
        with_line = next((k for k in step if tree.lines[k].key == "with"), None)
        inputs = {} if with_line is None else _with_inputs(tree, raw, with_line)
        reading = (_ruff_reading(_input(inputs, "args")) if name in _RUFF_ACTIONS
                   else _golangci_reading(ref, _input(inputs, "install-only")))
        if reading == "install_only":
            install_only.add(i)
        elif reading == "unsure":
            unsure.add(i)
    return LintInputs(frozenset(install_only), frozenset(unsure))
