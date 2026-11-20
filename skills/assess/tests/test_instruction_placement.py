"""Tests for lib/instruction_placement.py: the per-section always-on versus
fetch-on-need split of instruction, agent and skill files (issue #559)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.instruction_placement import (
    ALWAYS_ON_FLAG_LINES,
    LOADED_IN_FULL_MIN_LINES,
    analyse_file,
    scan_instruction_placement,
    split_sections,
)
from lib.scan_registry import SCANS

REPO_ROOT = Path(__file__).resolve().parents[3]


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _rules_block() -> str:
    """A 40-line identity and rules block."""
    lines = ["# CLAUDE.md", "", "## Rules", ""]
    lines += [f"- Never skip step {i} of the release checklist without the owner." for i in range(30)]
    lines += ["", "## Where things live", ""]
    lines += ["- Docs: `docs/index.md`", "- Scripts: `scripts/build.sh`", "- CI: `ci/main.yml`"]
    return "\n".join(lines[:40]) + "\n"


def _kubernetes_section(lines: int = 300) -> str:
    body = ["## Kubernetes commands", "", "```bash"]
    body += [f"kubectl get pods -n team-{i} --selector app=service-{i}" for i in range(lines - 4)]
    body += ["```"]
    return "\n".join(body) + "\n"


def _filler(lines: int) -> str:
    out = ["## Background notes", ""]
    out += [f"Note {i}: the service talks to the billing API through a queue." for i in range(lines - 2)]
    return "\n".join(out) + "\n"


def _instruction_entry() -> dict:
    return {"present": True}


# --- splitting --------------------------------------------------------------------


def test_file_with_no_headings_is_one_unclassified_section() -> None:
    text = "Just some prose.\nAnother line.\n"
    sections = split_sections(text)
    assert len(sections) == 1
    assert (sections[0].heading, sections[0].start, sections[0].end) == ("", 1, 2)
    record = analyse_file(text, "CLAUDE.md", "instruction", {})
    assert record["sections_total"] == 1
    assert record["unclassified_lines"] == 2
    assert record["always_on_lines"] == 2
    assert record["relocatable"] == []


def test_empty_file_yields_no_sections() -> None:
    assert split_sections("") == []
    record = analyse_file("", "CLAUDE.md", "instruction", {})
    assert record["total_lines"] == 0 and record["always_on_share"] == 0.0


def test_single_title_splits_at_the_next_level_and_subsections_roll_up() -> None:
    text = "# Title\nintro\n## A\na\n### A.1\na1\n## B\nb\n"
    sections = split_sections(text)
    assert [(s.heading, s.start, s.end) for s in sections] == [
        ("Title", 1, 2), ("A", 3, 6), ("B", 7, 8)]


def test_preamble_before_first_heading_and_fenced_hash_lines() -> None:
    text = "preamble\n## A\n```bash\n# not a heading\n```\n## B\n"
    sections = split_sections(text)
    assert [(s.heading, s.start, s.end) for s in sections] == [
        ("", 1, 1), ("A", 2, 5), ("B", 6, 6)]


def test_frontmatter_is_not_split() -> None:
    text = "---\nname: x\n# comment: y\n---\n# Agent\nbody\n"
    sections = split_sections(text)
    assert [(s.heading, s.start) for s in sections] == [("", 1), ("Agent", 5)]


# --- the #419 success criteria ------------------------------------------------------


def _big_claude_md() -> str:
    return _rules_block() + _kubernetes_section(300) + _filler(260)


def test_600_line_file_names_the_kubernetes_section_with_a_skill_home(tmp_path: Path) -> None:
    text = _big_claude_md()
    _write(tmp_path, "CLAUDE.md", text)
    _write(tmp_path, ".claude/skills/deploy/SKILL.md", "---\nname: deploy\n---\nDeploy.\n")
    block = scan_instruction_placement(tmp_path, {"CLAUDE.md": _instruction_entry()}, None)
    [record] = block["files"]
    assert record["total_lines"] == len(text.splitlines()) == 600
    [move] = record["relocatable"]
    assert move["heading"] == "Kubernetes commands"
    assert (move["start_line"], move["end_line"]) == (41, 340)
    assert {"scoped_heading", "command_reference", "long_section"} <= set(move["signals"])
    assert move["home"] == {"kind": "skill", "target": ".claude/skills/kubernetes-commands/SKILL.md"}
    assert record["always_on_lines"] == 300
    assert record["fetch_on_need_lines"] == 300
    assert record["always_on_share"] == 0.5
    # 300 always-on lines is half the file, not more than half: not flagged.
    assert record["flagged"] is False
    assert block["flagged"] == []


def test_same_file_with_the_section_replaced_by_a_pointer_has_no_move() -> None:
    text = _rules_block() + "## Kubernetes\n\nSee the `deploy` skill.\n" + _filler(260)
    record = analyse_file(text, "CLAUDE.md", "instruction", {})
    assert record["relocatable"] == []
    # 303 lines, all always-on: over 200 and over half the file.
    assert record["flagged"] is True


def test_120_line_rules_and_pointers_file_has_no_relocatable_section() -> None:
    lines = ["# CLAUDE.md", "", "## Hard rules", ""]
    lines += [f"- Always run `make check` before pushing change {i}." for i in range(57)]
    lines += ["", "## Reference", ""]
    lines += [f"- Topic {i}: `docs/topic-{i}.md`" for i in range(56)]
    text = "\n".join(lines) + "\n"
    record = analyse_file(text, "CLAUDE.md", "instruction", {})
    assert record["total_lines"] == 120
    assert record["relocatable"] == []
    assert record["flagged"] is False


def test_section_duplicating_a_skill_names_that_skill(tmp_path: Path) -> None:
    shared = [f"Step {i}: tag the release candidate and push the annotated tag upstream." for i in range(8)]
    skill = "---\nname: release\n---\n# Release\n" + "\n".join(shared) + "\n"
    _write(tmp_path, "skills/release/SKILL.md", skill)
    text = "# CLAUDE.md\n\n## Releasing\n" + "\n".join(shared) + "\n## Other\nshort\n"
    _write(tmp_path, "CLAUDE.md", text)
    block = scan_instruction_placement(tmp_path, {"CLAUDE.md": _instruction_entry()}, None)
    [move] = block["files"][0]["relocatable"]
    assert move["heading"] == "Releasing"
    assert move["signals"] == ["duplicates_skill"]
    assert move["home"] == {"kind": "existing_skill", "target": "skills/release/SKILL.md"}


def test_scorer_agent_appears_in_the_loaded_in_full_report_on_this_repo() -> None:
    block = scan_instruction_placement(REPO_ROOT, {}, None)
    entries = {f["path"]: f for f in block["loaded_in_full"]}
    scorer = entries["agents/assess-layer-scorer.md"]
    assert scorer["kind"] == "agent"
    assert 0.0 < scorer["always_on_share"] <= 1.0
    assert all(f["total_lines"] > LOADED_IN_FULL_MIN_LINES for f in block["loaded_in_full"])


# --- signals and homes --------------------------------------------------------------


def test_rules_heading_keeps_a_long_command_section_always_on() -> None:
    text = _kubernetes_section(300).replace("Kubernetes commands", "Kubernetes rules")
    record = analyse_file("# T\n## Intro\nx\n" + text, "CLAUDE.md", "instruction", {})
    assert record["relocatable"] == []


def test_a_single_fetch_signal_leaves_the_section_unclassified() -> None:
    record = analyse_file(_rules_block() + _filler(120), "CLAUDE.md", "instruction", {})
    assert record["relocatable"] == []
    assert record["unclassified_lines"] >= 120


def test_scoped_paths_suggest_a_nested_instruction_file() -> None:
    body = ["## Payments service", ""]
    body += [f"- Handler {i} lives in `services/payments/handler_{i}.py`; keep it pure." for i in range(60)]
    text = _rules_block() + "\n".join(body) + "\n"
    record = analyse_file(text, "AGENTS.md", "instruction", {})
    [move] = record["relocatable"]
    assert "scoped_paths" in move["signals"]
    assert move["home"] == {"kind": "nested_instruction_file", "target": "services/AGENTS.md"}


def test_skill_and_agent_homes_are_reference_docs() -> None:
    text = "# Skill\n## Intro\nx\n" + _kubernetes_section(300)
    skill = analyse_file(text, "skills/ops/SKILL.md", "skill", {})
    assert skill["relocatable"][0]["home"] == {
        "kind": "reference_doc", "target": "skills/ops/references/kubernetes-commands.md"}
    agent = analyse_file(text, "agents/ops.md", "agent", {})
    assert agent["relocatable"][0]["home"] == {"kind": "reference_doc", "target": None}


# --- the scan ----------------------------------------------------------------------


def _plugin(root: Path) -> None:
    _write(root, ".claude-plugin/plugin.json", json.dumps({"name": "p"}))


def test_loaded_in_full_lists_agents_and_skills_over_the_floor(tmp_path: Path) -> None:
    _plugin(tmp_path)
    big = "---\nname: big\n---\n# Big\n" + _kubernetes_section(LOADED_IN_FULL_MIN_LINES)
    _write(tmp_path, "agents/big.md", big)
    _write(tmp_path, "agents/small.md", "---\nname: small\n---\nsmall\n")
    _write(tmp_path, "skills/huge/SKILL.md", big + _filler(100))
    _write(tmp_path, "agents/excluded/x.md", big)
    block = scan_instruction_placement(
        tmp_path, {}, None, None, ({"excluded"}, []))
    assert [(f["path"], f["kind"]) for f in block["loaded_in_full"]] == [
        ("skills/huge/SKILL.md", "skill"), ("agents/big.md", "agent")]
    assert block["loaded_in_full_considered"] == 3
    assert block["thresholds"]["always_on_lines"] == ALWAYS_ON_FLAG_LINES


def test_aliases_absent_files_and_symlinks_are_analysed_once(tmp_path: Path) -> None:
    _write(tmp_path, "CLAUDE.md", "# A\nx\n")
    (tmp_path / "AGENTS.md").symlink_to("CLAUDE.md")
    files = {
        "CLAUDE.md": {"present": True},
        "AGENTS.md": {"present": True},
        "GEMINI.md": {"present": True, "is_alias": True},
        ".cursorrules": {"present": False},
        "missing.md": {"present": True},
        "junk": "not a mapping",
    }
    block = scan_instruction_placement(tmp_path, files, None)
    assert [f["path"] for f in block["files"]] == ["CLAUDE.md"]


def test_nested_instruction_files_are_included(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/CLAUDE.md", "# Pkg\nx\n")
    nested = {"available": True, "files": [{"path": "pkg/CLAUDE.md"}, {"nope": 1}]}
    block = scan_instruction_placement(tmp_path, {}, nested)
    assert [f["path"] for f in block["files"]] == ["pkg/CLAUDE.md"]
    unavailable = scan_instruction_placement(tmp_path, {}, {"available": False})
    assert unavailable["files"] == []


@pytest.mark.parametrize("cap", [10])
def test_relocatable_list_is_capped_with_a_total(cap: int) -> None:
    text = "# T\n" + "".join(
        _kubernetes_section(45).replace("Kubernetes commands", f"Docker step {i}")
        for i in range(cap + 2))
    record = analyse_file(text, "CLAUDE.md", "instruction", {})
    assert record["relocatable_total"] == cap + 2
    assert len(record["relocatable"]) == cap


def test_registered_last_in_the_scan_table() -> None:
    spec = SCANS[-1]
    assert spec.key == "instruction_placement"
    assert "nested_instructions" in spec.reads
