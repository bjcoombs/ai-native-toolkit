"""Structural check that an SVG's ``<style>`` never overrides a ``<text>``'s own
presentation attributes.

In the CSS cascade an author stylesheet rule beats an SVG presentation
attribute, whatever the selector's specificity. A bare ``text { text-anchor:
middle }`` therefore centres a ``<text text-anchor="start">`` in a browser and
in GitHub's image proxy alike, which is how the heatmap's survivor legend
rendered with its left half cut off. A renderer keeps a stylesheet default
safe by guarding it with ``:not([attr])`` so it reaches only the elements that
lack the attribute.

``overridden_text_attributes`` parses the stylesheet and returns every
(text, property) pair where a matching rule sets a property the element also
carries as an attribute. Only ``text`` and ``text:not([attr])...`` selectors are
understood; any other selector naming ``text``, and any selector using the
universal ``*`` (which reaches ``<text>`` as well), raises, so a new stylesheet
shape fails loudly instead of slipping past the check.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

_SVG_NS = "{http://www.w3.org/2000/svg}"
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_TEXT_SELECTOR = re.compile(r"text((?::not\(\[[\w-]+\]\))*)")
_NOT_ATTR = re.compile(r":not\(\[([\w-]+)\]\)")


def _text_rules(css: str) -> list[tuple[list[str], set[str]]]:
    """(required-absent attributes, properties) for each rule that targets
    ``<text>``."""
    rules = []
    for selectors, body in _RULE.findall(css):
        props = {d.split(":", 1)[0].strip()
                 for d in body.split(";") if ":" in d}
        for sel in (s.strip() for s in selectors.split(",")):
            if "*" in sel:
                raise ValueError(f"universal selector reaches <text>: {sel!r}")
            if not re.search(r"\btext\b", sel):
                continue
            m = _TEXT_SELECTOR.fullmatch(sel)
            if m is None:
                raise ValueError(f"unrecognised <text> selector: {sel!r}")
            rules.append((_NOT_ATTR.findall(m.group(1)), props))
    return rules


def overridden_text_attributes(svg: str) -> list[tuple[str, str]]:
    """Every (text content, property) a stylesheet rule overrides."""
    root = ET.fromstring(svg.encode("utf-8"))
    css = "".join(el.text or "" for el in root.iter(f"{_SVG_NS}style"))
    rules = _text_rules(css)
    hits = []
    for el in root.iter(f"{_SVG_NS}text"):
        for absent, props in rules:
            if any(a in el.attrib for a in absent):
                continue
            for prop in sorted(props & set(el.attrib)):
                hits.append(("".join(el.itertext()), prop))
    return hits
