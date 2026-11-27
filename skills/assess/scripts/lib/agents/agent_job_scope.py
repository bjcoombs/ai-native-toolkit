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
  job in the same workflow whose ``needs:`` names the agent job and whose own scope can write
  (``write`` or ``undeclared``). A read-only follower writes nothing, so it is no split.
- **Called workflows.** A job that calls a local reusable workflow takes the worst scope of the
  called workflow's agent jobs, each capped by the caller's: a called workflow can only lower
  the token it is given (``capped``). It is split when every write-scoped called agent job is.
- **Spend cap.** ``max-turns`` / ``max_turns`` or a budget limit in the job: recorded as
  supporting evidence only, never counted.

``summarize`` turns the jobs into the ``agent_ops.summary`` fields: ``agent_job_count``,
``agent_jobs_read_only``, ``agent_jobs_least_privilege`` (every agent job ``read_only``) and
``agent_job_write_access`` (agent jobs with a write scope and no split, the hazard). Both
signals are null with a sibling ``_reason``: ``no_agent_jobs`` with no agent job (or
``agent_workflows_unread`` when only another repository's workflow may run one, or when no
local job settles the reading and such a workflow may hold the write), and
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


Scope = tuple[str, list[str]]
ReadLocal = Callable[[str], "str | None"]
_LOCAL_WORKFLOW = re.compile(r"""^["']?\./(\.github/workflows/[\w.-]+\.ya?ml)""")
_RANK = (READ_ONLY, UNDECLARED, UNREAD, WRITE)


def capped(caller: Scope, callee: Scope) -> Scope:
    """A called workflow's scope under its caller's: a callee can only lower the caller's token."""
    if READ_ONLY in (caller[0], callee[0]):
        return READ_ONLY, []
    if callee[0] == UNDECLARED:
        return caller
    if callee[0] == UNREAD or caller[0] in (UNREAD, UNDECLARED) or "write-all" in caller[1]:
        return callee
    writes = [w for w in callee[1] if w in caller[1] or w == "write-all"]
    writes = caller[1] if writes == ["write-all"] else writes
    return (WRITE, writes) if writes else (READ_ONLY, [])


def _worst(scopes: list[Scope]) -> Scope:
    state = max((s for s, _ in scopes), key=_RANK.index)
    return state, sorted({w for s, ws in scopes if s == state for w in ws})


def workflow_jobs(rel: str, text: str, signs_of: SignsOf, read_local: ReadLocal | None = None,
                  inherited: Scope | None = None, seen: frozenset[str] = frozenset(),
                  remote_of: SignsOf | None = None, unsettled: list[str] | None = None) -> list[AgentJob]:
    """The agent jobs of one GitHub Actions workflow; ``signs_of`` says whether a job's text
    runs an agent (``lib.agents.agent_ops`` follows local actions and reusable workflows).

    A job that calls a local reusable workflow (``uses: ./.github/workflows/x.yml``, read through
    ``read_local``) takes the worst scope of that workflow's agent jobs, each capped by the
    caller's (``capped``); ``inherited`` is the caller's scope while reading a called workflow.
    A job that ``remote_of`` says calls an agent-named reusable workflow in another repository is
    an agent job read ``read_only`` when its own scope is read-only (the caller caps the callee);
    otherwise its name goes to ``unsettled``, since the called jobs cannot be read."""
    lines = text.splitlines()
    tree = _tree(text)
    top = _top(tree)
    default = scope_of(tree, top["permissions"]) if "permissions" in top else (UNDECLARED, [])
    if inherited is not None:
        default = capped(inherited, default)
    jobs_key = top.get("jobs")
    jobs = [k for k in tree.children(jobs_key) if tree.lines[k].key] if jobs_key is not None else []
    needs = {job: _needs(tree, job) for job in jobs}
    scopes: dict[int, Scope] = {}
    for job in jobs:
        key = _child(tree, job, "permissions")
        own = scope_of(tree, key) if key is not None else None
        scopes[job] = default if own is None else capped(inherited, own) if inherited else own
    found: list[AgentJob] = []
    for job in jobs:
        span = tree.subtree(job)
        body = lines[span.start:span.stop]
        name = tree.lines[job].key
        scope, writes = scopes[job]
        if not signs_of("\n".join(body)):
            if remote_of is not None and remote_of("\n".join(body)):
                if scope == READ_ONLY:
                    found.append(AgentJob(path=rel, job=name, scope=READ_ONLY, write_scopes=[],
                                          split=False, spend_cap=_spend_cap(body)))
                elif unsettled is not None:
                    unsettled.append(name)
            continue
        uses = _child(tree, job, "uses")
        local = _LOCAL_WORKFLOW.match(tree.lines[uses].value) if uses is not None else None
        called = read_local(local.group(1)) if local and read_local and local.group(1) not in seen else None
        inner_split = False
        if called is not None and local is not None:
            inner = workflow_jobs(local.group(1), called, signs_of, read_local, (scope, writes),
                                  seen | {rel})
            if inner:
                scope, writes = _worst([(j["scope"], j["write_scopes"]) for j in inner])
                # split inside the callee: every write-scoped called agent job hands its writes on
                inner_split = all(j["split"] for j in inner if j["scope"] == WRITE)
        # a split needs a job after this one that can write: a read-only follower writes nothing
        split = inner_split or any(name in needs[other] and scopes[other][0] in (WRITE, UNDECLARED)
                                   for other in jobs if other != job)
        found.append(AgentJob(path=rel, job=name, scope=scope, write_scopes=writes, split=split,
                              spend_cap=_spend_cap(body)))
    if not found and not unsettled and signs_of(text):
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
        elif unread_workflows and not exposed:
            nulls["agent_job_write_access"] = "agent_workflows_unread"
    for key, reason in nulls.items():
        summary[key] = None
        summary[f"{key}_reason"] = reason
    return summary
