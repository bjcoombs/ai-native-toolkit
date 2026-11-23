"""Tests for the ``parallel-docs-tree`` match (#817): production code only, the
doc's stem before its parent name, ties by the longest shared path suffix."""
from __future__ import annotations

from pathlib import Path

from lib.docs.doc_complexity_join import analyze_doc_complexity_join
from lib.docs.doc_staleness import analyze_doc_staleness
from lib.docs.parallel_docs import ParallelIndex, ParallelMatch, parallel_index


def _write(root: Path, rel: str, text: str = "x") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_stem_maps_to_module_file_not_test_dir_or_parent_dir() -> None:
    """The issue's fixture: the stem `foo` names the module file, not the
    parent-named `tests/models` nor the whole `src/pkg/models`."""
    index = ParallelIndex(["src/pkg/models/foo.py", "src/pkg/models/bar.py",
                           "tests/models/test_foo.py", "tests/models/helpers.py"])
    assert index.match("docs/api/models/foo.md") == ParallelMatch(
        "src/pkg/models/foo.py", "stem:only-match")


def test_parent_name_maps_to_source_dir_when_no_stem_match() -> None:
    index = ParallelIndex(["src/pkg/models/bar.py", "tests/models/helpers.py",
                           "tests/fixtures/models/m.py"])
    assert index.match("docs/api/models/cohere.md") == ParallelMatch(
        "src/pkg/models", "parent-name:only-match")


def test_only_test_or_fixture_code_matches_nothing() -> None:
    index = ParallelIndex(["tests/models/helpers.py", "tests/fixtures/billing/b.py",
                           "src/other/o.py"])
    assert index.match("docs/api/models/cohere.md") is None
    assert index.match("docs/billing.md") is None


def test_longest_shared_suffix_breaks_a_stem_tie() -> None:
    index = ParallelIndex(["pkg/providers/cohere.py", "pkg/deep/er/models/cohere.py"])
    assert index.match("docs/api/models/cohere.md") == ParallelMatch(
        "pkg/deep/er/models/cohere.py", "stem:longest-suffix")


def test_shallowest_then_path_order_break_remaining_ties() -> None:
    index = ParallelIndex(["a/b/auth/x.py", "src/auth/y.py", "lib/auth/z.py"])
    assert index.match("docs/auth.md") == ParallelMatch("lib/auth", "stem:path-order")
    index = ParallelIndex(["a/b/auth/x.py", "src/auth/y.py"])
    assert index.match("docs/auth.md") == ParallelMatch("src/auth", "stem:shallowest")


def test_generic_stem_uses_parent_name_and_non_docs_pages_never_match() -> None:
    index = ParallelIndex(["src/payments/p.py", "src/index/i.py", "src/readme/r.py"])
    assert index.match("docs/payments/index.md") == ParallelMatch(
        "src/payments", "parent-name:only-match")
    assert index.match("docs/payments/README.md") == ParallelMatch(
        "src/payments", "parent-name:only-match")
    assert index.match("notes/payments.md") is None
    assert index.match("docs/unknown.md") is None


def test_examples_still_count_as_a_subject() -> None:
    """Only test and fixture code is set aside; a page about an example maps."""
    index = ParallelIndex(["examples/quickstart/main.py"])
    assert index.match("docs/quickstart.md") == ParallelMatch(
        "examples/quickstart", "stem:only-match")


def test_parallel_index_skips_paths_outside_the_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    index = parallel_index([repo / "src" / "auth" / "a.py", tmp_path / "auth" / "b.py"], repo)
    assert index.match("docs/auth.md") == ParallelMatch("src/auth", "stem:only-match")


def test_staleness_and_join_map_the_issue_fixture_to_one_module(tmp_path: Path) -> None:
    _write(tmp_path, "docs/api/models/foo.md", "foo page")
    _write(tmp_path, "src/pkg/models/foo.py")
    _write(tmp_path, "src/pkg/models/bar.py")
    _write(tmp_path, "tests/models/test_foo.py")
    _write(tmp_path, "tests/models/conftest.py")
    staleness = analyze_doc_staleness(tmp_path)
    row = next(d for d in staleness["docs"] if d["path"] == "docs/api/models/foo.md")
    assert row["subject_method"] == "parallel-docs-tree"
    assert row["subject_roots"] == ["src/pkg/models/foo.py"]
    assert row["subject_match"] == "stem:only-match"
    assert row["subject_paths"] == ["src/pkg/models/foo.py"]
    assert row["subject_code_count"] == 1

    stats = {"ccn": {"p95": 10.0},
             "top_complex": [{"path": "src/pkg/models/foo.py", "ccn": 30.0},
                             {"path": "src/pkg/models/bar.py", "ccn": 30.0}]}
    joined = analyze_doc_complexity_join(dict(stats), dict(staleness), tmp_path)
    unit = next(u for u in joined["docs"] if u["path"] == "docs/api/models/foo.md")
    assert unit["subject_code_count"] == 1


def test_real_source_beside_its_tests_still_maps(tmp_path: Path) -> None:
    """A source dir beside a same-named test dir keeps its mapping (no false
    baseline), and a doc with only test code to match leaves the measured set."""
    _write(tmp_path, "docs/billing.md", "billing")
    _write(tmp_path, "billing/b.py")
    _write(tmp_path, "tests/billing/test_b.py")
    _write(tmp_path, "docs/harness.md", "harness")
    _write(tmp_path, "tests/harness/h.py")
    staleness = analyze_doc_staleness(tmp_path)
    rows = {d["path"]: d for d in staleness["docs"]}
    assert rows["docs/billing.md"]["subject_roots"] == ["billing"]
    assert rows["docs/billing.md"]["subject_match"] == "stem:only-match"
    assert rows["docs/billing.md"]["subject_code_count"] == 1
    assert rows["docs/harness.md"]["subject_method"] == "repo-baseline"
    assert "subject_match" not in rows["docs/harness.md"]


def test_generic_file_stem_matches_a_directory_never_a_file() -> None:
    """A prose page named like a common module (`docs/setup.md`) is not read as
    documenting `setup.py`; a directory of that name still matches."""
    index = ParallelIndex(["setup.py", "src/pkg/config.py", "cmd/main.go"])
    assert index.match("docs/setup.md") is None
    assert index.match("docs/config.md") is None
    assert index.match("docs/main.md") is None
    index = ParallelIndex(["src/config/loader.py", "src/pkg/config.py"])
    assert index.match("docs/config.md") == ParallelMatch("src/config", "stem:only-match")


def test_docs_dir_is_never_a_parent_name_key() -> None:
    """A Sphinx `docs/conf.py` does not pull every top-level page onto `docs/`."""
    index = ParallelIndex(["docs/conf.py", "docs/hooks.py", "src/auth/a.py"])
    assert index.match("docs/tutorial.md") is None
    assert index.match("docs/auth.md") == ParallelMatch("src/auth", "stem:only-match")


def test_generated_or_vendored_file_under_a_test_tree_is_not_a_candidate() -> None:
    """`path_class` reads generated and vendored before test, so the test tree
    is checked on its own: `tests/models/client.generated.ts` adds no candidate."""
    index = ParallelIndex(["tests/models/client.generated.ts",
                           "tests/fixtures/vendor/models/m.py", "src/pkg/models/bar.py"])
    assert index.match("docs/api/models/cohere.md") == ParallelMatch(
        "src/pkg/models", "parent-name:only-match")
    index = ParallelIndex(["tests/models/client.generated.ts"])
    assert index.match("docs/api/models/cohere.md") is None
