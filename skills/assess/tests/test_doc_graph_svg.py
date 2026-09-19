"""The doc-graph SVG honours the same excludes as the scorer (issue #177).

`doc-graph-svg.py` is a CLI wrapper that imports matplotlib/numpy at load, so
those are stubbed before import (same approach as test_complexity_treemap). We
then drive `main()` with `build_doc_graph` / `analyze_doc_staleness` / `render`
patched to capture the excludes they receive, proving the SVG path resolves
`.assess/config.toml` + `--exclude` and threads the union into both scans - so
the SVG and `lib.doc_graph` compute over the identical doc set.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "doc-graph-svg.py"


def _load_svg():
    for name in ("matplotlib", "matplotlib.pyplot", "numpy"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["matplotlib"].pyplot = sys.modules["matplotlib.pyplot"]
    spec = importlib.util.spec_from_file_location("doc_graph_svg", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def svg():
    return _load_svg()


class _FakeResult:
    available = True
    doc_count = 1
    graph = object()
    doc_to_code_edges: list = []
    entry_points: list = []
    unreachable: list = []
    orphans: list = []
    pagerank: dict = {}
    broken_links: list = []
    island_count = 0
    orphan_rate = 0.0
    reachability_pct = 0.0


def test_svg_threads_config_and_cli_excludes(svg, tmp_path, monkeypatch, capsys):
    # A repo with a durable config exclude plus an ad-hoc CLI exclude.
    (tmp_path / ".assess").mkdir()
    (tmp_path / ".assess" / "config.toml").write_text(
        'exclude_dirs = ["_archive"]\nexclude_patterns = ["*.csv"]\n',
        encoding="utf-8",
    )

    captured: dict = {}

    def fake_build(root, *, extra_exclude_dirs=None, extra_exclude_patterns=None):
        captured["graph_dirs"] = extra_exclude_dirs
        captured["graph_patterns"] = extra_exclude_patterns
        return _FakeResult()

    def fake_staleness(root, *, doc_to_code_edges=None,
                       extra_exclude_dirs=None, extra_exclude_patterns=None):
        captured["stale_dirs"] = extra_exclude_dirs
        captured["stale_patterns"] = extra_exclude_patterns
        return {"docs": []}

    monkeypatch.setattr(svg, "build_doc_graph", fake_build)
    monkeypatch.setattr(svg, "analyze_doc_staleness", fake_staleness)
    monkeypatch.setattr(svg, "render", lambda *a, **k: None)
    monkeypatch.setattr(
        sys, "argv",
        ["doc-graph-svg.py", str(tmp_path), "-o", str(tmp_path / "out.svg"),
         "--exclude", "_jira", "--exclude", "*.tmp"],
    )

    assert svg.main() == 0

    # config dir + CLI dir merged; config glob + CLI glob merged.
    assert captured["graph_dirs"] == {"_archive", "_jira"}
    assert captured["graph_patterns"] == ["*.csv", "*.tmp"]
    # The staleness scan (drives the SVG's colour) gets the identical excludes,
    # so colour and structure speak about the same doc set.
    assert captured["stale_dirs"] == captured["graph_dirs"]
    assert captured["stale_patterns"] == captured["graph_patterns"]


def _render_two_kinds(svg, tmp_path, monkeypatch, colour: str) -> list:
    """Render one link edge and one reference edge; return the parsed elements.

    numpy and matplotlib are stubbed here, so the colour map and the radial
    layout (``nx.shell_layout`` needs numpy) are replaced with fixed stand-ins;
    the edge and legend markup under test is the real code."""
    import xml.etree.ElementTree as ET

    import networkx as nx

    graph = nx.DiGraph()
    graph.add_edge("CLAUDE.md", "docs/linked.md", kind="link")
    graph.add_edge("CLAUDE.md", "docs/ref.md", kind="reference")
    result = _FakeResult()
    result.graph = graph
    result.entry_points = ["CLAUDE.md"]
    result.pagerank = {}
    fixed = {"CLAUDE.md": (500.0, 500.0), "docs/linked.md": (300.0, 300.0),
             "docs/ref.md": (700.0, 300.0)}
    monkeypatch.setattr(svg, "_radial_positions", lambda *a, **k: dict(fixed))
    monkeypatch.setattr(svg.plt, "get_cmap", lambda _name: (lambda _v: (1.0, 1.0, 1.0, 1.0)),
                        raising=False)
    out = tmp_path / "out.svg"
    svg.render(result, out, tmp_path, colour=colour)
    return list(ET.parse(out).iter())


_STYLE_KEYS = ("stroke", "stroke-dasharray", "stroke-width", "opacity")


@pytest.mark.parametrize("colour", ["staleness", "status"])
def test_reference_edge_drawn_distinct_from_link_with_legend(svg, tmp_path, monkeypatch, colour):
    """A reference edge (a backticked doc path) and a link edge render in two
    styles told apart by presentation attributes, and the legend names both.
    The reference dash must not reuse the ghost tether (4,3) or the orphan and
    ghost rings (3,2), which already carry meaning."""
    els = _render_two_kinds(svg, tmp_path, monkeypatch, colour)
    styles = {
        kind: [[e.get(k) for k in _STYLE_KEYS] for e in els if e.get("data-edge-kind") == kind]
        for kind in ("link", "reference")
    }
    assert len(styles["link"]) == 1
    assert len(styles["reference"]) == 1
    assert styles["link"][0] != styles["reference"][0]
    assert styles["reference"][0][1] not in ("4,3", "3,2")
    # Round caps add half the stroke width to both ends of a dash, so the painted
    # bead is dash + width and the painted gap is gap - width. That ratio is
    # scale-invariant, so this assertion proves the caps can never swallow the
    # gap at any scale - not that the dots stay visually distinct once rendered
    # small (at README embed scale both fall under a pixel and only the weight
    # and opacity difference actually survives).
    dash, gap = (float(v) for v in styles["reference"][0][1].split(","))
    width = float(styles["reference"][0][2])
    assert gap - width >= dash + width
    legend = sorted({e.get("data-legend-kind") for e in els if e.get("data-legend-kind")})
    assert legend == ["link", "reference"]
    # Legend samples are drawn in the same style as the edges they explain.
    for kind in ("link", "reference"):
        sample = next(e for e in els if e.get("data-legend-kind") == kind)
        assert [sample.get(k) for k in _STYLE_KEYS] == styles[kind][0]
    texts = [" ".join(e.itertext()).lower() for e in els if e.tag.endswith("text")]
    assert any("reference" in t for t in texts)
    assert any("link" in t for t in texts)


@pytest.mark.parametrize("colour", ["staleness", "status"])
def test_missing_or_unknown_edge_kind_draws_as_link(svg, tmp_path, monkeypatch, colour):
    """An edge with no `kind`, or one that names a kind the SVG doesn't know,
    normalizes to a link - both in styling and in the `data-edge-kind`
    attribute. `_edge_attrs` and the `data-edge-kind` markup both delegate to
    `_normalize_edge_kind`, so the two sites cannot diverge on this rule."""
    import xml.etree.ElementTree as ET

    import networkx as nx

    graph = nx.DiGraph()
    graph.add_edge("CLAUDE.md", "docs/nokind.md")
    graph.add_edge("CLAUDE.md", "docs/weird.md", kind="footnote")
    result = _FakeResult()
    result.graph = graph
    result.entry_points = ["CLAUDE.md"]
    result.pagerank = {}
    fixed = {"CLAUDE.md": (500.0, 500.0), "docs/nokind.md": (300.0, 300.0),
             "docs/weird.md": (700.0, 300.0)}
    monkeypatch.setattr(svg, "_radial_positions", lambda *a, **k: dict(fixed))
    monkeypatch.setattr(svg.plt, "get_cmap", lambda _name: (lambda _v: (1.0, 1.0, 1.0, 1.0)),
                        raising=False)
    out = tmp_path / "out.svg"
    svg.render(result, out, tmp_path, colour=colour)
    els = list(ET.parse(out).iter())
    edges = [e for e in els if e.get("data-edge-kind")]
    assert len(edges) == 2
    link_style = [svg._EDGE_STYLE["link"][k] for k in _STYLE_KEYS]
    for edge in edges:
        assert edge.get("data-edge-kind") == "link"
        assert [edge.get(k) for k in _STYLE_KEYS] == link_style


def test_normalize_edge_kind(svg):
    assert svg._normalize_edge_kind("link") == "link"
    assert svg._normalize_edge_kind("reference") == "reference"
    assert svg._normalize_edge_kind("footnote") == "link"
    assert svg._normalize_edge_kind("") == "link"
