"""The /assess diagrams and the report section that links them (issue #942, parent #940).

``build_diagrams(ctx, model_rows=None)`` turns one run context into a list of
``AssessDiagram`` entries (``lib/run_context_types_diagrams.py``), each an SVG or the
one-line reason it was not drawn, in report order; it never raises (a renderer that
raises becomes a "the renderer raised X" reason). ``write_diagrams`` writes the drawn
SVGs into the artifact directory beside ``run-context.json`` and removes any diagram
file (of ``files``, by default ``DIAGRAM_FILES``; ``lib/assess_diagrams_review.py``
passes its own, #943) this run did not draw, so a stale picture never sits beside new data; a symlink in
a diagram's place is removed, never written through, and a path that cannot be written
is named on stderr and skipped, so writing never stops a run either.
``run_publish.publish_run`` calls it, and ``assess_core.run_opt_in_mutation`` again
after it recounts the ``level`` block. ``diagrams_section`` is the Markdown the
deterministic report embeds, one ``## `` section per diagram, each opening with an
``In place:`` line (``lib/vocabulary.IN_PLACE``, #743), then the relative clickable
image link and the caption, or ``Not drawn:`` and the reason. The publish step and the
report (``assess_report.py``) both call ``build_diagrams`` on the same context; given the
artifact directory, the section says ``Not drawn:`` for a drawn diagram whose file is not
there (the write was skipped, or the context predates the diagram) instead of linking it.

Diagrams, in report order:

- **Step ladder** (``step-ladder.svg``, #1102): the model's Steps as rungs, the gate
  between each pair, "You are here" on the run's Step and the next gate highlighted;
  drawn by ``lib/assess_diagrams_ladder.py``, which documents it.
- **Evidence waffle** (``evidence-waffle.svg``): one square per condition of the run's
  ``level`` block (``lib/scoring/level_tally.py``), grouped by maturity-model row in model
  order, coloured by state and grade (``CATEGORIES``). Met conditions read verified,
  measured or listed (counted or not, as the tally reads them); unmet, declined, not
  applicable (an empty outline) and not measured (hatched, its reason in the key)
  follow. The verified and measured squares together are ``tally.met``, the N of the
  headline's catalogue coverage, and the caption says they count toward catalogue
  coverage: "level" is kept for a column, as ``docs/glossary.md`` defines it (#1102). A hazard
  condition (``polarity: "hazard"``, #843) is never counted: met draws as ``hazard``
  (the warn colour), unmet as ``hazard_absent`` (a warn outline); the key lists the two
  only when a square uses them. A "Level N" header heads each column, and the square at
  each Step gate's level (``assess_diagrams_ladder.gate_conditions``) is outlined, with a
  "Outlined: a Step gate" key entry when any is drawn. Each square carries what its
  ``<title>`` shows as data attributes (#1360): ``data-practice``, ``data-level``,
  ``data-category-label`` and, when not measured, ``data-reason``.

A new diagram adds its ``FILE`` and heading to ``HEADINGS``, a builder returning an
``AssessDiagram``, and an entry in ``build_diagrams``; it draws its cells, legend and
theme with the shared helpers here (``cell``, a square in a category's look;
``legend``, swatches with labels and indented notes; ``wrap``, caption lines by
character budget; ``style``, ``fleet_svg.theme_style`` plus ``ROLES``) over
``lib/fleet/fleet_svg.py``. Deterministic: model order, fixed formatting, no date, no set
iteration; two renders of one context are byte-identical. Captions name no layer
number: row labels drop a model title's ``Layer N:`` prefix (``row_label``).

Golden renders: ``tests/fixtures/assess_diagrams/`` (``make regen``, or
``uv run --locked python tests/assess_diagrams_fixture.py`` from ``skills/assess``).
Tests: ``tests/test_assess_diagrams.py``.
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import lib.fleet.fleet_svg as svg
from lib.svg_scope import SCOPES
from lib.assess_diagrams_ladder import (LADDER_FILE, LADDER_TITLE, gate_conditions, ladder_caption,
                                        ladder_in_place, render_ladder)
from lib.scoring.headline import reason_word
from lib.scoring.level_result_types import ConditionResult, LevelResult
from lib.scoring.level_tally import default_model
from lib.model_change import CUSTOM_NOT_DRAWN, custom_run, model_for_level
from lib.scoring.levels import StepDef
from lib.run_context_types_diagrams import AssessDiagram, WaffleSquare
from lib.vocabulary import IN_PLACE

WAFFLE_FILE = "evidence-waffle.svg"
# Each diagram's report heading, by file: one source for a drawn, skipped or raised diagram.
HEADINGS: dict[str, str] = {
    LADDER_FILE: LADDER_TITLE,
    WAFFLE_FILE: "Evidence behind the Step",
}
DIAGRAM_FILES = tuple(HEADINGS)

# Waffle categories in legend order: (key, legend label).
CATEGORIES: tuple[tuple[str, str], ...] = (
    ("verified", "Met, verified by a run or the platform"),
    ("measured", "Met, measured from the code or its history"),
    ("listed", "Met, listed from config or a declaration, not counted"),
    ("unmet", "Not met yet"),
    ("declined", "Declined"),
    ("not_applicable", "Not applicable"),
    ("not_measured", "Not measured"),
    ("hazard", "Hazard present, never counted"),
    ("hazard_absent", "Hazard absent, never counted"),
)
LABELS = dict(CATEGORIES)
# Shown in the key only when a square uses them: no shipped condition is a hazard before #843.
OPTIONAL = ("hazard", "hazard_absent")
COUNTED = ("verified", "measured")

# Category colour roles, (light, dark), added to the fleet theme. Declined has its own
# colour (#942 decision); ``DECLINED_COLOURS`` is the pair a DECLINED badge can reuse.
DECLINED_COLOURS = ("#7b4fa6", "#b892dd")
ROLES: dict[str, tuple[str, str]] = {
    "ev-verified": ("#00635d", "#4fc1b5"),
    "ev-measured": ("#3f8f89", "#2e9c94"),
    "ev-listed": ("#a8d5cf", "#24524d"),
    "ev-unmet": ("#d3d8d6", "#4a524f"),
    "ev-declined": DECLINED_COLOURS,
}

PAD = 16.0
SQ = 16.0
GAP = 4.0
ROW_H = 24.0
LINE_H = 20.0
HEADER_H = 18.0  # the "Level N" column headers above the squares
COL_W = svg.text_width("Level 9", 11) + 10  # one column per level, wide enough for its header
MIN_W = 680.0
MAX_LABEL = 36
_LAYER_PREFIX = re.compile(r"^Layer \d+:\s*")


def style() -> str:
    """The embedded ``<style>``: the fleet theme plus the category roles."""
    return svg.theme_style(ROLES)


def cell(x: float, y: float, category: str, *, size: float = SQ, tooltip: str | None = None,
         tagged: bool = True, values: Mapping[str, str | int | None] | None = None) -> str:
    """One square in a category's look; ``tagged`` adds ``data-category`` (a drawn condition,
    not a legend swatch), ``tooltip`` a hover ``<title>`` and ``values`` the data attributes
    holding what the title shows (``svg.data_attrs``)."""
    if category == "not_applicable":
        paint = 'class="f-bg s-muted" stroke-width="1.5"'
    elif category == "hazard":
        paint = 'class="f-warn s-rule" stroke-width="1"'
    elif category == "hazard_absent":
        paint = 'class="f-bg s-warn" stroke-width="1.5"'
    elif category == "not_measured":
        paint = f'fill="{svg.HATCH_FILL}" class="s-muted" stroke-width="1"'
    elif category in LABELS:
        paint = f'class="f-ev-{category} s-rule" stroke-width="1"'
    else:
        raise ValueError(f"unknown category {category!r}")
    data = (f' data-category="{category}"' if tagged else "") + svg.data_attrs(values or {})
    hover = "" if tooltip is None else f"<title>{svg.esc(tooltip)}</title>"
    return (f'<rect x="{x:.1f}" y="{y:.1f}" width="{size:.1f}" height="{size:.1f}" rx="2" '
            f'{paint}{data}>{hover}</rect>')


def legend(x: float, y: float, entries: Sequence[tuple[str, str, Sequence[str]]]) -> tuple[list[str], float]:
    """A key: per ``(category, label, notes)`` a swatch and its label, then each note indented
    on its own line. Returns the elements and the height used."""
    parts: list[str] = []
    at = y
    for category, label, notes in entries:
        parts.append(cell(x, at + 2, category, size=14, tagged=False))
        parts.append(svg.text(x + 22, at + 13, label, size=12, cls="fg"))
        at += LINE_H
        for note in notes:
            parts.append(svg.text(x + 22, at + 12, note, size=11.5, cls="muted"))
            at += LINE_H - 2
    return parts, at - y


def wrap(content: str, limit: int) -> list[str]:
    """``content`` in lines of at most ``limit`` characters, broken at spaces (a longer
    word keeps its own line)."""
    lines: list[str] = []
    for word in content.split():
        if lines and len(lines[-1]) + 1 + len(word) <= limit:
            lines[-1] += f" {word}"
        else:
            lines.append(word)
    return lines


def row_label(title: str) -> str:
    """A model row title without its ``Layer N:`` prefix, first letter capitalised."""
    stripped = _LAYER_PREFIX.sub("", title).strip() or title
    return stripped[:1].upper() + stripped[1:]


def _titles(model_rows: Sequence[Mapping[str, Any]] | None) -> dict[str, str]:
    return {str(r["id"]): row_label(str(r["title"])) for r in model_rows or ()}


def category(condition: ConditionResult) -> str:
    """A condition's waffle category from its state, grade and whether the tally counts it.

    A hazard (``polarity: "hazard"``, #843) is a harm and never counted: met reads
    ``hazard`` (present), unmet ``hazard_absent``; its other states draw as any condition's.
    """
    state = condition["state"]
    if condition.get("polarity") == "hazard" and state in ("met", "unmet"):
        return "hazard" if state == "met" else "hazard_absent"
    if state == "met":
        if not condition["counted"]:
            return "listed"
        return "verified" if condition["grade"] == "verified" else "measured"
    if state in ("unmet", "declined", "not_applicable", "not_measured"):
        return state
    raise ValueError(f"unknown condition state {state!r}")


def waffle_squares(level: LevelResult) -> list[WaffleSquare]:
    """One square per condition, in the level result's order (model row order)."""
    return [{"id": c["id"], "row": c["row"], "level": c["level"], "category": category(c),
             "reason": c.get("reason", "no_value") if c["state"] == "not_measured" else None}
            for c in level["conditions"]]


def _reason_text(code: str, titles: Mapping[str, str]) -> str:
    """A not-measured reason in words; a ``requires:`` reason names the row and level."""
    if code.startswith("requires:"):
        row, _, number = code.split(":", 1)[1].rpartition(".")
        return f"waits on {titles.get(row, row)} level {number}"
    return reason_word(code)


def _counts(squares: Sequence[WaffleSquare]) -> Counter[str]:
    return Counter(s["category"] for s in squares)


def _gate_outline(x: float, y: float, gate: str | None = None, size: float = SQ) -> str:
    """The Step-gate outline drawn around a square (``gate``: the condition id it marks)."""
    data = f' data-gate="{gate}"' if gate is not None else ""
    return (f'<rect x="{x - 3:.1f}" y="{y - 3:.1f}" width="{size + 6:.1f}" height="{size + 6:.1f}" '
            f'rx="3" fill="none" class="s-fg" stroke-width="2"{data}/>')


def render_waffle(level: LevelResult, titles: Mapping[str, str], gates: Sequence[str] = ()) -> str:
    """The evidence waffle SVG for one level result; ``titles`` maps row ids to labels and
    ``gates`` names the Step-gate condition ids to outline."""
    squares = waffle_squares(level)
    rows: dict[str, list[WaffleSquare]] = {}
    for square in squares:
        rows.setdefault(square["row"], []).append(square)
    labels = {row: svg.truncate(titles.get(row, row), MAX_LABEL) for row in rows}
    label_w = max(svg.text_width(label) for label in labels.values()) + 12
    widest = max(len(row) for row in rows.values())
    grid_x = PAD + label_w
    width = max(MIN_W, grid_x + widest * COL_W + PAD)
    counts = _counts(squares)
    body = [svg.text(PAD, 28, HEADINGS[WAFFLE_FILE], size=15, weight="600", cls="fg"),
            svg.text(PAD, 48, f"{len(squares)} conditions: each row is a practice, each square "
                     "one of its levels", size=12, cls="muted")]
    for index in range(widest):
        body.append(svg.text(grid_x + index * COL_W + SQ / 2, 64 + HEADER_H - 6, f"Level {index + 1}",
                             size=11, anchor="middle", cls="muted"))
    y = 64.0 + HEADER_H
    outlined = set(gates)
    drawn_gates = 0
    for row, members in rows.items():
        cut = labels[row] != titles.get(row, row)
        body.append(svg.text(grid_x - 12, y + SQ - 3, labels[row], anchor="end", cls="fg",
                             tooltip=titles.get(row, row) if cut else None))
        for index, square in enumerate(members):
            reason = None if square["reason"] is None else _reason_text(square["reason"], titles)
            tip = f"{titles.get(row, row)} level {square['level']}: {LABELS[square['category']]}"
            if reason is not None:
                tip += f" ({reason})"
            values: dict[str, str | int | None] = {
                "practice": titles.get(row, row), "level": square["level"],
                "category-label": LABELS[square["category"]], "reason": reason}
            body.append(cell(grid_x + index * COL_W, y, square["category"], tooltip=tip, values=values))
            if square["id"] in outlined:
                body.append(_gate_outline(grid_x + index * COL_W, y, square["id"]))
                drawn_gates += 1
        y += ROW_H
    reasons = Counter(s["reason"] for s in squares if s["reason"] is not None)
    notes = {"not_measured": [f"{_reason_text(code, titles)}: {n}" for code, n in sorted(reasons.items())]}
    entries = [(key, f"{label} ({counts[key]})", notes.get(key, [])) for key, label in CATEGORIES
               if key not in OPTIONAL or counts[key]]
    key_parts, key_h = legend(PAD, y + 12, entries)
    body += key_parts
    if drawn_gates:
        swatch_y = y + 12 + key_h
        body += [cell(PAD, swatch_y + 2, "unmet", size=14, tagged=False),
                 _gate_outline(PAD, swatch_y + 2, size=14),
                 svg.text(PAD + 22, swatch_y + 13, "Outlined: a Step gate", size=12, cls="fg")]
        key_h += LINE_H
    caption = waffle_caption()
    y += 12 + key_h
    for line in wrap(caption, int((width - 2 * PAD) / svg.CHAR_W)):
        y += LINE_H - 2
        body.append(svg.text(PAD, y, line, size=12, cls="fg"))
    desc = (f"{waffle_in_place(level)} {caption}")
    return svg.document(width, y + PAD, HEADINGS[WAFFLE_FILE], desc, body, scope=SCOPES["evidence-waffle"], style=style(),
                        defs=svg.THEMED_HATCH)


def waffle_caption() -> str:
    """The one-line caption: what to take from the waffle."""
    return ("Verified and measured squares count toward catalogue coverage, listed ones are shown but "
            "never counted, and hatched ones were not measured, never read as zero.")


def waffle_in_place(level: LevelResult) -> str:
    """The section's opening line: the counted conditions met, by grade, and the listed ones."""
    counts = _counts(waffle_squares(level))
    met = sum(counts[key] for key in COUNTED)
    return (f"{IN_PLACE} {met} counted {'condition' if met == 1 else 'conditions'} met "
            f"({counts['verified']} verified, {counts['measured']} measured) and "
            f"{counts['listed']} listed from config or a declaration.")


def _entry(file: str) -> AssessDiagram:
    return {"file": file, "title": HEADINGS[file],
            "in_place": f"{IN_PLACE} this run's scans, gathered with no model call.",
            "caption": "", "svg": None, "skipped": None}


def _ladder(ctx: Mapping[str, Any], steps: Sequence[StepDef]) -> AssessDiagram:
    entry = _entry(LADDER_FILE)
    level = ctx.get("level")
    if not isinstance(level, dict) or not isinstance(level.get("conditions"), list):
        entry["skipped"] = "the run has no level result, so there is no Step to place."
    elif not steps or level.get("step") is None:
        entry["skipped"] = "the maturity model defines no Steps."
    else:
        result = cast(LevelResult, level)
        entry["in_place"] = ladder_in_place(result, steps)
        entry["caption"] = ladder_caption(result, steps)
        entry["svg"] = render_ladder(result, steps)
    return entry


def _waffle(ctx: Mapping[str, Any], model_rows: Sequence[Mapping[str, Any]] | None,
            steps: Sequence[StepDef] = ()) -> AssessDiagram:
    entry = _entry(WAFFLE_FILE)
    level = ctx.get("level")
    if not isinstance(level, dict) or not isinstance(level.get("conditions"), list):
        entry["skipped"] = "the run has no level result, so there are no conditions to draw."
    elif not level["conditions"]:
        entry["skipped"] = "the maturity model has no conditions."
    else:
        result = cast(LevelResult, level)
        entry["in_place"] = waffle_in_place(result)
        entry["caption"] = waffle_caption()
        entry["svg"] = render_waffle(result, _titles(model_rows), gate_conditions(steps))
    return entry


def _guarded(build: Callable[[], AssessDiagram], file: str) -> AssessDiagram:
    """One diagram; a renderer that raises becomes a skip line naming the error class, so
    one broken picture never costs the others, the report or the run."""
    try:
        return build()
    except Exception as e:  # noqa: BLE001 - a diagram never stops a completed run
        entry = _entry(file)
        entry["skipped"] = f"the renderer raised {type(e).__name__}."
        return entry


def build_diagrams(ctx: Mapping[str, Any],
                   model_rows: Sequence[Mapping[str, Any]] | None = None,
                   steps: Sequence[StepDef] | None = None) -> list[AssessDiagram]:
    """Every /assess diagram for one run context, in report order; never raises.

    ``model_rows`` are the maturity model's rows (for row titles) and ``steps`` its Steps
    (the ladder and the outlined gate squares); None reads the model the run context's
    ``level`` block was computed with (``model_change.model_for_level``: the default model
    or a shipped profile, #1150), else the default model. A run under a custom ``--levels``
    model (``model_change.custom_run``) draws neither picture, each saying why, so the report
    never shows a Step from another model.
    """
    if model_rows is None or steps is None:
        model = model_for_level(ctx.get("level"))
        if model is None and custom_run(ctx):
            return [_custom(LADDER_FILE), _custom(WAFFLE_FILE)]
        model = model or default_model()
        model_rows = model["rows"] if model_rows is None else model_rows
        steps = model.get("steps", []) if steps is None else steps
    model_steps = steps
    return [_guarded(lambda: _ladder(ctx, model_steps), LADDER_FILE),
            _guarded(lambda: _waffle(ctx, model_rows, model_steps), WAFFLE_FILE)]


def _custom(file: str) -> AssessDiagram:
    """A diagram not drawn because the run's model is a custom file (#1150)."""
    entry = _entry(file)
    entry["skipped"] = CUSTOM_NOT_DRAWN
    return entry


NOT_ON_DISK = ("the file is not in the artifact directory (the write was skipped, or the "
               "run context comes from a run before this diagram).")


def diagrams_section(diagrams: Sequence[AssessDiagram], assess_dir: Path | None = None) -> str:
    """The report's Markdown: per diagram a ``## `` section opening with its ``In place:``
    line, then the SVG as a relative clickable image and its caption, or why it is not drawn.

    With ``assess_dir``, a drawn diagram whose file is not there reads ``Not drawn:``
    (``NOT_ON_DISK``) instead of linking an image that does not exist."""
    lines: list[str] = []
    for d in diagrams:
        lines += [f"## {d['title']}", "", d["in_place"], ""]
        if d["svg"] is not None and assess_dir is not None and not (assess_dir / d["file"]).is_file():
            lines += [f"Not drawn: {NOT_ON_DISK}", ""]
        elif d["svg"] is not None:
            lines += [f"[![{d['title']}](./{d['file']})](./{d['file']})", "", d["caption"], ""]
        else:
            lines += [f"Not drawn: {d['skipped']}", ""]
    return "\n".join(lines).rstrip("\n")


def write_diagrams(assess_dir: Path, diagrams: Sequence[AssessDiagram],
                   files: Sequence[str] = DIAGRAM_FILES) -> list[Path]:
    """Write each drawn SVG (LF line endings) and remove every diagram file not drawn this run;
    a symlink in a diagram's place is removed, never written through. A file that cannot be
    written (``OSError``) is named on stderr and skipped, so writing never stops the run either.
    ``files`` are the diagram files this call owns (a sibling module passes its own, #943).
    Returns the paths written."""
    written: list[Path] = []
    drawn = {d["file"]: d["svg"] for d in diagrams if d["svg"] is not None}
    for name in files:
        path = assess_dir / name
        try:
            if path.is_symlink() or (path.is_file() and name not in drawn):
                path.unlink()
            content = drawn.get(name)
            if content is not None:
                path.write_text(content, encoding="utf-8", newline="\n")
                written.append(path)
        except OSError as e:  # a diagram never stops a completed run
            print(f"/assess: {name} not written ({type(e).__name__}: {e})", file=sys.stderr)
    return written
