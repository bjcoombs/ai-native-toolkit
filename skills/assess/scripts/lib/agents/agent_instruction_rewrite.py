"""Read whether an agent job that can write files can rewrite its own instructions unguarded (#858).

A scheduled or event-triggered agent job whose token can push changes can rewrite the
instruction files agents read (``AGENTS.md``, ``CLAUDE.md``) and its own workflow, so the
next run follows text no reviewer approved (#826, hazard H2). The hazard reads the
``agent_jobs`` that ``lib.agents.agent_job_scope`` already scoped:

- **Can write files.** The job's effective scope is ``write`` and names ``contents`` or
  ``write-all``: its steps can push any path. A GitHub Agentic Workflows source can also
  write through ``safe-outputs:`` ``create-pull-request`` or ``push-to-pull-request-branch``,
  whatever its own scope. A split job with a ``contents`` write still counts: the scope is
  the agent job's own. An ``undeclared`` scope is not counted, as for
  ``agent_job_write_access``: the repository default token cannot be read here.
- **Paths at risk.** The root ``AGENTS.md`` and ``CLAUDE.md`` (written or created), every
  committed file at ``lib.instructions.instruction_files.INSTRUCTION_FILE_PATHS``, and the
  job's own workflow file (with a gh-aw source's compiled ``.lock.yml`` when committed).
- **Guard.** A committed CODEOWNERS (root, ``.github/`` or ``docs/``, GitHub's precedence)
  whose last matching line names an owner (``lib.docs.ownership_parser.codeowned``). The job
  raises the hazard when any path at risk has no owner. Whether branch protection requires
  code-owner review is a hosting setting this offline reader does not see.

``instruction_rewrite`` returns the count of such jobs (``agent_ops.summary
.agent_instruction_rewrite``), their workflow files, sorted (``agent_ops
.agent_instruction_rewrite_files``), and a not-measured reason when the count is 0 but
nothing settles it: ``no_agent_jobs`` or ``agent_workflows_unread`` with no agent job,
``agent_job_scope_unread`` when a scope could not be read, ``agent_workflows_unread`` when an
unread remote agent workflow may hold the job. Pure stdlib. Tests:
``tests/test_agent_instruction_rewrite.py``.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping

from lib.agents.agent_job_scope import NO_AGENT_JOBS, SCOPE_UNREAD, UNREAD, WRITE
from lib.ci.ci_masking import IndentTree, parse_lines
from lib.counts_only.block_shapes import AgentJob
from lib.docs.ownership_parser import CODEOWNERS_LOCATIONS, codeowned
from lib.instructions.instruction_files import INSTRUCTION_FILE_PATHS

ROOT_INSTRUCTIONS = ("AGENTS.md", "CLAUDE.md")
# gh-aw safe outputs that land the agent's file changes on a branch.
FILE_OUTPUTS = frozenset({"create-pull-request", "push-to-pull-request-branch"})
UNREAD_WORKFLOWS = "agent_workflows_unread"

Read = Callable[[str], "str | None"]


def file_outputs(front: str) -> bool:
    """Whether a gh-aw frontmatter's ``safe-outputs:`` lands file changes on a branch."""
    tree = IndentTree(parse_lines(front.splitlines()))
    top = next((i for i, line in enumerate(tree.lines) if line.structural and line.level == 0
                and not line.dash and line.key == "safe-outputs"), None)
    return top is not None and any(tree.lines[k].key in FILE_OUTPUTS for k in tree.children(top))


def writes_files(job: AgentJob, front: str | None) -> bool:
    """Whether the agent job can change files: a ``contents`` write, or gh-aw file outputs."""
    own = job["scope"] == WRITE and bool({"contents", "write-all"} & set(job["write_scopes"]))
    return own or (front is not None and file_outputs(front))


def at_risk(job: AgentJob, files: frozenset[str]) -> list[str]:
    """The instruction files and workflow files the job could rewrite (module doc)."""
    paths = {*ROOT_INSTRUCTIONS, *(p for p in INSTRUCTION_FILE_PATHS if p in files), job["path"]}
    lock = job["path"].removesuffix(".md") + ".lock.yml"
    if job["path"].endswith(".md") and lock in files:
        paths.add(lock)
    return sorted(paths)


def codeowners_text(files: frozenset[str], read: Read) -> str:
    """The committed CODEOWNERS in effect, by GitHub's precedence, or empty."""
    rel = next((r for r in CODEOWNERS_LOCATIONS if r in files), None)
    return (read(rel) or "") if rel is not None else ""


def instruction_rewrite(jobs: list[AgentJob], fronts: Mapping[str, str | None],
                        files: frozenset[str], read: Read,
                        unread_workflows: int) -> tuple[int | None, list[str], str | None]:
    """(count, workflow files, not-measured reason) for the hazard (module doc)."""
    owners = codeowners_text(files, read)
    exposed = [j for j in jobs if writes_files(j, fronts.get(j["path"]))
               and not all(codeowned(owners, p) for p in at_risk(j, files))]
    if exposed:
        return len(exposed), sorted({j["path"] for j in exposed}), None
    if not jobs:
        return None, [], UNREAD_WORKFLOWS if unread_workflows else NO_AGENT_JOBS
    if any(j["scope"] == UNREAD for j in jobs):
        return None, [], SCOPE_UNREAD
    if unread_workflows:
        return None, [], UNREAD_WORKFLOWS
    return 0, [], None
