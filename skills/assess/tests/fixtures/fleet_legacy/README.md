# fleet_legacy

A fleet run written before the 2.0.0 maturity-model rename (issue #670), kept as a
history entry (`acme/2026-11-20/`). Its `run.json` and `groups.jsonl` carry the old
`rubric_version` key, and each group line holds its rows under `rubric`.

`fleet.py` from commit `b1a2751` wrote it, run over two repositories (`acme/svc1` with a
one-line `CLAUDE.md`, `acme/svc2`), using `--scans nested_instructions`, the
`fleet_heatmap` fixture's model file (version `fixture-1`) and `--no-diagrams`.

It is a record of the old format and cannot be regenerated, because the current code
writes the new names. Never edit it.
`tests/test_fleet_legacy.py` reads it through `lib/fleet_legacy.py`, covering the
`--compare` loader, the same-day and baseline history matching, and the trend history.
