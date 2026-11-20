# happy-doc-vs-code-default

Holds `/doc-contradictions` to checking a doc's claim against the code it names, and to citing the code line.

- **Input:** [`tests/fixtures/doc-vs-code-default/`](../../../skills/doc-contradictions/tests/fixtures/doc-vs-code-default/README.md). It is authored for this case and is not drawn from a recorded run.
- **Planted contradiction:** `docs/caching.md:9` says entries expire after 600 seconds by default; `src/cache.py:4` sets `DEFAULT_TTL_SECONDS = 300`.
- **Decoy:** the same page's size claim matches the code, so a comparer that reports every number it sees fails item 6.
- **Rubric:** [`graders/criteria.md`](./graders/criteria.md).
