"""Tests for the static dependency-structure analysis (Signals A1-A4).

The deterministic core's contract: every signal is reproducible and verifiable
from a fixed source tree. These tests pin each signal's arithmetic (A1
footprint additivity + direct-only-ness), graph semantics (A2 SCCs and Q range,
A3 front-door vs burrow, A4 cut-lines), the config read, and graceful
degradation when grimp / networkx are absent -- plus an integration run against
this package itself.
"""
from __future__ import annotations

import networkx as nx
import pytest

import lib.graph.import_graph_python as igp
import lib.graph.structure_graph as sg
from lib.assess_config import DEFAULT_KEYHOLE_BUDGET


# --------------------------------------------------------------------------
# Graceful degradation
# --------------------------------------------------------------------------

def test_degrades_when_grimp_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(igp, "_GRIMP_AVAILABLE", False)
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    result = sg.analyze_structure(tmp_path)
    assert result.available is False
    assert "grimp" in result.reason
    # Still JSON-serialisable and carries the budget.
    assert result.as_dict()["available"] is False


def test_degrades_when_networkx_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(sg, "_NETWORKX_AVAILABLE", False)
    result = sg.analyze_structure(tmp_path)
    assert result.available is False
    assert "networkx" in result.reason


def test_no_packages_is_unavailable_not_an_empty_graph(tmp_path):
    (tmp_path / "loose.py").write_text("x = 1\n", encoding="utf-8")  # no __init__.py anywhere
    result = sg.analyze_structure(tmp_path)
    assert result.available is False
    assert result.footprints == []
    assert "no importable" in result.reason
    assert result.as_dict()["modularity_q"] is None   # unmeasured is null, not 0.0


@pytest.mark.parametrize("module, flag", [
    pytest.param(igp, "_GRIMP_AVAILABLE", id="lib.import_graph_python-_GRIMP_AVAILABLE"),
    pytest.param(sg, "_NETWORKX_AVAILABLE", id="lib.structure_graph-_NETWORKX_AVAILABLE")])
def test_an_unavailable_block_emits_null_modularity(monkeypatch, tmp_path, module, flag):
    monkeypatch.setattr(module, flag, False)
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    block = sg.analyze_structure(tmp_path).as_dict()
    assert block["available"] is False
    assert block["modularity_q"] is None


def test_an_edgeless_graph_has_no_modularity_not_zero(tmp_path):
    """Modules that never import each other: Q is undefined, not 0.0 (issue #682)."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    for name in ("__init__", "a", "b"):
        (pkg / f"{name}.py").write_text("x = 1\n", encoding="utf-8")
    block = sg.analyze_structure(tmp_path).as_dict()
    assert block["available"] is True and block["module_count"] == 3
    assert block["edge_count"] == 0
    assert block["modularity_q"] is None
    assert block["modularity_reason"] == sg.NO_IMPORT_EDGES == "no_import_edges"


def test_a_graph_with_edges_measures_modularity_with_no_reason(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "a.py").write_text("from pkg import b\n", encoding="utf-8")
    (pkg / "b.py").write_text("x = 1\n", encoding="utf-8")
    block = sg.analyze_structure(tmp_path).as_dict()
    assert block["edge_count"] > 0
    assert isinstance(block["modularity_q"], float)
    assert block["modularity_reason"] is None


def test_a_single_package_has_no_front_door_ratio_not_a_vacuous_one(tmp_path):
    """No package boundary is crossed: the ratio is unknown, not 1.0 (issue #760)."""
    _pkg(tmp_path, "pkga", {"x": "from pkga import y\n", "y": "def f():\n    return 1\n",
                            "z": "from pkga.y import f\n"})
    block = sg.analyze_structure(tmp_path).as_dict()
    assert block["available"] is True and block["edge_count"] > 0
    assert block["cross_package_edges"] == 0
    assert "front_door_ratio" in block and block["front_door_ratio"] is None


def test_a_crossed_boundary_still_measures_the_front_door_ratio(tmp_path):
    _pkg(tmp_path, "pkga", {"x": "from pkgb import y\n", "z": "from pkga import x\n"})
    pkgb = _pkg(tmp_path, "pkgb", {"y": "def f():\n    return 1\n",
                                   "w": "from pkgb import y\n"})
    (pkgb / "__init__.py").write_text("from pkgb.y import f\n", encoding="utf-8")
    block = sg.analyze_structure(tmp_path).as_dict()
    # pkga.x reaches the pkgb.y submodule: a measured burrow, not a null.
    assert block["cross_package_edges"] > 0
    assert block["front_door_ratio"] == 0.0


def test_an_unavailable_block_emits_null_front_door_ratio(tmp_path):
    (tmp_path / "loose.py").write_text("x = 1\n", encoding="utf-8")
    block = sg.analyze_structure(tmp_path).as_dict()
    assert block["available"] is False and block["cross_package_edges"] == 0
    assert block["front_door_ratio"] is None


# --------------------------------------------------------------------------
# A1 -- comprehension footprint
# --------------------------------------------------------------------------

def test_footprint_components_sum_to_total():
    surfaces = {"A": 3, "B": 2}
    sizes = {"A": 100, "B": 40}
    fp = sg.compute_footprint("A", ["B"], surfaces, sizes, keyhole_budget=2000)
    assert fp["size"] == 100
    assert fp["dep_surface"] == 2          # public_surface(B)
    assert fp["exposed_surface"] == 3      # public_surface(A)
    assert fp["total"] == fp["size"] + fp["dep_surface"] + fp["exposed_surface"]
    assert fp["total"] == 105


def test_footprint_over_budget_flag():
    fp = sg.compute_footprint(
        "A", [], {"A": 0}, {"A": 5000}, keyhole_budget=2000,
    )
    assert fp["over_budget"] is True
    fp2 = sg.compute_footprint(
        "A", [], {"A": 0}, {"A": 10}, keyhole_budget=2000,
    )
    assert fp2["over_budget"] is False


def test_footprint_uses_direct_deps_only():
    # A -> B -> C. A's direct dep is only B. C's surface must NOT leak in.
    surfaces = {"A": 1, "B": 2, "C": 999}
    sizes = {"A": 10, "B": 10, "C": 10}
    fp = sg.compute_footprint("A", ["B"], surfaces, sizes, keyhole_budget=2000)
    assert fp["dep_surface"] == 2  # B only, not B + C
    # Changing C's surface leaves A's footprint untouched (transitive isolation).
    surfaces["C"] = 1
    fp_again = sg.compute_footprint("A", ["B"], surfaces, sizes, keyhole_budget=2000)
    assert fp_again["total"] == fp["total"]


# --------------------------------------------------------------------------
# A2 -- blob vs modular
# --------------------------------------------------------------------------

def test_cycle_is_one_scc():
    g = nx.DiGraph()
    g.add_edges_from([("A", "B"), ("B", "C"), ("C", "A")])
    sccs, q = sg.compute_modularity(g)
    assert len(sccs) == 1
    assert sccs[0] == ["A", "B", "C"]
    assert -0.5 <= q <= 1.0


def test_acyclic_has_no_multi_member_scc():
    g = nx.DiGraph()
    g.add_edges_from([("A", "B"), ("B", "C")])
    sccs, q = sg.compute_modularity(g)
    assert sccs == []
    assert -0.5 <= q <= 1.0


def test_separated_clusters_score_higher_than_dense_crosstalk():
    # Two tight triangles joined by a single bridge edge: clear modularity.
    separated = nx.DiGraph()
    separated.add_edges_from([
        ("a1", "a2"), ("a2", "a3"), ("a3", "a1"),
        ("b1", "b2"), ("b2", "b3"), ("b3", "b1"),
        ("a1", "b1"),
    ])
    _, q_sep = sg.compute_modularity(separated)

    # Everything wired to everything: near-zero / negative modularity.
    dense = nx.DiGraph()
    nodes = ["n1", "n2", "n3", "n4"]
    for u in nodes:
        for v in nodes:
            if u != v:
                dense.add_edge(u, v)
    _, q_dense = sg.compute_modularity(dense)

    assert q_sep > q_dense
    assert -0.5 <= q_sep <= 1.0
    assert -0.5 <= q_dense <= 1.0


# --------------------------------------------------------------------------
# A3 -- contracts (front door vs burrow)
# --------------------------------------------------------------------------

def _tops(g):
    """Python-style package boundaries: a module's top dotted component."""
    return {n: n.split(".", 1)[0] for n in g}


def test_all_imports_via_front_door_ratio_is_one():
    g = nx.DiGraph()
    # app imports the pkg package itself (its __init__ / public API).
    g.add_edge("app", "pkg")
    ratio, burrow = sg.compute_front_door_ratio(g, {"app", "pkg"}, _tops(g))
    assert ratio == 1.0
    assert burrow == []


def test_burrowing_lowers_ratio_and_records_edges():
    g = nx.DiGraph()
    g.add_edge("app", "pkg")            # front door
    g.add_edge("app", "pkg.internal")   # burrow into internals
    ratio, burrow = sg.compute_front_door_ratio(g, {"app", "pkg"}, _tops(g))
    assert ratio == 0.5  # 1 front / 2 total -- exactly representable
    assert burrow == [{"importer": "app", "imported": "pkg.internal"}]


def test_intra_package_edges_are_not_contract_edges():
    g = nx.DiGraph()
    g.add_edge("pkg.a", "pkg.b")  # same package -> internal cohesion, ignored
    ratio, burrow = sg.compute_front_door_ratio(g, {"pkg"}, _tops(g))
    assert ratio == 1.0
    assert burrow == []
    assert sg.count_cross_package_edges(g, _tops(g)) == 0   # the 1.0 above is vacuous


def test_cross_package_edges_counts_front_doors_and_burrows():
    g = nx.DiGraph()
    g.add_edge("app", "pkg")            # front door
    g.add_edge("app", "pkg.internal")   # burrow
    g.add_edge("pkg.a", "pkg.b")        # intra-package: not counted
    assert sg.count_cross_package_edges(g, _tops(g)) == 2


# --------------------------------------------------------------------------
# A4 -- breakup candidates
# --------------------------------------------------------------------------

def test_cohesive_package_is_not_a_breakup_candidate():
    # One dense cluster of 4 modules -> cohesive, no proposed split.
    g = nx.DiGraph()
    mods = ["p.a", "p.b", "p.c", "p.d"]
    for u in mods:
        for v in mods:
            if u != v:
                g.add_edge(u, v)
    assert sg.find_breakup_candidates("p", g) is None


def test_two_clusters_yield_a_candidate_with_two_cuts():
    g = nx.DiGraph()
    # Two tight triangles, one thin bridge -> two natural cut-lines.
    g.add_edges_from([
        ("p.a1", "p.a2"), ("p.a2", "p.a3"), ("p.a3", "p.a1"),
        ("p.b1", "p.b2"), ("p.b2", "p.b3"), ("p.b3", "p.b1"),
        ("p.a1", "p.b1"),
    ])
    candidate = sg.find_breakup_candidates("p", g)
    assert candidate is not None
    assert candidate["package"] == "p"
    assert candidate["num_clusters"] == 2
    assert len(candidate["clusters"]) == 2
    # Every module is assigned to exactly one cut-line.
    flat = [m for cluster in candidate["clusters"] for m in cluster]
    assert sorted(flat) == sorted(g.nodes())


def test_too_small_package_returns_none():
    g = nx.DiGraph()
    g.add_edges_from([("p.a", "p.b"), ("p.c", "p.a")])  # 3 nodes < threshold
    assert sg.find_breakup_candidates("p", g) is None


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

def test_keyhole_budget_default_when_no_config(tmp_path):
    from lib.assess_config import load_structure_config
    cfg = load_structure_config(tmp_path)
    assert cfg["keyhole_budget"] == DEFAULT_KEYHOLE_BUDGET


def test_keyhole_budget_read_from_config(tmp_path):
    from lib.assess_config import load_structure_config
    assess = tmp_path / ".assess"
    assess.mkdir()
    (assess / "config.toml").write_text(
        "[structure]\nkeyhole_budget = 1500\n"
    , encoding="utf-8")
    cfg = load_structure_config(tmp_path)
    assert cfg["keyhole_budget"] == 1500


def test_keyhole_budget_rejects_malformed_value(tmp_path):
    from lib.assess_config import load_structure_config
    assess = tmp_path / ".assess"
    assess.mkdir()
    # A non-positive / wrong-typed value falls back to the default.
    (assess / "config.toml").write_text(
        '[structure]\nkeyhole_budget = "lots"\n'
    , encoding="utf-8")
    cfg = load_structure_config(tmp_path)
    assert cfg["keyhole_budget"] == DEFAULT_KEYHOLE_BUDGET


# --------------------------------------------------------------------------
# Package discovery
# --------------------------------------------------------------------------

def test_discover_packages_finds_top_level_only(tmp_path):
    pkg = tmp_path / "pkg"
    sub = pkg / "sub"
    pkg.mkdir()
    sub.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (sub / "__init__.py").write_text("", encoding="utf-8")
    found = igp.discover_packages(tmp_path)
    assert found == [pkg.resolve()]  # sub is a subpackage, not top-level


def test_discover_packages_skips_excluded_dirs(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "__init__.py").write_text("", encoding="utf-8")
    venv_pkg = tmp_path / ".venv" / "junk"
    venv_pkg.mkdir(parents=True)
    (venv_pkg / "__init__.py").write_text("", encoding="utf-8")
    found = igp.discover_packages(tmp_path)
    assert found == [real.resolve()]


# --------------------------------------------------------------------------
# Integration -- analyse this package itself
# --------------------------------------------------------------------------

def test_analyze_structure_on_lib_itself():
    from pathlib import Path
    lib_dir = Path(__file__).resolve().parent.parent / "scripts" / "lib"
    result = sg.analyze_structure(lib_dir)
    assert result.available is True
    assert result.reason == ""
    assert result.module_count > 0

    modules = {fp["module"] for fp in result.footprints}
    assert "lib.graph.structure_graph" in modules
    assert "lib.assess_config" in modules

    # Q is computed and in range; result round-trips to JSON-friendly dict.
    assert -0.5 <= result.modularity_q <= 1.0
    d = result.as_dict()
    assert d["module_count"] == result.module_count
    assert isinstance(d["footprints"], list)
    # Every footprint exposes the expected arithmetic shape.
    sample = result.footprints[0]
    assert sample["total"] == (
        sample["size"] + sample["dep_surface"] + sample["exposed_surface"]
    )


# --------------------------------------------------------------------------
# Resolution by path: the graph describes the assessed tree, never whatever
# copy of a package name the running interpreter already imported.
# --------------------------------------------------------------------------

def _pkg(root, name, modules):
    """Write package ``root/name`` with ``modules`` ({stem: source})."""
    pkg = root / name
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    for stem, src in modules.items():
        (pkg / f"{stem}.py").write_text(src, encoding="utf-8")
    return pkg


def _module_names(result):
    return {fp["module"] for fp in result.footprints}


def test_target_lib_package_is_graphed_not_the_running_assess_lib(tmp_path):
    # The test process has imported /assess's own top-level ``lib`` (see the
    # import at the top of this file), exactly as assess_core.py does.
    import sys
    assert "lib" in sys.modules
    _pkg(tmp_path, "lib", {
        "alpha": "X = 1\n",
        "beta": "from lib import alpha\n",
    })
    result = sg.analyze_structure(tmp_path)
    assert result.available is True, result.reason
    assert _module_names(result) == {"lib", "lib.alpha", "lib.beta"}
    assert result.module_count == 3
    assert result.edge_count == 1
    assert "lib.graph.structure_graph" not in _module_names(result)


def test_package_named_like_an_imported_module_is_graphed(tmp_path):
    # ``json`` (stdlib) and ``networkx`` (this module's own dependency) are
    # both in sys.modules; a target package of the same name must still be
    # read from the target tree.
    _pkg(tmp_path, "json", {"target_only": "Y = 2\n"})
    _pkg(tmp_path, "networkx", {"also_target": "from json import target_only\n"})
    result = sg.analyze_structure(tmp_path)
    assert result.available is True, result.reason
    assert _module_names(result) == {
        "json", "json.target_only", "networkx", "networkx.also_target",
    }
    assert result.edge_count == 1


def test_scan_leaves_interpreter_import_state_untouched(tmp_path):
    import sys

    from grimp.application.config import settings

    _pkg(tmp_path, "lib", {"alpha": "X = 1\n"})
    path_before = list(sys.path)
    lib_before = sys.modules["lib"]
    finder_before = settings.PACKAGE_FINDER
    sg.analyze_structure(tmp_path)
    assert sys.path == path_before
    assert sys.modules["lib"] is lib_before
    assert settings.PACKAGE_FINDER is finder_before


def test_package_finder_restored_when_grimp_raises(tmp_path, monkeypatch):
    from grimp.application.config import settings

    finder_before = settings.PACKAGE_FINDER

    def boom(*_args, **_kwargs):
        raise RuntimeError("parse failure")

    monkeypatch.setattr(igp.grimp, "build_graph", boom)
    pkg = _pkg(tmp_path, "pkg", {"a": ""})
    with pytest.raises(RuntimeError, match="parse failure"):
        igp.build_import_map([pkg], tmp_path)
    assert settings.PACKAGE_FINDER is finder_before


def test_same_named_packages_are_graphed_separately_and_disclosed(tmp_path):
    # Two roots each holding a ``lib``: one grimp graph cannot hold both, so
    # each copy is graphed under its repo-relative path, and the collision is
    # disclosed rather than one copy silently winning.
    _pkg(tmp_path / "a", "lib", {"x": "from lib import y\n", "y": ""})
    _pkg(tmp_path / "a", "app", {"main": "from lib import x\n"})
    _pkg(tmp_path / "b", "lib", {"z": ""})
    result = sg.analyze_structure(tmp_path)
    assert result.available is True, result.reason
    assert _module_names(result) == {
        "a/lib", "a/lib.x", "a/lib.y", "app", "app.main", "b/lib", "b/lib.z",
    }
    # Same-root imports still resolve: app.main -> a's lib, lib.x -> lib.y.
    assert result.edge_count == 2
    assert result.name_collisions == [{"name": "lib", "paths": ["a/lib", "b/lib"]}]
    sizes = {fp["module"]: fp["size"] for fp in result.footprints}
    assert sizes["a/lib.x"] == 1  # resolved back to a/lib/x.py, not b's tree
    d = result.as_dict()
    assert d["name_collisions"] == result.name_collisions


def test_qualified_name_escapes_dots_in_the_path(tmp_path):
    _pkg(tmp_path / "v1.2", "lib", {"m": "X = 1\n"})
    _pkg(tmp_path / "v2", "lib", {"n": ""})
    result = sg.analyze_structure(tmp_path)
    names = _module_names(result)
    assert "v1%2E2/lib.m" in names
    assert "v2/lib.n" in names
    sizes = {fp["module"]: fp["size"] for fp in result.footprints}
    assert sizes["v1%2E2/lib.m"] == 1  # mapped back to v1.2/lib/m.py


def test_path_finder_serves_both_grimp_port_shapes(tmp_path):
    # grimp 3.0-3.13 call determine_package_directory (one path); 3.14+ call
    # determine_package_directories (a set). Both must answer from the path.
    finder = igp._PathPackageFinder({"lib": tmp_path / "lib"})
    assert finder.determine_package_directory("lib", None) == str(tmp_path / "lib")
    assert finder.determine_package_directories("lib", None) == {
        str(tmp_path / "lib"),
    }
    with pytest.raises(ValueError):
        finder.determine_package_directory("json", None)


def test_non_identifier_package_dir_does_not_sink_the_scan(tmp_path):
    # ``my-pkg`` carries an __init__.py but is not importable by any name;
    # it must not knock out the whole structure block.
    _pkg(tmp_path, "my-pkg", {"a": ""})
    _pkg(tmp_path, "class", {"b": ""})  # keyword: not importable either
    _pkg(tmp_path, "real", {"c": ""})
    result = sg.analyze_structure(tmp_path)
    assert result.available is True, result.reason
    assert _module_names(result) == {"real", "real.c"}


def test_non_colliding_tree_has_no_collisions_and_plain_names(tmp_path):
    _pkg(tmp_path / "src", "alpha", {"one": "from beta import two\n"})
    _pkg(tmp_path / "src", "beta", {"two": ""})
    result = sg.analyze_structure(tmp_path)
    assert result.name_collisions == []
    assert _module_names(result) == {"alpha", "alpha.one", "beta", "beta.two"}
    assert result.edge_count == 1


def test_cross_build_import_of_a_unique_name_is_dropped(tmp_path):
    # src/ and tools/ both hold ``lib``, forcing two builds. tools/runner
    # imports the unique ``core`` from src/: grimp sees it as external to the
    # tools build, so the edge is dropped (documented in the module docstring).
    _pkg(tmp_path / "src", "lib", {"a": ""})
    _pkg(tmp_path / "src", "core", {"c": ""})
    _pkg(tmp_path / "tools", "lib", {"b": ""})
    _pkg(tmp_path / "tools", "runner", {"r": "from core import c\n"})
    result = sg.analyze_structure(tmp_path)
    assert {"core.c", "runner.r"} <= _module_names(result)
    assert result.edge_count == 0
    assert result.name_collisions == [
        {"name": "lib", "paths": ["src/lib", "tools/lib"]},
    ]


def test_third_root_import_of_a_colliding_name_is_not_pinned_to_one_copy(
    tmp_path,
):
    # a/lib and b/lib collide; c/app imports ``lib``. Which copy that reaches
    # depends on runtime sys.path, so the edge must be dropped, not resolved to
    # a/lib because "a" sorts first.
    _pkg(tmp_path / "a", "lib", {"x": ""})
    _pkg(tmp_path / "b", "lib", {"y": ""})
    _pkg(tmp_path / "c", "app", {"main": "from lib import x\nimport lib.y\n"})
    result = sg.analyze_structure(tmp_path)
    assert "app.main" in _module_names(result)
    assert result.edge_count == 0


def test_partition_isolates_colliding_roots_and_shares_the_rest(tmp_path):
    dirs = [
        tmp_path / "a" / "lib", tmp_path / "b" / "lib",
        tmp_path / "c" / "app", tmp_path / "d" / "core",
    ]
    builds = igp._partition_builds(dirs)
    assert builds == [
        [tmp_path / "c" / "app", tmp_path / "d" / "core"],
        [tmp_path / "a" / "lib"],
        [tmp_path / "b" / "lib"],
    ]


def test_concurrent_builds_each_graph_through_their_own_finder(tmp_path, monkeypatch):
    """#1382: the unit pass runs structure scans at once; grimp's finder is process-wide, so a
    build swapping it in while another is graphing would graph the wrong tree. The lock holds
    each swap and its build: a build that starts while another holds it waits, and each tree's
    map is its own."""
    import threading

    trees = []
    for name in ("one", "two"):
        root = tmp_path / name
        trees.append((_pkg(root, "pkg", {f"only_{name}": ""}), root))
    real = igp.grimp.build_graph
    inside = threading.Event()
    overlap: list[bool] = []

    def slow(*args, **kwargs):
        overlap.append(inside.is_set())
        inside.set()
        try:
            threading.Event().wait(0.05)
            return real(*args, **kwargs)
        finally:
            inside.clear()

    monkeypatch.setattr(igp.grimp, "build_graph", slow)
    results: dict[str, set[str]] = {}

    def build(pkg, root):
        results[root.name] = set(igp.build_import_map([pkg], root).imports)

    threads = [threading.Thread(target=build, args=tree) for tree in trees]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert overlap == [False, False]
    assert results == {"one": {"pkg", "pkg.only_one"}, "two": {"pkg", "pkg.only_two"}}
