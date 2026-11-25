"""Read a GitHub step whose command or ``continue-on-error`` is not literal (issues #1164, #1165).

``lib.ci.ci_checks`` reads each CI line as written, so a step that runs ``${{ inputs.cmd }}``
(a reusable workflow), ``${{ env.CMD }}`` or ``$CMD`` used to read as running no check, and
one under ``continue-on-error: ${{ ... }}`` as an enforced check. Neither is known.
``settle_expressions(text)`` runs after ``lib.ci.ci_matrix`` and keeps every line's index:

- **Command position.** A whole word where a command starts in a step's run value (the
  start of a line, or after ``;``, ``&``, ``|``, ``(``, ``{``, ``then``, ``do`` or
  ``else``) that is a ``${{ ... }}`` expression, ``$NAME`` or ``${NAME}`` (quoted or not):
  ``${{ env.NAME }}``, ``$NAME`` and ``${NAME}`` are replaced by a literal ``env:`` value
  of the step, else the job, else the workflow (a quoted ``"$NAME"`` only when the value is
  one word, as the shell runs it). A shell ``$NAME`` is word-split, not reparsed, so a
  value holding ``;&|()<>`` is not read for it (``${{ env.NAME }}`` is text, and is); nor is
  a name the run value assigns itself (``NAME=``, ``export NAME=``, on that line or an
  earlier one), nor, in either form, one an earlier step of the job writes to
  ``$GITHUB_ENV``. Anything else (``${{ inputs.cmd }}``, ``${{
  steps.x.outputs.cmd }}``, a variable no literal ``env:`` sets, one set in the script
  itself) puts the line in ``unknown``: ``lib.ci.ci_checks`` reads it as ``check_target``
  (null) when no other line settles the signals, false when masked, as for an unexpanded
  matrix command. A variable only as an argument (``pytest $ARGS``) or as part of a word
  (``$GITHUB_WORKSPACE/ci.sh``) keeps its reading.
- **continue-on-error.** A job- or step-level ``continue-on-error: ${{ true }}`` / ``${{
  false }}`` is made literal. Any other expression (one ``lib.ci.ci_matrix`` could not settle,
  ``${{ github.event_name == 'schedule' }}``) puts every line of that job or step in
  ``may_pass``: ``lib.ci.ci_checks`` reads its checks as ``check_may_pass`` (null), never
  enforced. A literal ``continue-on-error: false`` keeps the credit.

The structure comes from ``lib.ci.ci_masking``'s indentation model; there is no YAML parser.
``follow_workflow(text, read_local)`` is the whole GitHub reading ``lib.ci.ci_checks`` runs
before ``lib.ci.ci_action_inputs``: ``\\`` continuations joined, local composite actions
inlined (``lib.ci.ci_composite``), matrix commands copied out (``lib.ci.ci_matrix``), then this
module, with the unknown lines of all three merged.

Only GitHub workflow text is read. Inward-only imports: ``lib.ci.ci_action_inputs``,
``lib.ci.ci_composite``, ``lib.ci.ci_continuations``, ``lib.ci.ci_masking``, ``lib.ci.ci_matrix``.
Tests: ``tests/test_ci_expressions.py``.
"""
from __future__ import annotations

import re
from typing import NamedTuple

from lib.ci.ci_action_inputs import ReadLocal
from lib.ci.ci_composite import inline_composites
from lib.ci.ci_continuations import join_continued
from lib.ci.ci_masking import IndentTree, parse_lines
from lib.ci.ci_matrix import body_lines, expand_matrix, jobs, scalar

# A whole expression or variable word where a command starts.
_COMMAND = re.compile(
    r"""(?P<lead>^\s*(?:-\s+)?(?:[\w-]+:\s+)?|[;&|({]\s*|\b(?:then|do|else)\s+)"""
    r"""(?P<word>(?P<q>["']?)(?:\$\{\{(?P<expr>[^}]*)\}\}|\$(?P<var>[A-Za-z_]\w*)|\$\{(?P<bvar>[A-Za-z_]\w*)\})(?P=q))"""
    r"""(?=\s|$|[;&|)])""")
_ENV_EXPR = re.compile(r"^\s*env\.([A-Za-z_][\w-]*)\s*$")
_LITERAL = re.compile(r"""^(\s*(?:-\s+)?continue-on-error:\s*)["']?\$\{\{\s*(true|false)\s*\}\}["']?\s*$""",
                      re.IGNORECASE)

Env = dict[str, str | None]
# A shell assignment in a run value (``CMD=x``, ``export CMD=x``): that variable is not the env's.
_ASSIGN = re.compile(r"(?:^\s*|[;&|({]\s*|\b(?:export|then|do|else)\s+)([A-Za-z_]\w*)=")
# A write to $GITHUB_ENV (``echo "CMD=x" >> $GITHUB_ENV``, ``CMD<<EOF``): later steps may see it.
_ENV_WRITE = re.compile(r"""["']?([A-Za-z_]\w*)(?:=|<<).*>>\s*["']?\$\{?GITHUB_ENV\b""")
# Shell operators a word-split ``$NAME`` cannot run: such a value is not one command.
_OPERATORS = frozenset(";&|()<>")


class Settled(NamedTuple):
    """The text with literal values in place, the unknown command lines, the may-pass lines."""

    text: str
    unknown: frozenset[int]
    may_pass: frozenset[int]


def _env(tree: IndentTree, keys: list[int]) -> Env:
    """The ``env:`` names under one mapping: a literal scalar, or None for any other value."""
    block = next((k for k in keys if tree.lines[k].key == "env"), None)
    return {tree.lines[c].key: scalar(tree.lines[c].value) for c in tree.children(block)} if block is not None else {}


def _resolve(match: re.Match[str], env: Env, assigned: set[str]) -> str | None:
    """The literal a command-position word stands for, or None. A shell ``$NAME`` is word-split,
    never reparsed, so a value with a shell operator, or a name the script ``assigned``, is
    unknown; ``${{ env.NAME }}`` is substituted as text before the shell runs."""
    expr = match.group("expr")
    if expr is not None:
        named = _ENV_EXPR.match(expr)
        name = named.group(1) if named else None
    else:
        name = match.group("var") or match.group("bvar")
        if name in assigned:
            return None
    value = env.get(name) if name else None
    if value is None or (match.group("q") and len(value.split()) != 1):
        return None
    if expr is None and _OPERATORS & set(value):
        return None
    return value


def _settle_line(line: str, env: Env, assigned: set[str]) -> tuple[str, bool]:
    """``(line, unknown)`` for one run-value line."""
    unknown = False

    def swap(match: re.Match[str]) -> str:
        nonlocal unknown
        value = _resolve(match, env, assigned)
        if value is None:
            unknown = True
            return match.group(0)
        return match.group("lead") + value

    return _COMMAND.sub(swap, line), unknown


def settle_expressions(text: str) -> Settled:
    """Settle command and ``continue-on-error`` expressions (module docstring)."""
    lines = text.splitlines()
    tree = IndentTree(parse_lines(lines))
    out = list(lines)
    unknown: set[int] = set()
    may_pass: set[int] = set()
    tops = [i for i, line in enumerate(tree.lines) if line.structural and line.level == 0]
    workflow_env = _env(tree, tops)
    for job in jobs(tree):
        job_keys = tree.children(job)
        job_env = workflow_env | _env(tree, job_keys)
        steps = next((k for k in job_keys if tree.lines[k].key == "steps"), None)
        mappings = [(job_keys, tree.subtree(job))]
        written: set[str] = set()   # names an earlier step of the job wrote to $GITHUB_ENV
        for item in tree.items(steps) if steps is not None else []:
            env = job_env | _env(tree, item) | dict.fromkeys(written)
            assigned: set[str] = set()
            run_lines = body_lines(tree, item)
            for j in run_lines:
                assigned.update(_ASSIGN.findall(lines[j].split(":", 1)[-1] if tree.lines[j].structural else lines[j]))
                out[j], unsure = _settle_line(lines[j], env, assigned)
                if unsure:
                    unknown.add(j)
            written.update(n for j in run_lines for n in _ENV_WRITE.findall(lines[j]))
            mappings.append((item, range(item[0], max(tree.subtree(k).stop for k in item))))
        for keys, span in mappings:
            for k in keys:
                if tree.lines[k].key != "continue-on-error" or "${{" not in tree.lines[k].value:
                    continue
                literal = _LITERAL.match(lines[k])
                if literal:
                    out[k] = literal.group(1) + literal.group(2).lower()
                else:
                    may_pass.update(span)
    body = "\n".join(out) + ("\n" if text.endswith("\n") else "")
    return Settled(body, frozenset(unknown), frozenset(may_pass))


def follow_workflow(text: str, read_local: ReadLocal) -> Settled:
    """Composites, matrix and expressions read for one GitHub workflow (module docstring)."""
    expanded = expand_matrix(*inline_composites(join_continued(text), read_local))
    settled = settle_expressions(expanded.text)
    return settled._replace(unknown=settled.unknown | expanded.unknown)
