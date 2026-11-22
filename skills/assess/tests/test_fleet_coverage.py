"""Tests for fleet coverage against ghsync's discovery listing (issue #745): lib/fleet_coverage.py
and ``fleet.py --discovered``."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import get_type_hints

import jsonschema
import pytest

from lib import fleet_compare, fleet_history
from lib import fleet_coverage as fc
from lib.fleet import STATUS_FAILED, STATUS_OK, FleetRow
from lib.fleet_schema import build_stamp_schema
from lib.fleet_stamp import build_stamp
from test_fleet import make_repo
from test_fleet_cli import fleet_cli

ZEROS = "0" * 40


def _line(path: str, *, archived: bool = False, branch: str | None = "main") -> str:
    return json.dumps({"path": path, "default_branch": branch, "last_activity_at": "2026-11-01T10:00:00Z",
                       "head_committed_at": "2026-11-01T10:00:00Z", "archived": archived,
                       "forked_from": None, "mirror": False, "head_sha": ZEROS})


def _row(repo: str, status: str = STATUS_OK) -> FleetRow:
    ok = status == STATUS_OK
    return {"fleet_row_version": 2, "repo": repo, "group": repo.rsplit("/", 1)[0], "status": status,
            "reason": None if ok else "not_a_git_work_tree", "signals_schema_version": 1 if ok else None,
            "scans": {} if ok else None, "active": True if ok else None}


# --- parsing ---------------------------------------------------------------


def test_both_ghsync_listing_formats_parse_and_a_repeat_counts_once():
    text = "\n".join(["# from ghsync", "grp/plain/", "", _line("grp/a"), _line("grp/c", archived=True),
                      _line("grp/empty", branch=None), _line("grp/blank", branch=""), "grp/a"])
    assert fc.parse_discovery(text) == [
        fc.Discovered("grp/plain", False), fc.Discovered("grp/a", False), fc.Discovered("grp/c", True),
        fc.Discovered("grp/empty", True), fc.Discovered("grp/blank", True)]


@pytest.mark.parametrize("text, line", [
    ("grp/a\n{not json", 2),
    ('{"default_branch": "main"}', 1),
    ('["grp/a"]', 1),
    ('{"path": "/"}', 1),
    ("grp/a\n/", 2),
    ("grp/../../etc", 1),
    ('{"path": "grp/./a"}', 1),
])
def test_a_malformed_line_names_its_number(text, line):
    with pytest.raises(fc.DiscoveryError, match=f"line {line}:"):
        fc.parse_discovery(text)


def test_ghsyncs_archived_cache_adds_the_archived_repositories_its_listing_leaves_out(tmp_path):
    (tmp_path / "list.txt").write_text("grp/a\ngrp/b\n", encoding="utf-8")
    (tmp_path / fc.ARCHIVED_CACHE).write_text("grp/b\ngrp/old\n", encoding="utf-8")
    assert fc.read_discovery(tmp_path / "list.txt", tmp_path) == [
        fc.Discovered("grp/a", False), fc.Discovered("grp/b", False), fc.Discovered("grp/old", True)]


def test_a_malformed_archived_cache_is_named(tmp_path):
    (tmp_path / "list.txt").write_text("grp/a\n", encoding="utf-8")
    (tmp_path / fc.ARCHIVED_CACHE).write_text("../outside\n", encoding="utf-8")
    with pytest.raises(fc.DiscoveryError, match="archived-repos cache .*line 1:"):
        fc.read_discovery(tmp_path / "list.txt", tmp_path)


def test_an_unreadable_listing_is_a_discovery_error(tmp_path):
    with pytest.raises(fc.DiscoveryError, match="cannot read"):
        fc.read_discovery(tmp_path / "absent.jsonl", tmp_path)


# --- reconciling -----------------------------------------------------------


def test_each_discovered_path_lands_in_one_bucket(tmp_path):
    root = tmp_path / "org"
    make_repo(root, "grp/left-out")  # cloned, but this run was not given it
    entries = [fc.Discovered(p, a) for p, a in [
        ("grp/a", False), ("grp/b", True), ("grp/c", True), ("grp/d", False), ("grp/e", False),
        ("grp/left-out", False), ("solo", False)]]
    rows = [_row("grp/a"), _row("grp/b"), _row("grp/e", STATUS_FAILED), _row("org/solo"), _row("grp/extra")]

    coverage = fc.reconcile(entries, rows, root, frozenset(r["repo"] for r in rows))

    # grp/b is archived but assessed: a row wins; solo is a one-segment GitHub path
    assert coverage == {"discoverable": 7, "assessed": 3, "archived_or_empty": 1, "not_cloned": 1,
                        "failed_scan": 1, "excluded_by_filters": 1, "not_in_listing": 1}
    gaps = ("assessed", "archived_or_empty", "not_cloned", "failed_scan", "excluded_by_filters")
    assert sum(coverage[k] for k in gaps) == coverage["discoverable"]  # type: ignore[literal-required]


def test_the_coverage_line_counts_each_gap_or_says_unknown():
    coverage: fc.Coverage = {"discoverable": 5, "assessed": 2, "archived_or_empty": 1, "not_cloned": 1,
                             "failed_scan": 1, "excluded_by_filters": 0, "not_in_listing": 1}
    known = fc.coverage_line(coverage, 4)
    assert known.startswith("**Coverage:** 2 of 5 discoverable repositories were assessed.")
    for part in ("1 archived or empty", "1 discovered but not cloned", "1 cloned but could not be scanned",
                 "0 excluded by filters", "1 repository scanned here is not in the listing"):
        assert part in known
    unknown = fc.coverage_line(None, 3)
    assert unknown.startswith("**Coverage:** unknown.")
    assert "It scanned 3 repositories." in unknown
    assert " all " not in unknown


def test_coverage_is_counts_only_every_field_an_integer_in_the_stamp_schema():
    """A dropped or renamed field fails here and in the published run.json schema."""
    fields = {"discoverable", "assessed", "archived_or_empty", "not_cloned", "failed_scan",
              "excluded_by_filters", "not_in_listing"}
    assert set(get_type_hints(fc.Coverage)) == fields
    schema = build_stamp_schema()["properties"]["coverage"]
    described = next(m for m in schema["anyOf"] if m.get("type") == "object")
    assert set(described["required"]) == fields
    assert all(v == {"type": "integer"} for v in described["properties"].values())


# --- the CLI ---------------------------------------------------------------


def _coverage_root(tmp_path: Path) -> tuple[Path, Path]:
    """The contract's I745-1 fixture: grp/a, grp/b cloned, grp/e with no .git, grp/c (archived)
    and grp/d absent, all five in the listing."""
    root = tmp_path / "cv"
    for name in ("a", "b"):
        make_repo(root, f"grp/{name}", files={"main.py": "print(1)\n"})
    (root / "grp" / "e" / "e-main").mkdir(parents=True)
    (root / "grp" / "e" / "e-main" / "README.md").write_text("x\n", encoding="utf-8")
    listing = tmp_path / "disc.jsonl"
    listing.write_text("".join(_line(f"grp/{p}", archived=p == "c") + "\n" for p in "abcde"), encoding="utf-8")
    return root, listing


def test_the_report_and_stamp_state_two_of_five_assessed_with_each_gap(tmp_path):
    root, listing = _coverage_root(tmp_path)
    out = tmp_path / "c1"
    assert fleet_cli.main([str(root), "--discovered", str(listing), "--no-history", "--out", str(out)]) == 0

    report = (out / "report.md").read_text(encoding="utf-8")
    line = next(t for t in report.splitlines() if t.startswith("**Coverage:**"))
    assert report.index(line) < report.index("Drawn from") < report.index("\n## ")  # the first paragraph
    assert "2 of 5 discoverable repositories were assessed" in line
    for part in ("1 archived or empty", "1 discovered but not cloned", "1 cloned but could not be scanned",
                 "0 excluded by filters"):
        assert part in line
    stamp = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert stamp["coverage"] == {"discoverable": 5, "assessed": 2, "archived_or_empty": 1, "not_cloned": 1,
                                 "failed_scan": 1, "excluded_by_filters": 0, "not_in_listing": 0}
    jsonschema.validate(stamp, build_stamp_schema())
    for name in ("grp/c", "grp/d"):  # counts only: an unassessed repository is never named
        assert name not in report and name not in json.dumps(stamp)


def test_without_a_listing_coverage_reads_unknown(tmp_path):
    root, _listing = _coverage_root(tmp_path)
    out = tmp_path / "c2"
    assert fleet_cli.main([str(root), "--no-history", "--out", str(out)]) == 0
    report = (out / "report.md").read_text(encoding="utf-8")
    assert "**Coverage:** unknown." in report
    assert "all repositories" not in report.lower()
    assert json.loads((out / "run.json").read_text(encoding="utf-8"))["coverage"] is None


def test_a_from_list_run_counts_the_clones_it_left_out_as_excluded_by_filters(tmp_path):
    root, listing = _coverage_root(tmp_path)
    (tmp_path / "only-a.txt").write_text("grp/a\n", encoding="utf-8")
    out = tmp_path / "c3"
    assert fleet_cli.main(["--from-list", str(tmp_path / "only-a.txt"), str(root), "--discovered", str(listing),
                           "--no-history", "--no-diagrams", "--out", str(out)]) == 0
    coverage = json.loads((out / "run.json").read_text(encoding="utf-8"))["coverage"]
    assert coverage["assessed"] == 1
    assert coverage["excluded_by_filters"] == 2  # grp/b and grp/e are cloned but not listed
    assert coverage["archived_or_empty"] == coverage["not_cloned"] == 1


def test_discovered_needs_root_and_a_well_formed_listing(tmp_path, capsys):
    root, _listing = _coverage_root(tmp_path)
    (tmp_path / "list.txt").write_text("grp/a\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        fleet_cli.main(["--from-list", str(tmp_path / "list.txt"), "--discovered", str(tmp_path / "list.txt"),
                        "--out", str(tmp_path / "o")])
    assert exc.value.code == 2
    (tmp_path / "bad.jsonl").write_text('{"path": "grp/a"}\n{oops\n', encoding="utf-8")
    out = tmp_path / "o2"
    assert fleet_cli.main([str(root), "--discovered", str(tmp_path / "bad.jsonl"), "--out", str(out)]) == 2
    err = capsys.readouterr().err
    assert "error: --discovered" in err and "line 2:" in err
    assert not out.exists()


def test_coverage_is_no_comparability_key():
    """A run with --discovered still matches and compares with one without it."""
    coverage: fc.Coverage = {"discoverable": 1, "assessed": 1, "archived_or_empty": 0, "not_cloned": 0,
                             "failed_scan": 0, "excluded_by_filters": 0, "not_in_listing": 0}
    kwargs = dict(toolkit_commit="c", plugin_version="2.1.0", today=date(2026, 11, 22), window="6mo",
                  levels_version=None, counts_only=True, paths_hashed=False, scans=None)
    without = build_stamp([_row("grp/a")], **kwargs)  # type: ignore[arg-type]
    with_coverage = build_stamp([_row("grp/a")], coverage=coverage, **kwargs)  # type: ignore[arg-type]
    assert fleet_compare.comparability(without, with_coverage) == ([], [])
    assert "coverage" not in fleet_history._MATCH_KEYS


def test_a_listed_entry_with_no_clone_is_not_cloned_never_a_failed_scan(tmp_path):
    """A --from-list run over the listing itself: grp/d has no checkout (missing_checkout)."""
    root, listing = _coverage_root(tmp_path)
    (tmp_path / "list.txt").write_text("grp/a\ngrp/b\ngrp/d\ngrp/e\n", encoding="utf-8")
    out = tmp_path / "c4"
    assert fleet_cli.main(["--from-list", str(tmp_path / "list.txt"), str(root), "--discovered", str(listing),
                           "--no-history", "--no-diagrams", "--out", str(out)]) == 0
    coverage = json.loads((out / "run.json").read_text(encoding="utf-8"))["coverage"]
    assert coverage == {"discoverable": 5, "assessed": 2, "archived_or_empty": 1, "not_cloned": 1,
                        "failed_scan": 1, "excluded_by_filters": 0, "not_in_listing": 0}


def test_two_spellings_of_one_repository_count_once(tmp_path):
    root = tmp_path / "org"
    entries = [fc.Discovered("svc", False), fc.Discovered("org/svc", False)]
    coverage = fc.reconcile(entries, [_row("org/svc")], root, frozenset({"org/svc"}))
    assert coverage["discoverable"] == coverage["assessed"] == 1


def test_a_stamp_from_before_745_without_coverage_is_valid():
    stamp = build_stamp([_row("grp/a")], toolkit_commit="c", plugin_version="2.1.0", today=date(2026, 11, 22),
                        window="6mo", levels_version=None, counts_only=True, paths_hashed=False, scans=None)
    old = {k: v for k, v in stamp.items() if k != "coverage"}
    jsonschema.validate(old, build_stamp_schema())
    assert "coverage" not in build_stamp_schema()["required"]
