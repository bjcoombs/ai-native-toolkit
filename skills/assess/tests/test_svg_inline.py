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
    "<rect onmouseover=\"alert(1)\" ONCLICK=\"alert(1)\" width=\"1\"/>",
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
    _svg('<desc><style><a title="</style><img src=x onerror=alert(1)>"></a></style></desc>' + PART),
    _svg('<title><style><a title="</style><img src=x onerror=alert(1)>"></a></style></title>' + PART),
    _svg("<desc><script>x</script></desc>" + PART),
    _svg('<g data-x="a>b">' + PART + "</g>"),
    _svg('<style type="text/css">body{display:none}</style>' + PART),
    _svg('<style type="text/css">@\\69mport "https://evil.example/x.css";</style>' + PART),
    _svg("<style>rect{fill:u&#114;l(https://evil.example/a)}</style>" + PART),
    _svg('<style>&#64;import "https://evil.example/b";</style>' + PART),
    _svg('<rect style="fill:u\\72l(https://evil.example/c)"/>' + PART),
    _svg('<rect style="fill:image&#45;set(\'https://evil.example/d\' 1x)"/>' + PART),
    _svg('<rect fill="u\\72l(https://evil.example/e)"/>' + PART),
    _svg("<style>@layer a; body{display:none}</style>" + PART),
    _svg("<style>@layer a; body{display:none}</style>" + PART,
         head='<svg class="fleet-x" xmlns="http://www.w3.org/2000/svg"><title>r</title>'),
    _svg("<g id='dup'>" + PART + "</g>", head='<svg class="fleet-x" xmlns="http://www.w3.org/2000/svg"><title>r</title>'),
    _svg(PART, head="""<svg data-x='a class="evil"' class="real"><title>r</title>"""),
    _svg("<g id=bare>" + PART + "</g>"),
    _svg("<style>svg.fleet-x ~ *, svg.fleet-x + div{display:none}</style>" + PART,
         head='<svg class="fleet-x" xmlns="http://www.w3.org/2000/svg"><title>r</title>'),
    _svg("<style>~ div{display:none}</style>" + PART),
    _svg("<Style>svg.fleet-x ~ * {display:none}</Style>" + PART,
         head='<svg class="fleet-x" xmlns="http://www.w3.org/2000/svg"><title>r</title>'),
    _svg("<STYLE>body{display:none}</STYLE>" + PART),
    _svg('<g ID="main">' + PART + "</g>", head='<svg class="fleet-x" xmlns="http://www.w3.org/2000/svg"><title>r</title>'),
    _svg('<lineargradient id="g"/>' + PART),
    _svg("<style>svg.fleet-x /*{*/ , body{display:none}</style>" + PART,
         head='<svg class="fleet-x" xmlns="http://www.w3.org/2000/svg"><title>r</title>'),
    _svg('<style>rect{content:"}"} body{display:none}</style>' + PART),
    _svg("<style>rect{content:'a;b'}</style>" + PART),
    _svg("<style>svg.fleet-x{ ~ *{display:none}}</style>" + PART,
         head='<svg class="fleet-x" xmlns="http://www.w3.org/2000/svg"><title>r</title>'),
    _svg("<style>rect{fill:red} :root{ ~ *{display:none}}</style>" + PART),
])
def test_markup_it_cannot_trust_or_scope_stays_an_image(svg: str) -> None:
    assert inline_svg(svg, "s") is None


def test_a_complete_generator_scope_is_kept() -> None:
    head = '<svg class="ok" xmlns="http://www.w3.org/2000/svg"><title>r</title>'
    body = ('<style>svg.ok rect {fill:url(#ok-p)} @media (x){svg.ok text {fill:red}} svg.ok {color:red}</style>'
            '<pattern id="ok-p"/><use href="#ok-p"/>' + PART)
    assert inline_svg(_svg(body, head), "s") == _svg(body, head)


def test_a_data_attribute_may_hold_a_backslash_and_an_ampersand() -> None:
    out = inline_svg(_svg('<rect data-path="a\\b &amp; c" width="1"><title>part</title></rect>'), "s")
    assert out is not None and 'data-path="a\\b &amp; c"' in out


def test_an_end_tag_loses_its_attributes() -> None:
    out = inline_svg(_svg('<g>' + PART + '</g onclick="alert(1)">'), "s")
    assert out is not None and "onclick" not in out and "</g>" in out


def test_camel_case_svg_elements_are_kept() -> None:
    out = inline_svg(_svg('<defs><linearGradient id="g"/><clipPath id="c"/></defs>' + PART), "s")
    assert out is not None and "<linearGradient " in out and "<clipPath " in out


def test_a_link_keeps_its_external_href() -> None:
    out = inline_svg(_svg('<a href="https://example.com/doc">' + PART + "</a>"), "s")
    assert out is not None and 'href="https://example.com/doc"' in out


def test_a_self_closed_root_has_no_details() -> None:
    assert sanitise("<svg/>") == ("<svg/>", False)
    assert inline_svg("<svg/>", "s") is None


def test_css_selectors_reads_media_blocks_and_selector_lists() -> None:
    assert css_selectors("a, b {x:1} @media (y) { c {z:2} }") == ["a", "b", "c"]
    assert root_class("<g/>") is None


# --- a cold security review at e646b70 (#1362): each case was confirmed in a browser or is a
# lower-risk gap it named. Every one keeps the image.

@pytest.mark.parametrize("tag", [
    '<rect\x0bdata-x=" onmouseover=document.title=1337 x"/>',
    '<rect\xa0data-x=" onmouseover=document.title=1337 x"/>',
    '<rect data-x\x0b=" onmouseover=document.title=1337 x"/>',
    '<rect data-x="1"\x0bonmouseover="document.title=1337"/>',
    '<rect　onmouseover="document.title=1337"/>',
])
def test_a_tag_split_on_whitespace_html_does_not_read_is_refused(tag: str) -> None:
    assert inline_svg(_svg(tag + PART), "s") is None


def test_each_kept_attribute_is_written_again_with_one_space_and_double_quotes() -> None:
    out = inline_svg(_svg('<rect\n\twidth="1"\r\n  height="2"/>' + PART), "s")
    assert out is not None and '<rect width="1" height="2"/>' in out


@pytest.mark.parametrize("css", [
    'text { content: "}\n} body { background-color: rgb(1,2,3) }',
    "text { content: '}\n} body { background-color: rgb(1,2,3) }",
    'text { content: "a }',
    "text { fill: red } } body { fill: red }",
    "text { fill: red",
])
def test_an_unclosed_css_string_or_stray_brace_is_refused(css: str) -> None:
    assert inline_svg(_svg(f"<style>{css}</style>" + PART), "s") is None
    head = '<svg class="fleet-x" xmlns="http://www.w3.org/2000/svg"><title>r</title>'
    assert inline_svg(_svg(f"<style>{css.replace('text', 'svg.fleet-x text')}</style>" + PART, head), "s") is None


@pytest.mark.parametrize("style", ["fill:red;content:'a'", 'fill:red;content:&quot;a&quot;'])
def test_a_quote_in_a_style_attribute_is_refused(style: str) -> None:
    assert inline_svg(_svg(f'<rect style="{style}"/>' + PART), "s") is None


@pytest.mark.parametrize("ref", ['<use href="&#35;x"/>', '<use xlink:href="&#x23;x"/>', '<a href="&#35;x">' + PART + "</a>"])
def test_an_entity_encoded_reference_is_refused(ref: str) -> None:
    assert inline_svg(_svg('<g id="x"/>' + ref + PART), "s") is None


@pytest.mark.parametrize("css", ["rect { position: fixed }", "rect { z-index: 9 }", "@media (x) { rect { POSITION: fixed } }"])
def test_a_position_or_z_index_is_refused(css: str) -> None:
    assert inline_svg(_svg(f"<style>{css}</style>" + PART), "s") is None
    assert inline_svg(_svg(f'<rect style="{css.split("{")[-1].split("}")[0].strip()}"/>' + PART), "s") is None


@pytest.mark.parametrize("href", ["data:text/html,x", "mailto:a@b.c", "ftp://e.example/x", "javascript-ish:x", "//e.example/x"])
def test_a_link_to_another_scheme_is_refused(href: str) -> None:
    assert inline_svg(_svg(f'<a href="{href}">' + PART + "</a>"), "s") is None


@pytest.mark.parametrize("href", ["https://e.example/doc", "http://e.example/doc", "#part", "docs/a.md", "../b.md?x=1"])
def test_a_link_to_http_a_fragment_or_a_relative_path_is_kept(href: str) -> None:
    out = inline_svg(_svg(f'<a href="{href}">' + PART + "</a>"), "s")
    assert out is not None
    assert f'href="{href}"' in out if not href.startswith("#") else 'href="#s-part"' in out


def test_a_stray_end_tag_for_a_dropped_element_is_refused() -> None:
    assert inline_svg(_svg("<g>" + PART + "</script><rect/><script></g>"), "s") is None


@pytest.mark.parametrize("svg", [
    _svg('<rect width="1" cursor="u&#92;72l(https://example.invalid/p.cur), auto"/>' + PART),
    _svg("<style>@media all; body{display:none}</style>" + PART),
    _svg("<style>@media all; body{display:none}</style>" + PART,
         head='<svg class="ok" xmlns="http://www.w3.org/2000/svg"><title>r</title>'),
    _svg('<style>rect{background:src("http://x")}</style>' + PART),
])
def test_an_encoded_escape_a_semicolon_prelude_or_src_is_refused(svg: str) -> None:
    assert inline_svg(svg, "s") is None
