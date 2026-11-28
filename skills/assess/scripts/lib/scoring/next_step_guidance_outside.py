"""Wording for practices a team can set outside the repository: "not found in the repository" (#1662).

Agent permissions can sit in Claude Code managed settings, and a secrets guard in the host's
secret scanning and push protection, neither of which the commit holds. An unmet reading of either
shows only that the scan found no trace in the repository, so the views that word it say
``NOT_FOUND`` and name where else the practice can be set, never that it is absent:

- ``ELSEWHERE_SIGNALS``: signal -> where else, read by the fleet team page
  (``lib.fleet.fleet_next_steps``) for each repository whose reading is ``false``;
- ``ELSEWHERE_CONDITIONS``: the default model's condition ids -> where else, read by
  ``lib.scoring.headline.gate_note`` for an unmet Step gate (the top card's Blocks line);
- ``ELSEWHERE_ROWS``: the default model's row ids whose top level can be set outside the
  repository -> where else, read by ``lib.fleet.fleet_not_measured.absence_line``.

An unmet reading of these conditions always means no repository evidence: ``permissions_encoded``
is false only when no tracked settings file lists a scoped permission entry, ``configured`` only
when neither a pre-commit hook nor a CI scan is found, and ``enforcement.present`` only when no CI
scan is found. A host or machine source may also have been read and found off: on a github.com
remote an unmet secrets guard means ``--platform`` read push protection off (unread, it is not
measured), and ``--managed-settings`` may have read a managed source with no permission entry. The
wording holds either way: it names where else the practice can be set, as a fact, and never tells
the reader to run a read that already ran (#1688 review). A not-measured reading (``managed_settings_unreadable``, ``ci_includes_unresolved``,
``secret_scanning_not_read``, #1350) keeps its own wording. Sits apart from
``next_step_guidance.py`` to keep that file under its size ceiling, and imports nothing.
Tests: ``tests/test_not_found_outside.py``.
"""
from __future__ import annotations

from collections.abc import Mapping

NOT_FOUND = "not found in the repository"

AGENT_PERMISSIONS_ELSEWHERE = (
    "agent permissions can also be set in Claude Code managed settings, on each machine or for the "
    "whole organisation, which a run with `--managed-settings` reads on the machine running the check")
SECRETS_GUARD_ELSEWHERE = (
    "a secrets scan can also be set on the host: GitHub secret scanning with push protection in the "
    "repository or organisation settings, GitLab secret push protection in the project settings, "
    "or a pre-receive hook on the server")

ELSEWHERE_SIGNALS: Mapping[str, str] = {
    "agent_ops.summary.permissions_encoded": AGENT_PERMISSIONS_ELSEWHERE,
    "secrets_guard.configured": SECRETS_GUARD_ELSEWHERE,
    "secrets_guard.enforcement.present": SECRETS_GUARD_ELSEWHERE,
}
ELSEWHERE_CONDITIONS: Mapping[str, str] = {
    "layer-8-agent-ops.1": AGENT_PERMISSIONS_ELSEWHERE,
    "secrets-guard.1": SECRETS_GUARD_ELSEWHERE,
    "secrets-guard.2": SECRETS_GUARD_ELSEWHERE,
}
# layer-8-agent-ops is left out: its top level reads committed hooks and agent work, not permissions.
ELSEWHERE_ROWS: Mapping[str, str] = {"secrets-guard": SECRETS_GUARD_ELSEWHERE}


def not_found_note(where: str) -> str:
    """``NOT_FOUND`` and where else the practice can be set, as one clause."""
    return f"{NOT_FOUND}; {where}"
