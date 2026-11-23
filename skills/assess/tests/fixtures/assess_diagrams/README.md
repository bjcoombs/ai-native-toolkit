# /assess diagram goldens (#942)

Golden renders of `lib/assess_diagrams.py` over the hand-built level result in
`tests/assess_diagrams_fixture.py`, which holds every waffle category (including the
`declined` and `not_applicable` states no rule produces yet) and not-measured conditions
with a plain and a `requires:` reason.

- `expected-evidence-waffle.svg` - the evidence waffle
- `expected-section.md` - the report section that links it
- `expected-doc-drift.svg`, `expected-review-strip.svg` and `expected-review-section.md` -
  the doc-drift map, the review strip and their report sections (#943), drawn from the
  blocks in `tests/assess_diagrams_review_fixture.py`
- `expected-tag-profile.svg`, `expected-tag-profile-other-model.svg` and
  `expected-tag-section.md` - the tag profile strip (#940) with an earlier run under the same
  maturity model and under another, and its report section, drawn from the tag rows in
  `tests/assess_diagrams_tags_fixture.py` (every row state: measured shares with a real 0, a
  tag no condition carries, listed only, not measured and set aside)

Regenerate from `skills/assess`, then review the diff:

```bash
uv run --locked python tests/assess_diagrams_fixture.py
uv run --locked python tests/assess_diagrams_review_fixture.py
uv run --locked python tests/assess_diagrams_tags_fixture.py
```
