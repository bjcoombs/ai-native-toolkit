---
type: llm
weight: 1
---

# Criteria: clean set, false-positive guard

Every document agrees with the others and with `src/limits.py`, in different words and units.

## Response checks

Grade the final response (which contains the report). Pass requires every item.

1. The report states `No contradictions found.` and its header counts 0 contradictions.
2. The response raises no contradiction, discrepancy or suggested correction anywhere, in the report or around it. The planted equivalences are all agreement:
   - "10 MiB" (`README.md`), "ten mebibytes (10 * 1024 * 1024 bytes)" (`docs/limits.md`) and `MAX_UPLOAD_BYTES = 10 * 1024 * 1024`;
   - "half a minute" (`README.md`), "30-second timeout" (`docs/limits.md`) and `REQUEST_TIMEOUT_SECONDS = 30`;
   - "eight worker processes" (`docs/operations.md`) and `WORKER_PROCESSES = 8`.

## Trace checks

Checked by the operator from the run trace (`claude plugin eval --keep-temp` keeps it as `out/trace.jsonl`). The response judge does not see the trace and does not grade these items.

3. Every comparison subagent ran on a Haiku model ID.
4. Every comparer returned `[]`. The orchestrator never drops a comparer finding on its own judgement, so a raw finding at any confidence fails this item even if the citation check later removed it.
