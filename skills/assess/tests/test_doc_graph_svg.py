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
        'exclude_dirs = ["_archive"]\nexclude_patterns = ["*.csv"]\n'
        'working_notes_dirs = ["journal"]\nworking_notes_ignore = ["docs/chapters"]\n',
        encoding="utf-8",
    )

    captured: dict = {}

    def fake_build(root, *, extra_exclude_dirs=None, extra_exclude_patterns=None,
                   working_notes_dirs=None, working_notes_ignore=None):
        captured["graph_dirs"] = extra_exclude_dirs
        captured["graph_patterns"] = extra_exclude_patterns
        captured["notes"] = (working_notes_dirs, working_notes_ignore)
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
    # The working-notes overrides reach the graph, as in assess_core.
    assert captured["notes"] == (["journal"], ["docs/chapters"])
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


# --- Characterization: node styling, layouts, labels and CLI error paths ----
# These pin the branches of `render` and `main` so splitting them into named
# steps cannot change the markup.


def _render_nodes(svg, tmp_path, monkeypatch, **kwargs):
    """Render entry -> a, plus orphan `lone.md`, with fixed radial positions."""
    import xml.etree.ElementTree as ET

    import networkx as nx

    graph = nx.DiGraph()
    graph.add_edge("CLAUDE.md", "docs/a.md", kind="link")
    graph.add_node("docs/lone.md")
    result = _FakeResult()
    result.graph = graph
    result.entry_points = ["CLAUDE.md"]
    result.orphans = ["docs/lone.md"]
    result.unreachable = ["docs/lone.md"]
    result.pagerank = {}
    fixed = {"CLAUDE.md": (500.0, 500.0), "docs/a.md": (300.0, 300.0),
             "docs/lone.md": (800.0, 800.0)}
    monkeypatch.setattr(svg, "_radial_positions", lambda *a, **k: dict(fixed))
    monkeypatch.setattr(svg.plt, "get_cmap", lambda _name: (lambda _v: (1.0, 1.0, 1.0, 1.0)),
                        raising=False)
    # numpy is stubbed, so the percentile cap falls back to the plain maximum.
    monkeypatch.setattr(svg, "adaptive_cap", lambda v: (max(v, default=0.0), "max"))
    out = tmp_path / "out.svg"
    svg.render(result, out, tmp_path, **kwargs)
    els = list(ET.parse(out).iter())
    circles = {}
    for e in els:
        if e.tag.endswith("circle"):
            title = next((c.text for c in e if c.tag.endswith("title")), None)
            if title:
                circles[title.split("\n")[0]] = (e, title)
    texts = [e.text for e in els if e.tag.endswith("text")]
    return circles, texts, out.read_text(encoding="utf-8")


def test_staleness_mode_marks_entry_and_orphan_by_stroke(svg, tmp_path, monkeypatch):
    staleness = {"CLAUDE.md": {"last_commit_days": 10, "code_churn_in_window": 2}}
    circles, _, _ = _render_nodes(svg, tmp_path, monkeypatch, staleness=staleness)
    entry, entry_tip = circles["CLAUDE.md"]
    assert (entry.get("stroke"), entry.get("stroke-width")) == (svg.ENTRY_RING, "3.5")
    assert "10d stale, subject churn 2" in entry_tip
    orphan, orphan_tip = circles["docs/lone.md"]
    assert orphan.get("stroke") == svg.ORPHAN_RING
    assert orphan.get("stroke-dasharray") == "3,2"
    # Measured staleness exists for some docs, so an unmeasured doc is hatched.
    assert orphan.get("fill") == svg.UNMEASURED_FILL
    assert "staleness not measured" in orphan_tip
    plain, plain_tip = circles["docs/a.md"]
    assert (plain.get("stroke"), plain.get("stroke-width")) == ("#b8b8b8", "1.0")
    assert "1 line · in 1 · out 0 · reachable" in plain_tip


def test_status_mode_fills_by_status_without_orphan_ring(svg, tmp_path, monkeypatch):
    circles, _, _ = _render_nodes(svg, tmp_path, monkeypatch, colour="status")
    assert circles["CLAUDE.md"][0].get("fill") == svg.COLOR_ENTRY
    orphan, tip = circles["docs/lone.md"]
    assert orphan.get("fill") == svg.COLOR_ORPHAN
    assert orphan.get("stroke") == "#b8b8b8"
    assert orphan.get("stroke-dasharray") is None
    assert "stale" not in tip


def test_labels_and_centrality_size(svg, tmp_path, monkeypatch):
    circles, texts, _ = _render_nodes(svg, tmp_path, monkeypatch, show_labels=True,
                                      size_mode="centrality")
    # Labels only for the entry and linked docs, never the orphan.
    assert "CLAUDE.md" in texts and "a.md" in texts
    assert "lone.md" not in texts
    assert "centrality 0.333" in circles["docs/a.md"][1]
    assert any("size = link-graph centrality" in (t or "") for t in texts)


def test_no_labels_by_default(svg, tmp_path, monkeypatch):
    _, texts, _ = _render_nodes(svg, tmp_path, monkeypatch)
    assert "a.md" not in texts


def test_web_layout_bins_isolated_docs(svg, tmp_path, monkeypatch):
    """With no links at all the web layout skips the force layout and grids every
    doc into the labelled isolated-docs bin."""
    import xml.etree.ElementTree as ET

    import networkx as nx

    graph = nx.DiGraph()
    graph.add_nodes_from(["a.md", "b.md"])
    result = _FakeResult()
    result.graph = graph
    result.pagerank = {}
    monkeypatch.setattr(svg.plt, "get_cmap", lambda _name: (lambda _v: (1.0, 1.0, 1.0, 1.0)),
                        raising=False)
    out = tmp_path / "out.svg"
    svg.render(result, out, tmp_path, layout="web")
    root = ET.parse(out).getroot()
    assert root.get("viewBox") == "0 0 1600 1000"
    texts = [e.text or "" for e in root.iter() if e.tag.endswith("text")]
    assert any(t.startswith("2 isolated docs") for t in texts)
    assert any("loose dots = orphans" in t for t in texts)


def test_render_prints_summary(svg, tmp_path, monkeypatch, capsys):
    _render_nodes(svg, tmp_path, monkeypatch)
    out = capsys.readouterr().out
    assert "(3 docs, 1 edge, 0 islands)" in out
    assert "entries=['CLAUDE.md']" in out


def test_main_rejects_non_directory(svg, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["doc-graph-svg.py", str(tmp_path / "missing")])
    assert svg.main() == 1
    assert "is not a directory" in capsys.readouterr().err


@pytest.mark.parametrize("available, doc_count, message", [
    (False, 1, "doc graph unavailable - boom"),
    (True, 0, "no docs to graph"),
])
def test_main_reports_unusable_graph(svg, tmp_path, monkeypatch, capsys,
                                     available, doc_count, message):
    result = _FakeResult()
    result.available = available
    result.doc_count = doc_count
    result.reason = "boom"
    monkeypatch.setattr(svg, "build_doc_graph", lambda *a, **k: result)
    monkeypatch.setattr(sys, "argv", ["doc-graph-svg.py", str(tmp_path)])
    assert svg.main() == 1
    assert message in capsys.readouterr().err


def _counts_result(n_edges: int, islands: int, broken: list | None = None):
    import networkx as nx

    graph = nx.DiGraph()
    for i in range(n_edges):
        graph.add_edge(f"a{i}.md", f"b{i}.md")
    result = _FakeResult()
    result.graph = graph
    result.island_count = islands
    result.broken_links = broken or []
    return result


@pytest.mark.parametrize("n, edges, islands, expected", [
    (1, 1, 1, "1 doc, 1 edge, 1 island"),
    (9, 0, 9, "9 docs, 0 edges, 9 islands"),
    (3, 2, 0, "3 docs, 2 edges, 0 islands"),
])
def test_graph_counts_pluralise_each_noun(svg, n, edges, islands, expected):
    """The title and stdout summary share one count phrase; each noun agrees
    with its own count ('1 island', not '1 islands')."""
    assert svg._graph_counts(_counts_result(edges, islands), n) == expected


def test_title_uses_hyphen_and_singular_counts(svg):
    title = svg._title(_counts_result(1, 1), 1, 1000.0)
    assert "Doc map - 1 doc, 1 edge, 1 island</text>" in title
    assert "—" not in title


@pytest.mark.parametrize("broken, clause", [
    ([{"from": "a.md", "target": "x.md"}], " · 1 broken link</text>"),
    ([{"from": "a.md", "target": "x.md"}, {"from": "b.md", "target": "x.md"}],
     " · 2 broken links to 1 missing file</text>"),
])
def test_title_pluralises_broken_link_clause(svg, broken, clause):
    assert clause in svg._title(_counts_result(0, 0, broken), 2, 1000.0)


@pytest.mark.parametrize("count, text", [
    (1, "1 isolated doc - no link in or out"),
    (2, "2 isolated docs - no link in or out"),
])
def test_isolated_panel_label(svg, count, text):
    panel = "".join(svg._isolated_panel(count, 100.0, 1600.0, 1000.0))
    assert text in panel
    assert "—" not in panel


def test_rendered_svg_has_no_em_dash(svg, tmp_path, monkeypatch):
    """No generated text in the doc map carries an em dash."""
    _render_nodes(svg, tmp_path, monkeypatch)
    assert "—" not in (tmp_path / "out.svg").read_text(encoding="utf-8")


@pytest.mark.parametrize("size, text", [(0.6, "1 line"), (1.4, "1 line"), (2.0, "2 lines")])
def test_size_text_agrees_with_the_displayed_line_count(svg, size, text):
    """A fractional size shown as "1" reads "1 line", not "1 lines"."""
    from types import SimpleNamespace

    painter = SimpleNamespace(sizes={"a.md": size}, size_mode="lines")
    assert svg._NodePainter.size_text(painter, "a.md") == text
