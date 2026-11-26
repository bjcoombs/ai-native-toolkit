"""A diagram SVG made safe to inline in a generated HTML page, or None to keep it an ``<img>`` (#1362).

A page that inlines a diagram, rather than showing it as an ``<img>``, lets the reader hover or tap
each part for its details (``references/embedding-diagrams.md``). An inlined SVG runs in the page,
so it is sanitised first, and it shares the page's stylesheet and id space, so it is scoped
(``lib/svg_scope.py``).

``inline_svg(svg, scope)`` returns the markup to inline, or None when the diagram stays an image:

- **Details only.** A diagram whose only ``<title>`` is the root's (the Step movement grid, the
  step ladder) has nothing to show on hover: None.
- **Sanitised.** The XML prolog, doctype and comments go. ``<script>`` and ``<foreignObject>`` go
  with everything inside them. Every ``on*`` attribute goes, and so does any attribute whose value,
  with character references decoded and whitespace removed, holds a ``javascript:`` or
  ``vbscript:`` URL (an ``href``, or an ``<animate>``'s ``to``). So does any ``url(...)`` that is
  not ``#``-local, and any ``href`` that is not, apart from an ``<a>``'s: a link fetches nothing
  until followed, but a ``<use>`` or a ``url()`` would, and the page makes no network request.
- **Allow-list.** Any other element outside ``ELEMENTS`` (the SVG drawing elements), a CDATA
  section, markup the tokenizer cannot read, or a root that is not one balanced ``<svg>``: None.
  An element such as ``<img>`` or ``<p>`` would break out of the SVG in an HTML parser.
- **Scoped.** A root that already carries a class is the generator's scope (#1449): kept when it
  is complete (every id, reference and style selector under it), None otherwise. A root without a
  class is scoped here under ``scope``.
- **Checked.** The result must sanitise to itself and hold none of ``<script``,
  ``foreignObject``, ``javascript:``, ``vbscript:``, ``@import`` or ``image-set``, in any case, no
  ``url(`` that is not ``#``-local (in a ``<style>`` too) and no backslash in a ``<style>`` (a CSS
  escape could spell one), or it is None. An image embed never runs script or fetches, so
  refusing is always safe.

``root_class(markup)`` reads an inlined SVG's scope back, so a page can keep scopes unique, and
``css_selectors(css)`` lists a stylesheet's selectors, ``@media`` blocks included. Stdlib only and
deterministic. Tests: ``tests/test_svg_inline.py``.
"""
from __future__ import annotations

import html
import re

from lib.svg_scope import scope_svg

# The SVG elements a diagram may draw with. Anything else is refused, apart from the two dropped.
ELEMENTS = frozenset({
    "svg", "g", "defs", "title", "desc", "style", "rect", "circle", "ellipse", "line", "polyline",
    "polygon", "path", "text", "tspan", "textpath", "pattern", "lineargradient", "radialgradient",
    "stop", "clippath", "mask", "marker", "use", "symbol", "a", "metadata"})
DROPPED = frozenset({"script", "foreignobject"})  # removed with their contents
UNSAFE = ("<script", "foreignobject", "javascript:", "vbscript:", "@import", "image-set")

_ATTR = r"""\s+[^\s=/>"']+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s"'>]+))?"""
_TOKEN = re.compile(
    r"(?P<comment><!--.*?-->)|(?P<prolog><\?xml[^>]*\?>|<!DOCTYPE[^>]*>)"
    rf"|<(?P<close>/?)(?P<name>[A-Za-z][\w:.-]*)(?P<attrs>(?:{_ATTR})*)\s*(?P<empty>/?)>"
    r"|(?P<text>[^<]+)", re.S | re.I)
_ATTR_PARTS = re.compile(r"""(\s+)([^\s=/>"']+)(?:\s*=\s*("[^"]*"|'[^']*'|[^\s"'>]+))?""")
_URL_SCHEME = re.compile(r"(?:java|vb)script:")
_FETCH = re.compile(r"""url\(\s*['"]?(?!#)""", re.I)  # a url() that is not #-local
_ROOT_CLASS = re.compile(r'<svg\b[^>]*?\sclass="([^"]*)"')
_ID = re.compile(r'\sid="([^"]*)"')
_REF = re.compile(r'url\(#([^)]+)\)|\s(?:xlink:)?href="#([^"]*)"')


def _safe_attrs(element: str, attrs: str) -> str:
    """``attrs`` without event handlers, script URLs or fetches, the rest as written."""
    kept = []
    for m in _ATTR_PARTS.finditer(attrs):
        value = html.unescape((m.group(3) or "").strip("\"'"))
        value = re.sub(r"[\x00-\x20]", "", value).lower()
        name = m.group(2).lower()
        fetches = _FETCH.search(value) or (name.endswith("href") and element != "a" and not value.startswith("#"))
        if name.startswith("on") or _URL_SCHEME.search(value) or fetches:
            continue
        kept.append(m.group(0))
    return "".join(kept)


def sanitise(svg: str) -> tuple[str, bool]:
    """``svg`` sanitised, and whether it has per-part details; ``("", False)`` when refused."""
    out: list[str] = []
    open_names: list[str] = []
    skipping = 0  # depth inside a dropped element
    details = closed = False
    pos = 0
    while pos < len(svg):
        m = _TOKEN.match(svg, pos)
        if m is None:
            return "", False
        pos = m.end()
        if m.group("comment") or m.group("prolog"):
            continue
        if m.group("text") is not None:
            if not skipping and (open_names or not m.group("text").strip()):
                out.append(m.group("text") if open_names else "")
            elif not skipping:
                return "", False
            continue
        name, close, empty = m.group("name").lower(), m.group("close"), m.group("empty")
        if skipping or name in DROPPED:
            if not empty:
                skipping += -1 if close else 1
            continue
        if name not in ELEMENTS or closed or (not open_names and (name != "svg" or close)):
            return "", False
        if close:
            if open_names.pop() != name:
                return "", False
            closed = not open_names
            out.append(m.group(0))
            continue
        details = details or (name == "title" and len(open_names) > 1)
        out.append(f"<{m.group('name')}{_safe_attrs(name, m.group('attrs'))}{'/' if empty else ''}>")
        if not empty:
            open_names.append(name)
        elif not open_names:
            closed = True
    if not closed or skipping:
        return "", False
    return "".join(out).strip(), details


def css_selectors(css: str) -> list[str]:
    """Every selector in ``css``, ``@media`` and other ``@`` blocks recursed into."""
    out: list[str] = []
    pos = 0
    while (brace := css.find("{", pos)) >= 0:
        prelude, depth, end = css[pos:brace].strip(), 0, brace
        for end in range(brace, len(css)):
            depth += {"{": 1, "}": -1}.get(css[end], 0)
            if depth == 0:
                break
        if prelude.startswith("@"):
            out.extend(css_selectors(css[brace + 1:end]))
        else:
            out.extend(s.strip() for s in prelude.split(",") if s.strip())
        pos = end + 1
    return out


def root_class(markup: str) -> str | None:
    m = _ROOT_CLASS.match(markup)
    return m.group(1) if m else None


def _scope_complete(svg: str, scope: str) -> bool:
    """Every id, ``#`` reference and style selector in ``svg`` is under ``scope``."""
    prefix = f"{scope}-"
    refs = [a or b for a, b in _REF.findall(svg)]
    selectors = [s for css in re.findall(r"<style>(.*?)</style>", svg, re.S) for s in css_selectors(css)]
    return (all(i.startswith(prefix) for i in _ID.findall(svg) + refs)
            and all(s.startswith(f"svg.{scope} ") or s == f"svg.{scope}" for s in selectors))


def inline_svg(svg: str, scope: str) -> str | None:
    """The markup to inline for ``svg`` (see the module docstring), or None to keep the image."""
    clean, details = sanitise(svg)
    if not details:
        return None
    own = root_class(clean)
    if own is None:
        try:
            clean = scope_svg(clean, scope)
        except ValueError:
            return None
    elif not re.fullmatch(r"[a-z][a-z0-9-]*", own) or not _scope_complete(clean, own):
        return None
    styles = "".join(re.findall(r"<style>(.*?)</style>", clean, re.S))
    if (sanitise(clean)[0] != clean or any(word in clean.lower() for word in UNSAFE)
            or _FETCH.search(clean) or "\\" in styles):
        return None
    return clean
