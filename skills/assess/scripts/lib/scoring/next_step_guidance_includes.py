"""Measure hints for practices whose CI may sit in an unread GitLab include (#1350), read by ``next_step_guidance``.

``SECRETS_GUARD_HINT`` is the ``secrets_guard`` block's measure hint: why either level can read not
measured. ``COVERAGE_INCLUDES_HINT`` is the sentence the ``coverage_gate`` hint ends with for the
``ci_includes_unresolved`` reading (``lib.testing.coverage_includes``). They sit apart from
``next_step_guidance.py`` to keep that file under its size ceiling, and import nothing.
Tests: ``tests/test_fleet_next_steps.py``, ``tests/test_unresolved_includes.py``.
"""
from __future__ import annotations

SECRETS_GUARD_HINT = (
    "Level 2 is not measured (`secret_scanning_not_read`) on a GitHub repository with no secrets scan in "
    "CI when its push protection setting was not read: run with `--platform`, or `/assess` with `gh` "
    "signed in, as a repository admin, organization owner or security manager, or add a CI scan, to "
    "settle it. Both levels are not measured (`ci_includes_unresolved`) when no guard is found and a "
    "GitLab include that could hold the scan was not read: clone the included project beside this one "
    "(`/ghsync`), or add the scan to the repository's own `.gitlab-ci.yml`. Level 2 is also not measured "
    "(`gitlab_rules_unevaluated`) when a GitLab scan job runs under rules, only/except or a when that is "
    "not evaluated.")

COVERAGE_INCLUDES_HINT = (
    " With no gate in the checkout, the level reads unknown (`enforced_basis: ci_includes_unresolved`) "
    "when a GitLab include that could run one was not read. The coverage scan reads no included file, "
    "so run the threshold in the repository's own CI to measure it.")
