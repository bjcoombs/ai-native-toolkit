"""Tests for lib/complexity/stats_rows.py and the ``files`` key it gives complexity-stats.json.

The ``files`` list carries every scored file's aggregate CCN, so the
doc-complexity join sees a complex file that made none of the top-10 lists
(issue #556). The key is a layout change, so STATS_SCHEMA_VERSION moves to 6
and the cross-run diff is disarmed once against an older snapshot.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType

import pytest

from lib.complexity.diff_reliability import compute_diff_reliability
from lib.docs.doc_complexity_join import _extract_file_ccn, analyze_doc_complexity_join
from lib.run_context_types import ScoredHotspotRow
from lib.complexity.stats_rows import file_ccn_rows, strip_score, top_lists
from test_complexity_treemap import stubbed_treemap


@pytest.fixture(scope="module")
def treemap():
    with stubbed_treemap() as mod:
        yield mod


def _row(path: str, ccn: float, loc: int, score: float) -> ScoredHotspotRow:
    return {
        "path": path, "loc": loc, "est_tokens": loc * 10, "ccn": ccn,
        "ccn_basis": "file-aggregate", "max_fn_ccn": None, "max_fn_name": None,
        "commits": None, "source": "lizard", "_score": score,
    }


def test_file_ccn_rows_lists_every_file_sorted_by_path() -> None:
    rows = [_row(f"f{i:02d}.py", float(i), i, float(i)) for i in range(25, 0, -1)]
    out = file_ccn_rows(rows)
    assert len(out) == 25
    assert [r["path"] for r in out] == sorted(r["path"] for r in rows)
    assert out[0] == {"path": "f01.py", "ccn": 1.0}


def test_top_lists_cap_at_ten_and_strip_score() -> None:
    rows = [_row(f"f{i:02d}.py", float(i), i, float(i)) for i in range(1, 16)]
    lists = top_lists(rows)
    assert set(lists) == {"top_hotspots", "top_complex", "top_large"}
    assert all(len(v) == 10 for v in lists.values())
    assert lists["top_complex"][0]["path"] == "f15.py"
    assert all("_score" not in r for v in lists.values() for r in v)
    assert strip_score([]) == []


def _write_repo_stats(treemap: ModuleType, tmp_path: Path) -> dict:
    """Write real stats for 12 big complex files plus one small complex file.

    The small one (CCN 15, few lines) falls outside top_complex (ten files with
    CCN 20+ outrank it), top_large and top_hotspots, and 300 trivial files keep
    p95 under the MIN_HIGH_CCN floor of 10.
    """
    root = tmp_path
    files: list[tuple[Path, int, float, str]] = []
    for i in range(12):
        p = root / "big" / f"b{i:02d}.py"
        files.append((p, 2000 + i, 20.0 + i, "lizard"))
    for i in range(300):
        files.append((root / "triv" / f"t{i:02d}.py", 5, 1.0, "lizard"))
    files.append((root / "pkg" / "small.py", 30, 15.0, "lizard"))
    out = root / "stats.json"
    treemap.write_stats(files, None, None, root, out)
    return json.loads(out.read_text(encoding="utf-8"))


def test_write_stats_files_key_reaches_the_join(treemap: ModuleType, tmp_path: Path) -> None:
    stats = _write_repo_stats(treemap, tmp_path)
    assert stats["schema_version"] == treemap.STATS_SCHEMA_VERSION == 9
    assert len(stats["files"]) == stats["files_scored"] == 313
    top_paths = {r["path"] for k in ("top_complex", "top_large", "top_hotspots")
                 for r in stats[k]}
    assert "pkg/small.py" not in top_paths
    ccn = _extract_file_ccn(stats)
    assert set(ccn) == {r["path"] for r in stats["files"]}
    assert ccn["pkg/small.py"] == 15.0


def test_complex_file_outside_top_lists_yields_lying_map(treemap: ModuleType, tmp_path: Path) -> None:
    """A stale doc over a complex file no top-10 list holds is a lying map."""
    stats = _write_repo_stats(treemap, tmp_path)
    staleness = {"available": True, "docs": [{
        "path": "pkg/README.md", "ratio": 4.0, "confidence": "high",
        "subject_method": "nearest-ancestor", "subject_roots": ["pkg"],
        "base_doc_dir": "pkg",
    }]}
    result = analyze_doc_complexity_join(stats, staleness, tmp_path)
    assert [u["path"] for u in result["findings"]["lying_maps"]] == ["pkg/README.md"]
    without_files = {k: v for k, v in stats.items() if k != "files"}
    old = analyze_doc_complexity_join(without_files, staleness, tmp_path)
    assert old["findings"]["lying_maps"] == []


def test_stats_schema_bump_disarms_the_diff_once() -> None:
    prior = {"plugin_version": "1.110.0", "schema_version": 5}
    current = {"plugin_version": "1.128.0", "schema_version": 6}
    assert compute_diff_reliability(True, prior, current) == (
        False, "schema version changed 5->6", False)
    rearmed = {"plugin_version": "1.128.0", "schema_version": 6}
    assert compute_diff_reliability(True, rearmed, current) == (True, None, False)
