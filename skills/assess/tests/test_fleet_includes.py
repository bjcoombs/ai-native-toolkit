"""Tests for the CI include projects a fleet run could not read (issue #1355): lib/fleet/fleet_includes.py,
its ``run.json`` key and its ``report.md`` section."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import jsonschema
import pytest

import lib.fleet.fleet_includes as fi
from lib.fleet.fleet_data import RUN_EXCLUDED
from lib.fleet.fleet_diagrams import report_markdown
from lib.fleet.runner import STATUS_FAILED, STATUS_OK, FleetRow
from lib.fleet.fleet_schema import build_stamp_schema
from lib.fleet.fleet_stamp import build_stamp
from lib.vocabulary import IN_PLACE, banned_hits
from test_fleet import make_repo
from test_fleet_cli import fleet_cli

MISSING = "grp/ci-templates"
CI = (f"include:\n  - project: {MISSING}\n    file: /test.yml\n"
      "  - component: gitlab.example.com/grp/components/lint@1.0\n")


def _record(kind: str, ident: str | None, status: str = fi.NOT_CLONED) -> dict[str, Any]:
    return {"kind": kind, "id": ident, "status": status, "ref_basis": None, "depth": 1}


def _row(repo: str, records: list[dict[str, Any]], status: str = STATUS_OK) -> FleetRow:
    ok = status == STATUS_OK
    return {"fleet_row_version": 2, "repo": repo, "group": repo.rsplit("/", 1)[0], "status": status,
            "reason": None if ok else "scan_error", "signals_schema_version": 1 if ok else None,
            "scans": {"ci_checks": {"includes": records}} if ok else None, "active": True if ok else None,
            "head_sha": None, "dirty": None, "dirty_reason": None}


def _rows() -> list[FleetRow]:
    project = _record("project", f"gitlab_project:{MISSING}")
    component = _record("component", "gitlab_component:grp/components/lint")
    other = _record("component", "gitlab_component:grp/components/build")  # same project
    return [
        _row("g/a", [project, project, component, other]),  # named twice: still one repository
        _row("g/b", [project, _record("project", "gitlab_project:grp/other", "resolved")]),
        _row("g/c", [project, _record("local", None, "file_missing"), _record("template", None, "not_offline")]),
        _row("g/d", [], STATUS_FAILED),
    ]


def test_three_repositories_including_one_project_list_it_once_with_count_3_and_its_kind() -> None:
    assert fi.unresolved_includes(_rows(), counts_only=False, paths_hashed=False) == [
        {"project": MISSING, "kind": "project", "repositories": 3},
        {"project": "grp/components", "kind": "component", "repositories": 1},
    ]


def test_counts_only_keeps_counts_and_kind_and_drops_the_path() -> None:
    entries = fi.unresolved_includes(_rows(), counts_only=True, paths_hashed=False)
    assert entries == [{"project": None, "kind": "project", "repositories": 3},
                       {"project": None, "kind": "component", "repositories": 1}]


def test_hashed_runs_keep_the_rows_hashed_id_so_it_joins_repos_jsonl() -> None:
    rows = [_row("g/a", [_record("project", "h:0123456789abcdef")]),
            _row("g/b", [_record("project", "h:0123456789abcdef")])]
    assert fi.unresolved_includes(rows, counts_only=True, paths_hashed=True) == [
        {"project": "h:0123456789abcdef", "kind": "project", "repositories": 2}]


def test_rows_without_ci_includes_give_no_entry() -> None:
    row = _row("g/a", [])
    row["scans"] = {"ci_checks": {"available": False}}
    bare = _row("g/b", [])
    bare["scans"] = {}
    dropped = _row("g/c", [_record("project", None)])  # an id counts-only output dropped
    assert fi.unresolved_includes([row, bare, dropped], counts_only=True, paths_hashed=False) == []
    assert fi.includes_section([]) is None
    assert fi.includes_section(None) is None


@pytest.mark.parametrize("counts_only", [True, False])
def test_the_stamp_carries_the_list_only_when_there_is_one_and_matches_its_schema(counts_only: bool) -> None:
    kwargs: dict[str, Any] = {"toolkit_commit": None, "plugin_version": "1.0.0", "today": date(2026, 11, 25),
                              "window": "6mo", "levels_version": None, "counts_only": counts_only,
                              "paths_hashed": False, "scans": None}
    stamp = build_stamp(_rows(), **kwargs)
    jsonschema.validate(stamp, build_stamp_schema())
    assert [e["repositories"] for e in stamp["unresolved_includes"]] == [3, 1]
    assert (MISSING in json.dumps(stamp)) is not counts_only
    assert "unresolved_includes" not in build_stamp([_row("g/a", [])], **kwargs)
    assert "unresolved_includes" in RUN_EXCLUDED


def test_the_report_section_names_projects_under_full_and_none_in_counts_only() -> None:
    full = fi.includes_section(fi.unresolved_includes(_rows(), counts_only=False, paths_hashed=False))
    counts = fi.includes_section(fi.unresolved_includes(_rows(), counts_only=True, paths_hashed=False))
    assert full is not None and counts is not None
    assert f"| `{MISSING}` | project | 3 repositories |" in full
    assert "| `grp/components` | component | 1 repository |" in full
    assert MISSING not in counts and "grp/components" not in counts
    assert "| (not named) | project | 3 repositories |" in counts
    for section in (full, counts):
        assert section.splitlines()[2].startswith(IN_PLACE)
        assert not banned_hits(section)
    report = report_markdown([], coverage="**Coverage:** unknown.", includes=full)
    assert report.index("**Coverage:**") < report.index("## CI include projects not cloned")


def _gitlab_root(root: Path) -> None:
    for name in ("a", "b", "c"):
        make_repo(root, f"grp/{name}", files={".gitlab-ci.yml": CI, "README.md": "x\n"})


def test_a_fleet_run_lists_the_project_once_and_a_counts_only_run_names_no_include_path(tmp_path: Path) -> None:
    root = tmp_path / "froot"
    _gitlab_root(root)
    full, counts = tmp_path / "full", tmp_path / "counts"
    assert fleet_cli.main([str(root), "--full", "--no-history", "--scans", "ci_checks", "--out", str(full)]) == 0
    assert fleet_cli.main([str(root), "--no-history", "--scans", "ci_checks", "--out", str(counts)]) == 0

    stamp = json.loads((full / "run.json").read_text(encoding="utf-8"))
    assert stamp["unresolved_includes"] == [
        {"project": MISSING, "kind": "project", "repositories": 3},
        {"project": "grp/components", "kind": "component", "repositories": 3},
    ]
    assert f"`{MISSING}`" in (full / "report.md").read_text(encoding="utf-8")

    counted = json.loads((counts / "run.json").read_text(encoding="utf-8"))
    assert [(e["project"], e["kind"], e["repositories"]) for e in counted["unresolved_includes"]] == [
        (None, "project", 3), (None, "component", 3)]
    for name in ("run.json", "report.md"):
        text = (counts / name).read_text(encoding="utf-8")
        assert MISSING not in text and "grp/components" not in text, name
