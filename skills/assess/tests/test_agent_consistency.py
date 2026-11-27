"""Tests for lib/instructions/agent_systems.py and lib/instructions/agent_consistency.py (issue #768): every
agent system's instruction files are discovered with their scope, and the root
files are compared for divergent check commands and thresholds and for a link
to one canonical file."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path, PurePosixPath

import pytest

from lib.instructions.agent_consistency import (
    NOT_APPLICABLE_REASON,
    check_commands,
    pointer_line,
    scan_agent_consistency,
    thresholds,
)
from lib.instructions.agent_systems import SYSTEMS, aider_read_files, match_system_file, might_match
from lib.counts_only.content_free import counts_only_scans
from lib.instructions.instruction_discovery import delegation_target
from lib.instructions.instruction_files import nested_instruction_surface
from lib.scoring.level_tally import default_model, level_result
from lib.scoring.levels import evaluate_row
from lib.scoring.level_signals import load_default_levels

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


def test_a_single_system_reads_not_applicable_with_its_reason(tmp_path: Path) -> None:
    """#1407: one instruction file has nothing to agree with or link to, so both conditions read
    not applicable with the reason naming why, never not measured, and neither is counted."""
    block = scan_agent_consistency(_repo(tmp_path, {"AGENTS.md": AGENTS}))
    assert (block.get("conflicts_reason"), block.get("unlinked_reason")) == (
        NOT_APPLICABLE_REASON, NOT_APPLICABLE_REASON)
    result = level_result(default_model(), {"agent_consistency": dict(block)})
    conds = {c["id"]: c for c in result["conditions"] if c["row"] == "agent-consistency"}
    for cid in ("agent-consistency.1", "agent-consistency.2"):
        assert (conds[cid]["state"], conds[cid].get("reason")) == ("not_applicable", "fewer_than_two_systems")
        assert conds[cid]["counted"] is False and "disposition" not in conds[cid]
    assert not any(n["id"].startswith("agent-consistency") for n in result["not_measured"])
    # The reason tokens survive counts-only output, so /fleet reads the same state.
    kept = counts_only_scans({"agent_consistency": dict(block)})["agent_consistency"]
    assert (kept["conflicts_reason"], kept["unlinked_reason"]) == (
        NOT_APPLICABLE_REASON, NOT_APPLICABLE_REASON)


def test_two_systems_carry_no_not_applicable_reason(tmp_path: Path) -> None:
    block = scan_agent_consistency(_repo(tmp_path, {"AGENTS.md": AGENTS, "CLAUDE.md": "@AGENTS.md\n"}))
    assert block["applicable"] is True
    assert "conflicts_reason" not in block and "unlinked_reason" not in block


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


CLAUDE_RULES = "# Rules\n\nRun `make check` before pushing.\nRun `make lint` after an edit.\n"


def _shadows(root: Path) -> list[str]:
    return [f["kind"] for f in nested_instruction_surface(root)["findings"]
            if f["kind"] == "claude_shadows_agents"]


def test_a_one_line_agents_md_delegates_to_claude_md(tmp_path: Path) -> None:
    root = _repo(tmp_path, {
        "AGENTS.md": "The instructions for this repository live in [CLAUDE.md](CLAUDE.md).\n",
        "CLAUDE.md": CLAUDE_RULES})
    block = scan_agent_consistency(root)
    assert block["canonical"] == "CLAUDE.md"
    assert {f["path"]: f["link"] for f in block["files"]} == {
        "AGENTS.md": "pointer", "CLAUDE.md": "canonical"}
    assert (block["conflicts"], block["unlinked"], block["consistent"]) == (0, 0, True)
    assert block["next_steps"] == []
    assert _shadows(root) == []


def test_a_one_line_claude_md_delegates_to_agents_md(tmp_path: Path) -> None:
    root = _repo(tmp_path, {
        "AGENTS.md": AGENTS,
        "CLAUDE.md": "# Claude\n\nFollow the project instructions in [AGENTS.md](AGENTS.md).\n"})
    block = scan_agent_consistency(root)
    assert block["canonical"] == "AGENTS.md"
    assert {f["path"]: f["link"] for f in block["files"]}["CLAUDE.md"] == "pointer"
    assert (block["conflicts"], block["unlinked"], block["consistent"]) == (0, 0, True)
    assert _shadows(root) == []


def test_a_pointer_with_other_instructions_stays_a_second_file(tmp_path: Path) -> None:
    root = _repo(tmp_path, {
        "AGENTS.md": "The instructions live in [CLAUDE.md](CLAUDE.md).\n"
                     "Run `npm test` before pushing.\n",
        "CLAUDE.md": CLAUDE_RULES})
    block = scan_agent_consistency(root)
    assert block["canonical"] == "AGENTS.md"
    assert {f["path"]: f["link"] for f in block["files"]}["CLAUDE.md"] == "independent"
    assert block["conflicts"] == 3
    assert _shadows(root) == ["claude_shadows_agents"]


@pytest.mark.parametrize(("body", "names", "target"), [
    # PR #1503's line, and each allowed form.
    ("The instructions for this repository live in [CLAUDE.md](CLAUDE.md).\n",
     ["CLAUDE.md"], "CLAUDE.md"),
    ("# Agents\n\nSee `CLAUDE.md`.\n", ["CLAUDE.md"], "CLAUDE.md"),
    ("read CLAUDE.md\n", ["CLAUDE.md"], "CLAUDE.md"),
    ("Follow [`AGENTS.md`](AGENTS.md).\n", ["CLAUDE.md", "AGENTS.md"], "AGENTS.md"),
    ("Instructions  live in CLAUDE.md.\n", ["CLAUDE.md"], "CLAUDE.md"),
    ("Follow the project instructions in [AGENTS.md](AGENTS.md).\n", ["AGENTS.md"],
     "AGENTS.md"),
    # The sibling link may name the file as ./name (#1507).
    ("See [CLAUDE.md](./CLAUDE.md).\n", ["CLAUDE.md"], "CLAUDE.md"),
    ("The instructions for this repository live in [`CLAUDE.md`](./CLAUDE.md).\n",
     ["CLAUDE.md"], "CLAUDE.md"),
    # Anything else is no pointer.
    ("\n@AGENTS.md\n", ["AGENTS.md"], None),  # an import, read by follow_imports
    ("Read ./CLAUDE.md first\n", ["CLAUDE.md"], None),
    ("Read CLAUDE.md. Never push to main.\n", ["CLAUDE.md"], None),
    ("Read CLAUDE.md and run `make test`.\n", ["CLAUDE.md"], None),
    ("See CLAUDE.md\nRun the tests.\n", ["CLAUDE.md"], None),
    ("<!-- run npm test -->\nSee CLAUDE.md\n", ["CLAUDE.md"], None),
    ("See CLAUDE.md <!-- run npm test -->\n", ["CLAUDE.md"], None),
    ("See CLAUDE.md\n", [], None),
    ("Nothing to see here.\n", ["CLAUDE.md"], None),
    ("Instructions: [here](docs/CLAUDE.md).\n", ["CLAUDE.md"], None),
    ("See [CLAUDE.md](docs/CLAUDE.md).\n", ["CLAUDE.md"], None),
    ("See ../CLAUDE.md\n", ["CLAUDE.md"], None),
    ("See ./CLAUDE.md.\n", ["CLAUDE.md"], None),
    ("See [./CLAUDE.md](./CLAUDE.md).\n", ["CLAUDE.md"], None),
    ("See [CLAUDE.md](.//CLAUDE.md).\n", ["CLAUDE.md"], None),
    ("See [CLAUDE.md](../CLAUDE.md).\n", ["CLAUDE.md"], None),
    ("See [CLAUDE.md](./docs/CLAUDE.md).\n", ["CLAUDE.md"], None),
    ("Do not edit CLAUDE.md by hand.\n", ["CLAUDE.md"], None),
    ("Don\u2019t follow CLAUDE.md\n", ["CLAUDE.md"], None),
    ("CLAUDE.md isn't maintained\n", ["CLAUDE.md"], None),
    ("Won't use CLAUDE.md\n", ["CLAUDE.md"], None),
    ("Ignore CLAUDE.md\n", ["CLAUDE.md"], None),
    ("CLAUDE.md is outdated\n", ["CLAUDE.md"], None),
    ("Use Python 3.12 (see CLAUDE.md)\n", ["CLAUDE.md"], None),
])
def test_delegation_target_reads_only_a_bare_pointer(
        body: str, names: list[str], target: str | None) -> None:
    assert delegation_target(body, names) == target


def test_a_pointer_cycle_is_no_delegation(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"AGENTS.md": "See CLAUDE.md.\n", "CLAUDE.md": "See AGENTS.md.\n"})
    block = scan_agent_consistency(root)
    assert block["canonical"] == "AGENTS.md"
    assert {f["path"]: f["link"] for f in block["files"]}["CLAUDE.md"] == "independent"
    assert (block["unlinked"], block["consistent"]) == (1, False)
    assert _shadows(root) == ["claude_shadows_agents"]


def test_a_pointer_claude_md_leaves_another_claude_md_compared(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"AGENTS.md": AGENTS, "CLAUDE.md": "See AGENTS.md\n",
                            ".claude/CLAUDE.md": "Run `npm test` before pushing.\n"})
    findings = [f for f in nested_instruction_surface(root)["findings"]
                if f["kind"] == "claude_shadows_agents"]
    assert [(f["path"], f["parent"]) for f in findings] == [(".claude/CLAUDE.md", "AGENTS.md")]


def test_a_pointer_whose_target_imports_it_back_stays_canonical(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"AGENTS.md": "See CLAUDE.md.\n", "CLAUDE.md": "@AGENTS.md\n"})
    block = scan_agent_consistency(root)
    assert block["canonical"] == "AGENTS.md"
    assert {f["path"]: f["link"] for f in block["files"]}["CLAUDE.md"] == "import"


def test_a_pointer_to_a_nested_path_is_no_delegation(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"AGENTS.md": "See .claude/CLAUDE.md.\n",
                            ".claude/CLAUDE.md": CLAUDE_RULES})
    assert scan_agent_consistency(root)["canonical"] == "AGENTS.md"


def test_tools_linked_to_the_pointer_agents_md_stay_linked(tmp_path: Path) -> None:
    root = _repo(tmp_path, {
        "AGENTS.md": "The instructions for this repository live in [CLAUDE.md](CLAUDE.md).\n",
        "CLAUDE.md": CLAUDE_RULES, "GEMINI.md": "@AGENTS.md\n",
        ".cursorrules": "Follow AGENTS.md\n"})
    block = scan_agent_consistency(root)
    assert block["canonical"] == "CLAUDE.md"
    assert {f["path"]: f["link"] for f in block["files"]} == {
        "AGENTS.md": "pointer", "CLAUDE.md": "canonical", "GEMINI.md": "import",
        ".cursorrules": "pointer"}
    assert (block["conflicts"], block["unlinked"], block["consistent"]) == (0, 0, True)
    assert block["next_steps"] == []


def test_a_pointer_to_its_own_symlink_is_no_delegation(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"AGENTS.md": "See CLAUDE.md.\n"}, {"CLAUDE.md": "AGENTS.md"})
    assert scan_agent_consistency(root)["canonical"] == "AGENTS.md"


def _links(block: dict[str, object]) -> dict[str, str]:
    files = block["files"]
    assert isinstance(files, list)
    return {f["path"]: f["link"] for f in files}


def test_a_pointer_chain_makes_the_file_with_content_canonical(tmp_path: Path) -> None:
    # AGENTS.md -> CLAUDE.md -> GEMINI.md: the rules live at the end of the chain (#1507).
    root = _repo(tmp_path, {"AGENTS.md": "See CLAUDE.md.\n", "CLAUDE.md": "See GEMINI.md.\n",
                            "GEMINI.md": CLAUDE_RULES})
    block = scan_agent_consistency(root)
    assert block["canonical"] == "GEMINI.md"
    assert _links(dict(block)) == {
        "AGENTS.md": "pointer", "CLAUDE.md": "pointer", "GEMINI.md": "canonical"}
    assert (block["conflicts"], block["unlinked"], block["consistent"]) == (0, 0, True)
    assert block["next_steps"] == []


def test_a_third_tool_linked_to_any_file_in_the_chain_stays_linked(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"AGENTS.md": "See CLAUDE.md.\n", "CLAUDE.md": "See GEMINI.md.\n",
                            "GEMINI.md": CLAUDE_RULES, ".windsurfrules": "Follow AGENTS.md\n",
                            ".clinerules": "Follow CLAUDE.md\n"})
    block = scan_agent_consistency(root)
    assert block["canonical"] == "GEMINI.md"
    assert _links(dict(block))[".windsurfrules"] == "pointer"
    assert _links(dict(block))[".clinerules"] == "pointer"
    assert block["unlinked"] == 0


@pytest.mark.parametrize("gemini", [
    "See CLAUDE.md.\n",   # points back into the chain
    "See AGENTS.md.\n",   # points back to the start
    "@AGENTS.md\n",       # imports the start back
    "@CLAUDE.md\n",       # imports a link of the chain back
    "See GEMINI.md.\n",   # points at itself
    "@CLAUDE.md\nRun `make test`.\n",  # content, but imports the chain back (as #1505)
])
def test_a_chain_that_loops_back_is_no_delegation(tmp_path: Path, gemini: str) -> None:
    root = _repo(tmp_path, {"AGENTS.md": "See CLAUDE.md.\n", "CLAUDE.md": "See GEMINI.md.\n",
                            "GEMINI.md": gemini})
    assert scan_agent_consistency(root)["canonical"] == "AGENTS.md"


def test_a_chain_through_a_symlink_back_is_no_delegation(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"AGENTS.md": "See CLAUDE.md.\n", "CLAUDE.md": "See GEMINI.md.\n"},
                 {"GEMINI.md": "AGENTS.md"})
    assert scan_agent_consistency(root)["canonical"] == "AGENTS.md"


def test_a_dot_slash_link_delegates(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"AGENTS.md": "See [CLAUDE.md](./CLAUDE.md).\n",
                            "CLAUDE.md": CLAUDE_RULES})
    block = scan_agent_consistency(root)
    assert block["canonical"] == "CLAUDE.md"
    assert _links(dict(block))["AGENTS.md"] == "pointer"


def _prose(root: Path) -> list[dict[str, object]]:
    return [dict(f) for f in nested_instruction_surface(root)["findings"]
            if f["kind"] == "claude_prose_pointer"]


def test_a_prose_claude_md_pointer_reads_linked_and_recommends_the_import(
        tmp_path: Path) -> None:
    root = _repo(tmp_path, {"AGENTS.md": AGENTS, "CLAUDE.md": "See AGENTS.md.\n"})
    block = scan_agent_consistency(root)
    assert _links(dict(block))["CLAUDE.md"] == "pointer"
    # Informational only: the finding moves neither `consistent` nor the row (#1507).
    assert (block["conflicts"], block["unlinked"], block["consistent"]) == (0, 0, True)
    assert _row(dict(block))["level"] == 2
    found = _prose(root)
    assert [(f["path"], f["parent"]) for f in found] == [("CLAUDE.md", "AGENTS.md")]
    assert "`@AGENTS.md`" in str(found[0]["detail"])
    assert _shadows(root) == []


def test_a_nested_prose_pointer_recommends_the_sibling_import(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"AGENTS.md": AGENTS, "pkg/AGENTS.md": AGENTS,
                            "pkg/CLAUDE.md": "Follow [AGENTS.md](./AGENTS.md).\n"})
    assert [(f["path"], f["parent"]) for f in _prose(root)] == [
        ("pkg/CLAUDE.md", "pkg/AGENTS.md")]


@pytest.mark.parametrize("claude", [
    "@AGENTS.md\n",                       # the import Claude Code loads
    "See AGENTS.md.\nRun the tests.\n",  # not a bare pointer
])
def test_no_prose_pointer_finding_without_a_bare_prose_pointer(
        tmp_path: Path, claude: str) -> None:
    root = _repo(tmp_path, {"AGENTS.md": AGENTS, "CLAUDE.md": claude})
    assert _prose(root) == []


def test_no_prose_pointer_finding_for_a_cycle_or_a_symlink(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"AGENTS.md": "See CLAUDE.md.\n", "CLAUDE.md": "See AGENTS.md.\n"})
    assert _prose(root) == []
    linked = _repo(tmp_path / "s", {"AGENTS.md": AGENTS}, {"CLAUDE.md": "AGENTS.md"})
    assert _prose(linked) == []


def test_the_prose_pointer_finding_is_counts_only_safe(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"AGENTS.md": AGENTS, "CLAUDE.md": "See AGENTS.md.\n"})
    scans = counts_only_scans({"nested_instructions": nested_instruction_surface(root)})
    found = [f for f in scans["nested_instructions"]["findings"]
             if f["kind"] == "claude_prose_pointer"]
    assert len(found) == 1
    assert "detail" not in found[0]


def test_aider_reading_a_pointer_in_the_chain_reads_the_canonical_file(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"AGENTS.md": "See CLAUDE.md.\n", "CLAUDE.md": "See GEMINI.md.\n",
                            "GEMINI.md": CLAUDE_RULES, ".aider.conf.yml": "read: CLAUDE.md\n"})
    block = scan_agent_consistency(root)
    assert block["canonical"] == "GEMINI.md"
    assert _links(dict(block))[".aider.conf.yml"] == "config_read"
    assert "aider" in block["systems"]
    assert block["unlinked"] == 0


def test_no_prose_pointer_finding_when_agents_md_points_onward(tmp_path: Path) -> None:
    # `@AGENTS.md` would load only another pointer, so recommending it helps nobody.
    root = _repo(tmp_path, {"CLAUDE.md": "See AGENTS.md.\n", "AGENTS.md": "See GEMINI.md.\n",
                            "GEMINI.md": CLAUDE_RULES})
    assert _prose(root) == []
