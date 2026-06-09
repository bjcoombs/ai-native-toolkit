"""Unit tests for the vault-native navigation query parser (issue #176).

Covers the three predicate kinds (folder / tag / frontmatter field) across both
hub forms (`.base` filter expressions and ```dataview``` query blocks), the
frontmatter parser, and the union selection semantics.
"""
from __future__ import annotations

from pathlib import Path

from lib.vault_queries import (
    TAGS_KEY,
    parse_base_queries,
    parse_dataview_queries,
    parse_frontmatter,
    select_notes,
)


# ---- query parsing --------------------------------------------------------

def test_base_infolder_predicate_extracts_folder() -> None:
    text = 'filters:\n  and:\n    - file.inFolder("_jira")\n    - file.ext == "md"\n'
    queries = parse_base_queries(text)
    assert len(queries) == 1
    q = queries[0]
    assert q.folders == {"_jira"}
    # `file.ext == "md"` is file metadata, never a frontmatter-field predicate.
    assert q.fields == []


def test_base_frontmatter_field_predicate() -> None:
    q = parse_base_queries('filters:\n  and:\n    - status == "open"\n')[0]
    assert ("status", "open") in q.fields
    assert q.folders == set()


def test_base_tag_predicate() -> None:
    q = parse_base_queries('filters:\n  - tags.contains("project")\n')[0]
    assert q.tags == {"project"}


def test_dataview_from_folder_and_where() -> None:
    block = (
        "```dataview\n"
        'TABLE status FROM "_jira"\n'
        'WHERE status = "open"\n'
        "```\n"
    )
    queries = parse_dataview_queries(block)
    assert len(queries) == 1
    q = queries[0]
    assert q.folders == {"_jira"}
    assert ("status", "open") in q.fields


def test_dataview_from_tag() -> None:
    q = parse_dataview_queries("```dataview\nLIST FROM #project\n```")[0]
    assert q.tags == {"project"}


def test_dataviewjs_block_is_not_parsed() -> None:
    # dataviewjs is arbitrary JS - not statically resolvable, so no edges.
    block = '```dataviewjs\ndv.pages(\'"_jira"\')\n```'
    assert parse_dataview_queries(block) == []


def test_empty_query_is_dropped() -> None:
    assert parse_dataview_queries("```dataview\nLIST\n```") == []
    assert parse_base_queries("views:\n  - type: table\n") == []


# ---- frontmatter ----------------------------------------------------------

def test_frontmatter_inline_tags_and_scalar() -> None:
    fm = parse_frontmatter('---\nstatus: open\ntags: [project, urgent]\n---\nbody')
    assert fm["status"] == "open"
    assert fm[TAGS_KEY] == {"project", "urgent"}


def test_frontmatter_block_tags() -> None:
    fm = parse_frontmatter("---\ntags:\n  - alpha\n  - beta\n---\n")
    assert fm[TAGS_KEY] == {"alpha", "beta"}


def test_no_frontmatter_returns_empty() -> None:
    assert parse_frontmatter("# Just a heading\n") == {}


# ---- selection ------------------------------------------------------------

def _doc_rels(*rels: str) -> list[tuple[Path, Path]]:
    return [(Path("/repo") / r, Path(r)) for r in rels]


def test_select_by_folder() -> None:
    q = parse_base_queries('- file.inFolder("_jira")')[0]
    docs = _doc_rels("_jira/a.md", "_jira/deep/b.md", "notes/c.md")
    selected = {p.name for p in select_notes(q, docs, lambda _d: {})}
    assert selected == {"a.md", "b.md"}


def test_select_union_never_emptied_by_unmatched_field() -> None:
    # folder selects two notes; an unsatisfiable field predicate must NOT wipe
    # them out - selection is a union, so over-linking is the failure direction.
    q = parse_base_queries(
        '- file.inFolder("_jira")\n- nonexistent == "value"'
    )[0]
    docs = _doc_rels("_jira/a.md", "_jira/b.md")
    assert len(select_notes(q, docs, lambda _d: {})) == 2


def test_select_by_tag_uses_frontmatter() -> None:
    q = parse_dataview_queries("```dataview\nLIST FROM #project\n```")[0]
    docs = _doc_rels("a.md", "b.md")
    fm = {docs[0][0]: {TAGS_KEY: {"project"}}, docs[1][0]: {TAGS_KEY: {"other"}}}
    selected = {p.name for p in select_notes(q, docs, lambda d: fm.get(d, {}))}
    assert selected == {"a.md"}
