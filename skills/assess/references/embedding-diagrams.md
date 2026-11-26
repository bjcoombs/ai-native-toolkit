# Embedding the diagrams in a page

Every diagram `/assess` and `/fleet` draw is a standalone SVG. Opened on its own, each part's
details show as the browser's native `<title>` tooltip. A host page that shows the diagrams,
such as a team report or a fleet page, needs two things to keep those details: an embed that
lets the SVG receive events, and the tooltip helper.

## Use an inline or `<object>` embed, not `<img>`

A browser draws an `<img>` SVG as a static picture. It gets no pointer events, so neither the
native `<title>` tooltip nor any script can show a part's details. A touch screen has no hover
at all.

| Embed | Details on hover and tap | Note |
|---|---|---|
| Inline `<svg>` in the page | Yes, with the helper | Each diagram is scoped (`scripts/lib/svg_scope.py`): its styles and ids stay inside it, so several can sit on one page beside the host's own `rect` and `text`. |
| `<object type="image/svg+xml" data="...">` | Yes, with the helper | Served from the same origin as the page, or the helper cannot reach inside it. |
| `<img src="...">`, or a CSS background | No | A picture only. Markdown image links (`![...](x.svg)`) render this way. |

## The tooltip helper

`skills/assess/templates/diagram-tooltip.js` (relative to the repository root) is a single
script with no dependencies and no network requests. Copy it next to the page and include it
once:

```html
<script src="diagram-tooltip.js"></script>
```

On load it attaches to the page and to every `<object>` holding an SVG. For a diagram added
later, call `DiagramTooltip.attach(element)`. To word the tooltip yourself, pass
`{format: function (part) { return "..."; }}`.

What it does:

- **Content.** The tooltip lists the part's `data-*` attributes, one `name: value` line each.
  A part with no data attributes shows its `<title>` text instead. The helper never parses the
  `<title>` wording.
- **Pointer.** Mouse and pen show the tooltip on hover and hide it when the pointer leaves the
  part.
- **Touch.** The first tap shows it; a second tap, a tap elsewhere or Escape hides it.
- **Links.** No diagram draws a link today. On a part inside an `<a>`, a mouse click follows
  the link; on touch the first tap shows the tooltip and the second follows the link.
- **Theme.** It follows the viewer's light or dark scheme. A host overrides the colours with
  `--diagram-tooltip-bg`, `--diagram-tooltip-fg` and `--diagram-tooltip-border`.
- **Native tooltip.** While its tooltip shows, it empties the part's `<title>` so the browser's
  own tooltip does not show as well, and restores it on hide.

## What each diagram carries

Each part with a `<title>` carries the values its title shows as `data-*` attributes, written
as plain values: numbers without thousands separators or units. A value the run did not
measure is left out, never written as 0. Codes such as `data-category`, `data-state` and
`data-cell` name the state the title words.

| Diagram | Part | Data attributes |
|---|---|---|
| `complexity-heatmap.svg` | block | `data-path`, `data-loc`, `data-ccn`, `data-tokens`, `data-commits`; the root carries `data-churn-window` |
| `doc-graph.svg` | doc | `data-path`, `data-lines` or `data-centrality`, `data-in`, `data-out`, `data-status`, `data-stale-days`, `data-subject-churn` |
| `doc-graph.svg` | broken link | `data-path`, `data-status="broken"`, `data-sources`, `data-source-count` |
| `evidence-waffle.svg` | square | `data-practice`, `data-level`, `data-category`, `data-category-label`, `data-reason` |
| `package-map.svg` | bar, cell | `data-package`, `data-loc`; `data-from`, `data-to`, `data-imports`, `data-bypass` |
| `doc-drift.svg` | bar | `data-path`, `data-ratio`, `data-behind` |
| `review-strip.svg` | tick | `data-state`, `data-state-label`, `data-review-bot` |
| `tag-profile.svg` (both) | row | `data-tag`, `data-state`, `data-why` |
| `heatmap.svg` (fleet) | cell | `data-group`, `data-practice`, `data-level` or `data-floor`, `data-top`, `data-missing`, `data-unknown-repos`, `data-change` |
| `tag-heatmap.svg` | cell | `data-tag`, `data-tag-title`, `data-percent-met` |
| `one-change-away.svg` | bar | `data-condition`, `data-gate`, `data-practice`, `data-step` |
| `shared-tooling.svg` | link | `data-repo`, `data-item`, `data-refs` |
| `radar.svg` | point | `data-row`, `data-unit`, `data-practice`, `data-met`, `data-measured`, `data-repos` |
| `trend.svg` | point | `data-group`, `data-mean`, `data-run`, `data-floor-mean` |
| `slope.svg` | line | `data-group`, `data-then`, `data-now`, `data-change`, `data-paired-practices`, `data-practices`, `data-floor-then`, `data-floor-now` |
| any | truncated label | `data-label`, the full text |

The step ladder and the Step movement grid carry no per-part `<title>`, so there is nothing
more to show. `skills/assess/tests/test_diagram_data_attrs.py` checks that every titled part
carries its title's values.

Counts-only and hashed runs write the same names and hashes into the attributes as into the
titles, so an attribute never shows more than its title does.
