---
type: llm
weight: 1
---

# Criteria: excluded historical record

The only contradiction in the fixture is between the design record `docs/design/2024-03-batching.md` (batches of 50) and the code `src/consumer.py` (`BATCH_SIZE = 100`). The current page agrees with the code.

## Response checks

Grade the final response, which shows two reports. Pass requires every item.

Default run (the first report, saved as `report-default.md`):

1. The report states `No contradictions found.`
2. The header names `docs/design/` as excluded with 1 doc.
3. No finding cites `docs/design/2024-03-batching.md`.

Override run (`--no-default-excludes`, the second report):

4. The report lists a contradiction about the batch size.
5. One side cites `docs/design/2024-03-batching.md:7` (quote contains "batches of 50").
6. The other side cites `src/consumer.py:3` (quote contains "BATCH_SIZE = 100"). A finding that instead cites `docs/batching.md:5` (which names `BATCH_SIZE` without a value) on the other side fails this item.

## Trace checks

Checked by the operator from the run trace (`claude plugin eval --keep-temp` keeps it as `out/trace.jsonl`). The response judge does not see the trace and does not grade these items.

7. Every comparison subagent ran on a Haiku model ID.
8. No default-run comparer brief listed `docs/design/2024-03-batching.md`.
9. The override finding is in a comparer's raw JSON with both citations, so the orchestrator did not add it.
