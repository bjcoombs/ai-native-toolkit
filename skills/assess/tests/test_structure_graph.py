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

from lib import structure_graph as sg
from lib.assess_config import DEFAULT_KEYHOLE_BUDGET


# --------------------------------------------------------------------------
# Graceful degradation
# --------------------------------------------------------------------------

def test_degrades_when_grimp_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(sg, "_GRIMP_AVAILABLE", False)
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


def test_no_packages_is_available_but_empty(tmp_path):
    (tmp_path / "loose.py").write_text("x = 1\n")  # no __init__.py anywhere
    result = sg.analyze_structure(tmp_path)
    assert result.available is True
    assert result.footprints == []
    assert "no importable" in result.reason


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

def test_all_imports_via_front_door_ratio_is_one():
    g = nx.DiGraph()
    # app imports the pkg package itself (its __init__ / public API).
    g.add_edge("app", "pkg")
    ratio, burrow = sg.compute_front_door_ratio(g, packages={"app", "pkg"})
    assert ratio == 1.0
    assert burrow == []


def test_burrowing_lowers_ratio_and_records_edges():
    g = nx.DiGraph()
    g.add_edge("app", "pkg")            # front door
    g.add_edge("app", "pkg.internal")   # burrow into internals
    ratio, burrow = sg.compute_front_door_ratio(g, packages={"app", "pkg"})
    assert ratio == 0.5  # 1 front / 2 total -- exactly representable
    assert burrow == [{"importer": "app", "imported": "pkg.internal"}]


def test_intra_package_edges_are_not_contract_edges():
    g = nx.DiGraph()
    g.add_edge("pkg.a", "pkg.b")  # same package -> internal cohesion, ignored
    ratio, burrow = sg.compute_front_door_ratio(g, packages={"pkg"})
    assert ratio == 1.0
    assert burrow == []


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
    )
    cfg = load_structure_config(tmp_path)
    assert cfg["keyhole_budget"] == 1500


def test_keyhole_budget_rejects_malformed_value(tmp_path):
    from lib.assess_config import load_structure_config
    assess = tmp_path / ".assess"
    assess.mkdir()
    # A non-positive / wrong-typed value falls back to the default.
    (assess / "config.toml").write_text(
        '[structure]\nkeyhole_budget = "lots"\n'
    )
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
    (pkg / "__init__.py").write_text("")
    (sub / "__init__.py").write_text("")
    found = sg.discover_packages(tmp_path)
    assert found == [pkg.resolve()]  # sub is a subpackage, not top-level


def test_discover_packages_skips_excluded_dirs(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "__init__.py").write_text("")
    venv_pkg = tmp_path / ".venv" / "junk"
    venv_pkg.mkdir(parents=True)
    (venv_pkg / "__init__.py").write_text("")
    found = sg.discover_packages(tmp_path)
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
    assert "lib.structure_graph" in modules
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
    (pkg / "__init__.py").write_text("")
    for stem, src in modules.items():
        (pkg / f"{stem}.py").write_text(src)
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
    assert "lib.structure_graph" not in _module_names(result)


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

    monkeypatch.setattr(sg.grimp, "build_graph", boom)
    pkg = _pkg(tmp_path, "pkg", {"a": ""})
    try:
        sg.build_import_map([pkg], tmp_path)
    except RuntimeError:
        pass
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
    _pkg(tmp_path / "v1.2", "lib", {"m": ""})
    _pkg(tmp_path / "v2", "lib", {"n": ""})
    result = sg.analyze_structure(tmp_path)
    names = _module_names(result)
    assert "v1%2E2/lib.m" in names
    assert "v2/lib.n" in names
    sizes = {fp["module"]: fp["size"] for fp in result.footprints}
    assert sizes["v1%2E2/lib.m"] == 0  # empty file, but resolved (no crash)


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
