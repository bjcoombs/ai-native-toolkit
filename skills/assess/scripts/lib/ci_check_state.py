"""The ci_checks signal states read from per-line tallies (#775, #782, #1008).

Split out of ``lib.ci_checks`` (#1008), which counts each CI file's check lines into
``Tally`` tuples and calls ``_enforced_state`` / ``_lint_state`` here for
``enforced_check_runs`` / ``enforced_basis`` and ``lint_runs`` / ``lint_basis``. The
basis tokens are documented in ``lib.ci_checks``'s docstring. Inward-only imports:
stdlib only; never ``lib.ci_checks`` or an orchestrator. Tests: ``tests/test_ci_checks.py``.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

EnforcedBasis = Literal[
    "enforced_check", "included_jobs", "unknown_trigger", "check_may_pass", "script_not_found",
    "check_target",
    "unread_ci_system", "external_ci", "external_ci_marker", "not_enforced", "no_check_command",
    "no_ci"]
LintBasis = Literal["lint_command", "included_jobs", "merged_config_partial", "unknown_trigger",
                    "check_may_pass", "script_not_found", "check_target", "unread_ci_system", "external_ci",
                    "external_ci_marker", "analysers_in_build", "compiler_gate_only",
                    "not_enforced", "no_lint_command", "no_ci"]
# Off-repository CI, in precedence order (#928): platform evidence, then the Prow marker.
External = Literal["external_ci", "external_ci_marker"] | None

# ``(ordinary, unknown trigger, off trigger, masked, may pass)`` check lines: may pass
# counts the #1008 forms (``lib.ci_unsure``) on ordinary or unsettled triggers.
Tally = tuple[int, int, int, int, int]
NO_LINES: Tally = (0, 0, 0, 0, 0)


def _sum(tallies: Iterable[Tally]) -> Tally:
    a = b = c = d = e = 0
    for t in tallies:
        a, b, c, d, e = a + t[0], b + t[1], c + t[2], d + t[3], e + t[4]
    return a, b, c, d, e


def _max(x: Tally, y: Tally) -> Tally:
    return max(x[0], y[0]), max(x[1], y[1]), max(x[2], y[2]), max(x[3], y[3]), max(x[4], y[4])


def _enforced_state(ordinary: int, unknown: int, may_check: Tally, *, elsewhere: bool,
                    unread: bool, any_check: bool, has_files: bool,
                    external: External = None,
                    missing: Tally = NO_LINES, may_pass: int = 0) -> tuple[bool | None, EnforcedBasis]:
    """``enforced_check_runs`` and ``enforced_basis`` (#775); unknown beats wrong, so an
    unread CI system (#787) or an unmasked target whose recipe is not read (#920) reads
    null ahead of a masked or off-trigger check."""
    if ordinary:
        return True, "enforced_check"
    if elsewhere:
        return None, "included_jobs"
    if unknown:
        return None, "unknown_trigger"
    if may_pass:   # a check whose failure the step may hide (#1008)
        return None, "check_may_pass"
    if missing[0] or missing[1]:
        return None, "script_not_found"
    if may_check[0] or may_check[1]:
        return None, "check_target"
    if unread:
        return None, "unread_ci_system"
    if external:
        return None, external  # off-repository CI (#928): never credit, never false
    if any_check:
        return False, "not_enforced"
    return False, "no_check_command" if has_files else "no_ci"


def _lint_state(lint: Tally, may: Tally, *, elsewhere: bool, partial: bool, unread: bool,
                has_files: bool, external: External = None,
                missing: Tally = NO_LINES) -> tuple[bool | None, LintBasis]:
    """``lint_runs`` and ``lint_basis`` from the lint and may-lint tallies (#782); an unread
    CI system (#787) reads null ahead of the false cases, as for ``enforced_check_runs``."""
    ordinary, unknown, off, masked, may_pass = lint
    if ordinary:
        return True, "lint_command"
    if elsewhere:
        return None, "merged_config_partial" if partial else "included_jobs"
    if unknown:
        return None, "unknown_trigger"
    if may_pass:
        return None, "check_may_pass"
    if missing[0] or missing[1]:
        return None, "script_not_found"
    if may[0] or may[1]:
        return None, "check_target"
    if unread:
        return None, "unread_ci_system"
    if external:
        return None, external
    if not has_files:
        return False, "no_ci"
    return False, "not_enforced" if off or masked else "no_lint_command"
