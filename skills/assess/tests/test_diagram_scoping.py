"""Every diagram is scoped, so several inlined on one page leave each other alone (#1358, #1312).

Each generator's output is read as static markup only, with no DOM: the root ``<svg>`` carries
the diagram's scope class, every ``<style>`` selector starts with ``svg.<scope>``, every id
starts with ``<scope>-`` and every ``url(#...)`` / ``href="#..."`` names an id in the same SVG.
The goldens are each generator's render of its fixture (their own tests keep them current);
the radar and the treemap are rendered here, and the doc graph's real render is checked in
``tests/test_golden_svg_render.py``.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import lib.fleet.fleet_data as fd
import lib.fleet.fleet_radar as fr
from fleet_data_fixture import build
from lib.svg_scope import SCOPES, scope_css, scope_svg
from svg_cascade import overridden_text_attributes
from test_complexity_treemap import stubbed_treemap

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Golden glob (relative to tests/fixtures) -> the scope its generator writes under.
GOLDENS: dict[str, str] = {
    "assess_diagrams/expected-doc-drift.svg": "assess-doc-drift",
    "assess_diagrams/expected-evidence-waffle.svg": "assess-evidence-waffle",
    "assess_diagrams/expected-package-map*.svg": "assess-package-map",
    "assess_diagrams/expected-review-strip.svg": "assess-review-strip",
    "assess_diagrams/expected-step-ladder.svg": "assess-step-ladder",
    "assess_diagrams/expected-tag-profile*.svg": "assess-tag-profile",
    "fleet_diagrams/expected-shared-tooling.svg": "fleet-shared-tooling",
    "fleet_diagrams/expected-slope.svg": "fleet-slope",
    "fleet_diagrams/expected-tag-heatmap*.svg": "fleet-tag-heatmap",
    "fleet_diagrams/expected-tag-profile*.svg": "fleet-tag-profile",
    "fleet_heatmap/expected*.svg": "fleet-heatmap",
    "fleet_step_diagrams/expected-one-change-away*.svg": "fleet-one-change-away",
    "fleet_step_diagrams/expected-step-funnel*.svg": "fleet-step-funnel",
    "fleet_step_diagrams/expected-step-movement*.svg": "fleet-step-movement",
    "fleet_trends/expected-trend.svg": "fleet-trend",
}

_RULE = re.compile(r"([^{}]+)\{[^{}]*\}")
_ID = re.compile(r'\sid="([^"]+)"')
_REF = re.compile(r'url\(#([^)]+)\)|\shref="#([^"]+)"')


def _selectors(svg: str) -> list[str]:
    """Every selector in every ``<style>``, ``@media`` wrappers skipped (innermost rules only)."""
    css = "".join(re.findall(r"<style>(.*?)</style>", svg, re.S))
    return [sel.strip() for prelude in _RULE.findall(css) for sel in prelude.split(",") if sel.strip()]


def scope_problems(svg: str, scope: str) -> list[str]:
    """What breaks ``scope`` in ``svg``: the root class, a selector or id outside the scope, a
    reference to an id the SVG does not define. Empty when the diagram is scoped."""
    problems = []
    root = re.search(r"<svg\b[^>]*>", svg)
    if root is None or f'class="{scope}"' not in root.group(0):
        problems.append("root <svg> lacks the scope class")
    problems += [f"selector {s!r}" for s in _selectors(svg) if not s.startswith(f"svg.{scope} ")]
    ids = set(_ID.findall(svg))
    problems += [f"id {i!r}" for i in sorted(ids) if not i.startswith(f"{scope}-")]
    refs = {a or b for a, b in _REF.findall(svg)}
    problems += [f"dangling reference #{r}" for r in sorted(refs - ids)]
    return problems


def _goldens() -> list[tuple[Path, str]]:
    return [(path, scope) for pattern, scope in GOLDENS.items()
            for path in sorted(FIXTURES.glob(pattern))]


def _treemap_svg(tmp_path: Path) -> str:
    with stubbed_treemap():
        import importlib
        render = importlib.import_module("lib.complexity.treemap_render")
        node = render.Node(name="a.py", rel_path="src/a.py", loc=120, est_tokens=900, metric=7.0,
                           aux_metric=3.0, aux_label="commits (last 12mo)",
                           color=(0.8, 0.2, 0.1, 1.0), is_file=True, hatch="diag")
        out = tmp_path / "treemap.svg"
        render.write_svg([(0.0, 0.0, 400.0, 300.0, node)], Path("/repo"), 1600.0, 1000.0, out,
                         True, "ccn", show_survivor_legend=True, churn_window="last 12mo")
        return out.read_text(encoding="utf-8")


def _rendered(tmp_path: Path) -> list[tuple[str, str, str]]:
    """(name, svg, scope) for every golden plus the radar and the treemap rendered now."""
    out = [(path.name, path.read_text(encoding="utf-8"), scope) for path, scope in _goldens()]
    radar = fr.render(fr.radar_view(dict(build(fd.Privacy(full=True)))))
    out.append(("radar", radar, SCOPES["radar"]))
    out.append(("treemap", _treemap_svg(tmp_path), SCOPES["complexity-heatmap"]))
    return out


def test_every_golden_pattern_finds_a_file() -> None:
    for pattern in GOLDENS:
        assert sorted(FIXTURES.glob(pattern)), pattern


def test_scopes_are_distinct_and_every_generator_is_checked() -> None:
    assert len(set(SCOPES.values())) == len(SCOPES)
    checked = {*GOLDENS.values(), SCOPES["radar"], SCOPES["complexity-heatmap"],
               SCOPES["doc-graph"]}  # the doc graph: tests/test_golden_svg_render.py
    assert checked == set(SCOPES.values())


def test_every_diagram_is_scoped(tmp_path: Path) -> None:
    for name, svg, scope in _rendered(tmp_path):
        assert scope_problems(svg, scope) == [], name
        assert overridden_text_attributes(svg) == [], name


def test_no_two_diagrams_share_an_id(tmp_path: Path) -> None:
    """Diagrams of different kinds never share an id. Two renders of one generator share
    its pattern ids, whose definitions are identical (lib/svg_scope.py)."""
    owner: dict[str, str] = {}
    for name, svg, scope in _rendered(tmp_path):
        for ident in set(_ID.findall(svg)):
            assert owner.setdefault(ident, scope) == scope, (name, ident)


# ── lib/svg_scope.py ─────────────────────────────────────────────────────────


def test_scope_css_prefixes_each_selector_inside_media_blocks_too() -> None:
    css = "text { fill: red; }.a,.b:not([x]){fill:#fff}@media (prefers-color-scheme: dark){.a{fill:#000}}"
    assert scope_css(css, "d") == ("svg.d text { fill: red; }svg.d .a,svg.d .b:not([x]) {fill:#fff}"
                                   "@media (prefers-color-scheme: dark){svg.d .a {fill:#000}}")


def test_scope_css_keeps_a_comma_inside_parentheses_in_one_selector() -> None:
    assert scope_css("a:is(b, c){x:y}", "d") == "svg.d a:is(b, c) {x:y}"


def test_scope_svg_rewrites_the_root_ids_and_references() -> None:
    svg = ('<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg" role="img">'
           '<style>rect:hover{stroke:#000}</style><defs><pattern id="p"/></defs>'
           '<rect fill="url(#p)"/><use href="#p"/><use xlink:href="#p"/></svg>')
    out = scope_svg(svg, "kind")
    assert out.startswith('<?xml version="1.0"?>\n<svg class="kind" xmlns=')
    assert 'id="kind-p"' in out and 'url(#kind-p)' in out
    assert out.count('href="#kind-p"') == 2
    assert "svg.kind rect:hover {stroke:#000}" in out
    assert scope_problems(out, "kind") == []


def test_scope_svg_leaves_text_nodes_alone_and_rewrites_a_url_in_style() -> None:
    svg = ('<svg><style>rect{fill:url(#p)}</style><pattern id="p"/>'
           '<rect data-path="a/url(#x) id=&quot;y&quot;.py"><title>a/url(#x) id="y".py</title></rect>'
           '<text>see url(#x)</text></svg>')
    out = scope_svg(svg, "kind")
    assert "<title>a/url(#x) id=\"y\".py</title>" in out and "<text>see url(#x)</text>" in out
    assert 'data-path="a/url(#x) id=&quot;y&quot;.py"' in out
    assert "svg.kind rect {fill:url(#kind-p)}" in out
    assert scope_svg('<svg><rect data-path="url(#x)" fill="url(#x)"/></svg>', "kind") == (
        '<svg class="kind"><rect data-path="url(#x)" fill="url(#kind-x)"/></svg>')


def test_an_id_selector_follows_the_id_it_names() -> None:
    out = scope_svg('<svg><style>#label{fill:red}</style><text id="label">x</text></svg>', "kind")
    assert "svg.kind #kind-label {fill:red}" in out and 'id="kind-label"' in out


def test_scoping_an_already_scoped_document_raises() -> None:
    with pytest.raises(ValueError, match="already carries a class"):
        scope_svg(scope_svg("<svg><g/></svg>", "kind"), "kind")


@pytest.mark.parametrize("svg, scope, message", [
    ("<svg></svg>", "Bad Scope", "lowercase"),
    ("<g></g>", "kind", "no <svg> root"),
    ('<svg class="x"></svg>', "kind", "already carries a class"),
    ("<svg><style>a{</style></svg>", "kind", "unbalanced"),
])
def test_scope_svg_refuses_what_it_cannot_scope(svg: str, scope: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        scope_svg(svg, scope)
