"""End-to-end test for assess_finalize - the LLM write-back script."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from assess_finalize import finalize_run


def _seed_log_md(assess_dir: Path) -> None:
    """Seed a log.md with one entry that has placeholders awaiting LLM fill."""
    (assess_dir / "log.md").write_text(
        "# Assess Log\n\n"
        "## 2026-05-22\n\n"
        "- **Files scored:** 100\n"
        "- **AI Readiness:** 0.0 / 8 ((LLM fills in))\n"
        "- **Instructions grade:** B+\n"
        "- **Hotspot transitions:** 1 graduated, 0 regressed, 0 new, 2 persistent\n"
        "- **Top action:** Deterministic ranker not yet wired (LLM picks Top 3)\n\n"
        "[Full report](./assess-report.md)\n\n"
        "---\n",
        encoding="utf-8",
    )


def _seed_hotspot_page(assess_dir: Path, slug: str) -> None:
    (assess_dir / "hotspots").mkdir(exist_ok=True)
    (assess_dir / "hotspots" / f"{slug}.md").write_text(
        "# Hotspot: `src/foo.go`\n\n"
        "## Suggested actions\n\n"
        "- Pending LLM-generated suggestions\n",
        encoding="utf-8",
    )


def test_finalize_updates_log_last_entry(tmp_assess_dir: Path) -> None:
    """The latest log.md entry gets its placeholders replaced with LLM-provided values."""
    _seed_log_md(tmp_assess_dir)
    finalize_input = {
        "score": 6.0,
        "maturity_label": "Solid",
        "top_action": "Add cyclop rule to .golangci.yml (threshold 15)",
        "hotspot_actions": {},
    }
    (tmp_assess_dir / "finalize-input.json").write_text(
        json.dumps(finalize_input), encoding="utf-8"
    )

    finalize_run(assess_dir=tmp_assess_dir)
    content = (tmp_assess_dir / "log.md").read_text(encoding="utf-8")
    assert "AI Readiness:** 6.0 / 8 (Solid)" in content
    assert "Top action:** Add cyclop rule to .golangci.yml (threshold 15)" in content
    # Placeholders must be gone
    assert "((LLM fills in))" not in content
    assert "Deterministic ranker not yet wired" not in content


def test_finalize_updates_hotspot_actions(tmp_assess_dir: Path) -> None:
    """Hotspot pages get their 'Suggested actions' section rewritten with LLM input."""
    # The slug for "src/foo.go" includes a sha256[:8] hash - use the same function the script uses
    from lib.wiki_writer import slug_for_path
    slug = slug_for_path("src/foo.go")
    _seed_hotspot_page(tmp_assess_dir, slug=slug)
    finalize_input = {
        "score": 6.0,
        "maturity_label": "Solid",
        "top_action": "x",
        "hotspot_actions": {
            "src/foo.go": [
                "Split parseLine into smaller functions",
                "Add a test file at src/foo_test.go",
            ],
        },
    }
    (tmp_assess_dir / "finalize-input.json").write_text(
        json.dumps(finalize_input), encoding="utf-8"
    )
    # Need a log.md too since finalize_run does both
    _seed_log_md(tmp_assess_dir)

    finalize_run(assess_dir=tmp_assess_dir)
    page = (tmp_assess_dir / "hotspots" / f"{slug}.md").read_text(encoding="utf-8")
    assert "Split parseLine" in page
    assert "Add a test file at src/foo_test.go" in page
    assert "Pending LLM-generated suggestions" not in page


def test_finalize_missing_input_raises(tmp_assess_dir: Path) -> None:
    """If finalize-input.json doesn't exist, raise a clear error."""
    with pytest.raises(FileNotFoundError):
        finalize_run(assess_dir=tmp_assess_dir)


def test_finalize_hotspot_without_match_is_skipped(tmp_assess_dir: Path) -> None:
    """An entry in hotspot_actions whose page doesn't exist is silently skipped.

    This is forward-compatible with the path lifecycle: a hotspot might graduate
    between when the LLM read the data and when finalize runs.
    """
    _seed_log_md(tmp_assess_dir)
    finalize_input = {
        "score": 6.0,
        "maturity_label": "Solid",
        "top_action": "x",
        "hotspot_actions": {"src/nonexistent.go": ["something"]},
    }
    (tmp_assess_dir / "finalize-input.json").write_text(
        json.dumps(finalize_input), encoding="utf-8"
    )

    # Should not raise
    finalize_run(assess_dir=tmp_assess_dir)


def test_finalize_log_handles_backslash_in_top_action(tmp_assess_dir: Path) -> None:
    """Top action text containing \\1 must be inserted literally, not as a regex backreference."""
    _seed_log_md(tmp_assess_dir)
    finalize_input = {
        "score": 6.0,
        "maturity_label": "Solid",
        # Adversarial: contains \1 which would be a re.sub backreference if not handled
        "top_action": r"Replace `\1` capture group references in regexes",
        "hotspot_actions": {},
    }
    (tmp_assess_dir / "finalize-input.json").write_text(
        json.dumps(finalize_input), encoding="utf-8"
    )

    finalize_run(assess_dir=tmp_assess_dir)
    content = (tmp_assess_dir / "log.md").read_text(encoding="utf-8")
    assert r"Replace `\1` capture group references" in content


def test_finalize_hotspot_action_handles_backslash(tmp_assess_dir: Path) -> None:
    """Hotspot action text containing \\1 must be inserted literally."""
    from lib.wiki_writer import slug_for_path
    slug = slug_for_path("src/foo.go")
    _seed_hotspot_page(tmp_assess_dir, slug=slug)
    _seed_log_md(tmp_assess_dir)
    finalize_input = {
        "score": 6.0,
        "maturity_label": "Solid",
        "top_action": "x",
        "hotspot_actions": {
            "src/foo.go": [
                r"Use \1 to denote first capture group",
                "Add tests",
            ],
        },
    }
    (tmp_assess_dir / "finalize-input.json").write_text(
        json.dumps(finalize_input), encoding="utf-8"
    )

    finalize_run(assess_dir=tmp_assess_dir)
    page = (tmp_assess_dir / "hotspots" / f"{slug}.md").read_text(encoding="utf-8")
    assert r"Use \1 to denote first capture group" in page


def test_finalize_updates_last_entry_when_older_entry_unfinalized(tmp_assess_dir: Path) -> None:
    """When an older log entry still has placeholders, finalize updates the LATEST.

    Log entries are appended (newest at bottom). If we finalized the first match,
    we'd overwrite stale historical data and leave the new run unfilled.
    The older unfinalized entry stays as evidence of "this run wasn't finalized."
    """
    # Seed two entries: older (unfinalized, placeholders intact) then newer (also placeholders)
    (tmp_assess_dir / "log.md").write_text(
        "# Assess Log\n\n"
        "## 2026-05-01\n\n"
        "- **Files scored:** 80\n"
        "- **AI Readiness:** 0.0 / 8 ((LLM fills in))\n"
        "- **Top action:** Deterministic ranker not yet wired (LLM picks Top 3)\n\n"
        "---\n\n"
        "## 2026-05-22\n\n"
        "- **Files scored:** 100\n"
        "- **AI Readiness:** 0.0 / 8 ((LLM fills in))\n"
        "- **Top action:** Deterministic ranker not yet wired (LLM picks Top 3)\n\n"
        "---\n",
        encoding="utf-8",
    )
    finalize_input = {
        "score": 6.5,
        "maturity_label": "Solid",
        "top_action": "Add cyclop rule (threshold 15) to .golangci.yml",
        "hotspot_actions": {},
    }
    (tmp_assess_dir / "finalize-input.json").write_text(
        json.dumps(finalize_input), encoding="utf-8"
    )

    finalize_run(assess_dir=tmp_assess_dir)
    content = (tmp_assess_dir / "log.md").read_text(encoding="utf-8")

    # The 2026-05-22 (latest) entry got filled
    latest_section = content.split("## 2026-05-22")[1]
    assert "AI Readiness:** 6.5 / 8 (Solid)" in latest_section
    assert "Top action:** Add cyclop rule (threshold 15) to .golangci.yml" in latest_section

    # The 2026-05-01 (older) entry's placeholders are PRESERVED as historical evidence
    older_section = content.split("## 2026-05-22")[0]
    assert "AI Readiness:** 0.0 / 8 ((LLM fills in))" in older_section
    assert "Deterministic ranker not yet wired" in older_section
