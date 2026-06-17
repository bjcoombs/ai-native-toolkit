"""Contract suite for the ownership-map parser.

The parser turns the two declaration formats - GitHub ``CODEOWNERS`` (glob ->
owner) and a boundary-declaring markdown doc (``ARCHITECTURE.md`` / a seam-map
``README.md``) - into ``{declared_boundary: {matched_file_paths}}`` maps, and
flags the globs that already match zero files. These tests pin that behaviour
plus the two contracts the structure-drift signals depend on: deterministic,
byte-identical output run to run, and honest degradation (missing / malformed
input never crashes the assessment).

Fixtures build small repos in ``tmp_path``. CODEOWNERS resolution is against the
*tracked* file set, so a fixture that asserts on matched files commits them; a
fixture that only exercises parsing of patterns need not. Ambient git config is
neutralised process-wide by the package ``conftest.py``.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from lib.ownership_parser import (
    find_empty_globs,
    is_glob,
    parse_architecture_md,
    parse_codeowners,
    parse_ownership,
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args],
                   check=True, capture_output=True, text=True,
                   env={**os.environ})


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "dev@example.com")
    _git(repo, "config", "user.name", "Dev")
    return repo


def _write(repo: Path, rel: str, text: str = "x\n") -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _commit_all(repo: Path, message: str = "c") -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


def _matched_strs(files: set[Path]) -> set[str]:
    return {p.as_posix() for p in files}


# --- 1. CODEOWNERS parsing ---------------------------------------------------

def test_codeowners_multi_owner_comments_and_globs(tmp_path: Path) -> None:
    """A CODEOWNERS with multi-owner lines, comments, and globs parses cleanly.

    The parser keeps the glob pattern (the declared boundary), drops comment and
    blank lines, and ignores the owner tokens. Patterns are resolved against the
    tracked file set: ``*.js`` claims the two committed JS files, ``docs/`` claims
    everything under ``docs``, and ``src/**/*.py`` claims the nested python file.
    """
    repo = _init_repo(tmp_path)
    _write(repo, "a.js")
    _write(repo, "b.js")
    _write(repo, "src/pkg/mod.py")
    _write(repo, "docs/guide.md")
    _write(repo, "docs/api/ref.md")
    _write(repo, "README.md")
    _write(repo, "CODEOWNERS", "\n".join([
        "# top comment",
        "",
        "*.js   @frontend @web-team",
        "docs/  @docs-team",
        "src/**/*.py  @backend",
        "  # indented comment",
    ]) + "\n")
    _commit_all(repo)

    owners = parse_codeowners(repo)

    assert set(owners) == {"*.js", "docs/", "src/**/*.py"}
    assert _matched_strs(owners["*.js"]) == {"a.js", "b.js"}
    assert _matched_strs(owners["docs/"]) == {"docs/guide.md", "docs/api/ref.md"}
    assert _matched_strs(owners["src/**/*.py"]) == {"src/pkg/mod.py"}


def test_codeowners_anchored_and_bare_pattern_depth(tmp_path: Path) -> None:
    """A leading ``/`` anchors to root; a bare name matches at any depth.

    ``/config.yml`` matches only the root file, not the nested one. A bare
    ``Makefile`` (no slash, unanchored) matches at any depth - GitHub semantics.
    """
    repo = _init_repo(tmp_path)
    _write(repo, "config.yml")
    _write(repo, "sub/config.yml")
    _write(repo, "Makefile")
    _write(repo, "tools/Makefile")
    _write(repo, "CODEOWNERS", "/config.yml @a\nMakefile @b\n")
    _commit_all(repo)

    owners = parse_codeowners(repo)
    assert _matched_strs(owners["/config.yml"]) == {"config.yml"}
    assert _matched_strs(owners["Makefile"]) == {"Makefile", "tools/Makefile"}


def test_codeowners_duplicate_pattern_is_unioned(tmp_path: Path) -> None:
    """The same pattern on two lines unions its match set rather than overwriting."""
    repo = _init_repo(tmp_path)
    _write(repo, "x.py")
    _write(repo, "CODEOWNERS", "*.py @a\n*.py @b\n")
    _commit_all(repo)

    owners = parse_codeowners(repo)
    assert set(owners) == {"*.py"}
    assert _matched_strs(owners["*.py"]) == {"x.py"}


def test_codeowners_respects_gitignore(tmp_path: Path) -> None:
    """An ignored file never counts toward a glob's match set.

    ``tracked_files`` is the file universe, so a ``*.py`` glob over a repo with a
    gitignored ``secret.py`` claims only the tracked python file.
    """
    repo = _init_repo(tmp_path)
    _write(repo, ".gitignore", "secret.py\n")
    _write(repo, "kept.py")
    _write(repo, "secret.py")  # ignored, never tracked
    _write(repo, "CODEOWNERS", "*.py @a\n")
    _commit_all(repo)

    owners = parse_codeowners(repo)
    assert _matched_strs(owners["*.py"]) == {"kept.py"}


def test_codeowners_in_github_dir_is_found(tmp_path: Path) -> None:
    """``.github/CODEOWNERS`` is honoured - GitHub's standard location."""
    repo = _init_repo(tmp_path)
    _write(repo, "a.py")
    _write(repo, ".github/CODEOWNERS", "*.py @a\n")
    _commit_all(repo)

    owners = parse_codeowners(repo)
    assert _matched_strs(owners["*.py"]) == {"a.py"}


# --- 2. ARCHITECTURE.md parsing ----------------------------------------------

def test_architecture_md_module_extraction(tmp_path: Path) -> None:
    """Section headers name modules; their path references resolve to files.

    Two ``##`` sections each declare a module owning a directory and naming files
    in inline code. The parser attributes each section's references to that
    section's header (keyed ``<doc>::<header>``) and resolves directory and bare
    references to the tracked files.
    """
    repo = _init_repo(tmp_path)
    _write(repo, "src/api/server.py")
    _write(repo, "src/api/routes.py")
    _write(repo, "src/db/models.py")
    _write(repo, "ARCHITECTURE.md", "\n".join([
        "# System",
        "",
        "## API layer",
        "The API module owns `src/api/` and exposes the HTTP surface.",
        "",
        "## Data layer",
        "The data module owns `src/db/models.py`.",
    ]) + "\n")
    _commit_all(repo)

    modules = parse_architecture_md(repo)
    api = modules["ARCHITECTURE.md::API layer"]
    data = modules["ARCHITECTURE.md::Data layer"]
    assert _matched_strs(api) == {"src/api/server.py", "src/api/routes.py"}
    assert _matched_strs(data) == {"src/db/models.py"}


def test_architecture_md_ignores_fenced_code_paths(tmp_path: Path) -> None:
    """Paths inside a fenced code block are samples, not boundary declarations.

    A fenced listing of a path the module does *not* own must not be attributed
    to it - only the inline-code reference in prose counts.
    """
    repo = _init_repo(tmp_path)
    _write(repo, "src/real.py")
    _write(repo, "examples/sample.py")
    _write(repo, "DESIGN.md", "\n".join([
        "## Core",
        "The core module owns `src/real.py`.",
        "",
        "```",
        "examples/sample.py  # illustrative only",
        "```",
    ]) + "\n")
    _commit_all(repo)

    modules = parse_architecture_md(repo)
    core = modules["DESIGN.md::Core"]
    assert _matched_strs(core) == {"src/real.py"}
    assert all("sample.py" not in str(p) for files in modules.values() for p in files)


def test_architecture_readme_only_when_it_declares_boundaries(tmp_path: Path) -> None:
    """A generic README is skipped; a seam-declaring README is parsed.

    A README without ownership/seam vocabulary contributes no module. A README
    whose prose declares module ownership does, so a repo carrying its boundary
    map in a README (rather than ARCHITECTURE.md) is still read.
    """
    repo = _init_repo(tmp_path)
    _write(repo, "lib/core.py")
    _write(repo, "plain/README.md", "Just a project. Run `make`.\n")
    _write(repo, "lib/README.md", "\n".join([
        "## Module reference",
        "The core module owns `lib/core.py` - this seam is owned by design.",
    ]) + "\n")
    _commit_all(repo)

    modules = parse_architecture_md(repo)
    keys = set(modules)
    assert any(k.startswith("lib/README.md::") for k in keys)
    assert not any(k.startswith("plain/README.md::") for k in keys)


def test_architecture_wikilink_references_resolve(tmp_path: Path) -> None:
    """A ``[[wikilink]]`` path reference resolves to its tracked file."""
    repo = _init_repo(tmp_path)
    _write(repo, "docs/payments.md")
    _write(repo, "ARCHITECTURE.md", "\n".join([
        "## Payments",
        "The payments module owns [[docs/payments.md]].",
    ]) + "\n")
    _commit_all(repo)

    modules = parse_architecture_md(repo)
    assert _matched_strs(modules["ARCHITECTURE.md::Payments"]) == {"docs/payments.md"}


# --- 3. Empty-glob detection -------------------------------------------------

def test_find_empty_globs_flags_zero_match_patterns(tmp_path: Path) -> None:
    """A glob matching no tracked file is reported; a matching one is not.

    ``*.py`` matches the committed file; ``legacy/**`` matches nothing (the
    directory is gone). Only the latter surfaces, with its declared source.
    """
    repo = _init_repo(tmp_path)
    _write(repo, "a.py")
    _write(repo, "CODEOWNERS", "*.py @a\nlegacy/** @b\n")
    _commit_all(repo)

    owners = parse_codeowners(repo)
    empties = find_empty_globs(owners)
    assert empties == [{"pattern": "legacy/**", "declared_in": "CODEOWNERS"}]


def test_find_empty_globs_is_sorted(tmp_path: Path) -> None:
    """Empty globs are returned sorted by pattern for deterministic output."""
    owners = {
        "z/**": set(),
        "a/**": set(),
        "m.py": {Path("m.py")},  # matches - excluded
        "k/**": set(),
    }
    empties = find_empty_globs(owners)
    assert [e["pattern"] for e in empties] == ["a/**", "k/**", "z/**"]


def test_is_glob_classification() -> None:
    """Wildcard and directory patterns are globs; a literal file path is not."""
    assert is_glob("*.py")
    assert is_glob("src/**/*.ts")
    assert is_glob("docs/")  # trailing slash = directory
    assert is_glob("a[bc].py")
    assert not is_glob("README.md")
    assert not is_glob("src/main.py")


# --- 4. Graceful degradation -------------------------------------------------

def test_no_ownership_map_degrades(tmp_path: Path) -> None:
    """A repo with no CODEOWNERS and no boundary doc reports no ownership map."""
    repo = _init_repo(tmp_path)
    _write(repo, "a.py")
    _commit_all(repo)

    assert parse_codeowners(repo) == {}
    assert parse_architecture_md(repo) == {}

    summary = parse_ownership(repo)
    assert summary["available"] is False
    assert summary["reason"] == "no ownership map"
    assert summary["codeowners_globs"] == []
    assert summary["architecture_modules"] == []
    assert summary["empty_globs"] == []


def test_malformed_codeowners_line_is_skipped(tmp_path: Path) -> None:
    """A blank-ish / comment-only file parses to an empty map, never raises.

    Comment and blank lines carry no pattern; a file of only those yields an
    empty (but available-elsewhere) parse rather than crashing.
    """
    repo = _init_repo(tmp_path)
    _write(repo, "a.py")
    _write(repo, "CODEOWNERS", "# only comments\n\n   \n")
    _commit_all(repo)

    assert parse_codeowners(repo) == {}


def test_non_git_directory_resolves_via_filesystem(tmp_path: Path) -> None:
    """Outside git, the glob resolver falls back to a filesystem walk.

    No git history means ``tracked_files`` is None; the parser walks the tree
    (with the same excludes + symlink guard) so a CODEOWNERS still resolves.
    """
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "a.py").write_text("x\n", encoding="utf-8")
    (plain / "CODEOWNERS").write_text("*.py @a\n", encoding="utf-8")

    owners = parse_codeowners(plain)
    assert _matched_strs(owners["*.py"]) == {"a.py"}


# --- 5. Determinism ----------------------------------------------------------

def test_parse_ownership_is_byte_identical_across_runs(tmp_path: Path) -> None:
    """Two parses of one repo serialize to identical output.

    Sets are sorted at the boundary, so no iteration order leaks. Serialising the
    full summary twice and asserting equality pins the determinism contract the
    structure-drift signals rely on.
    """
    import json

    repo = _init_repo(tmp_path)
    _write(repo, "src/a.py")
    _write(repo, "src/b.py")
    _write(repo, "docs/x.md")
    _write(repo, "CODEOWNERS", "src/** @a\ndocs/ @b\nghost/** @c\n")
    _write(repo, "ARCHITECTURE.md", "## Core\nowns `src/`\n")
    _commit_all(repo)

    first = json.dumps(parse_ownership(repo), sort_keys=True)
    second = json.dumps(parse_ownership(repo), sort_keys=True)
    assert first == second

    summary = parse_ownership(repo)
    assert summary["available"] is True
    assert {e["pattern"] for e in summary["empty_globs"]} == {"ghost/**"}


# --- 6. Integration: this repo's own seam map --------------------------------

def test_integration_parses_lib_readme_seam_declaration() -> None:
    """Parsing this repo finds the lib README's co-change seam declaration.

    ``skills/assess/scripts/lib/README.md`` declares the module-ownership seam
    map (the ``assess_core.py -> lib`` seam and the wider co-change seams). Its
    boundary-declaring prose admits it as an architecture doc, and its seam
    section names ``doc_graph.py`` as a load-bearing module, which resolves to
    the real tracked file. This is the dogfood guard that the parser reads a
    genuine, human-written ownership declaration on real data.
    """
    repo_root = Path(__file__).resolve().parents[3]  # repo top
    modules = parse_architecture_md(repo_root)

    lib_readme = "skills/assess/scripts/lib/README.md"
    lib_sections = {k: v for k, v in modules.items() if k.startswith(lib_readme + "::")}
    assert lib_sections, "lib README should be parsed as a boundary declaration"

    # The seam map names doc_graph.py; it resolves to the real module file.
    resolved = {p.as_posix() for files in lib_sections.values() for p in files}
    assert "skills/assess/scripts/lib/doc_graph.py" in resolved
