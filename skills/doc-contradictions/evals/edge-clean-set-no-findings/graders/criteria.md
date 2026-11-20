# Criteria: clean set, false-positive guard

Every document agrees with the others and with `src/limits.py`, in different words. Pass requires every item below.

1. The report states `No contradictions found.` and its header counts 0 contradictions.
2. Every comparison subagent returned `[]`. The orchestrator never drops a comparer finding on its own judgement, so a raw finding at any confidence fails this item even when the citation check later removes it. Record each comparer's raw output in the results.
3. The equivalences the fixture plants are all treated as agreement:
   - "10 MB" (`README.md`), "ten megabytes (10 * 1024 * 1024 bytes)" (`docs/limits.md`) and `MAX_UPLOAD_BYTES = 10 * 1024 * 1024`;
   - "half a minute" (`README.md`), "30-second timeout" (`docs/limits.md`) and `REQUEST_TIMEOUT_SECONDS = 30`;
   - "eight worker processes" (`docs/operations.md`) and `WORKER_PROCESSES = 8`.
