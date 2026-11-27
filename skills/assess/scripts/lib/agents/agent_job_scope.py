"""Read the effective token scope of each committed workflow job that runs an agent (#849).

An agent job with write scope can push, merge or edit issues on the strength of whatever
text reached its prompt. The practice the audits found (#826 theme 1) keeps the agent job
read-only and moves writes into a separate job that acts on the agent's output.

- **Agent job.** A GitHub Actions job whose own lines, a repo-local action it uses or a local
  reusable workflow it calls carry an agent sign (the signs ``lib.agents.agent_ops`` reads:
  an agent action, a headless agent CLI such as ``claude -p`` or ``codex exec``, a named
  model). A GitHub Agentic Workflows source (``.github/workflows/*.md`` with an ``on:`` in its
  frontmatter) is one agent job, ``agent``, whose scope is its frontmatter ``permissions:``.
  A workflow whose only event is ``workflow_call`` is not read on its own: the caller's job
  scope caps it. A workflow with a sign outside every job (a workflow-level ``env:`` naming a
  model) gives one entry with an empty ``job`` and scope ``unread``.
- **Effective scope.** The job's ``permissions:``, else the workflow's, else ``undeclared``
  (the repository's default token, which this reader cannot see). ``read-all``, ``{}`` and a
  map with no ``write`` value read ``read_only``; ``write-all`` or any ``<name>: write`` reads
  ``write``, naming the scopes (an unknown name reads ``other``). ``id-token: write`` is not a
  write: it mints an OIDC token, which is how an agent action signs in to a cloud provider, and
  grants nothing on the repository. A value this reader cannot parse (an expression, a flow
  map left open) reads ``unread``.
- **Split.** Writes go through another job: a gh-aw source with ``safe-outputs:``, or another
  job in the same workflow whose ``needs:`` names the agent job.
- **Spend cap.** ``max-turns`` / ``max_turns`` or a budget limit in the job: recorded as
  supporting evidence only, never counted.

``summarize`` turns the jobs into the ``agent_ops.summary`` fields: ``agent_job_count``,
``agent_jobs_read_only``, ``agent_jobs_least_privilege`` (every agent job ``read_only``) and
``agent_job_write_access`` (agent jobs with a write scope and no split, the hazard). Both
signals are null with a sibling ``_reason``: ``no_agent_jobs`` with no agent job (or
``agent_workflows_unread`` when only another repository's workflow may run one), and
``agent_job_scope_unread`` when no job settles the reading but one scope could not be read.
Pure stdlib; never raises on malformed YAML. Tests: ``tests/test_agent_workflows.py``.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from lib.ci.ci_masking import IndentTree, parse_lines, strip_comment
from lib.counts_only.block_shapes import AgentJob

READ_ONLY, WRITE, UNDECLARED, UNREAD = "read_only", "write", "undeclared", "unread"
NO_AGENT_JOBS = "no_agent_jobs"
SCOPE_UNREAD = "agent_job_scope_unread"
# GitHub token permission names (workflow syntax, ``permissions``); any other reads ``other``.
SCOPES = frozenset({
    "actions", "artifact-metadata", "attestations", "checks", "contents", "deployments",
    "discussions", "id-token", "issues", "models", "packages", "pages", "pull-requests",
    "repository-projects", "security-events", "statuses",
})
_NOT_A_REPO_WRITE = frozenset({"id-token"})
_LEVELS = frozenset({"read", "write", "none"})
_SPEND_CAP = re.compile(r"max[-_]turns|max[-_]budget", re.IGNORECASE)

SignsOf = Callable[[str], bool]


def _tree(text: str) -> IndentTree:
    return IndentTree(parse_lines(text.splitlines()))


def _top(tree: IndentTree) -> dict[str, int]:
    return {line.key: i for i, line in enumerate(tree.lines)
            if line.structural and line.level == 0 and not line.dash and line.key}


def _child(tree: IndentTree, i: int, key: str) -> int | None:
    return next((k for k in tree.children(i) if tree.lines[k].key == key), None)


def _unquote(value: str) -> str:
    return value.strip().strip("'\"").strip()


def scope_of(tree: IndentTree, i: int) -> tuple[str, list[str]]:
    """(scope, write scopes) of the ``permissions:`` key on line ``i``."""
    value = _unquote(tree.lines[i].value)
    if value in ("read-all", "{}"):
        return READ_ONLY, []
    if value == "write-all":
        return WRITE, ["write-all"]
    if value.startswith("{") and value.endswith("}"):
        pairs = [tuple(_unquote(p) for p in item.split(":", 1)) for item in value[1:-1].split(",")
                 if item.strip()]
        if any(len(pair) != 2 for pair in pairs):
            return UNREAD, []
    elif value:
        return UNREAD, []
    else:
        children = tree.children(i)
        if not children or any(tree.lines[k].dash or not tree.lines[k].key for k in children):
            return UNREAD, []
        pairs = [(tree.lines[k].key, _unquote(tree.lines[k].value)) for k in children]
    if any(level not in _LEVELS for _, level in pairs):
        return UNREAD, []
    writes = sorted({name if name in SCOPES else "other" for name, level in pairs
                     if level == "write" and name not in _NOT_A_REPO_WRITE})
    return (WRITE, writes) if writes else (READ_ONLY, [])


def _needs(tree: IndentTree, job: int) -> set[str]:
    key = _child(tree, job, "needs")
    if key is None:
        return set()
    value = tree.lines[key].value
    if value.startswith("["):
        return {_unquote(v) for v in value.strip("[]").split(",") if _unquote(v)}
    if value:
        return {_unquote(value)}
    return {_unquote(tree.lines[k].value) for k in tree.children(key) if tree.lines[k].dash}


def _spend_cap(lines: list[str]) -> bool:
    return any(_SPEND_CAP.search(strip_comment(line)) for line in lines)


def workflow_jobs(rel: str, text: str, signs_of: SignsOf) -> list[AgentJob]:
    """The agent jobs of one GitHub Actions workflow; ``signs_of`` says whether a job's text
    runs an agent (``lib.agents.agent_ops`` follows local actions and reusable workflows)."""
    lines = text.splitlines()
    tree = _tree(text)
    top = _top(tree)
    default = scope_of(tree, top["permissions"]) if "permissions" in top else (UNDECLARED, [])
    jobs_key = top.get("jobs")
    jobs = [k for k in tree.children(jobs_key) if tree.lines[k].key] if jobs_key is not None else []
    needs = {job: _needs(tree, job) for job in jobs}
    found: list[AgentJob] = []
    for job in jobs:
        span = tree.subtree(job)
        body = lines[span.start:span.stop]
        if not signs_of("\n".join(body)):
            continue
        name = tree.lines[job].key
        key = _child(tree, job, "permissions")
        scope, writes = scope_of(tree, key) if key is not None else default
        split = any(name in needs[other] for other in jobs if other != job)
        found.append(AgentJob(path=rel, job=name, scope=scope, write_scopes=writes, split=split,
                              spend_cap=_spend_cap(body)))
    if not found and signs_of(text):
        found.append(AgentJob(path=rel, job="", scope=UNREAD, write_scopes=[], split=False,
                              spend_cap=_spend_cap(lines)))
    return found


def gh_aw_job(rel: str, front: str) -> AgentJob:
    """The agent job of a gh-aw source, from its frontmatter."""
    tree = _tree(front)
    top = _top(tree)
    scope, writes = scope_of(tree, top["permissions"]) if "permissions" in top else (UNDECLARED, [])
    return AgentJob(path=rel, job="agent", scope=scope, write_scopes=writes,
                    split="safe-outputs" in top, spend_cap=_spend_cap(front.splitlines()))


def summarize(jobs: list[AgentJob], unread_workflows: int) -> dict[str, Any]:
    """The ``agent_ops.summary`` fields for the agent jobs (see the module doc)."""
    read_only = sum(j["scope"] == READ_ONLY for j in jobs)
    exposed = sum(j["scope"] == WRITE and not j["split"] for j in jobs)
    unread = any(j["scope"] == UNREAD for j in jobs)
    summary: dict[str, Any] = {
        "agent_job_count": len(jobs),
        "agent_jobs_read_only": read_only,
        "agent_jobs_least_privilege": bool(jobs) and read_only == len(jobs),
        "agent_job_write_access": exposed,
    }
    if not jobs:
        reason = "agent_workflows_unread" if unread_workflows else NO_AGENT_JOBS
        nulls = {"agent_jobs_least_privilege": reason, "agent_job_write_access": reason}
    else:
        nulls = {}
        settled = any(j["scope"] in (WRITE, UNDECLARED) for j in jobs)
        if unread and not settled:
            nulls["agent_jobs_least_privilege"] = SCOPE_UNREAD
        elif not settled and unread_workflows:
            nulls["agent_jobs_least_privilege"] = "agent_workflows_unread"
        if unread and not exposed:
            nulls["agent_job_write_access"] = SCOPE_UNREAD
    for key, reason in nulls.items():
        summary[key] = None
        summary[f"{key}_reason"] = reason
    return summary
