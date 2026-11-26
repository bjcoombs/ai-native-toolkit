"""Shared SVG helpers for the fleet diagrams (issue #674, parent #672).

The heat map (``lib/fleet/fleet_heatmap.py``) and the fleet diagrams
(``lib/fleet/fleet_diagrams.py`` and its successors) draw static SVG for Markdown
reports: no script, no external font or link, byte-identical output for the
same inputs. This module holds what they share, so no diagram copies it:

- **Escaping.** ``esc`` escapes element text; ``text`` builds a ``<text>``
  element with fixed one-decimal coordinates and an optional ``<title>``
  tooltip, repeated as ``data-label`` so a host page can read it without the title. The heat map passes explicit ``fill`` colours; the themed diagrams
  pass a ``cls`` from ``THEME`` instead, so dark mode can recolour them.
- **Sizing without font metrics.** ``text_width`` estimates a label's width
  from its character count (``CHAR_W`` per character at 12px, scaled by font
  size); ``truncate`` cuts a label to a character budget with an ellipsis.
- **Scales.** ``LinearScale`` maps a domain onto a pixel range and gives
  evenly stepped ticks; ``nice_ceiling`` rounds an axis maximum up to 1, 2 or
  5 times a power of ten.
- **Level ramp.** ``LEVEL_RAMP`` (the YlGn growth palette, pale to dark), ``ramp_step`` (a level's step by
  its share of the row's top level) and ``STEP_TEXT`` (the text colour that
  reads on each step): the heat map colours a level with it.
- **Hatching.** ``hatch_pattern`` is the diagonal hatch that marks unknown,
  never zero. The heat map keeps its literal white-and-grey pattern; the
  themed diagrams use ``THEMED_HATCH``, coloured by class.
- **Label spacing.** ``spread_vertical`` pushes end labels (trend and slope charts) apart to a
  minimum gap, keeping their order. ``overlaps`` is the box test both use.
- **Theme.** ``theme_style`` writes the embedded ``<style>``: one class per
  role in ``THEME`` with its light colour, the dark colour under
  ``@media (prefers-color-scheme: dark)``. An SVG embedded as an image follows
  the viewer's colour scheme, so every themed diagram also paints a full
  canvas ``bg`` rect. The only ``text`` rule sets ``font-family``, which no
  element carries as an attribute, so the stylesheet never overrides a text's
  own presentation attributes (``tests/svg_cascade.py``).
- **Data attributes.** ``data_attrs`` writes a part's values as ``data-*`` attributes beside
  its ``<title>`` (#1360): plain values, quoted, and a value of None (not measured) left out,
  never written as 0. A diagram inlined into a host page or opened with ``<object>`` shows
  its details through them; ``templates/diagram-tooltip.js`` reads them.
- **Document.** ``document`` wraps a body in the XML prolog, the root
  ``<svg>`` with its ``viewBox``, ``<title>``, ``<desc>``, style and defs, then
  scopes it with ``lib.svg_scope.scope_svg``: a root class, every selector under
  it and every id prefixed, so diagrams inlined on one page never restyle each
  other (#1358). The scope is a required argument, so no diagram ships unscoped.

Stdlib only; numbers are formatted with fixed precision; nothing iterates a set.

Shared SVG helpers for every fleet diagram (#674; the heat map, the
shared-tooling map, and the slope and trend charts of #675/#676). The API:

| Helper | Use |
|---|---|
| `esc(s)` | Escape element text (`&`, `<`, `>`). |
| `text(x, y, s, *, size, fill, anchor, weight, extra, tooltip, cls)` | One `<text>`; one-decimal coordinates. Pass `fill` (a colour, the heat map) or `cls` (a `THEME` role, themed diagrams), and `tooltip` for a `<title>` (also written as `data-label`). |
| `data_attrs(values)` | ` data-<key>="<value>"` per entry, escaped; a None value (not measured) is left out. |
| `text_width(s, size)`, `text_box(x, y, s, size, anchor)`, `truncate(s, limit, keep_tail)` | Sizing by character count (`CHAR_W` at 12px), no font metrics. |
| `LinearScale(lo, hi, start, end)`, `.ticks(n)`, `nice_ceiling(v)`, `tick_label(v)` | Axes: a linear map (y runs `end` < `start`), even ticks, a 1/2/5 x 10^k axis top. |
| `hatch_pattern(id, background, stroke, width, classes)`, `THEMED_HATCH`, `HATCH_FILL` | The diagonal hatch for unknown, never zero; themed diagrams put `THEMED_HATCH` in the defs and fill with `HATCH_FILL`. |
| `LEVEL_RAMP`, `ramp_step(level, top)`, `STEP_TEXT` | The level ramp (#675 moved it here from the heat map): pale to dark by a level's share of its row's top level, and the fixed text colour that reads on each step. The heat map colours a level with it. |
| `hatched_note(x, y, lines)` | The "unknown" key: a hatched swatch with wrapped lines naming what is unknown. |
| `spread_vertical(ys, min_gap)` | End labels (trend, slope) pushed apart to `min_gap`, order kept. |
| `overlaps(a, b)`, `Box` | The box test. |
| `THEME`, `theme_style(extra)` | Role -> (light, dark) colours (plus a diagram's `extra` roles, such as the trend's series colours) as `t-`/`f-` (fill) and `s-` (stroke) classes, dark under `prefers-color-scheme`. The only `text` rule sets `font-family`, so it never overrides a text's attributes (`tests/svg_cascade.py`). |
| `document(w, h, title, desc, body, *, scope, style, defs, background)` | The whole file: prolog, `viewBox`, `<title>`, `<desc>`, style, defs and a full-canvas background (`f-bg` unless a colour is given), scoped under `scope` (`lib/svg_scope.py`). |

No script, no external font or link; fixed number formatting. Tests: `tests/test_fleet_diagrams.py`.
"""
from __future__ import annotations

import html
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from lib.svg_scope import scope_svg

CHAR_W = 7.0  # average glyph advance at 12px, used in place of font metrics
FONT_STACK = '-apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif'

# role -> (light, dark). Level colours follow the heat map's pale-to-dark ramp.
THEME: dict[str, tuple[str, str]] = {
    "bg": ("#ffffff", "#1a201e"),
    "fg": ("#1d2321", "#e6ebe8"),
    "muted": ("#5d6763", "#9aa5a0"),
    "rule": ("#d9dedb", "#2c3532"),
    "accent": ("#1f6f6b", "#5fb8b1"),
    "bad": ("#b33a2e", "#ec7a6c"),
    "warn": ("#b7791f", "#e0a84a"),
    "unk": ("#9aa39f", "#5f6a66"),
}
THEMED_HATCH_ID = "fleet-hatch"

# The level ramp (the heat map): a growth palette, ColorBrewer YlGn, 9 classes
# (colorbrewer2.org, colour-blind safe), pale yellow to deep green, so a higher level reads
# as growth rather than danger (#743); fixed colours that read on both backgrounds.
LEVEL_RAMP = ("#ffffe5", "#f7fcb9", "#d9f0a3", "#addd8e", "#78c679",
              "#41ab5d", "#238443", "#006837", "#004529")
TOP_STEP = len(LEVEL_RAMP) - 1
TEXT_DARK = "#1a1a1a"
TEXT_LIGHT = "#ffffff"
# Text colour per ramp step: dark text while it out-contrasts white (WCAG ratio).
STEP_TEXT = (TEXT_DARK,) * 6 + (TEXT_LIGHT,) * 3


def ramp_step(level: int, top: int | None) -> int:
    """The ramp step for a whole level: its share of ``top``, else the level itself."""
    if top is None or top <= 0:
        return max(0, min(level, TOP_STEP))
    return max(0, min((level * 2 * TOP_STEP + top) // (2 * top), TOP_STEP))


def esc(content: str) -> str:
    """Element text, escaped (``&``, ``<``, ``>``)."""
    return html.escape(content, quote=False)


def text(x: float, y: float, content: str, *, size: float = 12, fill: str | None = None,
         anchor: str = "start", weight: str | None = None, extra: str = "",
         tooltip: str | None = None, cls: str | None = None) -> str:
    """One ``<text>`` element; ``fill`` is a colour, ``cls`` a ``THEME`` role (``t-<role>``)."""
    paint = f' fill="{fill}"' if fill is not None else ""
    klass = f' class="t-{cls}"' if cls is not None else ""
    bold = f' font-weight="{weight}"' if weight else ""
    hover = "" if tooltip is None else f"<title>{esc(tooltip)}</title>"
    label = "" if tooltip is None else data_attrs({"label": tooltip})
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}"{paint} '
            f'text-anchor="{anchor}"{klass}{bold}{extra}{label}>{esc(content)}{hover}</text>')


def data_attrs(values: Mapping[str, str | int | None]) -> str:
    """`` data-<key>="<value>"`` per entry, in the given order, the value escaped for an
    attribute. A None value is one the run did not measure: it is left out, never written as 0."""
    return "".join(f' data-{key}="{html.escape(str(value))}"'
                   for key, value in values.items() if value is not None)


def text_width(content: str, size: float = 12) -> float:
    """Estimated rendered width: ``CHAR_W`` per character at 12px, scaled by ``size``."""
    return len(content) * CHAR_W * size / 12


def truncate(content: str, limit: int, keep_tail: bool = False) -> str:
    """At most ``limit`` characters, an ellipsis marking the cut."""
    if len(content) <= limit:
        return content
    return "…" + content[-(limit - 1):] if keep_tail else content[:limit - 1] + "…"


def nice_ceiling(value: float) -> float:
    """The smallest 1, 2 or 5 times a power of ten that is at least ``value`` (1 for <= 0)."""
    if value <= 0:
        return 1.0
    power = 10.0 ** math.floor(math.log10(value))
    for step in (1, 2, 5, 10):
        if step * power >= value:
            return step * power
    return 10 * power  # pragma: no cover - the loop always returns


@dataclass(frozen=True)
class LinearScale:
    """Maps ``[lo, hi]`` onto ``[start, end]`` (``end`` may be below ``start``, as for y)."""

    lo: float
    hi: float
    start: float
    end: float

    def __call__(self, value: float) -> float:
        span = self.hi - self.lo
        share = 0.0 if span == 0 else (value - self.lo) / span
        return self.start + (self.end - self.start) * share

    def ticks(self, count: int) -> list[float]:
        """``count + 1`` evenly spaced values from ``lo`` to ``hi``."""
        return [self.lo + (self.hi - self.lo) * i / count for i in range(count + 1)]


def tick_label(value: float) -> str:
    """A tick value with no trailing ``.0`` (``40``, ``0.25``)."""
    return f"{value:.0f}" if value == int(value) else f"{value:g}"


def hatch_pattern(pattern_id: str, background: str, stroke: str, stroke_width: str = "1.6",
                  classes: tuple[str, str] | None = None) -> str:
    """A 6px diagonal hatch ``<pattern>``: colours as attributes, or ``classes`` for the theme."""
    if classes is None:
        back, line = f'fill="{background}"', f'stroke="{stroke}"'
    else:
        back, line = f'class="{classes[0]}"', f'class="{classes[1]}"'
    return (f'<pattern id="{pattern_id}" width="6" height="6" patternUnits="userSpaceOnUse" '
            f'patternTransform="rotate(45)"><rect width="6" height="6" {back}/>'
            f'<line x1="0" y1="0" x2="0" y2="6" {line} stroke-width="{stroke_width}"/></pattern>')


THEMED_HATCH = hatch_pattern(THEMED_HATCH_ID, "", "", "2.4", classes=("f-bg", "s-unk"))
HATCH_FILL = f"url(#{THEMED_HATCH_ID})"


def theme_style(extra: Mapping[str, tuple[str, str]] | None = None) -> str:
    """The embedded ``<style>``: ``t-``/``f-`` (fill) and ``s-`` (stroke) classes per role in
    ``THEME``, plus any ``extra`` roles a diagram adds (such as series colours)."""
    roles = {**THEME, **(extra or {})}

    def rules(index: int) -> str:
        return "".join(f".t-{role},.f-{role}{{fill:{colours[index]}}}.s-{role}{{stroke:{colours[index]}}}"
                       for role, colours in roles.items())
    return (f"<style>text {{ font-family: {FONT_STACK}; }}{rules(0)}"
            f"@media (prefers-color-scheme: dark){{{rules(1)}}}</style>")


@dataclass(frozen=True)
class Box:
    """An axis-aligned box; ``x``/``y`` is the top-left corner."""

    x: float
    y: float
    w: float
    h: float


def overlaps(a: Box, b: Box) -> bool:
    """True when the boxes share any area (touching edges do not overlap)."""
    return a.x < b.x + b.w and b.x < a.x + a.w and a.y < b.y + b.h and b.y < a.y + a.h


def text_box(x: float, y: float, content: str, size: float = 12, anchor: str = "start") -> Box:
    """The estimated box of a ``<text>`` at baseline ``(x, y)``."""
    width = text_width(content, size)
    left = {"start": x, "middle": x - width / 2}.get(anchor, x - width)
    return Box(left, y - size, width, size * 1.2)


def hatched_note(x: float, y: float, lines: Sequence[str], *, size: float = 11.5,
                 line_h: float = 16.0) -> tuple[list[str], float]:
    """A hatched swatch then ``lines`` of text beside it, the shared "unknown" key.

    Returns the elements and the height used. Needs ``THEMED_HATCH`` in the defs.
    """
    parts = [f'<rect x="{x:.1f}" y="{y + 2:.1f}" width="14.0" height="14.0" fill="{HATCH_FILL}" '
             f'class="s-muted" stroke-width="1"/>']
    for index, line in enumerate(lines):
        parts.append(text(x + 22, y + 13 + index * line_h, line, size=size, cls="fg"))
    return parts, len(lines) * line_h


def spread_vertical(ys: Sequence[float], min_gap: float) -> list[float]:
    """Label baselines at least ``min_gap`` apart, in the order of ``ys``, each moved down only.

    Returns positions for the inputs in their given order; ties keep input order.
    """
    order = sorted(range(len(ys)), key=lambda i: (ys[i], i))
    out = list(ys)
    last = -math.inf
    for i in order:
        out[i] = max(ys[i], last + min_gap)
        last = out[i]
    return out


def document(width: float, height: float, title: str, desc: str, body: Sequence[str], *,
             scope: str, style: str | None = None, defs: str = "",
             background: str | None = None) -> str:
    """A whole SVG document under the diagram's ``scope`` (a ``lib.svg_scope.SCOPES`` value).
    ``style`` defaults to ``theme_style()``; ``background`` is a fill colour for the canvas
    rect, else the themed ``bg`` class."""
    w, h = f"{width:.0f}", f"{height:.0f}"
    canvas = (f'fill="{background}"' if background is not None else 'class="f-bg"')
    head = [
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'width="{w}" height="{h}" role="img">',
        f"<title>{esc(title)}</title>",
        f"<desc>{html.escape(desc)}</desc>",
        style if style is not None else theme_style(),
        f"<defs>{defs}</defs>",
        f'<rect x="0" y="0" width="{w}" height="{h}" {canvas}/>',
    ]
    return scope_svg("\n".join([*head, *body, "</svg>", ""]), scope)
