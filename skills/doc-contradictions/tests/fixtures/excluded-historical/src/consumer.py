"""Batching consumer for the orders queue."""

BATCH_SIZE = 100
FLUSH_AFTER_SECONDS = 2.0


def batches(messages: list[str]) -> list[list[str]]:
    return [messages[i : i + BATCH_SIZE] for i in range(0, len(messages), BATCH_SIZE)]
