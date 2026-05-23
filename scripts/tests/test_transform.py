"""Unit tests for transform_skill.py primitives."""
import textwrap
import pytest
from transform_skill import (
    strip_chat_skip,
    apply_chat_replace,
    override_frontmatter,
    check_orphan_markers,
)


# ── strip_chat_skip ──────────────────────────────────────────────────────────

def test_strip_removes_block():
    text = "before\n<!-- chat-skip:start -->\nhidden\n<!-- chat-skip:end -->\nafter\n"
    assert strip_chat_skip(text) == "before\nafter\n"


def test_strip_no_markers_unchanged():
    text = "no markers here\n"
    assert strip_chat_skip(text) == text


def test_strip_multiple_blocks():
    text = (
        "a\n<!-- chat-skip:start -->\nb\n<!-- chat-skip:end -->\n"
        "c\n<!-- chat-skip:start -->\nd\n<!-- chat-skip:end -->\ne\n"
    )
    assert strip_chat_skip(text) == "a\nc\ne\n"


def test_strip_trailing_newline_consumed():
    text = "keep\n<!-- chat-skip:start -->\nremove\n<!-- chat-skip:end -->\nkeep2\n"
    result = strip_chat_skip(text)
    assert result == "keep\nkeep2\n"


# ── apply_chat_replace ───────────────────────────────────────────────────────

def test_replace_substitutes_next_line():
    text = "before\n<!-- chat-replace:my-key -->\noriginal\nafter\n"
    result = apply_chat_replace(text, {"my-key": "replacement"})
    assert result == "before\nreplacement\nafter\n"


def test_replace_unknown_key_leaves_line():
    text = "<!-- chat-replace:unknown -->\noriginal\n"
    result = apply_chat_replace(text, {"other": "x"})
    assert "original" in result


def test_replace_multiple_keys():
    text = "<!-- chat-replace:k1 -->\nold1\n<!-- chat-replace:k2 -->\nold2\n"
    result = apply_chat_replace(text, {"k1": "new1", "k2": "new2"})
    assert result == "new1\nnew2\n"


def test_replace_preserves_surrounding_content():
    text = "line1\n<!-- chat-replace:k -->\nreplaced\nline3\n"
    result = apply_chat_replace(text, {"k": "NEW"})
    assert result == "line1\nNEW\nline3\n"


# ── override_frontmatter ─────────────────────────────────────────────────────

def test_frontmatter_replaces_name():
    text = "---\nname: old-name\ndescription: \"old\"\n---\nbody\n"
    result = override_frontmatter(text, "new-name", "new desc")
    assert "name: new-name" in result
    assert "old-name" not in result


def test_frontmatter_replaces_description():
    text = "---\nname: s\ndescription: \"old description\"\n---\nbody\n"
    result = override_frontmatter(text, "s", "new description")
    assert "new description" in result
    assert "old description" not in result


def test_frontmatter_preserves_body():
    text = "---\nname: s\ndescription: \"d\"\n---\nbody content here\n"
    result = override_frontmatter(text, "s", "d2")
    assert "body content here" in result


# ── check_orphan_markers ─────────────────────────────────────────────────────

def test_orphan_unclosed_start():
    text = "text\n<!-- chat-skip:start -->\nunclosed\n"
    issues = check_orphan_markers(text)
    assert any("chat-skip:start" in i for i in issues)


def test_orphan_unmatched_end():
    text = "text\n<!-- chat-skip:end -->\n"
    issues = check_orphan_markers(text)
    assert any("chat-skip:end" in i for i in issues)


def test_orphan_unconsumed_replace():
    text = "<!-- chat-replace:leftover -->\n"
    issues = check_orphan_markers(text)
    assert any("chat-replace" in i for i in issues)


def test_no_orphans_clean_text():
    assert check_orphan_markers("clean text\n") == []
