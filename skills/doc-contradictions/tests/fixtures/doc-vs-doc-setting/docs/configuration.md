# Configuration

Settings are read from `report.toml` at start-up. Back to the [README](../README.md).

## Output

The service writes finished reports to the `output_dir` directory.
Each report file is named after its job and the run date.

## Warehouse connection

The connection uses the read-only `reporting` role.
Set `query_timeout_seconds` to 30; longer queries are cancelled.
A cancelled query fails the whole report job.
