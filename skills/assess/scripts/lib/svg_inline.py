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
    is not plain ASCII (``data-x\x0b``); an ``id``, ``class``, ``href`` or ``style`` name not in
    lowercase (``ID=``); an ``&`` in an ``href`` that is not a script URL (an entity hides the reference); a ``\`` in any
    attribute but a ``data-*`` one; an ``<a>`` ``href`` that is not http(s), a fragment or a
    relative path.
  - Whitespace: only HTML's own (tab, line feed, form feed, carriage return, space) separates a
    tag's parts; a tag split on any other (``\x0b``, ``\xa0``) is unreadable, so refused. Each
    kept attribute is written again as `` name="value"``, and an end tag without attributes.
  - CSS, in a ``<style>``'s text or a ``style`` attribute: an ``&`` or ``\`` (an entity or CSS
    escape could spell ``url(`` or ``@import``); a comment; a string that holds a brace,
    semicolon or line break, or is not closed on its line; an unbalanced brace; a rule nested in
    a style rule; an at-rule but ``@media`` (``@layer a;`` would hide the next selector); a
    selector leaving the diagram through a sibling combinator (``svg.<scope> ~ *``); a
    ``position`` or ``z-index`` (a first guard only: the page that inlines the diagram
    clips it to its own box, as the fleet page's ``contain: paint`` does); and in a ``style``
    attribute any quote at all.
- **Scoped.** A root that already carries a class is the generator's scope (#1449): kept when it
  is complete (every id, reference and style selector under it), None otherwise. A root without a
  class is scoped here under ``scope``. Every selector is checked against the scope after
  scoping, on both paths.
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
_STYLE_RISK = re.compile(r"position|z-index", re.I)  # a diagram must not cover the page
_URL_SCHEME = re.compile(r"(?:java|vb)script:")
# A CSS comment, or a brace, semicolon or line break inside a quoted string: either would make
# the brace count in css_selectors disagree with the browser's reading of the sheet.
_HIDES_BRACES = re.compile(r"""/\*|"[^"]*[{};\n][^"]*"|'[^']*[{};\n][^']*'""")


def _css_misread(css: str) -> bool:
    """CSS whose braces a browser could count differently from ``_blocks``: a comment, a string
    that holds a brace, semicolon or line break or is never closed on its line, a ``}`` with no
    ``{`` or a ``{`` never closed, or a position or z-index."""
    if _HIDES_BRACES.search(css) or _STYLE_RISK.search(css):
        return True
    depth, pos = 0, 0
    while pos < len(css):
        char = css[pos]
        if char in "\"'":
            close = css.find(char, pos + 1)
            if close < 0 or re.search(r"[\n\r\f{};]", css[pos + 1:close]):
                return True
            pos = close
        elif char in "{}":
            depth += 1 if char == "{" else -1
            if depth < 0:
                return True
        pos += 1
    return depth != 0
_FETCH = re.compile(r"""url\(\s*['"]?(?!#)""", re.I)  # a url() that is not #-local
_ROOT_CLASS = re.compile(r'<svg\b[^>]*?\sclass="([^"]*)"')
_ID = re.compile(r'\sid="([^"]*)"')
_REF = re.compile(r'url\(#([^)]+)\)|\s(?:xlink:)?href="#([^"]*)"')


def _safe_attrs(element: str, attrs: str) -> str | None:
    """``attrs`` without event handlers, script URLs or fetches, each written again as
    `` name="value"``; None to refuse (see the module docstring)."""
    kept = []
    for m in _ATTR_PARTS.finditer(attrs):
        quoted, name = m.group(2) or "", m.group(1).lower()
        raw = quoted[1:-1]
        if (not _ATTR_NAME.fullmatch(m.group(1)) or (name in CHECKED_ATTRS and m.group(1) != name)
                or not quoted.startswith('"') or "<" in raw or ">" in raw or element == "style"
                or ("\\" in raw and not name.startswith("data-"))):
            return None
        value = re.sub(r"[\x00-\x20]", "", html.unescape(raw)).lower()
        if name.startswith("on") or _URL_SCHEME.search(value):
            continue  # dropped, the rest of the diagram kept
        if name in ("href", "xlink:href") and "&" in raw:
            return None  # an entity would hide the reference from the scope check
        if name == "style" and (re.search(r"[&'\"]", raw) or _css_misread(raw)):
            return None
        if element == "a" and name.endswith("href") and not _LINK.match(value):
            return None  # a link goes to http(s), a fragment or a relative path only
        fetches = _FETCH.search(value) or (name.endswith("href") and element != "a" and not value.startswith("#"))
        if fetches:
            continue
        kept.append(f' {m.group(1)}="{raw}"')
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
        if close:
            raise _Refused  # an end tag for a dropped element that never opened
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
    if (_css_misread(styles) or _nested_rule(styles) or not _scope_complete(clean, root_class(clean) or "")
            or sanitise(clean)[0] != clean or any(word in clean.lower() for word in UNSAFE)
            or _FETCH.search(clean) or "\\" in styles or re.search(r"@(?!media\b)", styles)
            or any(re.match(r"svg\.[a-z0-9-]+\s*[~+]", sel) for sel in css_selectors(styles))):
        return None
    return clean
