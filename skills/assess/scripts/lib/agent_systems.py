"""The agent systems and the instruction files each one loads (issue #768).

One table, ``SYSTEMS``, names every agent system ``/assess`` reads instruction
files for: its id (the ``tool`` of a ``nested_instructions`` file and the
system of an ``agent_consistency`` file), its display name, the locations it
loads and how a file of it points at a canonical source (``pointer``). The
systems ``lib.instruction_discovery`` already modelled (Claude Code, the
AGENTS.md readers, the Codex override, Gemini CLI, GitHub Copilot, Cursor) sit
beside the eight #768 adds, so a later reader (#846, non-Claude agent
configuration) takes the list from here rather than writing its own.

``match_system_file`` classifies one repo-relative path for the systems whose
files are not CLAUDE.md / AGENTS.md / GEMINI.md directory files or the
``.claude/rules``, ``.github/instructions`` and ``.cursor/rules`` rule files
(``instruction_discovery`` keeps those). Each rule below is the documented
scope of the tool, and every location is read at the repository root only:

* Windsurf: ``.windsurfrules`` (always loaded) and ``.windsurf/rules/*.md``,
  loaded always unless the frontmatter ``trigger`` is ``glob``, ``manual`` or
  ``model_decision`` (``globs`` names a glob rule's patterns);
* Cline: a ``.clinerules`` file, or each ``.md`` / ``.txt`` file directly in a
  ``.clinerules/`` directory, always loaded unless ``paths`` frontmatter scopes
  it;
* Aider: a root ``CONVENTIONS.md``, loaded only when ``.aider.conf.yml``
  names it under ``read:`` (Aider loads no file by default, so an unconfigured
  ``CONVENTIONS.md`` is listed but not always loaded);
* JetBrains Junie: ``.junie/guidelines.md``, always loaded;
* Amazon Q Developer: every ``.md`` under ``.amazonq/rules/``, always loaded;
* Kiro: ``.kiro/steering/*.md``, loaded always unless the frontmatter
  ``inclusion`` is ``fileMatch`` (patterns in ``fileMatchPattern``) or
  ``manual``;
* Continue: ``.continue/rules/*.md``, loaded always when ``alwaysApply`` is
  true, or when it is absent and no ``globs`` scope the rule;
* Zed: a root ``.rules`` file, always loaded.

Also classified here: the root ``.cursorrules`` (Cursor) and
``.github/copilot-instructions.md`` (Copilot), always loaded. Those two are
graded by ``lib.instruction_files`` as root locations, so discovery keeps them
for the consistency check only. Pure: no file access beyond the text passed in.

The agent systems table (#768): `SYSTEMS` holds one `AgentSystem` (`id`, `name`,
`locations`, `pointer` style: `import`, `kiro_include`, `text` or `aider_read`) per
system `/assess` reads instruction files for: `agents_md`, `claude_code`, `codex`,
`gemini`, `copilot`, `cursor`, `windsurf`, `cline`, `aider`, `junie`, `amazon_q`, `kiro`,
`continue`, `zed`, in that order (`SYSTEM_ORDER`). The single list for any reader of
non-Claude agent configuration (#846). `match_system_file(rel, text, aider_reads)`
classifies a root-located file into a `SystemMatch` (system, kind, globs,
always_loaded, body) by each tool's documented scope: `.windsurfrules` and
`.windsurf/rules/*.md` (`trigger` other than `always_on` is not always loaded; `globs`
for `glob`), `.clinerules` as a file or a directory of `.md` / `.txt` (`paths` scopes a
rule), `CONVENTIONS.md` (always loaded only when `.aider.conf.yml` names it under
`read:`, since Aider loads nothing by default), `.junie/guidelines.md`,
`.amazonq/rules/**/*.md`, `.kiro/steering/*.md` (`inclusion: fileMatch` with
`fileMatchPattern`, or `manual`, is not always loaded), `.continue/rules/*.md`
(`alwaysApply`, else always loaded only with no `globs`), `.rules` (Zed), plus the root
`.cursorrules` and `.github/copilot-instructions.md`. `might_match` is discovery's cheap
prefilter. `aider_read_files(config_text)` reads `read:` as a scalar, an inline list or a
block list. Pure. Tests: `tests/test_agent_consistency.py`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Literal

from lib.instruction_globs import is_true, scope_patterns, split_frontmatter

PointerStyle = Literal["import", "kiro_include", "text", "aider_read"]


@dataclass(frozen=True)
class AgentSystem:
    """One agent system: id, display name, the locations it loads, how it points."""

    id: str
    name: str
    locations: tuple[str, ...]
    pointer: PointerStyle


SYSTEMS: tuple[AgentSystem, ...] = (
    AgentSystem("agents_md", "AGENTS.md readers (Codex, the AGENTS.md spec)",
                ("AGENTS.md",), "text"),
    AgentSystem("claude_code", "Claude Code",
                ("CLAUDE.md", ".claude/CLAUDE.md", ".claude/rules/**/*.md"), "import"),
    AgentSystem("codex", "Codex (override file)", ("AGENTS.override.md",), "text"),
    AgentSystem("gemini", "Gemini CLI", ("GEMINI.md",), "import"),
    AgentSystem("copilot", "GitHub Copilot",
                (".github/copilot-instructions.md",
                 ".github/instructions/**/*.instructions.md"), "text"),
    AgentSystem("cursor", "Cursor", (".cursorrules", ".cursor/rules/**/*.mdc"), "text"),
    AgentSystem("windsurf", "Windsurf", (".windsurfrules", ".windsurf/rules/*.md"), "text"),
    AgentSystem("cline", "Cline", (".clinerules", ".clinerules/*.md"), "text"),
    AgentSystem("aider", "Aider", ("CONVENTIONS.md",), "aider_read"),
    AgentSystem("junie", "JetBrains Junie", (".junie/guidelines.md",), "text"),
    AgentSystem("amazon_q", "Amazon Q Developer", (".amazonq/rules/**/*.md",), "text"),
    AgentSystem("kiro", "Kiro", (".kiro/steering/*.md",), "kiro_include"),
    AgentSystem("continue", "Continue", (".continue/rules/*.md",), "text"),
    AgentSystem("zed", "Zed", (".rules",), "text"),
)
SYSTEM_BY_ID: dict[str, AgentSystem] = {s.id: s for s in SYSTEMS}
SYSTEM_ORDER: dict[str, int] = {s.id: i for i, s in enumerate(SYSTEMS)}

AIDER_CONFIG = ".aider.conf.yml"
# Root files and directories this module classifies: discovery's cheap prefilter.
ROOT_NAMES = frozenset({".windsurfrules", ".clinerules", "CONVENTIONS.md", ".rules",
                        ".cursorrules"})
ROOT_DIRS = frozenset({".windsurf", ".clinerules", ".junie", ".amazonq", ".kiro", ".continue"})
COPILOT_ROOT = (".github", "copilot-instructions.md")


@dataclass(frozen=True)
class SystemMatch:
    """How one file loads: its system, its kind, its scope patterns and body."""

    system: str
    kind: str
    globs: list[str] = field(default_factory=list)
    always_loaded: bool = True
    body: str = ""


def might_match(rel: PurePosixPath) -> bool:
    """Cheap prefilter: a path ``match_system_file`` could classify."""
    parts = rel.parts
    return ((len(parts) == 1 and parts[0] in ROOT_NAMES) or parts == COPILOT_ROOT
            or (len(parts) >= 2 and parts[0] in ROOT_DIRS))


def _windsurf_rule(text: str) -> SystemMatch:
    fields, body = split_frontmatter(text)
    trigger = (fields.get("trigger") or ["always_on"])[0].strip()
    globs = scope_patterns(fields.get("globs", [])) if trigger == "glob" else []
    return SystemMatch("windsurf", "windsurf_rule", globs, trigger == "always_on", body)


def _cline_rule(text: str) -> SystemMatch:
    fields, body = split_frontmatter(text)
    globs = scope_patterns(fields.get("paths", []))
    return SystemMatch("cline", "cline_rule", globs, not globs, body)


def _kiro_steering(text: str) -> SystemMatch:
    fields, body = split_frontmatter(text)
    inclusion = (fields.get("inclusion") or ["always"])[0].strip()
    globs = scope_patterns(fields.get("fileMatchPattern", [])) if inclusion == "fileMatch" else []
    return SystemMatch("kiro", "kiro_steering", globs, inclusion == "always", body)


def _continue_rule(text: str) -> SystemMatch:
    fields, body = split_frontmatter(text)
    globs = scope_patterns(fields.get("globs", []))
    always = is_true(fields["alwaysApply"]) if "alwaysApply" in fields else not globs
    return SystemMatch("continue", "continue_rule", globs, always, body)


def match_system_file(rel: PurePosixPath, text: str,
                      aider_reads: frozenset[str] = frozenset()) -> SystemMatch | None:
    """The system that loads ``rel`` (repo-relative), or None when none does.

    ``aider_reads`` holds the paths ``.aider.conf.yml`` names under ``read:``
    (``aider_read_files``); a ``CONVENTIONS.md`` it names is always loaded.
    """
    parts = rel.parts
    if len(parts) == 1:
        name = parts[0]
        if name == ".windsurfrules":
            return SystemMatch("windsurf", "windsurf_rules", body=text)
        if name == ".clinerules":
            return SystemMatch("cline", "cline_rules", body=text)
        if name == "CONVENTIONS.md":
            return SystemMatch("aider", "aider_conventions",
                               always_loaded=name in aider_reads, body=text)
        if name == ".rules":
            return SystemMatch("zed", "zed_rules", body=text)
        if name == ".cursorrules":
            return SystemMatch("cursor", "cursor_rules", body=text)
        return None
    head, suffix = parts[:2], rel.suffix
    if parts == COPILOT_ROOT:
        return SystemMatch("copilot", "copilot_instructions", body=text)
    if parts == (".junie", "guidelines.md"):
        return SystemMatch("junie", "junie_guidelines", body=text)
    if head == (".windsurf", "rules") and len(parts) == 3 and suffix == ".md":
        return _windsurf_rule(text)
    if parts[0] == ".clinerules" and len(parts) == 2 and suffix in (".md", ".txt"):
        return _cline_rule(text)
    if head == (".amazonq", "rules") and len(parts) >= 3 and suffix == ".md":
        return SystemMatch("amazon_q", "amazon_q_rule", body=text)
    if head == (".kiro", "steering") and len(parts) == 3 and suffix == ".md":
        return _kiro_steering(text)
    if head == (".continue", "rules") and len(parts) == 3 and suffix == ".md":
        return _continue_rule(text)
    return None


_READ_KEY = re.compile(r"^read\s*:\s*(.*)$")
_LIST_ITEM = re.compile(r"^\s*-\s+(.+?)\s*$")


def _unquote(value: str) -> str:
    value = value.split(" #", 1)[0].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def aider_read_files(config_text: str) -> frozenset[str]:
    """The paths a ``.aider.conf.yml`` names under its top-level ``read:`` key.

    Reads the three YAML forms Aider documents: a scalar (``read: CONVENTIONS.md``),
    an inline list (``read: [a.md, b.md]``) and a block list of ``- item`` lines.
    A leading ``./`` is dropped so a path compares with discovery's names.
    """
    out: list[str] = []
    lines = config_text.splitlines()
    for i, line in enumerate(lines):
        match = _READ_KEY.match(line)
        if not match:
            continue
        value = match.group(1).strip()
        if value.startswith("[") and value.endswith("]"):
            out.extend(_unquote(v) for v in value[1:-1].split(","))
        elif value and not value.startswith("#"):
            out.append(_unquote(value))
        else:
            for item in lines[i + 1:]:
                found = _LIST_ITEM.match(item)
                if not found:
                    break
                out.append(_unquote(found.group(1)))
    return frozenset(p.removeprefix("./") for p in out if p.strip())
