"""Tests for lib/instructions/instruction_placement.py: the per-section always-on versus
fetch-on-need split of instruction, agent and skill files (issue #559)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.instructions.instruction_placement import (
    ALWAYS_ON_FLAG_LINES,
    LOADED_IN_FULL_MIN_LINES,
    LOADED_IN_FULL_MIN_TOKENS,
    Section,
    always_on_signals,
    analyse_file,
    fence_mask,
    fetch_signals,
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


def test_fence_closes_only_on_its_own_character_and_length() -> None:
    text = (
        "## A\n"
        "~~~markdown\n```bash\n# inside tilde\n```\n~~~\n"
        "````md\n```\n# inside long fence\n```\n````\n"
        "```py\n# closer has text after it\n``` not a closer\n# still code\n```\n"
        "## B\n"
    )
    sections = split_sections(text)
    assert [(s.heading, s.start) for s in sections] == [("A", 1), ("B", 17)]
    assert fence_mask(text.splitlines()) == [False] + [True] * 15 + [False]


def test_inline_triple_backticks_do_not_open_a_fence() -> None:
    assert fence_mask(["```code``` inline", "## H"]) == [False, False]


def test_unclosed_fence_runs_to_the_end() -> None:
    assert [s.heading for s in split_sections("## A\n```\n## not\n")] == ["A"]


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


def test_rules_a_skill_restates_stay_always_on(tmp_path: Path) -> None:
    rules = [f"- Never deploy service {i} without the release owner's sign-off." for i in range(8)]
    _write(tmp_path, "skills/release/SKILL.md",
           "---\nname: release\n---\n# Release\n" + "\n".join(rules) + "\n")
    text = "# CLAUDE.md\n\n## Rules\n" + "\n".join(rules) + "\n## Other\nshort\n"
    _write(tmp_path, "CLAUDE.md", text)
    block = scan_instruction_placement(tmp_path, {"CLAUDE.md": _instruction_entry()}, None)
    record = block["files"][0]
    assert record["relocatable"] == []
    assert record["fetch_on_need_lines"] == 0


def test_two_skills_sharing_a_section_point_one_way(tmp_path: Path) -> None:
    _plugin(tmp_path)
    shared = "## Phase 0\n" + "\n".join(
        f"Step {i}: detect whether the team tools are available before spawning." for i in range(8))
    filler = "\n".join(f"Line {i} of skill-specific guidance for this workflow." for i in range(220))
    for name in ("alpha", "beta"):
        _write(tmp_path, f"skills/{name}/SKILL.md",
               f"---\nname: {name}\n---\n# {name}\n" + shared + f"\n## Own\n{filler}\n")
    block = scan_instruction_placement(tmp_path, {}, None)
    homes = {f["path"]: [m["home"] for m in f["relocatable"] if m["heading"] == "Phase 0"]
             for f in block["loaded_in_full"]}
    assert homes["skills/alpha/SKILL.md"] == []
    assert homes["skills/beta/SKILL.md"] == [
        {"kind": "existing_skill", "target": "skills/alpha/SKILL.md"}]


def test_scorer_agent_appears_in_the_loaded_in_full_report_on_this_repo() -> None:
    block = scan_instruction_placement(REPO_ROOT, {}, None)
    entries = {f["path"]: f for f in block["loaded_in_full"]}
    scorer = entries["agents/assess-layer-scorer.md"]
    assert scorer["kind"] == "agent"
    assert 0.0 < scorer["always_on_share"] <= 1.0
    assert all(f["total_lines"] > LOADED_IN_FULL_MIN_LINES or f["est_tokens"] > LOADED_IN_FULL_MIN_TOKENS
               for f in block["loaded_in_full"])
    tokens = [f["est_tokens"] for f in block["loaded_in_full"]]
    assert tokens == sorted(tokens, reverse=True)  # most tokens first (#1045)


def _skill(tmp_path: Path, lines: int, width: int) -> Path:
    skill = tmp_path / ".claude" / "skills" / "big" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: big\ndescription: d\n---\n" + ("x" * width + "\n") * lines, encoding="utf-8")
    return skill


def test_a_long_line_skill_under_the_line_threshold_is_reported_by_tokens(tmp_path: Path) -> None:
    _skill(tmp_path, 150, 200)  # 150 lines, about 7.6k tokens
    (entry,) = scan_instruction_placement(tmp_path, {}, None)["loaded_in_full"]
    assert entry["total_lines"] < LOADED_IN_FULL_MIN_LINES < LOADED_IN_FULL_MIN_TOKENS < entry["est_tokens"]


def test_a_short_skill_under_both_thresholds_is_not_reported(tmp_path: Path) -> None:
    _skill(tmp_path, 150, 20)
    block = scan_instruction_placement(tmp_path, {}, None)
    assert block["loaded_in_full"] == [] and block["thresholds"]["loaded_in_full_min_tokens"] == 5000


def test_est_tokens_is_the_always_on_grading_estimate() -> None:
    from lib.instructions.instruction_budget import CHARS_PER_TOKEN
    from lib.instructions.instruction_placement import est_tokens

    assert CHARS_PER_TOKEN == 4 and est_tokens("x" * 9) == 3 and est_tokens("") == 0


# --- signals and homes --------------------------------------------------------------


def test_scorer_remediation_keeps_non_negotiable_lines_always_on() -> None:
    """A size signal relocates a long rules-headed section despite its heading
    (#619), so the scorer's remediation copy must say its non-negotiable lines
    stay in the always-on file (issue #648)."""
    scorer = (REPO_ROOT / "agents" / "assess-layer-scorer.md").read_text(encoding="utf-8")
    [line] = [ln for ln in scorer.splitlines()
              if ln.startswith("- `instruction_file_size[path].bloat_penalty > 0`")]
    assert "non-negotiable lines stay in the always-on file" in line


def test_rules_heading_does_not_keep_a_long_command_section_always_on() -> None:
    """Size signals beat wording (#619): a 300-line fenced section renamed onto
    an always-on word stays relocatable, so the rename cannot lower the penalty."""
    text = _kubernetes_section(300).replace("Kubernetes commands", "Kubernetes rules")
    record = analyse_file("# T\n## Intro\nx\n" + text, "CLAUDE.md", "instruction", {})
    [move] = record["relocatable"]
    assert move["heading"] == "Kubernetes rules"
    assert {"command_reference", "long_section"} <= set(move["signals"])


def test_rules_heading_keeps_a_scoped_section_without_a_size_signal_always_on() -> None:
    """Without a size signal the #419 rule holds: always-on wording wins over
    a scoped heading and scoped paths."""
    body = ["## Python rules", ""]
    body += [f"- `src/pkg/mod_{i}.py` keeps its imports sorted." for i in range(10)]
    text = "# T\n## Intro\nx\n" + "\n".join(body) + "\n"
    [section] = [s for s in split_sections(text) if s.heading == "Python rules"]
    assert {"scoped_heading", "scoped_paths"} <= set(fetch_signals(section))
    record = analyse_file(text, "CLAUDE.md", "instruction", {})
    assert record["relocatable"] == []


def test_lowercase_must_mid_sentence_is_not_a_directive() -> None:
    body = ["## Payments service", ""]
    body += [f"Handler {i} in `services/payments/handler_{i}.py` must stay pure." for i in range(60)]
    [*_, section] = split_sections(_rules_block() + "\n".join(body) + "\n")
    assert "hard_rules" not in always_on_signals(section)
    caps = section.lines[:2] + tuple(f"Handler {i} MUST stay pure." for i in range(4))
    assert "hard_rules" in always_on_signals(Section(section.heading, 2, 1, 6, caps))


def test_a_single_fetch_signal_leaves_the_section_unclassified() -> None:
    record = analyse_file(_rules_block() + _filler(120), "CLAUDE.md", "instruction", {})
    assert record["relocatable"] == []
    assert record["unclassified_lines"] >= 120


def test_only_instruction_files_are_flagged() -> None:
    text = "\n".join(f"Line {i} of prose with no section structure at all." for i in range(300))
    assert analyse_file(text, "CLAUDE.md", "instruction", {})["flagged"] is True
    assert analyse_file(text, "agents/a.md", "agent", {})["flagged"] is False
    assert analyse_file(text, "skills/s/SKILL.md", "skill", {})["flagged"] is False


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


def _agent_body(lines: int) -> str:
    """A body of ``lines`` lines under several headings."""
    out: list[str] = []
    for h in range(5):
        out += [f"## Step {h}", ""]
        out += [f"- Check item {h}.{i} before handing the result back." for i in range(lines // 5)]
    return "\n".join(out[:lines]) + "\n"


def test_root_agents_without_a_manifest_are_reported_when_shaped_as_agents(
    tmp_path: Path,
) -> None:
    """Repo E (contract I559-4, #662): ``agents/big.md`` of 250 lines under
    several headings, a 30-line CLAUDE.md and no plugin manifest."""
    _write(tmp_path, "CLAUDE.md", "# Repo\n" + "".join(f"- rule {i}\n" for i in range(29)))
    _write(tmp_path, "agents/big.md", "# Big\n" + _agent_body(249))
    _write(tmp_path, "agents/described.md", "---\ndescription: d\n---\n" + _agent_body(240))
    # Documents that live in agents/: a repository doc name with no
    # frontmatter, or frontmatter without name or description.
    _write(tmp_path, "agents/README.md", "# Agents\n" + _agent_body(240))
    _write(tmp_path, "agents/notes.md", "---\ntitle: notes\n---\n" + _agent_body(240))
    _write(tmp_path, "agents/excluded/x.md", "# X\n" + _agent_body(249))
    block = scan_instruction_placement(
        tmp_path, {"CLAUDE.md": {"present": True}}, None, None, ({"excluded"}, []))
    assert [f["path"] for f in block["files"]] == ["CLAUDE.md"]
    loaded = {f["path"]: f for f in block["loaded_in_full"]}
    assert set(loaded) == {"agents/big.md", "agents/described.md"}
    big = loaded["agents/big.md"]
    assert big["kind"] == "agent"
    assert big["total_lines"] == 250
    assert big["sections_total"] > 1
    assert 0 < big["always_on_share"] <= 1


def test_a_plugin_manifest_leaves_agent_discovery_to_the_manifest(tmp_path: Path) -> None:
    _write(tmp_path, ".claude-plugin/plugin.json", json.dumps({"name": "p", "agents": []}))
    _write(tmp_path, "agents/big.md", "# Big\n" + _agent_body(249))
    block = scan_instruction_placement(tmp_path, {}, None)
    assert block["loaded_in_full"] == []


def test_root_agents_without_a_manifest_follow_the_scope(tmp_path: Path) -> None:
    head = "---\nname: big\n---\n"
    _write(tmp_path, "pkg/agents/big.md", head + _agent_body(240))
    _write(tmp_path, "agents/other.md", head + _agent_body(240))
    block = scan_instruction_placement(tmp_path, {}, None, tmp_path / "pkg")
    assert [f["path"] for f in block["loaded_in_full"]] == ["pkg/agents/big.md"]


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


def test_registered_after_the_scans_it_reads() -> None:
    keys = [spec.key for spec in SCANS]
    spec = SCANS[keys.index("instruction_placement")]
    assert "nested_instructions" in spec.reads
    assert keys.index("nested_instructions") < keys.index("instruction_placement")


def test_a_section_that_is_the_whole_file_is_not_long() -> None:
    """A heading-less file is one section; ``long_section`` measures a section
    against the rest of the file, so it cannot fire on the whole file."""
    text = "Paths: `src/a.py` `src/b.py` `src/c.py`.\n" + "x\n" * 60
    record = analyse_file(text, "CLAUDE.md", "instruction", {})
    assert record["fetch_on_need_lines"] == 0
    assert record["unclassified_lines"] == 61


def test_a_preamble_line_above_the_only_heading_does_not_make_it_long() -> None:
    """``long_section`` needs two headed sections, so a comment line above a
    file's single heading leaves the file unclassified, as without it."""
    body = "## Layout\n" + "\n".join(f"- `src/mod{i}.py` holds part {i}." for i in range(99)) + "\n"
    for text in (body, "<!-- generated -->\n" + body):
        record = analyse_file(text, "CLAUDE.md", "instruction", {})
        assert record["fetch_on_need_lines"] == 0, text[:20]
    two = body + "## Other\n" + "Prose line.\n" * 5
    assert analyse_file(two, "CLAUDE.md", "instruction", {})["fetch_on_need_lines"] == 100


def test_nested_file_about_its_own_subtree_is_not_scoped_elsewhere() -> None:
    """``scoped_paths`` does not fire when the file already sits under the
    dominant directory: a nested file about its own subtree is in place."""
    paths = "\n".join(f"- `src/api/handlers/h{i}.py` handles route {i}." for i in range(60))
    text = "# API\n\n## Intro\n\nSmall.\n\n## Handlers\n\n" + paths + "\n"
    nested = analyse_file(text, "src/api/CLAUDE.md", "instruction", {})
    assert nested["fetch_on_need_lines"] == 0
    root = analyse_file(text, "CLAUDE.md", "instruction", {})
    [move] = root["relocatable"]
    assert set(move["signals"]) == {"scoped_paths", "long_section"}
    assert move["home"] == {"kind": "nested_instruction_file", "target": "src/CLAUDE.md"}
