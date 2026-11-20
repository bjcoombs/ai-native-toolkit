# edge-excluded-historical-record

Holds `/doc-contradictions` to its default exclude of historical intent records, and to the override that brings them back.

- **Input:** [`tests/fixtures/excluded-historical/`](../../../skills/doc-contradictions/tests/fixtures/excluded-historical/README.md). It is authored for this case and is not drawn from a recorded run.
- **Planted contradiction:** `docs/design/2024-03-batching.md:7` decided on batches of 50; `src/consumer.py:3` ships `BATCH_SIZE = 100`. The current page `docs/batching.md` points at the constant without restating it, so the conflict exists only between the design record and the code.
- **Two runs:** the default run must report nothing, because `docs/design/` is excluded. The `--no-default-excludes` run must report the conflict with both lines cited.
- **Rubric:** [`graders/criteria.md`](./graders/criteria.md).
