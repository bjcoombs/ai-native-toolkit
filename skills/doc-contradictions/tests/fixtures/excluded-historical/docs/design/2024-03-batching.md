# Design: batching the ledger writes

Status: accepted, March 2024. Implementation in `src/consumer.py`.

## Decision

The consumer will group messages into batches of 50.
A partial batch will be flushed after 2 seconds.

## Alternatives considered

Writing each message individually was rejected because of the ledger's per-write latency.
