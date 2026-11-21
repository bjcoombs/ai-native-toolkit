"""Tests for the Go import-graph builder (issue #710)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import jsonschema
import pytest

import assess_signals
from lib.keyhole_signals import _graphed_dirs
from lib.import_graph import select_graph
from lib.import_graph_builders import BUILDERS
from lib.signals_schema import SCHEMA_PATH
from lib.structure_drift import detect_grouping_disagreement
from lib.import_graph_go import (
    GO_BUILDER,
    exported_surface,
    module_path,
    parse_imports,
    strip_comments,
)
from lib.structure_graph import analyze_structure

MODULE = "example.com/acme-internal/payments"


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _two_communities(root: Path) -> None:
    """Two package clusters joined by one import: a clear modularity score."""
    _write(root, {
        "go.mod": f"module {MODULE}\n\ngo 1.22\n",
        "cmd/server/main.go": (
            "package main\n\nimport (\n\t\"fmt\"\n\t\"" + MODULE + "/api\"\n)\n\n"
            "func main() { fmt.Println(api.Run()) }\n"),
        "api/api.go": (
            f'package api\n\nimport "{MODULE}/api/handlers"\n'
            f'import "{MODULE}/store"\n\nfunc Run() string {{ return handlers.X }}\n'),
        "api/handlers/h.go": (
            f'package handlers\n\nimport "{MODULE}/api/render"\n\nconst X = "x"\n'),
        "api/render/r.go": "package render\n\nfunc Render() {}\n",
        "store/store.go": (
            f'package store\n\nimport (\n\tdb "{MODULE}/store/db"\n\t_ "{MODULE}/store/cache"\n)\n'
            "type Store struct{}\n"),
        "store/db/db.go": f'package db\n\nimport "{MODULE}/store/cache"\n\ntype Conn int\n',
        "store/cache/cache.go": "package cache\n\ntype Cache struct{}\nfunc New() *Cache { return nil }\n",
    })


# -- parsing ------------------------------------------------------------------

def test_every_import_form_is_read() -> None:
    src = (
        "// Package x.\n/* import \"commented/out\" */\npackage x\n\n"
        "import \"a\"; import b \"b\"\n"
        "import (\n\t. \"c\" // trailing \"not/this\"\n\t_ `d`\n\n\t\"e\"\n)\n"
        "import \"C\"\n"
        "var s = \"import \\\"never\\\"\"\n"
        "import \"after/a/decl\"\n"
    )
    assert parse_imports(src) == ["a", "b", "c", "d", "e", "C"]


def test_a_file_with_no_package_clause_imports_nothing() -> None:
    assert parse_imports('import "a"\n') == []


def test_comments_are_blanked_but_strings_and_lines_kept() -> None:
    out = strip_comments('a := "//x" // gone\n/* one\ntwo */ r := \'"\'\n')
    assert '"//x"' in out and "gone" not in out and "one" not in out
    assert out.count("\n") == 3 and "'\"'" in out


@pytest.mark.parametrize("text, expected", [
    ("module example.com/a\n", "example.com/a"),
    ('// module wrong\nmodule "example.com/q" // c\n', "example.com/q"),
    ("module (\n\texample.com/block\n)\n", "example.com/block"),
    ("module\texample.com/tab\n", "example.com/tab"),
    ("module(\n\texample.com/tight\n)\n", "example.com/tight"),
    ("go 1.22\n", None),
])
def test_the_module_path_is_read_from_go_mod(text: str, expected: str | None) -> None:
    assert module_path(text) == expected


def test_the_surface_counts_exported_funcs_and_types_not_methods() -> None:
    src = (
        "package p\n"
        "func Exported() {}\nfunc internal() {}\nfunc (s *S) Method() {}\n"
        "type S struct {\n\tField int\n}\ntype hidden int\n"
        "type (\n\tA int\n\tb int\n\tC struct {\n\t\tInner int\n\t}\n)\n"
        "// func Commented() {}\n"
    )
    assert exported_surface(src) == 4  # Exported, S, A, C


# -- the builder --------------------------------------------------------------

def test_two_package_clusters_give_a_modularity_score(tmp_path: Path) -> None:
    _two_communities(tmp_path)
    block = analyze_structure(tmp_path).as_dict()
    assert block["available"] is True and block["language"] == "go"
    assert block["module_count"] == 7
    assert block["edge_count"] == 7
    assert block["modularity_q"] is not None and block["modularity_q"] >= 0.3
    # Every import reaches only exported identifiers: no burrow edge in Go.
    assert block["cross_package_edges"] == 7
    assert block["front_door_ratio"] == 1.0
    assert block["internal_burrow_edges"] == []
    by_module = {fp["module"]: fp for fp in block["footprints"]}
    assert set(by_module) == {"cmd/server", "api", "api/handlers", "api/render",
                              "store", "store/db", "store/cache"}
    assert by_module["store/cache"]["exposed_surface"] == 2
    assert by_module["store"]["dep_surface"] == 3  # db.Conn + cache.Cache, New
    assert all(fp["size"] == 0 for fp in block["footprints"])


def test_ids_are_directories_and_the_module_path_never_appears(tmp_path: Path) -> None:
    _two_communities(tmp_path)
    graph = GO_BUILDER.build(tmp_path, set(), None)
    assert graph is not None
    assert "example.com" not in json.dumps(
        [graph.modules, sorted((k, sorted(v)) for k, v in graph.edges.items())])


def test_no_go_mod_is_no_graph_and_names_go(tmp_path: Path) -> None:
    _write(tmp_path, {"a/a.go": 'package a\nimport "x/b"\n', "b/b.go": "package b\n"})
    assert GO_BUILDER.build(tmp_path, set(), None) is None
    assert select_graph(tmp_path, BUILDERS).reason.startswith("no_import_graph_go: ")


def test_tests_testdata_vendor_and_hidden_dirs_are_skipped(tmp_path: Path) -> None:
    _write(tmp_path, {
        "go.mod": "module m\n",
        "a/a.go": 'package a\nimport "m/b"\n',
        "a/a_test.go": 'package a\nimport "m/c"\n',
        "b/b.go": "package b\n",
        "c/c.go": "package c\n",
        "a/testdata/t.go": "package t\n",
        "vendor/v/v.go": "package v\n",
        "_tools/x.go": "package x\n",
        ".hidden/h.go": "package h\n",
        "gen/g.go": "package g\n",
    })
    graph = GO_BUILDER.build(tmp_path, {"gen"}, None)
    assert graph is not None
    assert graph.modules == ["a", "b", "c"]
    assert graph.edges["a"] == {"b"}


def test_a_nested_module_owns_its_own_tree(tmp_path: Path) -> None:
    _write(tmp_path, {
        "go.mod": "module outer\n",
        "main.go": 'package main\nimport (\n\t"outer/lib"\n\t"inner/core"\n)\n',
        "lib/lib.go": "package lib\n",
        "tools/go.mod": "module inner\n",
        "tools/core/core.go": 'package core\nimport "outer/lib"\n',
        "tools/x.go": "package tools\n",
    })
    graph = GO_BUILDER.build(tmp_path, set(), None)
    assert graph is not None
    assert graph.modules == [".", "lib", "tools", "tools/core"]
    assert graph.edges["."] == {"lib", "tools/core"}
    assert graph.edges["tools/core"] == {"lib"}


def test_scope_keeps_only_packages_inside_it(tmp_path: Path) -> None:
    _two_communities(tmp_path)
    graph = GO_BUILDER.build(tmp_path, set(), tmp_path / "store")
    assert graph is not None
    assert graph.modules == ["store", "store/cache", "store/db"]
    assert graph.edges["store"] == {"store/cache", "store/db"}
    assert GO_BUILDER.build(tmp_path, set(), tmp_path / "nowhere") is None


def test_an_unreadable_file_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, {"go.mod": "module m\n", "a/a.go": 'package a\nimport "m/b"\n',
                      "b/b.go": "package b\n"})
    real = Path.read_text

    def flaky(self: Path, *args: object, **kwargs: object) -> str:
        if self.name == "a.go":
            raise OSError("unreadable")
        return real(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", flaky)
    graph = GO_BUILDER.build(tmp_path, set(), None)
    assert graph is not None and graph.edges["a"] == set()


# -- counts-only, --hash-paths ------------------------------------------------

def test_hash_paths_hides_module_paths_and_package_dirs(tmp_path: Path) -> None:
    repo = tmp_path / "target"
    _two_communities(repo)
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True,
                   encoding="utf-8", errors="replace")
    out = tmp_path / "out.json"
    assert assess_signals.main([str(repo), "--out", str(out), "--scans", "structure",
                                "--counts-only", "--hash-paths"]) == 0
    text = out.read_text(encoding="utf-8")
    doc = json.loads(text)
    jsonschema.validate(doc, json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))
    block = doc["scans"]["structure"]
    assert block["available"] is True and block["language"] == "go"
    assert block["modularity_q"] is not None
    for leak in ("example.com", "acme-internal", "payments", "handlers", "store/db",
                 "cmd/server"):
        assert leak not in text, leak


# -- consumers of a graph with no module file ---------------------------------

def test_drift_tier_one_degrades_without_a_module_file_map(tmp_path: Path) -> None:
    """An empty static lens would read every declared pair as split by the graph."""
    _two_communities(tmp_path)
    _write(tmp_path, {"CODEOWNERS": "/api/ @team-a\n/store/ @team-b\n"})
    block = detect_grouping_disagreement(tmp_path, coupling_pairs=[])
    assert block["available"] is False
    assert block["reason"].startswith("no_static_file_map: the go import graph")
    assert block["human_grouped_static_splits_count"] == 0


def test_static_metrics_project_only_onto_the_graphed_language() -> None:
    commits = [{Path("api/api.go"), Path("scripts/tool.py"), Path("docs/a.md")}]
    assert _graphed_dirs(commits, "go") == {"api"}
    assert _graphed_dirs(commits, "python") == {"scripts"}
