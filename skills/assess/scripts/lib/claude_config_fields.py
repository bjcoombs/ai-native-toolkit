"""The Claude Code frontmatter fields ``lib.claude_config`` checks against.

A pinned snapshot of the documented field sets, taken by hand from the pages in
``SOURCE_URLS`` on ``SNAPSHOT_DATE``. The scan never fetches the docs at run
time: the deterministic core must give the same answer offline and on every
rerun, so updating these sets is one reviewed edit to this file (re-read the
pages, change the sets, move the date). Claude Code ignores a frontmatter field
it does not recognise without reporting an error, which is why a stale set here
shows up as a false ``unknown_key`` finding rather than a crash; the report
prints the snapshot date beside the findings for that reason.
"""
from __future__ import annotations

import re

SNAPSHOT_DATE = "2026-10-08"

SOURCE_URLS: tuple[str, ...] = (
    "https://code.claude.com/docs/en/skills.md",
    "https://code.claude.com/docs/en/sub-agents.md",
    "https://code.claude.com/docs/en/plugins-reference.md",
)

# Skills (`SKILL.md`) and command files share one frontmatter reference
# (skills.md, "Frontmatter reference").
SKILL_FIELDS: frozenset[str] = frozenset({
    "name", "description", "when_to_use", "argument-hint", "arguments",
    "allowed-tools", "disallowed-tools", "model", "effort", "context", "agent",
    "background", "hooks", "paths", "shell", "metadata", "license",
    "compatibility", "disable-model-invocation", "user-invocable",
})

# "A command file in .claude/commands/ accepts the same fields except name and
# paths" (skills.md). The two are reported as their own finding kind, not as
# unknown keys, because they are valid in a skill.
COMMAND_UNSUPPORTED_FIELDS: frozenset[str] = frozenset({"name", "paths"})
COMMAND_FIELDS: frozenset[str] = SKILL_FIELDS - COMMAND_UNSUPPORTED_FIELDS

# Subagent files (sub-agents.md, "Supported frontmatter fields").
AGENT_FIELDS: frozenset[str] = frozenset({
    "name", "description", "tools", "disallowedTools", "model",
    "permissionMode", "maxTurns", "skills", "mcpServers", "hooks", "memory",
    "background", "omitClaudeMd", "effort", "isolation", "color",
    "initialPrompt", "experimental",
})

# Keys documented inside the agent `experimental` map.
AGENT_EXPERIMENTAL_FIELDS: frozenset[str] = frozenset({"cacheTtl"})

# "Ignored for plugin subagents" (sub-agents.md, on each of these rows).
PLUGIN_AGENT_IGNORED_FIELDS: frozenset[str] = frozenset({
    "permissionMode", "hooks", "mcpServers", "initialPrompt",
})

# Fields whose value is a closed set. Keyed by field name; `kinds` restricts a
# set to the file kinds that document it (`skill` covers command files too).
EFFORT_VALUES: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")
ENUM_FIELDS: dict[str, tuple[frozenset[str], tuple[str, ...]]] = {
    "effort": (frozenset({"skill", "command", "agent"}), EFFORT_VALUES),
    "context": (frozenset({"skill", "command"}), ("fork",)),
    "shell": (frozenset({"skill", "command"}), ("bash", "powershell")),
    "color": (frozenset({"agent"}), (
        "red", "blue", "green", "yellow", "purple", "orange", "pink", "cyan",
    )),
    "memory": (frozenset({"agent"}), ("user", "project", "local")),
    "isolation": (frozenset({"agent"}), ("worktree",)),
    "permissionMode": (frozenset({"agent"}), (
        "default", "acceptEdits", "auto", "dontAsk", "bypassPermissions",
        "plan", "manual",
    )),
    "experimental.cacheTtl": (frozenset({"agent"}), ("5m", "1h")),
}

# `model` takes an alias, `inherit`, or a full model ID (sub-agents.md; skills
# accept "the same values as /model"). The alias list moves with each model
# release and provider IDs vary (Bedrock `us.anthropic.claude-...-v1:0`, Vertex
# `claude-...@date`), so the value is matched as a pattern rather than a fixed
# list: an alias, optionally with the `[1m]` context suffix, or any ID that
# contains `claude-`. Anything else is an unsupported value.
MODEL_ALIASES: tuple[str, ...] = (
    "inherit", "default", "best", "opusplan", "sonnet", "opus", "haiku", "fable",
)
MODEL_PATTERN = re.compile(
    r"^(?:" + "|".join(MODEL_ALIASES) + r")(?:\[1m\])?$"
    r"|^[a-z0-9.:@/_-]*claude-[a-z0-9.:@/_-]+(?:\[1m\])?$",
    re.IGNORECASE,
)
