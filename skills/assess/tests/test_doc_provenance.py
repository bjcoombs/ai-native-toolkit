"""Tests for provenance-aware staleness of generated docs (issue #178)."""
from __future__ import annotations

import os
from pathlib import Path

from lib.doc_provenance import (
    parse_frontmatter_provenance,
    resolve_doc_sources,
    source_is_newer,
)


def _write(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _set_mtime(path: Path, when: float) -> None:
    os.utime(path, (when, when))


# --- frontmatter parsing ---------------------------------------------------

def test_frontmatter_scalar_source(tmp_path: Path) -> None:
    doc = _write(tmp_path, "notes.md", "---\nsource: data/jira.tsv\n---\nbody")
    sources, generated_by = parse_frontmatter_provenance(doc)
    assert sources == ["data/jira.tsv"]
    assert generated_by is None


def test_frontmatter_flow_list_and_generated_by(tmp_path: Path) -> None:
    doc = _write(
        tmp_path, "ref.md",
        "---\nsource: [a.tsv, b.tsv]\ngenerated_by: scripts/gen.py\n---\nx",
    )
    sources, generated_by = parse_frontmatter_provenance(doc)
    assert sources == ["a.tsv", "b.tsv"]
    assert generated_by == "scripts/gen.py"


def test_frontmatter_block_list(tmp_path: Path) -> None:
    doc = _write(
        tmp_path, "ref.md",
        "---\nsource:\n  - a.tsv\n  - b.tsv\n---\nx",
    )
    sources, _ = parse_frontmatter_provenance(doc)
    assert sources == ["a.tsv", "b.tsv"]


def test_frontmatter_quotes_and_inline_comment(tmp_path: Path) -> None:
    doc = _write(tmp_path, "n.md", "---\nsource: \"data/x.tsv\"  # the dump\n---\n")
    sources, _ = parse_frontmatter_provenance(doc)
    assert sources == ["data/x.tsv"]


def test_no_frontmatter_returns_empty(tmp_path: Path) -> None:
    doc = _write(tmp_path, "plain.md", "# Just a heading\nbody")
    assert parse_frontmatter_provenance(doc) == ([], None)


def test_unterminated_frontmatter_degrades(tmp_path: Path) -> None:
    doc = _write(tmp_path, "bad.md", "---\nsource: x.tsv\nno closing fence")
    assert parse_frontmatter_provenance(doc) == ([], None)


# --- resolution ------------------------------------------------------------

def test_resolve_frontmatter_repo_relative(tmp_path: Path) -> None:
    _write(tmp_path, "data/jira.tsv", "data")
    doc = _write(tmp_path, "notes/dump.md", "---\nsource: data/jira.tsv\n---\n")
    resolved, _, method = resolve_doc_sources(doc, tmp_path, [])
    assert method == "frontmatter"
    assert [p.name for p in resolved] == ["jira.tsv"]


def test_resolve_skips_missing_source(tmp_path: Path) -> None:
    doc = _write(tmp_path, "notes/dump.md", "---\nsource: data/gone.tsv\n---\n")
    resolved, _, method = resolve_doc_sources(doc, tmp_path, [])
    assert resolved == []
    assert method == ""


def test_resolve_via_config_mapping(tmp_path: Path) -> None:
    _write(tmp_path, "data/jira.tsv", "data")
    doc = _write(tmp_path, "notes/123.md", "no frontmatter")
    config = [("notes", ["data/jira.tsv"])]
    resolved, _, method = resolve_doc_sources(doc, tmp_path, config)
    assert method == "config"
    assert [p.name for p in resolved] == ["jira.tsv"]


def test_frontmatter_wins_over_config(tmp_path: Path) -> None:
    _write(tmp_path, "data/fm.tsv", "fm")
    _write(tmp_path, "data/cfg.tsv", "cfg")
    doc = _write(tmp_path, "notes/x.md", "---\nsource: data/fm.tsv\n---\n")
    config = [("notes", ["data/cfg.tsv"])]
    resolved, _, method = resolve_doc_sources(doc, tmp_path, config)
    assert method == "frontmatter"
    assert [p.name for p in resolved] == ["fm.tsv"]


def test_config_does_not_match_sibling_prefix(tmp_path: Path) -> None:
    """`path = "notes"` must not match `notes-archive/` (prefix-on-segment)."""
    _write(tmp_path, "data/jira.tsv", "data")
    doc = _write(tmp_path, "notes-archive/x.md", "no fm")
    config = [("notes", ["data/jira.tsv"])]
    _, _, method = resolve_doc_sources(doc, tmp_path, config)
    assert method == ""


# --- source_is_newer (mtime fallback when not in git) ----------------------

def test_source_newer_true(tmp_path: Path) -> None:
    src = _write(tmp_path, "data/jira.tsv", "data")
    doc = _write(tmp_path, "notes/dump.md", "---\nsource: data/jira.tsv\n---\n")
    _set_mtime(doc, 1_000_000)
    _set_mtime(src, 2_000_000)  # source changed after the doc
    assert source_is_newer(doc, [src]) is True


def test_source_newer_false(tmp_path: Path) -> None:
    src = _write(tmp_path, "data/jira.tsv", "data")
    doc = _write(tmp_path, "notes/dump.md", "---\nsource: data/jira.tsv\n---\n")
    _set_mtime(src, 1_000_000)
    _set_mtime(doc, 2_000_000)  # doc regenerated after the source
    assert source_is_newer(doc, [src]) is False


def test_source_newer_none_without_sources(tmp_path: Path) -> None:
    doc = _write(tmp_path, "notes/dump.md", "body")
    assert source_is_newer(doc, []) is None
