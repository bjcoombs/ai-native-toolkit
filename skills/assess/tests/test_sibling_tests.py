"""Contract tests for ``lib/sibling_tests``: the one sibling-test resolver.

The hotspot page (``assess_core._has_sibling_test``), the E2 test-to-code map
(``keyhole_signals._find_sibling_test``), and the focus signal
(``test_focus.compute_test_focus``) must answer "does this file have a test
file?" the same way in one run. These tests pin that agreement on a fixture set
spanning every naming idiom, so a convention added to one consumer and not the
others fails here.
"""
from __future__ import annotations

from pathlib import Path

import assess_core
from lib import keyhole_signals as ks
from lib import sibling_tests as tc
from lib.test_focus import compute_test_focus, mutation_scope

# Co-located fixtures: source -> test file beside it or in an adjacent dir.
COLOCATED = {
    "go/foo.go": "go/foo_test.go",
    "ts/bar.ts": "ts/bar.test.ts",
    "ng/baz.ts": "ng/baz.spec.ts",
    "rb/qux.rb": "rb/qux_spec.rb",
    "py/mod.py": "py/test_mod.py",
    "java/Foo.java": "java/FooTest.java",
    "cs/Bar.cs": "cs/BarTests.cs",
    "js/e.js": "js/__tests__/e.test.js",
    "js/f.js": "js/__tests__/f.js",
    "rb2/g.rb": "rb2/spec/g_spec.rb",
    "scripts/tree-map.py": "scripts/test_tree_map.py",
}
# Sources with no test file anywhere.
UNTESTED = ["java/Lonely.java", "py/alone.py", "ts/solo.ts"]


def _touch(root: Path, rel: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x\n", encoding="utf-8")


def _fixture(root: Path) -> None:
    for src, test in COLOCATED.items():
        _touch(root, src)
        _touch(root, test)
    for src in UNTESTED:
        _touch(root, src)


def test_three_resolvers_agree_on_colocated_fixtures(tmp_path: Path) -> None:
    """Every co-located idiom (including FooTest.java / BarTests.cs) is credited
    by all three consumers, and every untested source by none of them."""
    _fixture(tmp_path)
    sources = [*COLOCATED, *UNTESTED]
    focus: dict[str, str] = {}
    for start in range(0, len(sources), 10):  # the focus block reads a top 10
        block = compute_test_focus(sources[start:start + 10], None, None,
                                   repo_root=tmp_path)
        focus.update({e["path"]: e["test_signal"] for e in block["entries"]})

    for src in sources:
        expected = src in COLOCATED
        assert assess_core._has_sibling_test(tmp_path, src) is expected, src
        found = ks._find_sibling_test(tmp_path, src)
        assert (found is not None) is expected, src
        if expected:
            assert found == tmp_path / COLOCATED[src]
        assert focus[src] == ("sibling_test_only" if expected else "unsupported"), src


def test_resolvers_agree_that_a_test_file_is_test_evidence(tmp_path: Path) -> None:
    """A hot file that is itself a test: the hotspot page and the focus signal
    both credit it, and E2 maps it to no sibling (it is not a source)."""
    _fixture(tmp_path)
    for test in COLOCATED.values():
        assert tc.is_test_path(test), test
        assert assess_core._has_sibling_test(tmp_path, test) is True
        assert ks._find_sibling_test(tmp_path, test) is None
        block = compute_test_focus([test], None, None, repo_root=tmp_path)
        assert block["entries"][0]["test_signal"] == "sibling_test_only"


def test_hotspot_page_and_focus_agree_on_mirrored_and_flat_trees(tmp_path: Path) -> None:
    """Beyond co-location the hotspot page reads the same tree probe as the
    focus signal: a mirrored tests/ tree credits in both, and a flat-tree match
    on a bare name two hot files share credits neither."""
    for rel in ("src/pkg/view.py", "tests/src/pkg/test_view.py",
                "a/mod.py", "b/mod.py", "tests/test_mod.py"):
        _touch(tmp_path, rel)
    hot = ["src/pkg/view.py", "a/mod.py", "b/mod.py"]
    shared = tc.shared_name_keys(hot)
    assert shared == frozenset({"mod.py"})
    block = compute_test_focus(hot, None, None, repo_root=tmp_path)
    focus = {e["path"]: e["test_signal"] == "sibling_test_only" for e in block["entries"]}
    for src in hot:
        assert assess_core._has_sibling_test(tmp_path, src, shared) is focus[src], src
    assert focus == {"src/pkg/view.py": True, "a/mod.py": False, "b/mod.py": False}


def test_missing_source_is_unknown_not_credited(tmp_path: Path) -> None:
    """A stale path whose test outlived it: unknown on the hotspot page, no
    sibling for E2, unsupported in the focus block."""
    _touch(tmp_path, "src/gone.test.ts")
    assert assess_core._has_sibling_test(tmp_path, "src/gone.ts") is None
    assert ks._find_sibling_test(tmp_path, "src/gone.ts") is None
    block = compute_test_focus(["src/gone.ts"], None, None, repo_root=tmp_path)
    assert block["entries"][0]["test_signal"] == "unsupported"


def test_sibling_test_names_cover_every_builder_and_hyphen_fold() -> None:
    names = tc.sibling_test_names("Foo-bar.java")
    for stem in ("Foo-bar", "Foo_bar"):
        assert f"{stem}Test.java" in names
        assert f"{stem}Tests.java" in names
        assert f"{stem}_spec.java" in names
        assert f"test_{stem}.java" in names
    assert len(names) == 2 * len(tc.TEST_SIBLING_BUILDERS)


# Parallel test trees that neither co-locate nor mirror the source path: the
# issue's fixture, the reported case where the test tree drops a directory, and
# the Dart layout (``lib/`` stripped, tests under ``test/unit``).
PARALLEL = {
    "app/functions/foo.js": "app/unit-tests/functions/foo.test.js",
    "app/functions/hmrc/hmrcVatReturnPost.js":
        "app/unit-tests/functions/hmrcVatReturnPost.test.js",
    "shop/lib/features/crm/repositories/customer_repository.dart":
        "shop/test/unit/customer_repository_test.dart",
}


def test_parallel_tree_basename_credits_the_three_layouts(tmp_path: Path) -> None:
    """A conventionally named test anywhere under a shared directory credits the
    source in the focus block and on the hotspot page, and enters the mutation
    scope; a source with no test anywhere stays unsupported."""
    for src, test in PARALLEL.items():
        _touch(tmp_path, src)
        _touch(tmp_path, test)
    _touch(tmp_path, "app/functions/bar.js")
    hot = [*PARALLEL, "app/functions/bar.js"]
    block = compute_test_focus(hot, None, None, repo_root=tmp_path)
    by_path = {e["path"]: e["test_signal"] for e in block["entries"]}
    assert by_path == {**{p: "sibling_test_only" for p in PARALLEL},
                       "app/functions/bar.js": "unsupported"}
    assert mutation_scope(block) == list(PARALLEL)
    for src in PARALLEL:
        assert tc.sibling_test_match(tmp_path, src) == tc.MATCH_BASENAME, src
        assert assess_core._has_sibling_test(tmp_path, src) is True, src
        assert ks._find_sibling_test(tmp_path, src) is None, src  # E2: co-located only
    assert assess_core._has_sibling_test(tmp_path, "app/functions/bar.js") is False


def test_parallel_tree_basename_common_name_goes_to_the_closest_source(
    tmp_path: Path,
) -> None:
    """Two sources sharing a basename (``index.js``): the test credits the one
    source with the strictly closest common ancestor, and a tie credits none,
    across the whole repository rather than only the hot files."""
    for rel in ("web/pages/index.js", "api/index.js",
                "web/unit-tests/pages/index.test.js",
                "svc/a/util.js", "svc/b/util.js", "svc/unit-tests/util.test.js"):
        _touch(tmp_path, rel)
    assert assess_core._has_sibling_test(tmp_path, "web/pages/index.js") is True
    assert assess_core._has_sibling_test(tmp_path, "api/index.js") is False
    # svc/a and svc/b tie on svc/: the test cannot say which util.js it tests.
    assert assess_core._has_sibling_test(tmp_path, "svc/a/util.js") is False
    assert assess_core._has_sibling_test(tmp_path, "svc/b/util.js") is False


def test_parallel_tree_basename_needs_a_shared_directory(tmp_path: Path) -> None:
    """A bare-name match whose only common ancestor is the repository root
    carries no path relationship, and a test under an excluded tree
    (``node_modules``, ``tests/fixtures``) is never evidence."""
    for rel in ("pkg/sub/deep/mod.py", "other/tests/test_mod.py",
                "app/lib/widget.js", "app/node_modules/x/widget.test.js",
                "tool/src/cfg.py", "tool/tests/fixtures/test_cfg.py"):
        _touch(tmp_path, rel)
    for src in ("pkg/sub/deep/mod.py", "app/lib/widget.js", "tool/src/cfg.py"):
        assert tc.sibling_test_match(tmp_path, src) is None, src


def test_parallel_tree_basename_reads_git_index_when_present(tmp_path: Path) -> None:
    """In a git repository the index is the tracked file list: an untracked
    stray test file is not evidence, a committed one is."""
    import subprocess

    for rel in ("app/functions/foo.js", "app/unit-tests/functions/foo.test.js",
                "app/functions/baz.js"):
        _touch(tmp_path, rel)
    git = ["git", "-C", str(tmp_path), "-c", "user.email=t@example.com",
           "-c", "user.name=t"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "init"], check=True)
    _touch(tmp_path, "app/unit-tests/functions/baz.test.js")  # untracked
    index = tc.build_test_index(tmp_path)
    assert tc.has_sibling_test(tmp_path, "app/functions/foo.js", index=index) is True
    assert tc.has_sibling_test(tmp_path, "app/functions/baz.js", index=index) is False


def test_parallel_tree_basename_truncated_index_credits_nothing(
    tmp_path: Path, monkeypatch,
) -> None:
    """A walk cut off at the index cap can drop a rival source as readily as a
    test, so a truncated index is empty: it fails closed rather than credit a
    source on evidence the complete index calls a tie."""
    for rel in ("svc/a/util.js", "svc/a-tests/util.test.js", "svc/b/util.js"):
        _touch(tmp_path, rel)
    # Complete index: svc/a and svc/b tie on svc/, so neither is credited.
    assert tc.sibling_test_match(tmp_path, "svc/a/util.js") is None
    # Cap at two files: the walk keeps svc/a and the test but drops svc/b.
    monkeypatch.setattr(tc, "MAX_INDEX_FILES", 2)
    index = tc.build_test_index(tmp_path)
    assert index == tc.TestIndex()
    assert tc.sibling_test_match(tmp_path, "svc/a/util.js", index) is None


def test_parallel_tree_basename_index_is_built_only_when_a_probe_reads_it(
    tmp_path: Path, monkeypatch,
) -> None:
    """A coverage report that records every hot file never reaches the test-file
    probe, so the repository index is not built; a caller's index is reused."""
    from lib import test_focus as tf

    for rel in ("app/functions/foo.js", "app/unit-tests/functions/foo.test.js"):
        _touch(tmp_path, rel)
    built: list[Path] = []
    real = tf.build_test_index
    monkeypatch.setattr(tf, "build_test_index",
                        lambda root: built.append(root) or real(root))
    coverage = {"_overall": 0.9, "per_file": {"app/functions/foo.js": 0.9}}
    compute_test_focus(["app/functions/foo.js"], coverage, None, repo_root=tmp_path)
    assert built == []
    shared = tc.build_test_index(tmp_path)
    block = compute_test_focus(["app/functions/foo.js"], None, None,
                               repo_root=tmp_path, index=shared)
    assert built == []
    assert block["entries"][0]["test_signal"] == "sibling_test_only"
    compute_test_focus(["app/functions/foo.js"], None, None, repo_root=tmp_path)
    assert built == [tmp_path]


def test_parallel_tree_basename_skips_tracked_files_deleted_from_disk(
    tmp_path: Path,
) -> None:
    """A tracked file deleted from the working tree (deletion not yet staged)
    is not evidence: ``git ls-files`` still lists it, the index does not."""
    import subprocess

    for rel in ("app/functions/foo.js", "app/unit-tests/functions/foo.test.js"):
        _touch(tmp_path, rel)
    git = ["git", "-C", str(tmp_path), "-c", "user.email=t@example.com",
           "-c", "user.name=t"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "init"], check=True)
    (tmp_path / "app/unit-tests/functions/foo.test.js").unlink()
    assert tc.build_test_index(tmp_path).tests_by_name == {}
    assert tc.sibling_test_match(tmp_path, "app/functions/foo.js") is None


def test_parallel_tree_basename_unreadable_subtree_credits_nothing(
    tmp_path: Path,
) -> None:
    """A walk that cannot read a directory may have missed a rival source, so
    it yields an empty index, the same fail-closed rule as truncation."""
    import os

    for rel in ("svc/a/util.js", "svc/a-tests/util.test.js", "svc/b/util.js"):
        _touch(tmp_path, rel)
    locked = tmp_path / "svc/b"
    locked.chmod(0)
    try:
        if os.access(locked, os.R_OK):
            import pytest
            pytest.skip("running with privileges that ignore file modes")
        assert tc.build_test_index(tmp_path) == tc.TestIndex()
    finally:
        locked.chmod(0o755)
