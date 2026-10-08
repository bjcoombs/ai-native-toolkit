"""Contract tests for ``lib/import_credit``: the import tier of the sibling-test
probe (issue #485).

A test file split by concern (``test_foo_a.py`` / ``test_foo_b.py``) is named
after no source, so only its imports say what it tests. These tests pin the
edge cases: the three Python import spellings, relative imports, support
modules that import everything, same-named modules in another package, the JS
relative-specifier scan, the scan's laziness and cap, and agreement between the
hotspot page, the focus signal and E2.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lib import import_credit as ic
from lib import keyhole_signals as ks
from lib import run_wiki
from lib import sibling_tests as tc
from lib.test_focus import compute_test_focus

A_TEST = "\n\ndef test_it():\n    assert True\n"


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def _credited(root: Path, rel: str) -> bool | None:
    return tc.has_sibling_test(root, rel, index=tc.build_test_index(root))


def test_split_tests_credit_the_module_they_import(tmp_path: Path) -> None:
    """The issue's fixture: two split tests importing ``foo`` credit ``foo.py``
    through the import tier; an unrelated test importing nothing credits
    nothing, so ``bar.py`` stays untested."""
    _write(tmp_path, {
        "pkg/__init__.py": "", "pkg/foo.py": "x = 1\n", "pkg/bar.py": "y = 2\n",
        "tests/test_foo_a.py": "from pkg import foo" + A_TEST,
        "tests/test_foo_b.py": "from pkg import foo" + A_TEST,
        "tests/test_unrelated.py": A_TEST,
    })
    index = tc.build_test_index(tmp_path)
    assert tc.sibling_test_match(tmp_path, "pkg/foo.py", index) == tc.MATCH_IMPORT
    assert tc.has_sibling_test(tmp_path, "pkg/foo.py", index=index) is True
    assert tc.has_sibling_test(tmp_path, "pkg/bar.py", index=index) is False
    # A ``from pkg import foo`` names the submodule, not the package.
    assert tc.has_sibling_test(tmp_path, "pkg/__init__.py", index=index) is False


@pytest.mark.parametrize("line", [
    "from pkg import mod", "import pkg.mod", "from pkg.mod import thing",
    "import pkg.mod as m", "from pkg.mod import *",
])
def test_each_import_spelling_credits_the_module(tmp_path: Path, line: str) -> None:
    _write(tmp_path, {"pkg/__init__.py": "", "pkg/mod.py": "thing = 1\n",
                      "tests/test_split_a.py": line + A_TEST})
    assert _credited(tmp_path, "pkg/mod.py") is True


def test_function_level_import_counts(tmp_path: Path) -> None:
    _write(tmp_path, {"pkg/mod.py": "",
                      "tests/test_a.py": "def test_it():\n    import pkg.mod\n"})
    assert _credited(tmp_path, "pkg/mod.py") is True


def test_from_package_import_name_credits_the_package(tmp_path: Path) -> None:
    """``from pkg import VERSION`` with no ``pkg/VERSION.py`` imports a name the
    package defines, so ``pkg/__init__.py`` is the module it exercises."""
    _write(tmp_path, {"pkg/__init__.py": "VERSION = 1\n",
                      "tests/test_v.py": "from pkg import VERSION" + A_TEST})
    assert _credited(tmp_path, "pkg/__init__.py") is True


def test_source_named_like_a_test_does_not_fall_back_to_the_package(
    tmp_path: Path,
) -> None:
    """``lib/test_focus.py`` reads as a test by name; ``from lib import
    test_focus`` still names that module, never ``lib/__init__.py``."""
    _write(tmp_path, {"lib/__init__.py": "", "lib/test_focus.py": "",
                      "tests/test_x.py": "from lib import test_focus" + A_TEST})
    assert _credited(tmp_path, "lib/__init__.py") is False


def test_relative_imports_resolve_against_the_test_directory(tmp_path: Path) -> None:
    _write(tmp_path, {
        "app/__init__.py": "", "app/engine.py": "", "app/view.py": "",
        "app/sub/__init__.py": "", "app/sub/deep.py": "",
        "app/checks/__init__.py": "",
        "app/checks/test_split_engine.py": "from ..engine import run" + A_TEST,
        "app/checks/test_split_view.py": "from .. import view" + A_TEST,
        "app/checks/test_split_deep.py": "from ..sub.deep import f" + A_TEST,
        "app/checks/test_too_far.py": "from ....nowhere import x" + A_TEST,
    })
    index = tc.build_test_index(tmp_path)
    for src in ("app/engine.py", "app/view.py", "app/sub/deep.py"):
        assert tc.has_sibling_test(tmp_path, src, index=index) is True, src


def test_support_modules_that_import_everything_credit_nothing(tmp_path: Path) -> None:
    """A conftest, a helper module (``keyhole_helpers.py``) and a helper whose
    name reads as a test but defines no test (``test_utils.py``) all import
    every module; none of them is evidence that a module is tested."""
    imports = "from pkg import a, b, c\n"
    _write(tmp_path, {
        "pkg/a.py": "", "pkg/b.py": "", "pkg/c.py": "",
        "tests/conftest.py": imports + A_TEST,
        "tests/keyhole_helpers.py": imports + A_TEST,
        "tests/test_utils.py": imports + "def helper():\n    return 1\n",
        "tests/test_real.py": "from keyhole_helpers import helper" + A_TEST,
    })
    index = tc.build_test_index(tmp_path)
    for src in ("pkg/a.py", "pkg/b.py", "pkg/c.py"):
        assert tc.has_sibling_test(tmp_path, src, index=index) is False, src
    # The helper a real test imports lives in tests/: support, never a subject.
    assert ic.ImportCredits(ic.FileReader(str(tmp_path)), ["tests/test_real.py"],
                            ["tests/keyhole_helpers.py"]).credits(
                                "tests/keyhole_helpers.py") is False


def test_class_based_test_qualifies(tmp_path: Path) -> None:
    test = "import pkg.mod\n\nclass TestMod:\n    pass\n"
    _write(tmp_path, {"pkg/mod.py": "", "tests/test_a.py": test})
    assert _credited(tmp_path, "pkg/mod.py") is True


def test_same_named_module_in_another_package_is_not_credited(tmp_path: Path) -> None:
    _write(tmp_path, {
        "pkg/__init__.py": "", "pkg/util.py": "",
        "other/__init__.py": "", "other/util.py": "",
        "tests/test_other_split.py": "from other import util" + A_TEST,
    })
    index = tc.build_test_index(tmp_path)
    assert tc.has_sibling_test(tmp_path, "other/util.py", index=index) is True
    assert tc.has_sibling_test(tmp_path, "pkg/util.py", index=index) is False


def test_bare_import_needs_a_unique_closest_source_below_the_root(
    tmp_path: Path,
) -> None:
    """``import util`` names a module only through ``sys.path``: credit the
    one same-named source closest to the test, none on a tie, and none when
    only the root is shared (it may be the standard library's module)."""
    _write(tmp_path, {
        "svc/a/util.py": "", "svc/b/util.py": "", "svc/a/tests/test_u.py":
        "import util" + A_TEST,
        "tools/x/json.py": "", "tests/test_j.py": "import json" + A_TEST,
        "svc/c/helpers.py": "", "svc/d/helpers.py": "",
        "svc/tests/test_h.py": "import helpers" + A_TEST,
    })
    index = tc.build_test_index(tmp_path)
    assert tc.has_sibling_test(tmp_path, "svc/a/util.py", index=index) is True
    assert tc.has_sibling_test(tmp_path, "svc/b/util.py", index=index) is False
    assert tc.has_sibling_test(tmp_path, "tools/x/json.py", index=index) is False
    assert tc.has_sibling_test(tmp_path, "svc/c/helpers.py", index=index) is False
    assert tc.has_sibling_test(tmp_path, "svc/d/helpers.py", index=index) is False


def test_unparseable_or_undecodable_test_credits_nothing(tmp_path: Path) -> None:
    _write(tmp_path, {"pkg/mod.py": "",
                      "tests/test_broken.py": "import pkg.mod\ndef test_(:\n"})
    (tmp_path / "tests/test_binary.py").write_bytes(b"import pkg.mod\n\xff\xfe")
    assert _credited(tmp_path, "pkg/mod.py") is False


def test_js_relative_specifiers_credit_and_aliases_do_not(tmp_path: Path) -> None:
    test = ("import { a } from '../src/alpha';\n"
            "import '../src/side.js';\n"
            "const g = require('../src/gamma');\n"
            "const d = await import('../src/delta');\n"
            "import x from '@/src/aliased';\n"
            "import type { T } from '../src/types';\n"
            "export { e } from '../src/epsilon';\n"
            "import y from 'react';\n"
            "describe('alpha', () => { it('works', () => {}); });\n")
    _write(tmp_path, {
        "web/src/alpha.ts": "", "web/src/side.js": "", "web/src/gamma/index.js": "",
        "web/src/delta.tsx": "", "web/src/aliased.ts": "",
        "web/spec/alpha_split.test.ts": test,
        "web/spec/no_tests.test.ts": "import { b } from '../src/beta';\n",
        "web/src/beta.ts": "", "web/src/types.ts": "", "web/src/epsilon.ts": "",
    })
    index = tc.build_test_index(tmp_path)
    for src in ("web/src/alpha.ts", "web/src/side.js", "web/src/gamma/index.js",
                "web/src/delta.tsx", "web/src/epsilon.ts"):
        assert tc.has_sibling_test(tmp_path, src, index=index) is True, src
    for src in ("web/src/aliased.ts", "web/src/beta.ts", "web/src/types.ts"):
        assert tc.has_sibling_test(tmp_path, src, index=index) is False, src


def test_js_specifier_above_the_root_is_ignored() -> None:
    files = frozenset({"a.ts"})
    assert ic.js_imports("import x from '../../a';\ntest('t', f);", "x", files) == []


def test_other_languages_keep_name_match_only(tmp_path: Path) -> None:
    """A Go repository is unchanged: no file is read for imports, and the name
    tiers answer exactly as before."""
    _write(tmp_path, {"go/foo.go": "package go\n", "go/foo_test.go": "package go\n",
                      "go/bar.go": "package go\n",
                      "go/split_test.go": 'import "example.com/go/bar"\n'})
    index = tc.build_test_index(tmp_path)
    assert tc.has_sibling_test(tmp_path, "go/foo.go", index=index) is True
    assert tc.has_sibling_test(tmp_path, "go/bar.go", index=index) is False


def test_scan_is_lazy_and_runs_once(tmp_path: Path, monkeypatch) -> None:
    """A source the name tiers credit never triggers the import scan; the
    first probe that reaches the tier reads each test once for the index."""
    _write(tmp_path, {"pkg/foo.py": "", "pkg/test_foo.py": A_TEST,
                      "pkg/bar.py": "", "pkg/baz.py": "",
                      "tests/test_split.py": "from pkg import bar" + A_TEST})
    reads: list[str] = []
    real = ic.FileReader.__call__
    monkeypatch.setattr(ic.FileReader, "__call__",
                        lambda self, rel: reads.append(rel) or real(self, rel))
    index = tc.build_test_index(tmp_path)
    assert tc.has_sibling_test(tmp_path, "pkg/foo.py", index=index) is True
    assert reads == []
    assert tc.has_sibling_test(tmp_path, "pkg/bar.py", index=index) is True
    assert tc.has_sibling_test(tmp_path, "pkg/baz.py", index=index) is False
    assert sorted(reads) == ["pkg/test_foo.py", "tests/test_split.py"]


def test_scan_cap_reads_tests_in_sorted_order(tmp_path: Path, monkeypatch) -> None:
    """Past the cap the remaining tests are not read: credits are lost, never
    invented, and the same files are lost on every run."""
    _write(tmp_path, {"pkg/a.py": "", "pkg/b.py": "",
                      "tests/test_1.py": "import pkg.a" + A_TEST,
                      "tests/test_2.py": "import pkg.b" + A_TEST})
    monkeypatch.setattr(ic, "MAX_SCAN_FILES", 1)
    index = tc.build_test_index(tmp_path)
    assert tc.has_sibling_test(tmp_path, "pkg/a.py", index=index) is True
    assert tc.has_sibling_test(tmp_path, "pkg/b.py", index=index) is False


def test_oversized_test_file_is_skipped(tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path, {"pkg/a.py": "", "tests/test_1.py": "import pkg.a" + A_TEST})
    monkeypatch.setattr(ic, "MAX_FILE_BYTES", 8)
    assert _credited(tmp_path, "pkg/a.py") is False


def test_consumers_agree_on_import_credit(tmp_path: Path) -> None:
    """The hotspot page and the focus signal credit an imported module; E2's
    co-located-and-co-committed map is out of scope by definition."""
    _write(tmp_path, {"pkg/foo.py": "", "pkg/bar.py": "",
                      "tests/test_foo_a.py": "from pkg import foo" + A_TEST})
    assert run_wiki._has_sibling_test(tmp_path, "pkg/foo.py") is True
    assert run_wiki._has_sibling_test(tmp_path, "pkg/bar.py") is False
    block = compute_test_focus(["pkg/foo.py", "pkg/bar.py"], None, None,
                               repo_root=tmp_path)
    by_path = {e["path"]: e["test_signal"] for e in block["entries"]}
    assert by_path == {"pkg/foo.py": "sibling_test_only", "pkg/bar.py": "unsupported"}
    assert ks._find_sibling_test(tmp_path, "pkg/foo.py") is None


def test_this_repo_credits_keyhole_signals() -> None:
    """The issue's success criterion: ``keyhole_signals.py`` is covered by the
    split ``test_keyhole_<family>.py`` files, none named after it."""
    repo = Path(__file__).resolve().parents[3]
    rel = "skills/assess/scripts/lib/keyhole_signals.py"
    if not (repo / rel).is_file():
        pytest.skip("not running from the repository checkout")
    index = tc.build_test_index(repo)
    assert tc.sibling_test_match(repo, rel, index) == tc.MATCH_IMPORT
    assert tc.has_sibling_test(repo, rel, index=index) is True
