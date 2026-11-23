"""lib/skill_disclosure.py: skill and agent size in tokens, descriptions and references (#1045)."""
from __future__ import annotations

from pathlib import Path

import pytest

import lib.skill_disclosure as sd

REPO_ROOT = Path(__file__).resolve().parents[3]


def _skill(root: Path, body: str, *, name: str | None = "demo", description: str | None = "Does a thing.",
           frontmatter: str | None = None, folder: str = "demo") -> Path:
    skill = root / ".claude" / "skills" / folder / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    if frontmatter is None:
        keys = ([f"name: {name}"] if name is not None else []) + (
            [f"description: {description}"] if description is not None else [])
        frontmatter = "\n".join(keys)
    skill.write_text(f"---\n{frontmatter}\n---\n{body}", encoding="utf-8")
    return skill


def _ref(skill: Path, name: str, text: str) -> None:
    path = skill.parent / "references" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _one(root: Path) -> sd.SkillFile:
    (entry,) = sd.scan_skill_disclosure(root)["files"]
    return entry


# --- size ------------------------------------------------------------------------------


def test_a_large_skill_breaks_the_token_rule_and_the_same_skill_split_does_not(tmp_path: Path) -> None:
    # The #1045 fixture: 449 lines and about 82 KB, so about 20k tokens.
    big = tmp_path / "big"
    _skill(big, ("x" * 180 + "\n") * 449)
    entry = _one(big)
    assert entry["body_lines"] == 449 and entry["est_tokens"] > 20_000 and entry["breaks"] == ["tokens"]

    split = tmp_path / "split"
    skill = _skill(split, "Read [the detail](references/detail.md) when needed.\n")
    _ref(skill, "detail.md", "# Contents\n" + ("x" * 180 + "\n") * 449)
    entry = _one(split)
    assert entry["breaks"] == [] and entry["est_tokens"] < sd.SKILL_MAX_TOKENS
    (ref,) = entry["references"]
    assert (ref["target"], ref["resolved"], ref["depth"], ref["lines"], ref["toc"]) == (
        "references/detail.md", True, 1, 450, True)


@pytest.mark.parametrize(("lines", "breaks"), [(500, []), (501, ["body_lines"])])
def test_the_body_line_limit_is_500(tmp_path: Path, lines: int, breaks: list[str]) -> None:
    _skill(tmp_path, "x\n" * lines)
    entry = _one(tmp_path)
    assert entry["body_lines"] == lines and entry["total_lines"] == lines + 4 and entry["breaks"] == breaks


# --- the description -------------------------------------------------------------------


@pytest.mark.parametrize(("chars", "breaks"), [(1024, []), (1025, ["description_long"])])
def test_the_description_limit_is_1024_characters(tmp_path: Path, chars: int, breaks: list[str]) -> None:
    _skill(tmp_path, "body\n", description="d" * chars)
    entry = _one(tmp_path)
    assert entry["description_chars"] == chars and entry["breaks"] == breaks


def test_a_folded_block_scalar_is_unfolded_before_it_is_counted(tmp_path: Path) -> None:
    _skill(tmp_path, "body\n", frontmatter="name: demo\ndescription: >\n  " + "\n  ".join(["word"] * 300))
    entry = _one(tmp_path)
    assert entry["description_chars"] == len(" ".join(["word"] * 300)) == 1499
    assert entry["breaks"] == ["description_long"]


@pytest.mark.parametrize(("frontmatter", "expected"), [
    ("name: demo\ndescription: " + "a" * 600 + "\n  " + "b" * 600, "a" * 600 + " " + "b" * 600),
    ('name: demo\ndescription: "' + "a" * 600 + "\n  " + "b" * 600 + '"', "a" * 600 + " " + "b" * 600),
    ("name: demo\ndescription: 'short'", "short"),
    ("name: demo\ndescription: |\n  one\n  two", "one\ntwo"),
    ("description: plain\nname: demo", "plain"),
])
def test_a_wrapped_or_quoted_description_is_read_whole(tmp_path: Path, frontmatter: str, expected: str) -> None:
    _skill(tmp_path, "body\n", frontmatter=frontmatter)
    entry = _one(tmp_path)
    assert entry["description_chars"] == len(expected)
    assert ("description_long" in entry["breaks"]) is (len(expected) > sd.DESCRIPTION_MAX_CHARS)
    assert sd.description_text(f"---\n{frontmatter}\n---\nbody\n") == expected


@pytest.mark.parametrize(("name", "description", "breaks"), [
    (None, "d", ["name_missing"]),
    ("demo", None, ["description_missing"]),
    (None, None, ["name_missing", "description_missing"]),
])
def test_name_and_description_are_required(tmp_path: Path, name: str | None, description: str | None,
                                           breaks: list[str]) -> None:
    _skill(tmp_path, "body\n", name=name, description=description)
    entry = _one(tmp_path)
    assert entry["breaks"] == breaks
    assert entry["description_chars"] == (None if description is None else 1)


# --- references ------------------------------------------------------------------------


def test_a_missing_linked_reference_breaks_and_is_named(tmp_path: Path) -> None:
    _skill(tmp_path, "See [missing](references/missing.md).\n")
    entry = _one(tmp_path)
    assert entry["breaks"] == ["reference_missing"]
    assert entry["references"] == [{"target": "references/missing.md", "resolved": False, "depth": 1,
                                    "lines": None, "toc": None}]


def test_a_skill_dir_path_counts_when_missing_and_a_bare_backtick_only_when_it_resolves(tmp_path: Path) -> None:
    skill = _skill(tmp_path, "Read `${CLAUDE_SKILL_DIR}/references/gone.md`.\n"
                             "The fleet skill's `references/level.schema.json` describes it.\n"
                             "Then `references/here.md`.\n")
    _ref(skill, "here.md", "# Here\n")
    entry = _one(tmp_path)
    assert [(r["target"], r["resolved"]) for r in entry["references"]] == [
        ("references/gone.md", False), ("references/here.md", True)]
    assert entry["breaks"] == ["reference_missing"]


def test_links_out_of_the_folder_in_fences_or_to_urls_are_not_references(tmp_path: Path) -> None:
    _skill(tmp_path, "[docs](../../docs/windows.md) [out](./report.svg) [web](https://example.com/references/x.md)\n"
                     "```\n[fenced](references/nope.md)\n```\n[anchor](#section)\n")
    assert _one(tmp_path)["references"] == []


def test_a_reference_named_only_by_another_reference_is_two_levels_deep(tmp_path: Path) -> None:
    skill = _skill(tmp_path, "See [a](references/a.md) and [c](references/c.md).\n")
    _ref(skill, "a.md", "Also [b](b.md), [c](./c.md) and [up](../SKILL.md).\n")
    _ref(skill, "b.md", "# B\n")
    _ref(skill, "c.md", "# C\n")
    entry = _one(tmp_path)
    assert [(r["target"], r["depth"]) for r in entry["references"]] == [
        ("references/a.md", 1), ("references/c.md", 1), ("references/b.md", 2)]
    assert entry["breaks"] == ["reference_deep"]


@pytest.mark.parametrize(("text", "toc"), [
    ("# Title\n" + "line\n" * 100, False),  # 101 lines: past the limit, no table of contents
    ("# Title\n" + "line\n" * 99, None),  # 100 lines: at the limit, not recorded
    ("# Title\n## Table of contents\n" + "line\n" * 120, True),
    ("# Title\n- [A](#a)\n- [B](#b)\n- [C](#c)\n" + "line\n" * 120, True),
    ("# Title\n" + "line\n" * 120, False),
])
def test_a_long_reference_records_whether_it_has_a_table_of_contents(tmp_path: Path, text: str,
                                                                    toc: bool | None) -> None:
    skill = _skill(tmp_path, "[r](references/r.md)\n")
    _ref(skill, "r.md", text)
    (ref,) = _one(tmp_path)["references"]
    assert ref["toc"] == toc


# --- the block -------------------------------------------------------------------------


def test_no_skill_or_agent_reads_zero_files(tmp_path: Path) -> None:
    block = sd.scan_skill_disclosure(tmp_path)
    assert block["available"] is True and (block["files"], block["files_considered"], block["files_breaking"]) == (
        [], 0, 0)
    assert block["thresholds"] == {"body_lines": 500, "tokens": 5000, "description_chars": 1024,
                                   "reference_toc_lines": 100}


def test_an_agent_definition_is_measured_too(tmp_path: Path) -> None:
    agent = tmp_path / "agents" / "helper.md"
    agent.parent.mkdir()
    agent.write_text("---\nname: helper\n---\nDo things.\n", encoding="utf-8")
    (entry,) = sd.scan_skill_disclosure(tmp_path)["files"]
    assert (entry["path"], entry["kind"], entry["breaks"]) == ("agents/helper.md", "agent", ["description_missing"])


def test_excluded_and_out_of_scope_files_are_left_out(tmp_path: Path) -> None:
    _skill(tmp_path, "body\n", folder="one")
    _skill(tmp_path, "body\n", folder="two")
    assert [f["path"] for f in sd.scan_skill_disclosure(tmp_path, excludes=({"two"}, []))["files"]] == [
        ".claude/skills/one/SKILL.md"]
    assert sd.scan_skill_disclosure(tmp_path, scope=tmp_path / "elsewhere")["files"] == []


def test_a_skill_folder_scope_reads_that_skill(tmp_path: Path) -> None:
    # The unit pass scopes a skill unit to its folder, which holds no .claude/ or plugin manifest.
    _skill(tmp_path, "body\n", folder="one")
    _skill(tmp_path, "body\n", folder="two")
    scoped = sd.scan_skill_disclosure(tmp_path, scope=tmp_path / ".claude" / "skills" / "two")
    assert [f["path"] for f in scoped["files"]] == [".claude/skills/two/SKILL.md"]


def test_a_plugin_skill_folder_scope_reads_that_skill() -> None:
    scoped = sd.scan_skill_disclosure(REPO_ROOT, scope=REPO_ROOT / "skills" / "ghreport")
    assert [f["path"] for f in scoped["files"]] == ["skills/ghreport/SKILL.md"]


def test_this_repository_reads_the_breaks_the_issue_names() -> None:
    # #1045: ghreport (1,226) and fleet (1,112) are past the description limit, and
    # assess-findings (about 20k tokens) past the token budget.
    files = {f["path"]: f for f in sd.scan_skill_disclosure(REPO_ROOT)["files"]}
    assert files["skills/ghreport/SKILL.md"]["description_chars"] == 1226
    assert "description_long" in files["skills/fleet/SKILL.md"]["breaks"]
    assert "tokens" in files["skills/assess-findings/SKILL.md"]["breaks"]
    assert not [p for p, f in files.items() if "reference_missing" in f["breaks"]]
