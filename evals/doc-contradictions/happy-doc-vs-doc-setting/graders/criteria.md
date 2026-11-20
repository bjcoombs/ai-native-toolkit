---
type: llm
weight: 1
---

# Criteria: planted doc-vs-doc contradiction

The fixture pins `query_timeout_seconds` to two values on two pages.

## Response checks

Grade the final response (which contains the report). Pass requires every item.

1. The report lists a contradiction about `query_timeout_seconds`, of kind `doc-vs-doc`.
2. One side cites `docs/configuration.md:13` (quote contains "Set `query_timeout_seconds` to 30"). Any other line number for this file fails.
3. The other side cites `docs/troubleshooting.md:9` (quote contains "setting is 120").
4. The finding carries a confidence of `high` or `medium`.
5. No other contradiction is reported at `high` confidence. The fixture holds no second planted conflict.

## Trace checks

Checked by the operator from the run trace (`claude plugin eval --keep-temp` keeps it as `out/trace.jsonl`). The response judge does not see the trace and does not grade these items.

6. Every comparison subagent ran on a Haiku model ID.
7. The finding is in a comparer's raw JSON with both citations, so the orchestrator did not add it.
