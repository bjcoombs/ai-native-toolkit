"""Tests for lib/agent_systems.py and lib/agent_consistency.py (issue #768): every
agent system's instruction files are discovered with their scope, and the root
files are compared for divergent check commands and thresholds and for a link
to one canonical file."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path, PurePosixPath

import pytest

from lib.agent_consistency import (
    check_commands,
    pointer_line,
    scan_agent_consistency,
    thresholds,
)
from lib.agent_systems import SYSTEMS, aider_read_files, match_system_file, might_match
from lib.counts_only.content_free import counts_only_scans
from lib.instruction_files import nested_instruction_surface
from lib.levels import evaluate_row
from lib.level_signals import load_default_levels

AGENTS = "# Agents\n\nRun `make test` before every commit.\n"


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                   text=True, encoding="utf-8", errors="replace")


def _commit(root: Path) -> None:
    _git(root, "init", "-qb", "main")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "one")


def _repo(tmp_path: Path, files: dict[str, str], links: dict[str, str] | None = None) -> Path:
    root = tmp_path / "r"
    root.mkdir(parents=True)
    for rel, text in files.items():
        _write(root, rel, text)
    for rel, target in (links or {}).items():
        os.symlink(target, root / rel)
    _commit(root)
    return root


def _row(block: dict[str, object]) -> dict[str, object]:
    row = next(r for r in load_default_levels()["rows"] if r["id"].startswith("agent-consistency"))
    return dict(evaluate_row(row, {"agent_consistency": block}))


def test_divergent_files_report_each_command_and_the_pointer_fix(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"AGENTS.md": AGENTS,
                            ".windsurfrules": "Run `npm test` before every commit.\n",
                            ".clinerules": "Run `pytest` before every commit.\n"})
    block = scan_agent_consistency(root)
    assert block["systems"] == ["agents_md", "windsurf", "cline"]
    assert block["canonical"] == "AGENTS.md"
    assert {d["value"]: d["missing_from"] for d in block["divergences"]} == {
        "make test": ["windsurf", "cline"], "npm test": ["agents_md", "cline"],
        "pytest": ["agents_md", "windsurf"]}
    assert (block["conflicts"], block["unlinked"], block["consistent"]) == (3, 2, False)
    assert [(s["system"], s["pointer"], s["symlink"]) for s in block["next_steps"]] == [
        ("windsurf", "Follow the project instructions in [AGENTS.md](AGENTS.md).",
         "ln -sf AGENTS.md .windsurfrules"),
        ("cline", "Follow the project instructions in [AGENTS.md](AGENTS.md).",
         "ln -sf AGENTS.md .clinerules")]
    assert _row(dict(block))["level"] == 0


def test_symlinked_files_are_consistent_and_reach_level_2(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"AGENTS.md": AGENTS},
                 {".windsurfrules": "AGENTS.md", ".clinerules": "AGENTS.md"})
    block = scan_agent_consistency(root)
    assert {f["path"]: f["link"] for f in block["files"]} == {
        "AGENTS.md": "canonical", ".windsurfrules": "symlink", ".clinerules": "symlink"}
    assert (block["conflicts"], block["unlinked"], block["consistent"]) == (0, 0, True)
    assert block["next_steps"] == []
    assert _row(dict(block))["level"] == 2
    # Discovery lists each symlink under its own name, with its system.
    files = nested_instruction_surface(root)["files"]
    assert {f["path"]: f["tool"] for f in files} == {
        ".clinerules": "cline", ".windsurfrules": "windsurf"}


def test_a_single_system_is_not_applicable_and_the_row_unknown(tmp_path: Path) -> None:
    block = scan_agent_consistency(_repo(tmp_path, {"AGENTS.md": AGENTS}))
    assert block["applicable"] is False
    assert (block["conflicts"], block["unlinked"], block["consistent"]) == (None, None, None)
    assert _row(dict(block))["status"] == "unknown"


@pytest.mark.parametrize("path", [".windsurfrules", ".clinerules", ".rules", "CLAUDE.md",
                                  "GEMINI.md", ".kiro/steering/main.md", ".cursorrules",
                                  ".github/copilot-instructions.md", ".junie/guidelines.md",
                                  ".amazonq/rules/team.md", ".continue/rules/base.md",
                                  ".windsurf/rules/base.md", ".clinerules/base.md"])
def test_each_pointer_line_round_trips_to_consistent(tmp_path: Path, path: str) -> None:
    root = _repo(tmp_path, {"AGENTS.md": AGENTS, path: "Run `npm test` first.\n"})
    before = scan_agent_consistency(root)
    assert before["unlinked"] == 1 and before["conflicts"] == 2
    step = before["next_steps"][0]
    _write(root, path, step["pointer"] + "\n")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "two")
    after = scan_agent_consistency(root)
    assert (after["conflicts"], after["unlinked"], after["consistent"]) == (0, 0, True)


def test_aider_counts_only_when_its_config_reads_a_file(tmp_path: Path) -> None:
    files = {"AGENTS.md": AGENTS, "CONVENTIONS.md": "Run `pytest`.\n"}
    unconfigured = scan_agent_consistency(_repo(tmp_path / "a", files))
    assert unconfigured["systems"] == ["agents_md"]
    configured = scan_agent_consistency(_repo(
        tmp_path / "b", {**files, ".aider.conf.yml": "read: CONVENTIONS.md\n"}))
    assert configured["systems"] == ["agents_md", "aider"]
    step = configured["next_steps"][0]
    assert (step["path"], step["pointer"], step["symlink"]) == (
        ".aider.conf.yml", "read: AGENTS.md", None)
    linked = scan_agent_consistency(_repo(
        tmp_path / "c", {"AGENTS.md": AGENTS, "CLAUDE.md": "@AGENTS.md\n",
                         ".aider.conf.yml": "read:\n  - AGENTS.md\n"}))
    assert {f["path"]: f["link"] for f in linked["files"]}[".aider.conf.yml"] == "config_read"
    assert linked["consistent"] is True


def test_import_pointer_and_mirror_links(tmp_path: Path) -> None:
    root = _repo(tmp_path, {
        "AGENTS.md": AGENTS,
        "CLAUDE.md": "# Claude\n@AGENTS.md\n",
        "GEMINI.md": "# Agents\n\nRun `make test` before every commit.\n",
        ".kiro/steering/main.md": "#[[file:AGENTS.md]]\n"})
    block = scan_agent_consistency(root)
    assert {f["path"]: f["link"] for f in block["files"]} == {
        "AGENTS.md": "canonical", "CLAUDE.md": "import", "GEMINI.md": "mirror",
        ".kiro/steering/main.md": "pointer"}
    assert block["conflicts"] == 0
    assert block["unlinked"] == 1  # the hand-kept GEMINI.md copy can drift
    assert block["next_steps"][0]["pointer"] == "@AGENTS.md"


def test_conflicting_thresholds_and_a_canonical_without_agents_md(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"CLAUDE.md": "Keep coverage at 90 percent.\n",
                            ".cursorrules": "Keep coverage at 80 percent.\n"})
    block = scan_agent_consistency(root)
    assert block["canonical"] == "CLAUDE.md"
    assert block["divergences"] == [{
        "kind": "threshold", "value": "coverage", "named_in": ["claude_code", "cursor"],
        "missing_from": [], "stated": [{"system": "claude_code", "number": 90.0},
                                       {"system": "cursor", "number": 80.0}]}]
    assert block["next_steps"][0]["pointer"] == (
        "Follow the project instructions in [CLAUDE.md](CLAUDE.md).")


def test_scoped_rules_and_nested_files_stay_out_of_the_comparison(tmp_path: Path) -> None:
    root = _repo(tmp_path, {
        "AGENTS.md": AGENTS,
        "src/a.py": "x = 1\n",
        ".kiro/steering/py.md": "---\ninclusion: fileMatch\nfileMatchPattern: 'src/**'\n---\n"
                                "Run `pytest`.\n",
        ".continue/rules/py.md": "---\nglobs: src/**\n---\nRun `pytest`.\n",
        "pkg/CLAUDE.md": "Run `pytest`.\n"})
    block = scan_agent_consistency(root)
    assert block["systems"] == ["agents_md"]
    globs = {f["path"]: f["scope"] for f in nested_instruction_surface(root)["files"]}
    assert globs[".kiro/steering/py.md"]["globs"] == ["src/**"]
    assert globs[".continue/rules/py.md"]["always_loaded"] is False


def test_match_system_file_scoping_rules() -> None:
    def match(path: str, text: str = "x\n"):  # type: ignore[no-untyped-def]
        return match_system_file(PurePosixPath(path), text)

    assert match(".windsurf/rules/a.md", "---\ntrigger: glob\nglobs: '*.py'\n---\nx\n").globs == ["*.py"]
    assert match(".windsurf/rules/a.md", "---\ntrigger: manual\n---\nx\n").always_loaded is False
    assert match(".clinerules/a.md", "---\npaths: ['src/**']\n---\nx\n").always_loaded is False
    assert match(".continue/rules/a.md", "---\nalwaysApply: true\nglobs: x\n---\nx\n").always_loaded
    assert match(".kiro/steering/a.md", "---\ninclusion: manual\n---\nx\n").always_loaded is False
    assert match("CONVENTIONS.md").always_loaded is False
    assert match_system_file(PurePosixPath("CONVENTIONS.md"), "x", frozenset({"CONVENTIONS.md"})).always_loaded
    assert match("docs/.rules") is None and match("README.md") is None
    assert match(".github/workflows/ci.yml") is None
    assert not might_match(PurePosixPath(".github/workflows/ci.yml"))
    assert might_match(PurePosixPath(".github/copilot-instructions.md"))
    assert {s.id for s in SYSTEMS} >= {"windsurf", "cline", "aider", "junie", "amazon_q",
                                       "kiro", "continue", "zed"}


def test_aider_read_forms() -> None:
    assert aider_read_files("read: ./CONVENTIONS.md  # notes\n") == {"CONVENTIONS.md"}
    assert aider_read_files("read: [a.md, 'b.md']\n") == {"a.md", "b.md"}
    assert aider_read_files("model: x\nread:\n  - a.md\n  - \"b.md\"\nauto: true\n") == {"a.md", "b.md"}
    assert aider_read_files("model: x\n") == frozenset()
    assert aider_read_files("read:\n- CONVENTIONS.md\n- AGENTS.md\nmodel: x\n") == {
        "CONVENTIONS.md", "AGENTS.md"}


def test_command_and_threshold_readers() -> None:
    text = "Run `pytest` and `ruff`.\n```bash\nmake lint\n```\nSee `README` and `npm install`.\n"
    assert check_commands(text) == {"pytest", "ruff", "make lint"}
    assert check_commands("```\n`pytest`\n```\n") == set()
    assert thresholds("Complexity stays under 15.\nLine length 100.\nCoverage is good.\n") == {
        "complexity": {15.0}, "line_length": {100.0}}
    assert pointer_line("claude_code", ".claude/CLAUDE.md", "AGENTS.md") == "@../AGENTS.md"
    assert pointer_line("unknown", "x/y.md", "AGENTS.md") == (
        "Follow the project instructions in [AGENTS.md](../AGENTS.md).")


def test_counts_only_keeps_the_counts_and_drops_the_text(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"AGENTS.md": AGENTS,
                            ".windsurfrules": "Run `npm test` before every commit.\n"})
    kept = counts_only_scans({"agent_consistency": dict(scan_agent_consistency(root))})
    block = kept["agent_consistency"]
    assert (block["conflicts"], block["unlinked"], block["systems"]) == (
        2, 1, ["agents_md", "windsurf"])
    assert (block["systems_total"], block["applicable"], block["consistent"]) == (2, True, False)
    assert [f["link"] for f in block["files"]] == ["independent", "canonical"]
    assert block["next_steps"][0]["system"] == "windsurf"
    assert block["divergences"][0]["missing_from"] == ["windsurf"]
    text = json.dumps(block)
    assert "npm test" not in text and "make test" not in text
    assert "Follow the project" not in text and "ln -sf" not in text


def test_one_linked_file_links_a_multi_file_system(tmp_path: Path) -> None:
    root = _repo(tmp_path, {
        "AGENTS.md": AGENTS,
        ".github/copilot-instructions.md": "See [AGENTS.md](../AGENTS.md).\n",
        ".github/instructions/all.instructions.md": "---\napplyTo: '**'\n---\nRun `make test`.\n"})
    block = scan_agent_consistency(root)
    assert (block["unlinked"], block["conflicts"], block["next_steps"]) == (0, 0, [])


def test_a_supplementary_rule_file_gets_a_pointer_but_no_symlink(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"AGENTS.md": AGENTS, ".amazonq/rules/team.md": "Run `make test`.\n"})
    step = scan_agent_consistency(root)["next_steps"][0]
    assert (step["path"], step["symlink"]) == (".amazonq/rules/team.md", None)
    assert step["pointer"] == "Follow the project instructions in [AGENTS.md](../../AGENTS.md)."


def test_aider_reading_conventions_and_agents_md_is_linked(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"AGENTS.md": AGENTS, "CONVENTIONS.md": "Keep it short.\n",
                            ".aider.conf.yml": "read: [CONVENTIONS.md, AGENTS.md]\n"})
    block = scan_agent_consistency(root)
    assert {f["path"]: f["link"] for f in block["files"]}[".aider.conf.yml"] == "config_read"
    assert (block["unlinked"], block["conflicts"], block["next_steps"]) == (0, 0, [])


def test_a_stray_number_beside_the_agreed_threshold_is_no_conflict(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"AGENTS.md": "Keep coverage at 90 (see issue 412).\n",
                            "CLAUDE.md": "Coverage stays at 90.\n"})
    assert scan_agent_consistency(root)["conflicts"] == 0


def test_a_prose_mention_is_no_link_for_claude_code(tmp_path: Path) -> None:
    root = _repo(tmp_path, {
        "AGENTS.md": "Run `make test` before pushing.\nCoverage floor 90.\n",
        "CLAUDE.md": "Run `npm test` before pushing.\nCoverage floor 70.\n"
                     "Codex reads AGENTS.md instead of this file.\n"})
    block = scan_agent_consistency(root)
    assert {f["path"]: f["link"] for f in block["files"]}["CLAUDE.md"] == "independent"
    assert block["unlinked"] == 1
    kinds = {(d["kind"], d["value"]) for d in block["divergences"]}
    assert kinds == {("command", "make test"), ("command", "npm test"), ("threshold", "coverage")}
    assert block["next_steps"][0]["pointer"] == "@AGENTS.md"
    assert any(f["kind"] == "claude_shadows_agents"
               for f in nested_instruction_surface(root)["findings"])


def test_a_prose_mention_still_points_a_text_system(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"AGENTS.md": AGENTS,
                            ".windsurfrules": "Codex and Windsurf read AGENTS.md.\n"})
    block = scan_agent_consistency(root)
    assert {f["path"]: f["link"] for f in block["files"]}[".windsurfrules"] == "pointer"
    assert block["consistent"] is True


def test_the_pointer_step_names_the_primary_file_over_a_rule_file(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"AGENTS.md": AGENTS,
                            ".claude/rules/style.md": "Keep functions short.\n",
                            "CLAUDE.md": "Run `npm test` before pushing.\n"})
    step = scan_agent_consistency(root)["next_steps"][0]
    assert (step["system"], step["path"], step["pointer"], step["symlink"]) == (
        "claude_code", "CLAUDE.md", "@AGENTS.md", "ln -sf AGENTS.md CLAUDE.md")


def test_a_system_with_only_rule_files_still_gets_a_rule_file_step(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"AGENTS.md": AGENTS,
                            ".claude/rules/style.md": "Keep functions short.\n"})
    step = scan_agent_consistency(root)["next_steps"][0]
    assert (step["path"], step["pointer"], step["symlink"]) == (
        ".claude/rules/style.md", "@../../AGENTS.md", None)


def test_a_heading_ordinal_is_no_threshold(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"AGENTS.md": "## 2. Coverage\n\nKeep it high.\n",
                            "CLAUDE.md": "Coverage stays at 90.\n"})
    assert scan_agent_consistency(root)["conflicts"] == 0


def test_heading_and_list_ordinals_are_stripped_before_numbers() -> None:
    assert thresholds("## 2. Coverage\n") == {}
    assert thresholds("### 3.1 Coverage floor 90\n") == {"coverage": {90.0}}
    assert thresholds("1. Coverage at 85\n2) Complexity under 15\n") == {
        "coverage": {85.0}, "complexity": {15.0}}
    assert thresholds("Coverage 2. Keep 90.\n") == {"coverage": {2.0, 90.0}}
    assert thresholds("## 90 percent coverage\n") == {"coverage": {90.0}}
    assert thresholds("# 100 line length\n") == {"line_length": {100.0}}
    # A dotted heading number reads as a section number, never a threshold:
    # `### 3.1 Coverage` is the common form, so a heading that opens with a
    # decimal threshold (`## 85.5 coverage`) states none, by choice.
    assert thresholds("## 85.5 coverage\n") == {}
