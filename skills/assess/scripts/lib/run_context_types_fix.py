"""Shapes for ``/assess --fix`` and ``--starter`` (``lib/starter_files.py``, ``assess_fix.py``, #1154).

Kept in its own module because ``run_context_types.py`` sits at its size ceiling.
Tests: ``tests/test_starter_files.py``, ``tests/test_assess_fix.py``.
"""
from __future__ import annotations

from typing import Literal, TypedDict

CiSystem = Literal["github", "gitlab"]
FileState = Literal["written", "conflict", "manual"]


class RepoFacts(TypedDict):
    """What the repository already has, read by the existing detectors."""

    language: str | None  # import_graph.dominant_language; None when no source file
    check_command: str | None  # the agent_environment check entry point it runs, or an override
    check_source: str | None  # where the check command came from ("pytest configuration", "override")
    ci_system: CiSystem  # the tracked CI system, else the remote's host, else GitHub
    ci_basis: str  # why: "tracked", "remote", "override" or "default"
    default_branch: str  # the branch the deny rule protects
    files: list[str]  # the marker files present at the root that the templates read (uv.lock, go.mod, ...)


class PlannedFile(TypedDict):
    """One starter file for one gate (or the guardrail), before it is written."""

    path: str  # relative to the repository root, POSIX
    gate: str  # a Step gate id, or "guardrail"
    content: str
    manual: str | None  # the one manual step left once it is written, if any


class FileOutcome(TypedDict):
    path: str  # "" for a manual step that writes no file
    gate: str
    state: FileState  # "conflict": the file exists, so nothing was written; "manual": no file fits
    manual: str | None  # the step left for the user; on a conflict, what to add by hand
    suggested: str | None  # on a conflict, the text that would have been written


class GateProof(TypedDict):
    gate: str
    task: str  # exec_summary.GATE_TASK wording
    held_before: bool
    held_after: bool
    manual: str | None  # the one manual step left when the gate does not hold yet


class Delivery(TypedDict):
    mode: Literal["pull_request", "local_branch", "working_tree", "none"]
    branch: str | None
    url: str | None  # the pull or merge request, when one was opened
    reason: str  # why this mode: "opened", "committed_locally", "not_on_default_branch", "dirty_tree", ...
    next_command: str | None  # on a local branch: the exact command that pushes it and opens the request


class FixReport(TypedDict):
    mode: Literal["fix", "starter"]
    step_before: int | None
    step_after: int | None
    facts: RepoFacts
    files: list[FileOutcome]
    gates: list[GateProof]
    delivery: Delivery
    nothing_to_do: bool  # every targeted gate already held and the guardrail was in place
