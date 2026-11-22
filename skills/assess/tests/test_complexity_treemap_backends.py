"""Per-function complexity backends per language: lizard, the approximate Dart
scanner, backend_by_language and the worst function's name.

Split from test_complexity_treemap.py (#720); the stubbed loader lives there.
"""
from __future__ import annotations

import json
import types

import pytest
from test_complexity_treemap import _load_treemap


@pytest.fixture(scope="module")
def treemap():
    return _load_treemap()


# --------------------------------------------------------------------------
# Per-function backend per language and the worst function's name (issue #363)


def test_write_stats_backend_by_language_maps_lizard_and_null(treemap, tmp_path):
    """fn_ccn.source lists the backends that scored a file, as objects, and
    backend_by_language maps each programming language to its backend or to
    null when only scc scored it at file level. Data and markup languages,
    where scc counts no decision points, carry no key."""
    root = tmp_path
    py = root / "src" / "app.py"
    ex = root / "lib" / "router.ex"
    js = root / "data.json"
    md = root / "README.md"
    out = root / "stats.json"
    treemap.write_stats(
        [(py, 20, 8.0, "lizard"), (ex, 9, 2.0, "scc"),
         (js, 50, 0.0, "scc"), (md, 30, 0.0, "scc")],
        None, None, root, out,
        fn_ccn_by_path={py: [1.0, 7.0]},
        fn_name_by_path={py: "gnarly"},
        languages_by_path={py: "Python", ex: "Elixir",
                           js: "JSON", md: "Markdown"},
    )
    fn = json.loads(out.read_text(encoding="utf-8"))["fn_ccn"]
    assert fn["source"] == [{"name": "lizard", "approximate": False}]
    assert fn["backend_by_language"] == {"Python": "lizard", "Elixir": None}


def test_write_stats_backend_by_language_partial_coverage_is_null(
        treemap, tmp_path):
    """A language a backend covers only in part maps to null: one lizard file
    must not make the language's scc-only files read as covered. A file with
    no decision points loses no per-function figure and does not downgrade."""
    root = tmp_path
    cpp, ipp = root / "a.cpp", root / "a.ipp"
    js, mjs = root / "a.js", root / "b.mjs"
    out = root / "stats.json"
    treemap.write_stats(
        [(cpp, 20, 8.0, "lizard"), (ipp, 30, 4.0, "scc"),
         (js, 20, 3.0, "lizard"), (mjs, 5, 0.0, "scc")],
        None, None, root, out,
        fn_ccn_by_path={cpp: [8.0], js: [3.0]},
        languages_by_path={cpp: "C++", ipp: "C++",
                           js: "JavaScript", mjs: "JavaScript"},
    )
    fn = json.loads(out.read_text(encoding="utf-8"))["fn_ccn"]
    assert fn["backend_by_language"] == {"C++": None, "JavaScript": "lizard"}


def test_write_stats_backend_by_language_empty_source_when_no_backend(
        treemap, tmp_path):
    """An scc-only run lists no backend rather than claiming lizard."""
    root = tmp_path
    ex = root / "router.ex"
    out = root / "stats.json"
    treemap.write_stats([(ex, 9, 2.0, "scc")], None, None, root, out,
                        languages_by_path={ex: "Elixir"})
    fn = json.loads(out.read_text(encoding="utf-8"))["fn_ccn"]
    assert fn["source"] == []
    assert fn["backend_by_language"] == {"Elixir": None}


def test_write_stats_max_fn_name_names_worst_function(treemap, tmp_path):
    """Every ranked row carries max_fn_name beside max_fn_ccn; it is null
    wherever max_fn_ccn is null (an scc-scored file, or a lizard file with no
    functions)."""
    root = tmp_path
    py = root / "app.py"
    flat = root / "flat.py"
    ex = root / "router.ex"
    out = root / "stats.json"
    treemap.write_stats(
        [(py, 20, 8.0, "lizard"), (flat, 5, 1.0, "lizard"),
         (ex, 9, 2.0, "scc")],
        None, None, root, out,
        fn_ccn_by_path={py: [1.0, 7.0], flat: []},
        fn_name_by_path={py: "gnarly"},
    )
    stats = json.loads(out.read_text(encoding="utf-8"))
    for key in ("top_hotspots", "top_complex", "top_large"):
        rows = {r["path"]: r for r in stats[key]}
        assert rows["app.py"]["max_fn_ccn"] == 7.0
        assert rows["app.py"]["max_fn_name"] == "gnarly"
        assert rows["flat.py"]["max_fn_ccn"] is None
        assert rows["flat.py"]["max_fn_name"] is None
        assert rows["router.ex"]["max_fn_name"] is None


def test_lizard_scores_fills_max_fn_name_with_worst_function(
        treemap, tmp_path, monkeypatch):
    """lizard_scores records the name of each file's highest-ccn function in
    the optional fn_names map, and collect threads it through."""
    src = tmp_path / "app.py"
    src.write_text("def simple(a):\n    return a\n", encoding="utf-8")

    def fn(name, ccn):
        return types.SimpleNamespace(name=name, cyclomatic_complexity=ccn)

    fake = types.SimpleNamespace(
        filename=str(src), nloc=10,
        function_list=[fn("simple", 1), fn("gnarly", 7), fn("tie", 7)],
    )
    monkeypatch.setattr(treemap.lizard, "analyze",
                        lambda **kw: [fake], raising=False)
    monkeypatch.setattr(treemap, "scc_scores", lambda root, **kw: {})
    names: dict = {}
    scores = treemap.lizard_scores(tmp_path, fn_names=names)
    assert scores[src.resolve()][2] == [1.0, 7.0, 7.0]
    assert names == {src.resolve(): "gnarly"}

    via_collect: dict = {}
    treemap.collect(tmp_path, by="complexity", fn_names=via_collect)
    assert via_collect == {src.resolve(): "gnarly"}


# Approximate per-function backend for Dart (issue #364)


def test_collect_dart_scanner_scores_scc_dart_files(
        treemap, tmp_path, monkeypatch):
    """collect adds the Dart scanner's per-function figures to scc-scored
    .dart files, names the worst function and records the backend; other
    scc files stay without a breakdown."""
    dart = tmp_path / "lib" / "order.dart"
    dart.parent.mkdir()
    dart.write_text("int a(x) { if (x) {} return 1; }\nint b() => 2;\n", encoding="utf-8")
    ex = tmp_path / "router.ex"
    ex.write_text("defmodule R do\nend\n", encoding="utf-8")
    monkeypatch.setattr(treemap, "lizard_scores", lambda root, **kw: {})
    monkeypatch.setattr(
        treemap, "scc_scores",
        lambda root, **kw: {dart.resolve(): (2, 3.0), ex.resolve(): (2, 1.0)})
    names: dict = {}
    backends: dict = {}
    *_, fn_ccn = treemap.collect(tmp_path, by="complexity",
                                 fn_names=names, fn_backends=backends)
    assert fn_ccn == {dart.resolve(): [2.0, 1.0]}
    assert names == {dart.resolve(): "a"}
    assert backends == {dart.resolve(): "dart-scanner"}


def test_write_stats_dart_scanner_marked_approximate(treemap, tmp_path):
    """The Dart scanner appears in fn_ccn.source with approximate true, maps
    Dart in backend_by_language, and fills the Dart row's max_fn_ccn and
    max_fn_name."""
    root = tmp_path
    py = root / "app.py"
    dart = root / "order.dart"
    out = root / "stats.json"
    treemap.write_stats(
        [(py, 20, 8.0, "lizard"), (dart, 40, 30.0, "scc")],
        None, None, root, out,
        fn_ccn_by_path={py: [7.0], dart: [1.0, 12.0]},
        fn_name_by_path={py: "gnarly", dart: "routeOrder"},
        languages_by_path={py: "Python", dart: "Dart"},
        fn_backend_by_path={dart: "dart-scanner"},
    )
    stats = json.loads(out.read_text(encoding="utf-8"))
    fn = stats["fn_ccn"]
    assert fn["source"] == [{"name": "dart-scanner", "approximate": True},
                            {"name": "lizard", "approximate": False}]
    assert fn["backend_by_language"] == {"Dart": "dart-scanner",
                                         "Python": "lizard"}
    row = {r["path"]: r for r in stats["top_hotspots"]}["order.dart"]
    assert (row["max_fn_ccn"], row["max_fn_name"]) == (12.0, "routeOrder")


def test_collect_dart_scanner_skips_files_with_no_function(
        treemap, tmp_path, monkeypatch):
    """A Dart file the scanner finds no function in stays scc-only, so it
    records no backend and cannot make Dart read as covered."""
    dart = tmp_path / "consts.dart"
    dart.write_text("const a = 1;\n", encoding="utf-8")
    monkeypatch.setattr(treemap, "lizard_scores", lambda root, **kw: {})
    monkeypatch.setattr(treemap, "scc_scores",
                        lambda root, **kw: {dart.resolve(): (1, 1.0)})
    backends: dict = {}
    *_, fn_ccn = treemap.collect(tmp_path, by="complexity",
                                 fn_backends=backends)
    assert fn_ccn == {} and backends == {}


def test_effective_ccn_clamps_dart_scanner_max_to_scc_aggregate(treemap):
    """A Dart row takes ccn from scc and max_fn_ccn from the scanner; a
    scanner figure above the aggregate must not lift the effective value
    past it. Lizard rows, where max <= aggregate, are unchanged."""
    assert treemap._effective_ccn(1.0, 5.0) == 1.0
    assert treemap._effective_ccn(0.0, 3.0) == 0.0
    w = treemap.PER_FUNCTION_WEIGHT
    expected = 10.0 ** w * 100.0 ** (1 - w)
    assert abs(treemap._effective_ccn(100.0, 10.0) - expected) < 1e-9
