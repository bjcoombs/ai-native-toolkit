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
Pure, like ``lib.check_command``. Tests: ``tests/test_ci_target_word.py``.
"""
from __future__ import annotations

from lib.check_command import CHECK_NAMES, CHECK_WORDS, check_verdict, is_check_command, is_check_name

INSTRUCTION_CHECK_WORDS = CHECK_WORDS | {"ci"}


def is_named_check(text: str) -> bool:
    """A check command an instruction file names, ``make ci`` and ``npm run ci`` included."""
    token = CHECK_NAMES.set(INSTRUCTION_CHECK_WORDS)
    try:
        return is_check_command(text)
    finally:
        CHECK_NAMES.reset(token)


def is_named_check_name(name: str) -> bool:
    """A target or script name an instruction-side entry point counts (``ci``, ``test:unit``)."""
    return is_check_name(name, INSTRUCTION_CHECK_WORDS)


def unread_ci_target(text: str) -> bool:
    """A command whose check reading rests on the name ``ci`` alone, which the CI vocabulary
    reads false (``python ci.py``, ``source ci.sh``, ``cargo ci``, ``./gradlew ci``, ``mvn ci``,
    ``go ci``): ``lib.ci_checks`` reads it as an unread target (null), never as no check."""
    return check_verdict(text) is False and is_named_check(text)
