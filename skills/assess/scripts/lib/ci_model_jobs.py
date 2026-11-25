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
Claude Code CLI in print mode or on a plugin eval suite (``agent_cli``: ``claude -p``,
``claude plugin eval``, never one named in an ``echo`` or a comment); a step uses an agent action
(``agent_action``, ``AGENT_ACTIONS``, also inside a repo-local action the step uses); an ``env:``,
``with:``, ``secrets:`` or ``variables:`` entry of the job or the workflow names a model-provider
secret (``provider_secret``, ``PROVIDER_SECRETS``; a step ``name:`` naming one is not a sign); or a
command runs ``pytest -m eval`` or a path under ``evals/`` (``eval_tests``). An install line
(``pip install ragas``, a package manager's install or add subcommand) is not a sign,
and a step or job whose ``if:`` is never true is not read.

**A gate** (``GATES``): ``paths`` (``paths:`` or ``paths-ignore:`` on every ordinary event that
fires the job; a ``paths:`` list read in order, so a catch-all ``**`` then ``!docs/**`` filters
and a catch-all last does not), ``pr_types`` (pull request ``types:`` without ``synchronize``), ``label`` (a
``labeled`` type, or a job or step ``if:`` on the pull request's labels), ``event_if`` (a job
``if:`` that requires a schedule or manual event; sign steps under different recognised gates
combine them), ``tags_only`` (a push filtered to tags),
``event_trigger`` (no ordinary event fires the workflow, each one a known ``GATING_EVENTS`` name:
schedule, ``workflow_dispatch``, comments) and GitLab ``changes`` (a ``rules:changes:`` the job cannot run without).
``concurrency`` with ``cancel-in-progress: true`` is recorded as ``cancel_superseded`` and is not
a gate: the last push of every update still runs the job.

**Each job** is ``gated``, ``ungated`` or ``unknown`` (with a ``reason`` token). Ungated means
its trigger is definitely ordinary and every gate is definitely absent; anything this reader
cannot settle is unknown, never ungated or gated. A job whose only sign is a provider secret
(a deploy that stores the key) reads unknown (``secret_only``) rather than ungated.

**Reusable workflows.** A called workflow's jobs take each local caller's route, resolved to a
fixed point (A calls B calls C), with the job's own conditions applied to each route before
the worst is taken; a ``workflow_call`` no local job calls adds an unknown route. A caller job
is never a model job itself, and one whose ``if:`` is never true calls nothing. A remote call
counts in ``unread`` only under a route that is not gated. **GitLab** jobs are read as GitLab
merges them (``lib.ci_merged_view`` over ``ci_includes.merged_config``, #1180): a root override,
an included job and an ``extends:`` template's ``script:`` read together; a merge that is not
settled reads unknown (``merge_unread``).

Block (``ci_model_jobs`` in run-context.json)::

    {"available": True, "files_read": int, "model_jobs": int | None, "gated": int, "unknown": int,
     "unread": int, "ungated": int | None, "ungated_reason": str (only when ungated is None),
     "jobs": [{"path", "line", "job", "platform" (github | gitlab), "signs": [...],
               "triggers": [...], "gates": [...], "state", "reason" (unknown only),
               "cancel_superseded": bool}]}

``ungated`` is the count of ungated jobs whenever one is ungated. With none ungated it is null
when a job is unknown (``trigger_unresolved``) or a job or include could not be read
(``unread`` above 0: ``ci_jobs_unread``), else 0. With no model-backed job, ``model_jobs`` is 0
and the condition is not measured (``no_model_jobs``, ``signal_grades.EMPTY_DENOMINATORS``);
with none found but ``unread`` above 0, ``model_jobs`` is null and the reason is ``ci_jobs_unread``.
Tests: ``tests/test_ci_model_jobs.py``, ``tests/test_ci_model_jobs_followups.py`` and
``tests/test_ci_model_jobs_plugin_eval.py`` (#1249).
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, NotRequired, TypedDict

from lib.ci_action_inputs import ReadLocal, local_action_paths
from lib.ci_includes import GITLAB_CI, merged_config, resolve_includes
from lib.ci_masking import IndentTree, parse_lines, strip_comment
from lib.ci_merged_view import merged_jobs, merged_view
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
# A paths pattern that matches every file filters nothing. GitHub's ``*`` stops at ``/``: not one.
_CATCH_ALL = frozenset({"**", "**/*", "./**", "/**"})

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
# A path a runner is told to leave out (``pytest --ignore=evals/``): keeping evals out of a job is the
# remedy, never a sign.
_EXCLUDED = re.compile(r"--(?:ignore|ignore-glob|deselect|exclude)(?:=|\s+)\S+")
# An install or add subcommand of a package manager (``pip install``, ``sudo python3 -m pip install``,
# ``poetry add``, ``sh -c "pip install x"``), never a bare word of a prompt (``claude -p 'add tests'``).
_INSTALL = re.compile(r"(?:^|[\s;&|(\"'/])(?:pip(?:\d+(?:\.\d+)?)?|pipx|pipenv|uv|poetry|pdm|rye|pixi|hatch|conda|mamba|"
                      r"micromamba|npm|pnpm|yarn|bun|deno|apt(?:-get)?|apk|dnf|yum|brew|gem|cargo|go|composer|dotnet)"
                      r"\s+(?:(?:-\S+|global|tools?|pip|python)\s+){0,3}(?:install|add)(?=[\s\"']|$)")
# ``claude plugin eval`` (#1249): options before or after ``plugin`` and ``eval``, also through
# ``npx @anthropic-ai/claude-code``. Read on ``_command_text``, so an echo or comment naming it is no sign.
_OPTS = r"(?:[ \t]+-[\w-]+(?:=\S+|[ \t]+(?!-|plugin\b|eval\b)[^\s;&|]+)?)*"
_PLUGIN_EVAL = re.compile(r"(?:^|[\s;&|(/=])(?:claude|@anthropic-ai/claude-code(?:@\S+)?)" + _OPTS
                          + r"[ \t]+plugin" + _OPTS + r"[ \t]+eval(?=[\s;&|)]|$)", re.M)
_ECHO = re.compile(r"(?:^|(?<=[;&|(\n]))[ \t]*(?:echo|printf)\b[^;&|\n]*", re.M)
_SHELL_C = re.compile(r"(?:^|[;&|(\n])\s*$|\s-c\s*$")
# The keys whose entries hand a job a secret; a key named anywhere else (a step ``name:``) is no sign.
_SECRET_KEYS = frozenset({"env", "with", "secrets", "variables"})
_SECRET = re.compile(r"\b(?:" + "|".join(PROVIDER_SECRETS) + r")\b")
_USES = re.compile(r"""^["']?([\w.-]+/[\w.-]+)""")
_LABEL = re.compile(r"github\.event\.(?:pull_request\.labels|label\b)")
_EVENT_IF = re.compile(r"github\.event_name\s*==\s*['\"](\w+)['\"]")
# A condition on a value this reader does not follow: a step output, another job's output (a
# changed-files job), a variable or an input. It may be a gate, so it reads unknown.
_OPAQUE = re.compile(r"\b(?:steps|needs|vars|env|inputs)\.")
# ``<operand> == false``: it negates a gate only when the operand is the label or event test itself
# (a call or a parenthesised test, or a label path), never a guard beside it (``draft == false``).
_FALSE = re.compile(r"(\S+)\s*==\s*['\"]?false\b|\bfalse['\"]?\s*==\s*(\S+)", re.IGNORECASE)
# A literal never-true if: (``false``, ``${{ false }}``): the step or job never runs.
_NEVER = re.compile(r"""^(["']?)(?:false|\$\{\{\s*false\s*\}\})\1$""", re.IGNORECASE)
_QUOTED_ON = re.compile(r"""^(["'])on\1\s*:(.*)$""")
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
    model_jobs: int | None  # null when none is found but a job or file was not read
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
        if not value.endswith("]"):  # a flow list continued on the next line: not read whole
            return None
        return [w.strip(" '\"") for w in value.strip("[]").split(",") if w.strip(" '\"")]
    if value:
        return [value.strip("'\"")]
    children = tree.children(i)
    if not all(tree.lines[k].dash and not tree.lines[k].key for k in children):
        return None
    return [tree.lines[k].value.strip("'\"") for k in children]


def _block_text(tree: IndentTree, i: int) -> str:
    """A block scalar's lines as the shell reads them: a ``\\`` continuation (or a folded ``>``)
    joins the next line. A ``- >`` list item's lines may sit at its own key column, where
    ``parse_lines`` does not own them, so its lines are those indented past its dash."""
    body = [tree.lines[j].value for j in range(i + 1, len(tree.lines)) if tree.owner[j] == i]
    if tree.lines[i].dash:
        end = next((j for j in range(i + 1, len(tree.lines))
                    if tree.lines[j].structural and tree.lines[j].indent <= tree.lines[i].indent), len(tree.lines))
        body = [(f"{x.key}: " if x.key else "") + x.value for x in tree.lines[i + 1:end]]
    text = ("\n" if tree.lines[i].value[:1] == "|" else " ").join(body)
    return re.sub(r"\\\n", " ", text)


def _command_text(text: str) -> str:
    """``text`` with what the shell does not run blanked: a quoted argument (``-m "..."``) becomes
    ``_`` unless it is itself a command (the whole value, or after ``sh -c``), a ``#`` comment
    is cut, and an ``echo`` or ``printf`` is dropped with its arguments."""
    out: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in "'\"":
            end = i + 1
            while end < len(text) and text[end] != ch:
                end += 2 if ch == '"' and text[end] == "\\" else 1
            inner = text[i + 1:end]
            out.append(_command_text(inner) if _SHELL_C.search("".join(out)) else "_")
            i = end + 1
            continue
        if ch == "#" and (i == 0 or text[i - 1].isspace()):
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
            continue
        out.append(ch)
        i += 1
    return _ECHO.sub(" ", "".join(out))


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
            # an env, with:, secrets: or variables: entry, never a command passing the secret on
            # (a deploy storing it) nor a step name naming it
            handed = line.key in _SECRET_KEYS or any(tree.lines[a].key in _SECRET_KEYS for a in tree.ancestors(i))
            if handed and (_SECRET.search(raw if line.structural else strip_comment(raw)) or _SECRET.search(line.key)):
                found.add("provider_secret")
            continue
        command = raw if not line.structural or line.dash or line.key in _BODY_KEYS else ""
        if command[:1] in ("|", ">"):  # a block body is read whole, so a continued command is one
            if _PLUGIN_EVAL.search(_command_text(_block_text(tree, i))):
                found.add("agent_cli")
            continue
        if not command:
            continue
        if _CLI.search(command) or (line.structural and _PLUGIN_EVAL.search(_command_text(command))):
            found.add("agent_cli")  # counts even beside an install (or a prompt naming one)
        if _INSTALL.search(command):
            continue
        if _EVAL.search(command):
            found.add("eval_harness")
        if _EVAL_TESTS.search(_EXCLUDED.sub(" ", command)):
            found.add("eval_tests")
    return sorted(found)


def _includes_all(patterns: Sequence[str]) -> bool:
    """Whether a ``paths:`` list, read in order (the last match wins), lets every file through:
    ``['**', '!docs/**']`` does not, as ``paths-ignore: ['docs/**']`` does not."""
    every = False
    for pattern in patterns:
        every = False if pattern.startswith("!") else every or pattern in _CATCH_ALL
    return every


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
        # ``paths-ignore`` of nothing or only catch-alls filters nothing; ``paths`` filters nothing
        # when, read in order as GitHub does, a catch-all is not followed by a ``!`` exclusion.
        if (key == "paths" and patterns and not _includes_all(patterns)) or (
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


def _on_value(key: str, value: str) -> str | None:
    """The value of a top-level ``on`` key, or None for another key. A quoted ``"on":`` (which
    yamllint's ``truthy`` rule asks for) parses with no key and the whole line as its value."""
    if key in ("on", "true"):
        return value
    quoted = _QUOTED_ON.match(value) if not key else None
    return quoted.group(2).strip() if quoted else None


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
    on = next((i for i, line in enumerate(tree.lines)  # ``on``, ``"on"`` or ``'on'``, as workflow_events reads it
               if line.structural and line.level == 0 and _on_value(line.key, line.value) is not None), None)
    if on is None or str(_on_value(tree.lines[on].key, tree.lines[on].value)).startswith("{"):
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
        return "unknown" if _negated(condition) else gate
    return "unknown" if _OPAQUE.search(condition) else None


def _negated(condition: str) -> bool:
    """A condition joined with ``||``, holding a negation, or comparing the label or event test
    itself with false (``contains(...labels...) == false``; ``draft == false`` beside it is not)."""
    return "||" in condition or "!" in condition or any(
        _is_test(_operand(condition, match)) for match in _FALSE.finditer(condition))


def _operand(condition: str, match: re.Match[str]) -> str:
    """The operand compared with false: a token, or the whole call or parenthesised group a
    ``)`` closes (``contains(...)`` or ``(a == b)``), in either order (``false == x``)."""
    if match.group(1) is None:
        start = match.start(2)
        if "(" not in match.group(2):
            return match.group(2)
        depth = 0
        for i in range(start, len(condition)):
            depth += {"(": 1, ")": -1}.get(condition[i], 0)
            if depth == 0 and condition[i] == ")":
                return condition[start:i + 1]
        return condition[start:]
    end = match.end(1)
    if not match.group(1).endswith(")"):
        return match.group(1)
    depth = 0
    for i in range(end - 1, -1, -1):
        depth += {")": 1, "(": -1}.get(condition[i], 0)
        if depth == 0:
            return condition[i:end]
    return condition[:end]


def _is_test(operand: str) -> bool:
    return bool(_LABEL.search(operand)) or "event_name" in operand


def _narrowed(route: _Route, condition: str) -> _Route:
    """An ungated route cut to the ordinary events a job ``if:`` requires (``github.event_name ==
    'pull_request'``); none of its ungated events left means the job runs only when gated. Only a
    positive requirement narrows: ``(github.event_name == 'push') == false`` does not."""
    events = set(_EVENT_IF.findall(condition))
    if route["state"] != "ungated" or not events or _negated(condition) or not events <= _ORDINARY:
        return route
    left = [e for e in route["triggers"] if e in events]
    if not left:
        return _Route(state="gated", triggers=sorted(events), gates=["event_if"], reason="")
    return _Route(state="ungated", triggers=left, gates=[], reason="")


def _apply_if(route: _Route, gate: str | list[str] | None) -> _Route:
    """``route`` under a job or step gate: one token, ``unknown``, or the step gates combined."""
    if route["state"] != "ungated" or gate is None:
        return route
    if gate == "unknown":
        return _Route(state="unknown", triggers=route["triggers"], gates=[], reason="condition_unread")
    return _Route(state="gated", triggers=route["triggers"], gates=[gate] if isinstance(gate, str) else gate, reason="")


def _step_gate(tree: IndentTree, job: int, lines: Sequence[int]) -> str | list[str] | None:
    """The gates on the steps holding every sign line: ``unknown`` when one is not read, None when
    one step has none, else each recognised gate (a ``label`` step beside an ``event_if`` one)."""
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
    if "unknown" in gates:
        return "unknown"
    return None if None in gates else sorted(g for g in gates if g is not None)


def _cancels(tree: IndentTree, i: int | None) -> bool:
    if i is None:
        return False
    flag = _child(tree, i, "cancel-in-progress")
    return flag is not None and tree.lines[flag].value.strip("'\"").lower() == "true"


class _Workflow:
    def __init__(self, rel: str, text: str) -> None:
        self.rel, self.text, self.tree = rel, text, _tree(text)
        self.name = rel.rsplit("/", 1)[-1]
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


def _bases(flow: _Workflow, callers: Sequence[_Route]) -> list[_Route | None]:
    """The routes a workflow's jobs start from, before their own conditions: the workflow's
    own (``[None]``) unless it is called; a called workflow's local callers' routes, its own
    direct events, and an unknown route for ``workflow_run`` or for a ``workflow_call`` no local
    job calls (callers in other repositories run it on events this reader cannot see)."""
    if not (flow.called or flow.run_by_workflow):
        return [None]
    routes: list[_Route | None] = list(callers)
    if flow.direct is not None:
        routes.append(flow.direct)
    if flow.run_by_workflow:
        routes.append(_Route(state="unknown", triggers=["workflow_run"], gates=[], reason="called_workflow"))
    if flow.called and not callers:
        routes.append(_Route(state="unknown", triggers=flow.route["triggers"], gates=[], reason="called_workflow"))
    return routes


def _routes(flow: _Workflow, job: int, lines: Sequence[int], bases: Sequence[_Route | None]) -> list[_Route]:
    """The job's route from each base, its own ``if:`` and step gates applied to each."""
    return [flow.job_route(job, lines, base) for base in bases]


def _callers(flows: dict[str, _Workflow]) -> tuple[dict[str, list[_Route]], int]:
    """Each local reusable workflow's caller routes, and the remote calls under a route that is
    not gated (``unread``). A caller job whose ``if:`` is never true calls nothing. A caller's
    own incoming routes are resolved first (A calls B calls C), to a fixed point."""
    callers: dict[str, list[_Route]] = {}
    remote = 0
    for _ in range(len(flows) + 1):
        found: dict[str, list[_Route]] = {}
        remote = 0
        for flow in flows.values():
            bases = _bases(flow, callers.get(flow.name, []))
            for job in flow.jobs():
                uses = _child(flow.tree, job, "uses")
                value = flow.tree.lines[uses].value if uses is not None else ""
                local = _LOCAL_WORKFLOW.match(value)
                if _never(flow.tree, flow.tree.children(job)) or not (local or _REMOTE_WORKFLOW.match(value)):
                    continue
                routes = _routes(flow, job, [], bases)
                if local:
                    into = found.setdefault(local.group(1), [])
                    into.extend(r for r in routes if r not in into)
                elif any(r["state"] != "gated" for r in routes):
                    remote += 1
        if found == callers:
            break
        callers = found
    return callers, remote


def _github(repo: Path, files: list[str], read_local: ReadLocal | None = None) -> tuple[list[ModelJob], int]:
    texts = {rel: read_config(repo, rel) for rel in files if rel.startswith(".github/")}
    unread = sum(not text.strip() for text in texts.values())  # a workflow not read may hold an ungated job
    flows = {rel: _Workflow(rel, text) for rel, text in texts.items() if text.strip()}
    callers, remote = _callers(flows)
    unread += remote
    found: list[ModelJob] = []
    for flow in flows.values():
        shared = _workflow_env(flow)
        for job in flow.jobs():
            uses = _child(flow.tree, job, "uses")
            if uses is not None and _LOCAL_WORKFLOW.match(flow.tree.lines[uses].value):
                continue  # a local reusable-workflow call: the callee is scanned on its own
            signs = _job_signs(flow.tree, job, read_local, shared)
            if not signs or set(signs) <= set(shared) and uses is not None:
                continue  # nothing beyond the workflow env, in a job that only calls a workflow
            # The step gate reads the lines that show a model call; a provider key alone (a job
            # env, a step that only sets it) proves none, so it neither gates nor ungates the job.
            calls = [i for i, found_signs in signs.items() if found_signs != ["provider_secret"]]
            lines = sorted(calls or signs)
            route = _worst(_routes(flow, job, lines, _bases(flow, callers.get(flow.name, []))))
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
    """``text`` with every ``rules:`` item holding ``changes:`` made one that never matches,
    ``- if: $CI_COMMIT_TAG`` (unset in a branch or merge request pipeline): the pipeline of a change
    outside those paths. A ``when: never`` exclusion (skip docs-only changes) then lets the later
    rules decide, as GitLab does for a source change. Line numbers are kept."""
    lines = text.splitlines()
    tree = _tree(text)
    for i, line in enumerate(tree.lines):
        if not (line.structural and line.key == "rules" and not line.value):
            continue
        for item in tree.items(i):
            keys = {tree.lines[k].key: k for k in item}
            if "changes" not in keys or not tree.lines[item[0]].dash:
                continue
            end = max(tree.subtree(k).stop for k in item)
            lead = " " * tree.lines[item[0]].indent
            lines[item[0]] = lead + "- if: $CI_COMMIT_TAG"
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


def _gitlab(rel: str, text: str, unsettled: frozenset[int] = frozenset()) -> list[ModelJob]:
    """The model jobs of one GitLab CI text; a job whose sign line is ``unsettled`` (a merge
    ``lib.ci_merged_view`` could not resolve) reads unknown (``merge_unread``)."""
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
            if lines[0] in unsettled:
                route = _Route(state="unknown", triggers=route["triggers"], gates=[], reason="merge_unread")
            found.append(_entry(rel, tree, job, "gitlab", names, route, False))
    return found


def _gitlab_merged(repo: Path, ghsync_root: Path | None) -> tuple[list[ModelJob], int]:
    """The GitLab model jobs as GitLab merges them (#1180, ``lib.ci_merged_view``): each file with
    its merged jobs blanked, then each merged job rendered once with its effective keys, so a
    root override, an included job and an ``extends:`` template's ``script:`` read together.
    An included job is reported at the repository's own include line; a merged job at its
    definition in the repository's own file, else at the include line that led to it."""
    text = read_config(repo, GITLAB_CI)
    resolution = resolve_includes(repo, ghsync_root)
    unread = int(not text.strip()) + resolution.unresolved
    config = merged_config(repo, ghsync_root)
    view = merged_view(config) if config is not None and config.sources[0] == text else None
    at = [0] + [included.line for included in resolution.files]
    sources = list(view.texts) if view is not None else [text, *(f.text for f in resolution.files)]
    jobs: list[ModelJob] = []
    for n, source in enumerate(sources):
        for job in _gitlab(GITLAB_CI, source):
            job["line"] = at[n] or job["line"]
            jobs.append(job)
    if view is not None and config is not None:
        merged = merged_jobs(config)
        for job in _gitlab(GITLAB_CI, view.rendered, view.unsettled):
            of = merged[int(job["job"].removeprefix("job"))]
            own = next((d for d in of.defs if d.source == 0), None)
            job["job"] = of.name
            job["line"] = own.start + 1 if own is not None else at[of.defs[-1].source]
            jobs.append(job)
    return jobs, unread


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
        gitlab_jobs, gitlab_unread = _gitlab_merged(repo, ghsync_root)
        jobs += gitlab_jobs
        unread += gitlab_unread
    count = {state: sum(j["state"] == state for j in jobs) for state in ("gated", "ungated", "unknown")}
    block = CiModelJobsBlock(available=True, files_read=len(files), model_jobs=len(jobs),
                             gated=count["gated"], unknown=count["unknown"], unread=unread,
                             ungated=count["ungated"], jobs=jobs)
    if not count["ungated"] and (count["unknown"] or unread):
        block["ungated"] = None
        block["ungated_reason"] = REASON_UNRESOLVED if count["unknown"] else REASON_UNREAD
        if not jobs:  # nothing found, but an unread job or file may call a model: not no_model_jobs
            block["model_jobs"] = None
    return block
