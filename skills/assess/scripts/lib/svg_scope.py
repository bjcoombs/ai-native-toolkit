"""One scope per diagram, so several SVGs inlined on one page leave each other alone.

Every diagram generator writes a standalone SVG whose ``<style>`` and ids assume the SVG
is the whole document. Inlined into a host page beside other diagrams, a bare
``text { ... }`` rule restyles every ``<text>`` on the page, a shared class such as
``.f-bg`` takes whichever diagram's palette comes last, and ``url(#fleet-hatch)``
resolves to whichever pattern comes first (#1312, #1358).

``scope_svg(svg, scope)`` rewrites a finished SVG document under one scope name:

- **Root.** The root ``<svg>`` gets ``class="<scope>"``.
- **Styles.** Every selector in every ``<style>`` is prefixed with ``svg.<scope>``, inside
  ``@media`` blocks too, so a rule reaches only elements of this SVG. Each rule gains the
  same specificity, so the order of rules within the sheet still decides between them.
- **Ids.** Every ``id="X"`` becomes ``id="<scope>-X"``, and every ``url(#X)`` and
  ``href="#X"`` reference follows it.

The scope is fixed per diagram kind (``SCOPES`` lists them), so output stays
byte-identical for the same inputs. Two diagrams of the same kind on one page (two
repositories' treemaps) share a scope: their rules and pattern definitions are the
same, so a reference that resolves to the other copy draws the same thing.

Stdlib only. Tests: ``tests/test_diagram_scoping.py``.
"""
from __future__ import annotations

import re

# Diagram kind -> scope name, one per generator. Assess and fleet both write a
# tag-profile.svg, so the scope names the skill as well as the file.
SCOPES: dict[str, str] = {
    "complexity-heatmap": "assess-complexity-heatmap",
    "doc-graph": "assess-doc-graph",
    "evidence-waffle": "assess-evidence-waffle",
    "step-ladder": "assess-step-ladder",
    "package-map": "assess-package-map",
    "doc-drift": "assess-doc-drift",
    "review-strip": "assess-review-strip",
    "assess-tag-profile": "assess-tag-profile",
    "fleet-heatmap": "fleet-heatmap",
    "shared-tooling": "fleet-shared-tooling",
    "slope": "fleet-slope",
    "trend": "fleet-trend",
    "radar": "fleet-radar",
    "step-funnel": "fleet-step-funnel",
    "step-movement": "fleet-step-movement",
    "one-change-away": "fleet-one-change-away",
    "fleet-tag-profile": "fleet-tag-profile",
    "tag-heatmap": "fleet-tag-heatmap",
}

_SCOPE_NAME = re.compile(r"[a-z][a-z0-9-]*")
_ROOT = re.compile(r"<svg\b([^>]*)>")
_STYLE = re.compile(r"(<style>)(.*?)(</style>)", re.S)
_ID = re.compile(r'(\sid=")([^"]+)(")')
_URL = re.compile(r"url\(#([^)]+)\)")
_HREF = re.compile(r'(\s(?:xlink:)?href="#)([^"]+)(")')


def _split_selectors(prelude: str) -> list[str]:
    """A selector list split on its top-level commas (none inside ``:not(...)``)."""
    parts, depth, start = [], 0, 0
    for index, char in enumerate(prelude):
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(prelude[start:index])
            start = index + 1
    parts.append(prelude[start:])
    return [part.strip() for part in parts if part.strip()]


def _block_end(css: str, open_at: int) -> int:
    """The index of the ``}`` that closes the ``{`` at ``open_at``."""
    depth = 0
    for index in range(open_at, len(css)):
        if css[index] == "{":
            depth += 1
        elif css[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    raise ValueError("unbalanced braces in <style>")


def scope_css(css: str, scope: str) -> str:
    """``css`` with every selector prefixed by ``svg.<scope>``; ``@`` blocks are recursed into."""
    out: list[str] = []
    pos = 0
    while True:
        brace = css.find("{", pos)
        if brace < 0:
            out.append(css[pos:])
            return "".join(out)
        prelude = css[pos:brace]
        end = _block_end(css, brace)
        lead = prelude[:len(prelude) - len(prelude.lstrip())]
        if prelude.strip().startswith("@"):
            out.append(f"{prelude}{{{scope_css(css[brace + 1:end], scope)}}}")
        else:
            selectors = ",".join(f"svg.{scope} {sel}" for sel in _split_selectors(prelude))
            out.append(f"{lead}{selectors} {css[brace:end + 1]}")
        pos = end + 1


def scope_svg(svg: str, scope: str) -> str:
    """A whole SVG document rewritten under ``scope`` (see the module docstring)."""
    if not _SCOPE_NAME.fullmatch(scope):
        raise ValueError(f"scope must be lowercase letters, digits and hyphens: {scope!r}")
    root = _ROOT.search(svg)
    if root is None:
        raise ValueError("no <svg> root element")
    if re.search(r"\sclass=", root.group(1)):
        raise ValueError("the root <svg> already carries a class")
    svg = f'{svg[:root.start()]}<svg class="{scope}"{root.group(1)}>{svg[root.end():]}'
    svg = _ID.sub(lambda m: f"{m.group(1)}{scope}-{m.group(2)}{m.group(3)}", svg)
    svg = _URL.sub(lambda m: f"url(#{scope}-{m.group(1)})", svg)
    svg = _HREF.sub(lambda m: f"{m.group(1)}{scope}-{m.group(2)}{m.group(3)}", svg)
    return _STYLE.sub(lambda m: f"{m.group(1)}{scope_css(m.group(2), scope)}{m.group(3)}", svg)
