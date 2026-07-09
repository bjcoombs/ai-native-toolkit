# Acceptance contract: log-scrubber (VACUOUS - must be rejected at freeze)

This contract is deliberately vacuous. Its single criterion is satisfiable by
absence: an empty (or missing) `output.log`, or a no-op tool that writes
nothing, passes it. Executed against the CLI-class null artifact (a no-op
entrypoint that produces no output), VC1 PASSES - and any criterion that passes
against the null artifact is vacuous (the jet-fighters pathology). The
red-contract-first kill-test (B1) must reject this contract back to authoring;
`freeze.py` must refuse to freeze it (canary criterion 3).

The criterion is written to the same machine-parseable format as the sound
fixtures on purpose - the defect is semantic (absence-satisfiable), not
structural. A freeze gate that only checks structure would wrongly accept it; the
kill-test against the null artifact is what catches it.

```yaml
class: cli
criteria:
  - id: VC1
    tier: 1
    action: "Run the tool, then scan output.log for lines at error level."
    observation: "No error-level lines appear in output.log."
```
