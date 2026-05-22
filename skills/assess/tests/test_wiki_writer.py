"""Tests for wiki writer module."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from lib.wiki_writer import (
    HotspotEntry,
    LogEntry,
    append_log_entry,
    slug_for_path,
    write_hotspot_page,
    write_index,
)


def test_slug_for_path_basic() -> None:
    assert slug_for_path("src/foo/bar.go").startswith("src-foo-bar-go-")
    assert slug_for_path("services/api/handler.ts").startswith("services-api-handler-ts-")


def test_slug_for_path_handles_special_chars() -> None:
    assert slug_for_path("src/foo bar/baz.go").startswith("src-foo-bar-baz-go-")


def test_slug_for_path_avoids_collision() -> None:
    """Distinct paths must produce distinct slugs even when they normalize the same."""
    slug_a = slug_for_path("src/foo-bar.py")
    slug_b = slug_for_path("src/foo/bar.py")
    assert slug_a != slug_b
    # Both still start with the readable form
    assert slug_a.startswith("src-foo-bar-py-")
    assert slug_b.startswith("src-foo-bar-py-")


def test_write_index_creates_file(tmp_assess_dir: Path) -> None:
    entries = [
        HotspotEntry(
            path="src/foo.go", first_flagged="2026-01-01", last_seen="2026-05-22",
            status="active", ccn=30, loc=600,
        ),
    ]
    write_index(tmp_assess_dir, entries, last_updated="2026-05-22")
    index = tmp_assess_dir / "index.md"
    assert index.exists()
    content = index.read_text()
    assert "src/foo.go" in content
    assert "active" in content


def test_write_index_overwrites(tmp_assess_dir: Path) -> None:
    (tmp_assess_dir / "index.md").write_text("OLD")
    entries = [HotspotEntry(
        path="src/new.go", first_flagged="2026-05-22", last_seen="2026-05-22",
        status="active", ccn=20, loc=300,
    )]
    write_index(tmp_assess_dir, entries, last_updated="2026-05-22")
    content = (tmp_assess_dir / "index.md").read_text()
    assert "OLD" not in content
    assert "src/new.go" in content


def test_append_log_entry_creates_file_if_missing(tmp_assess_dir: Path) -> None:
    entry = LogEntry(
        run_date="2026-05-22", files_scored=100, readiness_score=4.5,
        maturity_label="Solid", instructions_grade="B+",
        graduated_count=1, regressed_count=0, new_count=0, persistent_count=2,
        top_action="Add complexity rules to .golangci.yml",
    )
    append_log_entry(tmp_assess_dir, entry)
    log = tmp_assess_dir / "log.md"
    assert log.exists()
    assert "2026-05-22" in log.read_text()


def test_append_log_entry_appends(tmp_assess_dir: Path) -> None:
    (tmp_assess_dir / "log.md").write_text("# Assess Log\n\n## 2026-05-01\n\nOld entry.\n\n---\n")
    entry = LogEntry(
        run_date="2026-05-22", files_scored=100, readiness_score=4.5,
        maturity_label="Solid", instructions_grade="B+",
        graduated_count=0, regressed_count=0, new_count=0, persistent_count=0,
        top_action="Action X",
    )
    append_log_entry(tmp_assess_dir, entry)
    content = (tmp_assess_dir / "log.md").read_text()
    assert "2026-05-01" in content  # old entry preserved
    assert "2026-05-22" in content  # new entry appended
    assert content.index("2026-05-01") < content.index("2026-05-22")


def test_write_hotspot_page_creates_file(tmp_assess_dir: Path) -> None:
    write_hotspot_page(
        tmp_assess_dir,
        path="src/foo.go",
        first_flagged="2026-01-01",
        last_seen="2026-05-22",
        status="regressed",
        loc=600,
        ccn=30,
        commits=15,
        has_tests=False,
        history_rows="| 2026-01-01 | 500 | 25 | 8 | active |\n| 2026-05-22 | 600 | 30 | 15 | regressed |",
        briefing="Go API handler. Pairs with handler_test.go (which is missing).",
        actions="- Add `handler_test.go`\n- Split into smaller functions",
    )
    hotspots_dir = tmp_assess_dir / "hotspots"
    pages = list(hotspots_dir.iterdir())
    assert len(pages) == 1
    page = pages[0]
    assert page.name.startswith("src-foo-go-")
    assert page.name.endswith(".md")
    content = page.read_text(encoding="utf-8")
    assert "src/foo.go" in content
    assert "regressed" in content
    assert "handler_test.go" in content


def test_write_hotspot_page_unknown_has_tests(tmp_assess_dir: Path) -> None:
    """When has_tests is None, the page shows 'unknown' (not 'no').

    Test pairing is a deferred feature; honest reporting beats false negatives.
    """
    write_hotspot_page(
        tmp_assess_dir,
        path="src/foo.go",
        first_flagged="2026-01-01",
        last_seen="2026-05-22",
        status="active",
        loc=600,
        ccn=30,
        commits=15,
        has_tests=None,
        history_rows="| 2026-05-22 | 600 | 30 | 15 | active |",
        briefing="Go API handler.",
        actions="- Investigate complexity",
    )
    page = next((tmp_assess_dir / "hotspots").iterdir())
    content = page.read_text(encoding="utf-8")
    assert "Has test file | unknown" in content
