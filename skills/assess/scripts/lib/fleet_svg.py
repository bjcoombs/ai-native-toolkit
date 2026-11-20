"""Shared SVG helpers for the fleet diagrams (issue #674, parent #672).

The heat map (``lib/fleet_heatmap.py``) and the fleet diagrams
(``lib/fleet_diagrams.py`` and its successors) draw static SVG for Markdown
reports: no script, no external font or link, byte-identical output for the
same inputs. This module holds what they share, so no diagram copies it:

- **Escaping.** ``esc`` escapes element text; ``text`` builds a ``<text>``
  element with fixed one-decimal coordinates and an optional ``<title>``
  tooltip. The heat map passes explicit ``fill`` colours; the themed diagrams
  pass a ``cls`` from ``THEME`` instead, so dark mode can recolour them.
- **Sizing without font metrics.** ``text_width`` estimates a label's width
  from its character count (``CHAR_W`` per character at 12px, scaled by font
  size); ``truncate`` cuts a label to a character budget with an ellipsis.
- **Scales.** ``LinearScale`` maps a domain onto a pixel range and gives
  evenly stepped ticks; ``nice_ceiling`` rounds an axis maximum up to 1, 2 or
  5 times a power of ten.
- **Hatching.** ``hatch_pattern`` is the diagonal hatch that marks unknown,
  never zero. The heat map keeps its literal white-and-grey pattern; the
  themed diagrams use ``THEMED_HATCH``, coloured by class.
- **Label spacing.** ``place_labels`` puts each point label at the first
  candidate offset that collides with no placed label or point (in a fixed
  order), returning the placed boxes, or ``None`` for a label that fits
  nowhere, so a renderer and its overlap test read the same layout.
  ``spread_vertical`` pushes end labels (trend and slope charts) apart to a
  minimum gap, keeping their order. ``overlaps`` is the box test both use.
- **Theme.** ``theme_style`` writes the embedded ``<style>``: one class per
  role in ``THEME`` with its light colour, the dark colour under
  ``@media (prefers-color-scheme: dark)``. An SVG embedded as an image follows
  the viewer's colour scheme, so every themed diagram also paints a full
  canvas ``bg`` rect. The only ``text`` rule sets ``font-family``, which no
  element carries as an attribute, so the stylesheet never overrides a text's
  own presentation attributes (``tests/svg_cascade.py``).
- **Document.** ``document`` wraps a body in the XML prolog, the root
  ``<svg>`` with its ``viewBox``, ``<title>``, ``<desc>``, style and defs.

Stdlib only; numbers are formatted with fixed precision; nothing iterates a set.
"""
from __future__ import annotations

import html
import math
from collections.abc import Sequence
from dataclasses import dataclass

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
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}"{paint} '
            f'text-anchor="{anchor}"{klass}{bold}{extra}>{esc(content)}{hover}</text>')


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


def theme_style() -> str:
    """The embedded ``<style>``: ``t-``/``f-`` (fill) and ``s-`` (stroke) classes per role."""
    def rules(index: int) -> str:
        return "".join(f".t-{role},.f-{role}{{fill:{colours[index]}}}.s-{role}{{stroke:{colours[index]}}}"
                       for role, colours in THEME.items())
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


@dataclass(frozen=True)
class PlacedLabel:
    """Where a point label landed: the text anchor point, its anchor, and its box."""

    x: float
    y: float
    anchor: str  # "start" | "end"
    box: Box


# (dx, dy, anchor) tried in order: right, right-below, left, left-below, above, below.
LABEL_OFFSETS: tuple[tuple[float, float, str], ...] = (
    (9.0, -7.0, "start"), (9.0, 14.0, "start"), (-9.0, -7.0, "end"),
    (-9.0, 14.0, "end"), (9.0, -16.0, "start"), (9.0, 23.0, "start"),
)


def place_labels(points: Sequence[tuple[float, float, str]], bounds: Box, *, size: float = 11.5,
                 radius: float = 6.5) -> list[PlacedLabel | None]:
    """A label per ``(x, y, label)`` point, placed greedily in input order.

    Each label takes the first ``LABEL_OFFSETS`` candidate whose box stays inside
    ``bounds`` and overlaps no point disc (as a box) and no label placed before
    it; a label that fits nowhere is ``None`` and the caller names it elsewhere.
    The caller fixes the input order, so the layout is deterministic.
    """
    discs = [Box(x - radius, y - radius, 2 * radius, 2 * radius) for x, y, _ in points]
    height = size * 1.2
    placed: list[PlacedLabel | None] = []
    taken: list[Box] = []
    for x, y, label in points:
        width = text_width(label, size)
        choice: PlacedLabel | None = None
        for dx, dy, anchor in LABEL_OFFSETS:
            left = x + dx if anchor == "start" else x + dx - width
            box = Box(left, y + dy - size, width, height)
            inside = (box.x >= bounds.x and box.y >= bounds.y and box.x + box.w <= bounds.x + bounds.w
                      and box.y + box.h <= bounds.y + bounds.h)
            if inside and not any(overlaps(box, other) for other in [*discs, *taken]):
                choice = PlacedLabel(x + dx, y + dy, anchor, box)
                break
        placed.append(choice)
        if choice is not None:
            taken.append(choice.box)
    return placed


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
             style: str | None = None, defs: str = "", background: str | None = None) -> str:
    """A whole SVG document. ``style`` defaults to ``theme_style()``; ``background`` is a fill
    colour for the canvas rect, else the themed ``bg`` class."""
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
    return "\n".join([*head, *body, "</svg>", ""])
