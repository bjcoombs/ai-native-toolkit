"""Tests for lib/instruction_discovery.py (issue #511): nested and path-scoped
instruction files are discovered, graded with their scope, measured into the
always-loaded budget, and checked for the four scope-integrity defects and
the informational mirror (issue #589)."""
from __future__ import annotations

import os
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

import lib.instruction_discovery as discovery
from lib.git_churn import tracked_files
from lib.instruction_files import nested_instruction_surface

ROOT_AGENTS = "\n".join(f"- Always run check number {i} before pushing." for i in range(8))


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _distinct(tag: str) -> str:
    return "\n".join(f"- {tag} rule {i}: use the {tag} toolchain." for i in range(6))


def _monorepo(repo: Path) -> None:
    """Every discovered shape once, with exactly four planted defects."""
    _write(repo, "CLAUDE.md", "# Root\n@AGENTS.md\n@docs/conventions.md\nUse `make test`.\n")
    _write(repo, "AGENTS.md", ROOT_AGENTS)
    _write(repo, "docs/conventions.md", "Conventions.\n")
    _write(repo, "packages/api/AGENTS.md", _distinct("api"))
    _write(repo, "packages/api/app.py", "print('api')\n")
    # Defect: repeats its parent (the root AGENTS.md).
    _write(repo, "packages/web/AGENTS.md", ROOT_AGENTS + "\n- Web only: use pnpm.")
    _write(repo, "packages/web/index.ts", "export {}\n")
    # Defect: CLAUDE.md shadows a different AGENTS.md without importing it.
    _write(repo, "packages/cli/CLAUDE.md", _distinct("cli-claude"))
    _write(repo, "packages/cli/AGENTS.md", _distinct("cli-agents"))
    _write(repo, ".claude/rules/style.md", "Prefer small functions.\n")
    _write(repo, ".claude/rules/api.md",
           "---\npaths:\n  - \"packages/api/**/*.py\"\n---\nAPI rules.\n")
    # Defect: a scope glob matching no tracked file.
    _write(repo, ".claude/rules/dead.md", "---\npaths:\n  - \"legacy/**/*.rb\"\n---\nRuby.\n")
    _write(repo, ".github/instructions/py.instructions.md",
           "---\napplyTo: \"**/*.py\"\n---\nPython rules.\n")
    _write(repo, ".cursor/rules/web.mdc",
           "---\ndescription: web\nglobs: packages/web/**/*.ts\nalwaysApply: false\n---\nWeb.\n")
    # Defect: a plain .md under .cursor/rules, which Cursor ignores.
    _write(repo, ".cursor/rules/notes.md", "Notes Cursor never reads.\n")
    # Excluded trees: vendored, fixtures, and a config-excluded archive.
    _write(repo, "vendor/lib/AGENTS.md", "vendored")
    _write(repo, "node_modules/x/CLAUDE.md", "dependency")
    _write(repo, "tests/fixtures/AGENTS.md", "fixture")
    _write(repo, "archive/AGENTS.md", "archived")


@pytest.fixture
def monorepo(git_repo: Any) -> Path:
    repo, commit = git_repo
    _monorepo(repo)
    commit("monorepo fixture")
    _write(repo, "packages/api/CLAUDE.md", "untracked personal notes")
    return repo


def _block(repo: Path, excludes: tuple[set[str], list[str]] | None = None) -> Any:
    return nested_instruction_surface(repo, excludes)


def test_monorepo_grades_each_file_with_scope(monorepo: Path) -> None:
    block = _block(monorepo, ({"archive"}, []))
    files = {f["path"]: f for f in block["files"]}
    assert sorted(files) == [
        ".claude/rules/api.md", ".claude/rules/dead.md", ".claude/rules/style.md",
        ".cursor/rules/web.mdc", ".github/instructions/py.instructions.md",
        "packages/api/AGENTS.md", "packages/cli/AGENTS.md", "packages/cli/CLAUDE.md",
        "packages/web/AGENTS.md",
    ]
    assert block["files_total"] == 9
    assert files["packages/api/AGENTS.md"]["scope"] == {
        "directory": "packages/api", "globs": [], "always_loaded": False}
    assert files["packages/api/AGENTS.md"]["tool"] == "agents_md"
    assert files[".claude/rules/api.md"]["scope"] == {
        "directory": ".", "globs": ["packages/api/**/*.py"], "always_loaded": False}
    assert files[".claude/rules/style.md"]["scope"]["always_loaded"] is True
    assert files[".github/instructions/py.instructions.md"]["tool"] == "copilot"
    assert files[".cursor/rules/web.mdc"]["scope"]["globs"] == ["packages/web/**/*.ts"]
    assert files[".cursor/rules/web.mdc"]["kind"] == "cursor_rule"
    for entry in files.values():
        assert entry["grade"] and isinstance(entry["score"], int)
        assert entry["line_count"] >= 1


def test_monorepo_one_finding_per_planted_defect(monorepo: Path) -> None:
    block = _block(monorepo, ({"archive"}, []))
    kinds = Counter(f["kind"] for f in block["findings"])
    assert kinds == {"dead_glob": 1, "ignored_cursor_md": 1,
                     "claude_shadows_agents": 1, "repeats_parent": 1}
    by_kind = {f["kind"]: f for f in block["findings"]}
    assert by_kind["dead_glob"]["path"] == ".claude/rules/dead.md"
    assert by_kind["dead_glob"]["pattern"] == "legacy/**/*.rb"
    assert by_kind["ignored_cursor_md"]["path"] == ".cursor/rules/notes.md"
    assert by_kind["claude_shadows_agents"]["path"] == "packages/cli/CLAUDE.md"
    assert by_kind["claude_shadows_agents"]["parent"] == "packages/cli/AGENTS.md"
    assert by_kind["repeats_parent"]["path"] == "packages/web/AGENTS.md"
    assert by_kind["repeats_parent"]["parent"] == "AGENTS.md"
    assert by_kind["repeats_parent"]["overlap"] == round(8 / 9, 3)
    assert block["findings_total"] == 4


def test_monorepo_budget(monorepo: Path) -> None:
    budget = _block(monorepo, ({"archive"}, []))["budget"]
    claude = budget["claude_code"]
    assert claude["files"] == ["CLAUDE.md", "AGENTS.md", "docs/conventions.md",
                               ".claude/rules/style.md"]
    assert claude["lines"] == 4 + 8 + 1 + 1
    assert claude["est_tokens"] > 0 and claude["skipped_imports"] == []
    codex = budget["codex"]
    assert codex["files"] == ["AGENTS.md"]
    assert codex["deepest_chain"] is not None
    assert codex["deepest_chain"]["directory"] == "packages/web"
    assert budget["chars_per_token"] == 4


def test_config_excludes_apply(monorepo: Path) -> None:
    paths = [f["path"] for f in _block(monorepo)["files"]]
    assert "archive/AGENTS.md" in paths
    assert "vendor/lib/AGENTS.md" not in paths
    assert "tests/fixtures/AGENTS.md" not in paths
    paths = [f["path"] for f in _block(monorepo, (set(), ["AGENTS.md"]))["files"]]
    assert not any(p.endswith("AGENTS.md") for p in paths)


def test_identical_or_imported_or_symlinked_agents_not_shadowed(git_repo: Any) -> None:
    repo, commit = git_repo
    _write(repo, "same/CLAUDE.md", ROOT_AGENTS)
    _write(repo, "same/AGENTS.md", ROOT_AGENTS + "\n")
    _write(repo, "imp/CLAUDE.md", "@AGENTS.md\n")
    _write(repo, "imp/AGENTS.md", _distinct("imp"))
    _write(repo, "link/AGENTS.md", _distinct("link"))
    os.symlink("AGENTS.md", repo / "link" / "CLAUDE.md")
    commit("aliases")
    block = _block(repo)
    assert [f for f in block["findings"] if f["kind"] == "claude_shadows_agents"] == []
    # A byte-identical copy is a hand-kept mirror; a symlink or an import is not.
    assert [(f["kind"], f["path"], f["parent"]) for f in block["findings"]] == [
        ("mirrored_instruction_files", "same/CLAUDE.md", "same/AGENTS.md")]


def _mirror(title: str, comment: str, body: str) -> str:
    """The shape of a documented mirror: H1, blank, multi-line comment, body."""
    return f"# {title}\n\n<!--\n  {comment}\n  Keep both files identical below the H1.\n-->\n\n{body}\n"


MIRROR_BODY = "## Required reading\n\n" + ROOT_AGENTS + "\n\n## Build\nRun `make check`.\n"


def test_mirror_differing_only_in_header_is_informational(git_repo: Any) -> None:
    repo, commit = git_repo
    _write(repo, "CLAUDE.md", _mirror("Project notes", "Read by Claude Code.", MIRROR_BODY))
    _write(repo, "AGENTS.md", _mirror("AGENTS.md", "Read by agents.md tools.", MIRROR_BODY))
    commit("documented mirror")
    findings = _block(repo)["findings"]
    assert [(f["kind"], f["path"], f["parent"]) for f in findings] == [
        ("mirrored_instruction_files", "CLAUDE.md", "AGENTS.md")]
    assert "diff_lines" not in findings[0]
    assert "drift" not in findings[0]["detail"]
    assert "header comments differ" in findings[0]["detail"]


def test_mirror_with_one_changed_body_line_still_shadows_with_count(git_repo: Any) -> None:
    repo, commit = git_repo
    changed = MIRROR_BODY.replace("Run `make check`.", "Run `make test`.")
    _write(repo, "CLAUDE.md", _mirror("Project notes", "Read by Claude Code.", MIRROR_BODY))
    _write(repo, "AGENTS.md", _mirror("AGENTS.md", "Read by agents.md tools.", changed))
    commit("diverged mirror")
    findings = _block(repo)["findings"]
    assert [(f["kind"], f["path"]) for f in findings] == [("claude_shadows_agents", "CLAUDE.md")]
    assert findings[0]["diff_lines"] == 1
    assert "1 line(s)" in findings[0]["detail"]


def test_mirror_lines_strips_only_the_leading_header() -> None:
    body = "## A\n# Not a title\n<!-- inline -->\ntext"
    assert discovery.mirror_lines("<!-- c -->\n# T\n\n<!-- d\n e -->\n" + body) == [
        "## A", "# Not a title", "<!-- inline -->", "text"]
    # An unterminated comment is body, not a header that swallows the file.
    assert discovery.mirror_lines("# T\n<!-- open\nrule one") == ["<!-- open", "rule one"]
    # Whitespace collapses and blank lines go, so reflowed spacing still matches.
    assert discovery.mirror_lines("a   b\n\n\n  c ") == ["a b", "c"]


def test_text_after_a_closing_comment_is_body() -> None:
    assert discovery.mirror_lines("# T\n<!-- sync -->Always run make check.\nrest") == [
        "Always run make check.", "rest"]
    assert discovery.mirror_lines("<!--\nnote\n--> keep this\nrest") == ["keep this", "rest"]
    # A tail that opens another comment is still header.
    assert discovery.mirror_lines("<!-- a --><!-- b -->\nrest") == ["rest"]
    # A tail ends the header, so an H1 after `-->` is body too (issue #632).
    assert discovery.mirror_lines("<!-- a --> # Title\nbody") == ["# Title", "body"]
    # `<!-->` never closes the comment it opens, so it is body, not a crash.
    assert discovery.mirror_lines("<!-->\nrest") == ["<!-->", "rest"]


def test_differing_text_after_comment_close_is_not_a_mirror(git_repo: Any) -> None:
    repo, commit = git_repo
    _write(repo, "CLAUDE.md", "# C\n<!-- sync -->Run make check.\n" + MIRROR_BODY)
    _write(repo, "AGENTS.md", "# A\n<!-- sync -->Run make test.\n" + MIRROR_BODY)
    commit("tail differs")
    findings = _block(repo)["findings"]
    assert [(f["kind"], f.get("diff_lines")) for f in findings] == [("claude_shadows_agents", 1)]


def test_extra_instruction_in_one_files_leading_comment_is_not_a_mirror(git_repo: Any) -> None:
    # Agents read raw markdown, so a comment only one file carries is an instruction
    # the other tool never sees (issue #632).
    repo, commit = git_repo
    _write(repo, "CLAUDE.md", "<!-- Agents: always run make check -->\n"
           + _mirror("Project notes", "Read by Claude Code.", MIRROR_BODY))
    _write(repo, "AGENTS.md", _mirror("AGENTS.md", "Read by agents.md tools.", MIRROR_BODY))
    commit("hidden instruction")
    findings = _block(repo)["findings"]
    assert [(f["kind"], f.get("diff_lines")) for f in findings] == [("claude_shadows_agents", 1)]


def test_comment_in_only_one_file_is_compared(git_repo: Any) -> None:
    repo, commit = git_repo
    _write(repo, "CLAUDE.md", "# C\n<!-- Always run make check. -->\n" + MIRROR_BODY)
    _write(repo, "AGENTS.md", "# A\n" + MIRROR_BODY)
    commit("comment on one side")
    assert [f["kind"] for f in _block(repo)["findings"]] == ["claude_shadows_agents"]


def test_shared_header_comment_mirror_says_same_instructions(git_repo: Any) -> None:
    repo, commit = git_repo
    _write(repo, "CLAUDE.md", "# C\n<!-- Keep in sync. -->\n" + MIRROR_BODY)
    _write(repo, "AGENTS.md", "# A\n<!--   Keep in sync. -->\n" + MIRROR_BODY)
    commit("identical comments")
    findings = _block(repo)["findings"]
    assert [f["kind"] for f in findings] == ["mirrored_instruction_files"]
    assert "both tools read the same instructions" in findings[0]["detail"]


def test_mirror_pair_strips_shared_and_paired_comments_only() -> None:
    a = "<!-- shared -->\n<!-- note a -->\nbody"
    b = "<!-- note b -->\n<!-- shared -->\n<!-- extra -->\nbody"
    assert discovery.mirror_pair(a, b) == (["body"], ["<!-- extra -->", "body"], True)
    assert discovery.mirror_pair("<!-- s -->\nx", "<!-- s -->\nx") == (["x"], ["x"], False)


def test_only_one_differing_note_per_side_is_paired_away() -> None:
    # Three unshared comments a side: one note pairs off, the other two are compared.
    a = "<!-- note a -->\n<!-- Run make check. -->\n<!-- Use uv. -->\nbody"
    b = "<!-- note b -->\n<!-- Run make test. -->\n<!-- Use pip. -->\nbody"
    c_lines, a_lines, differ = discovery.mirror_pair(a, b)
    assert differ
    assert c_lines == ["<!-- Run make check. -->", "<!-- Use uv. -->", "body"]
    assert a_lines == ["<!-- Run make test. -->", "<!-- Use pip. -->", "body"]


def test_three_differing_comments_a_side_is_not_a_mirror(git_repo: Any) -> None:
    repo, commit = git_repo
    head = "<!-- note {} -->\n<!-- Run make {}. -->\n<!-- Use {}. -->\n"
    _write(repo, "CLAUDE.md", "# C\n" + head.format("c", "check", "uv") + MIRROR_BODY)
    _write(repo, "AGENTS.md", "# A\n" + head.format("a", "test", "pip") + MIRROR_BODY)
    commit("three comments a side")
    findings = _block(repo)["findings"]
    assert [(f["kind"], f.get("diff_lines")) for f in findings] == [("claude_shadows_agents", 2)]
    assert "header comments only one file carries included" in findings[0]["detail"]


def test_findings_cap_truncates_mirrors_before_defects(
    git_repo: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, commit = git_repo
    monkeypatch.setattr(discovery, "MAX_FINDINGS", 3)
    for n in range(3):
        _write(repo, f".cursor/rules/stray{n}.md", "stray")
    _write(repo, "CLAUDE.md", ROOT_AGENTS)
    _write(repo, "AGENTS.md", ROOT_AGENTS)
    commit("defects plus a mirror")
    block = _block(repo)
    assert block["findings_total"] == 4
    assert [f["kind"] for f in block["findings"]] == ["ignored_cursor_md"] * 3


def test_diff_line_count() -> None:
    assert discovery.diff_line_count(["a", "b", "c"], ["a", "b", "c"]) == 0
    assert discovery.diff_line_count(["a", "b", "c"], ["a", "x", "c"]) == 1
    assert discovery.diff_line_count(["a", "b"], ["a", "b", "c", "d"]) == 2


def test_empty_pair_reports_nothing(git_repo: Any) -> None:
    repo, commit = git_repo
    _write(repo, "pkg/CLAUDE.md", "# Title only\n")
    _write(repo, "pkg/AGENTS.md", "")
    commit("empty pair")
    assert _block(repo)["findings"] == []


def test_root_shadowing_reported(git_repo: Any) -> None:
    repo, commit = git_repo
    _write(repo, "CLAUDE.md", "Claude rules.")
    _write(repo, "AGENTS.md", "Different agent rules.")
    commit("root pair")
    findings = _block(repo)["findings"]
    assert [(f["kind"], f["path"]) for f in findings] == [("claude_shadows_agents", "CLAUDE.md")]
    assert findings[0]["diff_lines"] == 1


def test_agents_only_root_feeds_claude_budget_and_override_wins_for_codex(git_repo: Any) -> None:
    repo, commit = git_repo
    _write(repo, "AGENTS.md", "root agents")
    _write(repo, "AGENTS.override.md", "override")
    _write(repo, "pkg/AGENTS.md", "")
    commit("agents only")
    block = _block(repo)
    assert block["budget"]["claude_code"]["files"] == ["AGENTS.md"]
    codex = block["budget"]["codex"]
    assert codex["files"] == ["AGENTS.override.md"]
    # An empty nested AGENTS.md adds nothing Codex reads.
    assert codex["deepest_chain"] is None
    files = {f["path"]: f for f in block["files"]}
    assert files["AGENTS.override.md"]["tool"] == "codex"
    assert files["AGENTS.override.md"]["scope"]["always_loaded"] is True


def test_dot_claude_claude_md_is_root_scope(git_repo: Any) -> None:
    repo, commit = git_repo
    _write(repo, ".claude/CLAUDE.md", "Project memory in .claude.")
    _write(repo, "pkg/.claude/CLAUDE.md", "Package memory.")
    commit("dot-claude memory")
    block = _block(repo)
    files = {f["path"]: f for f in block["files"]}
    assert files[".claude/CLAUDE.md"]["scope"]["directory"] == "."
    assert files["pkg/.claude/CLAUDE.md"]["scope"]["directory"] == "pkg"
    assert block["budget"]["claude_code"]["files"] == [".claude/CLAUDE.md"]


def test_scope_flags_for_copilot_cursor_and_nested_rules(git_repo: Any) -> None:
    repo, commit = git_repo
    _write(repo, ".github/instructions/all.instructions.md", "---\napplyTo: \"**\"\n---\nAll.")
    _write(repo, ".github/instructions/none.instructions.md", "No frontmatter.")
    _write(repo, ".github/instructions/README.md", "not an instructions file")
    _write(repo, ".cursor/rules/always.mdc", "---\nalwaysApply: true\n---\nAlways.")
    _write(repo, ".cursor/rules/other.txt", "ignored silently")
    _write(repo, "pkg/.claude/rules/local.md", "---\npaths: [\"src/**\"]\n---\nLocal.")
    _write(repo, "pkg/.claude/rules/unscoped.md", "Nested unscoped.")
    _write(repo, "pkg/.cursor/rules/x.mdc", "---\nglobs: \"*.py\"\n---\nX.")
    _write(repo, "pkg/src/a.py", "a = 1\n")
    commit("scopes")
    block = _block(repo)
    files = {f["path"]: f for f in block["files"]}
    assert set(files) == {
        ".github/instructions/all.instructions.md", ".github/instructions/none.instructions.md",
        ".cursor/rules/always.mdc", "pkg/.claude/rules/local.md",
        "pkg/.claude/rules/unscoped.md", "pkg/.cursor/rules/x.mdc",
    }
    assert files[".github/instructions/all.instructions.md"]["scope"]["always_loaded"]
    assert not files[".github/instructions/none.instructions.md"]["scope"]["always_loaded"]
    assert files[".cursor/rules/always.mdc"]["scope"]["always_loaded"]
    assert files["pkg/.claude/rules/local.md"]["scope"]["directory"] == "pkg"
    # Only root .claude/rules load at launch for the root context.
    assert not files["pkg/.claude/rules/unscoped.md"]["scope"]["always_loaded"]
    assert block["budget"]["claude_code"]["files"] == []
    # `src/**` resolves against the rule's own directory, so it is not dead.
    assert block["findings"] == []


def test_short_nested_file_is_not_a_repeat(git_repo: Any) -> None:
    repo, commit = git_repo
    _write(repo, "AGENTS.md", ROOT_AGENTS)
    _write(repo, "pkg/AGENTS.md", "\n".join(ROOT_AGENTS.splitlines()[:4]))
    _write(repo, "pkg/deeper/GEMINI.md", _distinct("deep"))
    commit("short nested")
    assert _block(repo)["findings"] == []


def test_caps_files_and_findings(git_repo: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, commit = git_repo
    for i in range(3):
        _write(repo, f".claude/rules/r{i}.md", f"---\npaths: [\"none{i}/**\"]\n---\nR.")
    commit("dead rules")
    monkeypatch.setattr(discovery, "MAX_FILES", 2)
    monkeypatch.setattr(discovery, "MAX_FINDINGS", 1)
    block = _block(repo)
    assert len(block["files"]) == 2 and block["files_total"] == 3
    assert len(block["findings"]) == 1 and block["findings_total"] == 3


def test_non_git_tree_uses_filesystem(tmp_path: Path) -> None:
    _monorepo(tmp_path)
    tracked_files.cache_clear()
    block = _block(tmp_path, ({"archive"}, []))
    assert block["files_total"] == 9
    kinds = Counter(f["kind"] for f in block["findings"])
    assert kinds["dead_glob"] == 1 and kinds["ignored_cursor_md"] == 1


def test_run_context_carries_block(git_repo: Any) -> None:
    from assess_core import build_run_context
    from assess_core_helpers import _seed_assess

    repo, commit = git_repo
    _seed_assess(repo)
    _monorepo(repo)
    commit("monorepo")
    ctx = build_run_context(repo_root=repo, run_date="2026-10-09")
    block = ctx["nested_instructions"]
    assert block["available"] is True
    assert block["budget"]["claude_code"]["files"][0] == "CLAUDE.md"
    assert "CLAUDE.md" in ctx["instruction_files"]


def test_empty_override_falls_back_to_agents_md(git_repo: Any) -> None:
    repo, commit = git_repo
    _write(repo, "AGENTS.md", "root agents")
    _write(repo, "AGENTS.override.md", "   \n")
    commit("empty override")
    assert _block(repo)["budget"]["codex"]["files"] == ["AGENTS.md"]


def test_glob_matching_symlink_or_untracked_tree_is_not_dead(git_repo: Any) -> None:
    repo, commit = git_repo
    _write(repo, "real/target.py", "x = 1\n")
    (repo / "links").mkdir()
    os.symlink("../real/target.py", repo / "links" / "alias.py")
    _write(repo, ".gitignore", "dist/\n")
    _write(repo, "dist/bundle.js", "built")
    _write(repo, ".claude/rules/link.md", "---\npaths: [\"links/*.py\"]\n---\nLinks.")
    _write(repo, ".claude/rules/dist.md", "---\npaths: [\"./dist/**/*.js\"]\n---\nBuilt.")
    _write(repo, ".claude/rules/gone.md", "---\npaths: [\"gone/**/*.js\"]\n---\nGone.")
    _write(repo, ".claude/rules/up.md", "---\npaths: [\"../real/*.py\"]\n---\nUp.")
    commit("symlink and ignored tree")
    dead = sorted(f["pattern"] for f in _block(repo)["findings"] if f["kind"] == "dead_glob")
    assert dead == ["../real/*.py", "gone/**/*.js"]


def test_dead_glob_is_per_rule_and_skips_always_loaded(git_repo: Any) -> None:
    repo, commit = git_repo
    _write(repo, "src/a.ts", "export {}\n")
    _write(repo, ".claude/rules/mixed.md", "---\npaths: [\"**/*.ts\", \"**/*.tsx\"]\n---\nTS.")
    _write(repo, ".claude/rules/allgone.md", "---\npaths: [\"x/**/*.rb\", \"y/*.go\"]\n---\nNo.")
    _write(repo, ".cursor/rules/always.mdc",
           "---\nglobs: legacy/**/*.rb\nalwaysApply: true\n---\nAlways.")
    commit("multi-pattern rules")
    dead = [f for f in _block(repo)["findings"] if f["kind"] == "dead_glob"]
    assert [(f["path"], f["pattern"]) for f in dead] == [
        (".claude/rules/allgone.md", "x/**/*.rb, y/*.go")]


def test_nested_files_grade_against_the_repo(git_repo: Any) -> None:
    """A nested file is graded with the repository context (#512): its
    resolvable commands earn credit and its stale references are findings,
    exactly as at the root."""
    repo, commit = git_repo
    _write(repo, "packages/api/package.json", '{"scripts": {"test": "vitest"}}')
    _write(repo, "packages/api/src/app.ts", "export {}\n")
    _write(repo, "packages/api/AGENTS.md",
           "Run `npm test` and `npm run deploy`.\nCode is in `src/app.ts`, not `src/old.ts`.\n")
    commit("nested")
    block = _block(repo)
    entry = next(f for f in block["files"] if f["path"] == "packages/api/AGENTS.md")
    assert entry["subscores"]["verified_commands"] == 1
    assert entry["subscores"]["path_references_existing"] == 1
    assert [(f["kind"], f["reference"]) for f in entry["findings"]] == [
        ("unresolved_command", "npm run deploy"), ("stale_path", "src/old.ts"),
    ]


def test_nested_file_commands_resolve_from_its_directory(git_repo: Any) -> None:
    """A nested file's local script resolves from its own directory as well as
    the root, so neither reading is a false finding; a script in neither is."""
    repo, commit = git_repo
    _write(repo, "packages/api/scripts/dev.sh", "echo dev\n")
    _write(repo, "scripts/root.sh", "echo root\n")
    _write(repo, "packages/api/AGENTS.md",
           "```bash\n./scripts/dev.sh\nbash scripts/root.sh\n./scripts/gone.sh\n```\n")
    commit("nested scripts")
    entry = next(f for f in _block(repo)["files"] if f["path"] == "packages/api/AGENTS.md")
    assert entry["subscores"]["verified_commands"] == 2
    assert [(f["kind"], f["reference"]) for f in entry["findings"]] == [
        ("unresolved_command", "./scripts/gone.sh"),
    ]
