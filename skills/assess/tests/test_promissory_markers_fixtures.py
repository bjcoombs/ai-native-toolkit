"""Fixture files carry no promissory markers (issue #824).

A linter's fixtures are deliberate ``noqa`` and ``# type: ignore`` samples, and a
test-data tree is input to a test, not code anyone promised to fix. The scan
sets aside every file ``lib.path_classes.path_class`` reads as ``fixture`` (at
any depth: ``fixtures/``, ``__fixtures__/``, ``testdata/``, ``resources/test/``,
``mdtest/``, ``snapshots/``) and reports how many such files it set aside in
``fixture_files_excluded``. Test files, examples and source whose own name only
looks fixture-like still count, so a repository never reads a false 0.
Fixture strings below carry marker text on purpose.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from lib.counts_only import counts_only
from lib.keyhole.promissory_markers import MarkerScan, scan_promissory_markers

_ENV = {
    "GIT_AUTHOR_NAME": "Dev One", "GIT_AUTHOR_EMAIL": "dev@example.com",
    "GIT_COMMITTER_NAME": "Dev One", "GIT_COMMITTER_EMAIL": "dev@example.com",
}


def _git(repo: Path, *args: str, day: int = 1) -> None:
    date = f"2024-01-{day:02d}T12:00:00"
    env = {**os.environ, **_ENV, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   text=True, encoding="utf-8", errors="surrogateescape", env=env)


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _commit(repo, files, day=1)
    return repo


def _commit(repo: Path, files: dict[str, str], *, day: int) -> None:
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A", day=day)
    _git(repo, "commit", "-q", "-m", f"day {day}", day=day)


def _scan(repo: Path, **kw: object) -> MarkerScan:
    scan = scan_promissory_markers(repo, **kw)  # type: ignore[arg-type]
    assert scan.available, scan.reason
    return scan


NOQA = "x = 1  # noqa: E501\n"


def test_linter_fixture_noqa_is_not_a_suppression(tmp_path: Path) -> None:
    """The issue's success criterion: one fixture noqa, one source noqa, total 1."""
    repo = _repo(tmp_path, {
        "crates/x/resources/test/fixtures/a.py": NOQA,
        "src/b.py": NOQA,
    })
    summary = _scan(repo).summary()
    assert summary["families"]["suppression"]["total"] == 1
    assert [m["path"] for m in summary["top_offenders"]] == []
    assert summary["fixture_files_excluded"] == 1


@pytest.mark.parametrize("fixture_path", [
    "tests/fixtures/a.py",                     # the #486 exclusion, now via the classifier
    "pkg/fixtures/a.py",
    "web/__fixtures__/a.py",
    "internal/parser/testdata/a.py",
    "crates/ruff/resources/test/a.py",
    "crates/ruff/resources/mdtest/suppression/a.py",
    "ui/__snapshots__/a.py",
    "crates/x/snapshots/a.py",
    "Fixtures/a.py",                           # directory segments compare case-insensitively
])
def test_every_fixture_form_is_set_aside(tmp_path: Path, fixture_path: str) -> None:
    repo = _repo(tmp_path, {
        fixture_path: "# TODO fixture\n" + NOQA + "import pytest\n@pytest.mark.skip\n",
    })
    summary = _scan(repo).summary()
    assert summary["total_markers"] == 0
    assert summary["families"] == {}
    assert summary["fixture_files_excluded"] == 1


@pytest.mark.parametrize("source_path", [
    "src/fixtures.py",                         # a file name is never read as a directory
    "src/snapshots.py",
    "src/testdata_loader.py",
    "src/fixture_loader/a.py",
    "app/resources/main/a.py",
    "app/test_resources/a.py",
    "tests/test_x.py",                         # a test file is a test, not a fixture
    "tests/conftest.py",
    "examples/a.py",
])
def test_fixture_like_source_still_counts(tmp_path: Path, source_path: str) -> None:
    repo = _repo(tmp_path, {source_path: NOQA})
    summary = _scan(repo).summary()
    assert summary["families"]["suppression"]["total"] == 1
    assert summary["fixture_files_excluded"] == 0


def test_disabled_test_in_a_test_file_still_counts(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {
        "tests/test_x.py": "import pytest\n\n@pytest.mark.skip\ndef test_a():\n    pass\n",
        "tests/fixtures/sample_test.py": "import pytest\n\n@pytest.mark.skip\ndef test_b():\n    pass\n",
    })
    summary = _scan(repo).summary()
    assert summary["families"]["disabled_test"]["total"] == 1
    assert summary["fixture_files_excluded"] == 1


def test_a_stale_fixture_noqa_never_reads_stale(tmp_path: Path) -> None:
    fixture, source = "resources/test/fixtures/W605.py", "src/app.py"
    repo = _repo(tmp_path, {fixture: NOQA, source: NOQA})
    for day in range(2, 8):  # six later touches to each file
        _commit(repo, {fixture: NOQA + f"y = {day}\n", source: NOQA + f"y = {day}\n"}, day=day)
    summary = _scan(repo, stale_touches=5).summary()
    assert summary["families"]["suppression"]["stale"] == 1
    assert list(summary["stale_by_file"]) == [source]


def test_excluded_count_follows_the_scope(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {
        "a/fixtures/one.py": NOQA,
        "a/fixtures/two.py": NOQA,
        "a/src.py": NOQA,
        "b/fixtures/three.py": NOQA,
    })
    summary = _scan(repo, scope=repo / "a").summary()
    assert summary["fixture_files_excluded"] == 2
    assert summary["families"]["suppression"]["total"] == 1


def test_a_fixture_file_counts_once_however_many_markers(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {"fixtures/a.py": "# TODO one\n" + NOQA * 3})
    assert _scan(repo).summary()["fixture_files_excluded"] == 1


def test_unavailable_scan_reports_zero_excluded() -> None:
    assert MarkerScan(available=False, reason="x").summary()["fixture_files_excluded"] == 0


def test_counts_only_keeps_the_excluded_count() -> None:
    ctx = {"promissory_markers": {"available": True, "fixture_files_excluded": 119}}
    assert counts_only(ctx) == ctx
