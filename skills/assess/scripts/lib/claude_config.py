"""Claude Code configuration scan: legacy commands and ignored frontmatter.

Claude Code ignores a frontmatter field it does not recognise without reporting
an error, so a misspelt ``allowed_tools``, a ``name`` in a command file or an
unsupported agent ``color`` reads as configured while doing nothing. This is the
Layer 0 instance of the lying-map defect: a self-description of the agent setup
under no pressure to stay true. The scan reads every skill, command and agent
file Claude Code would load from the repository and reports each field the
runtime would drop, checked against the documented sets pinned in
``lib.claude_config_fields`` (never fetched at run time).

Files read, relative to the run root (the ``--scope`` directory, else the repo
root):

- ``.claude/skills/*/SKILL.md``, ``.claude/commands/**/*.md`` and
  ``.claude/agents/**/*.md`` (project configuration);
- for a plugin repository (``.claude-plugin/plugin.json`` present):
  ``skills/*/SKILL.md`` plus the manifest's ``skills`` directories (which add to
  the default), and ``commands/`` and ``agents/`` or, when the manifest sets
  ``commands`` / ``agents``, the paths it names instead (those keys replace the
  default scan, as Claude Code does).

Finding kinds, each carrying ``file``, ``line``, ``key``, ``value`` and a
one-line ``fix``:

- ``legacy_command``: a command file; ``commands/`` is the older form of skills.
- ``unknown_key``: a key outside the documented set for that file kind, with a
  rename hint when it normalises to a known key (``allowed_tools``).
- ``unsupported_value``: a closed-set field (``color``, ``effort``,
  ``context`` ...) or a ``model`` that matches no alias or model ID.
- ``command_unsupported_key``: ``name`` or ``paths`` in a command file.
- ``plugin_agent_ignored``: a field plugin agents ignore (``permissionMode``,
  ``hooks``, ``mcpServers``, ``initialPrompt``).
- ``malformed_frontmatter``: frontmatter the minimal parser cannot read; the
  scan reports it and moves on, it never raises.
- ``unreadable_file``: a configuration file the scan could not read.

No ``.claude/`` directory and no plugin manifest gives ``available: false``
with a reason: there is nothing to check, which is not a clean pass.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TypedDict

from lib.assess_config import is_user_excluded
from lib.claude_config_fields import (
    AGENT_EXPERIMENTAL_FIELDS,
    AGENT_FIELDS,
    COMMAND_FIELDS,
    COMMAND_UNSUPPORTED_FIELDS,
    ENUM_FIELDS,
    MODEL_PATTERN,
    PLUGIN_AGENT_IGNORED_FIELDS,
    SKILL_FIELDS,
    SNAPSHOT_DATE,
    SOURCE_URLS,
)

FileKind = Literal["skill", "command", "agent"]
Origin = Literal["project", "plugin"]


class ClaudeConfigFinding(TypedDict):
    """One field (or file) Claude Code would drop or treat as legacy."""

    kind: str
    file: str
    line: int
    key: str | None
    value: str | None
    fix: str


class ClaudeConfigBlock(TypedDict, total=False):
    """``run-context.json`` ``claude_config``."""

    available: bool
    reason: str | None
    snapshot_date: str
    source_urls: list[str]
    plugin_repo: bool
    manifest_error: str | None
    files_scanned: int
    files_by_kind: dict[str, int]
    finding_count: int
    counts_by_kind: dict[str, int]
    findings: list[ClaudeConfigFinding]


# --- frontmatter parsing ------------------------------------------------------

# A mapping line: a key that starts with no space, quote, dash or colon, then a
# colon followed by whitespace or the end of the line.
_KEY_RE = re.compile(r"^(?P<key>[^\s:#'\"\-][^:]*?)\s*:(?:\s+(?P<val>.*?))?\s*$")
_FENCE = "---"


@dataclass
class FmEntry:
    """A frontmatter key with its 1-based file line and scalar value.

    ``value`` is None for a key whose value is a nested map or list (or empty);
    ``children`` holds the first-level keys of a nested map.
    """

    key: str
    line: int
    value: str | None
    children: list[FmEntry] = field(default_factory=list)


@dataclass
class Frontmatter:
    """The parsed top-level entries and the malformed lines, as (line, message)."""

    entries: list[FmEntry] = field(default_factory=list)
    errors: list[tuple[int, str]] = field(default_factory=list)


def _clean_scalar(raw: str | None) -> str | None:
    """Unquote a scalar, drop a trailing comment; None for an empty value."""
    if raw is None or raw == "":
        return None
    if len(raw) >= 2 and raw[0] in "\"'" and raw[-1] == raw[0]:
        return raw[1:-1]
    return re.sub(r"\s+#.*$", "", raw)


class _Parser:
    """A line parser for the YAML subset frontmatter uses.

    Top-level ``key: value`` lines, block scalars (``|`` / ``>``), plain
    continuation lines, block lists, and one level of nested map keys. It needs
    no YAML dependency (the deterministic core ships none) and records what it
    cannot read instead of raising.
    """

    def __init__(self) -> None:
        self.fm = Frontmatter()
        self.parent: FmEntry | None = None
        self.child_indent: int | None = None
        self.seen: set[str] = set()

    def feed(self, line: str, lineno: int) -> None:
        """Consume one frontmatter line (``lineno`` is its 1-based file line)."""
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            return
        lead = line[: len(line) - len(line.lstrip())]
        if "\t" in lead:
            self.fm.errors.append((lineno, "tab in indentation (YAML allows spaces only)"))
            return
        if lead:
            self._indented(line, len(lead), lineno)
        else:
            self._top_level(line, lineno)

    def _top_level(self, line: str, lineno: int) -> None:
        if line.startswith("-"):
            if self.parent is None or self.parent.value is not None:
                self.fm.errors.append((lineno, "list item outside any key"))
            return
        m = _KEY_RE.match(line)
        if not m:
            self.fm.errors.append((lineno, f"not a `key: value` line: {line.strip()[:60]}"))
            self.parent = None
            return
        key = m.group("key").strip()
        if key in self.seen:
            self.fm.errors.append((lineno, f"duplicate key `{key}`"))
        self.seen.add(key)
        raw = m.group("val")
        value = "<block scalar>" if raw and raw[0] in "|>" else _clean_scalar(raw)
        self.parent = FmEntry(key, lineno, value)
        self.child_indent = None
        self.fm.entries.append(self.parent)

    def _indented(self, line: str, indent: int, lineno: int) -> None:
        if self.parent is None:
            self.fm.errors.append((lineno, "indented line before any key"))
            return
        if self.parent.value is not None:
            return  # block scalar or plain-scalar continuation
        body = line.strip()
        if body.startswith("-"):
            return  # a block list item
        if self.child_indent is None:
            self.child_indent = indent
        if indent != self.child_indent:
            return  # deeper nesting is free-form here
        m = _KEY_RE.match(body)
        if m:
            child = FmEntry(m.group("key").strip(), lineno, _clean_scalar(m.group("val")))
            self.parent.children.append(child)


def parse_frontmatter(text: str) -> Frontmatter | None:
    """Parse a leading ``---`` block; None when the file has no frontmatter."""
    lines = text.splitlines()
    if not lines or lines[0].lstrip("﻿").rstrip() != _FENCE:
        return None
    end = next(
        (i for i in range(1, len(lines)) if lines[i].rstrip() in (_FENCE, "...")),
        None,
    )
    if end is None:
        fm = Frontmatter()
        fm.errors.append((1, "frontmatter opens with `---` but never closes"))
        return fm
    parser = _Parser()
    for i in range(1, end):
        parser.feed(lines[i], i + 1)
    return parser.fm


# --- per-file checks ----------------------------------------------------------

_ALLOWED: dict[str, frozenset[str]] = {
    "skill": SKILL_FIELDS, "command": COMMAND_FIELDS, "agent": AGENT_FIELDS,
}


def _norm(key: str) -> str:
    return re.sub(r"[-_.\s]", "", key).lower()


def _unknown_key_fix(key: str, kind: str) -> str:
    """A rename hint for a likely misspelling, else a removal note."""
    allowed = SKILL_FIELDS if kind == "command" else _ALLOWED[kind]
    for known in sorted(allowed):
        if _norm(known) == _norm(key):
            return f"Rename `{key}` to `{known}`; Claude Code ignores the misspelt key."
    other = "agent" if kind != "agent" else "skill"
    if key in _ALLOWED[other]:
        article = "an" if other == "agent" else "a"
        return f"`{key}` is {article} {other} field; {kind} files ignore it - remove it."
    return f"Remove `{key}` or fix its spelling; Claude Code ignores unknown fields."


def _finding(kind: str, rel: str, line: int, key: str | None,
             value: str | None, fix: str) -> ClaudeConfigFinding:
    return {"kind": kind, "file": rel, "line": line, "key": key,
            "value": value, "fix": fix}


def _value_finding(path: str, value: str | None, kind: str) -> str | None:
    """The fix for an unsupported value of ``path``, or None when it is fine."""
    if value is None or value.startswith(("[", "{", "<", "$")):
        return None
    if path == "model":
        if MODEL_PATTERN.match(value):
            return None
        return ("Use a model alias (sonnet, opus, haiku, fable), `inherit`, "
                "or a full model ID such as `claude-opus-5-5`.")
    spec = ENUM_FIELDS.get(path)
    if spec is None or kind not in spec[0] or value in spec[1]:
        return None
    for allowed in spec[1]:
        if allowed.lower() == value.lower():
            return f"Write `{allowed}`; the value is case-sensitive."
    return f"Use one of: {', '.join(spec[1])}."


def _check_entry(entry: FmEntry, rel: str, kind: str, origin: str) -> list[ClaudeConfigFinding]:
    key = entry.key
    if kind == "command" and key in COMMAND_UNSUPPORTED_FIELDS:
        fix = ("Remove `name`; a command's name comes from its file path."
               if key == "name" else
               "Move the command to a skill to use `paths`; command files ignore it.")
        return [_finding("command_unsupported_key", rel, entry.line, key, entry.value, fix)]
    if kind == "agent" and origin == "plugin" and key in PLUGIN_AGENT_IGNORED_FIELDS:
        fix = (f"Remove `{key}`; plugin agents ignore it (it works only in "
               "a project agent under `.claude/agents/`).")
        return [_finding("plugin_agent_ignored", rel, entry.line, key, entry.value, fix)]
    if key not in _ALLOWED[kind]:
        return [_finding("unknown_key", rel, entry.line, key, entry.value,
                         _unknown_key_fix(key, kind))]
    out: list[ClaudeConfigFinding] = []
    value_fix = _value_finding(key, entry.value, kind)
    if value_fix:
        out.append(_finding("unsupported_value", rel, entry.line, key, entry.value, value_fix))
    if kind == "agent" and key == "experimental":
        out.extend(_check_experimental(entry, rel))
    return out


def _check_experimental(entry: FmEntry, rel: str) -> list[ClaudeConfigFinding]:
    out: list[ClaudeConfigFinding] = []
    for child in entry.children:
        path = f"experimental.{child.key}"
        if child.key not in AGENT_EXPERIMENTAL_FIELDS:
            out.append(_finding(
                "unknown_key", rel, child.line, path, child.value,
                f"Remove `{path}`; the documented experimental key is `cacheTtl`."))
            continue
        fix = _value_finding(path, child.value, "agent")
        if fix:
            out.append(_finding("unsupported_value", rel, child.line, path, child.value, fix))
    return out


def _skill_target(rel: str, origin: str) -> str:
    """Where a legacy command file moves to as a skill."""
    parts = Path(rel).with_suffix("").parts
    # Below a `commands/` directory the subfolders are part of the command name
    # (`frontend/component.md` is `/frontend:component`); a manifest-named file
    # elsewhere is named by its stem.
    sub = parts[parts.index("commands") + 1:] if "commands" in parts[:-1] else parts[-1:]
    name = "-".join(sub)
    base = ".claude/skills" if origin == "project" else "skills"
    return f"{base}/{name}/SKILL.md"


def check_file(rel: str, kind: FileKind, origin: Origin, text: str) -> list[ClaudeConfigFinding]:
    """Every finding for one configuration file, in line order."""
    out: list[ClaudeConfigFinding] = []
    if kind == "command":
        out.append(_finding(
            "legacy_command", rel, 1, None, None,
            f"Move to `{_skill_target(rel, origin)}`; `commands/` is the older form of skills."))
    fm = parse_frontmatter(text)
    if fm is None:
        return out
    for line, msg in fm.errors:
        out.append(_finding("malformed_frontmatter", rel, line, None, None,
                            f"Fix the frontmatter: {msg}."))
    for entry in fm.entries:
        out.extend(_check_entry(entry, rel, kind, origin))
    return sorted(out, key=lambda f: (f["line"], f["kind"]))


# --- discovery ----------------------------------------------------------------

def _paths_value(raw: object) -> list[str]:
    """A manifest path field as a list of strings (a string, or a list of them)."""
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [p for p in raw if isinstance(p, str)]
    return []


def _inside(root: Path, rel: str) -> Path | None:
    """``root / rel`` resolved, or None when it escapes ``root``."""
    path = (root / rel).resolve()
    return path if path.is_relative_to(root) else None


def _md_files(path: Path | None) -> list[Path]:
    if path is None:
        return []
    if path.is_file():
        return [path] if path.suffix == ".md" else []
    return sorted(p for p in path.rglob("*.md") if p.is_file()) if path.is_dir() else []


def _skill_files(path: Path | None) -> list[Path]:
    """A skills directory: one folder holding SKILL.md, or a folder of them."""
    if path is None or not path.is_dir():
        return []
    if (path / "SKILL.md").is_file():
        return [path / "SKILL.md"]
    return sorted(p for p in path.glob("*/SKILL.md") if p.is_file())


def _command_sources(raw: object) -> list[str]:
    """Paths named by a manifest ``commands`` value (path, list or object map)."""
    if isinstance(raw, dict):
        return [v["source"] for v in raw.values()
                if isinstance(v, dict) and isinstance(v.get("source"), str)]
    return _paths_value(raw)


def _load_manifest(path: Path) -> tuple[dict[str, object], str | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {}, f"could not parse {path.name}: {e}"
    if not isinstance(data, dict):
        return {}, f"{path.name} is not a JSON object"
    return data, None


Discovered = list[tuple[Path, FileKind, Origin]]


def _plugin_files(root: Path, manifest: dict[str, object]) -> Discovered:
    found: Discovered = []
    skill_dirs = [root / "skills"] + [
        p for p in (_inside(root, s) for s in _paths_value(manifest.get("skills"))) if p]
    for d in skill_dirs:
        found.extend((p, "skill", "plugin") for p in _skill_files(d))
    cmd_paths = (_command_sources(manifest["commands"]) if "commands" in manifest
                 else ["commands"])
    for c in cmd_paths:
        found.extend((p, "command", "plugin") for p in _md_files(_inside(root, c)))
    agent_paths = (_paths_value(manifest["agents"]) if "agents" in manifest
                   else ["agents"])
    for a in agent_paths:
        found.extend((p, "agent", "plugin") for p in _md_files(_inside(root, a)))
    return found


def _project_files(root: Path) -> Discovered:
    claude = root / ".claude"
    found: Discovered = [(p, "skill", "project")
                         for p in sorted((claude / "skills").glob("*/SKILL.md")) if p.is_file()]
    found.extend((p, "command", "project") for p in _md_files(claude / "commands"))
    found.extend((p, "agent", "project") for p in _md_files(claude / "agents"))
    return found


def _dedupe(found: Discovered) -> Discovered:
    seen: set[Path] = set()
    out: Discovered = []
    for item in found:
        if item[0] not in seen:
            seen.add(item[0])
            out.append(item)
    return out


# --- the scan -----------------------------------------------------------------

def _unavailable(reason: str) -> ClaudeConfigBlock:
    return {"available": False, "reason": reason, "snapshot_date": SNAPSHOT_DATE}


def scan_claude_config(
    repo_root: Path,
    scope: Path | None = None,
    excludes: tuple[set[str], list[str]] | None = None,
) -> ClaudeConfigBlock:
    """Scan the Claude Code configuration under ``scope`` (default the repo root).

    ``excludes`` is the ``(dirs, patterns)`` pair from ``.assess/config.toml``,
    matched against repo-relative paths as every other scan does. Paths in the
    findings are repo-relative.
    """
    repo = repo_root.resolve()
    root = (scope or repo).resolve()
    manifest_path = root / ".claude-plugin" / "plugin.json"
    plugin_repo = manifest_path.is_file()
    if not plugin_repo and not (root / ".claude").is_dir():
        return _unavailable(
            "no Claude Code configuration: no .claude/ directory and no "
            ".claude-plugin/plugin.json")
    manifest, manifest_error = _load_manifest(manifest_path) if plugin_repo else ({}, None)
    found = _project_files(root)
    if plugin_repo:
        found.extend(_plugin_files(root, manifest))
    dirs, pats = excludes or (set(), [])
    findings: list[ClaudeConfigFinding] = []
    by_kind = {"skill": 0, "command": 0, "agent": 0}
    for path, kind, origin in _dedupe(found):
        rel = path.relative_to(repo)
        if is_user_excluded(rel, dirs, pats):
            continue
        by_kind[kind] += 1
        findings.extend(_scan_one(path, rel.as_posix(), kind, origin))
    findings.sort(key=lambda f: (f["file"], f["line"], f["kind"]))
    counts: dict[str, int] = {}
    for f in findings:
        counts[f["kind"]] = counts.get(f["kind"], 0) + 1
    return {
        "available": True, "reason": None, "snapshot_date": SNAPSHOT_DATE,
        "source_urls": list(SOURCE_URLS), "plugin_repo": plugin_repo,
        "manifest_error": manifest_error, "files_scanned": sum(by_kind.values()),
        "files_by_kind": by_kind, "finding_count": len(findings),
        "counts_by_kind": dict(sorted(counts.items())), "findings": findings,
    }


def _scan_one(path: Path, rel: str, kind: FileKind, origin: Origin) -> list[ClaudeConfigFinding]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return [_finding("unreadable_file", rel, 1, None, None,
                         f"Make the file readable: {e.strerror or e}.")]
    return check_file(rel, kind, origin, text)
