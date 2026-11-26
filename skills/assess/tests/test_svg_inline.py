"""``lib/svg_inline.py``: a diagram SVG made safe to inline, or None to keep it an image (#1362).

Static markup only, no DOM: each case feeds a string and reads the string back.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lib.svg_inline import css_selectors, inline_svg, root_class, sanitise

FIXTURES = Path(__file__).resolve().parent / "fixtures"
HEAD = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4"><title>root</title>'
PART = '<rect width="1" height="1"><title>part</title></rect>'


def _svg(body: str, head: str = HEAD) -> str:
    return f"{head}{body}</svg>"


def test_a_generator_scoped_svg_with_details_is_inlined_as_drawn_without_its_prolog() -> None:
    drawn = (FIXTURES / "fleet_step_diagrams/expected-step-funnel.svg").read_text(encoding="utf-8")
    out = inline_svg(drawn, "unused")
    assert out == drawn.split("?>", 1)[1].strip()
    assert root_class(out) == "fleet-step-funnel"


@pytest.mark.parametrize("golden", ["fleet_step_diagrams/expected-step-movement.svg",
                                    "assess_diagrams/expected-step-ladder.svg"])
def test_a_diagram_with_only_a_root_title_stays_an_image(golden: str) -> None:
    assert inline_svg((FIXTURES / golden).read_text(encoding="utf-8"), "x") is None


def test_an_unscoped_svg_is_scoped_here() -> None:
    out = inline_svg(_svg('<style>rect {fill:red}</style><g id="a">' + PART + "</g>"), "fleet-page-x")
    assert out is not None and root_class(out) == "fleet-page-x"
    assert 'id="fleet-page-x-a"' in out and "svg.fleet-page-x rect" in out


@pytest.mark.parametrize("body", [
    '<script type="text/javascript">alert(1)</script>',
    "<script/>",
    "<foreignObject><foreignObject>x</foreignObject><p>y</p></foreignObject>",
    '<a href="java&#x09;script:alert(1)"><text>t</text></a>',
    '<a xlink:href=" JAVASCRIPT:alert(1)"><text>t</text></a>',
    "<rect onmouseover='alert(1)' ONCLICK=alert(1) width=\"1\"/>",
    '<set attributeName="href" to="vbscript:x"/>'.replace("set", "rect"),
    "<!-- --!><img src=x onerror=alert(1)> -->",
    '<use href="https://example.com/x.svg#p"/><use xlink:href="//example.com/x.svg"/>',
    '<rect style="fill:url(http://example.com/p)" fill="URL( \'https://example.com/q\')" width="1"/>',
])
def test_unsafe_markup_is_dropped(body: str) -> None:
    out = inline_svg(_svg(body + PART), "s")
    assert out is not None
    lowered = out.lower()
    for word in ("<script", "foreignobject", "javascript", "vbscript", "alert", "onclick", "onmouseover", "<img",
                 "example.com"):
        assert word not in lowered


@pytest.mark.parametrize("svg", [
    _svg("<img src=x>" + PART),
    _svg("<p>text</p>" + PART),
    _svg("<animate/>" + PART),
    _svg("<![CDATA[x]]>" + PART),
    _svg("<style>@import url(x.css);</style>" + PART),
    _svg("<text>a < b</text>" + PART),
    _svg(PART) + "<g/>",
    _svg(PART) + "trailing",
    "lead" + _svg(PART),
    _svg("<g>" + PART),
    _svg("<g>" + PART + "</rect>"),
    "<g>" + PART + "</g>",
    "</svg>",
    _svg("<script>" + PART),
    _svg(PART, head='<svg class="Bad" xmlns="http://www.w3.org/2000/svg"><title>r</title>'),
    _svg('<g id="loose">' + PART + "</g>", head='<svg class="ok" xmlns="http://www.w3.org/2000/svg"><title>r</title>'),
    _svg("<style>rect {fill:red}</style>" + PART, head='<svg class="ok"><title>r</title>'),
    _svg("<text>javascript: in a label</text>" + PART),
    _svg("<style>rect {fill:url(https://example.com/p)}</style>" + PART),
    _svg("<style>rect {fill:u\\72l(x)}</style>" + PART),
    _svg('<style>rect {background:image-set("x.png" 1x)}</style>' + PART),
])
def test_markup_it_cannot_trust_or_scope_stays_an_image(svg: str) -> None:
    assert inline_svg(svg, "s") is None


def test_a_complete_generator_scope_is_kept() -> None:
    head = '<svg class="ok" xmlns="http://www.w3.org/2000/svg"><title>r</title>'
    body = ('<style>svg.ok rect {fill:url(#ok-p)} @media (x){svg.ok text {fill:red}} svg.ok {color:red}</style>'
            '<pattern id="ok-p"/><use href="#ok-p"/>' + PART)
    assert inline_svg(_svg(body, head), "s") == _svg(body, head)


def test_a_link_keeps_its_external_href() -> None:
    out = inline_svg(_svg('<a href="https://example.com/doc">' + PART + "</a>"), "s")
    assert out is not None and 'href="https://example.com/doc"' in out


def test_a_self_closed_root_has_no_details() -> None:
    assert sanitise("<svg/>") == ("<svg/>", False)
    assert inline_svg("<svg/>", "s") is None


def test_css_selectors_reads_media_blocks_and_selector_lists() -> None:
    assert css_selectors("a, b {x:1} @media (y) { c {z:2} }") == ["a", "b", "c"]
    assert root_class("<g/>") is None
