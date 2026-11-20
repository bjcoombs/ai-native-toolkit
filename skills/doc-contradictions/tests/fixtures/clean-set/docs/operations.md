# Operations

Back to the [README](../README.md).

The service runs eight worker processes per instance, set in `src/limits.py`.
Scale out by adding instances, not by raising the worker count.
A request that exceeds the timeout in [Limits](limits.md) is logged at WARN.
