# Caching

Back to the [README](../README.md).

The cache lives in `src/cache.py` and holds one entry per product ID.

## Expiry

Entries expire after 600 seconds by default.
Pass `ttl_seconds` to `Cache()` to change it per deployment.

## Size

The cache holds at most 10,000 entries and evicts the least recently used entry when full.
