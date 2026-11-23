"""Check commands as instruction files name them: ``ci`` still counts there (#951).

A CI line that runs ``make ci``, ``npm run ci`` or ``./ci.sh`` reads null at Layer 5
(``check_command.CHECK_WORDS`` has no ``ci``), because the target may build or deploy
and its recipe is not read. An instruction file that tells the agent to run ``make ci``
is naming how to verify the work, which is what Layer 0 measures, so
``lib.agent_environment`` (``check.named``, ``check.entry_points``) and
``lib.agent_consistency`` (``check_commands``) read through this module, whose
vocabulary is ``INSTRUCTION_CHECK_WORDS`` (``CHECK_WORDS`` plus ``ci``).
``is_named_check(text)`` is ``check_command.is_check_command`` under that vocabulary,
set through the ``check_command.CHECK_NAMES`` context variable for the one call, and
``is_named_check_name(name)`` is ``is_check_name`` under it. ``bun ci`` and ``npm ci``
stay installs. ``unread_ci_target(text)`` is a command that reads as a check only under
that vocabulary and false under ``CHECK_WORDS`` (``python ci.py``, ``source ci.sh``,
``cargo ci``, ``./gradlew ci``, ``mvn ci``, ``go ci``, whose ``./ci.sh`` and ``make ci``
siblings already read null): ``lib.ci_checks._line_may_check`` counts it as a target whose
recipe is not read, so Layer 5 and Layer 3 read null with ``check_target``, never a false 0.
``may_lint_named(text)`` is ``check_command.may_lint`` under the instruction vocabulary;
``lib.coverage_ci._indirect`` reads it, so a CI line running only ``make ci`` keeps the
coverage gate's null ``indirect_run`` rather than a false ``not_run_in_ci``.
``instruction_words`` runs a function under that vocabulary (the decorator behind the
three above). ``lib.ci_checks`` wraps its missing-check test (``ci_scripts.runs_missing``) in it, so a missing
``./ci.sh`` still reads ``script_not_found`` (#937), and ``named_count(count, texts)`` is
true when ``count`` finds a check line under it, so a masked ``./ci.sh || true`` reads
``not_enforced``. Both are false readings or null ones, never credit: a committed ``./ci.sh``
reads null ``check_target``, because ``lib.ci_scripts`` checks that the file is there and
never reads its body (``echo deploy`` was credited before #951).
Pure, like ``lib.check_command``. Tests: ``tests/test_ci_target_word.py``.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from functools import wraps
from typing import ParamSpec, TypeVar

from lib.check_command import (
    CHECK_NAMES, CHECK_WORDS, check_verdict, is_check_command, is_check_name, may_lint)

INSTRUCTION_CHECK_WORDS = CHECK_WORDS | {"ci"}
P = ParamSpec("P")
R = TypeVar("R")


def instruction_words(fn: Callable[P, R]) -> Callable[P, R]:
    """``fn`` run with ``ci`` a check word (``check_command.CHECK_NAMES``), reset after the call."""
    @wraps(fn)
    def run(*args: P.args, **kwargs: P.kwargs) -> R:
        token = CHECK_NAMES.set(INSTRUCTION_CHECK_WORDS)
        try:
            return fn(*args, **kwargs)
        finally:
            CHECK_NAMES.reset(token)
    return run


@instruction_words
def is_named_check(text: str) -> bool:
    """A check command an instruction file names, ``make ci`` and ``npm run ci`` included."""
    return is_check_command(text)


def is_named_check_name(name: str) -> bool:
    """A target or script name an instruction-side entry point counts (``ci``, ``test:unit``)."""
    return is_check_name(name, INSTRUCTION_CHECK_WORDS)


@instruction_words
def may_lint_named(text: str) -> bool:
    """``check_command.may_lint`` under the instruction vocabulary: a ``make ci`` or
    ``./ci.sh`` whose recipe is not read may still run a gate inside it."""
    return may_lint(text)


@instruction_words
def named_count(count: Callable[[str], int], texts: Iterable[str]) -> bool:
    """Whether ``count`` finds a check line in any of ``texts`` under the instruction vocabulary."""
    return any(count(text) for text in texts)


def unread_ci_target(text: str) -> bool:
    """A command whose check reading rests on the name ``ci`` alone, which the CI vocabulary
    reads false (``python ci.py``, ``source ci.sh``, ``cargo ci``, ``./gradlew ci``, ``mvn ci``,
    ``go ci``): ``lib.ci_checks`` reads it as an unread target (null), never as no check."""
    return check_verdict(text) is False and is_named_check(text)
