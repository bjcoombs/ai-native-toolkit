"""Tests for the language-neutral import-graph builder interface (#707, #708).

The golden test pins the Python output: ``tests/fixtures/structure_golden/
expected.json`` was written by the pre-#708 ``structure_graph`` (grimp read in
place, no builder interface) over the fixture tree beside it, and the builder
path must reproduce it byte for byte, the new ``language`` field aside.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib import structure_drift as sd
from lib.headless_layers import structure
from lib.graph.import_graph import (
    NO_PYTHON_PACKAGES,
    NO_SOURCE_FILES,
    GraphUnavailable,
    ModuleGraph,
    dominant_language,
    is_graph_source,
    language_counts,
    select_graph,
)
from lib.import_graph_builders import BUILDERS
from lib.graph.import_graph_python import PYTHON_BUILDER
from lib.graph.import_graph_typescript import TYPESCRIPT_BUILDER
from lib.graph.structure_graph import analyze_structure

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "structure_golden"
REPO = GOLDEN / "repo"


def _dump(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True)


# The package map's fields (#942) postdate the frozen golden, which no current code rewrites.
PACKAGE_MAP_KEYS = ("packages", "package_count", "package_edges", "package_edge_count", "burrow_edge_count")


def _without_language(block: dict) -> dict:
    assert block.pop("language") == "python"
    for key in PACKAGE_MAP_KEYS:
        block.pop(key, None)
    return block


# --------------------------------------------------------------------------
# Golden: the Python output does not move
# --------------------------------------------------------------------------

def test_python_output_is_byte_identical_to_the_pre_builder_golden() -> None:
    expected = json.loads((GOLDEN / "expected.json").read_text(encoding="utf-8"))
    actual = {
        "structure": _without_language(analyze_structure(REPO).as_dict()),
        "structure_budget_20": _without_language(
            analyze_structure(REPO, keyhole_budget=20).as_dict()),
        "structure_scoped_alpha": _without_language(
            analyze_structure(REPO, scope=(REPO / "alpha").resolve()).as_dict()),
        "headless_structure": _without_language(structure(analyze_structure(REPO).as_dict())),
        "drift_communities": sorted(sorted(c) for c in sd._compute_communities(REPO)),
        "drift_module_paths": {
            k: v.as_posix() for k, v in sorted(sd._build_module_path_map(REPO).items())
        },
    }
    for key in expected:
        assert _dump(actual[key]) == _dump(expected[key]), key


def test_the_golden_fixture_exercises_every_signal() -> None:
    """A golden that measures nothing proves nothing: each A1-A4 path is hit."""
    block = json.loads((GOLDEN / "expected.json").read_text(encoding="utf-8"))["structure"]
    assert block["sccs"] and block["breakup_candidates"] and block["name_collisions"]
    assert block["internal_burrow_edges"] and block["cross_package_edges"] > 0
    assert 0 < block["front_door_ratio"] < 1
    assert any(fp["exposed_surface"] for fp in block["footprints"])


def test_python_is_the_registered_builder() -> None:
    assert PYTHON_BUILDER in BUILDERS
    assert PYTHON_BUILDER.language == "python"


# --------------------------------------------------------------------------
# Dominant-language reasons (#707)
# --------------------------------------------------------------------------

def _write(root: Path, rel: str, text: str = "x\n") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_a_repo_in_a_language_with_no_builder_names_it(tmp_path: Path) -> None:
    for rel in ("src/a.rs", "src/b.rs", "tools/x.py"):
        _write(tmp_path, rel)
    result = analyze_structure(tmp_path)
    assert result.available is False
    assert result.language is None
    assert result.reason.startswith("no_import_graph_rust: ")


def test_the_headless_marker_keeps_the_language_code(tmp_path: Path) -> None:
    from lib.counts_only import reason_code

    _write(tmp_path, "main.go", "package main\n")
    marker = structure(analyze_structure(tmp_path).as_dict())
    assert marker["available"] is False
    assert reason_code(marker["reason"]) == "no_import_graph_go"


def test_a_python_repo_without_packages_keeps_no_python_packages(tmp_path: Path) -> None:
    _write(tmp_path, "loose.py")
    _write(tmp_path, "run.sh")
    result = analyze_structure(tmp_path)
    assert result.reason == NO_PYTHON_PACKAGES
    assert structure(result.as_dict())["reason"].startswith("no_python_packages:")


def test_a_shell_heavy_repo_names_shell_not_python(tmp_path: Path) -> None:
    """Shell is named on purpose: the reader learns what the code is (#700)."""
    for rel in ("a.sh", "b.sh", "tool.py"):
        _write(tmp_path, rel)
    assert analyze_structure(tmp_path).reason.startswith("no_import_graph_shell: ")


def test_a_header_is_not_counted_as_c(tmp_path: Path) -> None:
    for rel in ("a.h", "b.h", "c.h", "main.cpp"):
        _write(tmp_path, rel)
    assert dominant_language(tmp_path) == "cpp"


def test_a_repo_with_no_source_says_so(tmp_path: Path) -> None:
    _write(tmp_path, "README.md")
    result = analyze_structure(tmp_path)
    assert result.reason == NO_SOURCE_FILES
    assert structure(result.as_dict())["reason"].startswith("no_source_files:")


def test_language_count_skips_excluded_and_out_of_scope_dirs(tmp_path: Path) -> None:
    _write(tmp_path, "node_modules/dep/index.js")
    _write(tmp_path, "vendored/a.go")
    _write(tmp_path, "svc/b.go")
    _write(tmp_path, "svc/c.rs")
    _write(tmp_path, "web/d.ts")
    assert language_counts(tmp_path, {"vendored"}) == {"go": 1, "rust": 1, "typescript": 1}
    assert dominant_language(tmp_path, {"vendored"}) == "go"  # a tie goes by name
    assert dominant_language(tmp_path, scope=tmp_path / "web") == "typescript"
    assert dominant_language(tmp_path / "web" / "missing") is None


# --------------------------------------------------------------------------
# Dispatch: one graph per language, the largest reported (#708)
# --------------------------------------------------------------------------

class _StubBuilder:
    """A builder that returns a fixed graph, or raises, whatever the tree."""

    def __init__(self, language: str, modules: list[str] | None,
                 edges: dict[str, set[str]] | None = None,
                 fail: str | None = None) -> None:
        self.language = language
        self._modules = modules
        self._edges = edges or {}
        self._fail = fail
        self.calls: list[tuple[set[str], Path | None]] = []

    def build(self, repo_root: Path, extra_exclude_dirs: set[str],
              scope: Path | None) -> ModuleGraph | None:
        self.calls.append((extra_exclude_dirs, scope))
        if self._fail is not None:
            raise GraphUnavailable(self._fail)
        if self._modules is None:
            return None
        return ModuleGraph(
            language=self.language,
            modules=sorted(self._modules),
            edges=self._edges,
            front_doors={m for m in self._modules if m.endswith("/index")},
            module_file={},
            package_of={m: m.split("/", 1)[0] for m in self._modules},
        )


def test_the_largest_graph_is_reported_with_its_language(tmp_path: Path) -> None:
    small = _StubBuilder("go", ["a/x", "b/y"], {"a/x": {"b/y"}})
    large = _StubBuilder("typescript", ["app/index", "app/main", "lib/index", "lib/util"],
                         {"app/main": {"lib/index", "lib/util"}, "lib/index": {"lib/util"}})
    block = analyze_structure(tmp_path, builders=(small, large)).as_dict()
    assert block["available"] is True
    assert block["language"] == "typescript"
    assert block["module_count"] == 4
    # A3 through the builder's package boundaries and front doors, no dotted names.
    assert block["cross_package_edges"] == 2
    assert block["front_door_ratio"] == 0.5
    assert block["internal_burrow_edges"] == [{"importer": "app/main", "imported": "lib/util"}]
    # A language with no public-surface measure reads 0, not a crash.
    assert all(fp["exposed_surface"] == 0 for fp in block["footprints"])


def test_the_package_map_data_counts_imports_and_front_door_bypasses(tmp_path: Path) -> None:
    builder = _StubBuilder("typescript", ["app/index", "app/main", "lib/index", "lib/util", "web/index"],
                           {"app/main": {"lib/index", "lib/util", "web/index"}, "lib/index": {"lib/util"},
                            "lib/util": {"app/index"}, "web/index": {"lib/util"}})
    block = analyze_structure(tmp_path, builders=(builder,)).as_dict()
    assert [p["id"] for p in block["packages"]] == ["app", "lib", "web"]  # all loc 0: by id
    assert [p["modules"] for p in block["packages"]] == [2, 2, 1]
    assert block["package_count"] == 3
    assert block["package_edges"] == [
        {"from": "app", "to": "lib", "imports": 2, "bypass": 1},
        {"from": "app", "to": "web", "imports": 1, "bypass": 0},
        {"from": "lib", "to": "app", "imports": 1, "bypass": 0},
        {"from": "web", "to": "lib", "imports": 1, "bypass": 1},
    ]
    assert sum(e["imports"] for e in block["package_edges"]) == block["cross_package_edges"]
    assert sum(e["bypass"] for e in block["package_edges"]) == block["burrow_edge_count"] == 2


def test_the_bypass_total_counts_every_burrow_past_the_listed_cap(tmp_path: Path) -> None:
    modules = ["app/index", "lib/index", *(f"lib/m{i:03}" for i in range(120))]
    builder = _StubBuilder("typescript", modules, {"app/index": {f"lib/m{i:03}" for i in range(120)}})
    block = analyze_structure(tmp_path, builders=(builder,)).as_dict()
    assert len(block["internal_burrow_edges"]) == 100
    assert block["burrow_edge_count"] == 120
    assert block["package_edges"] == [{"from": "app", "to": "lib", "imports": 120, "bypass": 120}]


def test_the_golden_repo_package_data_matches_its_burrows_and_sizes() -> None:
    block = analyze_structure(REPO).as_dict()
    assert block["packages"] and block["package_count"] == len(block["packages"])
    assert sum(e["bypass"] for e in block["package_edges"]) == block["burrow_edge_count"] > 0
    assert block["burrow_edge_count"] == len(block["internal_burrow_edges"])  # below the list cap
    assert sum(e["imports"] for e in block["package_edges"]) == block["cross_package_edges"]
    assert block["package_edge_count"] == len(block["package_edges"])
    assert [(-e["imports"], e["from"], e["to"]) for e in block["package_edges"]] == \
        sorted((-e["imports"], e["from"], e["to"]) for e in block["package_edges"])
    assert sum(p["loc"] for p in block["packages"]) == sum(fp["size"] for fp in block["footprints"])
    assert sum(p["modules"] for p in block["packages"]) == block["module_count"]
    assert [(-p["loc"], p["id"]) for p in block["packages"]] == sorted((-p["loc"], p["id"]) for p in block["packages"])


def test_the_package_list_is_capped_and_edges_stay_among_kept_packages(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("lib.graph.structure_graph.MAX_PACKAGES", 2)
    builder = _StubBuilder("typescript", ["a/index", "b/index", "c/index"],
                           {"a/index": {"b/index", "c/index"}})
    block = analyze_structure(tmp_path, builders=(builder,)).as_dict()
    assert [p["id"] for p in block["packages"]] == ["a", "b"] and block["package_count"] == 3
    assert block["package_edges"] == [{"from": "a", "to": "b", "imports": 1, "bypass": 0}]
    assert block["package_edge_count"] == 1


def test_the_package_edge_list_keeps_the_heaviest_pairs_and_counts_them_all(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("lib.graph.structure_graph.MAX_PACKAGE_EDGES", 2)
    builder = _StubBuilder("typescript", ["a/index", "a/x", "b/index", "c/index", "d/index"],
                           {"a/index": {"b/index", "c/index", "d/index"}, "a/x": {"c/index", "d/index"}})
    block = analyze_structure(tmp_path, builders=(builder,)).as_dict()
    assert block["package_edges"] == [{"from": "a", "to": "c", "imports": 2, "bypass": 0},
                                      {"from": "a", "to": "d", "imports": 2, "bypass": 0}]
    assert block["package_edge_count"] == 3
    assert block["cross_package_edges"] - sum(e["imports"] for e in block["package_edges"]) == 1


def test_counts_only_hashes_package_ids_and_keeps_every_count() -> None:
    from lib.counts_only import HASH_PREFIX, counts_only
    block = analyze_structure(REPO).as_dict()
    out = counts_only({"structure": block}, hash_paths=True, salt="fleet")["structure"]
    ids = [p["id"] for p in block["packages"]]
    assert [p["id"] for p in out["packages"]] != ids
    assert all(p["id"].startswith(HASH_PREFIX) for p in out["packages"])
    assert all(e["from"].startswith(HASH_PREFIX) and e["to"].startswith(HASH_PREFIX)
               for e in out["package_edges"])
    dumped = json.dumps({k: out[k] for k in PACKAGE_MAP_KEYS})
    assert not any(f'"{name}"' in dumped for name in ids)
    # Every number survives: a dropped field fails here.
    assert [{k: v for k, v in p.items() if k != "id"} for p in out["packages"]] == \
        [{k: v for k, v in p.items() if k != "id"} for p in block["packages"]]
    assert [(e["imports"], e["bypass"]) for e in out["package_edges"]] == \
        [(e["imports"], e["bypass"]) for e in block["package_edges"]]
    assert out["package_count"] == block["package_count"]
    assert out["burrow_edge_count"] == block["burrow_edge_count"]
    assert out["package_edge_count"] == block["package_edge_count"]


def test_an_unavailable_block_has_no_package_data(tmp_path: Path) -> None:
    block = analyze_structure(tmp_path).as_dict()
    assert block["available"] is False
    assert (block["packages"], block["package_count"], block["package_edges"],
            block["package_edge_count"], block["burrow_edge_count"]) == ([], 0, [], 0, 0)


def test_a_tie_on_modules_goes_to_edges_then_language(tmp_path: Path) -> None:
    edged = _StubBuilder("rust", ["a/x", "b/y"], {"a/x": {"b/y", "external"}})
    bare = _StubBuilder("go", ["a/x", "b/y"])
    assert select_graph(tmp_path, (bare, edged)).graph.language == "rust"
    twin = _StubBuilder("dart", ["a/x", "b/y"], {"a/x": {"b/y"}})
    assert select_graph(tmp_path, (edged, twin)).graph.language == "dart"


def test_a_failing_builder_does_not_sink_another_language(tmp_path: Path) -> None:
    broken = _StubBuilder("python", None, fail="grimp not installed; static structure not assessed")
    working = _StubBuilder("go", ["a/x", "b/y"], {"a/x": {"b/y"}})
    selection = select_graph(tmp_path, (broken, working))
    assert selection.graph is not None and selection.graph.language == "go"


def test_with_no_dominant_builder_the_first_failure_is_the_reason(tmp_path: Path) -> None:
    _write(tmp_path, "a.rs")
    first = _StubBuilder("python", None, fail="grimp failed to build import graph (x)")
    second = _StubBuilder("go", None, fail="second")
    assert select_graph(tmp_path, (first, second)).reason.startswith("grimp failed")


def test_the_dominant_builders_failure_is_the_reason(tmp_path: Path) -> None:
    _write(tmp_path, "a.go")
    first = _StubBuilder("python", None, fail="grimp failed to build import graph (x)")
    second = _StubBuilder("go", None, fail="go list failed")
    assert select_graph(tmp_path, (first, second)).reason == "go list failed"


def test_a_failing_dominant_builder_is_not_masked_by_a_minor_graph(tmp_path: Path) -> None:
    """A Python repo without grimp keeps no_grimp, not a stray .mjs graph (#709 review)."""
    for rel in ("pkg/__init__.py", "pkg/a.py", "pkg/b.py", "scripts/tool.mjs"):
        _write(tmp_path, rel)
    broken = _StubBuilder("python", None, fail="grimp not installed; static structure not assessed")
    selection = select_graph(tmp_path, (broken, TYPESCRIPT_BUILDER))
    assert selection.graph is None
    assert structure(analyze_structure(tmp_path, builders=(broken, TYPESCRIPT_BUILDER))
                     .as_dict())["reason"].startswith("no_grimp:")


def test_the_dominant_graph_wins_over_a_larger_minor_one(tmp_path: Path) -> None:
    """Sizes are not compared across languages: Go nodes are packages, TS nodes files."""
    for rel in ("a/x.go", "b/y.go", "c/z.go", "web/index.ts"):
        _write(tmp_path, rel)
    go = _StubBuilder("go", ["a", "b"], {"a": {"b"}})
    ts = _StubBuilder("typescript", ["w/index", "w/1", "w/2", "w/3"])
    assert select_graph(tmp_path, (ts, go)).graph.language == "go"


def test_a_dominant_builder_with_nothing_to_graph_falls_back(tmp_path: Path) -> None:
    """Found nothing is not a failure: a minor language's graph stands in, as on main."""
    for rel in ("loose.py", "other.py", "scripts/tool.mjs"):
        _write(tmp_path, rel)
    selection = select_graph(tmp_path, (PYTHON_BUILDER, TYPESCRIPT_BUILDER))
    assert selection.graph is not None and selection.graph.language == "javascript"


def test_with_no_graph_at_all_the_dominant_reason_stays(tmp_path: Path) -> None:
    for rel in ("loose.py", "other.py"):
        _write(tmp_path, rel)
    selection = select_graph(tmp_path, BUILDERS)
    assert selection.graph is None and selection.reason == NO_PYTHON_PACKAGES


def test_a_go_module_beside_loose_python_scripts_reports_go(tmp_path: Path) -> None:
    _write(tmp_path, "go.mod", "module example.com/m\n")
    _write(tmp_path, "svc/svc.go", 'package svc\nimport "example.com/m/util"\n')
    _write(tmp_path, "util/util.go", "package util\n")
    for rel in ("scripts/a.py", "scripts/b.py", "scripts/c.py"):
        _write(tmp_path, rel, "print(1)\n")
    assert dominant_language(tmp_path, builders=BUILDERS) == "python"
    block = analyze_structure(tmp_path).as_dict()
    assert block["available"] is True and block["language"] == "go"


def test_a_dominant_language_with_no_builder_falls_back(tmp_path: Path) -> None:
    for rel in ("a.rs", "b.rs", "c.rs", "svc/main.go"):
        _write(tmp_path, rel)
    selection = select_graph(tmp_path, (_StubBuilder("go", ["svc"]),))
    assert selection.graph is not None and selection.graph.language == "go"


def test_a_builders_languages_attribute_covers_the_dominant_language(tmp_path: Path) -> None:
    for rel in ("lib/a.js", "lib/b.js", "x.py"):
        _write(tmp_path, rel)
    _write(tmp_path, "lib/a.js", "require('./b');\n")
    broken_python = _StubBuilder("python", None, fail="grimp failed to build import graph (x)")
    selection = select_graph(tmp_path, (broken_python, TYPESCRIPT_BUILDER))
    assert selection.graph is not None and selection.graph.language == "javascript"


def test_an_empty_graph_counts_as_no_graph(tmp_path: Path) -> None:
    _write(tmp_path, "a.go")
    selection = select_graph(tmp_path, (_StubBuilder("go", []),))
    assert selection.graph is None
    assert selection.reason.startswith("no_import_graph_go: ")


def test_builders_receive_the_excludes_and_scope(tmp_path: Path) -> None:
    stub = _StubBuilder("go", None)
    scope = tmp_path / "svc"
    analyze_structure(tmp_path, extra_exclude_dirs={"gen"}, scope=scope, builders=(stub,))
    assert stub.calls == [({"gen"}, scope)]


@pytest.mark.parametrize("budget", [None, 5])
def test_an_unavailable_block_carries_no_language(tmp_path: Path, budget: int | None) -> None:
    block = analyze_structure(tmp_path, keyhole_budget=budget).as_dict()
    assert block["available"] is False and block["language"] is None


def test_files_a_builder_skips_do_not_make_their_language_dominant(tmp_path: Path) -> None:
    """A Python app's minified assets keep the Python graph, as on main (#709 review)."""
    _write(tmp_path, "app/__init__.py", "")
    _write(tmp_path, "app/views.py", "from app import models\n")
    _write(tmp_path, "app/models.py", "")
    for rel in ("static/js/jquery.min.js", "static/js/vendor.min.js", "static/js/x.min.js",
                "types/api.d.ts", "web/__tests__/a.js", "vite.config.js"):
        _write(tmp_path, rel)
    assert dominant_language(tmp_path) == "javascript"  # every file, no builder rules
    assert dominant_language(tmp_path, builders=BUILDERS) == "python"
    assert language_counts(tmp_path, builders=BUILDERS)["javascript"] == 0
    block = analyze_structure(tmp_path).as_dict()
    assert block["available"] is True and block["language"] == "python"


def test_go_test_files_and_skipped_dirs_do_not_count_toward_go(tmp_path: Path) -> None:
    """The Go builder's hook applies to dominance, as the TypeScript one does."""
    _write(tmp_path, "app/__init__.py", "")
    _write(tmp_path, "app/a.py", "")
    for rel in ("svc/a_test.go", "svc/b_test.go", "vendor/x/x.go", "_tools/t.go"):
        _write(tmp_path, rel, "package x\n")
    assert dominant_language(tmp_path) == "go"
    assert dominant_language(tmp_path, builders=BUILDERS) == "python"


@pytest.mark.parametrize(("rel", "language", "graphed"), [
    ("web/a.js", "typescript", True),       # the TypeScript builder covers .js
    ("web/a.ts", "javascript", True),
    ("web/a.min.js", "typescript", False),  # its skip rules apply
    ("web/__tests__/a.ts", "typescript", False),
    ("svc/a.go", "go", True),
    ("svc/a_test.go", "go", False),
    ("testdata/a.go", "go", False),
    ("pkg/a.py", "python", True),           # no hook: extension only
    ("pkg/a.py", "go", False),
    ("src/a.rs", "rust", True),             # no builder: extension only
])
def test_is_graph_source_asks_the_covering_builder(rel: str, language: str,
                                                   graphed: bool) -> None:
    assert is_graph_source(Path(rel), language) is graphed
