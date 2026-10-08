"""Tests for lib/claude_config.py - legacy commands and ignored frontmatter.

Claude Code drops an unrecognised frontmatter field without an error, so each
test plants one kind of silently-ignored configuration and checks the scan names
it with its file, line, key, value and fix.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import lib.scan_registry as reg
from assess_core import build_run_context
from lib.claude_config import FileKind, check_file, parse_frontmatter, scan_claude_config
from lib.claude_config_fields import (
    AGENT_FIELDS,
    COMMAND_FIELDS,
    SKILL_FIELDS,
    SNAPSHOT_DATE,
    SOURCE_URLS,
)

FIXTURE = Path(__file__).parent / "fixtures" / "claude_config_plugin"


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _rows(block: dict) -> list[tuple]:
    return [(f["kind"], f["file"], f["line"], f["key"], f["value"]) for f in block["findings"]]


def _fm(*lines: str) -> str:
    return "---\n" + "\n".join(lines) + "\n---\nBody.\n"


# --- the pinned field sets ----------------------------------------------------

def test_field_sets_carry_their_source_and_snapshot_date() -> None:
    assert SNAPSHOT_DATE == "2026-10-08"
    assert all(u.startswith("https://code.claude.com/docs/") for u in SOURCE_URLS)
    assert len(SKILL_FIELDS) == 20
    assert COMMAND_FIELDS == SKILL_FIELDS - {"name", "paths"}
    assert {"maxTurns", "omitClaudeMd", "experimental", "color"} <= AGENT_FIELDS


# --- availability -------------------------------------------------------------

def test_no_claude_config_is_unavailable_not_a_clean_pass(tmp_path: Path) -> None:
    _write(tmp_path, "skills/x/SKILL.md", _fm("bogus: 1"))  # no manifest: not a plugin
    block = scan_claude_config(tmp_path)
    assert block["available"] is False
    assert "no .claude/" in block["reason"]
    assert "findings" not in block


def test_empty_claude_dir_is_available_with_nothing_scanned(tmp_path: Path) -> None:
    (tmp_path / ".claude").mkdir()
    block = scan_claude_config(tmp_path)
    assert block["available"] is True
    assert block["files_scanned"] == 0 and block["findings"] == []
    assert block["plugin_repo"] is False


# --- one test per finding kind ------------------------------------------------

def test_legacy_command_file_is_flagged_with_its_skill_target(tmp_path: Path) -> None:
    _write(tmp_path, ".claude/commands/frontend/component.md", "Do the thing.\n")
    block = scan_claude_config(tmp_path)
    [f] = block["findings"]
    assert (f["kind"], f["file"], f["line"]) == (
        "legacy_command", ".claude/commands/frontend/component.md", 1)
    assert "`.claude/skills/frontend-component/SKILL.md`" in f["fix"]


@pytest.mark.parametrize("kind,key,hint", [
    ("skill", "allowed_tools", "allowed-tools"),
    ("skill", "argument_hint", "argument-hint"),
    ("skill", "disableModelInvocation", "disable-model-invocation"),
    ("command", "allowed_tools", "allowed-tools"),
    ("agent", "max_turns", "maxTurns"),
    ("agent", "disallowed-tools", "disallowedTools"),
])
def test_misspelt_key_is_unknown_with_a_rename_hint(kind: FileKind, key: str, hint: str) -> None:
    findings = check_file("x.md", kind, "project", _fm(f"{key}: x"))
    [f] = [f for f in findings if f["kind"] == "unknown_key"]
    assert (f["line"], f["key"], f["value"]) == (2, key, "x")
    assert f"Rename `{key}` to `{hint}`" in f["fix"]


def test_field_of_the_other_kind_is_named_as_such() -> None:
    [f] = check_file("s/SKILL.md", "skill", "project", _fm("color: red"))
    assert f["kind"] == "unknown_key"
    assert "an agent field" in f["fix"]
    [g] = check_file("a.md", "agent", "project", _fm("allowed-tools: Read"))
    assert "a skill field" in g["fix"]


def test_unrelated_unknown_key_says_remove() -> None:
    [f] = check_file("s/SKILL.md", "skill", "project", _fm("frobnicate: yes"))
    assert f["kind"] == "unknown_key" and f["fix"].startswith("Remove `frobnicate`")


@pytest.mark.parametrize("kind,line,expect", [
    ("agent", "color: indigo", "Use one of: red, blue"),
    ("agent", "color: Red", "Write `red`"),
    ("skill", "effort: extreme", "Use one of: low, medium, high, xhigh, max"),
    ("agent", "effort: High", "Write `high`"),
    ("skill", "context: thread", "Use one of: fork"),
    ("skill", "shell: zsh", "Use one of: bash, powershell"),
    ("agent", "memory: global", "Use one of: user, project, local"),
    ("agent", "isolation: container", "Use one of: worktree"),
    ("skill", "model: gpt-4o", "full model ID"),
    ("agent", "model: o3", "full model ID"),
])
def test_unsupported_value(kind: FileKind, line: str, expect: str) -> None:
    [f] = check_file("f.md", kind, "project", _fm(line))
    assert f["kind"] == "unsupported_value"
    assert f["value"] == line.split(": ", 1)[1]
    assert expect in f["fix"]


@pytest.mark.parametrize("model", [
    "inherit", "sonnet", "opus", "haiku", "fable", "opus[1m]", "opusplan",
    "claude-opus-5-5", "claude-sonnet-4-5-20250929",
    "us.anthropic.claude-opus-4-1-20250805-v1:0", "claude-opus-4@20250514",
])
def test_model_accepts_aliases_inherit_and_full_ids(model: str) -> None:
    assert check_file("a.md", "agent", "project", _fm(f"model: {model}")) == []


def test_command_name_and_paths_are_unsupported(tmp_path: Path) -> None:
    _write(tmp_path, ".claude/commands/deploy.md",
           _fm("name: deploy", "description: Ship it.", "paths: src/**"))
    block = scan_claude_config(tmp_path)
    assert _rows(block) == [
        ("legacy_command", ".claude/commands/deploy.md", 1, None, None),
        ("command_unsupported_key", ".claude/commands/deploy.md", 2, "name", "deploy"),
        ("command_unsupported_key", ".claude/commands/deploy.md", 4, "paths", "src/**"),
    ]


def test_plugin_agent_ignored_fields_only_for_plugin_agents() -> None:
    text = _fm("name: a", "permissionMode: plan", "hooks:", "  PreToolUse: []",
               "mcpServers:", "  - slack", "initialPrompt: hi")
    plugin = check_file("agents/a.md", "agent", "plugin", text)
    assert [(f["kind"], f["key"], f["line"]) for f in plugin] == [
        ("plugin_agent_ignored", "permissionMode", 3),
        ("plugin_agent_ignored", "hooks", 4),
        ("plugin_agent_ignored", "mcpServers", 6),
        ("plugin_agent_ignored", "initialPrompt", 8),
    ]
    assert check_file(".claude/agents/a.md", "agent", "project", text) == []


def test_experimental_nested_keys_are_checked() -> None:
    good = _fm("experimental:", "  cacheTtl: 1h")
    assert check_file("a.md", "agent", "project", good) == []
    bad = _fm("experimental:", "  cacheTtl: 2h", "  cache_ttl: 5m")
    rows = [(f["kind"], f["key"], f["line"], f["value"])
            for f in check_file("a.md", "agent", "project", bad)]
    assert rows == [
        ("unsupported_value", "experimental.cacheTtl", 3, "2h"),
        ("unknown_key", "experimental.cache_ttl", 4, "5m"),
    ]


def test_metadata_children_and_yaml_lists_are_free_form() -> None:
    text = _fm(
        "name: s", "metadata:", "  anything: goes", "  nested:", "    deeper: ok",
        "allowed-tools:", "  - Read", "  - Grep", "arguments: [a, b]",
        "description: >", "  folded text: with a colon", "  more",
        "when_to_use: plain", "  continuation line: with colon",
    )
    assert check_file("s/SKILL.md", "skill", "project", text) == []


@pytest.mark.parametrize("rel,kind,loaded_as", [
    ("agents/README.md", "agent", "an agent named `README`"),
    ("commands/README.md", "command", "the `/README` command"),
    (".claude/commands/CHANGELOG.md", "command", "the `/CHANGELOG` command"),
])
def test_document_under_a_commands_or_agents_path_is_stray(
        rel: str, kind: FileKind, loaded_as: str) -> None:
    [f] = check_file(rel, kind, "plugin", "# Notes\n\nNot configuration.\n")
    assert (f["kind"], f["file"], f["line"]) == ("stray_markdown", rel, 1)
    assert loaded_as in f["fix"]


def test_readme_in_a_skill_folder_is_not_stray() -> None:
    assert check_file("skills/readme/SKILL.md", "skill", "plugin", "# Body\n") == []


def test_agent_without_frontmatter_is_flagged() -> None:
    [f] = check_file("agents/helper.md", "agent", "plugin", "You help.\n")
    assert (f["kind"], f["line"]) == ("missing_frontmatter", 1)
    assert "`name` and `description`" in f["fix"]


def test_skill_and_command_without_frontmatter_are_valid() -> None:
    assert check_file("skills/s/SKILL.md", "skill", "plugin", "Body\n") == []
    assert [f["kind"] for f in check_file("commands/c.md", "command", "plugin", "Body\n")] == [
        "legacy_command"]


# --- malformed frontmatter ----------------------------------------------------

def test_unclosed_frontmatter_is_reported_not_raised() -> None:
    [f] = check_file("s/SKILL.md", "skill", "project", "---\nname: x\nno close\n")
    assert (f["kind"], f["line"]) == ("malformed_frontmatter", 1)
    assert "never closes" in f["fix"]


@pytest.mark.parametrize("body,line,msg", [
    ("name: x\n\tcolor: red", 3, "tab in indentation"),
    ("name: x\njust words", 3, "not a `key: value` line"),
    ("  indented: first", 2, "indented line before any key"),
    ("name: x\n- stray", 3, "list item outside any key"),
    ("name: a\nname: b", 3, "duplicate key `name`"),
])
def test_malformed_lines_are_reported_with_their_line(body: str, line: int, msg: str) -> None:
    findings = check_file("s/SKILL.md", "skill", "project", f"---\n{body}\n---\n")
    malformed = [f for f in findings if f["kind"] == "malformed_frontmatter"]
    assert [f["line"] for f in malformed] == [line]
    assert msg in malformed[0]["fix"]


def test_no_frontmatter_is_not_malformed() -> None:
    assert parse_frontmatter("# Title\n\nBody\n") is None
    assert check_file("s/SKILL.md", "skill", "project", "# Title\n") == []
    assert check_file("s/SKILL.md", "skill", "project", "") == []


def test_quoted_values_and_comments_are_cleaned() -> None:
    fm = parse_frontmatter(_fm(
        'color: "red"', "effort: low  # cheap", "model: 'inherit'",
        'shell: "bash"  # house shell', "description: 'it''s # not a comment'"))
    assert fm is not None
    assert [(e.key, e.value) for e in fm.entries] == [
        ("color", "red"), ("effort", "low"), ("model", "inherit"), ("shell", "bash"),
        ("description", "it's # not a comment")]
    assert check_file("a.md", "agent", "project", _fm('color: "red"  # house colour')) == []


def test_malformed_manifest_falls_back_to_defaults(tmp_path: Path) -> None:
    _write(tmp_path, ".claude-plugin/plugin.json", "{not json")
    _write(tmp_path, "agents/a.md", _fm("color: indigo"))
    block = scan_claude_config(tmp_path)
    assert block["plugin_repo"] is True
    assert "could not parse plugin.json" in block["manifest_error"]
    assert _rows(block) == [("unsupported_value", "agents/a.md", 2, "color", "indigo")]


def test_non_object_manifest_is_reported(tmp_path: Path) -> None:
    _write(tmp_path, ".claude-plugin/plugin.json", "[]")
    assert "not a JSON object" in scan_claude_config(tmp_path)["manifest_error"]


# --- plugin layout and manifest path overrides --------------------------------

def test_fixture_plugin_repo_honours_manifest_overrides() -> None:
    block = scan_claude_config(FIXTURE)
    assert block["plugin_repo"] is True and block["manifest_error"] is None
    # skills adds to the default; commands and agents replace it, so the
    # default commands/ and agents/ files are never read, and a path that
    # escapes the plugin root is skipped.
    assert block["files_by_kind"] == {"skill": 2, "command": 1, "agent": 1}
    files = {f["file"] for f in block["findings"]}
    assert "commands/not-loaded.md" not in files and "agents/not-loaded.md" not in files
    assert not any(f["file"].startswith("skills/clean") for f in block["findings"])
    assert _rows(block) == [
        ("unsupported_value", "bots/helper.md", 5, "color", "indigo"),
        ("unknown_key", "bots/helper.md", 6, "max_turns", "5"),
        ("plugin_agent_ignored", "bots/helper.md", 7, "permissionMode", "plan"),
        ("unsupported_value", "bots/helper.md", 9, "experimental.cacheTtl", "2h"),
        ("legacy_command", "cmds/deploy.md", 1, None, None),
        ("command_unsupported_key", "cmds/deploy.md", 2, "name", "deploy"),
        ("unknown_key", "cmds/deploy.md", 4, "argument_hint", "[env]"),
        ("unknown_key", "extra-skills/typo/SKILL.md", 4, "allowed_tools", "Read"),
        ("unknown_key", "extra-skills/typo/SKILL.md", 5, "disableModelInvocation", "true"),
        ("unsupported_value", "extra-skills/typo/SKILL.md", 6, "effort", "High"),
    ]
    assert block["finding_count"] == 10
    assert block["counts_by_kind"] == {
        "command_unsupported_key": 1, "legacy_command": 1,
        "plugin_agent_ignored": 1, "unknown_key": 4, "unsupported_value": 3,
    }


def test_default_plugin_layout_without_overrides(tmp_path: Path) -> None:
    _write(tmp_path, ".claude-plugin/plugin.json", '{"name": "p"}')
    _write(tmp_path, "skills/s/SKILL.md", _fm("name: s"))
    _write(tmp_path, "commands/c.md", "Body\n")
    _write(tmp_path, "agents/review/sec.md", _fm("color: cyan"))
    block = scan_claude_config(tmp_path)
    assert block["files_by_kind"] == {"skill": 1, "command": 1, "agent": 1}
    assert _rows(block) == [("legacy_command", "commands/c.md", 1, None, None)]
    assert "`skills/c/SKILL.md`" in block["findings"][0]["fix"]


def test_command_name_fix_keeps_it_for_other_loaders() -> None:
    [_, f] = check_file("commands/c.md", "command", "plugin", _fm("name: c"))
    assert f["kind"] == "command_unsupported_key"
    assert "keep it only if another loader" in f["fix"]


def test_manifest_agents_directory_entry_is_not_loaded(tmp_path: Path) -> None:
    _write(tmp_path, ".claude-plugin/plugin.json", '{"name": "p", "agents": ["./bots/"]}')
    _write(tmp_path, "bots/a.md", _fm("color: teal"))
    assert scan_claude_config(tmp_path)["files_by_kind"]["agent"] == 0


def test_manifest_commands_object_map_and_single_skill_dir(tmp_path: Path) -> None:
    _write(tmp_path, ".claude-plugin/plugin.json", json.dumps({
        "name": "p",
        "skills": "./solo",
        "commands": {"status": {"source": "./docs/status.md"},
                     "about": {"content": "inline"}},
        "agents": "./a.md",
    }))
    _write(tmp_path, "solo/SKILL.md", _fm("bogus: 1"))
    _write(tmp_path, "docs/status.md", "Status\n")
    _write(tmp_path, "a.md", _fm("color: teal"))
    block = scan_claude_config(tmp_path)
    assert block["files_by_kind"] == {"skill": 1, "command": 1, "agent": 1}
    assert [r[:2] for r in _rows(block)] == [
        ("unsupported_value", "a.md"), ("legacy_command", "docs/status.md"),
        ("unknown_key", "solo/SKILL.md"),
    ]


def test_project_and_plugin_config_together_tag_their_origin(tmp_path: Path) -> None:
    _write(tmp_path, ".claude-plugin/plugin.json", '{"name": "p"}')
    _write(tmp_path, ".claude/agents/local.md", _fm("permissionMode: plan"))
    _write(tmp_path, "agents/shipped.md", _fm("permissionMode: plan"))
    block = scan_claude_config(tmp_path)
    assert _rows(block) == [("plugin_agent_ignored", "agents/shipped.md", 2,
                             "permissionMode", "plan")]


def test_project_skills_need_their_own_folder(tmp_path: Path) -> None:
    _write(tmp_path, ".claude/skills/SKILL.md", _fm("bogus: 1"))
    _write(tmp_path, ".claude/skills/real/SKILL.md", _fm("bogus: 1"))
    block = scan_claude_config(tmp_path)
    assert [f["file"] for f in block["findings"]] == [".claude/skills/real/SKILL.md"]


# --- scope and excludes -------------------------------------------------------

def test_scope_reads_the_subtree_config_with_repo_relative_paths(tmp_path: Path) -> None:
    _write(tmp_path, ".claude/agents/root.md", _fm("color: indigo"))
    _write(tmp_path, "svc/.claude/agents/svc.md", _fm("color: indigo"))
    _write(tmp_path, "other/.claude/agents/other.md", _fm("color: indigo"))
    block = scan_claude_config(tmp_path, scope=tmp_path / "svc")
    assert [f["file"] for f in block["findings"]] == ["svc/.claude/agents/svc.md"]
    assert scan_claude_config(tmp_path, scope=tmp_path / "other" / ".claude" / "agents")[
        "available"] is False


def test_config_excludes_apply(tmp_path: Path) -> None:
    _write(tmp_path, ".claude/agents/keep.md", _fm("color: indigo"))
    _write(tmp_path, ".claude/agents/vendor/drop.md", _fm("color: indigo"))
    _write(tmp_path, ".claude/agents/skip-me.md", _fm("color: indigo"))
    block = scan_claude_config(tmp_path, excludes=({"vendor"}, ["skip-*.md"]))
    assert [f["file"] for f in block["findings"]] == [".claude/agents/keep.md"]
    assert block["files_scanned"] == 1


def test_unreadable_file_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, ".claude/agents/a.md", _fm("name: a"))

    def boom(self: Path, *a: object, **k: object) -> str:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "read_text", boom)
    [f] = scan_claude_config(tmp_path)["findings"]
    assert f["kind"] == "unreadable_file" and "Permission denied" in f["fix"]


# --- run-context wiring -------------------------------------------------------

def _run_context_without(repo: Path, drop: bool) -> dict:
    shutil.rmtree(repo / ".assess", ignore_errors=True)
    specs = tuple(s for s in reg.SCANS if not (drop and s.key == "claude_config"))
    original = reg.SCANS
    reg.SCANS = specs
    try:
        build_run_context(repo_root=repo, run_date="2026-10-08")
    finally:
        reg.SCANS = original
    return json.loads((repo / ".assess" / "run-context.json").read_text(encoding="utf-8"))


def test_unconfigured_repo_run_context_is_unchanged_but_for_the_block(git_repo) -> None:
    """A repo with no Claude Code config: byte-identical apart from the block."""
    repo, commit = git_repo
    _write(repo, "README.md", "# Repo\n")
    _write(repo, "app.py", "def f():\n    return 1\n")
    commit("init")
    without = _run_context_without(repo, drop=True)
    with_block = _run_context_without(repo, drop=False)
    assert with_block["claude_config"] == {
        "available": False,
        "reason": ("no Claude Code configuration: no .claude/ directory and no "
                   ".claude-plugin/plugin.json"),
        "snapshot_date": SNAPSHOT_DATE,
    }
    assert "claude_config" not in without
    for ctx in (without, with_block):
        ctx.pop("run_id")
        ctx.pop("claude_config", None)
    assert json.dumps(without, indent=2) == json.dumps(with_block, indent=2)


def test_build_run_context_carries_the_block_for_a_scoped_run(git_repo) -> None:
    repo, commit = git_repo
    _write(repo, "svc/.claude/agents/a.md", _fm("color: indigo"))
    _write(repo, "svc/app.py", "x = 1\n")
    commit("init")
    ctx = build_run_context(repo_root=repo, run_date="2026-10-08", scope=Path("svc"))
    block = ctx["claude_config"]
    assert block["available"] is True
    assert _rows(block) == [("unsupported_value", "svc/.claude/agents/a.md", 2, "color", "indigo")]
