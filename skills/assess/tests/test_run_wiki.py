"""Tests for lib/run_wiki.py: the hotspot pages, index.md rows and graduation
records one run writes.

Moved out of test_assess_core.py when assess_core's helpers split into
lib/ modules; shared repo-seeding helpers live in assess_core_helpers.py.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from assess_core import build_run_context
from lib import run_wiki
from lib.wiki_writer import (
    hotspot_page_source_path,
    hotspot_page_status,
    is_retired_status,
    slug_for_path,
    write_hotspot_page,
)


def test_unfinalized_hotspot_page_uses_neutral_pointer_not_placeholder(tmp_path: Path) -> None:
    """A hotspot page the deterministic core writes - before any LLM finalize -
    must carry the neutral out-of-Top-3 pointer, never a TODO-style placeholder.

    assess_finalize only rewrites the pages it's handed actions for (at minimum
    the Top 3), so a flagged-but-not-Top-3 page can ship un-finalized. Its default
    "Suggested actions" body must read as intentional, not as unfinished work
    (issue #165).
    """
    from lib.wiki_writer import UNFINALIZED_ACTIONS_POINTER, slug_for_path

    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 50,
        "loc": {"p50": 30, "p95": 200, "max": 500},
        "ccn": {"p50": 2, "p95": 8, "max": 20},
        "top_hotspots": [{"path": "src/a.go", "loc": 500, "ccn": 20, "commits": 5}],
        "top_complex": [{"path": "src/a.go", "ccn": 20}],
        "top_large": [{"path": "src/a.go", "loc": 500}],
    }))

    build_run_context(repo_root=repo, run_date="2026-05-22")

    page = (assess_dir / "hotspots" / f"{slug_for_path('src/a.go')}.md").read_text(encoding="utf-8")
    # The "## Suggested actions" heading the finalizer keys off must survive.
    assert "## Suggested actions" in page
    # The neutral pointer is present...
    assert UNFINALIZED_ACTIONS_POINTER in page
    # ...and no TODO/placeholder marker leaks into the committed page.
    for marker in ("Pending LLM-generated suggestions", "TODO", "placeholder", "FIXME"):
        assert marker not in page


def test_hotspot_page_carries_growth_profile_when_file_accretes(git_repo) -> None:
    """A top-hotspot file that the accretion scan flags (monotonic growth, low
    deletion across several commits) gets the growth-profile line wired into its
    generated hotspot page - tasks 4+5 end-to-end."""
    from lib.wiki_writer import slug_for_path

    repo, commit = git_repo
    src = repo / "src"
    src.mkdir()
    grower = src / "grower.go"
    # Five commits that only add lines and never delete: pure accretion.
    for i in range(1, 6):
        grower.write_text("\n".join(f"line {n}" for n in range(i * 40)) + "\n")
        commit(f"grow {i}", days_ago=(6 - i) * 20)

    assess_dir = repo / ".assess"
    assess_dir.mkdir(exist_ok=True)
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 50,
        "loc": {"p50": 30, "p95": 200, "max": 500},
        "ccn": {"p50": 2, "p95": 8, "max": 20},
        "top_hotspots": [{"path": "src/grower.go", "loc": 200, "ccn": 20, "commits": 5}],
        "top_complex": [{"path": "src/grower.go", "ccn": 20}],
        "top_large": [{"path": "src/grower.go", "loc": 200}],
    }))

    ctx = build_run_context(repo_root=repo, run_date="2026-06-17")
    # Precondition: the scan actually flagged this file (else the test proves nothing).
    flagged = {f["path"] for f in ctx["accretion_ratchet"].get("files", [])}
    assert "src/grower.go" in flagged

    # The block must also reach the keyhole: a populated accretion_ratchet block
    # is wired into the derived finding (assess_core passes it to integrate), so
    # the cross-layer finding lists the path - not just the run-context block.
    finding = next(
        f for f in ctx["derived_findings"] if f["name"] == "accretion_ratchet"
    )
    assert "src/grower.go" in finding["paths"]

    page = (assess_dir / "hotspots" / f"{slug_for_path('src/grower.go')}.md").read_text(
        encoding="utf-8"
    )
    assert "Growth profile: monotonic" in page
    assert "lines net over" in page


def test_hotspot_page_omits_growth_profile_when_scan_unavailable(tmp_path: Path) -> None:
    """Graceful degradation: outside a git repo the accretion scan is
    unavailable, so the hotspot page is still written - just without a growth
    line. Hotspot generation never depends on the scan succeeding."""
    from lib.wiki_writer import slug_for_path

    repo = tmp_path / "repo"  # not a git repo
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 50,
        "loc": {"p50": 30, "p95": 200, "max": 500},
        "ccn": {"p50": 2, "p95": 8, "max": 20},
        "top_hotspots": [{"path": "src/a.go", "loc": 500, "ccn": 20, "commits": 5}],
        "top_complex": [{"path": "src/a.go", "ccn": 20}],
        "top_large": [{"path": "src/a.go", "loc": 500}],
    }))

    ctx = build_run_context(repo_root=repo, run_date="2026-06-17")
    assert ctx["accretion_ratchet"]["available"] is False

    page = (assess_dir / "hotspots" / f"{slug_for_path('src/a.go')}.md").read_text(
        encoding="utf-8"
    )
    # Page exists and is complete, but carries no growth profile.
    assert "## Suggested actions" in page
    assert "Growth profile" not in page


def test_unfinalized_actions_pointer_carries_no_placeholder_marker() -> None:
    """The neutral pointer text itself must be free of TODO-style markers - it is
    the default that ships when a page is never finalized."""
    from lib.wiki_writer import UNFINALIZED_ACTIONS_POINTER

    lowered = UNFINALIZED_ACTIONS_POINTER.lower()
    for marker in ("pending", "todo", "placeholder", "fixme", "tbd"):
        assert marker not in lowered


def test_graduated_index_row_carries_current_metrics(tmp_path: Path) -> None:
    """Issue #52 Bug 1: when a file graduates off top_hotspots[:10] but is
    still present in top_complex or top_large, the index row must show its
    *current* CCN and LOC, not 0. Zero in those columns reads as "the file
    was emptied," contradicts assess-report.md, and misleads reviewers."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()

    # Prior run: src/legacy.go was a top hotspot.
    prior_stats = {
        "files_scored": 50, "loc": {}, "ccn": {},
        "top_hotspots": [
            {"path": "src/legacy.go", "loc": 1096, "ccn": 172, "commits": 5},
        ],
        "top_complex": [{"path": "src/legacy.go", "ccn": 172}],
        "top_large": [{"path": "src/legacy.go", "loc": 1096}],
    }
    (assess_dir / "complexity-stats.prior.json").write_text(json.dumps(prior_stats))

    # Current run: src/legacy.go dropped off top_hotspots (a bigger file
    # took its slot) but still appears in top_complex and top_large at its
    # actual current metrics. This is the case CodeRabbit caught.
    current_stats = {
        "files_scored": 55, "loc": {}, "ccn": {},
        "top_hotspots": [
            {"path": "src/giant.go", "loc": 2500, "ccn": 200, "commits": 8},
        ],
        "top_complex": [
            {"path": "src/giant.go", "ccn": 200},
            {"path": "src/legacy.go", "ccn": 172},
        ],
        "top_large": [
            {"path": "src/giant.go", "loc": 2500},
            {"path": "src/legacy.go", "loc": 1096},
        ],
    }
    (assess_dir / "complexity-stats.json").write_text(json.dumps(current_stats))

    build_run_context(repo_root=repo, run_date="2026-05-29")
    index = (assess_dir / "index.md").read_text(encoding="utf-8")

    # The graduated row must reflect reality: 1,096 LOC, ccn 172. NEVER 0.
    legacy_row = next(line for line in index.splitlines() if "src/legacy.go" in line)
    assert "graduated" in legacy_row
    assert "| 172 | 1096 |" in legacy_row, (
        f"expected current ccn/loc, got: {legacy_row}"
    )
    # The active row stays intact.
    assert "src/giant.go" in index


def test_graduated_index_row_uses_dash_when_metrics_unknown(tmp_path: Path) -> None:
    """When a graduated file fell off every top-N list (no current metrics
    available anywhere), the row renders `-` rather than misleading zeros."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()

    # Prior: src/legacy.go was a hotspot.
    prior_stats = {
        "files_scored": 50, "loc": {}, "ccn": {},
        "top_hotspots": [
            {"path": "src/legacy.go", "loc": 800, "ccn": 90, "commits": 3},
        ],
        "top_complex": [{"path": "src/legacy.go", "ccn": 90}],
        "top_large": [{"path": "src/legacy.go", "loc": 800}],
    }
    (assess_dir / "complexity-stats.prior.json").write_text(json.dumps(prior_stats))

    # Current: src/legacy.go fell off ALL top-N lists (none of them mention it).
    current_stats = {
        "files_scored": 55, "loc": {}, "ccn": {},
        "top_hotspots": [
            {"path": "src/new.go", "loc": 600, "ccn": 80, "commits": 4},
        ],
        "top_complex": [{"path": "src/new.go", "ccn": 80}],
        "top_large": [{"path": "src/new.go", "loc": 600}],
    }
    (assess_dir / "complexity-stats.json").write_text(json.dumps(current_stats))

    build_run_context(repo_root=repo, run_date="2026-05-29")
    index = (assess_dir / "index.md").read_text(encoding="utf-8")
    legacy_row = next(line for line in index.splitlines() if "src/legacy.go" in line)
    # Sentinel "-" not "0" - never lie about the size.
    assert "| - | - |" in legacy_row


def test_build_run_context_writes_hotspot_pages(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 10, "loc": {"p50": 10, "p95": 30, "max": 50},
        "ccn": {"p50": 1, "p95": 3, "max": 5},
        "top_hotspots": [
            {"path": "src/foo.go", "loc": 500, "ccn": 20, "commits": 5},
        ],
        "top_complex": [{"path": "src/foo.go", "ccn": 20}],
        "top_large": [{"path": "src/foo.go", "loc": 500}],
    }))

    build_run_context(repo_root=repo, run_date="2026-05-22")
    hotspots = list((assess_dir / "hotspots").iterdir())
    assert len(hotspots) == 1
    assert hotspots[0].name.startswith("src-foo-go-")
    assert hotspots[0].name.endswith(".md")


def test_briefing_includes_loc_ccn_commits_and_status(tmp_path: Path) -> None:
    """The auto-generated briefing should reflect the actual stats, not be vague."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 10, "loc": {"p50": 10, "p95": 30, "max": 50},
        "ccn": {"p50": 1, "p95": 3, "max": 5},
        "top_hotspots": [
            {"path": "src/foo.go", "loc": 500, "ccn": 20, "commits": 15},
        ],
        "top_complex": [{"path": "src/foo.go", "ccn": 20}],
        "top_large": [{"path": "src/foo.go", "loc": 500}],
    }))
    build_run_context(repo_root=repo, run_date="2026-05-22")

    page = next((assess_dir / "hotspots").iterdir())
    content = page.read_text(encoding="utf-8")
    assert "500 LOC" in content
    assert "aggregate cyclomatic complexity 20" in content
    assert "15 commits" in content
    # has_tests should be "unknown" now, not "no"
    assert "Has test file | unknown" in content


def test_has_sibling_test_detects_colocated_and_adjacent(tmp_path: Path) -> None:
    """Co-located and adjacent-dir test files lift has_tests from unknown to
    yes/no cheaply (issue #47, observation 6)."""
    repo = tmp_path / "repo"
    (repo / "go").mkdir(parents=True)
    (repo / "go" / "foo.go").write_text("package foo")
    (repo / "go" / "foo_test.go").write_text("package foo")  # co-located
    (repo / "ts").mkdir()
    (repo / "ts" / "bar.ts").write_text("export const x = 1")
    (repo / "ts" / "bar.test.ts").write_text("test")          # co-located .test.
    (repo / "py").mkdir()
    (repo / "py" / "baz.py").write_text("x = 1")              # no test
    (repo / "svc").mkdir()
    (repo / "svc" / "api.py").write_text("x = 1")
    (repo / "svc" / "__tests__").mkdir()
    (repo / "svc" / "__tests__" / "test_api.py").write_text("t")  # adjacent dir

    assert run_wiki._has_sibling_test(repo, "go/foo.go") is True
    assert run_wiki._has_sibling_test(repo, "ts/bar.ts") is True
    assert run_wiki._has_sibling_test(repo, "py/baz.py") is False
    assert run_wiki._has_sibling_test(repo, "svc/api.py") is True
    # The file is itself a test -> counts as covered.
    assert run_wiki._has_sibling_test(repo, "go/foo_test.go") is True
    # Not on disk (e.g. a since-deleted path in a stats snapshot) -> unknown.
    assert run_wiki._has_sibling_test(repo, "go/gone.go") is None


def test_hotspot_page_shows_yes_when_sibling_test_exists(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "foo.go").write_text("package foo")
    (repo / "src" / "foo_test.go").write_text("package foo")
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 10, "loc": {"p50": 10, "p95": 30, "max": 50},
        "ccn": {"p50": 1, "p95": 3, "max": 5},
        "top_hotspots": [{"path": "src/foo.go", "loc": 500, "ccn": 20, "commits": 5}],
        "top_complex": [], "top_large": [],
    }))
    build_run_context(repo_root=repo, run_date="2026-05-22")
    content = next((assess_dir / "hotspots").iterdir()).read_text(encoding="utf-8")
    assert "Has test file | yes" in content


def test_commits_read_from_legacy_churn_field(tmp_path: Path) -> None:
    """A stats snapshot using the legacy `churn` key still shows real commits in
    the hotspot page, not 0 (issue #47, observation 5)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.json").write_text(json.dumps({
        "files_scored": 10, "loc": {"p50": 10, "p95": 30, "max": 50},
        "ccn": {"p50": 1, "p95": 3, "max": 5},
        "top_hotspots": [{"path": "src/foo.go", "loc": 500, "ccn": 20, "churn": 33}],
        "top_complex": [], "top_large": [],
    }))
    build_run_context(repo_root=repo, run_date="2026-05-22")
    content = next((assess_dir / "hotspots").iterdir()).read_text(encoding="utf-8")
    assert "33 commits" in content
    assert "Commits in churn window | 33" in content


def test_generated_header_exclusion_is_not_recorded_as_graduation(tmp_path: Path) -> None:
    """A prior hotspot that left the ranking because this run excluded it as
    generated did not graduate: it is dropped from diff.graduated, while a real
    graduation in the same run is still counted."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    prior_stats = {
        "files_scored": 50, "loc": {}, "ccn": {},
        "top_hotspots": [
            {"path": "db/schema.sql", "loc": 900, "ccn": 40, "commits": 30},
            {"path": "src/legacy.go", "loc": 500, "ccn": 20, "commits": 5},
        ],
        "top_complex": [], "top_large": [],
    }
    (assess_dir / "complexity-stats.prior.json").write_text(json.dumps(prior_stats))
    current_stats = {
        "files_scored": 49, "loc": {}, "ccn": {},
        "top_hotspots": [{"path": "src/new.go", "loc": 400, "ccn": 18, "commits": 4}],
        "top_complex": [], "top_large": [],
        "excluded_generated": [{"path": "db/schema.sql", "reason": "generated-header"}],
    }
    (assess_dir / "complexity-stats.json").write_text(json.dumps(current_stats))

    ctx = build_run_context(repo_root=repo, run_date="2026-09-18")
    assert ctx["diff"]["graduated"] == 1
    assert ctx["excluded_generated"] == [
        {"path": "db/schema.sql", "reason": "generated-header"}
    ]
    index = (assess_dir / "index.md").read_text()
    assert "src/legacy.go" in index
    assert "db/schema.sql" not in index


def test_generated_header_free_name_glob_is_not_recorded_as_graduation(tmp_path: Path) -> None:
    """A prior hotspot now excluded by a generated-name glob (silent, so not in
    excluded_generated) is also kept out of diff.graduated."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    prior_stats = {
        "files_scored": 50, "loc": {}, "ccn": {},
        "top_hotspots": [
            {"path": "web/src/database.types.ts", "loc": 8000, "ccn": 5, "commits": 130},
            {"path": "web/src/api.generated.ts", "loc": 3000, "ccn": 9, "commits": 40},
            {"path": "src/legacy.go", "loc": 500, "ccn": 20, "commits": 5},
        ],
        "top_complex": [], "top_large": [],
    }
    (assess_dir / "complexity-stats.prior.json").write_text(json.dumps(prior_stats))
    current_stats = {
        "files_scored": 49, "loc": {}, "ccn": {},
        "top_hotspots": [{"path": "src/new.go", "loc": 400, "ccn": 18, "commits": 4}],
        "top_complex": [], "top_large": [], "excluded_generated": [],
    }
    (assess_dir / "complexity-stats.json").write_text(json.dumps(current_stats))

    ctx = build_run_context(repo_root=repo, run_date="2026-09-19")
    assert ctx["diff"]["graduated"] == 1
    index = (assess_dir / "index.md").read_text()
    assert "src/legacy.go" in index
    assert "database.types.ts" not in index
    assert "api.generated.ts" not in index


def _stats(top: list[dict]) -> dict:
    return {
        "files_scored": 10, "loc": {}, "ccn": {},
        "top_hotspots": top,
        "top_complex": [{"path": h["path"], "ccn": h["ccn"]} for h in top],
        "top_large": [{"path": h["path"], "loc": h["loc"]} for h in top],
    }


def test_index_keeps_graduated_row_absent_from_later_diff(tmp_path: Path) -> None:
    """#420: a hotspot that graduated in run one and is in neither run two's
    top hotspots nor its graduations keeps its index row; #421: a page written
    by both runs keeps both history rows, ordered by date."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    legacy = {"path": "src/legacy.go", "loc": 800, "ccn": 90, "commits": 3}
    new = {"path": "src/new.go", "loc": 600, "ccn": 80, "commits": 4}
    new_later = {"path": "src/new.go", "loc": 650, "ccn": 85, "commits": 6}

    # Run one: legacy graduates, new enters.
    (assess_dir / "complexity-stats.prior.json").write_text(json.dumps(_stats([legacy])))
    (assess_dir / "complexity-stats.json").write_text(json.dumps(_stats([new])))
    build_run_context(repo_root=repo, run_date="2026-05-29")
    index_one = (assess_dir / "index.md").read_text(encoding="utf-8")
    assert "graduated" in next(r for r in index_one.splitlines() if "src/legacy.go" in r)

    # Run two: new persists; legacy is in neither top_hotspots nor graduated.
    (assess_dir / "complexity-stats.prior.json").write_text(json.dumps(_stats([new])))
    (assess_dir / "complexity-stats.json").write_text(json.dumps(_stats([new_later])))
    build_run_context(repo_root=repo, run_date="2026-06-05")
    index_two = (assess_dir / "index.md").read_text(encoding="utf-8")
    legacy_row = next(r for r in index_two.splitlines() if "src/legacy.go" in r)
    assert "| graduated |" in legacy_row
    assert "2026-05-29" in legacy_row  # last seen stays at the run that saw it

    page = (assess_dir / "hotspots" / f"{slug_for_path('src/new.go')}.md").read_text()
    history = page.split("## History across runs", 1)[1].split("##", 1)[0]
    rows = [r for r in history.splitlines() if r.startswith("| 2026-")]
    assert [r.split(" | ")[0] for r in rows] == ["| 2026-05-29", "| 2026-06-05"]
    assert "| 600 | 80 | 4 | new |" in rows[0]
    assert "| 650 | 85 | 6 |" in rows[1]


# --- index rows for paths absent from this run (#420) -------------------------
#
# Each test is a run sequence on the real core: run one ranks the path, which
# writes its hotspot page and its index row, so later runs start from both. One
# test per reason a previously indexed path can be absent from this run's set.

_KEEP = {"path": "src/keep.go", "loc": 300, "ccn": 30, "commits": 3}


def _absent_repo(tmp_path: Path, path: str) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text("package main\n", encoding="utf-8")
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "keep.go").write_text("package main\n", encoding="utf-8")
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    return repo, assess_dir


def _run(repo: Path, top: list[dict], day: str, **extra: object) -> dict:
    """Rotate the stats sidecar like the treemap does, then run the core."""
    assess_dir = repo / ".assess"
    current = assess_dir / "complexity-stats.json"
    if current.exists():
        (assess_dir / "complexity-stats.prior.json").write_text(current.read_text())
    current.write_text(json.dumps({**_stats(top), **extra}))
    return build_run_context(repo_root=repo, run_date=day)


def _index_row(assess_dir: Path, path: str) -> str | None:
    index = (assess_dir / "index.md").read_text(encoding="utf-8")
    return next((r for r in index.splitlines() if f"| `{path}` |" in r), None)


def _seeded(assess_dir: Path, path: str) -> None:
    """Run one left both a page and an index row for ``path``."""
    assert (assess_dir / "hotspots" / f"{slug_for_path(path)}.md").exists()
    assert _index_row(assess_dir, path) is not None


def test_index_row_for_path_graduating_this_run(tmp_path: Path) -> None:
    hot = {"path": "src/hot.go", "loc": 900, "ccn": 90, "commits": 9}
    repo, assess_dir = _absent_repo(tmp_path, hot["path"])
    _run(repo, [hot, _KEEP], "2026-06-01")
    _seeded(assess_dir, hot["path"])
    _run(repo, [_KEEP], "2026-06-08")
    row = _index_row(assess_dir, hot["path"])
    assert row is not None and "| 2026-06-08 | graduated |" in row


def test_index_row_for_path_graduated_in_an_earlier_run(tmp_path: Path) -> None:
    hot = {"path": "src/hot.go", "loc": 900, "ccn": 90, "commits": 9}
    repo, assess_dir = _absent_repo(tmp_path, hot["path"])
    _run(repo, [hot, _KEEP], "2026-06-01")
    _seeded(assess_dir, hot["path"])
    _run(repo, [_KEEP], "2026-06-08")
    _run(repo, [_KEEP], "2026-06-15")
    row = _index_row(assess_dir, hot["path"])
    assert row is not None and "| 2026-06-08 | graduated |" in row


def test_index_row_for_deleted_file(tmp_path: Path) -> None:
    """The run that drops a deleted file counts it graduated, as diff.graduated
    does, while its page is retired; every later run carries the page's
    retired status."""
    hot = {"path": "src/hot.go", "loc": 900, "ccn": 90, "commits": 9}
    repo, assess_dir = _absent_repo(tmp_path, hot["path"])
    _run(repo, [hot, _KEEP], "2026-06-01")
    _seeded(assess_dir, hot["path"])
    (repo / hot["path"]).unlink()
    ctx = _run(repo, [_KEEP], "2026-06-08")
    assert [g["path"] for g in ctx["diff_detail"]["graduated"]] == [hot["path"]]
    row = _index_row(assess_dir, hot["path"])
    assert row is not None and "| graduated |" in row
    _run(repo, [_KEEP], "2026-06-15")
    row = _index_row(assess_dir, hot["path"])
    assert row is not None and "| retired - file deleted |" in row


def test_index_row_for_file_excluded_by_config_after_finalized_run(tmp_path: Path) -> None:
    """A config exclude after a run on another day is outside #356: the core
    counts the file graduated, and later runs carry it as graduated."""
    hot = {"path": "vendor/fin.go", "loc": 900, "ccn": 90, "commits": 9}
    repo, assess_dir = _absent_repo(tmp_path, hot["path"])
    _run(repo, [hot, _KEEP], "2026-06-01")
    _seeded(assess_dir, hot["path"])
    (assess_dir / "config.toml").write_text('exclude_dirs = ["vendor"]\n')
    ctx = _run(repo, [_KEEP], "2026-06-08")
    assert [g["path"] for g in ctx["diff_detail"]["graduated"]] == [hot["path"]]
    _run(repo, [_KEEP], "2026-06-15")
    row = _index_row(assess_dir, hot["path"])
    assert row is not None and "| 2026-06-08 | graduated |" in row


def test_index_drops_file_excluded_before_finalize(tmp_path: Path) -> None:
    """#356: a file first flagged only by a never-finalized run and then
    excluded is retired as excluded before finalize and loses the index row
    that unfinalized run wrote."""
    hot = {"path": "gen/big.go", "loc": 900, "ccn": 90, "commits": 9}
    repo, assess_dir = _absent_repo(tmp_path, hot["path"])
    _run(repo, [_KEEP], "2026-06-01")
    _run(repo, [hot, _KEEP], "2026-06-08")  # flags gen/big.go, never finalized
    _seeded(assess_dir, hot["path"])
    (assess_dir / "config.toml").write_text('exclude_dirs = ["gen"]\n')
    ctx = _run(repo, [_KEEP], "2026-06-08")
    assert ctx["retired_excluded_hotspots"] == [hot["path"]]
    assert _index_row(assess_dir, hot["path"]) is None
    _run(repo, [_KEEP], "2026-06-15")
    assert _index_row(assess_dir, hot["path"]) is None


@pytest.mark.parametrize("path, excluded_generated", [
    ("db/schema.sql", [{"path": "db/schema.sql", "reason": "generated-header"}]),
    ("web/src/database.types.ts", []),  # generated-name glob, silent
])
def test_index_drops_file_now_excluded_as_generated(
    tmp_path: Path, path: str, excluded_generated: list[dict],
) -> None:
    """A ranked file the generated filter now drops did not graduate: its
    carried index row and the row its page would backfill are both dropped,
    this run and the next."""
    hot = {"path": path, "loc": 900, "ccn": 90, "commits": 9}
    repo, assess_dir = _absent_repo(tmp_path, path)
    _run(repo, [hot, _KEEP], "2026-06-01")
    _seeded(assess_dir, path)
    ctx = _run(repo, [_KEEP], "2026-06-08", excluded_generated=excluded_generated)
    assert ctx["diff"]["graduated"] == 0
    assert _index_row(assess_dir, path) is None
    _run(repo, [_KEEP], "2026-06-15", excluded_generated=excluded_generated)
    assert _index_row(assess_dir, path) is None


def test_every_live_hotspot_page_has_an_index_row(tmp_path: Path) -> None:
    """#420 success criterion: after a run, every non-retired hotspot page,
    including one orphaned from the index by an older writer, has a row."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "orphan.go").write_text("package main\n")
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    write_hotspot_page(
        assess_dir, path="src/orphan.go", first_flagged="2026-01-01",
        last_seen="2026-02-01", status="graduated", loc=300, ccn=40, commits=2,
        has_tests=None, briefing="x", actions="- y",
    )
    (assess_dir / "index.md").write_text("# Assess Wiki Index\n\n| File |\n")
    (assess_dir / "complexity-stats.json").write_text(json.dumps(_stats(
        [{"path": "src/foo.go", "loc": 500, "ccn": 20, "commits": 5}])))
    build_run_context(repo_root=repo, run_date="2026-06-05")

    index = (assess_dir / "index.md").read_text(encoding="utf-8")
    for page in (assess_dir / "hotspots").glob("*.md"):
        content = page.read_text(encoding="utf-8")
        if is_retired_status(hotspot_page_status(content)):
            continue
        assert f"| `{hotspot_page_source_path(content)}` |" in index
    assert "| `src/orphan.go` | 2026-01-01 | 2026-02-01 | graduated | 40 | 300 |" in index


def test_hotspot_page_names_worst_function_and_aggregate(tmp_path: Path) -> None:
    """#423: the file ccn is labelled an aggregate and the worst function from
    the sidecar gets its own row and a mention in the briefing."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assess_dir = repo / ".assess"
    assess_dir.mkdir()
    (assess_dir / "complexity-stats.json").write_text(json.dumps(_stats([
        {"path": "src/a.py", "loc": 900, "ccn": 159.0, "commits": 13,
         "max_fn_ccn": 8.0, "max_fn_name": "test_scan"},
        {"path": "src/b.go", "loc": 400, "ccn": 30, "commits": 2,
         "max_fn_ccn": None, "max_fn_name": None},
    ])))
    build_run_context(repo_root=repo, run_date="2026-06-05")
    hot = assess_dir / "hotspots"
    a = (hot / f"{slug_for_path('src/a.py')}.md").read_text()
    b = (hot / f"{slug_for_path('src/b.go')}.md").read_text()
    assert "| Cyclomatic complexity (file aggregate) | 159 |" in a
    assert "| Worst function | `test_scan` (8) |" in a
    assert "aggregate cyclomatic complexity 159 (worst function `test_scan` 8)" in a
    assert "file max" not in a and "max cyclomatic" not in a
    assert "Worst function" not in b
    assert "aggregate cyclomatic complexity 30, " in b
