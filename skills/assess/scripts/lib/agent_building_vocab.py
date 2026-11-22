"""The pinned vocabulary of the ``agent_building`` scan (issue #689).

Every name the scan can report is a short slug from these tables, never a
string copied from a file, so the block survives counts-only reduction and a
fleet roll-up can compare repositories by it.

- ``PACKAGES``: dependency names (as declared in a manifest, normalised by
  ``normalise``) to ``(slug, kind)``. Kinds: ``framework`` (an agent framework:
  declaring it is evidence of agent construction on its own), ``llm`` (a model
  SDK: evidence only together with a tool-call loop or the API-client pairing),
  ``mcp`` (the Model Context Protocol SDK, used by servers and clients alike),
  ``tracker`` (an issue-tracker API client) and ``git_host`` (a Git-host API
  client). A name ending in ``*`` matches by prefix; exact names win over
  prefixes.
- ``TOOL_LOOP_PATTERNS`` and ``MCP_SERVER_PATTERNS``: the code heuristic,
  matched per source file. Each is written with ``[.]`` and ``[(]`` rather than
  ``\\.`` and ``\\(``, and no comment quotes a matched form, so this module's own
  source never matches its own patterns.
- ``ENDPOINT_PATTERNS``: REST endpoints of an issue tracker or Git host called
  without a client library.
- ``ISSUE_API_PATTERNS`` and ``CHANGE_API_PATTERNS``: calls to a Git host's
  issues API and to its merge-request or pull-request API, through its client
  library (python-gitlab, PyGithub, githubkit, Octokit, gitbeaker, go-gitlab,
  go-github, gitlab4j, the Kohsuke GitHub API) or REST paths, tagged with the
  host slug. A host whose source calls both is its own issue tracker (issue
  #731). A call form two clients share is listed under both hosts; the scan
  credits a host only when that host's client is also declared or called.
- ``CI_AGENT_PATTERNS``: a CI job that runs an agent.

Widening a table changes which repositories are classified, so it is a change
of its own with a test.
"""
from __future__ import annotations

import re
from typing import Literal

PackageKind = Literal["framework", "llm", "mcp", "tracker", "git_host"]

PACKAGES: dict[str, tuple[str, PackageKind]] = {
    # agent frameworks
    "claude-agent-sdk": ("claude-agent-sdk", "framework"),
    "claude-code-sdk": ("claude-agent-sdk", "framework"),
    "@anthropic-ai/claude-agent-sdk": ("claude-agent-sdk", "framework"),
    "openai-agents": ("openai-agents", "framework"),
    "@openai/agents": ("openai-agents", "framework"),
    "@openai/agents-*": ("openai-agents", "framework"),
    "langgraph": ("langgraph", "framework"),
    "langgraph-*": ("langgraph", "framework"),
    "@langchain/langgraph": ("langgraph", "framework"),
    "langchain": ("langchain", "framework"),
    "langchain-*": ("langchain", "framework"),
    "@langchain/*": ("langchain", "framework"),
    "dev.langchain4j:*": ("langchain4j", "framework"),
    "llama-index": ("llamaindex", "framework"),
    "llama-index-*": ("llamaindex", "framework"),
    "llamaindex": ("llamaindex", "framework"),
    "crewai": ("crewai", "framework"),
    "crewai-tools": ("crewai", "framework"),
    "autogen": ("autogen", "framework"),
    "pyautogen": ("autogen", "framework"),
    "autogen-agentchat": ("autogen", "framework"),
    "autogen-core": ("autogen", "framework"),
    "ag2": ("autogen", "framework"),
    "semantic-kernel": ("semantic-kernel", "framework"),
    "microsoft.semantickernel*": ("semantic-kernel", "framework"),
    "pydantic-ai": ("pydantic-ai", "framework"),
    "pydantic-ai-slim": ("pydantic-ai", "framework"),
    "smolagents": ("smolagents", "framework"),
    "haystack-ai": ("haystack", "framework"),
    "google-adk": ("google-adk", "framework"),
    "@mastra/core": ("mastra", "framework"),
    "org.springframework.ai:*": ("spring-ai", "framework"),
    "strands-agents": ("strands-agents", "framework"),
    "agno": ("agno", "framework"),
    # model SDKs
    "anthropic": ("anthropic", "llm"),
    "@anthropic-ai/sdk": ("anthropic", "llm"),
    "github.com/anthropics/anthropic-sdk-go": ("anthropic", "llm"),
    "com.anthropic:anthropic-java": ("anthropic", "llm"),
    "openai": ("openai", "llm"),
    "github.com/openai/openai-go": ("openai", "llm"),
    "github.com/sashabaranov/go-openai": ("openai", "llm"),
    "com.openai:openai-java": ("openai", "llm"),
    "ruby-openai": ("openai", "llm"),
    "async-openai": ("openai", "llm"),
    "com.azure:azure-ai-openai": ("openai", "llm"),
    "google-genai": ("google-genai", "llm"),
    "google-generativeai": ("google-genai", "llm"),
    "@google/genai": ("google-genai", "llm"),
    "@google/generative-ai": ("google-genai", "llm"),
    "google.golang.org/genai": ("google-genai", "llm"),
    "mistralai": ("mistral", "llm"),
    "@mistralai/mistralai": ("mistral", "llm"),
    "cohere": ("cohere", "llm"),
    "cohere-ai": ("cohere", "llm"),
    "ollama": ("ollama", "llm"),
    "litellm": ("litellm", "llm"),
    "@aws-sdk/client-bedrock-runtime": ("bedrock", "llm"),
    "ai": ("vercel-ai", "llm"),
    "@ai-sdk/*": ("vercel-ai", "llm"),
    # Model Context Protocol
    "mcp": ("mcp", "mcp"),
    "fastmcp": ("mcp", "mcp"),
    "@modelcontextprotocol/sdk": ("mcp", "mcp"),
    "github.com/mark3labs/mcp-go": ("mcp", "mcp"),
    "github.com/modelcontextprotocol/go-sdk": ("mcp", "mcp"),
    "io.modelcontextprotocol.sdk:*": ("mcp", "mcp"),
    "rmcp": ("mcp", "mcp"),
    # issue-tracker API clients
    "jira": ("jira", "tracker"),
    "atlassian-python-api": ("jira", "tracker"),
    "jira-client": ("jira", "tracker"),
    "jira.js": ("jira", "tracker"),
    "jira-ruby": ("jira", "tracker"),
    "github.com/andygrunwald/go-jira": ("jira", "tracker"),
    "com.atlassian.jira:*": ("jira", "tracker"),
    "@linear/sdk": ("linear", "tracker"),
    "linear-api": ("linear", "tracker"),
    # Git-host API clients
    "pygithub": ("github", "git_host"),
    "githubkit": ("github", "git_host"),
    "ghapi": ("github", "git_host"),
    "octokit": ("github", "git_host"),
    "@octokit/*": ("github", "git_host"),
    "github.com/google/go-github*": ("github", "git_host"),
    "org.kohsuke:github-api": ("github", "git_host"),
    "python-gitlab": ("gitlab", "git_host"),
    "@gitbeaker/*": ("gitlab", "git_host"),
    "gitlab": ("gitlab", "git_host"),
    "github.com/xanzy/go-gitlab": ("gitlab", "git_host"),
    "gitlab.com/gitlab-org/api/client-go": ("gitlab", "git_host"),
    "org.gitlab4j:gitlab4j-api": ("gitlab", "git_host"),
}

_EXACT = {name: entry for name, entry in PACKAGES.items() if not name.endswith("*")}
# Longest prefix first, so ``@langchain/langgraph`` (exact) and then the most
# specific family win.
_PREFIXES = sorted(
    ((name[:-1], entry) for name, entry in PACKAGES.items() if name.endswith("*")),
    key=lambda item: -len(item[0]),
)


def normalise(name: str) -> str:
    """A bare Python-style dependency name with PEP 503 separators folded to ``-``, lowercased.

    A scoped npm name, a Go module path and a Maven ``group:artifact``
    coordinate keep their ``/``, ``:`` and ``.``.
    """
    name = name.strip().lower()
    if any(sep in name for sep in "/:@"):
        return name
    return re.sub(r"[-_.]+", "-", name)


def lookup(name: str) -> tuple[str, PackageKind] | None:
    """``(slug, kind)`` for a declared dependency, or None when it is not in the vocabulary.

    The name is tried as written (lowercased; ``jira.js``, ``Microsoft.SemanticKernel``)
    and normalised (``llama_index`` is ``llama-index``), exact names before prefixes.
    """
    keys = (name.strip().lower(), normalise(name))
    for key in keys:
        if key in _EXACT:
            return _EXACT[key]
    for prefix, entry in _PREFIXES:
        if any(key.startswith(prefix) for key in keys):
            return entry
    return None


# A tool-call loop: the model asked for a tool and the code feeds a result back.
TOOL_LOOP_PATTERNS = tuple(re.compile(p) for p in (
    r"stop_reason\W{1,8}tool_use\b",                  # Anthropic: the stop reason is tool use
    r"StopReason[T]oolUse\b",                         # Anthropic Go SDK
    r"finish_reason\W{1,8}tool_calls\b",              # OpenAI: the finish reason is tool calls
    r"[.]tool_calls\b",                               # OpenAI: the message tool-calls attribute
    r"[\"']type[\"']\s*:\s*[\"']tool_result[\"']",    # Anthropic tool_result message
    r"[.]bind_tools[(]",                              # LangChain
    r"\bcreate_react_agent[(]",                       # LangGraph prebuilt agent
    r"\bcreate_tool_calling_agent[(]",                # LangChain
    r"\bAgentExecutor[(]",                            # LangChain
))

# An MCP server: tools, resources or prompts registered on a server object.
MCP_SERVER_PATTERNS = tuple(re.compile(p) for p in (
    r"@(?:mcp|server|app)[.](?:tool|resource|prompt)\b",    # Python FastMCP / low-level server
    r"@\w+[.](?:list_tools|call_tool)[(]",                  # Python low-level server handlers
    r"\bFastMCP[(]",
    r"\bnew\s+McpServer[(]",                                # TypeScript SDK
    r"setRequestHandler[(]\s*(?:CallTool|ListTools)RequestSchema",
    r"\bserver[.](?:tool|registerTool)[(]",                 # TypeScript McpServer
    r"\bNewMCPServer[(]",                                   # mcp-go
    r"\bmcp[.]NewServer[(]",                                # Go SDK
))

# REST endpoints called without a client library: (slug, kind, pattern).
ENDPOINT_PATTERNS: tuple[tuple[str, PackageKind, re.Pattern[str]], ...] = (
    ("jira", "tracker", re.compile(r"/rest/api/(?:2|3|latest)/(?:issue|search)\b")),
    ("linear", "tracker", re.compile(r"\bapi[.]linear[.]app\b")),
    ("github", "git_host", re.compile(r"\bapi[.]github[.]com\b")),
    ("gitlab", "git_host", re.compile(r"/api/v4/project[s]\b")),
)

# Same-host ticket-to-merge-request calls (issue #731): (host slug, pattern).
# A call to the host's issues API.
ISSUE_API_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (slug, re.compile(p)) for slug, p in (
        ("gitlab", r"[.]issues[.](?:list|get|create)[(]"),         # python-gitlab project manager
        ("gitlab", r"\bIssues[.]\w+[(]"),                          # gitbeaker, go-gitlab
        ("gitlab", r"\bgetIssuesApi[(]"),                           # gitlab4j
        ("gitlab", r"/project[s]/[^\s\"'`]*/issues\b"),             # REST v4
        ("github", r"[.]issues[.]\w+[(]"),                          # Octokit, githubkit
        ("github", r"\bIssues[.]\w+[(]"),                          # go-github
        ("github", r"[.](?:get_issues?|create_issue)[(]"),          # PyGithub
        ("github", r"[.]getIssues?[(]"),                            # Kohsuke GitHub API
        ("github", r"/repo[s]/[^\s\"'`]*/issues\b"),                # REST
    ))
# A call to the host's merge-request or pull-request API.
CHANGE_API_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (slug, re.compile(p)) for slug, p in (
        ("gitlab", r"[.]mergerequests[.]\w+[(]"),                   # python-gitlab
        ("gitlab", r"\bMergeRequests[.]\w+[(]"),                   # gitbeaker, go-gitlab
        ("gitlab", r"\bgetMergeRequestApi[(]"),                     # gitlab4j
        ("gitlab", r"/project[s]/[^\s\"'`]*/merge_requests\b"),     # REST v4
        ("github", r"[.]pulls[.]\w+[(]"),                           # Octokit, githubkit
        ("github", r"\bPullRequests[.]\w+[(]"),                    # go-github
        ("github", r"[.](?:create_pull|get_pulls?)[(]"),            # PyGithub
        ("github", r"[.]createPullRequest[(]"),                     # Kohsuke GitHub API
        ("github", r"/repo[s]/[^\s\"'`]*/pulls\b"),                 # REST
    ))

# A CI job that runs an agent: (slug, pattern), matched per CI file.
CI_AGENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("claude-code", re.compile(r"anthropics/claude-code(?:-base)?-action\b")),
    ("claude-code", re.compile(r"\bclaude\s+(?:-p|--print)\b")),
    ("codex", re.compile(r"openai/codex-action\b")),
    ("codex", re.compile(r"\bcodex\s+exec\b")),
    ("gemini-cli", re.compile(r"google-github-actions/run-gemini-cli\b")),
    ("aider", re.compile(r"\baider\s+(?:--message|-m|--yes)\b")),
    ("cursor-agent", re.compile(r"\bcursor-agent\s+(?:-p|--print)\b")),
)
