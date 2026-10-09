"""Tests for lib/instruction_budget.py: @import discovery and resolution, and the
always-loaded instruction budget for Claude Code and Codex."""
from __future__ import annotations

from pathlib import Path

import pytest

import lib.instruction_budget as budget
from lib.instruction_budget import (
    CODEX_MAX_BYTES,
    claude_code_budget,
    codex_budget,
    find_imports,
    follow_imports,
    resolve_import,
    totals,
)


def _all_tracked(_p: Path) -> bool:
    return True


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_find_imports_skips_code_mentions_and_emails() -> None:
    text = (
        "See @docs/a.md and (@./b.md), also @AGENTS.md.\n"
        "Mail me at dev@example.com or ping @someone.\n"
        "Inline `@not/this.md` is code.\n"
        "```\n@fenced/c.md\n```\n"
        "~~~\n@tilde/d.md\n~~~\n"
        "@last/e.md\n"
    )
    assert find_imports(text) == ["docs/a.md", "./b.md", "AGENTS.md", "last/e.md"]


def test_resolve_import_relative_to_importing_file(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/notes/x.md", "x")
    assert resolve_import("pkg/CLAUDE.md", "notes/x.md", tmp_path, _all_tracked) == (
        "pkg/notes/x.md", "")


@pytest.mark.parametrize(
    ("target", "reason"),
    [
        ("~/.claude/personal.md", "outside repository"),
        ("../../outside.md", "outside repository"),
        ("/etc/hosts", "outside repository"),
        ("missing.md", "missing"),
    ],
)
def test_resolve_import_skips(tmp_path: Path, target: str, reason: str) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "outside.md").write_text("x", encoding="utf-8")
    assert resolve_import("CLAUDE.md", target, repo, _all_tracked) == (None, reason)


def test_resolve_import_untracked_and_absolute_inside(tmp_path: Path) -> None:
    _write(tmp_path, "local.md", "x")
    assert resolve_import("CLAUDE.md", "local.md", tmp_path, lambda p: False) == (
        None, "untracked")
    assert resolve_import("CLAUDE.md", str(tmp_path / "local.md"), tmp_path,
                          _all_tracked) == ("local.md", "")


def test_follow_imports_guards_cycles(tmp_path: Path) -> None:
    _write(tmp_path, "CLAUDE.md", "@a.md")
    _write(tmp_path, "a.md", "@b.md @CLAUDE.md")
    _write(tmp_path, "b.md", "@a.md @gone.md")
    loaded, skipped = follow_imports(["CLAUDE.md"], tmp_path, _all_tracked)
    assert loaded == ["CLAUDE.md", "a.md", "b.md"]
    assert skipped == [{"source": "b.md", "target": "gone.md", "reason": "missing"}]


def test_follow_imports_stops_after_four_hops(tmp_path: Path) -> None:
    _write(tmp_path, "CLAUDE.md", "@f1.md")
    for i in range(1, 8):
        _write(tmp_path, f"f{i}.md", f"@f{i + 1}.md")
    loaded, _ = follow_imports(["CLAUDE.md"], tmp_path, _all_tracked)
    assert loaded == ["CLAUDE.md", "f1.md", "f2.md", "f3.md", "f4.md"]


def test_follow_imports_caps_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(budget, "MAX_SKIPPED_IMPORTS", 2)
    _write(tmp_path, "CLAUDE.md", "@x/1.md @x/2.md @x/3.md")
    _loaded, skipped = follow_imports(["CLAUDE.md"], tmp_path, _all_tracked)
    assert [s["target"] for s in skipped] == ["x/1.md", "x/2.md"]


def test_totals_counts_lines_words_bytes_tokens(tmp_path: Path) -> None:
    _write(tmp_path, "a.md", "one two\nthree\n")
    _write(tmp_path, "b.md", "é\n")
    t = totals(tmp_path, ["a.md", "b.md", "absent.md"])
    assert t == {"files": ["a.md", "b.md", "absent.md"], "lines": 3, "words": 4,
                 "bytes": 14 + 3, "est_tokens": 4}


def test_claude_code_budget_adds_unscoped_rules_once(tmp_path: Path) -> None:
    _write(tmp_path, "CLAUDE.md", "@.claude/rules/style.md\nroot")
    _write(tmp_path, ".claude/rules/style.md", "style")
    _write(tmp_path, ".claude/rules/other.md", "other")
    b = claude_code_budget(tmp_path, ["CLAUDE.md"],
                           [".claude/rules/style.md", ".claude/rules/other.md"], _all_tracked)
    assert b["files"] == ["CLAUDE.md", ".claude/rules/style.md", ".claude/rules/other.md"]
    assert b["skipped_imports"] == []


def test_claude_code_budget_empty(tmp_path: Path) -> None:
    b = claude_code_budget(tmp_path, [], [], _all_tracked)
    assert (b["files"], b["bytes"], b["est_tokens"]) == ([], 0, 0)


def test_codex_budget_root_and_deepest_chain(tmp_path: Path) -> None:
    _write(tmp_path, "AGENTS.md", "r" * 100)
    _write(tmp_path, "a/AGENTS.md", "a" * 10)
    _write(tmp_path, "a/b/AGENTS.md", "b" * (CODEX_MAX_BYTES))
    _write(tmp_path, "c/AGENTS.md", "c" * 50)
    b = codex_budget(tmp_path, {".": "AGENTS.md", "a": "a/AGENTS.md",
                                "a/b": "a/b/AGENTS.md", "c": "c/AGENTS.md"})
    assert b["files"] == ["AGENTS.md"]
    assert b["bytes"] == 100 and not b["exceeds_limit"]
    chain = b["deepest_chain"]
    assert chain is not None
    assert chain["directory"] == "a/b"
    assert chain["files"] == ["AGENTS.md", "a/AGENTS.md", "a/b/AGENTS.md"]
    assert chain["bytes"] == 110 + CODEX_MAX_BYTES and chain["exceeds_limit"]


def test_codex_budget_without_files(tmp_path: Path) -> None:
    b = codex_budget(tmp_path, {})
    assert b["files"] == [] and b["deepest_chain"] is None and b["limit_bytes"] == 32768


def test_codex_budget_root_over_limit_and_nested_without_root(tmp_path: Path) -> None:
    _write(tmp_path, "AGENTS.md", "x" * (CODEX_MAX_BYTES + 1))
    assert codex_budget(tmp_path, {".": "AGENTS.md"})["exceeds_limit"]
    _write(tmp_path, "p/AGENTS.md", "p")
    chain = codex_budget(tmp_path, {"p": "p/AGENTS.md"})["deepest_chain"]
    assert chain is not None and chain["files"] == ["p/AGENTS.md"]
