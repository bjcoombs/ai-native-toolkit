"""lib/skill_library.py and the skill-library condition (#848, #826 theme 5).

A committed library of valid SKILL.md files in a known location meets the condition, and a
symlink sharing it across harnesses is named as evidence. SKILL.md files under node_modules or a
fixture folder, symlinks that leave the repository and invalid frontmatter do not count.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

import lib.scoring.level_tally as lt
from lib.counts_only.content_free import counts_only_scans
from lib.run.run_scope import Scope
from lib.skill_library import library_location, scan_skill_library, valid_frontmatter

CID = "skill-library.1"
SKILL = "---\nname: {name}\ndescription: Use when {name} is needed.\n---\n\n# {name}\n"
needs_symlinks = pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")


def _skill(name: str) -> str:
    return SKILL.format(name=name)


def _git(root: Path, files: dict[str, str], links: dict[str, str] | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for rel, target in (links or {}).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(target)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "c"],
                   cwd=root, check=True)
    return root


def _state(block: Any) -> str:
    cond = next(c for c in lt.level_result(lt.default_model(), {"skill_library": block})["conditions"]
                if c["id"] == CID)
    return str(cond["state"])


def test_counts_only_keeps_the_reason_code(tmp_path: Path) -> None:
    out = counts_only_scans({"skill_library": dict(scan_skill_library(tmp_path))})["skill_library"]
    assert (out["available"], out["reason"]) == (False, "not_a_git_repository")


@needs_symlinks
def test_a_two_skill_library_shared_by_symlink_meets_and_names_the_link(tmp_path: Path) -> None:
    root = _git(tmp_path, {".claude/skills/deploy/SKILL.md": _skill("deploy"),
                           ".claude/skills/review/SKILL.md": _skill("review")},
                links={".agents/skills": "../.claude/skills"})
    block = scan_skill_library(root)
    assert block["present"] is True and block["skills_counted"] == 2
    assert [s["path"] for s in block["skills"]] == [".claude/skills/deploy/SKILL.md",
                                                    ".claude/skills/review/SKILL.md"]
    assert block["shared_by_symlink"] == [{"path": ".agents/skills", "target": ".claude/skills"}]
    assert block["symlinks_outside"] == 0
    assert _state(block) == "met"


def test_skills_under_node_modules_or_a_fixture_folder_do_not_meet(tmp_path: Path) -> None:
    root = _git(tmp_path, {"node_modules/pkg/skills/a/SKILL.md": _skill("a"),
                           "skills/node_modules/b/SKILL.md": _skill("b"),
                           "tests/fixtures/repo/.claude/skills/c/SKILL.md": _skill("c"),
                           "skills/fixtures/d/SKILL.md": _skill("d"),
                           "docs/examples/e/SKILL.md": _skill("e")})
    block = scan_skill_library(root)
    assert block["present"] is False and block["skills_counted"] == 0
    assert (block["set_aside_vendored"], block["set_aside_fixture"], block["set_aside_location"]) == (2, 2, 1)
    assert _state(block) == "unmet"


@needs_symlinks
def test_a_symlink_resolving_outside_the_repository_does_not_count(tmp_path: Path) -> None:
    outside = tmp_path / "home" / "skills"
    (outside / "x").mkdir(parents=True)
    (outside / "x" / SKILL_NAME).write_text(_skill("x"), encoding="utf-8")
    root = _git(tmp_path / "repo", {"README.md": "r\n"},
                links={".claude/skills": str(outside),
                       ".agents/skills/y/SKILL.md": str(outside / "x" / SKILL_NAME)})
    block = scan_skill_library(root)
    assert block["present"] is False and block["symlinks_outside"] == 2
    assert block["shared_by_symlink"] == []
    assert _state(block) == "unmet"


@needs_symlinks
def test_a_symlinked_skill_file_inside_the_repository_counts_once(tmp_path: Path) -> None:
    root = _git(tmp_path, {".claude/skills/a/SKILL.md": _skill("a")},
                links={".gemini/skills/a/SKILL.md": "../../../.claude/skills/a/SKILL.md"})
    block = scan_skill_library(root)
    assert block["skills_counted"] == 1


@needs_symlinks
def test_a_tree_reached_only_through_a_known_link_counts(tmp_path: Path) -> None:
    root = _git(tmp_path, {"tools/agent-skills/a/SKILL.md": _skill("a")},
                links={".claude/skills": "../tools/agent-skills"})
    block = scan_skill_library(root)
    assert block["skills"] == [{"path": "tools/agent-skills/a/SKILL.md", "location": ".claude/skills"}]
    assert block["shared_by_symlink"] == [{"path": ".claude/skills", "target": "tools/agent-skills"}]


@pytest.mark.parametrize("text", [
    "# no frontmatter\n",
    "---\nname: a\n---\nbody\n",                         # no description
    "---\ndescription: d\n---\nbody\n",                  # no name
    "---\nname: a\ndescription: d\n",                    # never closes
    "---\nname: a\nnot a key line\ndescription: d\n---\n",  # malformed line
])
def test_a_skill_with_invalid_frontmatter_does_not_meet(tmp_path: Path, text: str) -> None:
    root = _git(tmp_path, {"skills/a/SKILL.md": text})
    block = scan_skill_library(root)
    assert block["present"] is False
    assert (block["invalid_frontmatter"], block["invalid_files"]) == (1, ["skills/a/SKILL.md"])
    assert _state(block) == "unmet"


def test_block_scalar_description_is_valid() -> None:
    assert valid_frontmatter("---\nname: a\ndescription: >\n  folded text\n---\n")


def test_untracked_skills_do_not_count(tmp_path: Path) -> None:
    root = _git(tmp_path, {"README.md": "r\n"})
    (root / ".claude" / "skills" / "a").mkdir(parents=True)
    (root / ".claude" / "skills" / "a" / SKILL_NAME).write_text(_skill("a"), encoding="utf-8")
    assert scan_skill_library(root)["present"] is False


@pytest.mark.parametrize(("rel", "where"), [
    ("skills/a/SKILL.md", "skills"),
    ("skills/group/a/SKILL.md", "skills"),
    (".codex/skills/a/SKILL.md", ".codex/skills"),
    ("plugins/p/skills/a/SKILL.md", "plugins/p/skills"),
    ("src/skills/a/SKILL.md", None),
    ("skills/SKILL.md", None),
    ("skills/a/b/c/SKILL.md", None),
    ("skills/a/README.md", None),
])
def test_known_locations(rel: str, where: str | None) -> None:
    assert library_location(rel) == where


def test_lock_file_and_hashed_manifest_are_evidence(tmp_path: Path) -> None:
    manifest = '{"plugins": [{"name": "p", "sha256": "0123456789abcdef0123"}]}\n'
    root = _git(tmp_path, {"skills/a/SKILL.md": _skill("a"), "skills-lock.json": "{}\n",
                           ".claude-plugin/marketplace.json": manifest})
    block = scan_skill_library(root)
    assert (block["lock_files"], block["hashed_manifests"]) == (["skills-lock.json"],
                                                                [".claude-plugin/marketplace.json"])


def test_scope_keeps_skills_under_it_and_reads_its_own_prefixes(tmp_path: Path) -> None:
    root = _git(tmp_path, {"skills/a/SKILL.md": _skill("a"),
                           "packages/web/.claude/skills/b/SKILL.md": _skill("b")})
    block = scan_skill_library(root, Scope.of(root, Path("packages/web")))
    assert block["skills"] == [{"path": "packages/web/.claude/skills/b/SKILL.md",
                                "location": "packages/web/.claude/skills"}]


def test_excluded_dirs_are_set_aside(tmp_path: Path) -> None:
    root = _git(tmp_path, {"skills/a/SKILL.md": _skill("a")})
    block = scan_skill_library(root, None, ({"skills"}, []))
    assert (block["present"], block["set_aside_vendored"]) == (False, 1)


def test_not_a_git_repository_is_not_measured(tmp_path: Path) -> None:
    (tmp_path / "skills" / "a").mkdir(parents=True)
    (tmp_path / "skills" / "a" / SKILL_NAME).write_text(_skill("a"), encoding="utf-8")
    block = scan_skill_library(tmp_path)
    assert block["available"] is False and block["reason"].startswith("not_a_git_repository")
    assert _state(block) == "not_measured"


def test_counts_only_keeps_every_count_field(tmp_path: Path) -> None:
    root = _git(tmp_path, {"skills/a/SKILL.md": _skill("a"), "skills/b/SKILL.md": "# none\n"})
    out = counts_only_scans({"skill_library": dict(scan_skill_library(root))})["skill_library"]
    for key in ("present", "skills_counted", "invalid_frontmatter", "set_aside_vendored",
                "set_aside_fixture", "set_aside_location", "symlinks_outside"):
        assert key in out, key
    assert (out["present"], out["skills_counted"], out["invalid_frontmatter"]) == (True, 1, 1)


SKILL_NAME = "SKILL.md"
