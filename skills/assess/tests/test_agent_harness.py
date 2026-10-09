"""Tests for lib/agent_harness.py - the committed agent surface that runs code.

The planted fixture carries one instance of every finding kind and must yield
exactly those findings; the clean fixture carries the same features configured
safely, plus the near misses (a trigger named only in a comment, a deploy on a
push trigger, a review bot on ``pull_request_target``), and must yield none.
Hidden characters are built with ``chr`` so this file holds none itself.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from assess_core import build_run_context
from lib.agent_harness import _on_block, _safe_url, scan_agent_harness

ZWSP, RLO, BOM = chr(0x200B), chr(0x202E), chr(0xFEFF)
REPO_ROOT = Path(__file__).resolve().parents[3]


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _json(root: Path, rel: str, data: object) -> None:
    _write(root, rel, json.dumps(data, indent=2) + "\n")


def _force_add(repo: Path, rel: str) -> None:
    """Stage ``rel`` past any ignore rule, including a machine-wide git ignore."""
    subprocess.run(["git", "-C", str(repo), "add", "-f", rel], check=True)


def _kinds(block: dict) -> list[tuple[str, str]]:
    return [(f["kind"], f["file"]) for f in block["findings"]]


SKILL_WITH_HOOKS = """---
name: deploy
description: Deploy things.
hooks:
  PreToolUse:
    - matcher: "Bash"
      hooks:
        - type: command
          command: "./scripts/check.sh"
  PostToolUse:
    - hooks:
        - type: http
          url: https://hooks.example.com/x?token=abc
  Stop:
    - hooks:
        - type: prompt
allowed-tools: Bash
---
Body.
"""


def _plant(root: Path) -> None:
    """One instance of every finding kind, plus every inventory kind."""
    _json(root, ".claude/settings.json", {
        "hooks": {"PreToolUse": [{"matcher": "Bash",
                                  "hooks": [{"type": "command", "command": "./hooks/guard.sh"}]}]},
        "env": {"API_TOKEN": "s3cr3t-value"},
        "apiKeyHelper": "./bin/key.sh",
        "disableAllHooks": False,
        "permissions": {"allow": ["Bash(*)", "Read"], "deny": []},
    })
    _json(root, ".claude/settings.local.json", {"permissions": {"allow": ["Bash(ls:*)"]}})
    _json(root, ".mcp.json", {"mcpServers": {
        "db": {"command": "npx", "args": ["--token", "argsecret"]},
        "remote": {"type": "http", "url": "https://user:pw@mcp.example.com/sse?key=zzz#f"},
    }})
    _json(root, ".cursor/hooks.json", {"version": 1, "hooks": {"afterFileEdit": [{"command": "./fmt.sh"}]}})
    _write(root, ".claude/skills/deploy/SKILL.md", SKILL_WITH_HOOKS)
    _write(root, ".devcontainer/devcontainer.json",
           '{\n  "mounts": [\n    "source=${localEnv:HOME}/.ssh,target=/root/.ssh,type=bind"\n  ]\n}\n')
    _write(root, "CLAUDE.md", f"# Rules\n\nBe kind.{ZWSP}\n")
    _write(root, ".github/workflows/deploy.yml",
           "on:\n  issue_comment:\n    types: [created]\njobs:\n  go:\n    runs-on: ubuntu-latest\n"
           "    steps:\n      - run: terraform apply -auto-approve\n")
    _write(root, ".gitignore", ".env\n")
    _write(root, ".env.production", "DB_PASSWORD=hunter2\n")
    _write(root, ".env.example", "DB_PASSWORD=\n")


PLANTED = [
    (".claude/settings.json", "broad_allow"),
    (".claude/settings.json", "hooks_forced_on"),
    (".claude/settings.json", "missing_env_deny"),
    (".claude/settings.local.json", "tracked_local_settings"),
    (".devcontainer/devcontainer.json", "credential_mount"),
    (".env.production", "tracked_env_file"),
    (".github/workflows/deploy.yml", "privileged_comment_trigger"),
    ("CLAUDE.md", "hidden_unicode"),
]


def test_planted_fixture_yields_exactly_one_finding_per_kind(git_repo) -> None:
    repo, commit = git_repo
    _plant(repo)
    _force_add(repo, ".claude/settings.local.json")
    commit("init")
    block = scan_agent_harness(repo)
    assert block["available"] is True and block["tracked_known"] is True
    assert sorted((f["file"], f["kind"]) for f in block["findings"]) == PLANTED
    assert block["finding_count"] == 8
    assert block["counts_by_kind"] == {k: 1 for _, k in PLANTED}
    assert all(f["fix"] for f in block["findings"])


def test_planted_fixture_inventory(git_repo) -> None:
    repo, commit = git_repo
    _plant(repo)
    _force_add(repo, ".claude/settings.local.json")
    commit("init")
    rows = [(i["kind"], i["file"], i["name"], i["detail"]) for i in scan_agent_harness(repo)["executes"]]
    assert rows == [
        ("settings_hook", ".claude/settings.json", "PreToolUse [Bash]", "./hooks/guard.sh"),
        ("env", ".claude/settings.json", "API_TOKEN", None),
        ("api_key_helper", ".claude/settings.json", "apiKeyHelper", "./bin/key.sh"),
        ("frontmatter_hook", ".claude/skills/deploy/SKILL.md", "PreToolUse", "./scripts/check.sh"),
        ("frontmatter_hook", ".claude/skills/deploy/SKILL.md", "PostToolUse",
         "https://hooks.example.com/x"),
        ("frontmatter_hook", ".claude/skills/deploy/SKILL.md", "Stop", None),
        ("cursor_hook", ".cursor/hooks.json", "afterFileEdit", "./fmt.sh"),
        ("mcp_server", ".mcp.json", "db", "npx"),
        ("mcp_server", ".mcp.json", "remote", "https://mcp.example.com/sse"),
    ]


def test_no_secret_value_or_hidden_character_reaches_the_block(git_repo) -> None:
    repo, commit = git_repo
    _plant(repo)
    _force_add(repo, ".claude/settings.local.json")
    commit("init")
    dumped = json.dumps(scan_agent_harness(repo), ensure_ascii=False)
    for secret in ("s3cr3t-value", "hunter2", "argsecret", "token=abc", "pw@", "key=zzz", ZWSP):
        assert secret not in dumped
    hidden = next(f for f in scan_agent_harness(repo)["findings"] if f["kind"] == "hidden_unicode")
    assert hidden["line"] == 3
    assert hidden["detail"] == "1 hidden character(s): U+200B ZERO WIDTH SPACE"


def _clean(root: Path) -> None:
    """The same features configured safely, plus near misses."""
    _json(root, ".claude/settings.json", {
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "./hooks/notify.sh"}]}]},
        "permissions": {"allow": ["Bash(npm run test:*)", "Read(*)"],
                        "deny": ["Read(./.env)", "Read(./.env.*)"]},
        "disableAllHooks": True,
    })
    _write(root, ".gitignore", ".env\n.env.*\n!.env.example\n.claude/settings.local.json\n")
    _write(root, ".env.example", "X=\n")
    _write(root, ".env.sample", "X=\n")
    _write(root, "config/.env.template", "X=\n")
    _write(root, ".devcontainer/devcontainer.json",
           '{\n  // "source=${localEnv:HOME}/.aws,target=/root/.aws,type=bind"\n'
           '  "mounts": ["source=ssh-vol,target=/home/vscode/.ssh,type=volume"]\n}\n')
    _write(root, "CLAUDE.md", f"{BOM}# Rules\n")
    # A review bot on pull_request_target posts comments; it deploys nothing.
    _write(root, ".github/workflows/review.yml",
           "on:\n  pull_request_target:\n    types: [opened]\njobs:\n  r:\n    runs-on: ubuntu-latest\n"
           "    steps:\n      # never terraform apply here\n      - run: gh pr comment 1 --body ok\n")
    # A deploy on push, with the risky trigger named only in a comment.
    _write(root, ".github/workflows/deploy.yml",
           "on:  # not issue_comment\n  push:\n    branches: [main]\njobs:\n  d:\n"
           "    runs-on: ubuntu-latest\n    steps:\n      - run: terraform apply -auto-approve\n"
           "      - run: echo pull_request_target\n")


def test_clean_fixture_yields_no_findings(git_repo) -> None:
    repo, commit = git_repo
    _clean(repo)
    commit("init")
    block = scan_agent_harness(repo)
    assert block["findings"] == []
    assert block["finding_count"] == 0
    assert block["executes_by_kind"] == {"settings_hook": 1}


def test_this_repository_raises_no_false_findings() -> None:
    block = scan_agent_harness(REPO_ROOT)
    assert block["available"] is True
    assert block["findings"] == []


# --- the edge-case matrix -----------------------------------------------------

@pytest.mark.parametrize(("rule", "broad"), [
    ("Bash", True), ("Bash(*)", True), ("Bash(:*)", True), ("Bash( * )", True),
    ("Bash(**)", True), ("Bash(npm test:*)", False), ("Read(*)", False), ("Bashful", False),
])
def test_broad_allow_variants(tmp_path: Path, rule: str, broad: bool) -> None:
    _json(tmp_path, ".claude/settings.json", {"permissions": {"allow": [rule]}})
    assert _kinds(scan_agent_harness(tmp_path)) == ([("broad_allow", ".claude/settings.json")]
                                                    if broad else [])


@pytest.mark.parametrize(("name", "flagged"), [
    (".env", True), (".env.local", True), ("svc/.env", True), (".env.production.local", True),
    (".env.example", False), (".env.sample", False), (".env.template", False),
    (".env.dist", False), (".env.local.example", False), (".envrc", False), ("env.txt", False),
])
def test_tracked_env_file_names(git_repo, name: str, flagged: bool) -> None:
    repo, commit = git_repo
    _write(repo, name, "X=1\n")
    commit("init")
    got = [f["file"] for f in scan_agent_harness(repo)["findings"] if f["kind"] == "tracked_env_file"]
    assert got == ([name] if flagged else [])


def test_untracked_env_and_local_settings_are_not_findings(git_repo) -> None:
    repo, commit = git_repo
    _write(repo, ".gitignore", ".env.local\n.claude/settings.local.json\n")
    _write(repo, ".env.local", "X=1\n")
    _json(repo, ".claude/settings.local.json", {})
    _json(repo, ".claude/settings.json", {"permissions": {"deny": ["Read(.env.*)"]}})
    commit("init")
    assert scan_agent_harness(repo)["findings"] == []


def test_outside_git_tracking_findings_are_not_checked(tmp_path: Path) -> None:
    _write(tmp_path, ".env", "X=1\n")
    _json(tmp_path, ".claude/settings.local.json", {})
    block = scan_agent_harness(tmp_path)
    assert block["tracked_known"] is False
    assert block["findings"] == []


@pytest.mark.parametrize(("gitignore", "example", "deny", "flagged"), [
    (".env\n", False, [], True),
    ("/.env*\n", False, [], True),
    ("**/.env.local\n", False, [], True),
    ("", True, [], True),
    (".env\n", False, ["Read(./.env)"], False),
    (".env\n", False, ["Read( ./.env.* )"], False),
    (".env\n", False, ["Write(./.env)"], True),
    ("", False, [], False),
    ("# .env\n.envrc\n", False, [], False),
])
def test_missing_env_deny_matrix(tmp_path: Path, gitignore: str, example: bool,
                                 deny: list[str], flagged: bool) -> None:
    _write(tmp_path, ".gitignore", gitignore)
    if example:
        _write(tmp_path, ".env.example", "X=\n")
    _json(tmp_path, ".claude/settings.json", {"permissions": {"deny": deny}})
    got = _kinds(scan_agent_harness(tmp_path))
    assert got == ([("missing_env_deny", ".claude/settings.json")] if flagged else [])


@pytest.mark.parametrize(("marker", "flagged"), [
    (None, False), (".claude/agents/a.md", True), ("CLAUDE.md", True), ("AGENTS.md", False),
])
def test_missing_env_deny_needs_claude_code_configured(tmp_path: Path, marker: str | None,
                                                       flagged: bool) -> None:
    """A stock `.gitignore` ignores `.env`; without Claude Code there is no deny rule to add."""
    _write(tmp_path, ".gitignore", ".env\n")
    if marker:
        _write(tmp_path, marker, "# x\n")
    got = _kinds(scan_agent_harness(tmp_path))
    assert got == ([("missing_env_deny", ".claude/settings.json")] if flagged else [])


@pytest.mark.parametrize(("value", "flagged"), [(False, True), (True, False), ("false", False)])
def test_disable_all_hooks(tmp_path: Path, value: object, flagged: bool) -> None:
    _json(tmp_path, ".claude/settings.json", {"disableAllHooks": value})
    block = scan_agent_harness(tmp_path)
    assert _kinds(block) == ([("hooks_forced_on", ".claude/settings.json")] if flagged else [])
    if flagged:
        assert block["findings"][0]["line"] == 2


@pytest.mark.parametrize(("rel", "line", "flagged"), [
    (".devcontainer/devcontainer.json", '"source=${localEnv:HOME}${localEnv:USERPROFILE}/.aws,target=/x"', True),
    (".devcontainer.json", '"source=${env:HOME}/.config/gcloud,target=/x,type=bind"', True),
    (".devcontainer/docker-compose.yml", "      - ~/.kube:/root/.kube:ro", True),
    (".devcontainer/devcontainer.json", '"runArgs": ["-v", "$HOME/.netrc:/root/.netrc"]', True),
    (".devcontainer/devcontainer.json", '// "source=${localEnv:HOME}/.ssh,target=/x"', False),
    (".devcontainer/docker-compose.yml", "  # - ~/.ssh:/root/.ssh", False),
    (".devcontainer/devcontainer.json", '"source=${localEnv:HOME}/.sshd-config,target=/x"', False),
    (".devcontainer/devcontainer.json", '"source=${localWorkspaceFolder}/.aws-docs,target=/x"', False),
    ("docker-compose.yml", "      - ~/.ssh:/root/.ssh", False),
])
def test_credential_mount_matrix(tmp_path: Path, rel: str, line: str, flagged: bool) -> None:
    _write(tmp_path, rel, "{\n" + line + "\n}\n")
    block = scan_agent_harness(tmp_path)
    assert _kinds(block) == ([("credential_mount", rel)] if flagged else [])
    if flagged:
        assert block["findings"][0]["line"] == 2


@pytest.mark.parametrize("rel", [
    "AGENTS.md", ".cursorrules", ".github/copilot-instructions.md", ".claude/rules/style.md",
    ".cursor/rules/a.mdc", ".claude/agents/x.md", ".claude/commands/go.md",
    ".claude/skills/s/SKILL.md",
])
def test_hidden_unicode_locations(tmp_path: Path, rel: str) -> None:
    _write(tmp_path, rel, f"line one\nline {RLO}two{ZWSP}\n{ZWSP}\n")
    block = scan_agent_harness(tmp_path)
    assert _kinds(block) == [("hidden_unicode", rel)]
    f = block["findings"][0]
    assert f["line"] == 2
    assert f["detail"] == ("3 hidden character(s): U+200B ZERO WIDTH SPACE, "
                           "U+202E RIGHT-TO-LEFT OVERRIDE")


def test_hidden_unicode_bom_only_allowed_at_file_start(tmp_path: Path) -> None:
    _write(tmp_path, "CLAUDE.md", f"{BOM}ok\n")
    _write(tmp_path, "AGENTS.md", f"ok\nmid{BOM}file\n")
    _write(tmp_path, "README.md", f"not an instruction file{ZWSP}\n")
    assert _kinds(scan_agent_harness(tmp_path)) == [("hidden_unicode", "AGENTS.md")]


def test_hidden_unicode_in_plugin_skills_and_agents(tmp_path: Path) -> None:
    _write(tmp_path, "skills/a/SKILL.md", f"x{RLO}\n")
    _write(tmp_path, "agents/b.md", f"y{ZWSP}\n")
    assert _kinds(scan_agent_harness(tmp_path)) == []  # not a plugin repo: not loaded
    _json(tmp_path, ".claude-plugin/plugin.json", {"name": "p"})
    assert _kinds(scan_agent_harness(tmp_path)) == [
        ("hidden_unicode", "agents/b.md"), ("hidden_unicode", "skills/a/SKILL.md")]


WF_STEPS = "jobs:\n  j:\n    runs-on: ubuntu-latest\n    steps:\n      - run: {cmd}\n"


@pytest.mark.parametrize(("on", "cmd", "flagged"), [
    ("on: issue_comment\n", "terraform apply", True),
    ("on: [push, pull_request_target]\n", "kubectl apply -f k8s/", True),
    ('"on":\n  - pull_request_target\n', "helm upgrade app ./chart", True),
    ("on:\n  issue_comment:\n    types: [created]\n", "npx vercel deploy --prod", True),
    ("on:\n  pull_request_target:\n", "aws s3 sync dist s3://bucket", True),
    ("on:\n  issue_comment:\n", "npm publish", True),
    ("true:\n  issue_comment:\n", "pulumi up --yes", True),
    ("on:\n  pull_request_target:\n", "gh pr comment 1 --body ok", False),
    ("on:\n  pull_request_target:\n", "terraform plan", False),
    ("on:\n  push:\n", "terraform apply", False),
    ("on:\n  pull_request:\n", "terraform apply", False),
    ("on:\n  push:  # issue_comment later\n", "terraform apply", False),
    ("on:\n  issue_comment:\n", "echo ok  # terraform apply", False),
])
def test_privileged_comment_trigger_matrix(tmp_path: Path, on: str, cmd: str, flagged: bool) -> None:
    _write(tmp_path, ".github/workflows/w.yml", on + WF_STEPS.format(cmd=cmd))
    assert _kinds(scan_agent_harness(tmp_path)) == (
        [("privileged_comment_trigger", ".github/workflows/w.yml")] if flagged else [])


def test_privileged_uses_deploy_action_and_one_finding_per_workflow(tmp_path: Path) -> None:
    body = ("on: pull_request_target\njobs:\n  j:\n    steps:\n"
            "      - uses: aws-actions/amazon-ecs-deploy-task-definition@v2\n"
            "      - run: terraform apply\n")
    _write(tmp_path, ".github/workflows/w.yaml", body)
    block = scan_agent_harness(tmp_path)
    assert len(block["findings"]) == 1
    f = block["findings"][0]
    assert f["line"] == 5
    assert f["detail"].startswith("`pull_request_target` workflow runs `uses:")


def test_on_block_stops_at_next_top_level_key() -> None:
    assert "issue_comment" not in _on_block(["on:", "  push:", "jobs:", "  issue_comment:"])
    assert _on_block(["name: x"]) == ""


@pytest.mark.parametrize(("raw", "safe"), [
    ("https://u:p@h.example.com:8443/p?q=1#f", "https://h.example.com:8443/p"),
    ("http://h/sse", "http://h/sse"),
    ("https://[::1", "<unparseable url>"),
])
def test_safe_url(raw: str, safe: str) -> None:
    assert _safe_url(raw) == safe


def test_malformed_and_odd_shaped_json_degrade(tmp_path: Path) -> None:
    _write(tmp_path, ".claude/settings.json", "{not json")
    _write(tmp_path, ".mcp.json", "[1, 2]")
    _json(tmp_path, ".cursor/hooks.json", {"hooks": ["x"]})
    block = scan_agent_harness(tmp_path)
    assert block["executes"] == [] and block["findings"] == []
    _json(tmp_path, ".claude/settings.json", {
        "hooks": {"Stop": "x", "Pre": [1, {"hooks": [2, {"type": "agent"}]}]},
        "env": ["X"], "apiKeyHelper": 3, "permissions": {"allow": "Bash", "deny": "x"}})
    _json(tmp_path, ".mcp.json", {"mcpServers": {"a": "x", "b": {"args": []}}})
    rows = [(i["kind"], i["name"], i["detail"]) for i in scan_agent_harness(tmp_path)["executes"]]
    assert rows == [("settings_hook", "Pre", "<agent hook>"), ("mcp_server", "b", None)]


def test_long_command_is_clipped(tmp_path: Path) -> None:
    _json(tmp_path, ".claude/settings.json", {"apiKeyHelper": "./" + "x" * 300})
    detail = scan_agent_harness(tmp_path)["executes"][0]["detail"]
    assert len(detail) == 160 and detail.endswith("...")


@pytest.mark.parametrize(("command", "detail"), [
    ("./hooks/guard.sh", "./hooks/guard.sh"),
    ("curl -H 'Authorization: Bearer tok123' https://x", "curl ..."),
    ("TOKEN=tok123 DEBUG=1 ./run.sh --flag", "./run.sh ..."),
    ("'./my hook.sh'", "./my ..."),
    ("TOKEN=tok123", "<command>"),
])
def test_commands_report_the_program_never_arguments(tmp_path: Path, command: str,
                                                     detail: str) -> None:
    _json(tmp_path, ".claude/settings.json", {
        "apiKeyHelper": command,
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": command}]}]}})
    _json(tmp_path, ".mcp.json", {"mcpServers": {"s": {"command": command}}})
    _write(tmp_path, ".claude/agents/a.md",
           f"---\nname: a\nhooks:\n  Stop:\n    - hooks:\n        - command: {command}\n---\n")
    block = scan_agent_harness(tmp_path)
    assert [i["detail"] for i in block["executes"]] == [detail] * 4
    assert "tok123" not in json.dumps(block)


def test_excludes_and_scope(git_repo) -> None:
    repo, commit = git_repo
    _write(repo, "svc/CLAUDE.md", f"x{ZWSP}\n")
    _write(repo, "svc/.env", "X=1\n")
    _write(repo, "other/.env", "X=1\n")
    _write(repo, "vendor/AGENTS.md", f"x{ZWSP}\n")
    commit("init")
    scoped = scan_agent_harness(repo, repo / "svc")
    assert sorted(_kinds(scoped)) == [("hidden_unicode", "svc/CLAUDE.md"),
                                      ("tracked_env_file", "svc/.env")]
    excluded = scan_agent_harness(repo, repo / "svc", ({"svc"}, []))
    assert excluded["findings"] == []
    assert sorted(_kinds(scan_agent_harness(repo, None, (set(), [".env"])))) == []


def test_frontmatter_hooks_last_key_and_symlinked_skill(tmp_path: Path) -> None:
    _write(tmp_path, ".claude/agents/a.md",
           "---\nname: a\nhooks:\n  Stop:\n    - hooks:\n        - command: ./x.sh\n---\n")
    _write(tmp_path, ".claude/agents/b.md", "no frontmatter\n")
    rows = [(i["file"], i["line"], i["name"], i["detail"]) for i in scan_agent_harness(tmp_path)["executes"]]
    assert rows == [(".claude/agents/a.md", 6, "Stop", "./x.sh")]


def test_build_run_context_carries_the_block(git_repo) -> None:
    repo, commit = git_repo
    _write(repo, "app.py", "x = 1\n")
    _json(repo, ".mcp.json", {"mcpServers": {"gh": {"command": "gh-mcp"}}})
    commit("init")
    ctx = build_run_context(repo_root=repo, run_date="2026-10-09")
    block = ctx["agent_harness"]
    assert block["executes_by_kind"] == {"mcp_server": 1}
    assert block["findings"] == []
