# Criteria: planted doc-vs-code contradiction

The fixture's doc states a cache expiry default that the code does not use. Pass requires every item below.

1. The report lists a contradiction about the cache expiry or TTL default, of kind `doc-vs-code`.
2. The doc side cites `docs/caching.md:9` (quote contains "600 seconds").
3. The code side cites `src/cache.py:4` (quote contains "DEFAULT_TTL_SECONDS = 300"). Citing line 9 (the `__init__` default that names the constant) as well is acceptable; citing no line of `src/cache.py` fails.
4. The finding carries a confidence of `high` or `medium`.
5. The finding appears in the raw output of a comparison subagent that ran on Haiku.
6. The size claim (`docs/caching.md:13`, 10,000 entries, LRU) is not reported: it matches `MAX_ENTRIES = 10_000` and the eviction code.
