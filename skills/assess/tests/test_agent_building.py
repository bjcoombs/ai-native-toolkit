"""Tests for the agent-building inventory scan (issue #689)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path, PurePosixPath

import pytest

from lib import agent_manifests as m
from lib.agent_building import (
    CLASSES,
    AgentBuildingBlock,
    classify,
    is_test_path,
    scan_agent_building,
)
from lib.agent_building_vocab import (
    CI_AGENT_PATTERNS,
    ENDPOINT_PATTERNS,
    MCP_SERVER_PATTERNS,
    PACKAGES,
    TOOL_LOOP_PATTERNS,
    lookup,
    normalise,
)
from lib.counts_only import counts_only_scans
from lib.counts_only_rules import TOKEN_RE
from lib.scan_registry import SCANS

# ── fixture repositories, one per class ─────────────────────────────────────

AGENT_LOOP = """\
import anthropic
client = anthropic.Anthropic()
while True:
    response = client.messages.create(model="m", messages=messages, tools=tools)
    if response.stop_reason != "tool_use":
        break
    messages.append({"role": "user", "content": [{"type": "tool_result", "content": "ok"}]})
"""

MCP_SERVER = """\
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("tickets")

@mcp.tool()
def lookup(ticket: str) -> str:
    return ticket
"""

FIXTURES: dict[str, dict[str, str]] = {
    "builds-agent": {
        "pyproject.toml": '[project]\nname = "bot"\ndependencies = ["anthropic>=0.40", "requests"]\n',
        "src/bot/loop.py": AGENT_LOOP,
        "CLAUDE.md": "# rules\n",
    },
    "builds-mcp-server": {
        "requirements.txt": "mcp==1.2.0\n",
        "server.py": MCP_SERVER,
    },
    "uses-agent-tooling": {
        "AGENTS.md": "# agents\n",
        ".mcp.json": json.dumps({"mcpServers": {}}),
        ".claude/agents/reviewer.md": "---\nname: reviewer\n---\n",
        "src/app.py": "print('hello')\n",
    },
    "none": {
        "package.json": json.dumps({"dependencies": {"express": "^4"}}),
        "index.js": "console.log('hi')\n",
    },
}


def _repo(tmp_path: Path, name: str, files: dict[str, str], *, untracked: dict[str, str] | None = None) -> Path:
    repo = tmp_path / name
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    repo.mkdir(exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    for rel, text in (untracked or {}).items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return repo


@pytest.mark.parametrize("expected", list(FIXTURES))
def test_fixture_repositories_are_classified(tmp_path: Path, expected: str) -> None:
    block = scan_agent_building(_repo(tmp_path, expected, FIXTURES[expected]))
    assert block["available"] is True
    assert block["agent_class"] == expected


def test_builds_agent_block_records_each_kind_of_evidence(tmp_path: Path) -> None:
    block = scan_agent_building(_repo(tmp_path, "a", FIXTURES["builds-agent"]))
    assert block["sdks"] == ["anthropic"]
    assert block["llm_sdk"] is True and block["agent_framework"] is False
    assert block["manifests"] == 1
    assert block["tool_loop_files"] == 1
    assert block["mcp_server_files"] == 0
    assert block["config"] == ["instruction_file"]


def test_only_agent_configuration_is_uses_agent_tooling(tmp_path: Path) -> None:
    files = {**FIXTURES["uses-agent-tooling"],
             ".github/workflows/review.yml": "jobs:\n  r:\n    steps:\n      - uses: anthropics/claude-code-action@v1\n"}
    block = scan_agent_building(_repo(tmp_path, "cfg", files))
    assert block["agent_class"] == "uses-agent-tooling"
    assert block["config"] == ["ci_agent_job", "claude_agents", "instruction_file", "mcp_json"]
    assert block["ci_agents"] == ["claude-code"]


def test_ticket_to_merge_request_pairing_makes_an_sdk_repo_an_agent(tmp_path: Path) -> None:
    files = {
        "package.json": json.dumps({"dependencies": {"openai": "^4", "jira.js": "^4",
                                                     "@gitbeaker/rest": "^40"}}),
        "src/index.ts": "export const x = 1;\n",
    }
    block = scan_agent_building(_repo(tmp_path, "t2mr", files))
    assert block["issue_trackers"] == ["jira"]
    assert block["git_hosts"] == ["gitlab"]
    assert block["pairing"] is True
    assert block["agent_class"] == "builds-agent"


def test_endpoints_in_source_count_as_api_clients(tmp_path: Path) -> None:
    files = {"bot.py": 'J = "https://jira.example.com/rest/api/2/issue"\nG = "https://gl/api/v4/projects"\n'}
    block = scan_agent_building(_repo(tmp_path, "raw", files))
    assert (block["issue_trackers"], block["git_hosts"], block["pairing"]) == (["jira"], ["gitlab"], True)
    # No model SDK: pairing alone is not agent construction.
    assert block["agent_class"] == "none"


def test_a_model_sdk_without_a_tool_loop_is_not_an_agent(tmp_path: Path) -> None:
    files = {"requirements.txt": "openai\n", "summarise.py": "client.chat.completions.create()\n"}
    assert scan_agent_building(_repo(tmp_path, "sum", files))["agent_class"] == "none"


def test_an_agent_framework_alone_builds_an_agent(tmp_path: Path) -> None:
    files = {"pyproject.toml": '[tool.poetry.dependencies]\npython = "^3.11"\nlangchain-openai = "*"\n'}
    block = scan_agent_building(_repo(tmp_path, "lc", files))
    assert block["sdks"] == ["langchain"]
    assert block["agent_class"] == "builds-agent"


def test_test_files_vendored_and_untracked_files_are_not_evidence(tmp_path: Path) -> None:
    files = {
        "tests/test_loop.py": AGENT_LOOP,
        "src/loop_test.go": "if resp.StopReasonToolUse {}\n",
        "node_modules/x/package.json": json.dumps({"dependencies": {"@openai/agents": "1"}}),
        "web/app.spec.ts": MCP_SERVER,
    }
    block = scan_agent_building(_repo(tmp_path, "t", files, untracked={"server.py": MCP_SERVER}))
    assert block["tool_loop_files"] == 0
    assert block["mcp_server_files"] == 0
    assert block["sdks"] == []
    assert block["agent_class"] == "none"


def test_source_file_cap_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("lib.agent_building.MAX_SOURCE_FILES", 1)
    block = scan_agent_building(_repo(tmp_path, "cap", {"a.py": "x=1\n", "b.py": "y=2\n"}))
    assert block["source_files_read"] == 1
    assert block["source_files_capped"] is True


def test_not_a_git_checkout_is_unavailable(tmp_path: Path) -> None:
    (tmp_path / "plain").mkdir()
    block = scan_agent_building(tmp_path / "plain")
    assert block["available"] is False
    assert block["agent_class"] is None
    assert "git" in block["reason"]


def test_symlinked_config_is_not_credited(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "s", {"real.md": "# x\n"})
    (repo / "CLAUDE.md").symlink_to(repo / "real.md")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    assert scan_agent_building(repo)["config"] == []


@pytest.mark.parametrize("module", [
    "agent_building.py", "agent_building_vocab.py", "agent_manifests.py", "fleet_agent_building.py"])
def test_the_scan_never_matches_its_own_source(module: str) -> None:
    text = (Path(__file__).resolve().parents[1] / "scripts" / "lib" / module).read_text(encoding="utf-8")
    # The patterns matched against source files (the CI patterns read CI files only).
    patterns = [*TOOL_LOOP_PATTERNS, *MCP_SERVER_PATTERNS, *(p for _, _, p in ENDPOINT_PATTERNS)]
    assert [p.pattern for p in patterns if p.search(text)] == []


def test_every_reported_string_is_a_counts_only_token() -> None:
    slugs = {slug for slug, _ in PACKAGES.values()} | {slug for slug, _, _ in ENDPOINT_PATTERNS}
    slugs |= {slug for slug, _ in CI_AGENT_PATTERNS} | set(CLASSES)
    slugs |= {"instruction_file", "mcp_json", "claude_agents", "ci_agent_job"}
    assert [s for s in sorted(slugs) if not TOKEN_RE.match(s)] == []


# ── class rule ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("framework", "llm", "loop", "pairing", "mcp", "config", "expected"), [
    (True, False, 0, False, 0, [], "builds-agent"),
    (False, True, 1, False, 0, [], "builds-agent"),
    (False, True, 0, True, 0, [], "builds-agent"),
    (False, True, 0, False, 0, [], "none"),
    (False, False, 3, True, 0, [], "none"),
    (False, True, 0, False, 1, [], "builds-mcp-server"),
    (True, False, 0, False, 1, ["instruction_file"], "builds-agent"),
    (False, False, 0, False, 1, ["mcp_json"], "builds-mcp-server"),
    (False, False, 0, False, 0, ["mcp_json"], "uses-agent-tooling"),
    (False, False, 0, False, 0, [], "none"),
])
def test_classify_precedence(framework: bool, llm: bool, loop: int, pairing: bool, mcp: int,
                             config: list[str], expected: str) -> None:
    assert classify(agent_framework=framework, llm_sdk=llm, tool_loop_files=loop, pairing=pairing,
                    mcp_server_files=mcp, config=config) == expected


# ── vocabulary and manifests ────────────────────────────────────────────────

@pytest.mark.parametrize(("name", "expected"), [
    ("Anthropic", ("anthropic", "llm")),
    ("llama_index.core", ("llamaindex", "framework")),
    ("@langchain/langgraph", ("langgraph", "framework")),
    ("@langchain/core", ("langchain", "framework")),
    ("langchain-community", ("langchain", "framework")),
    ("jira.js", ("jira", "tracker")),
    ("Microsoft.SemanticKernel.Core", ("semantic-kernel", "framework")),
    ("github.com/google/go-github/v60", ("github", "git_host")),
    ("dev.langchain4j:langchain4j-open-ai", ("langchain4j", "framework")),
    ("PyGithub", ("github", "git_host")),
    ("requests", None),
])
def test_lookup(name: str, expected: tuple[str, str] | None) -> None:
    assert lookup(name) == expected


def test_normalise_folds_only_bare_names() -> None:
    assert normalise("Llama_Index") == "llama-index"
    assert normalise("@Scope/pkg.js") == "@scope/pkg.js"


def test_manifest_readers() -> None:
    assert m.package_json('{"devDependencies": {"ai": "1"}, "dependencies": {"x": "1"}}') == ["x", "ai"]
    assert m.package_json("not json") == []
    pyproject = (
        '[project]\ndependencies = ["anthropic>=1"]\n'
        '[project.optional-dependencies]\nmcp = ["mcp[cli]"]\n'
        '[dependency-groups]\ndev = ["pytest", {include-group = "x"}]\n'
        '[tool.poetry.group.ai.dependencies]\ncrewai = "*"\n'
    )
    assert m.pyproject(pyproject) == ["anthropic", "mcp", "pytest", "crewai"]
    assert m.pyproject("[broken") == []
    assert m.requirements("# c\n-r base.txt\nopenai==1\n\n") == ["openai"]
    assert m.pipfile('[packages]\nlitellm = "*"\n') == ["litellm"]
    assert m.go_mod("module x\nrequire (\n\tgithub.com/openai/openai-go v1.0.0\n)\n"
                    "require github.com/xanzy/go-gitlab v0.1.0\n") == [
        "github.com/openai/openai-go", "github.com/xanzy/go-gitlab"]
    assert m.pom_xml("<dependency><groupId>com.anthropic</groupId>"
                     "<artifactId>anthropic-java</artifactId></dependency>") == ["com.anthropic:anthropic-java"]
    assert m.gradle('implementation("org.springframework.ai:spring-ai-core:1.0")') == [
        "org.springframework.ai:spring-ai-core"]
    assert m.cargo_toml('[dependencies]\nrmcp = "0.1"\n') == ["rmcp"]
    assert m.gemfile("gem 'ruby-openai'\n") == ["ruby-openai"]
    assert m.csproj('<PackageReference Include="Microsoft.SemanticKernel" />') == ["Microsoft.SemanticKernel"]


def test_reader_for() -> None:
    assert m.reader_for("requirements-dev.txt") is m.requirements
    assert m.reader_for("App.csproj") is m.csproj
    assert m.reader_for("Gemfile") is m.gemfile
    assert m.reader_for("README.md") is None


def test_is_test_path() -> None:
    assert is_test_path(PurePosixPath("pkg/tests/x.py"))
    assert is_test_path(PurePosixPath("a/test_x.py"))
    assert is_test_path(PurePosixPath("a/x_test.go"))
    assert is_test_path(PurePosixPath("a/x.test.ts"))
    assert not is_test_path(PurePosixPath("src/contest.py"))


# ── counts-only and registry ────────────────────────────────────────────────

def test_counts_only_keeps_every_field_the_roll_up_reads(tmp_path: Path) -> None:
    block: AgentBuildingBlock = scan_agent_building(_repo(tmp_path, "a", FIXTURES["builds-agent"]))
    reduced = counts_only_scans({"agent_building": block}, hash_paths=True, salt="s")["agent_building"]
    assert reduced == block


def test_scan_is_registered() -> None:
    spec = next(s for s in SCANS if s.key == "agent_building")
    assert spec.fn is scan_agent_building
    assert spec.network is False
