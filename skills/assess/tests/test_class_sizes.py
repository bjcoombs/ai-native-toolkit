"""The path classifier and the per-class file-size block behind L4 level 2 (#820)."""
from __future__ import annotations

import subprocess
from pathlib import Path, PureWindowsPath

import pytest

from lib import headless_layers as hl
from lib.scoring import level_tally
from lib.class_sizes import class_loc, loc_by_class, percentile
from lib.path_classes import PATH_CLASSES, is_production, path_class


@pytest.mark.parametrize("rel, expected", [
    ("src/app.py", "source"),
    ("packages/next/src/server/app-render/app-render.tsx", "source"),
    ("homeassistant/components/hue/light.py", "source"),
    ("lib/testing_helpers.py", "source"),       # no test segment, no test file name
    ("src/contest.py", "source"),
    ("tests/test_app.py", "test"),
    ("test/unit/thing.js", "test"),
    ("src/app_test.go", "test"),
    ("src/app.spec.ts", "test"),
    ("src/main/java/FooTest.java", "test"),
    ("pkg/__tests__/a.tsx", "test"),
    ("Tests/Thing.swift", "test"),               # directory segments ignore case
    ("tests/fixtures/sample.py", "fixture"),     # fixture wins over test
    ("crates/x/resources/test/fixtures/a.py", "fixture"),
    ("crates/ruff_linter/resources/test/W605_1.py", "fixture"),
    ("crates/ruff_linter/resources/mdtest/suppression/ignore.md", "fixture"),
    ("pkg/testdata/input.go", "fixture"),
    ("src/__fixtures__/a.ts", "fixture"),
    ("src/__snapshots__/a.snap", "fixture"),
    ("examples/basic/main.py", "example"),
    ("docs/samples/demo.py", "example"),
    ("examples/basic/tests/test_main.py", "test"),
    ("third_party/zlib/inflate.c", "vendored"),
    ("vendor/github.com/x/y.go", "vendored"),
    ("web/api.gen.ts", "generated"),
    ("vendor/schema.generated.go", "generated"),  # generated wins over vendored
    ("fixtures.py", "source"),                   # a file name is never read as a directory
])
def test_path_class(rel: str, expected: str) -> None:
    assert path_class(rel) == expected
    assert is_production(rel) is (expected == "source")


def test_a_windows_path_classifies_like_its_posix_spelling() -> None:
    assert path_class(PureWindowsPath(r"tests\fixtures\a.py")) == "fixture"
    assert path_class(Path("src") / "app.py") == "source"


def test_class_loc_reads_null_over_no_files() -> None:
    assert class_loc([]) == {"files_scored": 0, "p50": None, "p95": None, "max": None}
    assert class_loc([10.0, 20.0]) == {"files_scored": 2, "p50": 15.0, "p95": 19.5, "max": 20.0}


def test_loc_by_class_splits_every_class_and_counts_each_denominator() -> None:
    block = loc_by_class([("src/a.py", 900), ("tests/test_a.py", 40), ("tests/fixtures/f.py", 5),
                          ("examples/e.py", 12)])
    assert tuple(block) == PATH_CLASSES
    assert block["source"] == {"files_scored": 1, "p50": 900.0, "p95": 900.0, "max": 900.0}
    assert block["test"]["files_scored"] == 1 and block["test"]["p95"] == 40.0
    assert block["fixture"]["files_scored"] == 1 and block["example"]["files_scored"] == 1
    assert block["vendored"]["p95"] is None and block["generated"]["files_scored"] == 0


def test_percentile_is_reexported_for_the_headless_block() -> None:
    assert hl.percentile is percentile


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                   encoding="utf-8", errors="surrogateescape", check=True)


def _layer_4_2(stats: object) -> tuple[str, str | None]:
    signals = {"stats_summary": stats, "structure_drift": {"tier_0": {"boundary_patterns": 2}}}
    result = level_tally.level_result(level_tally.default_model(), signals)
    condition = next(c for c in result["conditions"] if c["id"] == "layer-4-architecture.2")
    return condition["state"], condition.get("reason")


@pytest.fixture
def fixture_heavy(tmp_path: Path) -> Path:
    """The issue's case: one 900-line source file and 50 five-line test fixtures."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "big.py").write_text("x = 1\n" * 900, encoding="utf-8")
    fixtures = repo / "tests" / "fixtures"
    fixtures.mkdir(parents=True)
    for n in range(50):
        (fixtures / f"case_{n}.py").write_text("x = 1\n" * 5, encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    return repo


def _count_lines(path: str) -> hl.FileScore:
    return len(Path(path).read_text(encoding="utf-8").splitlines()), []


def test_a_large_source_file_fails_l4_2_behind_many_small_fixtures(fixture_heavy: Path) -> None:
    # tests/fixtures is a built-in exclude for most scans; score it here to show the split
    files = [p for p in sorted(fixture_heavy.rglob("*.py"))]
    rows = [(p.relative_to(fixture_heavy).as_posix(), _count_lines(str(p))[0]) for p in files]
    block = loc_by_class(rows)
    assert block["source"]["p95"] == 900.0 and block["source"]["files_scored"] == 1
    assert block["fixture"]["files_scored"] == 50 and block["fixture"]["p95"] == 5.0
    all_files = hl.summarise([(nloc, []) for _, nloc in rows])
    assert all_files["loc"]["p95"] == 5.0   # the whole-set figure the old condition read
    state, _ = _layer_4_2({"files_scored": 51, "loc": all_files["loc"], "loc_by_class": block})
    assert state == "unmet"


def test_the_headless_block_carries_the_split(fixture_heavy: Path) -> None:
    (fixture_heavy / "tests" / "test_big.py").write_text("x = 1\n" * 700, encoding="utf-8")
    _git(fixture_heavy, "add", "-A")
    block = hl.stats_summary(fixture_heavy, (set(), []), None, analyzer=lambda: _count_lines)
    by_class = block["loc_by_class"]
    assert by_class["source"]["p95"] == 900.0
    assert by_class["test"] == {"files_scored": 1, "p50": 700.0, "p95": 700.0, "max": 700.0}
    assert block["files_scored"] == by_class["source"]["files_scored"] + by_class["test"]["files_scored"]
    assert _layer_4_2(block)[0] == "unmet"


def test_large_tests_do_not_fail_small_source() -> None:
    block = loc_by_class([("pkg/a.py", 120), ("tests/test_a.py", 2000), ("tests/test_b.py", 1800)])
    stats = {"files_scored": 3, "loc": {"p95": 1980.0}, "loc_by_class": block}
    assert _layer_4_2(stats)[0] == "met"


def test_no_source_file_reads_not_measured_never_met() -> None:
    block = loc_by_class([("tests/test_a.py", 20)])
    stats = {"files_scored": 1, "loc": {"p95": 20.0}, "loc_by_class": block}
    assert _layer_4_2(stats) == ("not_measured", "no_source_files")
