r"""A diagram SVG made safe to inline in a generated HTML page, or None to keep it an ``<img>`` (#1362).

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
  ``vbscript:`` URL (an ``href``, or a ``fill``). So does any ``url(...)`` that is
  not ``#``-local, and any ``href`` that is not, apart from an ``<a>``'s: a link fetches nothing
  until followed, but a ``<use>`` or a ``url()`` would, and the page makes no network request.
- **Allow-list.** Any other element outside ``ELEMENTS`` (the SVG drawing elements), a CDATA
  section, markup the tokenizer cannot read, or a root that is not one balanced ``<svg>``: None.
  An element such as ``<img>`` or ``<p>`` would break out of the SVG in an HTML parser.
- **Where the HTML parser differs.** ``<title>`` and ``<desc>`` are HTML integration points and a
  ``<style>``'s text has its character references decoded, so these hold text only (no child
  element). A literal ``<`` or ``>`` in an attribute value, a ``<style>`` with attributes, and an
  ``&`` or ``\`` in a ``<style>``'s text or a ``style`` attribute (an entity or a CSS escape could
  spell ``url(`` or ``@import``) all refuse; a ``\`` refuses in any attribute but a ``data-*``
  one. So does an attribute that is not double-quoted, which the id and scope checks would not
  read, and any ``<style>`` at-rule but ``@media``: a statement such as ``@layer a;`` would hide
  the next rule's selector from the scope check, and a selector that leaves the diagram through a
  sibling combinator (``svg.<scope> ~ *``), a CSS comment, or a brace, semicolon or line break in
  a quoted string, or a rule nested in a style rule (each would let the selector check read the sheet differently from a browser).
  Every selector is checked against the scope after scoping, on both paths. So does a tag name not in its SVG case (``<STYLE>``),
  or an ``id``, ``class``, ``href`` or ``style`` attribute name not in lowercase (``ID=``): the
  browser folds the case, the checks here do not. An end tag is written without attributes. The
  generators write none of these.
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
CAMEL = {"lineargradient": "linearGradient", "radialgradient": "radialGradient", "clippath": "clipPath",
         "textpath": "textPath"}  # the SVG spelling of a mixed-case element name
CHECKED_ATTRS = frozenset({"id", "class", "href", "xlink:href", "style"})  # read by the checks in lowercase
TEXT_ONLY = frozenset({"title", "desc", "style"})  # no child element: see the module docstring
UNSAFE = ("<script", "foreignobject", "javascript:", "vbscript:", "@import", "image-set")

_ATTR = r"""\s+[^\s=/>"']+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s"'>]+))?"""
_TOKEN = re.compile(
    r"(?P<comment><!--.*?-->)|(?P<prolog><\?xml[^>]*\?>|<!DOCTYPE[^>]*>)"
    rf"|<(?P<close>/?)(?P<name>[A-Za-z][\w:.-]*)(?P<attrs>(?:{_ATTR})*)\s*(?P<empty>/?)>"
    r"|(?P<text>[^<]+)", re.S | re.I)
_ATTR_PARTS = re.compile(r"""(\s+)([^\s=/>"']+)(?:\s*=\s*("[^"]*"|'[^']*'|[^\s"'>]+))?""")
_URL_SCHEME = re.compile(r"(?:java|vb)script:")
# A CSS comment, or a brace, semicolon or line break inside a quoted string: either would make
# the brace count in css_selectors disagree with the browser's reading of the sheet.
_HIDES_BRACES = re.compile(r"""/\*|"[^"]*[{};\n][^"]*"|'[^']*[{};\n][^']*'""")
_FETCH = re.compile(r"""url\(\s*['"]?(?!#)""", re.I)  # a url() that is not #-local
_ROOT_CLASS = re.compile(r'<svg\b[^>]*?\sclass="([^"]*)"')
_ID = re.compile(r'\sid="([^"]*)"')
_REF = re.compile(r'url\(#([^)]+)\)|\s(?:xlink:)?href="#([^"]*)"')


def _safe_attrs(element: str, attrs: str) -> str | None:
    """``attrs`` without event handlers, script URLs or fetches, the rest as written; None to refuse."""
    kept = []
    for m in _ATTR_PARTS.finditer(attrs):
        raw = (m.group(3) or "").strip("\"'")
        name = m.group(2).lower()
        if name in CHECKED_ATTRS and m.group(2) != name:
            return None
        if not (m.group(3) or "").startswith('"') or "<" in raw or ">" in raw or element == "style" or ("\\" in raw and not name.startswith("data-")) \
                or (name == "style" and "&" in raw):
            return None
        value = html.unescape(raw)
        value = re.sub(r"[\x00-\x20]", "", value).lower()
        fetches = _FETCH.search(value) or (name.endswith("href") and element != "a" and not value.startswith("#"))
        if name.startswith("on") or _URL_SCHEME.search(value) or fetches:
            continue
        kept.append(m.group(0))
    return "".join(kept)


class _Refused(Exception):
    """The markup cannot be inlined safely."""


def _text(text: str, open_names: list[str]) -> str:
    """A text token as kept: outside the root only whitespace (dropped), in a ``<style>`` no ``&`` or ``\\``."""
    if not open_names:
        if text.strip():
            raise _Refused
        return ""
    if open_names[-1] == "style" and re.search(r"[&\\]", text):
        raise _Refused
    return text


def _check_tag(name: str, close: bool, open_names: list[str], closed: bool) -> None:
    """Refuse a tag inside a text-only element, outside ``ELEMENTS``, or outside one ``<svg>`` root."""
    if open_names and open_names[-1] in TEXT_ONLY and not (close and name == open_names[-1]):
        raise _Refused
    if name in DROPPED:
        return
    if name not in ELEMENTS or closed or (not open_names and (name != "svg" or close)):
        raise _Refused


def sanitise(svg: str) -> tuple[str, bool]:
    """``svg`` sanitised, and whether it has per-part details; ``("", False)`` when refused."""
    try:
        return _sanitise(svg)
    except _Refused:
        return "", False


def _sanitise(svg: str) -> tuple[str, bool]:
    out: list[str] = []
    open_names: list[str] = []
    skipping = 0  # depth inside a dropped element
    details = closed = False
    pos = 0
    while pos < len(svg):
        m = _TOKEN.match(svg, pos)
        if m is None:
            raise _Refused
        pos = m.end()
        if m.group("comment") or m.group("prolog"):
            continue
        if m.group("text") is not None:
            out.append("" if skipping else _text(m.group("text"), open_names))
            continue
        name, close, empty = m.group("name").lower(), bool(m.group("close")), m.group("empty")
        if not skipping:
            _check_tag(name, close, open_names, closed)
            if name not in DROPPED and m.group("name") != CAMEL.get(name, name):
                raise _Refused  # the checks read tag names in their SVG case
        if skipping or name in DROPPED:
            if not empty:
                skipping += -1 if close else 1
            continue
        if close:
            if open_names.pop() != name:
                raise _Refused
            closed = not open_names
            out.append(f"</{m.group('name')}>")  # an end tag's attributes are dropped
            continue
        details = details or (name == "title" and len(open_names) > 1)
        attrs = _safe_attrs(name, m.group("attrs"))
        if attrs is None:
            raise _Refused
        out.append(f"<{m.group('name')}{attrs}{'/' if empty else ''}>")
        if not empty:
            open_names.append(name)
        elif not open_names:
            closed = True
    if not closed or skipping:
        return "", False
    return "".join(out).strip(), details


def _blocks(css: str) -> list[tuple[str, str]]:
    """The top-level ``(prelude, body)`` pairs of ``css``."""
    out: list[tuple[str, str]] = []
    pos = 0
    while (brace := css.find("{", pos)) >= 0:
        depth, end = 0, brace
        for end in range(brace, len(css)):
            depth += {"{": 1, "}": -1}.get(css[end], 0)
            if depth == 0:
                break
        out.append((css[pos:brace].strip(), css[brace + 1:end]))
        pos = end + 1
    return out


def css_selectors(css: str) -> list[str]:
    """Every selector in ``css``, ``@media`` and other ``@`` blocks recursed into."""
    out: list[str] = []
    for prelude, body in _blocks(css):
        if prelude.startswith("@"):
            out.extend(css_selectors(body))
        else:
            out.extend(s.strip() for s in prelude.split(",") if s.strip())
    return out


def _nested_rule(css: str) -> bool:
    """A rule nested inside a style rule (CSS nesting), whose selector ``css_selectors`` never reads."""
    return any(_nested_rule(body) if prelude.startswith("@") else "{" in body for prelude, body in _blocks(css))


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
    elif not re.fullmatch(r"[a-z][a-z0-9-]*", own):
        return None
    styles = "".join(re.findall(r"<style>(.*?)</style>", clean, re.S))
    if (_HIDES_BRACES.search(styles) or _nested_rule(styles) or not _scope_complete(clean, root_class(clean) or "")
            or sanitise(clean)[0] != clean or any(word in clean.lower() for word in UNSAFE)
            or _FETCH.search(clean) or "\\" in styles or re.search(r"@(?!media\b)", styles)
            or any(re.match(r"svg\.[a-z0-9-]+\s*[~+]", sel) for sel in css_selectors(styles))):
        return None
    return clean
