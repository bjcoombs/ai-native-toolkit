# golden-doc-repo — doc-graph-svg golden fixture

Tiny documentation tree consumed by `tests/test_golden_svg_render.py` to lock
the doc-graph SVG's staleness colour mapping (`days-stale -> hue`), the
entry-node marker, and the SVG accessibility metadata.

The test copies these files into a temp dir, synthesizes a git history
(committing `old.md` far in the past so it is stale, everything else "now"),
churns `src/app.py` so the staleness saturation axis is live, then runs the
**real** renderer (`scripts/doc-graph-svg.py` via `uv run --script`, no
matplotlib stubbing) and parses the produced SVG.

This file is named `FIXTURE.md` rather than `README.md` so it is NOT itself a
graphed doc - `README.md` is the fixture's entry point and must describe the
repo, not the test.

## Structure

- `README.md` - entry point; links to `guide.md` and `old.md`.
- `guide.md` - fresh doc, reachable from the entry.
- `old.md` - stale doc (committed far in the past), reachable from the entry.
- `src/app.py` - code the docs describe; churned so staleness saturation is live.

## Expected node encoding (golden values)

Deterministic from the OrRd colormap: staleness hue = `days / max(days)`, so the
oldest doc caps at the darkest red and same-day docs sit at the pale end.
Subject churn is equal across docs (repo-wide), so saturation is full (no grey
blend) and the fills are the pure base hue.

| Doc         | staleness | fill      | RGB           | marker                          |
|-------------|-----------|-----------|---------------|---------------------------------|
| `README.md` | fresh (0d)| `#fff7ec` | (255,247,236) | blue entry ring `#0072B2`, sw 3.5 |
| `guide.md`  | fresh (0d)| `#fff7ec` | (255,247,236) | default grey stroke             |
| `old.md`    | stale     | `#7f0000` | (127,  0,  0) | default grey stroke             |

Each node's `<title>` carries the doc path plus its staleness ("Nd stale"), the
accessible label a screen reader announces.

If the staleness colour mapping changes, these fills move and the golden test
fails - update this table and the test together, deliberately.
