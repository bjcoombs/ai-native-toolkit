"""Read a GitHub step whose command or ``continue-on-error`` is not literal (issues #1164, #1165).

``lib.ci_checks`` reads each CI line as written, so a step that runs ``${{ inputs.cmd }}``
(a reusable workflow), ``${{ env.CMD }}`` or ``$CMD`` used to read as running no check, and
one under ``continue-on-error: ${{ ... }}`` as an enforced check. Neither is known.
``settle_expressions(text)`` runs after ``lib.ci_matrix`` and keeps every line's index:

- **Command position.** A whole word where a command starts in a step's run value (the
  start of a line, or after ``;``, ``&``, ``|``, ``(``, ``{``, ``then``, ``do`` or
  ``else``) that is a ``${{ ... }}`` expression, ``$NAME`` or ``${NAME}`` (quoted or not):
  ``${{ env.NAME }}``, ``$NAME`` and ``${NAME}`` are replaced by a literal ``env:`` value
  of the step, else the job, else the workflow (a quoted ``"$NAME"`` only when the value is
  one word, as the shell runs it). Anything else (``${{ inputs.cmd }}``, ``${{
  steps.x.outputs.cmd }}``, a variable no literal ``env:`` sets, one set in the script
  itself) puts the line in ``unknown``: ``lib.ci_checks`` reads it as ``check_target``
  (null) when no other line settles the signals, false when masked, as for an unexpanded
  matrix command. A variable only as an argument (``pytest $ARGS``) or as part of a word
  (``$GITHUB_WORKSPACE/ci.sh``) keeps its reading.
- **continue-on-error.** A job- or step-level ``continue-on-error: ${{ true }}`` / ``${{
  false }}`` is made literal. Any other expression (one ``lib.ci_matrix`` could not settle,
  ``${{ github.event_name == 'schedule' }}``) puts every line of that job or step in
  ``may_pass``: ``lib.ci_checks`` reads its checks as ``check_may_pass`` (null), never
  enforced. A literal ``continue-on-error: false`` keeps the credit.

The structure comes from ``lib.ci_masking``'s indentation model; there is no YAML parser.
``follow_workflow(text, read_local)`` is the whole GitHub reading ``lib.ci_checks`` runs
before ``lib.ci_action_inputs``: ``\\`` continuations joined, local composite actions
inlined (``lib.ci_composite``), matrix commands copied out (``lib.ci_matrix``), then this
module, with the unknown lines of all three merged.

Only GitHub workflow text is read. Inward-only imports: ``lib.ci_action_inputs``,
``lib.ci_composite``, ``lib.ci_continuations``, ``lib.ci_masking``, ``lib.ci_matrix``.
Tests: ``tests/test_ci_expressions.py``.
"""
from __future__ import annotations

import re
from typing import NamedTuple

from lib.ci_action_inputs import ReadLocal
from lib.ci_composite import inline_composites
from lib.ci_continuations import join_continued
from lib.ci_masking import IndentTree, parse_lines
from lib.ci_matrix import body_lines, expand_matrix, jobs, scalar

# A whole expression or variable word where a command starts.
_COMMAND = re.compile(
    r"""(?P<lead>^\s*(?:-\s+)?(?:[\w-]+:\s+)?|[;&|({]\s*|\b(?:then|do|else)\s+)"""
    r"""(?P<word>(?P<q>["']?)(?:\$\{\{(?P<expr>[^}]*)\}\}|\$(?P<var>[A-Za-z_]\w*)|\$\{(?P<bvar>[A-Za-z_]\w*)\})(?P=q))"""
    r"""(?=\s|$|[;&|)])""")
_ENV_EXPR = re.compile(r"^\s*env\.([A-Za-z_][\w-]*)\s*$")
_LITERAL = re.compile(r"""^(\s*(?:-\s+)?continue-on-error:\s*)["']?\$\{\{\s*(true|false)\s*\}\}["']?\s*$""",
                      re.IGNORECASE)

Env = dict[str, str | None]


class Settled(NamedTuple):
    """The text with literal values in place, the unknown command lines, the may-pass lines."""

    text: str
    unknown: frozenset[int]
    may_pass: frozenset[int]


def _env(tree: IndentTree, keys: list[int]) -> Env:
    """The ``env:`` names under one mapping: a literal scalar, or None for any other value."""
    block = next((k for k in keys if tree.lines[k].key == "env"), None)
    return {tree.lines[c].key: scalar(tree.lines[c].value) for c in tree.children(block)} if block is not None else {}


def _resolve(match: re.Match[str], env: Env) -> str | None:
    """The literal a command-position word stands for, or None."""
    expr = match.group("expr")
    if expr is not None:
        named = _ENV_EXPR.match(expr)
        name = named.group(1) if named else None
    else:
        name = match.group("var") or match.group("bvar")
    value = env.get(name) if name else None
    if value is None or (match.group("q") and len(value.split()) != 1):
        return None
    return value


def _settle_line(line: str, env: Env) -> tuple[str, bool]:
    """``(line, unknown)`` for one run-value line."""
    unknown = False

    def swap(match: re.Match[str]) -> str:
        nonlocal unknown
        value = _resolve(match, env)
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
        for item in tree.items(steps) if steps is not None else []:
            env = job_env | _env(tree, item)
            for j in body_lines(tree, item):
                out[j], unsure = _settle_line(lines[j], env)
                if unsure:
                    unknown.add(j)
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
