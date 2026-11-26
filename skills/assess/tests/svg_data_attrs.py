"""Static check that a diagram's per-part ``<title>`` details are also data attributes (#1360).

A host page that inlines a diagram, or opens it with ``<object>``, reads each part's details
from its ``data-*`` attributes instead of parsing the ``<title>`` text. ``title_data_problems``
reads one SVG as text, with no DOM, and for every element other than the root ``<svg>`` whose
first child is a ``<title>`` (after any text, as a truncated ``<text>`` label has) checks:

- **Carried.** The element has at least one data attribute outside ``CODE_KEYS``.
- **Matches.** Each such value is shown in the title: a number (a signed decimal) equals a
  number in the title to the title's printed precision, with thousands separators read
  through; any other value is a substring of the title.
- **Complete.** Every number the title prints is one an attribute carries, once the string
  values are taken out of the title, so a value shown only in the title fails.

``CODE_KEYS`` hold machine codes whose title shows a word for them, not the code itself. They
come in two kinds. ``ID_KEYS`` are model ids (``data-row``, ``data-condition``) that a readable
attribute on the same part names; the tooltip helper never lists them. ``STATE_KEYS`` record
a state (``data-category="unmet"``, ``data-cell="not_measured"``, ``data-status="orphan"``);
the helper lists one unless the part carries its readable twin (``data-category-label``,
``data-tag-title``), because for some parts the code is the only record of the state once the
helper mutes the ``<title>``. ``EXTRA_KEYS`` hold values a part carries beyond
its title (the package map's ``data-bypass="0"``, where the title names no bypass). A value
the root ``<svg>`` carries once for every part (the treemap's ``data-churn-window``) counts
as carried for each part's title too.
"""
from __future__ import annotations

import html
import re

# Model ids a readable attribute on the same part names: the tooltip helper never lists them.
ID_KEYS = frozenset({"condition", "row"})
# State codes: listed by the helper unless the part carries data-<key>-label or -title.
STATE_KEYS = frozenset({"category", "state", "cell", "mark", "tag", "status", "review-bot",
                        "behind", "outline"})
# Machine codes: the title shows a word for them, never the code.
CODE_KEYS = ID_KEYS | STATE_KEYS

# Values a part carries beyond what its title prints.
EXTRA_KEYS = frozenset({"bypass"})

_ROOT = re.compile(r"<svg\b([^>]*)>")
_TITLED = re.compile(r"<(\w+)((?:\s[^<>]*?)?)/?>([^<]*)<title>([^<]*)</title>")
_DATA = re.compile(r'\sdata-([\w-]+)="([^"]*)"')
_NUMBER = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")
_NUMERIC = re.compile(r"[-+]?\d+(?:\.\d+)?")


def titled_parts(svg: str) -> list[tuple[str, dict[str, str], str]]:
    """``(tag, data attributes, title)`` for every element whose first child is a ``<title>``,
    the root ``<svg>`` left out; values and title unescaped."""
    parts = []
    for tag, attrs, _text, title in _TITLED.findall(svg):
        if tag == "svg":
            continue
        data = {key: html.unescape(value) for key, value in _DATA.findall(attrs)}
        parts.append((tag, data, html.unescape(title)))
    return parts


def _decimals(token: str) -> int:
    return len(token.split(".", 1)[1]) if "." in token else 0


def _same_number(value: str, token: str) -> bool:
    """``value`` printed to ``token``'s precision reads as ``token`` (sign and commas aside)."""
    shown = token.replace(",", "")
    places = _decimals(shown)
    return abs(float(value)) == abs(round(float(shown), places)) or \
        f"{abs(float(value)):.{places}f}" == shown.lstrip("+-")


def part_problems(data: dict[str, str], title: str, root: dict[str, str] | None = None) -> list[str]:
    """What breaks the carried, matches and complete rules for one part; ``root`` is the root
    ``<svg>``'s data attributes, values every part shares."""
    shown = {key: value for key, value in data.items() if key not in CODE_KEYS | EXTRA_KEYS}
    numbers = [value for key, value in data.items() if key in EXTRA_KEYS and _NUMERIC.fullmatch(value)]
    if not shown:
        return [f"no data attribute beside title {title!r}"]
    problems = []
    rest = title
    for value in (root or {}).values():
        rest = rest.replace(value, " ")
    for key, value in sorted(shown.items(), key=lambda kv: -len(kv[1])):
        if _NUMERIC.fullmatch(value):
            numbers.append(value)
            if not any(_same_number(value, token) for token in _NUMBER.findall(title)):
                problems.append(f"data-{key}={value!r} is not a number in title {title!r}")
        elif value not in title:
            problems.append(f"data-{key}={value!r} is not in title {title!r}")
        else:
            rest = rest.replace(value, " ")
    for token in _NUMBER.findall(rest):
        if not any(_same_number(value, token) for value in numbers):
            problems.append(f"title {title!r} shows {token!r}, which no data attribute carries")
    return problems


def title_data_problems(svg: str) -> list[str]:
    """Every rule broken by every titled part of ``svg``; empty when each carries its title."""
    match = _ROOT.search(svg)
    root = {key: html.unescape(value) for key, value in _DATA.findall(match.group(1))} if match else {}
    return [problem for _tag, data, title in titled_parts(svg)
            for problem in part_problems(data, title, root)]
