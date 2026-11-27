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
- **Where the HTML parser differs.** Each of these refuses, because a browser would read the
  markup differently from the checks here. The generators write none of them.

  - Markup: a child element inside ``<title>`` or ``<desc>`` (HTML integration points) or
    ``<style>``; a ``<style>`` with attributes; a tag name not in its SVG case (``<STYLE>``).
  - Attributes: a value that is not double-quoted or holds a literal ``<`` or ``>``; a name that
    is not plain ASCII (``data-x\x0b``); a name given twice (the browser keeps the first); an
    ``&`` or a leading or trailing space in an ``href`` that is not a script URL (either hides the
    reference from the scope check); a ``\`` in any attribute but a ``data-*`` one, written or
    entity-encoded; an ``<a>`` ``href`` that is not http(s), a fragment or a relative path.
  - Whitespace: only HTML's own (tab, line feed, form feed, carriage return, space) separates a
    tag's parts; a tag split on any other (``\x0b``, ``\xa0``) is unreadable, so refused. Each
    kept attribute is written again as `` name="value"``, and an end tag without attributes.
- **Attribute allow-list** (#1502). Apart from ``data-*``, an attribute is kept only if its name,
  in its SVG spelling (``viewBox``, not ``viewbox`` or ``ID``), is in ``ATTRIBUTES``: what the
  generators write, plus an ``href`` and a ``style``. The root takes only ``ROOT_ATTRIBUTES``, so
  no ``transform``, ``overflow`` or ``style`` there. Every ``viewBox`` reads ``0 0 W H`` within
  ``MAX_WIDE`` (10:1) and ``MAX_TALL`` (1:64), the generators' range, with at most six digits per
  side (a 309-digit side would read as infinite and pass); the root has one, and its
  ``width`` and ``height``, when given, are its ``W`` and ``H``, so the diagram cannot stretch the
  page (``viewBox="0 0 40 40000"`` would be 868,000 px tall). Anything else refuses.
- **CSS allow-list.** A ``<style>`` is kept only if the whole sheet parses as the shape the
  generators write (``css_selectors``): rules ``svg.<scope> part part, ... { declarations }``,
  optionally inside ``@media (prefers-color-scheme: dark)``, one level deep. A part is an element
  name and/or ``.class`` and ``#id`` tokens, with at most one of ``:hover``, ``:not([fill])`` or
  ``:not([text-anchor])`` (the treemap's and doc graph's), joined by single spaces. A declaration
  (``declarations_ok``, also applied to a ``style`` attribute) is a property in ``PROPERTIES``
  and a value of letters, digits, ``#.,%-`` and spaces, ``rgb()``/``rgba()`` of numbers and
  ``url(#id)``; quotes only as closed pairs in ``font-family``. Anything else (a comment, an
  escape, an entity, a string, a bad URL, nesting, another at-rule, ``position``) fails the
  parse, so no difference between this reader and a browser's tokenizer can hide a rule. The page
  that inlines the diagram still clips it to its own box (the fleet page's ``contain: paint``).
- **Scoped.** A root that already carries a class is the generator's scope (#1449): kept when it
  is complete (every id and reference under it, every sheet on the allow-list under it), None
  otherwise. A root without a class is scoped here under ``scope``, then checked the same way.
  A scope is lowercase parts joined by single ``-`` (no ``fleet-`` or ``fleet--x``). References are
  read per attribute from its decoded value (#1502), never by a pattern over the whole markup,
  which one attribute could open and another close: an ``id``, a ``#`` ``href`` and each
  ``url(#id)``, matched in any case (CSS reads ``URL(`` as ``url(``). A ``url(`` that is not
  exactly ``url(#id)`` refuses. A ``data-*`` value is never read as a reference: it draws nothing.
- **Checked.** The result must sanitise to itself and hold none of ``<script``,
  ``foreignObject``, ``javascript:``, ``vbscript:``, ``@import``, ``image-set`` or ``src(``, in
  any case, and no ``url(`` that is not ``#``-local, or it is None. An image embed never runs
  script or fetches, so refusing is always safe.

``root_class(markup)`` reads an inlined SVG's scope back, so a page can keep scopes unique, and
``css_selectors(css, scope)`` lists a sheet's selectors, or None when it is off the allow-list.
Stdlib only and deterministic. Tests: ``tests/test_svg_inline.py``.
"""
from __future__ import annotations

import html
import re
from typing import NamedTuple

from lib.svg_scope import scope_svg

# The SVG elements a diagram may draw with. Anything else is refused, apart from the two dropped.
ELEMENTS = frozenset({
    "svg", "g", "defs", "title", "desc", "style", "rect", "circle", "ellipse", "line", "polyline",
    "polygon", "path", "text", "tspan", "textpath", "pattern", "lineargradient", "radialgradient",
    "stop", "clippath", "mask", "marker", "use", "symbol", "a", "metadata"})
DROPPED = frozenset({"script", "foreignobject"})  # removed with their contents
CAMEL = {"lineargradient": "linearGradient", "radialgradient": "radialGradient", "clippath": "clipPath",
         "textpath": "textPath"}  # the SVG spelling of a mixed-case element name
# Every attribute a generator writes, in its SVG spelling (#1502), plus the ``href`` of a link or a
# ``<use>`` and a ``style`` the CSS allow-list reads. A ``data-*`` attribute is kept as well: it
# draws nothing and references nothing. Any other name refuses the diagram.
ATTRIBUTES = frozenset({
    "class", "id", "role", "xmlns", "href", "xlink:href", "style", "x", "y", "x1", "x2", "y1", "y2",
    "cx", "cy", "r", "rx", "width", "height", "d", "points", "transform", "viewBox",
    "preserveAspectRatio", "fill", "opacity", "stroke", "stroke-width", "stroke-opacity",
    "stroke-dasharray", "stroke-linecap", "font-size", "font-weight", "text-anchor", "pointer-events",
    "patternUnits", "patternTransform", "offset", "stop-color", "marker-end", "markerWidth",
    "markerHeight", "refX", "refY", "orient"})
# The root's own: no transform, overflow or position, and a size that only scales its viewBox.
ROOT_ATTRIBUTES = frozenset({"class", "xmlns", "role", "viewBox", "width", "height", "preserveAspectRatio"})
# The viewBox shapes the generators draw (#1502): the widest is 8:1 (a placeholder strip), the
# tallest grows one 20 px row per repository at about 820 px wide, so 64:1 is some 2,600 rows.
MAX_WIDE, MAX_TALL = 10.0, 64.0
TEXT_ONLY = frozenset({"title", "desc", "style"})  # no child element: see the module docstring
UNSAFE = ("<script", "foreignobject", "javascript:", "vbscript:", "@import", "image-set", "src(")

# HTML's own whitespace: Python's \s also matches \x0b, \xa0 and others, which HTML reads as
# part of a name or value, so a tag split on them would be read differently by the browser.
_WS = r"[\t\n\f\r ]"
_ATTR = rf"""{_WS}+[^\t\n\f\r =/>"']+(?:{_WS}*={_WS}*(?:"[^"]*"|'[^']*'|[^\t\n\f\r "'>]+))?"""
_TOKEN = re.compile(
    r"(?P<comment><!--.*?-->)|(?P<prolog><\?xml[^>]*\?>|<!DOCTYPE[^>]*>)"
    rf"|<(?P<close>/?)(?P<name>[A-Za-z][\w:.-]*)(?P<attrs>(?:{_ATTR})*){_WS}*(?P<empty>/?)>"
    r"|(?P<text>[^<]+)", re.S | re.I | re.A)
_ATTR_PARTS = re.compile(rf"""{_WS}+([^\t\n\f\r =/>"']+)(?:{_WS}*={_WS}*("[^"]*"|'[^']*'|[^\t\n\f\r "'>]+))?""")
_ATTR_NAME = re.compile(r"[A-Za-z_][\w:.-]*", re.A)
_LINK = re.compile(r"(?:https?://|#|(?!//)[^:/?#]*(?:[/?#]|$))", re.A)  # http(s), a fragment or a relative path
_URL_SCHEME = re.compile(r"(?:java|vb)script:")
_FETCH = re.compile(r"""url\(\s*['"]?(?!#)""", re.I)  # a url() that is not #-local
_ROOT_CLASS = re.compile(r'<svg\b[^>]*?\sclass="([^"]*)"')
_URL_OPEN = re.compile(r"url\(", re.I)  # CSS reads URL( as url( (#1502)
_URL_LOCAL = re.compile(r"url\(#([A-Za-z0-9_-]+)\)", re.I)
_VIEWBOX = re.compile(r"0 0 (\d{1,6}(?:\.\d{1,3})?) (\d{1,6}(?:\.\d{1,3})?)", re.A)  # finite: no 1e400
_SCOPE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", re.A)  # no empty, doubled or trailing part
_ROOT_VIEWBOX = re.compile(r'<svg\b[^>]*?\sviewBox="')


class _Parsed(NamedTuple):
    markup: str
    details: bool
    names: list[str]  # every id and # reference, each read from its own decoded attribute value
    sheets: list[str]  # the text of every <style>


def _box_ok(width: str, height: str) -> bool:
    """A width and height the generators draw: both positive, within ``MAX_WIDE`` and ``MAX_TALL``."""
    w, h = float(width), float(height)
    return w > 0 and h > 0 and w <= MAX_WIDE * h and h <= MAX_TALL * w


def _references(name: str, decoded: str) -> list[str] | None:
    """The ids ``decoded`` (one attribute's value) names: its own id, its ``#`` href and every
    ``url(#id)``; None when a ``url(`` in it is not exactly ``url(#id)``."""
    if name == "id":
        return [decoded]
    refs = [decoded[1:]] if name.endswith("href") and decoded.startswith("#") else []
    for m in _URL_OPEN.finditer(decoded):
        local = _URL_LOCAL.match(decoded, m.start())
        if local is None:
            return None
        refs.append(local.group(1))
    return refs


def _allowed(name: str, raw: str, decoded: str, names: list[str], box: dict[str, str], root: bool) -> bool:
    """``name`` is on the allow-list in its SVG spelling, its references are readable (added to
    ``names``) and a ``viewBox`` is in range (its size, and a root's ``width`` and ``height``, in ``box``)."""
    if name not in (ROOT_ATTRIBUTES if root else ATTRIBUTES):
        return False  # off the allow-list, or not in its SVG spelling (ID=, viewbox=)
    refs = _references(name, decoded)
    if refs is None:
        return False
    names.extend(refs)
    if name == "viewBox":
        vb = _VIEWBOX.fullmatch(raw)
        if vb is None or not _box_ok(vb.group(1), vb.group(2)):
            return False
        box["vw"], box["vh"] = vb.group(1), vb.group(2)
    elif root and name in ("width", "height"):
        box[name] = raw
    return True


def _safe_attrs(element: str, attrs: str, names: list[str], root: bool = False) -> str | None:
    """``attrs`` without event handlers, script URLs or fetches, each written again as
    `` name="value"``, every id and ``#`` reference added to ``names``; None to refuse (see the
    module docstring)."""
    kept = []
    box: dict[str, str] = {}
    seen: set[str] = set()
    for m in _ATTR_PARTS.finditer(attrs):
        quoted, name = m.group(2) or "", m.group(1).lower()
        raw = quoted[1:-1]
        if (not _ATTR_NAME.fullmatch(m.group(1)) or not quoted.startswith('"') or "<" in raw or ">" in raw
                or element == "style" or ("\\" in raw and not name.startswith("data-")) or name in seen):
            return None  # a repeated name: the browser keeps the first, the checks would read both
        seen.add(name)
        decoded = html.unescape(raw)
        value = re.sub(r"[\x00-\x20]", "", decoded).lower()
        if name.startswith("on") or _URL_SCHEME.search(value):
            continue  # dropped, the rest of the diagram kept
        if "\\" in decoded and not name.startswith("data-"):
            return None  # an entity can spell a CSS escape (u&#92;72l)
        if name in ("href", "xlink:href") and ("&" in raw or raw != raw.strip()):
            return None  # an entity would hide the reference from the scope check
        if name == "style" and not declarations_ok(raw):
            return None
        if element == "a" and name.endswith("href") and not _LINK.match(value):
            return None  # a link goes to http(s), a fragment or a relative path only
        fetches = _FETCH.search(value) or (name.endswith("href") and element != "a" and not value.startswith("#"))
        if fetches:
            continue
        if not name.startswith("data-") and not _allowed(m.group(1), raw, decoded, names, box, root):
            return None
        kept.append(f' {m.group(1)}="{raw}"')
    if root and any(box.get(k, box.get(v)) != box.get(v) for k, v in (("width", "vw"), ("height", "vh"))):
        return None  # a root size is its viewBox's, so it cannot change the shape the page lays out
    return "".join(kept)


class _Refused(Exception):
    """The markup cannot be inlined safely."""


def _text(text: str, open_names: list[str]) -> str:
    """A text token as kept: outside the root only whitespace (dropped)."""
    if not open_names:
        if text.strip():
            raise _Refused
        return ""
    return text


def _check_tag(name: str, close: bool, open_names: list[str], closed: bool) -> None:
    """Refuse a tag inside a text-only element, outside ``ELEMENTS``, or outside one ``<svg>`` root."""
    if open_names and open_names[-1] in TEXT_ONLY and not (close and name == open_names[-1]):
        raise _Refused
    if name in DROPPED:
        if close:
            raise _Refused  # an end tag for a dropped element that never opened
        return
    if name not in ELEMENTS or closed or (not open_names and (name != "svg" or close)):
        raise _Refused


def sanitise(svg: str) -> tuple[str, bool]:
    """``svg`` sanitised, and whether it has per-part details; ``("", False)`` when refused."""
    parsed = _parse(svg)
    return ("", False) if parsed is None else (parsed.markup, parsed.details)


def _parse(svg: str) -> _Parsed | None:
    try:
        return _sanitise(svg)
    except _Refused:
        return None


def _sanitise(svg: str) -> _Parsed | None:
    out: list[str] = []
    names: list[str] = []
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
        attrs = _safe_attrs(name, m.group("attrs"), names, root=not open_names)
        if attrs is None:
            raise _Refused
        out.append(f"<{m.group('name')}{attrs}{'/' if empty else ''}>")
        if not empty:
            open_names.append(name)
        elif not open_names:
            closed = True
    if not closed or skipping:
        return None
    markup = "".join(out).strip()
    return _Parsed(markup, details, names, re.findall(r"<style>(.*?)</style>", markup, re.S))


# --- CSS: an allow-list. A sheet is read only if it parses whole as the shape the generators
# write, so no tokenizer difference from a browser (bad-url tokens, strings, comments, escapes,
# nesting) can hide a rule: every character outside the shape refuses it.

# Every CSS property a generator writes today; nothing that moves a diagram off its own box.
PROPERTIES = frozenset({"fill", "stroke", "stroke-width", "font-family", "text-anchor",
                        "dominant-baseline", "pointer-events"})
_DARK = "@media (prefers-color-scheme: dark)"  # the one at-rule, as fleet_svg.theme_style writes it
_CSS_WS = "\t\n\r\f "
# One compound selector: an element and/or classes and ids, then at most one of the three
# pseudo-classes a generator uses (the treemap's and doc graph's :hover and :not([attr]) guards).
_COMPOUND = re.compile(r"(?=.)(?:[a-z][a-z0-9_-]*)?(?:[.#][A-Za-z0-9_-]+)*"
                       r"(?::hover|:not\(\[(?:fill|text-anchor)\]\))?", re.A)
_RULE = re.compile(r"[\t\n\r\f ]*([^{}@]*)\{([^{}]*)\}", re.A)
_DECL = re.compile(r"[\t\n\r\f ]*([a-z-]+)[\t\n\r\f ]*:[\t\n\r\f ]*(.*?)[\t\n\r\f ]*", re.S | re.A)
_VALUE = re.compile(r"(?:[A-Za-z0-9 #.,%-]|rgba?\([0-9., %]+\)|url\(#[A-Za-z0-9_-]+\))+", re.A)
_FONT = re.compile(r'(?:[A-Za-z0-9 ,-]|"[A-Za-z0-9 -]*")+', re.A)  # quotes only as a closed pair here


def declarations_ok(text: str) -> bool:
    """Every ``property: value`` in ``text`` is on the allow-list (a ``<style>`` block or a ``style`` attribute)."""
    for decl in text.split(";"):
        if not decl.strip(_CSS_WS):
            continue
        m = _DECL.fullmatch(decl)
        if m is None or m.group(1) not in PROPERTIES:
            return False
        if not (_FONT if m.group(1) == "font-family" else _VALUE).fullmatch(m.group(2)):
            return False
    return True


def css_selectors(css: str, scope: str) -> list[str] | None:
    """Every selector in ``css`` when the whole sheet is on the allow-list under ``scope``, else None.

    The shape: rules ``svg.<scope> part part, ... { declarations }``, each part a ``_COMPOUND``
    and parts joined by single spaces, optionally inside one ``@media (prefers-color-scheme: dark)``
    block at a time."""
    out: list[str] = []
    pos, dark = 0, False
    while True:
        while pos < len(css) and css[pos] in _CSS_WS:
            pos += 1
        if pos == len(css):
            return None if dark else out
        if dark and css[pos] == "}":
            dark, pos = False, pos + 1
            continue
        if not dark and css.startswith(_DARK, pos):
            pos += len(_DARK)
            while pos < len(css) and css[pos] in _CSS_WS:
                pos += 1
            if not css.startswith("{", pos):
                return None
            dark, pos = True, pos + 1
            continue
        m = _RULE.match(css, pos)
        if m is None or not declarations_ok(m.group(2)):
            return None
        for selector in (sel.strip(_CSS_WS) for sel in m.group(1).split(",")):
            parts = selector.split(" ")
            if parts[0] != f"svg.{scope}" or not all(_COMPOUND.fullmatch(part) for part in parts[1:]):
                return None
            out.append(selector)
        pos = m.end()


def root_class(markup: str) -> str | None:
    m = _ROOT_CLASS.match(markup)
    return m.group(1) if m else None


def _scope_complete(parsed: _Parsed, scope: str) -> bool:
    """Every id and ``#`` reference, each read from its own attribute (#1502), and every
    ``url(#id)`` in a sheet is under ``scope``, and every ``<style>`` is on the CSS allow-list
    under it."""
    css_refs = [ref for css in parsed.sheets for ref in _URL_LOCAL.findall(css)]
    return (all(name.startswith(f"{scope}-") for name in parsed.names + css_refs)
            and all(css_selectors(css, scope) is not None for css in parsed.sheets))


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
    scope = root_class(clean) or ""
    parsed = _parse(clean)
    if (parsed is None or parsed.markup != clean or not _SCOPE.fullmatch(scope)
            or not _ROOT_VIEWBOX.match(clean) or not _scope_complete(parsed, scope)
            or any(word in clean.lower() for word in UNSAFE) or _FETCH.search(clean)):
        return None
    return clean
