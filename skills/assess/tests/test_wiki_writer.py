"""Tests for wiki writer module."""
from __future__ import annotations

from pathlib import Path


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


def test_write_index_renders_none_metrics_as_dash(tmp_assess_dir: Path) -> None:
    """A graduated file that fell off every top-N list has unknown current
    metrics. The wiki must render those as `-`, never as `0` - zero in this
    column reads as "the file was emptied" and contradicts the report
    (issue #52 Bug 1)."""
    entries = [HotspotEntry(
        path="src/grad.go", first_flagged="2026-01-01", last_seen="2026-05-29",
        status="graduated", ccn=None, loc=None,
    )]
    write_index(tmp_assess_dir, entries, last_updated="2026-05-29")
    content = (tmp_assess_dir / "index.md").read_text()
    # `-` appears in the row's metric cells; `0` must not.
    row = [line for line in content.splitlines() if "src/grad.go" in line][0]
    assert "| - | - |" in row
    # Real zeros remain zeros (rare for tracked source, but the renderer
    # must distinguish them from unknown values).
    entries = [HotspotEntry(
        path="src/empty.go", first_flagged="2026-01-01", last_seen="2026-05-29",
        status="graduated", ccn=0, loc=0,
    )]
    write_index(tmp_assess_dir, entries, last_updated="2026-05-29")
    content = (tmp_assess_dir / "index.md").read_text()
    row = [line for line in content.splitlines() if "src/empty.go" in line][0]
    assert "| 0 | 0 |" in row


def test_write_index_legend_defines_every_status_token(tmp_assess_dir: Path) -> None:
    """The legend must define every status a hotspot row can carry: the four
    tokens assess_core's diff/status map emits (graduated, new, regressed,
    persistent) plus the `active` fallback for paths with no diff entry."""
    entries = [HotspotEntry(
        path="src/foo.go", first_flagged="2026-06-07", last_seen="2026-06-07",
        status="new", ccn=30, loc=600,
    )]
    write_index(tmp_assess_dir, entries, last_updated="2026-06-07")
    content = (tmp_assess_dir / "index.md").read_text()
    legend = content.split("## Legend", 1)[1]
    for status in ("active", "new", "graduated", "regressed", "persistent"):
        assert f"- **{status}**" in legend, f"legend missing status {status!r}"


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


def test_log_heading_includes_plugin_version(tmp_assess_dir: Path) -> None:
    """The log heading always carries the plugin version when known. This
    makes the log a version history at a glance and naturally disambiguates
    same-day runs across versions (issue #52 Bug 2)."""
    entry = LogEntry(
        run_date="2026-05-29", files_scored=100, readiness_score=4.5,
        maturity_label="Solid", instructions_grade="B+",
        graduated_count=0, regressed_count=0, new_count=0, persistent_count=0,
        top_action="X", plugin_version="1.13.0",
    )
    append_log_entry(tmp_assess_dir, entry)
    content = (tmp_assess_dir / "log.md").read_text()
    assert "## 2026-05-29 (v1.13.0)" in content


def test_log_heading_disambiguates_same_date_same_version(tmp_assess_dir: Path) -> None:
    """Two runs on the same date AT THE SAME version must produce distinct
    headings - otherwise GitHub anchors collide and markdownlint MD024
    fires. Second run appends a HH:MM timestamp inside the parentheses."""
    base = dict(
        run_date="2026-05-29", files_scored=100, readiness_score=4.5,
        maturity_label="Solid", instructions_grade="B+",
        graduated_count=0, regressed_count=0, new_count=0, persistent_count=0,
        top_action="X", plugin_version="1.13.0",
    )
    append_log_entry(tmp_assess_dir, LogEntry(**base))
    append_log_entry(tmp_assess_dir, LogEntry(**base))

    content = (tmp_assess_dir / "log.md").read_text()
    headings = [line for line in content.splitlines() if line.startswith("## ")]
    assert len(headings) == 2
    # First heading has no time; second has a HH:MM stamp inside the parens.
    assert headings[0] == "## 2026-05-29 (v1.13.0)"
    assert headings[1].startswith("## 2026-05-29 (v1.13.0 ")
    assert headings[1].endswith(")")
    # No two identical headings (the bug condition).
    assert headings[0] != headings[1]


def test_log_heading_same_date_different_versions_each_unique(tmp_assess_dir: Path) -> None:
    """Same-day runs at different plugin versions distinguish themselves
    via the version in the heading - no time stamp needed."""
    base = dict(
        run_date="2026-05-29", files_scored=100, readiness_score=4.5,
        maturity_label="Solid", instructions_grade="B+",
        graduated_count=0, regressed_count=0, new_count=0, persistent_count=0,
        top_action="X",
    )
    append_log_entry(tmp_assess_dir, LogEntry(**base, plugin_version="1.12.0"))
    append_log_entry(tmp_assess_dir, LogEntry(**base, plugin_version="1.13.0"))

    content = (tmp_assess_dir / "log.md").read_text()
    assert "## 2026-05-29 (v1.12.0)" in content
    assert "## 2026-05-29 (v1.13.0)" in content


def test_log_heading_omits_version_when_none(tmp_assess_dir: Path) -> None:
    """A caller that doesn't pass plugin_version (older code path) still
    works - the heading falls back to the bare date format."""
    entry = LogEntry(
        run_date="2026-05-29", files_scored=100, readiness_score=4.5,
        maturity_label="Solid", instructions_grade="B+",
        graduated_count=0, regressed_count=0, new_count=0, persistent_count=0,
        top_action="X",
    )
    append_log_entry(tmp_assess_dir, entry)
    content = (tmp_assess_dir / "log.md").read_text()
    assert "## 2026-05-29" in content
    assert "(v" not in content


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


def _hotspot_kwargs(**overrides: object) -> dict:
    """Baseline write_hotspot_page kwargs; overrides win."""
    base = dict(
        path="src/foo.go",
        first_flagged="2026-01-01",
        last_seen="2026-06-17",
        status="active",
        loc=600,
        ccn=30,
        commits=15,
        has_tests=None,
        history_rows="| 2026-06-17 | 600 | 30 | 15 | active |",
        briefing="Go API handler.",
        actions="- Investigate complexity",
    )
    base.update(overrides)
    return base


def test_hotspot_page_includes_growth_profile_when_accreting(tmp_assess_dir: Path) -> None:
    """A file present in the accretion data gets one growth-profile line in the
    briefing - the monotonic-growth tendency named where an agent is briefed."""
    write_hotspot_page(tmp_assess_dir, **_hotspot_kwargs(accretion_data={
        "path": "src/foo.go", "net_additions": 420, "commit_count": 18,
        "deletion_fraction": 0.04, "time_span_months": 7.2, "reliable": True,
    }))
    page = next((tmp_assess_dir / "hotspots").iterdir())
    content = page.read_text(encoding="utf-8")
    assert "Growth profile: monotonic" in content
    assert "+420 LOC" in content
    assert "0 net reductions over 18 commits in 7 months" in content
    # No new section header - the line rides inside the existing briefing.
    assert "## Growth" not in content


def test_hotspot_page_no_growth_profile_without_accretion_data(tmp_assess_dir: Path) -> None:
    """A file with no accretion entry (None) earns no line - growth that wasn't
    flagged as pure accretion is normal development, not a ratchet."""
    write_hotspot_page(tmp_assess_dir, **_hotspot_kwargs(accretion_data=None))
    page = next((tmp_assess_dir / "hotspots").iterdir())
    content = page.read_text(encoding="utf-8")
    assert "Growth profile" not in content


def test_hotspot_page_growth_profile_defaults_to_none(tmp_assess_dir: Path) -> None:
    """accretion_data is optional: a caller that doesn't pass it still works and
    produces no growth line (back-compat with the pre-accretion call site)."""
    write_hotspot_page(tmp_assess_dir, **_hotspot_kwargs())
    page = next((tmp_assess_dir / "hotspots").iterdir())
    content = page.read_text(encoding="utf-8")
    assert "Growth profile" not in content


def test_hotspot_page_growth_profile_disclaims_unreliable_history(tmp_assess_dir: Path) -> None:
    """reliable=False (shallow/squashed clone) still reports the profile but
    appends the incomplete-history disclaimer so the count isn't over-trusted."""
    write_hotspot_page(tmp_assess_dir, **_hotspot_kwargs(accretion_data={
        "path": "src/foo.go", "net_additions": 200, "commit_count": 5,
        "deletion_fraction": 0.02, "time_span_months": 3.0, "reliable": False,
    }))
    page = next((tmp_assess_dir / "hotspots").iterdir())
    content = page.read_text(encoding="utf-8")
    assert "Growth profile: monotonic" in content
    assert "history may be incomplete" in content
    assert "shallow/squashed repo" in content
