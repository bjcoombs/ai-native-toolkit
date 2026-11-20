# Criteria: planted doc-vs-doc contradiction

The fixture pins `query_timeout_seconds` to two values on two pages. Pass requires every item below.

1. The report lists a contradiction about `query_timeout_seconds`, of kind `doc-vs-doc`.
2. One side cites `docs/configuration.md:13` (quote contains "Set `query_timeout_seconds` to 30").
3. The other side cites `docs/troubleshooting.md:9` (quote contains "setting is 120").
4. The finding carries a confidence of `high` or `medium`.
5. The finding appears in the raw output of a comparison subagent that ran on Haiku, not only in the orchestrator's report.
6. No other contradiction is reported at `high` confidence. The fixture holds no second planted conflict.

Grading is mechanical: grep the report for the two `path:line` strings, and check the subagent model ID in the run transcript.
