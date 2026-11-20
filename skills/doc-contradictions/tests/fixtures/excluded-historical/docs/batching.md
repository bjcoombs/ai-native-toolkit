# Batching

Back to the [README](../README.md). The batch logic is in `src/consumer.py`.

The batch size is the `BATCH_SIZE` constant in `src/consumer.py`.
A partial batch is flushed after 2 seconds so a quiet queue still drains.
