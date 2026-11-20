# Limits

Back to the [README](../README.md). The limits are constants in `src/limits.py`.

## Size

The upload limit is ten megabytes (10 * 1024 * 1024 bytes).
A bigger file gets HTTP 413.

## Time

Each upload request has a 30-second timeout.
