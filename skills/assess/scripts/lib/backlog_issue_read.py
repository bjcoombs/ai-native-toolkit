"""The shape of an issue-tracker read for backlog-ready level 2 (#1323), with no network import.

``lib.hosting.issue_outcomes`` makes the read; ``lib.backlog_ready_outcomes`` turns it into metrics.
The shapes live here so the ``backlog_ready`` collector reads them without importing a platform
client: a scan whose module reaches ``gh_cli`` or ``gitlab_cli`` must be a network scan, and
``backlog_ready`` is not one (its network read is the private ``backlog_outcomes`` spec).

`Issue` (`created`, `closed`, lower-cased `labels`, `first_response`, `response_read`), `IssueRead`
(`provider`, `start`, `now`, `recent`, `open_old`, `truncated`, `window_truncated`, `open_truncated`), `OutcomeRead` (an `IssueRead` or an
unavailable dict), the reason codes `REASONS` and `UNTRIAGED_AFTER` (7 days). Tests:
`tests/test_backlog_ready_platform.py`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

NO_NETWORK = "no_network"
NO_PLATFORM_AUTH = "no_platform_auth"
UNSUPPORTED_HOST = "unsupported_host"
PLATFORM_ERROR = "platform_error"
REASONS = (NO_NETWORK, NO_PLATFORM_AUTH, UNSUPPORTED_HOST, PLATFORM_ERROR)

# The age past which an open issue should carry a triage decision.
UNTRIAGED_AFTER = timedelta(days=7)

Provider = Literal["github", "gitlab"]


@dataclass(frozen=True)
class Issue:
    """One issue as times and lower-cased label names; nothing that names it."""

    created: datetime
    closed: datetime | None
    labels: tuple[str, ...]
    first_response: datetime | None = None
    response_read: bool = True  # False: the events read could not settle the first response
    labels_complete: bool = True  # False: the issue has more labels than were read


@dataclass(frozen=True)
class IssueRead:
    """The issues read. ``recent``: updated since ``start``; ``open_old``: open and created before
    ``now - UNTRIAGED_AFTER``. ``truncated``: any cap was hit; ``window_truncated``: the ``recent``
    list itself stopped at its page cap, so issues closed in the window may be missing from it."""

    provider: Provider
    start: datetime
    now: datetime
    recent: tuple[Issue, ...]
    open_old: tuple[Issue, ...]
    truncated: bool
    window_truncated: bool = False
    open_truncated: bool = False  # the open-issue list stopped at its page cap


OutcomeRead = IssueRead | dict[str, Any]
