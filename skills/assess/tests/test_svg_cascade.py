"""The heatmap stylesheet must not override any <text> presentation attribute.

Lives apart from test_complexity_treemap.py, which sits at its file-size
ceiling; the cascade check itself is in svg_cascade.py.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from svg_cascade import overridden_text_attributes
from test_complexity_treemap import _load_treemap


@pytest.fixture(scope="module")
def render_lib():
    """lib.treemap_render, imported with numpy stubbed the way
    test_complexity_treemap stubs it, so the two modules share one stub."""
    _load_treemap()
    return importlib.import_module("lib.treemap_render")


def test_write_svg_stylesheet_never_overrides_text_attributes(render_lib, tmp_path):
    """A stylesheet rule beats an SVG presentation attribute, so a bare
    `text { text-anchor: middle }` centred the left-anchored survivor legend on
    x=14 and cut its left half off. Every <text> attribute (the legend's
    text-anchor="start", the grey label lines' fill) must survive the cascade."""
    node = render_lib.Node(name="a.py", rel_path="a.py", loc=100,
                           metric=5.0, color=(0.8, 0.2, 0.1, 1.0),
                           is_file=True, hatch="diag")
    rects = [(0.0, 0.0, 1600.0, 1000.0, node)]
    out = tmp_path / "cascade.svg"
    render_lib.write_svg(rects, Path("/repo"), 1600.0, 1000.0, out,
                         True, "ccn", show_survivor_legend=True)
    svg = out.read_text()
    assert 'text-anchor="start"' in svg  # the legend is in the render
    assert 'fill="#444"' in svg  # so are the grey label lines
    assert overridden_text_attributes(svg) == []


def test_svg_cascade_check_catches_a_bare_text_rule():
    """The check itself fails on the pre-fix stylesheet shape."""
    svg = ('<svg xmlns="http://www.w3.org/2000/svg"><style>text { fill: #1a1a1a; '
           'text-anchor: middle; }</style><text x="14" text-anchor="start">'
           'legend</text><text>label</text></svg>')
    assert overridden_text_attributes(svg) == [("legend", "text-anchor")]
