---
type: llm
weight: 1
---

# Criteria: planted doc-vs-code contradiction

The fixture's doc states a cache expiry default that the code does not use.

## Response checks

Grade the final response (which contains the report). Pass requires every item.

1. The report lists a contradiction about the cache expiry or TTL default, of kind `doc-vs-code`.
2. The doc side cites `docs/caching.md:9` (quote contains "600 seconds").
3. The code side cites `src/cache.py:4` (quote contains "DEFAULT_TTL_SECONDS = 300"). Citing line 9 (the `__init__` default that names the constant) instead is acceptable; citing no line of `src/cache.py` fails.
4. The finding carries a confidence of `high` or `medium`.
5. The size claim (`docs/caching.md:13`, 10,000 entries, LRU) is not reported: it matches `MAX_ENTRIES = 10_000` and the eviction code.

## Trace checks

Checked by the operator from the run trace (`claude plugin eval --keep-temp` keeps it as `out/trace.jsonl`). The response judge does not see the trace and does not grade these items.

6. Every comparison subagent ran on a Haiku model ID.
7. The finding is in a comparer's raw JSON with both citations, so the orchestrator did not add it.
