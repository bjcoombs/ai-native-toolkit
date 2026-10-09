"""Tests for lib/instruction_content.py: stale references, trees, overviews, README overlap."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lib.instruction_content import (
    build_repo_context,
    check_paths,
    check_symbols,
    count_directory_trees,
    overview_headings,
    path_references,
    readme_overlap_pct,
    symbol_references,
)


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


@pytest.mark.parametrize(("span", "expected"), [
    ("src/app.py", "src/app.py"),
    ("src/", "src/"),
    ("docs/guide", "docs/guide"),
    ("CLAUDE.md", "CLAUDE.md"),
    (".cursorrules", ".cursorrules"),
    ("tests/test_x.py::test_y", "tests/test_x.py"),
    ("src/app.py:12", "src/app.py"),
    ("src/app.py:12:4", "src/app.py"),
    ("src/app.py#L10-L20", "src/app.py"),
    ("https://example.com/a.md", None),     # URL
    ("src/*.py", None),                     # glob
    ("commands/<name>.md", None),           # placeholder
    ("skills/{id}/SKILL.md", None),         # placeholder
    ("$HOME/x.md", None),                   # variable
    ("/etc/hosts", None),                   # absolute
    ("~/.claude/CLAUDE.md", None),          # home
    ("../sibling/x.md", None),              # leaves the file's tree
    ("node_modules/x/index.js", None),      # excluded tree
    ("tests/fixtures/a.md", None),          # excluded sequence
    ("npm test", None),                     # whitespace: a command, not a path
    ("lib.agent_instructions_grader", None),  # dotted module
    ("v1.2.3", None),                       # version
    ("e.g.", None),
    ("a//b", None),                         # empty segment
    ("--flag", None),
])
def test_path_candidates(span: str, expected: str | None) -> None:
    refs = path_references(f"See `{span}` here.\n")
    assert [r.text for r in refs] == ([expected] if expected else [])


def test_fenced_spans_are_not_references() -> None:
    text = "```\n`src/gone.py` and `gone_symbol`\n```\nprose `src/real.py`\n"
    assert [r.text for r in path_references(text)] == ["src/real.py"]
    assert symbol_references(text) == []


@pytest.mark.parametrize(("span", "is_symbol"), [
    ("grade_instructions", True),
    ("grade_instructions()", True),
    ("parseConfig", True),
    ("RepoIndex", True),
    ("_private_helper", True),
    ("GIT_CONFIG_GLOBAL", False),   # ALL_CAPS: often an external env var
    ("main", False),                # plain word
    ("Grade", False),               # single capital word
    ("abc", False),                 # too short
    ("os.path.join", False),        # dotted
    ("in-progress", False),         # hyphenated label
])
def test_symbol_candidates(span: str, is_symbol: bool) -> None:
    assert bool(symbol_references(f"`{span}`")) is is_symbol


REPO_FILES = {
    "src/app.py": "def real_function():\n    pass\n",
    "skills/x/lib/helper.py": "",
    "docs/guide.md": "",
    ".github/notes.md": "",
    "CLAUDE.md": "",
    "coverage.xml.keep": "",
}


def test_check_paths_in_a_walked_tree(tmp_path: Path) -> None:
    root = _write(tmp_path, REPO_FILES)
    ctx = build_repo_context(root)
    text = (
        "`src/app.py` `src/` `docs/guide` `lib/helper.py` `CLAUDE.md` `notes.md`\n"
        "`src/gone.py` `owner/repo` `feat/branch` `docs/missing/` `ghost.md`\n"
    )
    existing, stale = check_paths(text, ctx, ".github/CLAUDE.md")
    assert {r.text for r in existing} == {
        "src/app.py", "src/", "lib/helper.py", "CLAUDE.md", "notes.md",
    }
    # `docs/guide` has no extension but `docs` exists, so it is checked: the
    # file is `docs/guide.md`, and the reference is stale.
    assert [(s["reference"], s["line"], s["kind"]) for s in stale] == [
        ("docs/guide", 1, "stale_path"), ("src/gone.py", 2, "stale_path"), ("docs/missing/", 2, "stale_path"),
        ("ghost.md", 2, "stale_path"),
    ]
    # Without git there is no symbol search: no evidence, no finding.
    assert not ctx.git
    assert check_symbols("`gone_symbol`", ctx, "CLAUDE.md") == []


def test_relative_to_the_instruction_file(tmp_path: Path) -> None:
    root = _write(tmp_path, {"docs/CLAUDE.md": "", "docs/sub/page.md": ""})
    ctx = build_repo_context(root)
    existing, stale = check_paths("`sub/page.md`", ctx, "docs/CLAUDE.md")
    assert [r.text for r in existing] == ["sub/page.md"] and stale == []


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_git_repo_symbols_and_gitignored_paths(git_repo) -> None:
    repo, commit = git_repo
    _write(repo, {
        "src/app.py": "def real_function():\n    return parseConfig\n",
        ".gitignore": "coverage.xml\nbuild/\n",
        "CLAUDE.md": "Call `real_function` and `parseConfig()`, not `removed_function`.\n"
                     "Open `coverage.xml` or `build/out.js`; `src/gone.py` is stale.\n",
    })
    (repo / "link").symlink_to("src")
    commit("init")
    ctx = build_repo_context(repo)
    assert ctx.git
    text = (repo / "CLAUDE.md").read_text()
    stale_symbols = check_symbols(text, ctx, "CLAUDE.md")
    assert [(s["reference"], s["line"]) for s in stale_symbols] == [("removed_function", 1)]
    existing, stale = check_paths(text + "`link`\n", ctx, "CLAUDE.md")
    assert [s["reference"] for s in stale] == ["src/gone.py"]  # gitignored paths dropped
    assert "link" not in {r.text for r in existing}  # no extension, not a path candidate
    assert "link" in ctx.index.files  # but the tracked symlink is listed as itself


def test_symbol_check_without_a_self_path(git_repo) -> None:
    """A direct caller with no instruction-file path still gets symbol findings."""
    repo, commit = git_repo
    _write(repo, {"x.py": "def kept_name(): pass\n"})
    commit("init")
    ctx = build_repo_context(repo)
    stale = check_symbols("`kept_name` `gone_name`\n", ctx, "")
    assert [s["reference"] for s in stale] == ["gone_name"]


def test_symbol_only_in_the_instruction_file_is_stale(git_repo) -> None:
    repo, commit = git_repo
    _write(repo, {"AGENTS.md": "`only_here_symbol`\n", "x.py": "pass\n"})
    commit("init")
    ctx = build_repo_context(repo)
    stale = check_symbols("`only_here_symbol`\n", ctx, "AGENTS.md")
    assert [s["reference"] for s in stale] == ["only_here_symbol"]


def test_readme_is_read_when_tracked(tmp_path: Path) -> None:
    root = _write(tmp_path, {"README.md": "# Title\nhello\n"})
    assert build_repo_context(root).readme_text.startswith("# Title")
    assert build_repo_context(_write(tmp_path / "none", {"x.py": ""})).readme_text == ""


def test_directory_trees() -> None:
    tree = "```\nrepo/\n├── src/\n│   └── app.py\n└── README.md\n```\n"
    ascii_tree = "|-- src\n|   `-- app.py\n`-- README.md\n"
    assert count_directory_trees(tree) == 1
    assert count_directory_trees(ascii_tree) == 1
    assert count_directory_trees(tree + "\ntext\n" + tree) == 2
    assert count_directory_trees("├── one\n└── two\n") == 0  # under TREE_MIN_LINES
    assert count_directory_trees("plain text") == 0
    flags = "- `--dry-run`: print only\n- `--fix`: apply\n- `--verbose`: log more\n"
    table = "| a | b |\n|---|---|\n|--x| y |\n| `--fix` | z |\n"
    assert count_directory_trees(flags) == 0
    assert count_directory_trees(table) == 0
    assert count_directory_trees("    |-- a\n    |-- b\n    `-- c\n") == 1


@pytest.mark.parametrize(("heading", "is_overview"), [
    ("## Project overview", True),
    ("# Repository structure", True),
    ("### Codebase layout", True),
    ("## Directory structure:", True),
    ("## Overview", True),
    ("## About this project", True),
    ("## Introduction", True),
    ("## What is this repo", True),
    ("## Architecture", False),
    ("## Commands", False),
    ("Project overview", False),  # not a heading
])
def test_overview_headings(heading: str, is_overview: bool) -> None:
    assert bool(overview_headings(heading + "\n")) is is_overview


def test_readme_overlap() -> None:
    readme = "\n".join(f"- This README line number {i} describes the project in detail." for i in range(10))
    copied = "\n".join(f"## This README line number {i} describes the project in detail." for i in range(5))
    own = "\n".join(f"A line written only for agents, number {i}, with detail." for i in range(5))
    assert readme_overlap_pct(copied + "\n" + own, readme) == 50
    assert readme_overlap_pct(own, readme) == 0
    assert readme_overlap_pct(copied, "") == 0
    assert readme_overlap_pct("short\nlines\n", readme) == 0  # too few comparable lines
