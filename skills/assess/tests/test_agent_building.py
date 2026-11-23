"""Tests for the agent-building inventory scan (issue #689)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path, PurePosixPath

import pytest

import lib.agents.agent_manifests as m
from lib.agents.agent_building import (
    CLASSES,
    AgentBuildingBlock,
    classify,
    is_test_path,
    scan_agent_building,
)
from lib.agents.agent_building_vocab import (
    CHANGE_API_PATTERNS,
    CI_AGENT_PATTERNS,
    ENDPOINT_PATTERNS,
    ISSUE_API_PATTERNS,
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


GITLAB_BOT = """\
import anthropic
import gitlab
def run():
    gl = gitlab.Gitlab('https://gl.test')
    p = gl.projects.get('grp/x')
    issue = p.issues.list()[0]
    text = anthropic.Anthropic().messages.create(model='m', max_tokens=10, messages=[{'role': 'user', 'content': issue.title}])
    p.mergerequests.create({'source_branch': 'b', 'target_branch': 'main', 'title': str(text)})
"""
GITLAB_PYPROJECT = "[project]\nname = 'x'\nversion = '0.1.0'\ndependencies = ['anthropic', 'python-gitlab']\n"


def test_same_host_issues_and_merge_requests_are_the_pairing(tmp_path: Path) -> None:
    # src/bot.py sorts before pyproject.toml: the client is declared after the calls are read.
    block = scan_agent_building(_repo(tmp_path, "pair", {"pyproject.toml": GITLAB_PYPROJECT,
                                                         "src/bot.py": GITLAB_BOT}))
    assert (block["issue_trackers"], block["git_hosts"], block["pairing"]) == (["gitlab"], ["gitlab"], True)
    assert block["agent_class"] == "builds-agent"


def test_same_host_issues_without_merge_requests_is_not_the_pairing(tmp_path: Path) -> None:
    bot = GITLAB_BOT.rsplit("    p.mergerequests", 1)[0]
    block = scan_agent_building(_repo(tmp_path, "issues", {"pyproject.toml": GITLAB_PYPROJECT,
                                                           "src/bot.py": bot}))
    assert (block["issue_trackers"], block["git_hosts"], block["pairing"]) == ([], ["gitlab"], False)
    assert block["agent_class"] == "none"


@pytest.mark.parametrize(("deps", "source", "host"), [
    ({"@octokit/rest": "^20"},
     "await octokit.rest.issues.listForRepo(q);\nawait octokit.rest.pulls.create(pr);\n", "github"),
    ({"@gitbeaker/rest": "^40"},
     "const i = await api.Issues.all({projectId});\nawait api.MergeRequests.create(p, 'b', 'main', 't');\n",
     "gitlab"),
])
def test_same_host_pairing_through_javascript_clients(tmp_path: Path, deps: dict[str, str], source: str,
                                                      host: str) -> None:
    files = {"package.json": json.dumps({"dependencies": {"openai": "^4", **deps}}), "src/bot.ts": source}
    block = scan_agent_building(_repo(tmp_path, host, files))
    assert (block["issue_trackers"], block["pairing"], block["agent_class"]) == ([host], True, "builds-agent")


@pytest.mark.parametrize(("manifest", "source"), [
    ("requirements.txt", "issue = repo.get_issue(1)\nrepo.create_pull(title='t', head='b', base='main')\n"),
    ("go.mod", "client.Issues.Get(ctx, o, r, 1)\nclient.PullRequests.Create(ctx, o, r, pr)\n"),
])
def test_same_host_pairing_through_pygithub_and_go_github(tmp_path: Path, manifest: str, source: str) -> None:
    manifests = {"requirements.txt": "openai\nPyGithub\n",
                 "go.mod": "module x\n\ngo 1.22\n\nrequire (\n\tgithub.com/google/go-github/v60 v60.0.0\n"
                           "\tgithub.com/openai/openai-go v0.1.0\n)\n"}
    name = "bot.go" if manifest == "go.mod" else "bot.py"
    block = scan_agent_building(_repo(tmp_path, "gh", {manifest: manifests[manifest], name: source}))
    assert (block["issue_trackers"], block["git_hosts"], block["pairing"]) == (["github"], ["github"], True)


def test_go_gitlab_issues_and_merge_requests_are_the_pairing(tmp_path: Path) -> None:
    files = {"go.mod": "module x\n\ngo 1.22\n\nrequire gitlab.com/gitlab-org/api/client-go v0.1.0\n",
             "bot.go": "git.Issues.ListProjectIssues(pid, opt)\ngit.MergeRequests.CreateMergeRequest(pid, mr)\n"}
    assert scan_agent_building(_repo(tmp_path, "gogl", files))["pairing"] is True


def test_same_host_pairing_needs_one_host_for_both_calls(tmp_path: Path) -> None:
    # GitLab issues (gitlab4j) and a GitHub pull request (PyGithub) are not one host's pairing.
    files = {"requirements.txt": "openai\npython-gitlab\nPyGithub\n",
             "bot.py": "api.getIssuesApi()\nrepo.create_pull(title='t', head='b', base='main')\n"}
    block = scan_agent_building(_repo(tmp_path, "mixed", files))
    assert (block["issue_trackers"], block["pairing"]) == ([], False)


@pytest.mark.parametrize("issues_call", ["x = p.issues.list()", "issue = gl.Issues.GetIssue(pid, 1)"])
def test_an_issues_call_two_declared_hosts_share_is_not_the_pairing(tmp_path: Path, issues_call: str) -> None:
    # python-gitlab issues (or go-gitlab's Issues service) with a GitHub pull request, both clients declared.
    files = {"requirements.txt": "openai\npython-gitlab\nPyGithub\n",
             "bot.py": f"{issues_call}\nrepo.create_pull(title='t', head='b', base='main')\n"}
    block = scan_agent_building(_repo(tmp_path, "ambiguous", files))
    assert (block["issue_trackers"], block["pairing"], block["agent_class"]) == ([], False, "none")


POM_BOTH_HOSTS = """<project><dependencies>
<dependency><groupId>com.anthropic</groupId><artifactId>anthropic-java</artifactId></dependency>
<dependency><groupId>org.gitlab4j</groupId><artifactId>gitlab4j-api</artifactId></dependency>
<dependency><groupId>org.kohsuke</groupId><artifactId>github-api</artifactId></dependency>
</dependencies></project>
"""


@pytest.mark.parametrize("issues_call", [
    "List<Issue> issues = issuesApi.getIssues(projectId);",  # gitlab4j or Kohsuke: ambiguous
    "GHIssue issue = repo.getIssue(1);",                     # the same form, singular
])
def test_a_java_issues_call_two_declared_hosts_share_is_not_the_pairing(tmp_path: Path, issues_call: str) -> None:
    files = {"pom.xml": POM_BOTH_HOSTS,
             "src/main/java/Bot.java": f"{issues_call}\nrepo.createPullRequest(\"t\", \"b\", \"main\", \"x\");\n"}
    block = scan_agent_building(_repo(tmp_path, "java", files))
    assert block["git_hosts"] == ["github", "gitlab"]
    assert block["llm_sdk"] is True
    assert (block["issue_trackers"], block["pairing"], block["agent_class"]) == ([], False, "none")


def test_a_java_issues_call_pairs_with_one_declared_host(tmp_path: Path) -> None:
    pom = POM_BOTH_HOSTS.replace(
        "<dependency><groupId>org.gitlab4j</groupId><artifactId>gitlab4j-api</artifactId></dependency>\n", "")
    files = {"pom.xml": pom,
             "src/main/java/Bot.java": "GHIssue i = repo.getIssue(1);\nrepo.createPullRequest(\"t\", \"b\", \"main\", \"x\");\n"}
    block = scan_agent_building(_repo(tmp_path, "java1", files))
    assert (block["issue_trackers"], block["pairing"], block["agent_class"]) == (["github"], True, "builds-agent")


def test_an_unambiguous_issues_call_still_pairs_with_two_hosts_declared(tmp_path: Path) -> None:
    files = {"requirements.txt": "openai\npython-gitlab\nPyGithub\n",
             "bot.py": "issue = repo.get_issue(1)\nrepo.create_pull(title='t', head='b', base='main')\n"}
    block = scan_agent_building(_repo(tmp_path, "gh2", files))
    assert (block["issue_trackers"], block["pairing"]) == (["github"], True)


def test_an_unrelated_issues_attribute_is_not_an_issues_call(tmp_path: Path) -> None:
    # A Django related manager named issues, beside a real PyGithub pull-request call.
    files = {"requirements.txt": "openai\nPyGithub\n",
             "bot.py": "qs = self.issues.filter(x=1)\nrepo.get_pulls()\n"}
    assert scan_agent_building(_repo(tmp_path, "django", files))["pairing"] is False


@pytest.mark.parametrize("call", ["octokit.rest.issues.listForRepo(q)", "github.rest.issues.create(o, r)",
                                  "p.issues.get(1)"])
def test_issues_client_verbs_are_issues_calls(tmp_path: Path, call: str) -> None:
    files = {"requirements.txt": "openai\nPyGithub\n", "bot.py": f"{call}\nrepo.get_pulls()\n"}
    assert scan_agent_building(_repo(tmp_path, "verbs", files))["pairing"] is True


def test_same_host_calls_without_a_declared_client_are_not_the_pairing(tmp_path: Path) -> None:
    files = {"bot.py": "x = p.issues.list()\np.mergerequests.create({})\n"}
    block = scan_agent_building(_repo(tmp_path, "noclient", files))
    assert (block["issue_trackers"], block["git_hosts"], block["pairing"]) == ([], [], False)


def test_same_host_calls_in_test_files_are_not_the_pairing(tmp_path: Path) -> None:
    files = {"pyproject.toml": GITLAB_PYPROJECT, "testdata/bot.py": GITLAB_BOT, "tests/test_bot.py": GITLAB_BOT}
    assert scan_agent_building(_repo(tmp_path, "tests", files))["pairing"] is False


def test_the_docstring_lists_every_excluded_test_directory() -> None:
    import lib.agents.agent_building as ab
    doc = ab.__doc__ or ""
    assert [d for d in sorted(ab._TEST_DIRS) if f"``{d}``" not in doc] == []


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
    monkeypatch.setattr("lib.agents.agent_building.MAX_SOURCE_FILES", 1)
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
    pytest.param("agents/agent_building.py", id="agent_building.py"),
    pytest.param("agents/agent_building_vocab.py", id="agent_building_vocab.py"),
    pytest.param("agents/agent_manifests.py", id="agent_manifests.py"),
    pytest.param("fleet/fleet_agent_building.py", id="fleet_agent_building.py")])
def test_the_scan_never_matches_its_own_source(module: str) -> None:
    text = (Path(__file__).resolve().parents[1] / "scripts" / "lib" / module).read_text(encoding="utf-8")
    # The patterns matched against source files (the CI patterns read CI files only).
    patterns = [*TOOL_LOOP_PATTERNS, *MCP_SERVER_PATTERNS, *(p for _, _, p in ENDPOINT_PATTERNS),
                *(p for _, p in ISSUE_API_PATTERNS), *(p for _, p in CHANGE_API_PATTERNS)]
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


@pytest.mark.parametrize("text", [
    '[project]\ndependencies = "anthropic"\n',
    '[project]\noptional-dependencies = ["anthropic"]\n',
    'dependency-groups = [1]\n',
    '[tool.poetry]\ngroup = [1]\n',
    '[tool.uv]\ndev-dependencies = "openai"\n',
])
def test_pyproject_tolerates_malformed_shapes(text: str) -> None:
    assert m.pyproject(text) == []


def test_a_malformed_pyproject_keeps_the_rest_of_the_block(tmp_path: Path) -> None:
    files = {"pyproject.toml": "dependency-groups = [1]\n", "requirements.txt": "crewai\n"}
    block = scan_agent_building(_repo(tmp_path, "bad", files))
    assert block["available"] is True
    assert block["sdks"] == ["crewai"]


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
