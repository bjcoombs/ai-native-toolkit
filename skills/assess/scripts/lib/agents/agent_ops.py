"""Scan encoded agent-operations guardrails (Layer 8 workflow-maturity evidence).

A team that runs agents in parallel or autonomously needs the operational
guardrails encoded in the repo, not in one operator's head: pre-approved
permission allowlists, hooks that intercept tool calls, sandbox/deny rules, and
routine/loop definitions that make repeated agent work a committed artifact.
This module scans the repo-observable subset of those guardrails:

- ``.claude/settings.json`` / ``.claude/settings.local.json`` - permission
  ``allow`` / ``deny`` / ``ask`` entry counts, hook events, sandbox config.
- ``.claude/hooks/`` - hook scripts on disk.
- ``.claude/workflows/`` and ``.claude/routines/`` - routine/loop definitions
  (evidence of repeated, encoded agent work cycles).
- ``.github/workflows/`` - workflows that run an agent on a schedule (#815) or on an
  issue, pull request, review or discussion event (#847): ``agent_workflows``, one entry per
  workflow with its ``path``, ``triggers``, ``signs`` (``agent_action``, ``agent_cli``,
  ``model_name``, ``gh_aw``) and ``via`` (the local reusable workflows followed to reach the
  agent). A GitHub Agentic Workflows source (``.github/workflows/*.md`` with an ``on:`` in its
  frontmatter) counts by construction and its ``.lock.yml`` is not counted again. A provider
  secret alone, a comment, or an ``if:`` or ``name:`` mention is not a sign.
  ``summary.routines_present`` is true on a routine dir file or a scheduled agent workflow;
  ``summary.agent_work_present``, the maturity model's routines condition, is true on either or
  on an event-triggered agent workflow, so the two kinds are one condition, never two. It is
  null (``agent_workflows_unread``) when it would read false but a scheduled or event-triggered
  workflow calls a reusable workflow in another repository whose path names an agent
  (``claude``, ``codex``, ``agent``, ``ai`` ...; ``agent_workflows_unread`` counts those), since
  the agent may run there. Ordinary shared CI from another repository leaves it false. A
  prompt in quotes is set aside before the install-line check, so ``codex exec "add a label"``
  still counts.

Only **git-tracked** artifacts count toward the summary booleans, mirroring the
Layer 0 rule: a settings file present on disk but uncommitted reaches no clone,
so it is reported (``tracked: false``) but never credited. ``.claude/agents/``
and ``.claude/skills/`` are deliberately NOT scanned here - they already feed
Layer 0; this block feeds Layer 8 only, so the two never double-count.

Pure stdlib. ``scan_agent_ops`` never raises (callers wrap in ``_safe`` anyway).

Layer 8 workflow-maturity evidence: scans the repo-observable agent-operations
guardrails - `.claude/settings.json` / `.claude/settings.local.json` permission
`allow`/`deny`/`ask` counts, hook events, sandbox config; scripts under
`.claude/hooks/`; routine definitions under `.claude/workflows/` /
`.claude/routines/`. Summary booleans (`permissions_encoded`, `hooks_present`,
`routines_present`) credit **git-tracked** evidence only (a hook event counts only when
it holds a hook that does something: a `prompt`, `agent`, `http` or `mcp_tool` hook, or a command other than
`true`, `:` or `exit 0`; an empty event list or a typeless hook does not count, #922), mirroring the Layer 0
rule (an uncommitted settings file reaches no clone). Deliberately excludes
`.claude/agents/` and `.claude/skills/` - those are Layer 0's evidence - so the
two layers never double-count. Pure stdlib JSON/filesystem reads plus
`git_churn.tracked_files`.

Central guardrails (#1153): managed settings override every repository's. Under the
opt-in `managed_settings` input (`--managed-settings`, or a single repository's
`[agent_ops] managed_settings` config key; never on by default, since it measures the
machine), `permissions_encoded` also reads true when a managed source on the machine running
the check lists a permission entry (`lib.agents.managed_settings`, kept as the
`managed` sub-block; `permissions_source` says which). When the repository has none
and a managed source exists but cannot be read, it reads null with
`permissions_encoded_reason: managed_settings_unreadable`, so the tally reports the
condition not measured, never unmet. With no managed source on the machine, or with
the option off (`managed.state: not_read`), the repository reading stands. A
settings file's `permissions.defaultMode` (auto mode included) is recorded as
`default_mode`, information only: it never fails the practice, and deny rules
apply in every mode.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from lib.ci.ci_action_inputs import local_action_paths
from lib.ci.ci_masking import IndentTree, parse_lines, strip_comment
from lib.ci.ci_model_jobs import signs_in
from lib.ci.ci_triggers import workflow_events
from lib.command_index import NOOP_COMMANDS, ci_config_files, read_config, repo_files
from lib.counts_only.block_shapes import AgentWorkflow
from lib.agents.managed_settings import managed_permissions, not_read, read_managed_settings
from lib.git_churn import is_tracked, tracked_files

# Candidate settings files, repo-root-relative. Order is the report order.
_SETTINGS_PATHS = (
    ".claude/settings.json",
    ".claude/settings.local.json",
)

# Directories whose committed contents evidence encoded routine/loop work.
_ROUTINE_DIRS = (
    ".claude/workflows",
    ".claude/routines",
)

_HOOKS_DIR = ".claude/hooks"

# permissions.defaultMode values Claude Code documents
# (https://code.claude.com/docs/en/permission-modes); any other string reads "other".
_DEFAULT_MODES = frozenset({
    "default", "manual", "acceptEdits", "plan", "auto", "dontAsk", "bypassPermissions",
})
UNREADABLE_MANAGED = "managed_settings_unreadable"
UNREAD_WORKFLOWS = "agent_workflows_unread"
# Hook types other than command that act on their own (#922); a typeless hook does not.
_ACTING_TYPES = frozenset({"prompt", "agent", "http", "mcp_tool"})


def _count_list(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _hook_acts(hook: Any) -> bool:
    """A hook that does something: a known non-command type (``prompt``, ``agent``, ``http``,
    ``mcp_tool``), or a
    command other than ``true``, ``:`` or ``exit 0``. An echo counts: a hook's output can add
    context or a decision. A hook with no or an unknown ``type`` does not count."""
    if not isinstance(hook, dict):
        return False
    if hook.get("type") in _ACTING_TYPES:
        return True
    if hook.get("type") != "command":
        return False
    command = hook.get("command")
    parts = command.replace("&&", ";").split(";") if isinstance(command, str) else []
    return not all(not p.strip() or p.strip() in NOOP_COMMANDS for p in parts)


def _event_acts(groups: Any) -> bool:
    """An event whose matcher groups hold at least one hook that does something (#922)."""
    return isinstance(groups, list) and any(
        isinstance(g, dict) and isinstance(g.get("hooks"), list) and any(map(_hook_acts, g["hooks"]))
        for g in groups)


def _scan_settings_file(
    repo_root: Path, rel: str, tracked: frozenset[Path] | None
) -> dict[str, Any] | None:
    """Parse one settings file into its guardrail counts, or None when absent."""
    path = repo_root / rel
    if not path.is_file():
        return None
    entry: dict[str, Any] = {
        "path": rel,
        "tracked": is_tracked(path, tracked),
        "parse_ok": False,
        "allow_count": 0,
        "deny_count": 0,
        "ask_count": 0,
        "hook_events": 0,
        "sandbox_configured": False,
    }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return entry
    if not isinstance(data, dict):
        return entry
    entry["parse_ok"] = True
    permissions = data.get("permissions")
    if isinstance(permissions, dict):
        entry["allow_count"] = _count_list(permissions.get("allow"))
        entry["deny_count"] = _count_list(permissions.get("deny"))
        entry["ask_count"] = _count_list(permissions.get("ask"))
        mode = permissions.get("defaultMode")
        if isinstance(mode, str):
            entry["default_mode"] = mode if mode in _DEFAULT_MODES else "other"
    hooks = data.get("hooks")
    if isinstance(hooks, dict):
        entry["hook_events"] = sum(1 for groups in hooks.values() if _event_acts(groups))
    entry["sandbox_configured"] = "sandbox" in data
    return entry


def _scan_dir(
    repo_root: Path, rel: str, tracked: frozenset[Path] | None
) -> dict[str, Any]:
    """Count files (and how many are tracked) directly under ``rel``."""
    root = repo_root / rel
    file_count = 0
    tracked_count = 0
    if root.is_dir():
        try:
            for p in sorted(root.rglob("*")):
                if not p.is_file() or p.name.startswith("."):
                    continue
                file_count += 1
                if is_tracked(p, tracked):
                    tracked_count += 1
        except OSError:
            pass
    return {
        "path": rel,
        "present": root.is_dir(),
        "file_count": file_count,
        "tracked_count": tracked_count,
    }


# -- agent workflows (#815, #847) ----------------------------------------------

# Events that start an agent on an issue, a pull request, a review or a discussion (#847).
EVENT_TRIGGERS = frozenset({
    "issues", "issue_comment", "pull_request", "pull_request_target", "pull_request_review",
    "pull_request_review_comment", "discussion", "discussion_comment",
})
# GitHub Agentic Workflows (gh-aw) trigger names beyond GitHub's own: a slash command is a comment
# event, and a bare frequency (``on: daily``) is a fuzzy schedule.
_GH_AW_EVENTS = frozenset({"command", "slash_command"})
_GH_AW_SCHEDULE = re.compile(r"^(?:hourly|daily|weekly|monthly|every\b)", re.IGNORECASE)
_CMD = r"(?:^|[\s;&|(/=\"'])"
# A headless agent CLI. ``claude -p`` is read here too, after quoted prompt text is set aside,
# so a prompt that says "add" or "install" does not hide it.
_AGENT_CLI = re.compile(_CMD + r"""(?:(?:codex|@openai/codex)(?:@\S+)?\s+(?:exec|e)\b
    |(?:claude|@anthropic-ai/claude-code)(?:@\S+)?\s+(?:[^;&|]*\s)?(?:-p|--print)(?:\s|$)
    |(?:gemini|copilot|cursor-agent|@google/gemini-cli|@github/copilot)(?:@\S+)?\s+(?:[^;&|]*\s)?(?:-p|--prompt|--print)(?:[\s=]|$)
    |opencode(?:@\S+)?\s+run\b)""", re.X)
# A model named in a command, an env or a with: value (``OZ_MODEL=claude-4-8-opus-high``).
_MODEL = re.compile(r"(?<![\w.-])(?:claude-(?:\d|opus|sonnet|haiku|instant)|gpt-(?:\d|o\b|oss\b)"
                    r"|gemini-(?:\d|pro\b|flash\b|ultra\b))", re.IGNORECASE)
_INSTALL = re.compile(r"(?<![-\w])(?:install|add)\b")  # never a flag fragment (--add-dir)
# A quoted string (a prompt): an install verb inside one is prompt text, not an install.
_QUOTED = re.compile(r"\"[^\"]*\"|'[^']*'")
# A reusable workflow in another repository whose path names an agent: only such a call may hide
# one. Ordinary shared CI (``org/shared/.github/workflows/ci.yml``) does not leave the reading open.
_AGENT_NAME = re.compile(r"claude|codex|gemini|copilot|opencode|agent|llm|gpt|(?<![a-z])ai(?![a-z])",
                         re.IGNORECASE)
_SKIP_KEYS = frozenset({"if", "name", "description"})
_LOCAL_WORKFLOW = re.compile(r"""^["']?\./(\.github/workflows/[\w.-]+\.ya?ml)""")
_REMOTE_WORKFLOW = re.compile(r"""^["']?[\w.-]+/[\w.-]+/\.github/workflows/""")
_WORKFLOWS = ".github/workflows/"


def _frontmatter(text: str) -> str | None:
    """A gh-aw source's YAML frontmatter, or None when ``text`` has none."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    return "\n".join(lines[1:end]) if end is not None else None


def _kinds(events: list[str], gh_aw: bool) -> tuple[bool, bool]:
    """(scheduled, event-triggered) for a workflow's ``on`` event names."""
    scheduled = "schedule" in events or (gh_aw and any(_GH_AW_SCHEDULE.match(e) for e in events))
    event = bool(set(events) & EVENT_TRIGGERS) or (gh_aw and bool(set(events) & _GH_AW_EVENTS))
    return scheduled, event


class _Signs:
    """The agent signs of one workflow, following repo-local actions and reusable workflows."""

    def __init__(self, repo: Path, files: frozenset[str]) -> None:
        self.repo, self.files = repo, files
        self.cache: dict[str, tuple[frozenset[str], tuple[str, ...], bool]] = {}

    def read(self, rel: str) -> str | None:
        if rel not in self.files or not (self.repo / rel).resolve().is_relative_to(self.repo):
            return None
        return read_config(self.repo, rel)

    def lines(self, text: str) -> set[str]:
        """Signs on the lines of one YAML text: an agent action, an agent CLI or a model name.
        Comments, ``if:`` and ``name:`` values and install lines are never read."""
        tree = IndentTree(parse_lines(text.splitlines()))
        found = {s for s in signs_in(tree, range(len(tree.lines))) if s in ("agent_action", "agent_cli")}
        for i, line in enumerate(tree.lines):
            owner = tree.lines[tree.body(i)]
            if owner.key in _SKIP_KEYS or line.key in _SKIP_KEYS:
                continue
            value = line.value if line.structural else strip_comment(line.value)
            if not value or _INSTALL.search(_QUOTED.sub(" ", value)):
                continue
            if _AGENT_CLI.search(value):
                found.add("agent_cli")
            if _MODEL.search(value):
                found.add("model_name")
        return found

    def workflow(self, rel: str, text: str, seen: frozenset[str] = frozenset()) -> tuple[
            frozenset[str], tuple[str, ...], bool]:
        """(signs, local workflows the signs came through, calls a remote reusable workflow whose
        path names an agent)."""
        if not seen and rel in self.cache:
            return self.cache[rel]
        signs = self.lines(text)
        via: list[str] = []
        remote = False
        for raw in text.splitlines():
            stripped = strip_comment(raw.strip().removeprefix("- ").strip())
            if not stripped.startswith("uses:"):
                continue
            uses = stripped[len("uses:"):].strip()
            local = _LOCAL_WORKFLOW.match(uses)
            if local:  # a reusable workflow, never read as a composite action
                if local.group(1) in seen or local.group(1) == rel:
                    continue
                called = self.read(local.group(1))
                if called is not None:
                    more, deeper, far = self.workflow(local.group(1), called, seen | {rel})
                    remote = remote or far
                    if more:
                        signs |= more
                        via += [local.group(1), *deeper]
            elif _REMOTE_WORKFLOW.match(uses) and _AGENT_NAME.search(uses.split("@", 1)[0]):
                remote = True
            elif uses.strip("'\"").startswith("./"):
                for action in local_action_paths(uses.strip("'\"")):
                    action_text = self.read(action)
                    if action_text is not None:
                        signs |= self.lines(action_text)
                        break
        result = (frozenset(signs), tuple(sorted(set(via))), remote)
        if not seen:  # a result read inside a call chain leaves out the chain's own files
            self.cache[rel] = result
        return result


def scan_agent_workflows(repo_root: Path) -> tuple[list[AgentWorkflow], int]:
    """Committed scheduled and event-triggered agent workflows, and the count of such workflows
    that call another repository's reusable workflow whose path names an agent (not read, never
    counted).

    A GitHub Actions workflow counts when its ``on`` names ``schedule`` (#815) or an event in
    ``EVENT_TRIGGERS`` (#847) and it runs an agent: an agent action, a headless agent CLI or a
    named model, in its own lines, a repo-local action it uses or a local reusable workflow it
    calls (followed to any depth). A gh-aw source (``.github/workflows/*.md`` with an ``on:`` in
    its frontmatter) is an agent workflow by construction (sign ``gh_aw``); its compiled
    ``.lock.yml`` is not counted again. A model-provider secret alone is not a sign.
    """
    repo = repo_root.resolve()
    files = repo_files(repo)
    signs = _Signs(repo, files)
    found: list[AgentWorkflow] = []
    unread = 0
    sources = sorted(f for f in files if f.startswith(_WORKFLOWS) and f.endswith(".md")
                     and "/" not in f[len(_WORKFLOWS):])
    for rel in sources:
        front = _frontmatter(read_config(repo, rel))
        events = workflow_events(front) if front is not None else None
        if events:
            scheduled, event = _kinds(events, gh_aw=True)
            if scheduled or event:
                found.append(AgentWorkflow(path=rel, triggers=sorted(set(events)), signs=["gh_aw"],
                                           via=[], scheduled=scheduled, event=event))
    for rel in ci_config_files(files):
        if not rel.startswith(_WORKFLOWS) or rel.removesuffix(".lock.yml") + ".md" in sources:
            continue
        text = read_config(repo, rel)
        scheduled, event = _kinds(workflow_events(text) or [], gh_aw=False)
        if not (scheduled or event):
            continue
        agent, via, remote = signs.workflow(rel, text)
        if agent:
            found.append(AgentWorkflow(path=rel, triggers=sorted(set(workflow_events(text) or [])),
                                       signs=sorted(agent), via=list(via), scheduled=scheduled,
                                       event=event))
        elif remote:
            unread += 1
    return sorted(found, key=lambda w: w["path"]), unread


def scan_agent_ops(repo_root: Path, managed_settings: bool = False) -> dict[str, Any]:
    """Build the run-context ``agent_ops`` block.

    Returns ``settings`` (one entry per settings file found), ``hooks_dir``,
    ``routine_dirs``, ``agent_workflows`` and ``agent_workflows_unread``
    (``scan_agent_workflows``), and a ``summary`` of four booleans, each True only on
    **tracked** evidence, plus ``scheduled_agent_workflows`` and ``event_agent_workflows``:

    - ``permissions_encoded``: a tracked settings file carries at least one
      permission ``allow`` / ``deny`` / ``ask`` entry (``permissions_source``
      ``repository``), or a managed source does (``managed``); null with
      ``permissions_encoded_reason`` ``managed_settings_unreadable`` when neither does and
      a managed source could not be read, since a central rule may sit there.
    - ``hooks_present``: a tracked settings file configures a hook event that
      does something (``hook_events`` counts those, #922), or
      ``.claude/hooks/`` holds at least one tracked script (its body is not read).
    - ``routines_present``: a routine dir holds at least one tracked file, or a scheduled
      workflow runs an agent (#815).
    - ``agent_work_present``: ``routines_present``, or an issue, pull request, review or
      discussion workflow runs an agent (#847); the maturity model reads this one. Null with
      ``agent_work_present_reason`` ``agent_workflows_unread`` when false but such a workflow
      calls an agent-named reusable workflow in another repository.
    """
    repo_root = repo_root.resolve()
    tracked = tracked_files(repo_root)

    settings = [
        entry
        for rel in _SETTINGS_PATHS
        if (entry := _scan_settings_file(repo_root, rel, tracked)) is not None
    ]
    hooks_dir = _scan_dir(repo_root, _HOOKS_DIR, tracked)
    routine_dirs = [_scan_dir(repo_root, rel, tracked) for rel in _ROUTINE_DIRS]

    managed = read_managed_settings() if managed_settings else not_read()
    tracked_settings = [s for s in settings if s["tracked"]]
    in_repo = any(
        s["allow_count"] + s["deny_count"] + s["ask_count"] > 0
        for s in tracked_settings
    )
    hooks_present = (
        any(s["hook_events"] > 0 for s in tracked_settings)
        or hooks_dir["tracked_count"] > 0
    )
    workflows, unread = scan_agent_workflows(repo_root)
    scheduled = sum(w["scheduled"] for w in workflows)
    triggered = sum(w["event"] for w in workflows)
    routines_present = any(d["tracked_count"] > 0 for d in routine_dirs) or scheduled > 0
    agent_work: bool | None = routines_present or triggered > 0

    summary: dict[str, Any] = {
        "permissions_encoded": in_repo,
        "hooks_present": hooks_present,
        "routines_present": routines_present,
        "scheduled_agent_workflows": scheduled,
        "event_agent_workflows": triggered,
        "agent_work_present": agent_work,
    }
    if not agent_work and unread:
        summary["agent_work_present"] = None
        summary["agent_work_present_reason"] = UNREAD_WORKFLOWS
    if in_repo:
        summary["permissions_source"] = "repository"
    elif managed_permissions(managed):
        summary["permissions_encoded"] = True
        summary["permissions_source"] = "managed"
    elif managed["state"] == "unreadable":
        summary["permissions_encoded"] = None
        summary["permissions_encoded_reason"] = UNREADABLE_MANAGED

    return {
        "available": True,
        "settings": settings,
        "hooks_dir": hooks_dir,
        "routine_dirs": routine_dirs,
        "agent_workflows": workflows,
        "agent_workflows_unread": unread,
        "managed": managed,
        "summary": summary,
    }
