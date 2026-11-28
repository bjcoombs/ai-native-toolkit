"""Agent settings and hook files across harnesses (#846), read by ``agent_ops`` and ``agent_harness``.

Claude Code is not the only harness a repository configures. The settings and hook files of the
others are read here and treated as equivalent to the Claude ones, so the existing permission and
hook conditions count them and no harness adds a condition of its own:

- ``.claude/settings.json`` / ``.claude/settings.local.json`` (``claude_code``): permission
  ``allow`` / ``deny`` / ``ask`` counts, hook events, sandbox and ``defaultMode``, read as strict
  JSON as before.
- ``.gemini/settings.json`` (``gemini``): hook events, in Claude Code's grouped shape
  (``{event: [{matcher, hooks: [{type, command}]}]}``).
- ``.cursor/hooks.json`` (``cursor``) and ``.github/hooks/*.json`` (``copilot``, the hook files of
  the Copilot coding agent, Copilot CLI and VS Code): hook events in the flat shape
  (``{event: [{command}]}``, or Copilot's ``{type: command, bash, powershell}``), where a hook
  with no ``type`` is a command hook.
- ``.vscode/settings.json`` (``vscode``): listed only when it sets
  ``chat.tools.terminal.autoApprove``; each rule approved counts as an ``allow`` entry and each
  rule refused (``false``) as a ``deny`` entry. A ``.vscode/settings.json`` with no such key is
  editor state and is not listed.

These files are read as JSON with comments and trailing commas, as their tools read them.
``settings_files`` returns one ``AgentOpsSettingsFile`` per file found, each with its ``tool``. A
hook event counts in ``hook_events`` only when it holds a hook that does something (#922,
``hook_acts``). ``hooks_present`` is true on a tracked file with a hook event that counts, or a
tracked script under ``.claude/hooks/``.

A hook file renamed with a ``.disabled`` suffix (``.github/hooks/build.json.disabled``,
``.cursor/hooks.json.disabled``) is a decision to turn the hooks off, not an absence:
``disabled_hook_files`` lists the tracked ones that still hold a hook that would act.
``read_hooks_disabled`` turns them into a ``DeclineRecord`` (source ``hooks_disabled``) covering
``HOOK_CONDITIONS``, only when no live hook counts anywhere, so a disabled file never takes a met
hook condition away.

``auto_approve`` reads VS Code's terminal auto-approve rules. A rule is broad when it approves
every command: the key ``*`` (or any key of only ``*`` and spaces), or a regex key such as
``/.*/``, ``/^.+$/`` or a bare anchor ``/^/``. ``chat.tools.autoApprove: true`` and
``chat.tools.global.autoApprove: true`` approve every tool, the terminal included, and are broad too. ``agent_harness`` reports
each as a ``broad_auto_approve`` finding, the twin of Claude Code's ``broad_allow``.

Pure stdlib plus ``git_churn.tracked_files``; never raises on a malformed file. Tests:
``tests/test_harness_settings.py``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, NamedTuple

from lib.counts_only.block_shapes import AgentOpsDir, AgentOpsSettingsFile
from lib.graph.import_graph_typescript import read_jsonc
from lib.history.git_churn import is_tracked, tracked_files
from lib.instructions.command_index import NOOP_COMMANDS
from lib.scoring.level_result_types import DeclineRecord

CLAUDE_SETTINGS = (".claude/settings.json", ".claude/settings.local.json")
# Settings and hook files at fixed paths, with their tool token and hook shape. Order is the
# report order; Copilot's hook directory follows them.
_FIXED: tuple[tuple[str, str], ...] = (
    (".claude/settings.json", "claude_code"),
    (".claude/settings.local.json", "claude_code"),
    (".gemini/settings.json", "gemini"),
    (".cursor/hooks.json", "cursor"),
    (".vscode/settings.json", "vscode"),
)
COPILOT_HOOKS = ".github/hooks"
HOOKS_DIR = ".claude/hooks"
DISABLED = ".disabled"
HOOKS_DISABLED = "hooks_disabled"
# The default model's conditions that read agent_ops.summary.hooks_present (tests check the list).
HOOK_CONDITIONS: tuple[str, ...] = ("layer-8-agent-ops.2",)
# Tools whose hook entries sit directly in the event list; a typeless entry is a command hook.
_FLAT = frozenset({"cursor", "copilot"})
_JSONC = frozenset({"gemini", "cursor", "copilot", "vscode"})
# permissions.defaultMode values Claude Code documents
# (https://code.claude.com/docs/en/permission-modes); any other string reads "other".
_DEFAULT_MODES = frozenset({
    "default", "manual", "acceptEdits", "plan", "auto", "dontAsk", "bypassPermissions",
})
# Hook types other than command that act on their own (#922); a typeless grouped hook does not.
_ACTING_TYPES = frozenset({"prompt", "agent", "http", "mcp_tool"})
_COMMAND_KEYS = ("command", "bash", "powershell")
TERMINAL_AUTO_APPROVE = "chat.tools.terminal.autoApprove"
# Settings that approve every tool, the terminal included, when true.
_ALL_TOOLS = ("chat.tools.autoApprove", "chat.tools.global.autoApprove")
_MATCH_ALL = re.compile(r"^/(?:\^?(?:\.|\[\\s\\S\]|\[\^\])[*+]\$?|\^|\$|)/[a-z]*$")


def _count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _acts(command: Any) -> bool:
    parts = command.replace("&&", ";").split(";") if isinstance(command, str) else []
    return not all(not p.strip() or p.strip() in NOOP_COMMANDS for p in parts)


def hook_acts(hook: Any, flat: bool = False) -> bool:
    """A hook that does something: a known non-command type (``prompt``, ``agent``, ``http``,
    ``mcp_tool``), or a command (``command``, or Copilot's ``bash`` / ``powershell``) other than
    ``true``, ``:`` or ``exit 0``. An echo counts: a hook's output can add context or a decision.
    A grouped hook with no or an unknown ``type`` does not count; a ``flat`` one with no type is a
    command hook."""
    if not isinstance(hook, dict):
        return False
    kind = hook.get("type")
    if kind in _ACTING_TYPES:
        return True
    if kind != "command" and not (flat and kind is None):
        return False
    return any(_acts(hook.get(key)) for key in _COMMAND_KEYS)


def event_acts(groups: Any, flat: bool = False) -> bool:
    """An event that holds at least one hook that does something (#922): in matcher groups, or
    listed directly when ``flat``."""
    if not isinstance(groups, list):
        return False
    if flat:
        return any(hook_acts(h, flat=True) for h in groups)
    return any(isinstance(g, dict) and isinstance(g.get("hooks"), list)
               and any(hook_acts(h) for h in g["hooks"]) for g in groups)


def hook_events(hooks: Any, flat: bool = False) -> int:
    """The events of a ``hooks`` map that hold a hook that does something."""
    return sum(1 for groups in hooks.values() if event_acts(groups, flat)) if isinstance(hooks, dict) else 0


class AutoApprove(NamedTuple):
    allow: int  # rules approved
    deny: int  # rules refused
    broad: list[str]  # the setting names or rule keys that approve every command


def _approves(value: Any) -> bool:
    return value is True or (isinstance(value, dict) and value.get("approve") is True)


def is_broad_rule(key: str) -> bool:
    """A terminal auto-approve key that matches every command: ``*`` (only ``*`` and spaces), or a
    match-all regex such as ``/.*/``, ``/^.+$/``, ``/[\\s\\S]*/`` or a bare anchor (``/^/``, ``/$/``)."""
    stripped = key.strip()
    return bool(stripped) and (set(stripped) <= {"*", " "} or bool(_MATCH_ALL.match(stripped)))


def auto_approve(data: dict[str, Any]) -> AutoApprove | None:
    """VS Code's terminal auto-approve rules in one settings object, or None when it sets neither
    ``chat.tools.terminal.autoApprove`` nor an all-tools auto-approve."""
    rules = data.get(TERMINAL_AUTO_APPROVE)
    all_tools = [name for name in _ALL_TOOLS if data.get(name) is True]
    if rules is None and not all_tools:
        return None
    allow = deny = 0
    broad = list(all_tools)
    if isinstance(rules, dict):
        for key, value in rules.items():
            if _approves(value):
                allow += 1
                if is_broad_rule(str(key)):
                    broad.append(str(key))
            elif value is False or (isinstance(value, dict) and value.get("approve") is False):
                deny += 1
    elif rules is True:  # not a documented shape, but it can only mean every command
        allow += 1
        broad.append(TERMINAL_AUTO_APPROVE)
    return AutoApprove(allow, deny, broad)


def load(path: Path, jsonc: bool) -> dict[str, Any] | None:
    """A settings or hook file as a JSON object, or None when absent, unreadable or not an object."""
    if not path.is_file():
        return None
    if jsonc:
        return read_jsonc(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _entry(repo_root: Path, rel: str, tool: str, tracked: frozenset[Path] | None) -> AgentOpsSettingsFile | None:
    """One settings or hook file's guardrail counts, or None when absent (or, for VS Code, when
    it sets no auto-approve)."""
    path = repo_root / rel
    if not path.is_file():
        return None
    data = load(path, tool in _JSONC)
    approve = auto_approve(data) if tool == "vscode" and data is not None else None
    if tool == "vscode" and approve is None:
        return None  # editor settings only: no agent configuration to report
    entry: AgentOpsSettingsFile = {
        "path": rel, "tool": tool, "tracked": is_tracked(path, tracked), "parse_ok": data is not None,
        "allow_count": 0, "deny_count": 0, "ask_count": 0, "hook_events": 0,
        "sandbox_configured": False,
    }
    if data is None:
        return entry
    if approve is not None:
        entry["allow_count"], entry["deny_count"] = approve.allow, approve.deny
    if tool == "claude_code":
        permissions = data.get("permissions")
        if isinstance(permissions, dict):
            entry["allow_count"] = _count(permissions.get("allow"))
            entry["deny_count"] = _count(permissions.get("deny"))
            entry["ask_count"] = _count(permissions.get("ask"))
            mode = permissions.get("defaultMode")
            if isinstance(mode, str):
                entry["default_mode"] = mode if mode in _DEFAULT_MODES else "other"
        entry["sandbox_configured"] = "sandbox" in data
    if tool != "vscode":
        entry["hook_events"] = hook_events(data.get("hooks"), flat=tool in _FLAT)
    return entry


def _copilot_hook_files(repo_root: Path, suffix: str = "") -> list[str]:
    root = repo_root / COPILOT_HOOKS
    try:
        names = sorted(p.name for p in root.iterdir() if p.is_file()) if root.is_dir() else []
    except OSError:
        return []
    return [f"{COPILOT_HOOKS}/{n}" for n in names if n.endswith(".json" + suffix)]


def settings_files(repo_root: Path, tracked: frozenset[Path] | None) -> list[AgentOpsSettingsFile]:
    """Every harness settings or hook file found, tracked or not, in report order."""
    found = [(rel, tool) for rel, tool in _FIXED] + [(rel, "copilot") for rel in _copilot_hook_files(repo_root)]
    return [e for rel, tool in found if (e := _entry(repo_root, rel, tool, tracked)) is not None]


def scan_dir(repo_root: Path, rel: str, tracked: frozenset[Path] | None) -> AgentOpsDir:
    """Count files (and how many are tracked) under ``rel``; dot files are skipped."""
    root = repo_root / rel
    file_count = tracked_count = 0
    if root.is_dir():
        try:
            for p in sorted(root.rglob("*")):
                if not p.is_file() or p.name.startswith("."):
                    continue
                file_count += 1
                tracked_count += is_tracked(p, tracked)
        except OSError:
            pass
    return {"path": rel, "present": root.is_dir(), "file_count": file_count, "tracked_count": tracked_count}


def hooks_present(settings: list[AgentOpsSettingsFile], hooks_dir: AgentOpsDir) -> bool:
    """A tracked settings or hook file with a hook event that counts, or a tracked hook script."""
    return any(s["tracked"] and s["hook_events"] > 0 for s in settings) or hooks_dir["tracked_count"] > 0


def disabled_hook_files(repo_root: Path, tracked: frozenset[Path] | None) -> list[str]:
    """Tracked hook files renamed with a ``.disabled`` suffix that still hold a hook that would act."""
    candidates = [".cursor/hooks.json" + DISABLED, *_copilot_hook_files(repo_root, DISABLED)]
    out = []
    for rel in candidates:
        path = repo_root / rel
        data = load(path, jsonc=True)
        if data is not None and is_tracked(path, tracked) and hook_events(data.get("hooks"), flat=True):
            out.append(rel)
    return out


def read_hooks_disabled(repo_root: Path) -> DeclineRecord | None:
    """The decline record of disabled hook files, or None when there is none or a live hook counts
    (absence never declines, and a disabled file never takes a met hook condition away)."""
    root = repo_root.resolve()
    tracked = tracked_files(root)
    files = disabled_hook_files(root, tracked)
    if not files or hooks_present(settings_files(root, tracked), scan_dir(root, HOOKS_DIR, tracked)):
        return None
    return {"source": HOOKS_DISABLED, "conditions": list(HOOK_CONDITIONS), "declined_by": None,
            "declined_at": None, "version": None, "reason": "agent hooks disabled in " + ", ".join(files)}
