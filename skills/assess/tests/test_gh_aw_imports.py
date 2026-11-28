"""Tests for the gh-aw frontmatter ``imports:`` reader (#1602, ``lib.agents.gh_aw_imports``).

The reader reads YAML structure and raises ``YamlError`` on anything it does not follow, so a
caller can fail closed.
"""
from __future__ import annotations

import pytest

from lib.agents.agent_instruction_rewrite import gh_aw_imports, remote_import
from lib.agents.gh_aw_imports import imports
from lib.pack_yaml import YamlError


@pytest.mark.parametrize("front, expected", [
    ("engine: claude\n", []),
    ("\n# header\nimports: [a.md]\n", ["a.md"]),
    ("imports:\n", []),
    ("imports: []\n", []),
    ("imports: [a.md, b.md,]\n", ["a.md", "b.md"]),
    ("imports: ['a,b.md', \"c, d.md\"]\n", ["a,b.md", "c, d.md"]),
    ("imports:\n  [a.md,\n   b.md]\n", ["a.md", "b.md"]),
    ("imports: [\n  a.md,  # first\n  b.md\n]\n", ["a.md", "b.md"]),
    ("imports:\n- a.md\n- 'it''s.md'\n", ["a.md", "it's.md"]),
    ("imports:\n  - inputs:\n      tone: plain\n    path: a.md\n  - uses: b.md\n", ["a.md", "b.md"]),
    ("imports:\n  -\n    path: a.md\n", ["a.md"]),
    ("imports:\n  - {inputs: {x: [1, 2]}, path: 'a.md'}\n", ["a.md"]),
    ("imports: [{uses: a.md}, b.md]\n", ["a.md", "b.md"]),
    ('description: "spans\n  two lines"\nimports: [a.md]\n', ["a.md"]),
    ("tools: {github: {\n  toolsets: [repos]}}\nimports: [a.md]\nengine: claude\n", ["a.md"]),
    ("on:\n  schedule: daily\nimports:\n  - a.md  # house style\nengine: claude\n", ["a.md"]),
    ("imports: [\"a\\u0040b.md\"]\n", ["a@b.md"]),
])
def test_imports_reads_each_yaml_list_form(front: str, expected: list[str]) -> None:
    assert imports(front) == expected


@pytest.mark.parametrize("front", [
    "imports: [a.md\n",                      # an unclosed flow list
    "imports: [a.md]]\n",                    # text past the close
    "imports: [a.md, , b.md]\n",             # an empty item
    "imports: [[a.md]]\n",                   # a nested list
    "imports: a.md\n",                       # a scalar, not a list
    "imports: *shared\n",                    # an alias
    "imports: &x [a.md]\n",                  # an anchor
    "imports:\n  tone: plain\n",             # a mapping, not a list
    "imports:\n  - a.md\n   - b.md\n",       # items out of line
    "imports:\n  - a.md\n    more.md\n",     # a plain scalar over two lines
    "imports:\n  - inputs: {x: 1}\n",        # a mapping with no path
    "imports:\n  - path: a.md\n    path: b.md\n",  # a duplicate key
    "imports:\n  -\n",                       # a null item
    "imports:\n  - - a.md\n",                # a nested block list
    "imports:\n  - path:\n      a.md\n",     # a path on the next line
    "imports: [a.md]\nimports: [b.md]\n",    # a second imports key
    "  imports: [a.md]\n",                   # a line no top-level key owns
    "<<: *base\nimports: [a.md]\n",          # a merge key
    "imports: ['a.md]\n",                    # an unclosed quote
    'imports: ["\\q.md"]\n',                 # an escape the reader cannot decode
    "imports:\n\t- a.md\n",                  # a tab in the indentation
    'description: "never closes\nimports: [a.md]\n',
    "imports: [{path: a.md}}\n",             # mismatched brackets
    "imports: [null]\n",
    "imports: ['a.md' x]\n",                # text past a quoted scalar
    "imports:\n  - {path: a.md\n",          # an unclosed flow mapping item
    "imports: [{path: a.md, path: b.md}]\n",  # a duplicate flow key
    "imports: [{inputs: x}]\n",             # a flow mapping with no path
    "imports: [{, path: a.md}]\n",          # an empty leading flow key
    "imports:\n  - {path: a.md, , x: 1}\n",  # an empty middle flow key
    "imports:\n  - path: a.md\n   x: 1\n",  # a key out of line with its mapping
    "imports:\n  - [a.md]\n",               # a flow list inside a block list
])
def test_imports_raises_on_what_it_does_not_follow(front: str) -> None:
    with pytest.raises(YamlError):
        imports(front)


def test_gh_aw_imports_resolves_from_the_source_folder_and_skips_remote_imports() -> None:
    rel = ".github/workflows/guide.md"
    front = ("imports:\n  - shared/a.md#Tone\n  - example-org/agentics/shared/x.md@v1\n"
             "  - shared/b@2.md\n  - ../docs/c.md\n")
    assert gh_aw_imports(rel, front) == [".github/workflows/shared/a.md",
                                         ".github/workflows/shared/b@2.md", ".github/docs/c.md"]


def test_remote_import_needs_three_path_parts_and_a_ref() -> None:
    assert remote_import("example-org/agentics/shared/x.md@v1")
    assert remote_import("example-org/agentics/x.md@feature/branch")
    assert not remote_import("shared/x@v1.md")
    assert not remote_import(".github/agentics/x.md@v1")
    assert not remote_import("example-org/agentics/x.md")
