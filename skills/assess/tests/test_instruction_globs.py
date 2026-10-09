"""Tests for lib/instruction_globs.py: scope frontmatter parsing and the lenient
glob matcher behind the dead-glob finding."""
from __future__ import annotations

import pytest

from lib.instruction_globs import (
    expand_braces,
    is_true,
    pattern_matches_any,
    scope_patterns,
    split_frontmatter,
    split_patterns,
)


@pytest.mark.parametrize(
    ("text", "key", "expected"),
    [
        ("---\npaths:\n  - \"src/**/*.ts\"\n  - lib/*.py\n---\nbody", "paths",
         ["src/**/*.ts", "lib/*.py"]),
        ("---\npaths: [\"a/**\", 'b/*.{ts,tsx}']\n---\n", "paths", ["a/**", "b/*.{ts,tsx}"]),
        ("---\napplyTo: \"**/*.py,**/*.pyi\"\n---\n", "applyTo", ["**/*.py,**/*.pyi"]),
        ("---\nglobs: *.ts, *.tsx\nalwaysApply: false\n---\n", "globs", ["*.ts, *.tsx"]),
        ("---\npaths:\n---\n", "paths", []),
        ("---\npaths:\n  -\n  - x\n---\n", "paths", ["x"]),
    ],
)
def test_split_frontmatter_shapes(text: str, key: str, expected: list[str]) -> None:
    fields, _body = split_frontmatter(text)
    assert fields[key] == expected


def test_split_frontmatter_returns_body() -> None:
    fields, body = split_frontmatter("---\ndescription: x\n---\n# Title\nline")
    assert fields == {"description": ["x"]}
    assert body == "# Title\nline"


@pytest.mark.parametrize("text", ["# no frontmatter", "", "---\npaths: x\nnever closed"])
def test_split_frontmatter_absent_or_unclosed(text: str) -> None:
    assert split_frontmatter(text) == ({}, text)


def test_split_frontmatter_ignores_stray_items_and_noise() -> None:
    fields, _ = split_frontmatter("---\n- orphan\n# comment\nkey: v\n---\n")
    assert fields == {"key": ["v"]}


def test_split_patterns_keeps_brace_commas() -> None:
    assert split_patterns("src/*.{ts,tsx}, 'lib/**' ,,") == ["src/*.{ts,tsx}", "lib/**"]


def test_scope_patterns_flattens_comma_lists() -> None:
    assert scope_patterns(["*.ts, *.tsx", "a/**"]) == ["*.ts", "*.tsx", "a/**"]


@pytest.mark.parametrize(
    ("values", "expected"),
    [(["true"], True), ([" True "], True), (["false"], False), ([], False), (None, False)],
)
def test_is_true(values: list[str] | None, expected: bool) -> None:
    assert is_true(values) is expected


def test_expand_braces_nested_and_plain() -> None:
    assert expand_braces("a.{ts,{js,mjs}}") == ["a.ts", "a.js", "a.mjs"]
    assert expand_braces("plain/*.py") == ["plain/*.py"]
    assert expand_braces("open{brace") == ["open{brace"]
    assert expand_braces("x{}") == ["x"]


_PATHS = ["src/app/main.ts", "src/app/view.tsx", "lib/util.py", "README.md", "a.md",
          "docs/guide/intro.md"]


@pytest.mark.parametrize(
    "pattern",
    [
        "**/*.ts", "src/**", "src/**/*.{ts,tsx}", "*.py", "lib/*.py", "src/app",
        "./lib/*.py", "/lib/util.py", "src/", "?.md", "[ab].md", "[!b].md",
        "docs/**/intro.md", "**", "!anything", "", "   ", "src/app/main.ts",
    ],
)
def test_pattern_matches(pattern: str) -> None:
    assert pattern_matches_any(pattern, _PATHS)


@pytest.mark.parametrize(
    "pattern",
    ["legacy/**/*.rb", "*.go", "lib/*.ts", "src/*.ts", "[xy].md", "[!a].md", "docs/*.md",
     "src/app/main.tsx", "bad["],
)
def test_pattern_does_not_match(pattern: str) -> None:
    assert not pattern_matches_any(pattern, _PATHS)


def test_pattern_against_empty_universe_is_dead() -> None:
    assert not pattern_matches_any("**/*.py", [])
