"""The doc-drift map and the review strip, two /assess diagrams beside the evidence waffle (#943).

``review_diagrams(ctx)`` returns both as ``AssessDiagram`` entries
(``lib/run_context_types_diagrams.py``), each an SVG or the one-line reason it was not
drawn; it never raises. ``build_all(ctx)`` is ``assess_diagrams.build_diagrams``, the package map
(``lib/assess_diagrams_packages.py``, #942), then these two, in report order, and ``write_all`` writes every diagram file through
``assess_diagrams.write_diagrams`` with ``ALL_FILES``, so a file this run did not draw is
removed. ``run_publish.publish_run``, ``assess_core.run_opt_in_mutation`` and
``assess_report.py`` call these. Under ``--counts-only``, ``assess_core.main`` also draws the
two and the package map from the counts-only document (``counts_only_diagrams``) as
``package-map.counts-only.svg``, ``doc-drift.counts-only.svg`` and
``review-strip.counts-only.svg`` (``COUNTS_ONLY_FILES``) beside
``run-context.counts-only.json``, so a hashed run (``--hash-paths`` or ``--hash-salt``) has
pictures with hashed labels while the full-text ones beside the report stay as they are;
under ``--counts-only`` alone their path-class labels stay in clear, as in the JSON.
The report section is ``assess_diagrams.diagrams_section``, one ``## `` section per diagram.

Diagrams:

- **Doc drift** (``doc-drift.svg``): ranked horizontal bars for the first ``DRIFT_TOP`` docs
  with a code subject of their own: the docs behind their code first, then the rest, each
  group in ``doc_staleness.docs`` order (by ``ratio``, ties in scan order), so a generated
  doc behind its newer declared source at ratio 0 still ranks among the most behind
  (``ranked_docs``). Each bar is labelled with the doc and its first subject
  root; its length is the ``ratio`` and it reads "behind" (amber) where
  ``doc_complexity_join.is_stale`` holds, the same test as ``summary.stale_docs``. A dashed
  line marks ``STALENESS_RATIO_THRESHOLD``. Docs past the top are stated ("and K more");
  ``repo-baseline`` docs are counted in the caption, never drawn. A truncated or degenerate
  history draws nothing and says why, as the summary's counts read null there.
- **Review strip** (``review-strip.svg``): one tick per sampled merged change from
  ``review_reality.sequence`` (``lib/hosting/review_reality.py``), oldest merge first, in three
  states: approved, reviewed and merged without review; an ``unknown`` change (GitLab notes
  that decide nothing) is hatched. A dot over a tick marks review-bot output
  (``bot_sequence``) and a hollow dot a change where that is not known (an ``unknown`` mark,
  or a ``bot_sequence`` whose length differs from the sequence: every mark is then unknown),
  so an unknown mark never reads as no bot; the key then counts both. The approved ticks over ``merged_count`` equal ``approved_share`` and
  approved plus reviewed ticks equal ``reviewed_share``; a token outside the four reads
  unknown (``strip_tokens``). An unavailable block draws nothing
  and names its reason; nothing names a change, title, author or reviewer.

Deterministic: input order, fixed formatting, no date; two renders are byte-identical. No
caption or label names a layer number. Golden renders: ``tests/fixtures/assess_diagrams/``
(``make regen``, or ``uv run --locked python tests/assess_diagrams_review_fixture.py`` from
``skills/assess``). Tests: ``tests/test_assess_diagrams_review.py``.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import lib.fleet.fleet_svg as svg
from lib.assess_diagrams import DIAGRAM_FILES, build_diagrams, wrap, write_diagrams
from lib.assess_diagrams_packages import PACKAGE_COUNTS_ONLY_FILE, PACKAGE_FILE, package_diagrams
from lib.doc_complexity_join import STALENESS_RATIO_THRESHOLD, is_stale
from lib.doc_ownership import REPO_BASELINE
from lib.headline import reason_word
from lib.reason_codes import reason_code
from lib.hosting.review_reality import APPROVED, BOT, NO_BOT, REVIEWED, SEQUENCE_STATES, UNKNOWN, UNREVIEWED
from lib.run_context_types_diagrams import AssessDiagram
from lib.run_context_types_diagrams_review import DriftBar
from lib.vocabulary import IN_PLACE

DRIFT_FILE = "doc-drift.svg"
STRIP_FILE = "review-strip.svg"
HEADINGS: dict[str, str] = {
    DRIFT_FILE: "Docs behind their code",
    STRIP_FILE: "Review on recent merges",
}
REVIEW_FILES = tuple(HEADINGS)
COUNTS_ONLY_FILES = (PACKAGE_COUNTS_ONLY_FILE,
                     *(f.removesuffix(".svg") + ".counts-only.svg" for f in REVIEW_FILES))
ALL_FILES = (*DIAGRAM_FILES, PACKAGE_FILE, *REVIEW_FILES)
DRIFT_TOP = 15

# Colour roles, (light, dark), added to the shared diagram theme.
ROLES: dict[str, tuple[str, str]] = {
    "dr-behind": ("#d08a1e", "#e0a84a"),
    "dr-current": ("#8fc7c0", "#2e7d76"),
    "rv-approved": ("#00635d", "#4fc1b5"),
    "rv-reviewed": ("#8fc7c0", "#2e7d76"),
    "rv-unreviewed": ("#f1d9a6", "#7a5a1c"),
}
# Strip states in key order: (token, legend label).
STATES: tuple[tuple[str, str], ...] = (
    (APPROVED, "Approved by someone other than the author"),
    (REVIEWED, "Reviewed, not approved"),
    (UNREVIEWED, "Merged without review"),
    (UNKNOWN, "Not known: the platform left it undecided"),
)
LABELS = dict(STATES)
assert tuple(LABELS) == SEQUENCE_STATES

PAD = 16.0
MIN_W = 680.0
LINE_H = 20.0
LABEL_W = 296.0
LABEL_CHARS = 40
ROW_H = 36.0
BAR_H = 12.0
TICK_W = 16.0
TICK_GAP = 4.0
TICK_H = 32.0


def _style() -> str:
    return svg.theme_style(ROLES)


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _entry(file: str) -> AssessDiagram:
    return {"file": file, "title": HEADINGS[file],
            "in_place": f"{IN_PLACE} this run's scans, gathered with no model call.",
            "caption": "", "svg": None, "skipped": None}


def _not_measured(block: Mapping[str, Any]) -> str:
    return f"not measured here: {reason_word(reason_code(block.get('reason')))}."


def _swatch(x: float, y: float, paint: str) -> str:
    return f'<rect x="{x:.1f}" y="{y:.1f}" width="14.0" height="14.0" rx="2" {paint}/>'


def _key(x: float, y: float, entries: Sequence[tuple[str, str]]) -> tuple[list[str], float]:
    """Swatches with labels, one per line: ``(paint, label)``."""
    parts: list[str] = []
    for index, (paint, label) in enumerate(entries):
        at = y + index * LINE_H
        parts.append(paint.format(x=f"{x:.1f}", y=f"{at + 2:.1f}", cx=f"{x + 7:.1f}", cy=f"{at + 9:.1f}"))
        parts.append(svg.text(x + 22, at + 13, label, size=12, cls="fg"))
    return parts, len(entries) * LINE_H


def _caption(body: list[str], caption: str, width: float, y: float) -> float:
    for line in wrap(caption, int((width - 2 * PAD) / svg.CHAR_W)):
        y += LINE_H - 2
        body.append(svg.text(PAD, y, line, size=12, cls="fg"))
    return y


# --- doc drift ------------------------------------------------------------------


def mapped_docs(block: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The ``doc_staleness.docs`` rows with a code subject of their own, in block order."""
    return [r for r in block.get("docs") or [] if isinstance(r, Mapping)
            and r.get("subject_method") != REPO_BASELINE]


def ranked_docs(block: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The mapped docs behind their code first, then the rest, each in block order."""
    mapped = mapped_docs(block)
    behind = [is_stale(dict(r)) for r in mapped]
    return ([r for r, b in zip(mapped, behind) if b] + [r for r, b in zip(mapped, behind) if not b])


def drift_bars(block: Mapping[str, Any]) -> list[DriftBar]:
    """The first ``DRIFT_TOP`` ranked docs as bars (``ranked_docs``)."""
    bars: list[DriftBar] = []
    for row in ranked_docs(block)[:DRIFT_TOP]:
        roots = [str(r) for r in row.get("subject_roots") or []]
        bars.append({"path": str(row.get("path", "")), "subject": roots[0] if roots else "",
                     "more_roots": max(0, len(roots) - 1), "ratio": float(row.get("ratio") or 0.0),
                     "behind": is_stale(dict(row))})
    return bars


def _subject_label(bar: DriftBar) -> str:
    if not bar["subject"]:
        return "describes its linked code"
    more = f" and {bar['more_roots']} more" if bar["more_roots"] else ""
    return f"describes {svg.truncate(bar['subject'], LABEL_CHARS - 10 - len(more), keep_tail=True)}{more}"


def drift_caption(baseline: int) -> str:
    """The one-line caption: what to take from the drift map."""
    counted = (f" {_plural(baseline, 'doc')} measured against the whole repository "
               f"{'is' if baseline == 1 else 'are'} counted here, not drawn." if baseline else "")
    return ("The longest bars are the docs whose code has moved most since the doc last changed, "
            f"so they are the first to read again before an agent leans on them.{counted}")


def drift_in_place(block: Mapping[str, Any]) -> str:
    mapped = mapped_docs(block)
    current = sum(not is_stale(dict(r)) for r in mapped)
    return (f"{IN_PLACE} {_plural(len(mapped), 'doc')} {'maps' if len(mapped) == 1 else 'map'} "
            f"to code of {'its' if len(mapped) == 1 else 'their'} own, and {current} of them "
            "keep pace with that code.")


def render_drift(block: Mapping[str, Any]) -> str:
    """The doc-drift SVG for one ``doc_staleness`` block with at least one mapped doc."""
    mapped = mapped_docs(block)
    bars = drift_bars(block)
    baseline = len(block.get("docs") or []) - len(mapped)
    width = max(MIN_W, 720.0)
    bar_x = PAD + LABEL_W
    bar_span = width - bar_x - PAD - 44
    top = svg.nice_ceiling(max([STALENESS_RATIO_THRESHOLD, *(b["ratio"] for b in bars)]))
    scale = svg.LinearScale(0.0, top, bar_x, bar_x + bar_span)
    body = [svg.text(PAD, 28, HEADINGS[DRIFT_FILE], size=15, weight="600", cls="fg"),
            svg.text(PAD, 48, f"The {_plural(len(bars), 'doc')} furthest behind of "
                     f"{len(mapped)} mapped to code of their own, most behind first", size=12, cls="muted"),
            svg.text(PAD, 66, "Bar: commits to the doc's code per edit of the doc, counting only "
                     "those since its last change", size=11.5, cls="muted")]
    y = 84.0
    line_x = scale(STALENESS_RATIO_THRESHOLD)
    rows_h = len(bars) * ROW_H
    body.append(f'<line x1="{line_x:.1f}" y1="{y - 4:.1f}" x2="{line_x:.1f}" y2="{y + rows_h:.1f}" '
                'class="s-muted" stroke-width="1" stroke-dasharray="3 3"/>')
    body.append(svg.text(line_x + 4, y + 6, f"behind above {svg.tick_label(STALENESS_RATIO_THRESHOLD)}",
                         size=11, cls="muted"))
    for bar in bars:
        label = svg.truncate(bar["path"], LABEL_CHARS, keep_tail=True)
        body.append(svg.text(PAD, y + 15, label, cls="fg",
                             tooltip=bar["path"] if label != bar["path"] else None))
        body.append(svg.text(PAD, y + 30, _subject_label(bar), size=11, cls="muted"))
        length = scale(min(bar["ratio"], top)) - bar_x
        if bar["behind"]:
            length = max(length, 3.0)  # a newer declared source at ratio 0 still shows
        role = "dr-behind" if bar["behind"] else "dr-current"
        tip = f"{bar['path']}: {svg.tick_label(bar['ratio'])}"
        body.append(f'<rect x="{bar_x:.1f}" y="{y + 10:.1f}" width="{length:.1f}" height="{BAR_H:.1f}" '
                    f'class="f-{role}" data-ratio="{bar["ratio"]:g}" data-behind="{str(bar["behind"]).lower()}">'
                    f"<title>{svg.esc(tip)}</title></rect>")
        body.append(svg.text(bar_x + length + 6, y + 20, svg.tick_label(bar["ratio"]), size=11, cls="fg"))
        y += ROW_H
    more = len(mapped) - len(bars)
    if more:
        body.append(svg.text(PAD, y + 14, f"And {_plural(more, 'more mapped doc')}, not drawn",
                             size=12, cls="muted"))
        y += LINE_H
    key, key_h = _key(PAD, y + 8, [
        ('<rect x="{x}" y="{y}" width="14.0" height="14.0" rx="2" class="f-dr-behind"/>',
         f"Behind its code: above {svg.tick_label(STALENESS_RATIO_THRESHOLD)}, or a newer declared source"),
        ('<rect x="{x}" y="{y}" width="14.0" height="14.0" rx="2" class="f-dr-current"/>',
         "Keeping pace with its code"),
    ])
    body += key
    caption = drift_caption(baseline)
    y = _caption(body, caption, width, y + 8 + key_h)
    return svg.document(width, y + PAD, HEADINGS[DRIFT_FILE], f"{drift_in_place(block)} {caption}",
                        body, style=_style())


def _drift(ctx: Mapping[str, Any]) -> AssessDiagram:
    entry = _entry(DRIFT_FILE)
    block = ctx.get("doc_staleness")
    if not isinstance(block, Mapping):
        entry["skipped"] = "the run has no doc-freshness data."
    elif block.get("available") is False:
        entry["skipped"] = _not_measured(block)
    elif block.get("history_truncated"):
        entry["skipped"] = ("the clone is shallow, so how far each doc is behind its code is "
                            "not measured.")
    elif block.get("churn_degenerate"):
        entry["skipped"] = ("the history shows about one commit per file, too little to measure "
                            "how far each doc is behind its code.")
    elif not mapped_docs(block):
        docs = len(block.get("docs") or [])
        entry["skipped"] = (f"no doc maps to code of its own; {_plural(docs, 'doc')} measured "
                            f"against the whole repository {'is' if docs == 1 else 'are'} counted, not drawn."
                            if docs else "the repository has no docs to measure.")
    else:
        baseline = len(block.get("docs") or []) - len(mapped_docs(block))
        entry["in_place"] = drift_in_place(block)
        entry["caption"] = drift_caption(baseline)
        entry["svg"] = render_drift(block)
    return entry


# --- review strip ---------------------------------------------------------------


def strip_tokens(sequence: Sequence[object]) -> list[str]:
    """The sequence with any token outside ``SEQUENCE_STATES`` read as unknown, never drawn
    as a state of its own."""
    return [t if isinstance(t, str) and t in SEQUENCE_STATES else UNKNOWN for t in sequence]


def strip_counts(sequence: Sequence[str]) -> dict[str, int]:
    """Ticks per state, in key order."""
    return {state: sum(t == state for t in sequence) for state in SEQUENCE_STATES}


def bot_marks(block: Mapping[str, Any], n: int) -> list[str]:
    """One mark per tick: ``bot``, ``none`` or ``unknown``. Any other token, or a list of
    another length, reads unknown, never as no bot."""
    marks = block.get("bot_sequence")
    if not isinstance(marks, list) or len(marks) != n:
        return [UNKNOWN] * n
    return [m if m in (BOT, NO_BOT) else UNKNOWN for m in marks]


def _bot_key(marks: Sequence[str], share_unknown: bool) -> list[tuple[str, str]]:
    """The bot rows of the key; a bare count only when every mark and the share are known."""
    known, unknown = marks.count(BOT), marks.count(UNKNOWN)
    if not unknown and not share_unknown:
        return [(_DOT, f"Review-bot output ({known})")]
    return [(_DOT, f"Review-bot output ({known} known)"),
            (_HOLLOW, f"Not known whether a review bot commented ({unknown})")]


_DOT = '<circle cx="{cx}" cy="{cy}" r="3.5" class="f-fg"/>'
_HOLLOW = '<circle cx="{cx}" cy="{cy}" r="3.5" class="f-bg s-fg" stroke-width="1.2"/>'


def strip_caption() -> str:
    """The one-line caption: what to take from the strip."""
    return ("Each tick is one merged change, oldest first, so a run of ticks merged without review "
            "shows a stretch that a single share hides; a dot marks review-bot output.")


def strip_in_place(sequence: Sequence[str]) -> str:
    counts = strip_counts(sequence)
    unknown = f", and {counts[UNKNOWN]} not known" if counts[UNKNOWN] else ""
    return (f"{IN_PLACE} {counts[APPROVED]} of {_plural(len(sequence), 'sampled merged change')} "
            f"approved and {counts[REVIEWED]} more reviewed{unknown}.")


def _tick_paint(state: str) -> str:
    if state == UNKNOWN:
        return f'fill="{svg.HATCH_FILL}" class="s-muted" stroke-width="1"'
    return f'class="f-rv-{state} s-muted" stroke-width="1"'


def render_strip(block: Mapping[str, Any]) -> str:
    """The review strip SVG for one available ``review_reality`` block with a sequence."""
    sequence = strip_tokens(block["sequence"])
    bots = bot_marks(block, len(sequence))
    n = len(sequence)
    width = max(MIN_W, 2 * PAD + n * (TICK_W + TICK_GAP))
    body = [svg.text(PAD, 28, HEADINGS[STRIP_FILE], size=15, weight="600", cls="fg"),
            svg.text(PAD, 48, f"{_plural(n, 'sampled merged change')}, one tick each, in merge order",
                     size=12, cls="muted")]
    y = 70.0
    for index, (state, bot) in enumerate(zip(sequence, bots)):
        x = PAD + index * (TICK_W + TICK_GAP)
        tip = LABELS.get(state, state) + {BOT: ", with review-bot output",
                                          UNKNOWN: ", review-bot output not known"}.get(bot, "")
        body.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{TICK_W:.1f}" height="{TICK_H:.1f}" rx="2" '
                    f'{_tick_paint(state)} data-state="{svg.esc(state)}"><title>{svg.esc(tip)}</title></rect>')
        if bot == BOT:
            body.append(f'<circle cx="{x + TICK_W / 2:.1f}" cy="{y - 7:.1f}" r="3.5" class="f-fg" '
                        'data-bot="true"/>')
        elif bot == UNKNOWN:
            body.append(f'<circle cx="{x + TICK_W / 2:.1f}" cy="{y - 7:.1f}" r="3.5" class="f-bg s-fg" '
                        'stroke-width="1.2" data-bot="unknown"/>')
    y += TICK_H
    body.append(svg.text(PAD, y + 16, "oldest", size=11, cls="muted"))
    body.append(svg.text(PAD + n * (TICK_W + TICK_GAP) - TICK_GAP, y + 16, "newest", size=11,
                         cls="muted", anchor="end"))
    counts = strip_counts(sequence)
    entries = [(('<rect x="{x}" y="{y}" width="14.0" height="14.0" rx="2" '
                 + _tick_paint(state) + "/>"), f"{label} ({counts[state]})")
               for state, label in STATES if state != UNKNOWN or counts[UNKNOWN]]
    entries += _bot_key(bots, "bot_review_share" in block and block["bot_review_share"] is None)
    key, key_h = _key(PAD, y + 30, entries)
    body += key
    caption = strip_caption()
    y = _caption(body, caption, width, y + 30 + key_h)
    return svg.document(width, y + PAD, HEADINGS[STRIP_FILE], f"{strip_in_place(sequence)} {caption}",
                        body, style=_style(), defs=svg.THEMED_HATCH)


def _strip(ctx: Mapping[str, Any]) -> AssessDiagram:
    entry = _entry(STRIP_FILE)
    block = ctx.get("review_reality")
    if not isinstance(block, Mapping):
        entry["skipped"] = "the run has no review data."
    elif block.get("available") is False:
        entry["skipped"] = _not_measured(block)
    elif not isinstance(block.get("sequence"), list):
        entry["skipped"] = "this run's review data has no per-change sequence."
    elif not block["sequence"]:
        entry["skipped"] = "no change was merged in the sampled window."
    else:
        entry["in_place"] = strip_in_place(strip_tokens(block["sequence"]))
        entry["caption"] = strip_caption()
        entry["svg"] = render_strip(block)
    return entry


# --- the set --------------------------------------------------------------------


def _guarded(build: Callable[[Mapping[str, Any]], AssessDiagram], ctx: Mapping[str, Any],
             file: str) -> AssessDiagram:
    """One diagram; a renderer that raises becomes a skip line naming the error class."""
    try:
        return build(ctx)
    except Exception as e:  # noqa: BLE001 - a diagram never stops a completed run
        entry = _entry(file)
        entry["skipped"] = f"the renderer raised {type(e).__name__}."
        return entry


def review_diagrams(ctx: Mapping[str, Any]) -> list[AssessDiagram]:
    """The doc-drift map and the review strip for one run context; never raises."""
    return [_guarded(_drift, ctx, DRIFT_FILE), _guarded(_strip, ctx, STRIP_FILE)]


def counts_only_diagrams(content_free: Mapping[str, Any]) -> list[AssessDiagram]:
    """The package map (#942) and both review diagrams drawn from a counts-only document,
    named ``COUNTS_ONLY_FILES``."""
    drawn = [*package_diagrams(content_free), *review_diagrams(content_free)]
    assert len(drawn) == len(COUNTS_ONLY_FILES)
    return [{**d, "file": name} for d, name in zip(drawn, COUNTS_ONLY_FILES)]


def build_all(ctx: Mapping[str, Any],
              model_rows: Sequence[Mapping[str, Any]] | None = None) -> list[AssessDiagram]:
    """Every /assess diagram in report order: ``build_diagrams``, the package map (#942,
    ``lib/assess_diagrams_packages.py``), then these two."""
    return [*build_diagrams(ctx, model_rows), *package_diagrams(ctx), *review_diagrams(ctx)]


def write_all(assess_dir: Path, diagrams: Sequence[AssessDiagram],
              files: Sequence[str] = ALL_FILES) -> list[Path]:
    """``write_diagrams`` over ``files`` (every diagram file by default)."""
    return write_diagrams(assess_dir, diagrams, files)
