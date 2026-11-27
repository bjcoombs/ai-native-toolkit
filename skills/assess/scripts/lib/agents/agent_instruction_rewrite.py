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
  ``agent_job_write_access``: the repository default token cannot be read here. A read-only
  agent job is not counted even when a writer job that ``needs:`` it holds ``contents:
  write``: what that job writes is not read, so the fix is CODEOWNERS, never a split.
- **Paths at risk.** The root ``AGENTS.md`` and ``CLAUDE.md`` (written or created), every
  committed file at ``lib.instructions.instruction_files.INSTRUCTION_FILE_PATHS``, the job's
  own workflow file (with a gh-aw source's compiled ``.lock.yml`` when committed), every local
  reusable workflow and local action that workflow runs, and every committed file a gh-aw
  source's frontmatter ``imports:`` names (usually ``.github/workflows/shared/*.md``), each
  followed to any depth (``local_reach``: the agent's steps and prompt may sit there, not in
  the caller; #1600), and the CODEOWNERS file in effect (``.github/CODEOWNERS`` when none is
  committed): a guard whose own lines the agent can drop unreviewed guards nothing.
- **Guard.** A committed CODEOWNERS (``.github/``, then the root, then ``docs/``: the first
  GitHub finds, ``CODEOWNERS_ORDER``)
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

import posixpath
import re
from collections.abc import Callable, Mapping

from lib.agents.agent_job_scope import NO_AGENT_JOBS, SCOPE_UNREAD, UNREAD, WRITE
from lib.ci.ci_masking import IndentTree, parse_lines, strip_comment
from lib.ci.ci_step_paths import local_workflow, read_action
from lib.counts_only.block_shapes import AgentJob
from lib.docs.ownership_parser import codeowned
from lib.instructions.instruction_files import INSTRUCTION_FILE_PATHS

ROOT_INSTRUCTIONS = ("AGENTS.md", "CLAUDE.md")
# gh-aw safe outputs that land the agent's file changes on a branch.
FILE_OUTPUTS = frozenset({"create-pull-request", "push-to-pull-request-branch"})
UNREAD_WORKFLOWS = "agent_workflows_unread"
# GitHub uses the first CODEOWNERS it finds in this order (about-code-owners).
CODEOWNERS_ORDER = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")

Read = Callable[[str], "str | None"]
# A gh-aw import from another repository: ``owner/repo/path@ref``.
_REMOTE_IMPORT = re.compile(r"@[\w.\-/]+$")


def frontmatter(text: str) -> str | None:
    """A gh-aw source's YAML frontmatter, or None when ``text`` has none."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    return "\n".join(lines[1:end]) if end is not None else None


def gh_aw_imports(rel: str, front: str) -> list[str]:
    """The local files a gh-aw frontmatter's ``imports:`` names, relative to ``rel``'s folder.

    Block (``- shared/x.md``) and flow (``[a.md, b.md]``, over one line or several) lists are
    read; a trailing comment and a ``#Section`` suffix are dropped and a remote
    ``owner/repo/path@ref`` import is skipped.
    """
    lines = front.splitlines()
    tree = IndentTree(parse_lines(lines))
    top = next((i for i, line in enumerate(tree.lines) if line.structural and line.level == 0
                and not line.dash and line.key == "imports"), None)
    if top is None:
        return []
    flow = tree.lines[top].value
    if flow.startswith("["):
        end = next((k for k in range(top + 1, len(lines)) if "]" in strip_comment(lines[k])),
                   top) if "]" not in flow else top
        joined = " ".join([flow, *(strip_comment(line.strip()) for line in lines[top + 1:end + 1])])
        values = joined[1:].split("]", 1)[0].split(",")
    else:
        values = [tree.lines[k].value for k in tree.children(top)
                  if tree.lines[k].dash and not tree.lines[k].key]
    found: list[str] = []
    for raw in values:
        value = strip_comment(raw.strip()).strip("'\"").split("#", 1)[0].strip()
        if value and not _REMOTE_IMPORT.search(value):
            found.append(posixpath.normpath(posixpath.join(posixpath.dirname(rel), value)))
    return found


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


def owners_file(files: frozenset[str]) -> str:
    """The committed CODEOWNERS in effect, or the first location when none is committed."""
    return next((r for r in CODEOWNERS_ORDER if r in files), CODEOWNERS_ORDER[0])


def local_reach(rel: str, read: Read) -> set[str]:
    """The local reusable workflows, local actions and gh-aw imports of ``rel``, to any depth."""
    found: set[str] = set()
    todo = [rel]
    while todo:
        current = todo.pop()
        text = read(current) or ""
        front = frontmatter(text) if current.endswith(".md") else None
        for imported in gh_aw_imports(current, front) if front is not None else []:
            if imported not in found and imported != rel and read(imported) is not None:
                found.add(imported)
                todo.append(imported)
        for raw in text.splitlines():
            stripped = strip_comment(raw.strip().removeprefix("- ").strip())
            if not stripped.startswith("uses:"):
                continue
            uses = stripped[len("uses:"):].strip().strip("'\"")
            called = local_workflow(uses)
            action = None if called else read_action(uses, read)
            target = called if called and read(called) is not None else action[0] if action else None
            if target is not None and target not in found and target != rel:
                found.add(target)
                todo.append(target)
    return found


def at_risk(job: AgentJob, files: frozenset[str], read: Read) -> list[str]:
    """The instruction, workflow, local action, gh-aw import and CODEOWNERS files at risk."""
    paths = {*ROOT_INSTRUCTIONS, *(p for p in INSTRUCTION_FILE_PATHS if p in files), job["path"],
             owners_file(files), *local_reach(job["path"], read)}
    lock = job["path"].removesuffix(".md") + ".lock.yml"
    if job["path"].endswith(".md") and lock in files:
        paths.add(lock)
    return sorted(paths)


def codeowners_text(files: frozenset[str], read: Read) -> str:
    """The committed CODEOWNERS in effect (the first of ``CODEOWNERS_ORDER``), or empty."""
    rel = owners_file(files)
    return (read(rel) or "") if rel in files else ""


def instruction_rewrite(jobs: list[AgentJob], fronts: Mapping[str, str | None],
                        files: frozenset[str], read: Read,
                        unread_workflows: int) -> tuple[int | None, list[str], str | None]:
    """(count, workflow files, not-measured reason) for the hazard (module doc)."""
    owners = codeowners_text(files, read)
    exposed = [j for j in jobs if writes_files(j, fronts.get(j["path"]))
               and not all(codeowned(owners, p) for p in at_risk(j, files, read))]
    if exposed:
        return len(exposed), sorted({j["path"] for j in exposed}), None
    if not jobs:
        return None, [], UNREAD_WORKFLOWS if unread_workflows else NO_AGENT_JOBS
    if any(j["scope"] == UNREAD for j in jobs):
        return None, [], SCOPE_UNREAD
    if unread_workflows:
        return None, [], UNREAD_WORKFLOWS
    return 0, [], None
