"""Scan what a repository builds with agents: an inventory, not a score (issue #689).

The other scans measure whether a repository is ready for agent contributors.
This one records whether it *builds* agents, so a fleet roll-up can show where
agent work is concentrated and which teams are building the same agent twice.
Per repository it records four kinds of evidence:

- (a) ``sdks``: agent frameworks, model SDKs and the MCP SDK declared in
  dependency manifests (``lib/agent_manifests.py``), as slugs from the pinned
  vocabulary in ``lib/agent_building_vocab.py``.
- (b) ``config``: agent configuration committed to the repository -
  ``instruction_file`` (a known agent hint file, ``INSTRUCTION_FILE_PATHS``:
  ``CLAUDE.md``, ``AGENTS.md``, ``.cursorrules`` ...), ``mcp_json``
  (``.mcp.json``, ``.cursor/mcp.json``, ``.vscode/mcp.json``),
  ``claude_agents`` (a file under ``.claude/agents/``) and ``ci_agent_job`` (a
  CI file that runs an agent; which agents in ``ci_agents``).
- (c) ``tool_loop_files`` and ``mcp_server_files``: how many source files match
  the tool-call loop and MCP server patterns. Counts only; no source text is
  kept.
- (d) ``issue_trackers`` and ``git_hosts``: issue-tracker and Git-host API
  clients, from manifests or from REST endpoints in source. ``pairing`` is true
  when the repository has at least one of each: the marker of a
  ticket-to-merge-request agent.

Class rule (``classify``), first match wins:

1. ``builds-agent``: an agent framework is declared, or a model SDK is declared
   and either a source file matches a tool-call loop or ``pairing`` is true.
2. ``builds-mcp-server``: a source file matches an MCP server pattern.
3. ``uses-agent-tooling``: any ``config`` kind is present.
4. ``none``: otherwise. A model SDK with no tool-call loop and no pairing
   (a summariser, an embedding job) is not agent construction.

So a repository with only agent configuration is ``uses-agent-tooling``, never
``builds-agent``.

Only git-tracked files count, and symlinks are skipped, as in
``shared_tooling``. Manifests and source files under the built-in excluded
trees (``node_modules``, ``vendor``, ``tests/fixtures`` ...) are skipped, and
the code heuristic skips test files (a ``test``/``tests``/``__tests__``/``spec``
directory, ``test_*``, ``*_test.*``, ``*.test.*``, ``*.spec.*``), so a test
that mocks a tool call is not read as an agent. At most ``MAX_SOURCE_FILES``
source files of at most ``MAX_FILE_BYTES`` are read; ``source_files_capped``
says when the cap was reached. Deterministic and offline.
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Literal, NotRequired, TypedDict

from lib.agent_building_vocab import (
    CI_AGENT_PATTERNS,
    ENDPOINT_PATTERNS,
    MCP_SERVER_PATTERNS,
    TOOL_LOOP_PATTERNS,
    lookup,
)
from lib.agent_manifests import reader_for
from lib.doc_graph import is_excluded_path
from lib.git_churn import tracked_files
from lib.instruction_files import INSTRUCTION_FILE_PATHS

AgentClass = Literal["builds-agent", "builds-mcp-server", "uses-agent-tooling", "none"]
CLASSES: tuple[AgentClass, ...] = ("builds-agent", "builds-mcp-server", "uses-agent-tooling", "none")

MAX_SOURCE_FILES = 5000
MAX_FILE_BYTES = 512_000
SOURCE_SUFFIXES = frozenset({
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".go", ".java", ".kt", ".rb", ".cs", ".rs",
})
_TEST_DIRS = frozenset({"test", "tests", "__tests__", "spec", "testdata"})
_MCP_JSON = frozenset({".mcp.json", ".cursor/mcp.json", ".vscode/mcp.json"})
_CLAUDE_AGENTS = ".claude/agents/"
_GITLAB_CI = ".gitlab-ci.yml"
_WORKFLOWS = ".github/workflows/"


class AgentBuildingBlock(TypedDict):
    """``run-context.json`` ``agent_building``."""

    available: bool
    agent_class: AgentClass | None  # None when the block is unavailable
    sdks: list[str]  # vocabulary slugs: frameworks, model SDKs, mcp
    agent_framework: bool  # an agent framework is among sdks
    llm_sdk: bool  # a model SDK is among sdks
    manifests: int  # manifest files read
    config: list[str]  # instruction_file | mcp_json | claude_agents | ci_agent_job
    ci_agents: list[str]  # vocabulary slugs of the agents CI runs
    tool_loop_files: int
    mcp_server_files: int
    source_files_read: int
    source_files_capped: bool
    issue_trackers: list[str]
    git_hosts: list[str]
    pairing: bool  # an issue-tracker and a Git-host client together
    reason: NotRequired[str]


def classify(*, agent_framework: bool, llm_sdk: bool, tool_loop_files: int, pairing: bool,
             mcp_server_files: int, config: Iterable[str]) -> AgentClass:
    """The class rule in the module docstring, as a pure function of the evidence."""
    if agent_framework or (llm_sdk and (tool_loop_files > 0 or pairing)):
        return "builds-agent"
    if mcp_server_files > 0:
        return "builds-mcp-server"
    if any(True for _ in config):
        return "uses-agent-tooling"
    return "none"


def is_test_path(rel: PurePosixPath) -> bool:
    """A test file by directory or file-name convention."""
    if any(part.lower() in _TEST_DIRS for part in rel.parts[:-1]):
        return True
    name = rel.name.lower()
    stem = name.split(".", 1)[0]
    return (stem.startswith("test_") or stem.endswith("_test")
            or ".test." in name or ".spec." in name)


def _read(path: Path) -> str | None:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


class _Evidence:
    def __init__(self) -> None:
        self.sdks: set[str] = set()
        self.framework = False
        self.llm = False
        self.trackers: set[str] = set()
        self.hosts: set[str] = set()
        self.manifests = 0
        self.config: set[str] = set()
        self.ci_agents: set[str] = set()
        self.tool_loop = 0
        self.mcp_server = 0
        self.source_read = 0
        self.capped = False

    def add_package(self, name: str) -> None:
        found = lookup(name)
        if found is None:
            return
        slug, kind = found
        if kind == "tracker":
            self.trackers.add(slug)
        elif kind == "git_host":
            self.hosts.add(slug)
        else:
            self.sdks.add(slug)
            self.framework |= kind == "framework"
            self.llm |= kind == "llm"

    def add_source(self, text: str) -> None:
        self.source_read += 1
        self.tool_loop += any(p.search(text) for p in TOOL_LOOP_PATTERNS)
        self.mcp_server += any(p.search(text) for p in MCP_SERVER_PATTERNS)
        for slug, kind, pattern in ENDPOINT_PATTERNS:
            if pattern.search(text):
                (self.trackers if kind == "tracker" else self.hosts).add(slug)

    def add_ci(self, text: str) -> None:
        agents = {slug for slug, pattern in CI_AGENT_PATTERNS if pattern.search(text)}
        if agents:
            self.config.add("ci_agent_job")
            self.ci_agents |= agents


def _config_kind(rel: str) -> str | None:
    if rel in INSTRUCTION_FILE_PATHS:
        return "instruction_file"
    if rel in _MCP_JSON:
        return "mcp_json"
    if rel.startswith(_CLAUDE_AGENTS):
        return "claude_agents"
    return None


def _is_ci_file(rel: str) -> bool:
    return rel == _GITLAB_CI or (rel.startswith(_WORKFLOWS) and rel.endswith((".yml", ".yaml"))
                                 and rel.count("/") == 2)


def _collect(repo_root: Path, tracked: Iterable[Path]) -> _Evidence:
    evidence = _Evidence()
    rels: list[PurePosixPath] = []
    for path in tracked:
        try:
            rels.append(PurePosixPath(path.relative_to(repo_root).as_posix()))
        except ValueError:
            continue
    for rel in sorted(rels):
        text_rel = rel.as_posix()
        kind = _config_kind(text_rel)
        if kind is not None and not (repo_root / rel).is_symlink():
            evidence.config.add(kind)
        if _is_ci_file(text_rel) and (text := _read(repo_root / rel)) is not None:
            evidence.add_ci(text)
        if is_excluded_path(Path(rel)):
            continue
        reader = reader_for(rel.name)
        if reader is not None and (text := _read(repo_root / rel)) is not None:
            evidence.manifests += 1
            for name in reader(text):
                evidence.add_package(name)
        if rel.suffix not in SOURCE_SUFFIXES or is_test_path(rel):
            continue
        if evidence.source_read >= MAX_SOURCE_FILES:
            evidence.capped = True
            continue
        if (text := _read(repo_root / rel)) is not None:
            evidence.add_source(text)
    return evidence


def unavailable_block(reason: str) -> AgentBuildingBlock:
    return {
        "available": False, "agent_class": None, "sdks": [], "agent_framework": False,
        "llm_sdk": False, "manifests": 0, "config": [], "ci_agents": [], "tool_loop_files": 0,
        "mcp_server_files": 0, "source_files_read": 0, "source_files_capped": False,
        "issue_trackers": [], "git_hosts": [], "pairing": False, "reason": reason,
    }


def scan_agent_building(repo_root: Path) -> AgentBuildingBlock:
    """Build the run-context ``agent_building`` block for one repository."""
    repo_root = repo_root.resolve()
    tracked = tracked_files(repo_root)
    if tracked is None:
        return unavailable_block("git tracking unknown (not a git checkout, or git failed)")
    e = _collect(repo_root, tracked)
    pairing = bool(e.trackers) and bool(e.hosts)
    config = sorted(e.config)
    return {
        "available": True,
        "agent_class": classify(agent_framework=e.framework, llm_sdk=e.llm, tool_loop_files=e.tool_loop,
                                pairing=pairing, mcp_server_files=e.mcp_server, config=config),
        "sdks": sorted(e.sdks),
        "agent_framework": e.framework,
        "llm_sdk": e.llm,
        "manifests": e.manifests,
        "config": config,
        "ci_agents": sorted(e.ci_agents),
        "tool_loop_files": e.tool_loop,
        "mcp_server_files": e.mcp_server,
        "source_files_read": e.source_read,
        "source_files_capped": e.capped,
        "issue_trackers": sorted(e.trackers),
        "git_hosts": sorted(e.hosts),
        "pairing": pairing,
    }
