"""Unit tests for transform_skill.py primitives."""
import zipfile
from pathlib import Path

import pytest
from transform_skill import (
    REQUIRED_EXCLUDES,
    apply_chat_replace,
    build_standalone_skill_zip,
    check_orphan_markers,
    override_frontmatter,
    strip_chat_skip,
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


def test_replace_indented_marker():
    # Markers inside indented code blocks (e.g. markdown list continuations) must match
    text = "before\n   <!-- chat-replace:my-key -->\noriginal\nafter\n"
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


def test_frontmatter_does_not_touch_body_yaml_example():
    # Regression: a fenced YAML example in the body must not be rewritten
    # just because it contains name: / description: lines.
    text = (
        "---\nname: real\ndescription: \"real\"\n---\n\n"
        "```yaml\nname: example\ndescription: \"shown in docs\"\n```\n"
    )
    result = override_frontmatter(text, "NEW", "NEW DESC")
    assert "name: NEW" in result
    assert "description: \"NEW DESC\"" in result
    # Body example untouched
    assert "name: example" in result
    assert 'description: "shown in docs"' in result


def test_frontmatter_no_frontmatter_returns_input_unchanged():
    text = "no frontmatter here\nname: foo\n"
    assert override_frontmatter(text, "x", "y") == text


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


# ── standalone_skill_config smoke tests ─────────────────────────────────────

def test_config_has_required_keys():
    from standalone_skill_config import SKILLS
    required = {
        "standalone_name", "standalone_description",
        "source_dir", "replacements", "exclude_dirs",
    }
    for name, cfg in SKILLS.items():
        missing = required - set(cfg)
        assert not missing, f"SKILLS['{name}'] missing keys: {missing}"


def test_assess_config_covers_all_markers():
    from standalone_skill_config import SKILLS
    from pathlib import Path
    import re
    in_file: set[str] = set()
    for md in Path("../skills/assess").rglob("*.md"):
        in_file |= set(re.findall(r"<!-- chat-replace:(\S+?) -->", md.read_text()))
    in_config = set(SKILLS["assess"]["replacements"])
    uncovered = in_file - in_config
    assert not uncovered, f"assess markers with no config entry: {uncovered}"


# ── build-level safety: REQUIRED_EXCLUDES always applied ───────────────────

def _zip_names(out_zip: Path) -> list[str]:
    with zipfile.ZipFile(out_zip) as zf:
        return zf.namelist()


def _make_fake_skill(root: Path) -> Path:
    """Build a synthetic skill source with tooling artefacts."""
    src = root / "fake_skill"
    src.mkdir()
    (src / "SKILL.md").write_text('---\nname: fake\ndescription: "fake"\n---\n# Body\n')
    (src / "__pycache__").mkdir()
    (src / "__pycache__" / "junk.pyc").write_bytes(b"junk")
    (src / ".pytest_cache").mkdir()
    (src / ".pytest_cache" / "v").write_text("cache")
    (src / ".venv").mkdir()
    (src / ".venv" / "pyvenv.cfg").write_text("home = /usr/bin")
    return src


def test_required_excludes_applied_even_when_caller_passes_empty_set(tmp_path):
    src = _make_fake_skill(tmp_path)
    out_zip = tmp_path / "fake.zip"
    issues = build_standalone_skill_zip(
        skill_source_dir=src,
        out_zip=out_zip,
        standalone_name="fake",
        standalone_description="fake desc",
        replacements={},
        exclude_dirs=frozenset(),  # caller opts out of all configured excludes
    )
    assert issues == []
    names = _zip_names(out_zip)
    for forbidden in REQUIRED_EXCLUDES:
        assert not any(forbidden in n for n in names), (
            f"{forbidden!r} leaked into ZIP: {names}"
        )


def test_huddle_config_has_no_orphan_replace_markers():
    from standalone_skill_config import SKILLS
    from pathlib import Path
    import re
    in_file: set[str] = set()
    for md in Path("../skills/huddle").rglob("*.md"):
        in_file |= set(re.findall(r"<!-- chat-replace:(\S+?) -->", md.read_text()))
    # huddle uses chat-skip+inline only; no chat-replace markers expected
    assert not in_file, f"unexpected chat-replace markers in huddle: {in_file}"
    assert SKILLS["huddle"]["replacements"] == {}, "huddle replacements should be empty"
