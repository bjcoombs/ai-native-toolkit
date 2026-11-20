# Criteria: excluded historical record

The only contradiction in the fixture is between the design record `docs/design/2024-03-batching.md` (batches of 50) and the code `src/consumer.py` (`BATCH_SIZE = 100`). The current page agrees with the code. Pass requires every item below.

Default run (`report-default.md`):

1. The report states `No contradictions found.`
2. The header names `docs/design/` as excluded with 1 doc.
3. No comparison subagent was given `docs/design/2024-03-batching.md`, and no finding cites it.

Override run (`--no-default-excludes`, `report.md`):

4. The report lists a contradiction about the batch size.
5. One side cites `docs/design/2024-03-batching.md:7` (quote contains "batches of 50").
6. The other side cites `src/consumer.py:3` (quote contains "BATCH_SIZE = 100"). A finding that instead cites `docs/batching.md:5` (which names `BATCH_SIZE` without a value) on the other side fails this item.
7. The finding appears in the raw output of a comparison subagent that ran on Haiku.
