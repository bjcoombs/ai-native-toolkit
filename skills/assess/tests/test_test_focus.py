"""Contract tests for ``lib/test_focus.compute_test_focus``.

Cover each signal classification, the risk-band assignment by hotspot position,
the ranking order, the ``covered_clean`` filter, the honest no-coverage degrade
(``coverage_data=None`` without ``repo_root`` -> every entry
``unknown_no_coverage`` and ``coverage_present: False``), the test-file fallback
under ``repo_root`` (``sibling_test_only`` / ``unsupported``), and the
``mutation_scope`` filter.
"""
from __future__ import annotations

import sys
from pathlib import Path

# scripts/ on the path so ``lib`` imports resolve the same way the orchestrator does.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib.test_focus import compute_test_focus, mutation_scope  # noqa: E402


def _hot(*paths: str) -> list[dict]:
    """Build a top_hotspots-shaped list (ranked, each entry a dict with a path)."""
    return [{"path": p} for p in paths]


def _coverage(per_file: dict[str, float], overall: float = 0.5) -> dict:
    return {"_overall": overall, "per_file": per_file}


def _empty_heuristics() -> dict:
    return {
        "assertion_on_internal": [],
        "untested_boundaries": [],
        "duplicate_truth": [],
    }


def _entry(block: dict, path: str) -> dict:
    return next(e for e in block["entries"] if e["path"] == path)


# ── signal classification ─────────────────────────────────────────────────────

def test_no_covering_test_when_file_absent_from_report() -> None:
    block = compute_test_focus(_hot("a.py"), _coverage({"other.py": 0.8}),
                               _empty_heuristics())
    entry = _entry(block, "a.py")
    assert entry["test_signal"] == "no_covering_test"
    assert entry["suggested_action"] == "add_tests"
    assert entry["hollow_heuristic_kinds"] == []


def test_no_covering_test_when_line_rate_zero() -> None:
    block = compute_test_focus(_hot("a.py"), _coverage({"a.py": 0.0}),
                               _empty_heuristics())
    assert _entry(block, "a.py")["test_signal"] == "no_covering_test"


def test_covered_but_hollow_when_in_a_heuristic_bucket() -> None:
    heuristics = {
        "assertion_on_internal": [],
        "untested_boundaries": [{"file": "a.py", "line": 3, "operator": "<="}],
        "duplicate_truth": [],
    }
    block = compute_test_focus(_hot("a.py"), _coverage({"a.py": 0.9}), heuristics)
    entry = _entry(block, "a.py")
    assert entry["test_signal"] == "covered_but_hollow"
    assert entry["suggested_action"] == "strengthen_assertions"
    assert entry["hollow_heuristic_kinds"] == ["untested_boundaries"]


def test_covered_but_hollow_matches_test_file_key() -> None:
    """assertion_on_internal entries name the file under ``test_file``; a hot file
    matching that key still counts as hollow."""
    heuristics = {
        "assertion_on_internal": [
            {"test_file": "a.py", "subject_function": "test_x:obj",
             "internal_field": "_y", "confidence": "medium"}
        ],
        "untested_boundaries": [],
        "duplicate_truth": [],
    }
    block = compute_test_focus(_hot("a.py"), _coverage({"a.py": 0.9}), heuristics)
    assert _entry(block, "a.py")["hollow_heuristic_kinds"] == ["assertion_on_internal"]


def test_multiple_hollow_kinds_collected_in_report_order() -> None:
    heuristics = {
        "assertion_on_internal": [{"test_file": "a.py", "internal_field": "_y"}],
        "untested_boundaries": [{"file": "a.py", "line": 1, "operator": "<"}],
        "duplicate_truth": [{"file": "a.py", "field_name": "x", "derives_from": "y"}],
    }
    block = compute_test_focus(_hot("a.py"), _coverage({"a.py": 0.9}), heuristics)
    assert _entry(block, "a.py")["hollow_heuristic_kinds"] == [
        "assertion_on_internal", "untested_boundaries", "duplicate_truth",
    ]


def test_covered_clean_is_filtered_out() -> None:
    block = compute_test_focus(_hot("a.py"), _coverage({"a.py": 0.95}),
                               _empty_heuristics())
    assert block["entries"] == []
    assert block["total_focus_targets"] == 0
    assert block["coverage_present"] is True


# ── risk bands ────────────────────────────────────────────────────────────────

def test_risk_band_by_hotspot_position() -> None:
    paths = [f"f{i}.py" for i in range(10)]
    # No coverage report -> every file is a focus target, so all 10 appear.
    block = compute_test_focus(_hot(*paths), None, _empty_heuristics())
    band = {e["path"]: e["risk_band"] for e in block["entries"]}
    assert [band[f"f{i}.py"] for i in range(3)] == ["high", "high", "high"]
    assert [band[f"f{i}.py"] for i in range(3, 7)] == ["medium"] * 4
    assert [band[f"f{i}.py"] for i in range(7, 10)] == ["low"] * 3


def test_files_beyond_top_ten_are_excluded() -> None:
    paths = [f"f{i}.py" for i in range(13)]
    block = compute_test_focus(_hot(*paths), None, _empty_heuristics())
    assert block["total_focus_targets"] == 10
    assert all(int(e["path"][1:-3]) < 10 for e in block["entries"])


# ── ranking ───────────────────────────────────────────────────────────────────

def test_ranking_risk_band_dominates_then_signal_severity() -> None:
    # low-risk file with the most severe signal vs high-risk with a milder one:
    # the high-risk file must still rank first (band dominates).
    hot = _hot(*[f"f{i}.py" for i in range(8)])  # f0-f2 high, f3-f6 medium, f7 low
    coverage = _coverage({
        "f0.py": 0.95,  # high, covered_clean -> filtered
        "f7.py": 0.0,   # low, no_covering_test (severe)
    })
    # f0 filtered; f1,f2 high no_covering_test; f3-f6 medium; f7 low severe.
    block = compute_test_focus(hot, coverage, _empty_heuristics())
    ranked = [e["path"] for e in block["entries"]]
    # First entries are the high-band files, low-band f7 is last despite severity.
    assert ranked[0] in {"f1.py", "f2.py"}
    assert ranked[-1] == "f7.py"
    assert block["entries"][0]["risk_band"] == "high"


def test_signal_severity_orders_within_a_band() -> None:
    # Two high-risk files: one with no test (severe), one covered-but-hollow.
    hot = _hot("a.py", "b.py")
    coverage = _coverage({"b.py": 0.9})  # a.py absent -> no_covering_test
    heuristics = {
        "assertion_on_internal": [],
        "untested_boundaries": [{"file": "b.py", "line": 1, "operator": "<"}],
        "duplicate_truth": [],
    }
    block = compute_test_focus(hot, coverage, heuristics)
    ranked = [e["path"] for e in block["entries"]]
    assert ranked == ["a.py", "b.py"]  # no_covering_test outranks covered_but_hollow


# ── honest degrade ────────────────────────────────────────────────────────────

def test_no_coverage_degrades_to_unknown_not_clean() -> None:
    paths = [f"f{i}.py" for i in range(3)]
    block = compute_test_focus(_hot(*paths), None, _empty_heuristics())
    assert block["coverage_present"] is False
    assert block["available"] is True
    assert block["total_focus_targets"] == 3
    for entry in block["entries"]:
        assert entry["test_signal"] == "unknown_no_coverage"
        assert entry["suggested_action"] == "add_tests"
        assert entry["hollow_heuristic_kinds"] == []


def test_empty_inputs_produce_empty_block() -> None:
    block = compute_test_focus([], None, None)
    assert block == {
        "available": True,
        "coverage_present": False,
        "entries": [],
        "total_focus_targets": 0,
    }


def test_bare_string_hotspot_entries_supported() -> None:
    block = compute_test_focus(["a.py", "b.py"], None, _empty_heuristics())
    assert {e["path"] for e in block["entries"]} == {"a.py", "b.py"}


def test_malformed_hotspot_entries_are_skipped() -> None:
    block = compute_test_focus(
        [{"path": "a.py"}, {"no_path": 1}, None, 42],
        _coverage({"a.py": 0.0}),
        _empty_heuristics(),
    )
    assert [e["path"] for e in block["entries"]] == ["a.py"]


# ── sibling-test fallback and the unsupported signal (#317) ───────────────────


def _touch(root: Path, rel: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x\n", encoding="utf-8")


def test_sibling_test_fallback_credits_file_with_sibling_test(tmp_path: Path) -> None:
    """No coverage report, but a sibling test file exists: the file is credited
    as sibling_test_only / measure_coverage (never a covered bucket, never
    unknown/no_covering_test/unsupported, never add_tests). Covers each naming
    convention and the sibling __tests__/ directory."""
    for rel in ("src/a.ts", "src/a.test.ts",
                "src/b.tsx", "src/b.spec.tsx",
                "pkg/c.go", "pkg/c_test.go",
                "lib/d.py", "lib/test_d.py",
                "web/e.js", "web/__tests__/e.test.js",
                "web/f.js", "web/__tests__/f.js"):
        _touch(tmp_path, rel)
    hot = ["src/a.ts", "src/b.tsx", "pkg/c.go", "lib/d.py", "web/e.js", "web/f.js"]
    block = compute_test_focus(hot, None, None, repo_root=tmp_path)
    assert block["coverage_present"] is False
    by_path = {e["path"]: e for e in block["entries"]}
    assert set(by_path) == set(hot)
    for path in hot:
        assert by_path[path]["test_signal"] == "sibling_test_only"
        assert by_path[path]["suggested_action"] == "measure_coverage"
        assert by_path[path]["hollow_heuristic_kinds"] == []


def test_sibling_test_fallback_keeps_hollow_heuristics(tmp_path: Path) -> None:
    """A sibling-tested file that trips a hollow heuristic keeps the
    sibling_test_only signal (no coverage was measured) but carries the hollow
    kinds so that evidence is not lost."""
    _touch(tmp_path, "src/a.py")
    _touch(tmp_path, "src/test_a.py")
    heur = _empty_heuristics()
    heur["untested_boundaries"] = [{"file": "src/a.py"}]
    block = compute_test_focus(["src/a.py"], None, heur, repo_root=tmp_path)
    entry = _entry(block, "src/a.py")
    assert entry["test_signal"] == "sibling_test_only"
    assert entry["suggested_action"] == "measure_coverage"
    assert entry["hollow_heuristic_kinds"] == ["untested_boundaries"]


def test_unsupported_test_signal_when_no_coverage_and_no_sibling(tmp_path: Path) -> None:
    """No coverage report and no sibling test: the honest signal is unsupported,
    with its own action and a severity below a known no_covering_test."""
    _touch(tmp_path, "src/b.ts")
    block = compute_test_focus(["src/b.ts"], None, None, repo_root=tmp_path)
    entry = _entry(block, "src/b.ts")
    assert entry["test_signal"] == "unsupported"
    assert entry["suggested_action"] == "measure_coverage"


def test_unsupported_test_signal_ranks_within_band(tmp_path: Path) -> None:
    """unsupported has its own severity rank: less tested ranks higher, so a file
    with no test found anywhere outranks a sibling-tested one in the same band,
    whatever the hotspot order."""
    _touch(tmp_path, "a.py")
    _touch(tmp_path, "test_a.py")
    _touch(tmp_path, "b.py")
    heur = _empty_heuristics()
    heur["duplicate_truth"] = [{"file": "a.py"}]
    block = compute_test_focus(["a.py", "b.py"], None, heur, repo_root=tmp_path)
    assert [(e["path"], e["test_signal"]) for e in block["entries"]] == [
        ("b.py", "unsupported"), ("a.py", "sibling_test_only"),
    ]


def test_sibling_test_fallback_skips_deleted_source(tmp_path: Path) -> None:
    """A stale hotspot entry for a deleted source is never credited by a test
    file that outlived it."""
    _touch(tmp_path, "src/a.test.ts")
    block = compute_test_focus(["src/a.ts"], None, None, repo_root=tmp_path)
    assert _entry(block, "src/a.ts")["test_signal"] == "unsupported"


def test_sibling_test_fallback_parallel_tests_tree(tmp_path: Path) -> None:
    """The parallel tests/ layout credits: this repo's own convention
    (skills/assess/tests/test_<stem>.py for skills/assess/scripts/lib/<stem>.py),
    a root tests/ tree mirroring the source path, a mirror that drops a src/
    root, an adjacent test/ directory, and a Go-style root test/ tree."""
    for rel in ("skills/assess/scripts/lib/doc_graph.py",
                "skills/assess/tests/test_doc_graph.py",
                "src/pkg/mod.py", "tests/src/pkg/test_mod.py",
                "src/app/view.ts", "tests/app/view.spec.ts",
                "lib/util.js", "lib/test/util.test.js",
                "cmd/run.go", "test/run_test.go"):
        _touch(tmp_path, rel)
    hot = ["skills/assess/scripts/lib/doc_graph.py", "src/pkg/mod.py",
           "src/app/view.ts", "lib/util.js", "cmd/run.go"]
    block = compute_test_focus(hot, None, None, repo_root=tmp_path)
    by_path = {e["path"]: e["test_signal"] for e in block["entries"]}
    assert by_path == {path: "sibling_test_only" for path in hot}


def test_sibling_test_fallback_parallel_tree_needs_matching_name(tmp_path: Path) -> None:
    """A tests/ tree with only unrelated test files does not credit a source."""
    _touch(tmp_path, "skills/assess/scripts/lib/doc_graph.py")
    _touch(tmp_path, "skills/assess/tests/test_other.py")
    _touch(tmp_path, "tests/doc_graph.py")
    block = compute_test_focus(
        ["skills/assess/scripts/lib/doc_graph.py"], None, None, repo_root=tmp_path,
    )
    assert block["entries"][0]["test_signal"] == "unsupported"


def test_no_repo_root_keeps_unknown_no_coverage() -> None:
    """Backward compatible: without repo_root the no-report degrade is unchanged."""
    block = compute_test_focus(["a.py"], None, None)
    assert block["entries"][0]["test_signal"] == "unknown_no_coverage"


def test_sibling_test_fallback_hyphenated_stem_and_test_files(tmp_path: Path) -> None:
    """A hyphenated script matches its underscore test name (this repo's
    complexity-treemap.py -> tests/test_complexity_treemap.py), and a hot file
    that is itself a test is test evidence, never 'no test found'."""
    for rel in ("skills/assess/scripts/complexity-treemap.py",
                "skills/assess/tests/test_complexity_treemap.py",
                "src/a.test.ts", "web/__tests__/b.js"):
        _touch(tmp_path, rel)
    hot = ["skills/assess/scripts/complexity-treemap.py",
           "skills/assess/tests/test_complexity_treemap.py",
           "src/a.test.ts", "web/__tests__/b.js"]
    block = compute_test_focus(hot, None, None, repo_root=tmp_path)
    by_path = {e["path"]: e["test_signal"] for e in block["entries"]}
    assert by_path == {path: "sibling_test_only" for path in hot}


def test_flat_tests_tree_does_not_credit_every_same_named_file(tmp_path: Path) -> None:
    """A single root tests/test_mod.py carries no path relationship to either
    src/a/mod.py or src/b/mod.py, so a bare-name match cannot credit both; this
    repo's own flat skills/assess/tests layout is still credited alongside."""
    for rel in ("src/a/mod.py", "src/b/mod.py", "tests/test_mod.py",
                "skills/assess/scripts/lib/doc_graph.py",
                "skills/assess/tests/test_doc_graph.py"):
        _touch(tmp_path, rel)
    hot = ["src/a/mod.py", "src/b/mod.py", "skills/assess/scripts/lib/doc_graph.py"]
    block = compute_test_focus(hot, None, None, repo_root=tmp_path)
    by_path = {e["path"]: e["test_signal"] for e in block["entries"]}
    credited = [p for p in ("src/a/mod.py", "src/b/mod.py")
                if by_path[p] == "sibling_test_only"]
    assert len(credited) <= 1
    assert by_path["skills/assess/scripts/lib/doc_graph.py"] == "sibling_test_only"


def test_flat_tests_tree_bounded_to_package_depth(tmp_path: Path) -> None:
    """A flat root tests/ tree does not reach a source nested deeper than its
    top-level package directory, while a mirrored path at the same root and a
    same-named file with a direct sibling test keep their credit."""
    for rel in ("pkg/sub/deep/mod.py", "tests/test_mod.py",
                "pkg/sub/deep/view.py", "tests/sub/deep/test_view.py",
                "src/x/util.py", "src/x/test_util.py",
                "src/y/util.py"):
        _touch(tmp_path, rel)
    hot = ["pkg/sub/deep/mod.py", "pkg/sub/deep/view.py",
           "src/x/util.py", "src/y/util.py"]
    block = compute_test_focus(hot, None, None, repo_root=tmp_path)
    by_path = {e["path"]: e["test_signal"] for e in block["entries"]}
    assert by_path == {
        "pkg/sub/deep/mod.py": "unsupported",
        "pkg/sub/deep/view.py": "sibling_test_only",
        "src/x/util.py": "sibling_test_only",
        "src/y/util.py": "unsupported",
    }


def test_sibling_test_fallback_jvm_ruby_and_dotnet_spellings(tmp_path: Path) -> None:
    """The focus signal reads the shared convention list, so JUnit / XCTest /
    RSpec spellings credit a file, a Ruby spec/ mirror tree credits it, and a hot
    file that is itself a ``_spec`` / ``Test`` file is test evidence."""
    for rel in ("src/Foo.java", "src/FooTest.java",
                "src/Bar.cs", "src/BarTests.cs",
                "lib/baz.rb", "lib/baz_spec.rb",
                "app/models/qux.rb", "spec/app/models/qux_spec.rb",
                "lib/quux_spec.rb", "src/CorgeTest.kt"):
        _touch(tmp_path, rel)
    hot = ["src/Foo.java", "src/Bar.cs", "lib/baz.rb", "app/models/qux.rb",
           "lib/quux_spec.rb", "src/CorgeTest.kt"]
    block = compute_test_focus(hot, None, None, repo_root=tmp_path)
    by_path = {e["path"]: e["test_signal"] for e in block["entries"]}
    assert by_path == {path: "sibling_test_only" for path in hot}


def test_sibling_test_fallback_with_partial_coverage_report(tmp_path: Path) -> None:
    """A present report that omits a file is not evidence of no test: with a
    sibling test on disk the file reads sibling_test_only / measure_coverage.
    Without one it stays no_covering_test, and a file the report records at a
    0 rate stays no_covering_test even with a sibling test (the report measured
    it)."""
    for rel in ("src/a.ts", "src/a.test.ts", "src/b.ts",
                "src/c.ts", "src/c.test.ts"):
        _touch(tmp_path, rel)
    cov = _coverage({"src/c.ts": 0.0, "src/other.ts": 0.8})
    heur = _empty_heuristics()
    heur["untested_boundaries"] = [{"file": "src/a.ts"}]
    block = compute_test_focus(["src/a.ts", "src/b.ts", "src/c.ts"], cov, heur,
                               repo_root=tmp_path)
    assert block["coverage_present"] is True
    got = {e["path"]: (e["test_signal"], e["suggested_action"],
                       e["hollow_heuristic_kinds"]) for e in block["entries"]}
    assert got == {
        "src/a.ts": ("sibling_test_only", "measure_coverage", ["untested_boundaries"]),
        "src/b.ts": ("no_covering_test", "add_tests", []),
        "src/c.ts": ("no_covering_test", "add_tests", []),
    }


def test_mutation_scope_keeps_only_entries_with_test_evidence(tmp_path: Path) -> None:
    """The mutation pass skips files with no test: an unsupported head entry
    (which outranks sibling_test_only in the table) never enters the scope, and
    the scope keeps the table's ranked order among the rest."""
    for rel in ("a.py", "b.py", "test_b.py", "c.py", "d.py", "test_d.py"):
        _touch(tmp_path, rel)
    block = compute_test_focus(["a.py", "b.py", "c.py", "d.py"], None, None,
                               repo_root=tmp_path)
    assert [e["test_signal"] for e in block["entries"]] == [
        "unsupported", "unsupported", "sibling_test_only", "sibling_test_only"]
    assert mutation_scope(block) == ["b.py", "d.py"]
    assert mutation_scope(block["entries"]) == ["b.py", "d.py"]
    hollow = {"entries": [
        {"path": "x.py", "test_signal": "no_covering_test"},
        {"path": "y.py", "test_signal": "covered_but_hollow"},
        {"path": "z.py", "test_signal": "unknown_no_coverage"},
    ]}
    assert mutation_scope(hollow) == ["y.py"]
    assert mutation_scope(None) == []
    assert mutation_scope({"entries": "bad"}) == []


def test_mutation_scope_excludes_hot_file_that_is_itself_a_test(tmp_path: Path) -> None:
    """A hot test file counts as its own test (sibling_test_only, never
    unsupported) and keeps its table row, but nothing tests it, so mutating it
    measures nothing: it never enters the mutation scope."""
    for rel in ("src/a.py", "src/test_a.py", "web/b.ts", "web/b.test.ts",
                "web/__tests__/c.ts"):
        _touch(tmp_path, rel)
    hot = ["src/test_a.py", "web/b.test.ts", "web/__tests__/c.ts", "src/a.py", "web/b.ts"]
    block = compute_test_focus(hot, None, None, repo_root=tmp_path)
    by_path = {e["path"]: e["test_signal"] for e in block["entries"]}
    assert by_path == {path: "sibling_test_only" for path in hot}
    assert mutation_scope(block) == ["src/a.py", "web/b.ts"]
    hollow = {"entries": [
        {"path": "tests/test_x.py", "test_signal": "covered_but_hollow"},
        {"path": "x.py", "test_signal": "covered_but_hollow"},
    ]}
    assert mutation_scope(hollow) == ["x.py"]


def test_skill_md_mutation_scope_jq_mirrors_test_path_rule() -> None:
    """SKILL.md Step 2d re-derives the mutation scope in jq; its test-file regex
    must stay the same pattern as ``sibling_tests.IS_TEST_RE`` so the offer and
    the core never disagree about which hot files are tests."""
    from lib.sibling_tests import IS_TEST_RE

    skill = (Path(__file__).resolve().parents[1] / "SKILL.md").read_text()
    focus_line = next(line for line in skill.splitlines() if line.startswith("FOCUS_FILES="))
    assert IS_TEST_RE.pattern.replace("\\", "\\\\") in focus_line
    assert '"__tests__"' in focus_line
