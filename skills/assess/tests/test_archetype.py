"""Tests for repository archetype detection (lib/archetype.py).

Covers the four acceptance criteria of issue #224:
1. A markdown KB repo is auto-detected; an override marker forces/suppresses.
2. A detected KB marks write-side layers N/A and renormalises the denominator.
3. The KB-maintenance (Karpathy LLM-wiki) workflow is detected and the gist
   is always available as the best-practice pointer.
4. A conventional software repo is unaffected (all 0-8 layers, denominator 8).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib.archetype import (  # noqa: E402
    KARPATHY_GIST_URL,
    KB_APPLICABLE_LAYERS,
    SOFTWARE_DENOMINATOR,
    WRITE_SIDE_LAYERS,
    analyze_archetype,
    classify_archetype,
    detect_kb_maintenance,
    read_archetype_override,
)


def _kb_maint_empty() -> dict:
    return {"documented": False, "signals_found": [], "gist_cited": False, "gist": KARPATHY_GIST_URL}


# --- classify_archetype: heuristic --------------------------------------


def test_markdown_kb_detected_by_ratio_and_no_runtime():
    block = classify_archetype(
        code_file_count=1,
        doc_file_count=80,
        other_file_count=4,
        has_runtime_surface=False,
        override=None,
        kb_maintenance=_kb_maint_empty(),
    )
    assert block["archetype"] == "knowledge-base"
    assert block["detected_via"] == "heuristic"
    assert block["na_layers"] == WRITE_SIDE_LAYERS
    assert block["applicable_layers"] == KB_APPLICABLE_LAYERS
    assert block["denominator"] == len(KB_APPLICABLE_LAYERS) == 3
    assert "ratio" in block["reason"]


def test_software_repo_unaffected_all_layers():
    block = classify_archetype(
        code_file_count=120,
        doc_file_count=20,
        other_file_count=10,
        has_runtime_surface=True,
        override=None,
        kb_maintenance=_kb_maint_empty(),
    )
    assert block["archetype"] == "software"
    assert block["na_layers"] == []
    assert block["applicable_layers"] == list(range(0, 9))
    assert block["denominator"] == SOFTWARE_DENOMINATOR == 8


def test_doc_heavy_app_with_runtime_surface_is_software():
    # Lots of markdown but a real build manifest -> not a KB.
    block = classify_archetype(
        code_file_count=2,
        doc_file_count=200,
        other_file_count=5,
        has_runtime_surface=True,
        override=None,
        kb_maintenance=_kb_maint_empty(),
    )
    assert block["archetype"] == "software"
    assert block["signals"]["has_runtime_surface"] is True
    assert "runtime surface" in block["reason"]


def test_empty_or_tiny_doc_set_not_kb():
    block = classify_archetype(
        code_file_count=0,
        doc_file_count=1,
        other_file_count=0,
        has_runtime_surface=False,
        override=None,
        kb_maintenance=_kb_maint_empty(),
    )
    # Only one doc and no real base -> not enough to call it a knowledge base.
    assert block["archetype"] == "software"


# --- classify_archetype: override ---------------------------------------


def test_override_forces_knowledge_base():
    block = classify_archetype(
        code_file_count=500,  # code-heavy; heuristic would say software
        doc_file_count=3,
        other_file_count=0,
        has_runtime_surface=True,
        override="knowledge-base",
        kb_maintenance=_kb_maint_empty(),
    )
    assert block["archetype"] == "knowledge-base"
    assert block["detected_via"] == "override"
    assert block["denominator"] == 3


def test_override_suppresses_detection():
    block = classify_archetype(
        code_file_count=0,  # heuristic would say knowledge-base
        doc_file_count=99,
        other_file_count=0,
        has_runtime_surface=False,
        override="software",
        kb_maintenance=_kb_maint_empty(),
    )
    assert block["archetype"] == "software"
    assert block["detected_via"] == "override"
    assert block["denominator"] == 8


# --- read_archetype_override --------------------------------------------


def test_read_override_marker_force_kb(tmp_path: Path):
    (tmp_path / "CLAUDE.md").write_text(
        "# Repo\n\n<!-- assess-archetype: knowledge-base -->\n", encoding="utf-8"
    )
    assert read_archetype_override(tmp_path) == "knowledge-base"


def test_read_override_marker_suppress(tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text(
        "assess-archetype: software\n", encoding="utf-8"
    )
    assert read_archetype_override(tmp_path) == "software"


def test_read_override_alias_kb(tmp_path: Path):
    (tmp_path / "CLAUDE.md").write_text("<!-- assess-archetype: kb -->", encoding="utf-8")
    assert read_archetype_override(tmp_path) == "knowledge-base"


def test_read_override_absent_returns_none(tmp_path: Path):
    (tmp_path / "CLAUDE.md").write_text("# nothing special here\n", encoding="utf-8")
    assert read_archetype_override(tmp_path) is None


def test_read_override_unrecognised_value_ignored(tmp_path: Path):
    (tmp_path / "CLAUDE.md").write_text("<!-- assess-archetype: banana -->", encoding="utf-8")
    assert read_archetype_override(tmp_path) is None


# --- detect_kb_maintenance ----------------------------------------------


def test_kb_maintenance_documented_by_two_facets():
    text = (
        "Raw sources are immutable and append-only. "
        "A periodic consolidation pass lints the wiki and prunes stale notes."
    )
    sig = detect_kb_maintenance(text)
    assert sig["documented"] is True
    assert "immutable-sources" in sig["signals_found"]
    assert "periodic-consolidation" in sig["signals_found"]
    assert sig["gist"] == KARPATHY_GIST_URL


def test_kb_maintenance_documented_by_gist_citation():
    text = "We follow the LLM-wiki pattern: " + KARPATHY_GIST_URL
    sig = detect_kb_maintenance(text)
    assert sig["gist_cited"] is True
    assert sig["documented"] is True


def test_kb_maintenance_single_keyword_not_documented():
    text = "This project has a database schema."
    sig = detect_kb_maintenance(text)
    assert sig["documented"] is False
    # gist pointer is always present regardless
    assert sig["gist"] == KARPATHY_GIST_URL


def test_kb_maintenance_gist_always_present_when_absent():
    sig = detect_kb_maintenance("nothing relevant")
    assert sig["documented"] is False
    assert sig["signals_found"] == []
    assert sig["gist"] == KARPATHY_GIST_URL


# --- analyze_archetype (integration over a temp repo) -------------------


def _git_init(repo: Path) -> None:
    import subprocess

    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@e.com", "-c", "user.name=t",
         "commit", "-q", "-m", "init"],
        check=True,
    )


def test_analyze_markdown_repo_is_knowledge_base(tmp_path: Path):
    repo = tmp_path / "kb"
    (repo / "notes").mkdir(parents=True)
    for i in range(12):
        (repo / "notes" / f"note-{i}.md").write_text(f"# Note {i}\n", encoding="utf-8")
    (repo / "CLAUDE.md").write_text(
        "# KB schema\nRaw sources are immutable. Periodic consolidation lints the wiki.\n",
        encoding="utf-8",
    )
    _git_init(repo)

    block = analyze_archetype(repo)
    assert block["available"] is True
    assert block["archetype"] == "knowledge-base"
    assert block["na_layers"] == WRITE_SIDE_LAYERS
    assert block["denominator"] == 3
    assert block["kb_maintenance"]["documented"] is True


def test_analyze_software_repo_is_software(tmp_path: Path):
    repo = tmp_path / "app"
    (repo / "src").mkdir(parents=True)
    for i in range(15):
        (repo / "src" / f"mod_{i}.py").write_text(f"def f{i}():\n    return {i}\n", encoding="utf-8")
    (repo / "README.md").write_text("# App\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname='app'\n", encoding="utf-8")
    _git_init(repo)

    block = analyze_archetype(repo)
    assert block["archetype"] == "software"
    assert block["na_layers"] == []
    assert block["denominator"] == 8
    assert block["signals"]["has_runtime_surface"] is True
