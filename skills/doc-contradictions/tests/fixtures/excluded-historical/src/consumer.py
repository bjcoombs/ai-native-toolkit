"""Batching consumer for the orders queue."""

BATCH_SIZE = 100
FLUSH_AFTER_SECONDS = 2.0


def should_flush(batch: list[str], oldest_age_seconds: float) -> bool:
    """A full batch is written at once; a partial one after FLUSH_AFTER_SECONDS."""
    if len(batch) >= BATCH_SIZE:
        return True
    return bool(batch) and oldest_age_seconds >= FLUSH_AFTER_SECONDS
