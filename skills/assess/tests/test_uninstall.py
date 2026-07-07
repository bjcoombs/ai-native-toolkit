"""Tests for the uninstall path: run-context pointer, doc completeness, offer (Task 14)."""
from __future__ import annotations

import json
from pathlib import Path

from assess_core import build_run_context

# skills/assess/tests/ -> skills/assess is two parents up.
ASSESS_DIR = Path(__file__).resolve().parents[1]
UNINSTALL_DOC = ASSESS_DIR / "references" / "uninstall.md"
ASSESS_PR_SKILL = ASSESS_DIR.parent / "assess-pr" / "SKILL.md"
ASSESS_SKILL = ASSESS_DIR / "SKILL.md"


# ── run-context pointer ─────────────────────────────────────────────────────

def test_run_context_carries_uninstall_path(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".assess").mkdir()
    (repo / ".assess" / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 1, "loc": {}, "ccn": {},
        "top_hotspots": [], "top_complex": [], "top_large": [],
    }))
    ctx = build_run_context(repo_root=repo, run_date="2026-05-22")
    assert ctx["uninstall_instructions_path"] == "references/uninstall.md"


def test_uninstall_path_resolves_to_real_doc() -> None:
    # The pointer is relative to the skill dir; it must name a file that ships.
    assert (ASSESS_DIR / "references" / "uninstall.md").is_file()


# ── doc completeness / accuracy ─────────────────────────────────────────────

def test_uninstall_doc_covers_every_artifact_class() -> None:
    text = UNINSTALL_DOC.read_text(encoding="utf-8")
    # Each artifact class /assess can leave in a target repo.
    assert "rm -rf" in text and ".assess" in text          # 1. the wiki dir
    assert "badge.json" in text                              # 2. README badge
    assert ".github/workflows/assess-gate.yml" in text       # 3. CI gate
    assert ".no-" in text                                    # 4. decline markers
    assert "assess-archetype" in text                        # 5. archetype marker
    # Findings issues are acknowledged but explicitly NOT auto-closed.
    assert "assess-finding" in text


def test_uninstall_doc_lists_instruction_files_for_archetype_marker() -> None:
    text = UNINSTALL_DOC.read_text(encoding="utf-8")
    for name in ("CLAUDE.md", "AGENTS.md", "GEMINI.md",
                 ".cursorrules", ".github/copilot-instructions.md"):
        assert name in text, f"uninstall doc omits instruction file {name}"


# ── offer appears at end of run ─────────────────────────────────────────────

def test_uninstall_offered_in_assess_pr() -> None:
    text = ASSESS_PR_SKILL.read_text(encoding="utf-8")
    assert "Step 8" in text and "Uninstall" in text
    assert "uninstall_instructions_path" in text


def test_orchestrator_references_uninstall() -> None:
    text = ASSESS_SKILL.read_text(encoding="utf-8")
    assert "uninstall" in text.lower()
    assert "uninstall_instructions_path" in text
