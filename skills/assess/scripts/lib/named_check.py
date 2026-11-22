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
stay installs. Pure, like ``lib.check_command``. Tests: ``tests/test_ci_target_word.py``.
"""
from __future__ import annotations

from lib.check_command import CHECK_NAMES, CHECK_WORDS, is_check_command, is_check_name

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
