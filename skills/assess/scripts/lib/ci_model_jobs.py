"""Model-backed CI jobs and whether each is gated (#1242).

A CI job that calls a model API costs money on every run. One that runs on every push to the
default branch or every pull request update, with nothing to limit it, costs in step with merge
volume rather than with changes to the prompts it tests. This module finds those jobs in the
committed CI files and says whether each one is gated. Rules only, no model; one
``IndentTree`` walk per file (``lib.ci_masking``), no YAML parser. The sign and gate tables,
the trigger rules and the known limits are in the long reference.
Full reference: lib/docs/ci_model_jobs.md.

**A model-backed job** (``signs``): a ``run:`` or ``script:`` line runs an eval harness
(``eval_harness``: promptfoo eval, deepeval test, oaieval, inspect eval, lm_eval, ragas) or the
Claude Code CLI in print mode (``agent_cli``: ``claude -p``); a step uses an agent action
(``agent_action``, ``AGENT_ACTIONS``, also inside a repo-local action the step uses); an ``env:``
or ``with:`` line of the job or the workflow names a model-provider secret (``provider_secret``,
``PROVIDER_SECRETS``); or a command runs ``pytest -m eval`` or a path
under ``evals/`` (``eval_tests``). An install line (``pip install ragas``) is not a sign, and a step or job whose ``if:`` is never
true is not read.

**A gate** (``GATES``): ``paths`` (``paths:`` or ``paths-ignore:`` on every ordinary event that
fires the job, never a catch-all ``**``), ``pr_types`` (pull request ``types:`` without ``synchronize``), ``label`` (a
``labeled`` type, or a job or step ``if:`` on the pull request's labels), ``event_if`` (a job
``if:`` that requires a schedule or manual event), ``tags_only`` (a push filtered to tags),
``event_trigger`` (no ordinary event fires the workflow, each one a known ``GATING_EVENTS`` name:
schedule, ``workflow_dispatch``, comments) and GitLab ``changes`` (a ``rules:changes:`` the job cannot run without).
``concurrency`` with ``cancel-in-progress: true`` is recorded as ``cancel_superseded`` and is not
a gate: the last push of every update still runs the job.

**Each job** is ``gated``, ``ungated`` or ``unknown`` (with a ``reason`` token). Ungated means
its trigger is definitely ordinary and every gate is definitely absent; anything this reader
cannot settle is unknown, never ungated or gated. A job whose only sign is a provider secret
(a deploy that stores the key) reads unknown (``secret_only``) rather than ungated.

Block (``ci_model_jobs`` in run-context.json)::

    {"available": True, "files_read": int, "model_jobs": int, "gated": int, "unknown": int,
     "unread": int, "ungated": int | None, "ungated_reason": str (only when ungated is None),
     "jobs": [{"path", "line", "job", "platform" (github | gitlab), "signs": [...],
               "triggers": [...], "gates": [...], "state", "reason" (unknown only),
               "cancel_superseded": bool}]}

``ungated`` is the count of ungated jobs whenever one is ungated. With none ungated it is null
when a job is unknown (``trigger_unresolved``) or a job or include could not be read
(``unread`` above 0: ``ci_jobs_unread``), else 0. With no model-backed job, ``model_jobs`` is 0
and the condition is not measured (``no_model_jobs``, ``signal_grades.EMPTY_DENOMINATORS``).
Tests: ``tests/test_ci_model_jobs.py``.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, NotRequired, TypedDict

from lib.ci_action_inputs import ReadLocal, local_action_paths
from lib.ci_includes import GITLAB_CI, resolve_includes
from lib.ci_masking import IndentTree, parse_lines, strip_comment
from lib.ci_triggers import gitlab_line_triggers, workflow_events
from lib.command_index import ci_config_files, read_config, repo_files

State = Literal["gated", "ungated", "unknown"]

GATES = frozenset({"paths", "pr_types", "label", "event_if", "tags_only", "event_trigger", "changes"})
REASON_UNRESOLVED = "trigger_unresolved"
REASON_UNREAD = "ci_jobs_unread"
# The non-ordinary events GitHub documents; only these gate. Any other name (an expression such
# as ``${{ fromJSON(x) }}``, a typo) reads unknown, never gated.
GATING_EVENTS = frozenset({
    "schedule", "workflow_dispatch", "repository_dispatch", "issue_comment", "issues",
    "pull_request_review", "pull_request_review_comment", "release", "discussion",
    "discussion_comment", "label", "milestone", "create", "delete", "deployment",
    "deployment_status", "check_run", "check_suite", "fork", "gollum", "page_build", "public",
    "registry_package", "status", "watch", "branch_protection_rule", "project", "project_card",
    "project_column", "member", "image_version",
})
# A paths pattern that matches every file filters nothing.
_CATCH_ALL = frozenset({"**", "**/*", "*", "./**", "/**"})

AGENT_ACTIONS = frozenset({
    "anthropics/claude-code-action", "anthropics/claude-code-base-action",
    "openai/codex-action", "google-github-actions/run-gemini-cli",
})
PROVIDER_SECRETS = (
    "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "OPENAI_API_KEY", "AZURE_OPENAI_API_KEY",
    "GEMINI_API_KEY", "MISTRAL_API_KEY", "COHERE_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY",
    "TOGETHER_API_KEY", "XAI_API_KEY", "DEEPSEEK_API_KEY", "PERPLEXITY_API_KEY",
)
_ORDINARY = frozenset({"push", "pull_request", "pull_request_target", "merge_group"})
_CALLED = frozenset({"workflow_call", "workflow_run"})
_PR = frozenset({"pull_request", "pull_request_target"})
_BODY_KEYS = frozenset({"run", "script", "before_script", "after_script"})
_GITLAB_RESERVED = frozenset({
    "stages", "variables", "include", "workflow", "default", "image", "services", "cache",
    "before_script", "after_script",
})

_CMD = r"(?:^|[\s;&|(/=])"
_EVAL = re.compile(_CMD + r"""(?:promptfoo(?:@\S+)?\s+(?:eval|redteam)\b|deepeval\s+test\b|oaieval(?:set)?\b
                     |inspect\s+eval(?:-set)?\b|lm[_-]eval\b|ragas\b)""", re.X)
_CLI = re.compile(_CMD + r"(?:claude|@anthropic-ai/claude-code)\s+(?:[^;&|]*\s)?(?:-p|--print)(?:\s|$)")
_EVAL_TESTS = re.compile(r"\bpytest\b[^;&|]*\s-m\s+[\"']?evals?\b|\b(?:pytest|python3?|node|bun|deno|npx|tsx)\b"
                         r"[^;&|]*?(?:^|[\s=])(?:\./)?(?:[\w.-]+/)*evals/")
_INSTALL = re.compile(r"\b(?:install|add)\b")
_SECRET = re.compile(r"\b(?:" + "|".join(PROVIDER_SECRETS) + r")\b")
_USES = re.compile(r"""^["']?([\w.-]+/[\w.-]+)""")
_LABEL = re.compile(r"github\.event\.(?:pull_request\.labels|label\b)")
_EVENT_IF = re.compile(r"github\.event_name\s*==\s*['\"](\w+)['\"]")
# A condition on a value this reader does not follow: a step output, another job's output (a
# changed-files job), a variable or an input. It may be a gate, so it reads unknown.
_OPAQUE = re.compile(r"\b(?:steps|needs|vars|env|inputs)\.")
# A literal never-true if: (``false``, ``${{ false }}``): the step or job never runs.
_FALSE = re.compile(r"==\s*['\"]?false\b", re.IGNORECASE)
_NEVER = re.compile(r"""^(["']?)(?:false|\$\{\{\s*false\s*\}\})\1$""", re.IGNORECASE)
_REMOTE_WORKFLOW = re.compile(r"""^["']?[\w.-]+/[\w.-]+/\.github/workflows/""")
_LOCAL_WORKFLOW = re.compile(r"""^["']?\./\.github/workflows/([\w.-]+\.ya?ml)""")


class ModelJob(TypedDict):
    path: str
    line: int  # 1-based line of the job's key
    job: str
    platform: str  # github | gitlab
    signs: list[str]
    triggers: list[str]
    gates: list[str]
    state: State
    reason: NotRequired[str]  # unknown only: why the trigger or gate was not settled
    cancel_superseded: bool


class CiModelJobsBlock(TypedDict):
    available: bool
    files_read: int
    model_jobs: int
    gated: int
    unknown: int
    unread: int  # job-level remote reusable workflows and unresolved GitLab includes
    ungated: int | None
    ungated_reason: NotRequired[str]
    jobs: list[ModelJob]


class _Route(TypedDict):
    state: State
    triggers: list[str]
    gates: list[str]
    reason: str


def _tree(text: str) -> IndentTree:
    return IndentTree(parse_lines(text.splitlines()))


def _top(tree: IndentTree) -> dict[str, int]:
    return {line.key: i for i, line in enumerate(tree.lines)
            if line.structural and line.level == 0 and not line.dash and line.key}


def _child(tree: IndentTree, i: int, key: str) -> int | None:
    return next((k for k in tree.children(i) if tree.lines[k].key == key), None)


def _text(tree: IndentTree, i: int) -> str:
    """A key's value, joined with a block scalar's lines (``if: |``)."""
    value = tree.lines[i].value
    if value[:1] in ("|", ">"):
        return " ".join(tree.lines[j].value for j in range(i + 1, len(tree.lines)) if tree.owner[j] == i)
    return value


def _list(tree: IndentTree, i: int) -> list[str] | None:
    """A key's list of scalars (inline, flow or block), or None when it is not one."""
    value = tree.lines[i].value
    if value.startswith("{"):
        return None
    if value.startswith("["):
        return [w.strip(" '\"") for w in value.strip("[]").split(",") if w.strip(" '\"")]
    if value:
        return [value.strip("'\"")]
    children = tree.children(i)
    if not all(tree.lines[k].dash and not tree.lines[k].key for k in children):
        return None
    return [tree.lines[k].value.strip("'\"") for k in children]


def signs_in(tree: IndentTree, lines: range) -> list[str]:
    """The sign tokens in ``lines`` of one job (see the module doc)."""
    found: set[str] = set()
    for i in lines:
        line = tree.lines[i]
        raw = line.value
        if not raw or raw.startswith("#"):
            continue
        if line.key == "uses":
            match = _USES.match(raw)
            if match and match.group(1).lower() in AGENT_ACTIONS:
                found.add("agent_action")
        if tree.lines[tree.body(i)].key not in _BODY_KEYS:
            # an env or with: entry, never a command passing the secret on (a deploy storing it)
            if _SECRET.search(raw if line.structural else strip_comment(raw)) or _SECRET.search(line.key):
                found.add("provider_secret")
            continue
        command = raw if not line.structural or line.dash or line.key in _BODY_KEYS else ""
        if not command or command[:1] in ("|", ">") or _INSTALL.search(command):
            continue
        if _EVAL.search(command):
            found.add("eval_harness")
        if _CLI.search(command):
            found.add("agent_cli")
        if _EVAL_TESTS.search(command):
            found.add("eval_tests")
    return sorted(found)


def _event_gates(tree: IndentTree, event: int) -> list[str] | None:
    """The gates on one ordinary event's filters ([] for none), or None when not read."""
    line = tree.lines[event]
    if line.value and line.value not in ("null", "~"):
        return None
    name = line.key
    filters = {tree.lines[k].key: k for k in tree.children(event)}
    gates: list[str] = []
    for key in ("paths", "paths-ignore"):
        if key not in filters:
            continue
        patterns = _list(tree, filters[key])
        if patterns is None:
            return None
        # ``paths`` holding a catch-all, or ``paths-ignore`` of nothing or only catch-alls, filters nothing.
        catch = any(p in _CATCH_ALL for p in patterns)
        if (key == "paths" and patterns and not catch) or (
                key == "paths-ignore" and patterns and not all(p in _CATCH_ALL for p in patterns)):
            gates.append("paths")
    gates = sorted(set(gates))
    if name in _PR and "types" in filters:
        types = _list(tree, filters["types"])
        if types is None:
            return None
        if "synchronize" not in types:
            gates.append("label" if types == ["labeled"] else "pr_types")
    if name == "push" and ("tags" in filters or "tags-ignore" in filters) and not (
            "branches" in filters or "branches-ignore" in filters):
        gates.append("tags_only")
    return gates


def _workflow_route(tree: IndentTree, text: str, direct: bool = False) -> _Route | None:
    """How a GitHub workflow's ``on`` fires its jobs, before any job ``if:``. With ``direct``, the
    events other than ``workflow_call`` / ``workflow_run`` only, None when there are none."""
    route = _Route(state="unknown", triggers=[], gates=[], reason="on_unread")
    events = workflow_events(text)
    if events is None:
        return None if direct else route
    if direct:
        events = [e for e in events if e not in _CALLED]
        if not events:
            return None
    ordinary = sorted(set(events) & _ORDINARY)
    route["triggers"] = ordinary or sorted(set(events))
    if not set(events) <= _ORDINARY | _CALLED | GATING_EVENTS:
        return route  # an event name this reader does not know: on_unread
    if not ordinary:
        if set(events) & _CALLED:
            route["reason"] = "called_workflow"
            return route
        return _Route(state="gated", triggers=route["triggers"], gates=["event_trigger"], reason="")
    on = next((i for i, line in enumerate(tree.lines)
               if line.structural and line.level == 0 and line.key in ("on", "true")), None)
    if on is None or tree.lines[on].value.startswith("{"):
        route["reason"] = "filters_unread"
        return route
    keyed = {tree.lines[k].key: k for k in tree.children(on) if not tree.lines[k].dash}
    gates: set[str] = set()
    ungated: list[str] = []
    for name in ordinary:
        found = _event_gates(tree, keyed[name]) if name in keyed else []
        if found is None:
            route["reason"] = "filters_unread"
            return route
        gates.update(found)
        if not found:
            ungated.append(name)
    if ungated:
        return _Route(state="ungated", triggers=ungated, gates=[], reason="")
    return _Route(state="gated", triggers=ordinary, gates=sorted(gates), reason="")


def _if_gate(condition: str) -> str | None:
    """``label``, ``event_if``, ``unknown`` (a gate joined with ``||``, or a condition on a step
    or job output, a variable or an input) or None for an ``if:`` that limits nothing counted."""
    events = _EVENT_IF.findall(condition)
    gate = ("label" if _LABEL.search(condition)
            else "event_if" if events and not set(events) & _ORDINARY else None)
    if gate is not None:  # a gate joined with ||, negated or compared with false may not limit the job
        return "unknown" if "||" in condition or "!" in condition or _FALSE.search(condition) else gate
    return "unknown" if _OPAQUE.search(condition) else None


def _narrowed(route: _Route, condition: str) -> _Route:
    """An ungated route cut to the ordinary events a job ``if:`` requires (``github.event_name ==
    'pull_request'``); none of its ungated events left means the job runs only when gated."""
    events = set(_EVENT_IF.findall(condition))
    if route["state"] != "ungated" or not events or "||" in condition or "!" in condition or not events <= _ORDINARY:
        return route
    left = [e for e in route["triggers"] if e in events]
    if not left:
        return _Route(state="gated", triggers=sorted(events), gates=["event_if"], reason="")
    return _Route(state="ungated", triggers=left, gates=[], reason="")


def _apply_if(route: _Route, gate: str | None) -> _Route:
    if route["state"] != "ungated" or gate is None:
        return route
    if gate == "unknown":
        return _Route(state="unknown", triggers=route["triggers"], gates=[], reason="condition_unread")
    return _Route(state="gated", triggers=route["triggers"], gates=[gate], reason="")


def _step_gate(tree: IndentTree, job: int, lines: Sequence[int]) -> str | None:
    """The gate shared by the steps holding every sign line, or None."""
    steps = _child(tree, job, "steps")
    if steps is None:
        return None
    gates: set[str | None] = set()
    for item in tree.items(steps):
        span = range(item[0], max(tree.subtree(k).stop for k in item))
        if not any(i in span for i in lines):
            continue
        condition = next((k for k in item if tree.lines[k].key == "if"), None)
        gates.add(_if_gate(_text(tree, condition)) if condition is not None else None)
    if not gates or any(i < steps for i in lines):  # a sign outside the steps (job env)
        return None
    return gates.pop() if len(gates) == 1 else "unknown" if "unknown" in gates else None


def _cancels(tree: IndentTree, i: int | None) -> bool:
    if i is None:
        return False
    flag = _child(tree, i, "cancel-in-progress")
    return flag is not None and tree.lines[flag].value.strip("'\"").lower() == "true"


class _Workflow:
    def __init__(self, rel: str, text: str) -> None:
        self.rel, self.text, self.tree = rel, text, _tree(text)
        self.top = _top(self.tree)
        route = _workflow_route(self.tree, text)
        assert route is not None
        self.route = route
        self.direct = _workflow_route(self.tree, text, direct=True)
        events = set(workflow_events(text) or [])
        self.called = "workflow_call" in events
        self.run_by_workflow = "workflow_run" in events
        self.cancels = _cancels(self.tree, self.top.get("concurrency"))

    def jobs(self) -> list[int]:
        jobs = self.top.get("jobs")
        return [k for k in self.tree.children(jobs) if self.tree.lines[k].key] if jobs is not None else []

    def job_route(self, job: int, sign_lines: Sequence[int], base: _Route | None = None) -> _Route:
        """The job's route: ``base`` (else the workflow's), then its own ``if:`` and step gates."""
        key = _child(self.tree, job, "if")
        condition = _text(self.tree, key) if key is not None else ""
        start = base if base is not None else self.route
        route = _apply_if(_narrowed(start, condition), _if_gate(condition) if condition else None)
        return _apply_if(route, _step_gate(self.tree, job, sign_lines) if sign_lines else None)


def _never(tree: IndentTree, keys: Sequence[int]) -> bool:
    return any(tree.lines[k].key == "if" and _NEVER.match(tree.lines[k].value) for k in keys)


def _composite_signs(uses: str, read_local: ReadLocal | None) -> list[str]:
    """The signs in a repo-local action a step uses (``uses: ./.github/actions/x``), one level,
    read through ``read_local`` as ``lib.ci_composite`` reads one (tracked files only)."""
    if read_local is None or not uses.startswith("./") or _LOCAL_WORKFLOW.match(uses):
        # a reusable workflow (``./.github/workflows/x.yml``) is scanned as a workflow of its own
        return []
    for rel in local_action_paths(uses):
        text = read_local(rel)
        if text is not None:
            tree = _tree(text)
            return signs_in(tree, range(len(tree.lines)))
    return []


def _job_signs(tree: IndentTree, job: int, read_local: ReadLocal | None = None,
               shared: dict[int, list[str]] | None = None) -> dict[int, list[str]]:
    """The sign lines of ``job`` and their signs, outside a step (or job) whose ``if:`` is never
    true; a local action's signs sit on its ``uses:`` line, and ``shared`` (a workflow-level
    ``env:`` secret, which reaches every job) is added as it is."""
    if _never(tree, tree.children(job)):
        return {}
    steps = _child(tree, job, "steps")
    dead = {i for item in (tree.items(steps) if steps is not None else []) if _never(tree, item)
            for i in range(item[0], max(tree.subtree(k).stop for k in item))}
    found: dict[int, list[str]] = dict(shared or {})
    for i in tree.subtree(job):
        if i in dead:
            continue
        signs = set(signs_in(tree, range(i, i + 1)))
        if tree.lines[i].key == "uses":
            signs.update(_composite_signs(tree.lines[i].value.strip("'\""), read_local))
        if signs:
            found[i] = sorted(signs)
    return found


def _sign_lines(tree: IndentTree, job: int) -> list[int]:
    return sorted(_job_signs(tree, job))


def _secret_only(route: _Route, signs: Sequence[str]) -> _Route:
    """A job whose only sign is a provider secret may only store or pass the key on (a deploy):
    ungated, it reads unknown (``secret_only``), never ungated."""
    if route["state"] == "ungated" and list(signs) == ["provider_secret"]:
        return _Route(state="unknown", triggers=route["triggers"], gates=[], reason="secret_only")
    return route


def _worst(routes: Sequence[_Route]) -> _Route:
    for state in ("ungated", "unknown"):
        hit = [r for r in routes if r["state"] == state]
        if hit:
            return hit[0]
    return routes[0]


def _workflow_env(flow: "_Workflow") -> dict[int, list[str]]:
    env = flow.top.get("env")
    lines = flow.tree.subtree(env) if env is not None else range(0)
    return {i: ["provider_secret"] for i in lines if "provider_secret" in signs_in(flow.tree, range(i, i + 1))}


def _base(flow: _Workflow, callers: Sequence[_Route]) -> _Route:
    """A called workflow's route before its jobs' conditions: the worst of its own direct
    events and its local callers' routes; unknown with no route this repository settles."""
    routes = list(callers)
    if flow.direct is not None:
        routes.append(flow.direct)
    if flow.run_by_workflow:
        routes.append(_Route(state="unknown", triggers=["workflow_run"], gates=[], reason="called_workflow"))
    if not callers and flow.direct is None:
        return flow.route
    return _worst(routes)


def _github(repo: Path, files: list[str], read_local: ReadLocal | None = None) -> tuple[list[ModelJob], int]:
    texts = {rel: read_config(repo, rel) for rel in files if rel.startswith(".github/")}
    unread = sum(not text.strip() for text in texts.values())  # a workflow not read may hold an ungated job
    flows = {rel: _Workflow(rel, text) for rel, text in texts.items() if text.strip()}
    callers: dict[str, list[_Route]] = {}
    for flow in flows.values():
        for job in flow.jobs():
            uses = _child(flow.tree, job, "uses")
            value = flow.tree.lines[uses].value if uses is not None else ""
            local = _LOCAL_WORKFLOW.match(value)
            if local:
                callers.setdefault(local.group(1), []).append(flow.job_route(job, []))
            elif _REMOTE_WORKFLOW.match(value):
                unread += 1
    found: list[ModelJob] = []
    for flow in flows.values():
        shared = _workflow_env(flow)
        for job in flow.jobs():
            signs = _job_signs(flow.tree, job, read_local, shared)
            if not signs or set(signs) <= set(shared) and _child(flow.tree, job, "uses") is not None:
                continue  # nothing beyond the workflow env, in a job that only calls a workflow
            lines = sorted(signs)
            base = _base(flow, callers.get(flow.rel.rsplit("/", 1)[-1], [])) if (
                flow.called or flow.run_by_workflow) else None
            route = flow.job_route(job, lines, base)
            cancels = flow.cancels or _cancels(flow.tree, _child(flow.tree, job, "concurrency"))
            names = sorted({s for found_signs in signs.values() for s in found_signs})
            found.append(_entry(flow.rel, flow.tree, job, "github", names, _secret_only(route, names), cancels))
    return found, unread


def _entry(rel: str, tree: IndentTree, job: int, platform: str, signs: list[str],
           route: _Route, cancels: bool) -> ModelJob:
    entry = ModelJob(path=rel, line=job + 1, job=tree.lines[job].key, platform=platform,
                     signs=signs, triggers=route["triggers"],
                     gates=route["gates"], state=route["state"], cancel_superseded=cancels)
    if route["state"] == "unknown":
        entry["reason"] = route["reason"]
    return entry


def without_changes(text: str) -> str:
    """``text`` with every ``rules:`` item holding ``changes:`` made unable to run the job: one
    that runs becomes ``- if: $CI_COMMIT_TAG`` (unset in a branch or merge request pipeline),
    one with ``when: never`` becomes ``- when: never``. Line numbers are kept."""
    lines = text.splitlines()
    tree = _tree(text)
    for i, line in enumerate(tree.lines):
        if not (line.structural and line.key == "rules" and not line.value):
            continue
        for item in tree.items(i):
            keys = {tree.lines[k].key: k for k in item}
            if "changes" not in keys or not tree.lines[item[0]].dash:
                continue
            never = "when" in keys and tree.lines[keys["when"]].value.strip("'\"") == "never"
            end = max(tree.subtree(k).stop for k in item)
            lead = " " * tree.lines[item[0]].indent
            lines[item[0]] = lead + ("- when: never" if never else "- if: $CI_COMMIT_TAG")
            for j in range(item[0] + 1, end):
                lines[j] = ""
    return "\n".join(lines)


def _gitlab_route(before: str, after: str) -> _Route:
    triggers = ["push_or_merge_request"]
    if before == "not_ordinary":
        return _Route(state="gated", triggers=[], gates=["event_trigger"], reason="")
    if after == "not_ordinary":
        return _Route(state="gated", triggers=triggers, gates=["changes"], reason="")
    if after == "ordinary":
        return _Route(state="ungated", triggers=triggers, gates=[], reason="")
    return _Route(state="unknown", triggers=triggers, gates=[], reason="rules_unread")


def _gitlab(rel: str, text: str) -> list[ModelJob]:
    tree = _tree(text)
    before, after = gitlab_line_triggers(text), gitlab_line_triggers(without_changes(text))
    found: list[ModelJob] = []
    for key, job in _top(tree).items():
        if key in _GITLAB_RESERVED or key.startswith("."):
            continue
        lines = _sign_lines(tree, job)
        if lines:  # a line inside the job: the job's own key line reads as outside any job
            names = sorted({s for i in lines for s in signs_in(tree, range(i, i + 1))})
            route = _secret_only(_gitlab_route(before[lines[0]], after[lines[0]]), names)
            found.append(_entry(rel, tree, job, "gitlab", names, route, False))
    return found


def scan_ci_model_jobs(repo_root: Path, ghsync_root: Path | None = None) -> CiModelJobsBlock:
    """Build the ``ci_model_jobs`` block (see the module doc)."""
    repo = repo_root.resolve()
    tracked = repo_files(repo)
    files = ci_config_files(tracked)

    def read_local(rel: str) -> str | None:  # tracked and inside the checkout, as lib.ci_checks reads
        if rel not in tracked or not (repo / rel).resolve().is_relative_to(repo):
            return None
        return read_config(repo, rel)

    jobs, unread = _github(repo, files, read_local)
    if GITLAB_CI in files:
        text = read_config(repo, GITLAB_CI)
        unread += not text.strip()
        jobs += _gitlab(GITLAB_CI, text)
        resolution = resolve_includes(repo, ghsync_root)
        unread += resolution.unresolved
        for included in resolution.files:
            for job in _gitlab(GITLAB_CI, included.text):
                job["line"] = included.line
                jobs.append(job)
    count = {state: sum(j["state"] == state for j in jobs) for state in ("gated", "ungated", "unknown")}
    block = CiModelJobsBlock(available=True, files_read=len(files), model_jobs=len(jobs),
                             gated=count["gated"], unknown=count["unknown"], unread=unread,
                             ungated=count["ungated"], jobs=jobs)
    if not count["ungated"] and jobs and (count["unknown"] or unread):
        block["ungated"] = None
        block["ungated_reason"] = REASON_UNRESOLVED if count["unknown"] else REASON_UNREAD
    return block
