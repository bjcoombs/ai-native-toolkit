"""Classify one CI line: does it run a check, a linter, or a target that may (#698, #782, #920).

Split out of ``lib.ci.ci_checks`` (#1061), which counts each CI file's lines with these tests
and documents what each one credits (its docstring: the ``check_commands``, ``lint_runs`` and
``check_target`` rules). ``count_check_lines(lines, context=, test=, extra=)`` counts one
file's lines that a line test accepts, reading each bare list item's parent key from
``context`` (``_script_items``: a lone ``- pytest`` counts only under a GitLab ``script:``
key). The line tests are ``_line_runs_check`` (``check_command.is_check_command`` or
``is_lint_command``), ``_line_runs_lint`` (``is_lint_run`` or a ``uses:`` step of a known
lint action, ``lint_action`` over ``LINT_ACTIONS``), ``_line_may_lint`` (``may_lint`` or a
``MAY_LINT_ACTIONS`` step; ``lint_step_action`` names either action kind for
``lib.ci.ci_composite``, #1194) and ``_line_may_check`` (``may_check``: a target whose recipe is
not read); ``_skipped_check`` flags a Maven or Gradle run with its tests skipped (#799, #1046).

Inward-only imports: ``lib.ci.check_command``, ``lib.ci.ci_lint_gates``, ``lib.ci.ci_scripts``,
``lib.command_flags``, ``lib.instructions.command_index``, ``lib.instructions.command_resolver``,
``lib.ci.lint_vocabulary``, ``lib.named_check`` and ``lib.runner_args``; never ``lib.ci.ci_checks`` or an orchestrator.
Tests: ``tests/test_ci_checks.py``, ``tests/test_ci_false_credit.py``.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from lib.ci.check_command import (
    BARE_RUN_PROGRAMS, LINT_PROGRAMS, TYPE_PROGRAMS, is_check_command, is_info_invocation,
    is_lint_command, is_lint_run, may_check, may_lint)
from lib.ci.ci_compound import compound_core
from lib.ci.ci_lint_gates import line_commands
from lib.ci.ci_scripts import Present, committed, script_runs
from lib.command_flags import maven_skips_tests
from lib.instructions.command_index import ci_line_text
from lib.instructions.command_resolver import command_words
from lib.ci.lint_vocabulary import dotnet_build
from lib.named_check import setup_ci_target, sourced_env, unread_ci_target
from lib.runner_args import gradle_skips_tests


# A YAML list item with no key (``- mypy``), as opposed to ``- run: ...``.
_BARE_ITEM = re.compile(r"^\s*-\s+(?![\w.-]+:(?:\s|$))")
# A GitLab key whose list items are shell lines (``script:``, ``  before_script: &s``).
_SCRIPT_KEY = re.compile(r"^\s*(?:before_script|after_script|script):\s*(?:&\S+\s*)?(?:#.*)?$")


def _line_runs_check(line: str, *, script_item: bool = True, present: Present | None = None) -> bool:
    """One CI line, classified on its own so an open quote or a stray fence cannot carry over.

    ``script_item`` is false for a bare list item whose parent key is not a script key
    (``_script_items``): a lone ``- pytest`` there is a value, not a run. With ``present``,
    a check that runs a script the tree does not hold is not counted (#937).
    """
    commands = line_commands(line)   # the compound-command reader (#1061)
    bare_item = _BARE_ITEM.match(line) is not None
    return any(
        not is_info_invocation(c.text) and committed(c.text, present) and (
            ((is_check_command(c.text) and not setup_ci_target(c.text) and not _skips_tests(c.text)
              or script_runs(c.text, present, is_check_command))   # a read body (#1033)
             and not (bare_item and not script_item and _lone_check(c.text)))
            # a lone - mypy runs under a GitLab script: key, as for lint_runs (#1180)
            or (is_lint_command(c.text) and not (bare_item and not script_item and _lone_linter(c.text))))
        for c in commands
    )


# GitHub Actions that run a linter as a step (#782): ``uses: <one of these>@<ref>``.
# An unknown third-party action is not read.
LINT_ACTIONS = frozenset({
    "golangci/golangci-lint-action", "astral-sh/ruff-action", "chartboost/ruff-action",
    "github/super-linter", "super-linter/super-linter",
    "wearerequired/lint-action", "davidanson/markdownlint-cli2-action",
})
# reviewdog actions that install or post, not lint.
_REVIEWDOG_NOT_LINT = frozenset({"reviewdog/action-setup", "reviewdog/action-suggester"})
# Actions that run a check whose config may or may not lint: ``pre-commit/action`` runs
# ``pre-commit run --all-files``, read like that line (``check_command.may_lint``).
MAY_LINT_ACTIONS = frozenset({"pre-commit/action"})
_USES = re.compile(r"""^\s*(?:-\s+)?uses:\s*["']?([\w.-]+/[\w.-]+)(?:/[^@\s"']*)?@""")


def _action(line: str) -> str:
    """The lower-cased ``owner/name`` a workflow step's ``uses:`` line calls, or empty."""
    match = _USES.match(line)
    return match.group(1).lower() if match else ""


def lint_action(line: str) -> bool:
    """A workflow step ``uses:`` line calling a known lint action (``reviewdog/action-*`` too,
    apart from the setup and suggester actions)."""
    name = _action(line)
    return name in LINT_ACTIONS or (name.startswith("reviewdog/action-")
                                    and name not in _REVIEWDOG_NOT_LINT)


def lint_step_action(line: str) -> bool:
    """A ``uses:`` line of an action the line tests read as a lint step: a known lint action
    or a ``MAY_LINT_ACTIONS`` one (``lib.ci.ci_composite`` copies these out of a composite, #1194)."""
    return lint_action(line) or _action(line) in MAY_LINT_ACTIONS


def _line_runs_lint(line: str, *, script_item: bool = True, present: Present | None = None) -> bool:
    """One CI line that runs a linter (#782): ``is_lint_run``, never a bare ``- mypy`` item,
    or a ``uses:`` step of a known lint action (``LINT_ACTIONS``). With ``present``, a lint
    script the tree does not hold is not counted (#937)."""
    if lint_action(line):
        return True
    bare_item = _BARE_ITEM.match(line) is not None
    # A lone ``- mypy`` is a value under matrix: / needs: / stages:, but runs under a
    # GitLab script: key (``_script_items``).
    return any((is_lint_run(c.text) or script_runs(c.text, present, is_lint_run)) and committed(c.text, present)
               and not (bare_item and not script_item and _lone_linter(c.text))
               for c in line_commands(line))


def _line_may_lint(line: str, *, script_item: bool = True) -> bool:
    """One CI line whose check target or script may run a linter inside it (#782),
    or a ``uses:`` step of an action that does (``MAY_LINT_ACTIONS``)."""
    if _action(line) in MAY_LINT_ACTIONS:
        return True
    bare_item = _BARE_ITEM.match(line) is not None
    return any(may_lint(c.text) and not is_info_invocation(c.text)
               and not (bare_item and not script_item and _lone_check(c.text))
               for c in line_commands(line))


def _line_may_check(line: str, *, script_item: bool = True) -> bool:
    """One CI line whose target's recipe is not read, so it may run a check (#920):
    ``make foo``, ``npm run foo``, ``./scripts/foo.sh``, ``docker compose run app``."""
    bare_item = _BARE_ITEM.match(line) is not None
    return any((may_check(c.text) or unread_ci_target(c.text)
                or is_check_command(c.text) and _skips_tests(c.text))
               and not (setup_ci_target(c.text) or sourced_env(c.text))
               and not is_info_invocation(c.text)
               and not (bare_item and not script_item and _lone_check(c.text))
               for c in line_commands(line))


def _skips_tests(text: str) -> bool:
    """``mvn -DskipTests verify``, ``./gradlew check -x test``: a check reading that rests on
    skipped tests, so the line may check (``check_target``), never credit (#799, #1046;
    ``command_flags.maven_skips_tests``, ``runner_args.gradle_skips_tests``)."""
    words = command_words(text)
    return maven_skips_tests(words) or gradle_skips_tests(words)


def _skipped_check(line: str, *, script_item: bool = True) -> bool:
    """A CI line whose check reading rests on Maven tests it skips (``mvn -DskipTests verify``)."""
    return any(is_check_command(c.text) and _skips_tests(c.text) for c in line_commands(line))


def _lone_linter(text: str) -> bool:
    """A linter or type-checker name with no argument (``- mypy``, ``- tsc``): under
    ``matrix:`` / ``needs:`` / ``stages:`` a value, not a run."""
    words = command_words(text)
    return len(words) == 1 and (words[0] in LINT_PROGRAMS or words[0] in TYPE_PROGRAMS)


def _lone_build(text: str) -> bool:
    """A lone .NET build program (``- msbuild``): under ``matrix:`` a value (#926)."""
    words = command_words(text)
    return len(words) == 1 and dotnet_build(words)


def _lone_check(text: str) -> bool:
    """A test program or target runner with no argument (``pytest``, ``tox``, ``rspec``,
    ``make``): a value unless under ``script:``."""
    words = command_words(text)
    return len(words) == 1 and (words[0] in BARE_RUN_PROGRAMS or "/" in words[0])


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _script_items(lines: Sequence[str]) -> list[bool]:
    """Per line: false for a bare list item whose parent key is not a GitLab script key.

    Read for a lone always-check item (``- pytest``) and a lone linter (``- mypy``): both
    count only under a script key, in every signal (#1180).

    The parent is the nearest line above at the item's indent or less that is not a
    sibling item, skipping blank and comment lines and lines nested deeper (a sibling's
    block scalar). An indentless list (``script:`` then ``- pytest`` at its indent) works.
    """
    flags = [True] * len(lines)
    for i, line in enumerate(lines):
        text = compound_core(ci_line_text(line))   # - (pytest) under matrix: is a value too (#1061)
        if _BARE_ITEM.match(line) is None or not (_lone_check(text) or _lone_linter(text)
                                                  or _lone_build(text)):
            continue   # only a lone always-check, linter or build item needs its parent
        depth = _indent(line)
        parent = ""
        for above in reversed(lines[:i]):
            stripped = above.strip()
            if not stripped or stripped.startswith("#") or _indent(above) > depth:
                continue
            if _indent(above) == depth and _BARE_ITEM.match(above) is not None:
                continue
            parent = above
            break
        flags[i] = _SCRIPT_KEY.match(parent) is not None
    return flags


LineTest = Callable[..., bool]


def count_check_lines(lines: Sequence[str], *, context: Sequence[str] | None = None,
                      test: LineTest = _line_runs_check, extra: frozenset[int] = frozenset()) -> int:
    """How many of one CI file's lines run a test or lint check (a blanked line runs none).

    ``context`` is the file's original lines when ``lines`` is a rewrite of them with
    the same length (``ci_masking.unmasked_lines``): parent keys are read from it.
    ``test`` classifies one line (``_line_runs_lint`` and ``_line_may_lint`` for #782);
    a non-blank line whose index is in ``extra`` counts whatever it holds (a formatter
    rewrite a later ``git diff`` gate makes a lint run, ``ci_lint_gates.rewrite_gated``).
    """
    flags = _script_items(lines if context is None else context)
    return sum(1 for i, (line, script_item) in enumerate(zip(lines, flags, strict=True))
               if (i in extra and line.strip()) or test(line, script_item=script_item))
