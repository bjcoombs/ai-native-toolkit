"""The ``api_compat`` run-context block (issue #853): API and contract compatibility checks CI runs.

``ApiCompatBlock`` is what ``lib.ci.api_compat.scan_api_compat`` returns, and ``ApiCompatRun`` one
of its ``runs`` or ``declared`` rows: a tool token and the file that runs or names it.
"""
from __future__ import annotations

from typing import NotRequired, TypedDict


class ApiCompatRun(TypedDict):
    """One compatibility tool and the file that runs it (a CI file) or names it (a build file)."""

    path: str
    tool: str


class ApiCompatBlock(TypedDict):
    """``api_compat`` (``lib.ci.api_compat``)."""

    files_read: int          # CI files read
    tools_run: int           # distinct tools a CI file runs
    pinning_tests: int       # test files that pin public exports
    declared_only: int       # distinct tools named in build files that no CI run reaches
    present: bool | None
    present_reason: NotRequired[str]
    runs: list[ApiCompatRun]
    pins: list[str]
    declared: list[ApiCompatRun]
