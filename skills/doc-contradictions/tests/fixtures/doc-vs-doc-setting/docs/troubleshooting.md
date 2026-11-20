# Troubleshooting

Back to the [README](../README.md).

## A report job fails overnight

Check the job log for the failing query first.
If the log shows a cancelled query, the query ran past the timeout.
The `query_timeout_seconds` setting is 120, so only very large queries hit it.
Split such a report into smaller jobs rather than raising the limit.

## A report is empty

The warehouse load may not have finished; rerun the job after 06:00.
