"""Committed agent configuration that executes code or exposes secrets (#513).

A repository's committed agent configuration runs on every agent session,
including headless ``claude -p`` and SDK runs in a folder the user never
trusted: settings hooks, the ``env`` block, ``apiKeyHelper``, skill hooks and
``.mcp.json`` servers all apply there (code.claude.com/docs/en/permissions).
An agent-readiness report that credits committed hooks without listing what
they run reads as an endorsement, so this scan inventories that surface and
reports the configurations that expose a secret or widen it. It is the
guardrail-erosion tendency pointed at the harness itself: a broad allow rule
or a tracked personal settings file reads as configured agent operations
while it removes the protection.

Every check is static and deterministic: no network, no model, no YAML
dependency (the core ships none). Paths are relative to the repository root.

``executes`` (inventory, informational): ``settings_hook`` (``hooks`` in
``.claude/settings.json``), ``frontmatter_hook`` (``hooks`` in skill or agent
frontmatter), ``env`` (one row per ``env`` variable in project settings, name
only), ``api_key_helper``, ``mcp_server`` (``.mcp.json``, command or URL) and
``cursor_hook`` (``.cursor/hooks.json``). A command is reported as written; a
URL loses its credentials, query and fragment; an ``env`` value or MCP
argument is never reported.

``findings``, each with ``kind``, ``file``, ``line`` (None for a whole-file or
absent-file finding), ``detail`` and a one-line ``fix``:

- ``tracked_local_settings``: ``.claude/settings.local.json`` is tracked.
- ``tracked_env_file``: a tracked ``.env`` / ``.env.*`` that is not an
  example, sample, template or dist file. The path only, never a value.
- ``missing_env_deny``: ``.gitignore`` ignores a ``.env`` pattern or an example
  file exists, but ``.claude/settings.json`` has no ``Read(...env...)`` deny.
- ``hooks_forced_on``: ``disableAllHooks: false`` in project settings, which
  overrides a user who disabled hooks.
- ``broad_allow``: ``Bash``, ``Bash(*)`` or ``Bash(:*)`` in ``permissions.allow``.
- ``credential_mount``: a devcontainer file that mounts a host credential
  directory (``~/.ssh``, ``~/.aws``, ``~/.config/gcloud`` ...).
- ``hidden_unicode``: zero-width or bidirectional-control characters in an
  instruction file, rule, skill, command or agent file; reported as code
  points, never as the characters.
- ``privileged_comment_trigger``: a workflow triggered by ``issue_comment`` or
  ``pull_request_target`` that runs a deploy, ``terraform apply`` or a publish.

Limits: workflow and devcontainer files are scanned line by line. The ``on:``
block is read up to the next top-level key, so a trigger written in flow style
across several lines or behind a YAML anchor is missed; a privileged command
built from variables, or inside a called reusable workflow or action, is
missed; a mount is matched only when its host path starts from a home
reference (``~``, ``$HOME``, ``${localEnv:...}``). Skill and agent files are
read from the default locations, not from ``plugin.json`` path overrides.
Tracking-based findings need git; outside a repository ``tracked_known`` is
false and they are not checked.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Literal, TypedDict
from urllib.parse import urlsplit, urlunsplit

from lib.assess_config import is_user_excluded
from lib.claude_config import parse_frontmatter
from lib.git_churn import tracked_files
from lib.instruction_files import INSTRUCTION_FILE_PATHS

ItemKind = Literal[
    "settings_hook", "frontmatter_hook", "env", "api_key_helper", "mcp_server", "cursor_hook",
]
FindingKind = Literal[
    "tracked_local_settings", "tracked_env_file", "missing_env_deny", "hooks_forced_on",
    "broad_allow", "credential_mount", "hidden_unicode", "privileged_comment_trigger",
]


class HarnessItem(TypedDict):
    """One committed item that executes code or connects to a service."""

    kind: ItemKind
    file: str
    line: int | None
    name: str
    detail: str | None


class HarnessFinding(TypedDict):
    """One configuration that exposes a secret or widens what agents may run."""

    kind: FindingKind
    file: str
    line: int | None
    detail: str
    fix: str


class AgentHarnessBlock(TypedDict):
    """``run-context.json`` ``agent_harness``."""

    available: bool
    tracked_known: bool
    executes: list[HarnessItem]
    executes_by_kind: dict[str, int]
    finding_count: int
    counts_by_kind: dict[str, int]
    findings: list[HarnessFinding]


SETTINGS = ".claude/settings.json"
LOCAL_SETTINGS = ".claude/settings.local.json"
_MAX_DETAIL = 160
_MAX_FILE_BYTES = 1_000_000

_HIDDEN = frozenset(
    [0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF]
    + list(range(0x202A, 0x202F)) + list(range(0x2066, 0x206A))
)
_HIDDEN_RE = re.compile("[" + "".join(chr(c) for c in sorted(_HIDDEN)) + "]")

_ENV_NAME = re.compile(r"^\.env(\..+)?$")
_ENV_EXAMPLE_PARTS = frozenset({"example", "sample", "template", "dist", "tmpl", "tpl"})
_ENV_IGNORE_LINE = re.compile(r"^/?(\*\*/)?\.env(\*|\.\*|\.[\w.*-]+)?/?$")
_ENV_DENY = re.compile(r"^Read\(.*\.env")
_BROAD_BASH = re.compile(r"^Bash(\((\*|:\*|\*\*|\*:\*)\))?$")

_HOME_REF = r"(?:\$\{localEnv:\w+\}|\$\{env:\w+\}|\$\{HOME\}|\$HOME|~)+"
_CRED_DIRS = (r"\.ssh|\.aws|\.config/gcloud|\.azure|\.kube|\.gnupg|\.docker"
              r"|\.netrc|\.npmrc|\.pypirc|\.git-credentials")
_CRED_MOUNT = re.compile(_HOME_REF + r"[/\\](?P<dir>" + _CRED_DIRS + r")(?![\w-])")

_RISKY_TRIGGERS = ("issue_comment", "pull_request_target")
_TRIGGER_RE = re.compile(r"\b(issue_comment|pull_request_target)\b")
_ON_KEY = re.compile(r"""^(?:on|"on"|'on'|true)\s*:(?P<rest>.*)$""")
_PRIVILEGED = re.compile(
    r"\b(?:terraform|tofu)\s+(?:apply|destroy)\b"
    r"|\bpulumi\s+(?:up|destroy)\b"
    r"|\bcdk\s+deploy\b"
    r"|\bkubectl\s+(?:apply|create|delete|replace|rollout|set)\b"
    r"|\bhelm\s+(?:install|upgrade|uninstall)\b"
    r"|\b(?:serverless|sls|vercel|netlify|fly|flyctl|firebase|wrangler)\s+(?:deploy|publish)\b"
    r"|\b(?:gcloud|az)\s+[\w\s-]*\bdeploy\b"
    r"|\baws\s+(?:cloudformation\s+deploy|ecs\s+update-service|lambda\s+update-function-code|s3\s+sync)\b"
    r"|\b(?:npm|yarn|pnpm|cargo)\s+publish\b|\btwine\s+upload\b|\bgem\s+push\b"
    r"|\buses:\s*\S*deploy\S*"
)


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _MAX_DETAIL else text[: _MAX_DETAIL - 3] + "..."


def _safe_url(raw: str) -> str:
    """A URL without userinfo, query or fragment, any of which can carry a token."""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return "<unparseable url>"
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def _line_of(text: str, needle: str) -> int | None:
    """The 1-based line of the first occurrence of ``needle``, else None."""
    pos = text.find(needle)
    return None if pos < 0 else text.count("\n", 0, pos) + 1


def _strip_comment(line: str) -> str:
    """A YAML line without its trailing ``#`` comment (a ``#`` inside a string is lost)."""
    return re.sub(r"(^|\s)#.*$", "", line)


class _Scan:
    """One run over the scope root; collects items and findings."""

    def __init__(self, repo: Path, root: Path, excludes: tuple[set[str], list[str]] | None):
        self.repo = repo
        self.root = root
        self.dirs, self.pats = excludes or (set(), [])
        tracked = tracked_files(root)
        self.tracked_known = tracked is not None
        self.tracked: list[str] = sorted(
            p.relative_to(root).as_posix() for p in (tracked or ()) if p.is_relative_to(root))
        self.items: list[HarnessItem] = []
        self.findings: list[HarnessFinding] = []

    # --- helpers --------------------------------------------------------------

    def rel(self, path: Path) -> str:
        return path.relative_to(self.repo).as_posix()

    def excluded(self, path: Path) -> bool:
        return is_user_excluded(path.relative_to(self.repo), self.dirs, self.pats)

    def read(self, path: Path) -> str | None:
        try:
            if not path.is_file() or path.stat().st_size > _MAX_FILE_BYTES or self.excluded(path):
                return None
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def load_json(self, rel: str) -> tuple[dict[str, Any], str] | None:
        text = self.read(self.root / rel)
        if text is None:
            return None
        try:
            data = json.loads(text)
        except ValueError:
            return None
        return (data, text) if isinstance(data, dict) else None

    def item(self, kind: ItemKind, path: Path, line: int | None, name: str,
             detail: str | None) -> None:
        self.items.append({"kind": kind, "file": self.rel(path), "line": line,
                           "name": name, "detail": _clip(detail) if detail else None})

    def finding(self, kind: FindingKind, file: str, line: int | None, detail: str,
                fix: str) -> None:
        self.findings.append({"kind": kind, "file": file, "line": line,
                              "detail": detail, "fix": fix})

    # --- inventory ------------------------------------------------------------

    def hook_groups(self, kind: ItemKind, path: Path, text: str, hooks: object) -> None:
        """``{event: [{matcher, hooks: [{type, command|url}]}]}``, or Cursor's flat
        ``{event: [{command}]}``."""
        if not isinstance(hooks, dict):
            return
        for event, groups in hooks.items():
            for group in groups if isinstance(groups, list) else []:
                if not isinstance(group, dict):
                    continue
                matcher = group.get("matcher")
                name = f"{event} [{matcher}]" if isinstance(matcher, str) and matcher else str(event)
                inner = group.get("hooks")
                for hook in inner if isinstance(inner, list) else [group]:
                    if isinstance(hook, dict):
                        detail = self.hook_detail(hook)
                        line = _line_of(text, json.dumps(detail)[1:-1]) if detail else None
                        self.item(kind, path, line or _line_of(text, f'"{event}"'), name, detail)

    @staticmethod
    def hook_detail(hook: dict[str, Any]) -> str | None:
        if isinstance(hook.get("command"), str):
            return str(hook["command"])
        if isinstance(hook.get("url"), str):
            return _safe_url(hook["url"])
        kind = hook.get("type")
        return f"<{kind} hook>" if isinstance(kind, str) else None

    def settings(self) -> dict[str, Any]:
        loaded = self.load_json(SETTINGS)
        if loaded is None:
            return {}
        data, text = loaded
        path = self.root / SETTINGS
        self.hook_groups("settings_hook", path, text, data.get("hooks"))
        env = data.get("env")
        if isinstance(env, dict):
            for var in env:
                self.item("env", path, _line_of(text, f'"{var}"'), str(var), None)
        helper = data.get("apiKeyHelper")
        if isinstance(helper, str):
            self.item("api_key_helper", path, _line_of(text, '"apiKeyHelper"'), "apiKeyHelper", helper)
        return data

    def mcp(self) -> None:
        loaded = self.load_json(".mcp.json")
        if loaded is None:
            return
        data, text = loaded
        servers = data.get("mcpServers")
        for name, spec in servers.items() if isinstance(servers, dict) else []:
            if not isinstance(spec, dict):
                continue
            if isinstance(spec.get("url"), str):
                detail: str | None = _safe_url(spec["url"])
            else:
                detail = spec["command"] if isinstance(spec.get("command"), str) else None
            self.item("mcp_server", self.root / ".mcp.json", _line_of(text, f'"{name}"'),
                      str(name), detail)

    def cursor_hooks(self) -> None:
        loaded = self.load_json(".cursor/hooks.json")
        if loaded is not None:
            data, text = loaded
            self.hook_groups("cursor_hook", self.root / ".cursor/hooks.json", text, data.get("hooks"))

    def agent_files(self) -> list[Path]:
        """Skill, command and agent files Claude Code loads from the default locations."""
        claude = self.root / ".claude"
        found = list(claude.glob("skills/*/SKILL.md")) + list(claude.glob("commands/**/*.md"))
        found += list(claude.glob("agents/**/*.md"))
        if (self.root / ".claude-plugin" / "plugin.json").is_file():
            found += list(self.root.glob("skills/*/SKILL.md")) + list(self.root.glob("agents/**/*.md"))
            found += list(self.root.glob("commands/*.md"))
        seen: dict[Path, Path] = {}
        for p in sorted(found):
            if p.is_file():
                seen.setdefault(p.resolve(), p)
        return sorted(seen.values())

    def frontmatter_hooks(self, path: Path, text: str) -> None:
        fm = parse_frontmatter(text)
        entry = next((e for e in fm.entries if e.key == "hooks"), None) if fm else None
        if fm is None or entry is None:
            return
        lines = text.splitlines()
        after = [e.line for e in fm.entries if e.line > entry.line]
        end = min(after) if after else next(
            (i + 1 for i in range(entry.line, len(lines)) if lines[i].rstrip() in ("---", "...")),
            len(lines) + 1)
        events = {c.line: c.key for c in entry.children}
        current: tuple[int, str] | None = None
        reported: set[int] = set()
        for lineno in range(entry.line + 1, end):
            if lineno in events:
                current = (lineno, events[lineno])
                continue
            m = re.match(r"^\s*(?:-\s*)?(command|url)\s*:\s*(.+?)\s*$", lines[lineno - 1])
            if m and current:
                raw = m.group(2).strip("'\"")
                self.item("frontmatter_hook", path, lineno, current[1],
                          _safe_url(raw) if m.group(1) == "url" else raw)
                reported.add(current[0])
        for lineno, event in events.items():
            if lineno not in reported:
                self.item("frontmatter_hook", path, lineno, event, None)

    # --- findings -------------------------------------------------------------

    def tracked_secrets(self) -> None:
        if LOCAL_SETTINGS in self.tracked:
            self.finding(
                "tracked_local_settings", self.rel(self.root / LOCAL_SETTINGS), None,
                "personal settings file is committed",
                "`git rm --cached .claude/settings.local.json` and gitignore it; "
                "commit shared settings in `.claude/settings.json`.")
        for rel in self.tracked:
            name = rel.rsplit("/", 1)[-1]
            m = _ENV_NAME.match(name)
            if not m or self.excluded(self.root / rel):
                continue
            parts = set((m.group(1) or "").lower().split("."))
            if parts & _ENV_EXAMPLE_PARTS:
                continue
            self.finding(
                "tracked_env_file", self.rel(self.root / rel), None,
                "environment file is committed (values not read)",
                "Remove it from git (`git rm --cached`), rotate any secret it held, and "
                "commit a `.env.example` with placeholder values instead.")

    def env_deny(self, settings: dict[str, Any]) -> None:
        ignore = self.read(self.root / ".gitignore") or ""
        ignored = any(_ENV_IGNORE_LINE.match(ln.strip()) for ln in ignore.splitlines())
        example = any(
            (m := _ENV_NAME.match(p.name)) and set((m.group(1) or "").lower().split("."))
            & _ENV_EXAMPLE_PARTS for p in self.root.glob(".env.*"))
        if not (ignored or example):
            return
        perms = settings.get("permissions")
        deny = perms.get("deny") if isinstance(perms, dict) else None
        rules = [r for r in deny if isinstance(r, str)] if isinstance(deny, list) else []
        if any(_ENV_DENY.match(r.replace(" ", "")) for r in rules):
            return
        why = "`.gitignore` ignores `.env`" if ignored else "a `.env` example file exists"
        self.finding(
            "missing_env_deny", self.rel(self.root / SETTINGS), None,
            f"{why} but no `Read(./.env)` deny rule stops an agent reading it",
            'Add `"Read(./.env)"` and `"Read(./.env.*)"` to `permissions.deny` in '
            "`.claude/settings.json`.")

    def settings_findings(self, settings: dict[str, Any]) -> None:
        if not settings:
            return
        text = self.read(self.root / SETTINGS) or ""
        file = self.rel(self.root / SETTINGS)
        if settings.get("disableAllHooks") is False:
            self.finding(
                "hooks_forced_on", file, _line_of(text, '"disableAllHooks"'),
                "`disableAllHooks: false` overrides a user who disabled hooks",
                "Remove `disableAllHooks` from project settings; leave the choice to each user.")
        perms = settings.get("permissions")
        allow = perms.get("allow") if isinstance(perms, dict) else None
        for rule in allow if isinstance(allow, list) else []:
            if isinstance(rule, str) and _BROAD_BASH.match(rule.replace(" ", "")):
                self.finding(
                    "broad_allow", file, _line_of(text, json.dumps(rule)),
                    f"`{rule}` lets agents run any shell command without asking",
                    "Allow the specific commands agents need, e.g. `Bash(npm run test *)`.")

    def credential_mounts(self) -> None:
        files = [self.root / ".devcontainer.json"]
        files += sorted(p for p in (self.root / ".devcontainer").rglob("*") if p.is_file())
        for path in files:
            text = self.read(path)
            for lineno, line in enumerate((text or "").splitlines(), 1):
                if line.lstrip().startswith(("//", "#")):
                    continue
                m = _CRED_MOUNT.search(line)
                if m:
                    self.finding(
                        "credential_mount", self.rel(path), lineno,
                        f"mounts host `~/{m.group('dir')}` into the container",
                        "Remove the mount; a devcontainer does not stop a project "
                        "exfiltrating what is mounted into it. Use scoped tokens instead.")

    def hidden_unicode(self, agent_files: list[Path]) -> None:
        files = [self.root / p for p in INSTRUCTION_FILE_PATHS]
        for sub in (".claude/rules", ".cursor/rules"):
            files += sorted(p for p in (self.root / sub).rglob("*") if p.is_file())
        seen: set[Path] = set()
        for path in files + agent_files:
            text = self.read(path)
            if text is None or path.resolve() in seen:
                continue
            seen.add(path.resolve())
            hits = [(m.start(), m.group()) for m in _HIDDEN_RE.finditer(text)
                    if not (m.start() == 0 and m.group() == "﻿")]
            if not hits:
                continue
            points = sorted({ch for _, ch in hits})
            names = ", ".join(f"U+{ord(c):04X} {unicodedata.name(c, '')}".strip() for c in points)
            self.finding(
                "hidden_unicode", self.rel(path), text.count("\n", 0, hits[0][0]) + 1,
                f"{len(hits)} hidden character(s): {names}",
                "Delete the invisible characters; an agent reads them while a reviewer "
                "cannot see them.")

    def workflows(self) -> None:
        wf_dir = self.root / ".github" / "workflows"
        files = sorted(p for p in wf_dir.glob("*") if p.suffix in (".yml", ".yaml"))
        for path in files:
            text = self.read(path)
            if text is None:
                continue
            lines = [_strip_comment(ln) for ln in text.splitlines()]
            triggers = sorted(set(_TRIGGER_RE.findall(_on_block(lines))))
            if not triggers:
                continue
            for lineno, line in enumerate(lines, 1):
                m = _PRIVILEGED.search(line)
                if m:
                    self.finding(
                        "privileged_comment_trigger", self.rel(path), lineno,
                        f"`{'`/`'.join(triggers)}` workflow runs `{_clip(m.group())}`",
                        "Move the privileged step to a workflow a maintainer triggers "
                        "(`workflow_dispatch`, push to the default branch) or gate it "
                        "behind a protected environment.")
                    break


def _on_block(lines: list[str]) -> str:
    """The text of a workflow's top-level ``on:`` value, inline or as a block."""
    for i, line in enumerate(lines):
        m = _ON_KEY.match(line)
        if not m:
            continue
        body = [m.group("rest")]
        for nxt in lines[i + 1:]:
            if nxt.strip() and not nxt[:1].isspace():
                break
            body.append(nxt)
        return "\n".join(body)
    return ""


def _counts(rows: list[HarnessItem] | list[HarnessFinding]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[r["kind"]] = out.get(r["kind"], 0) + 1
    return dict(sorted(out.items()))


def scan_agent_harness(
    repo_root: Path,
    scope: Path | None = None,
    excludes: tuple[set[str], list[str]] | None = None,
) -> AgentHarnessBlock:
    """Inventory and check the committed agent harness under ``scope`` (default the repo root)."""
    repo = repo_root.resolve()
    root = (repo / scope).resolve() if scope else repo
    scan = _Scan(repo, root, excludes)
    settings = scan.settings()
    scan.mcp()
    scan.cursor_hooks()
    agent_files = scan.agent_files()
    for path in agent_files:
        text = scan.read(path)
        if text is not None:
            scan.frontmatter_hooks(path, text)
    if scan.tracked_known:
        scan.tracked_secrets()
    scan.env_deny(settings)
    scan.settings_findings(settings)
    scan.credential_mounts()
    scan.hidden_unicode(agent_files)
    scan.workflows()
    items = sorted(scan.items, key=lambda r: (r["file"], r["line"] or 0, r["kind"], r["name"]))
    findings = sorted(scan.findings, key=lambda f: (f["file"], f["line"] or 0, f["kind"]))
    return {
        "available": True, "tracked_known": scan.tracked_known,
        "executes": items, "executes_by_kind": _counts(items),
        "finding_count": len(findings), "counts_by_kind": _counts(findings),
        "findings": findings,
    }
