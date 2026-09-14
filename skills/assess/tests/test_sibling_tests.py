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
from lib.test_focus import compute_test_focus

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
