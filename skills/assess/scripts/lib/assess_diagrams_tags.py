"""The repository tag profile strip, an /assess diagram beside the evidence waffle (#940).

``tag_diagrams(ctx)`` returns the strip (``tag-profile.svg``) as an ``AssessDiagram``
(``lib/run_context_types_diagrams.py``), an SVG or the one-line reason it was not drawn; it
never raises. ``assess_diagrams_review.build_all`` places it after the evidence waffle, and
``write_all`` owns its file (``ALL_FILES``).

The strip draws from data only: the run context's ``level.tags`` (``lib/level_tags.py``
``tag_rows``, whose ``met`` and ``measured`` are the headline strip's cells, never a second
tally) and ``prior_level`` (``lib/prior_level.py``, read before this run wrote its own). One
row per truth tag, every tag of ``TRUTH_TAGS`` in that fixed order, so two profiles always
compare row for row. Each row's label is the tag title and its condition count
(``Supply chain · 1``), and its ``<title>`` hover text is the tag's ``why`` from the check
manifest (``lib.manifest.manifest_whys.tag_why``, #939). A row reads one of (``row_state``):

- ``share``: a dot on a shared 0 to 100 percent scale at ``met`` of ``measured``, with the
  counts beside it. A point mark on a line, never a filled area;
- ``none``: no condition carries the tag yet (``contracts`` today): "not yet measured", no track;
- ``listed``: no condition has a grade the tally counts (config, a declaration or a hazard):
  ``countable`` is 0;
- ``not_measured``: none was measured and a countable condition was not measured; the most
  frequent reason code among the countable ones (``countable_not_measured``, ties by code) in words;
- ``set_aside``: every countable condition was excluded by the owner or declined.

Every row but ``share`` is a dashed, hollow track with its reason and no dot: a tag that was
not measured never plots at 0. A real 0 of N measured does.

**Earlier run.** When ``prior_level`` was counted under this run's maturity-model version
(``prior_level.same_model``), each tag the earlier run measured gets a hollow outline dot at its
earlier share, so the strip shows what moved; on a row this run did not measure, the outline
stays and a key line says it is the earlier share. Otherwise no outline dot is drawn and one
line says why: no earlier run is recorded, its record could not be read, or it used another
model version.

The strip names no path, repository or person (tag titles, counts, reason words and model
versions only), so it needs no counts-only variant. Deterministic: fixed tag order, fixed
formatting, no date; two renders are byte-identical. No caption or label names a layer
number. Golden renders: ``tests/fixtures/assess_diagrams/`` (``make regen``, or
``uv run --locked python tests/assess_diagrams_tags_fixture.py`` from ``skills/assess``).
Tests: ``tests/test_assess_diagrams_tags.py``.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any, cast

import lib.fleet.fleet_svg as svg
from lib.assess_diagrams import wrap
from lib.headline import reason_word
from lib.level_result_types import TagRow
from lib.levels_fields import TRUTH_TAG_TITLES, TRUTH_TAGS
from lib.prior_level import as_prior_level, same_model
from lib.run_context_types_diagrams import AssessDiagram
from lib.vocabulary import IN_PLACE

TAG_FILE = "tag-profile.svg"
HEADINGS: dict[str, str] = {TAG_FILE: "Practices in place by truth tag"}
TAG_FILES = tuple(HEADINGS)

# Row states in key order, and what a dashed row says.
STATES = ("share", "none", "listed", "not_measured", "set_aside")
DASHED_TEXT = {
    "none": "not yet measured: no condition has this tag",
    "listed": "not counted: its conditions are listed, never counted",
    "set_aside": "not measured: excluded by the owner or declined",
}

PAD = 16.0
MIN_W = 680.0
LINE_H = 20.0
ROW_H = 30.0
TRACK_W = 320.0
DOT_R = 6.0
PRIOR_R = 8.0
TICKS = (0, 25, 50, 75, 100)


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _entry() -> AssessDiagram:
    return {"file": TAG_FILE, "title": HEADINGS[TAG_FILE],
            "in_place": f"{IN_PLACE} this run's scans, gathered with no model call.",
            "caption": "", "svg": None, "skipped": None}


def row_state(row: TagRow) -> str:
    """How a tag row reads (see the module docstring); only ``share`` draws a dot."""
    if row["conditions"] == 0:
        return "none"
    if row["measured"] > 0:
        return "share"
    if row["countable"] == 0:
        return "listed"
    if row["countable_not_measured"]:
        return "not_measured"
    return "set_aside"


def main_reason(row: TagRow) -> str:
    """The most frequent reason code among a row's countable conditions not measured, ties by
    code: a listed condition never explains a missing share."""
    counts = Counter(m["reason"] for m in row["countable_not_measured"])
    return min(counts, key=lambda code: (-counts[code], code))


def dashed_text(row: TagRow) -> str:
    """The reason a row that draws no dot gives, in words."""
    state = row_state(row)
    if state == "not_measured":
        code = main_reason(row)
        # a ``requires:`` code names a row id, which can carry a layer number: say it plainly
        words = "waits on another condition" if code.startswith("requires:") else reason_word(code)
        return f"not measured: {words}"
    return DASHED_TEXT[state]


def row_label(row: TagRow) -> str:
    """The tag title and its condition count: ``Supply chain · 1``."""
    return f"{TRUTH_TAG_TITLES[row['tag']]} · {row['conditions']}"


def tag_rows_of(level: Mapping[str, Any]) -> list[TagRow] | None:
    """The level block's tag rows in ``TRUTH_TAGS`` order; None when it has none (a block
    from before #940) or they are not every tag once."""
    rows = level.get("tags")
    if not isinstance(rows, list) or [r.get("tag") if isinstance(r, Mapping) else None
                                      for r in rows] != list(TRUTH_TAGS):
        return None
    return cast(list[TagRow], rows)


def tag_whys() -> dict[str, str]:
    """Each tag's ``why`` from the core check manifest; the tag title where it has none."""
    from lib.manifest.check_manifest import core_manifest  # lazy: the manifest loads once
    from lib.manifest.manifest_whys import tag_why
    manifest = core_manifest()
    return {tag: tag_why(manifest, tag)["why"] or TRUTH_TAG_TITLES[tag] for tag in TRUTH_TAGS}


def earlier_shares(raw: Any, level: Mapping[str, Any]) -> dict[str, tuple[int, int]]:
    """``(met, measured)`` per tag the earlier run measured, when it used this run's model.
    ``raw`` is the run context's ``prior_level`` as stored."""
    prior = as_prior_level(raw)
    if prior is None or not same_model(prior, level):
        return {}
    return {r["tag"]: (r["met"], r["measured"]) for r in prior["tags"] if r["measured"] > 0}


def prior_note(raw: Any, level: Mapping[str, Any]) -> str:
    """The one line about the earlier run's outline dots. ``raw`` is the run context's
    ``prior_level``: None when no earlier file exists; anything not a readable ``PriorLevel``
    (``prior_level.UNREADABLE``, or a hand-edited value) is an earlier record not read."""
    if raw is None:
        return "No earlier run is recorded here, so no earlier dots are drawn."
    prior = as_prior_level(raw)
    if prior is None:
        return "The earlier run's record could not be read, so no earlier dots are drawn."
    if not same_model(prior, level):
        return (f"Earlier dots not drawn: the earlier run used maturity model {prior['levels_version']} "
                f"and this run {level.get('levels_version')}, so the two do not compare.")
    if not earlier_shares(prior, level):
        return "No earlier dots: the earlier run, under the same maturity model, measured no tag."
    return f"Hollow dots: the earlier run, under the same maturity model {level.get('levels_version')}."


def _share(met: int, measured: int) -> float:
    return met / measured


def render_strip(level: Mapping[str, Any], rows: Sequence[TagRow], prior: Any,
                 whys: Mapping[str, str]) -> str:
    """The tag profile strip SVG for one level block's tag rows."""
    earlier = earlier_shares(prior, level)
    labels = [row_label(r) for r in rows]
    label_w = max(svg.text_width(label) for label in labels) + 16
    x0 = PAD + label_w
    x1 = x0 + TRACK_W
    side = [f"{r['met']} of {r['measured']} met" if row_state(r) == "share" else dashed_text(r) for r in rows]
    width = max(MIN_W, x1 + 16 + max(svg.text_width(s, 11.5) for s in side) + PAD)
    body = [svg.text(PAD, 28, HEADINGS[TAG_FILE], size=15, weight="600", cls="fg"),
            svg.text(PAD, 48, "Share of each tag's measured conditions that are met, one row per truth tag",
                     size=12, cls="muted")]
    top = 64.0
    for tick in TICKS:
        x = x0 + TRACK_W * tick / 100
        body.append(svg.text(x, top + 12, f"{tick}%", size=11, anchor="middle", cls="muted"))
    y = top + 24
    bottom = y + ROW_H * len(rows)
    for tick in TICKS:
        x = x0 + TRACK_W * tick / 100
        body.append(f'<line x1="{x:.1f}" y1="{y:.1f}" x2="{x:.1f}" y2="{bottom:.1f}" class="s-rule" '
                    'stroke-width="1"/>')
    for row, label, note in zip(rows, labels, side):
        state = row_state(row)
        mid = y + ROW_H / 2
        parts = [f'<g data-tag="{row["tag"]}" data-state="{state}"><title>{svg.esc(whys[row["tag"]])}</title>',
                 svg.text(x0 - 12, mid + 4, label, anchor="end", cls="fg")]
        if state == "share":
            parts.append(f'<line x1="{x0:.1f}" y1="{mid:.1f}" x2="{x1:.1f}" y2="{mid:.1f}" class="s-muted" '
                         'stroke-width="1.5"/>')
        else:
            parts.append(f'<line x1="{x0:.1f}" y1="{mid:.1f}" x2="{x1:.1f}" y2="{mid:.1f}" class="s-muted" '
                         'stroke-width="1.5" stroke-dasharray="4 4" data-mark="dashed"/>')
        if row["tag"] in earlier:
            px = x0 + TRACK_W * _share(*earlier[row["tag"]])
            parts.append(f'<circle cx="{px:.1f}" cy="{mid:.1f}" r="{PRIOR_R:.1f}" fill="none" class="s-fg" '
                         'stroke-width="1.5" data-mark="prior"/>')
        if state == "share":
            dx = x0 + TRACK_W * _share(row["met"], row["measured"])
            parts.append(f'<circle cx="{dx:.1f}" cy="{mid:.1f}" r="{DOT_R:.1f}" class="f-accent s-bg" '
                         'stroke-width="1.5" data-mark="dot"/>')
        parts.append(svg.text(x1 + 16, mid + 4, note, size=11.5, cls="fg" if state == "share" else "muted"))
        parts.append("</g>")
        body.append("".join(parts))
        y += ROW_H
    y += 12
    key = [(f'<circle cx="{PAD + 7:.1f}" cy="{{cy}}" r="{DOT_R:.1f}" class="f-accent s-bg" stroke-width="1.5"/>',
            "This run: the share of the tag's measured conditions that are met")]
    if earlier:
        key.append((f'<circle cx="{PAD + 7:.1f}" cy="{{cy}}" r="{PRIOR_R - 1:.1f}" fill="none" class="s-fg" '
                    'stroke-width="1.5"/>', "The earlier run, under the same maturity model"))
    if any(r["tag"] in earlier and row_state(r) != "share" for r in rows):
        key.append((f'<circle cx="{PAD + 7:.1f}" cy="{{cy}}" r="{PRIOR_R - 1:.1f}" fill="none" class="s-fg" '
                    'stroke-width="1.5"/>',
                    "A hollow dot on a dashed row is the earlier run's share; this run did not measure it"))
    key.append((f'<line x1="{PAD:.1f}" y1="{{cy}}" x2="{PAD + 14:.1f}" y2="{{cy}}" class="s-muted" '
                'stroke-width="1.5" stroke-dasharray="4 4"/>', "Not measured: drawn without a dot, never as 0"))
    for paint, text in key:
        body.append(paint.format(cy=f"{y + 9:.1f}"))
        body.append(svg.text(PAD + 22, y + 13, text, size=12, cls="fg"))
        y += LINE_H
    limit = int((width - 2 * PAD) / svg.CHAR_W)
    for line in wrap(prior_note(prior, level), limit):
        y += LINE_H - 2
        body.append(svg.text(PAD, y, line, size=12, cls="muted"))
    for line in wrap(strip_caption(), limit):
        y += LINE_H - 2
        body.append(svg.text(PAD, y, line, size=12, cls="fg"))
    desc = f"{strip_in_place(rows)} {prior_note(prior, level)} {strip_caption()}"
    return svg.document(width, y + PAD, HEADINGS[TAG_FILE], desc, body, style=svg.theme_style())


def strip_caption() -> str:
    """The one-line caption: what to take from the strip."""
    return ("Each dot is the share of that tag's measured conditions that are met. A condition "
            "with several tags counts on each of its rows and once in the tally, and a dashed row "
            "was not measured, never read as zero.")


def strip_in_place(rows: Sequence[TagRow]) -> str:
    """The section's opening line: how many tags have a measured condition."""
    measured = sum(row_state(r) == "share" for r in rows)
    return (f"{IN_PLACE} {measured} of the {len(rows)} truth tags have a measured condition, "
            "each shown with the share of its measured conditions met.")


def _strip(ctx: Mapping[str, Any]) -> AssessDiagram:
    entry = _entry()
    level = ctx.get("level")
    if not isinstance(level, Mapping) or not isinstance(level.get("conditions"), list):
        entry["skipped"] = "the run has no level result, so there are no tags to draw."
        return entry
    rows = tag_rows_of(level)
    if rows is None:
        entry["skipped"] = "the level result has no per-tag rows (it was written before them)."
        return entry
    prior = ctx.get("prior_level")
    entry["in_place"] = strip_in_place(rows)
    entry["caption"] = f"{strip_caption()} {prior_note(prior, level)}"
    entry["svg"] = render_strip(level, rows, prior, tag_whys())
    return entry


def tag_diagrams(ctx: Mapping[str, Any]) -> list[AssessDiagram]:
    """The tag profile strip for one run context; never raises."""
    try:
        return [_strip(ctx)]
    except Exception as e:  # noqa: BLE001 - a diagram never stops a completed run
        entry = _entry()
        entry["skipped"] = f"the renderer raised {type(e).__name__}."
        return [entry]
