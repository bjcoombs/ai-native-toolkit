# /assess diagram goldens (#942)

Golden renders of `lib/assess_diagrams.py` over the hand-built level result in
`tests/assess_diagrams_fixture.py`, which holds every waffle category (including the
`declined` and `not_applicable` states no rule produces yet) and not-measured conditions
with a plain and a `requires:` reason.

- `expected-evidence-waffle.svg` - the evidence waffle
- `expected-section.md` - the report section that links it

Regenerate from `skills/assess`, then review the diff:

```bash
uv run --locked python tests/assess_diagrams_fixture.py
```
