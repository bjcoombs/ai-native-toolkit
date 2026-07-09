# Acceptance contract: porcelain (CLI class)

Frozen contract for the known-good CLI fixture. The cold exit verifier runs the
reference implementation on the committed real input and checks exit code and
output. All criteria are tier-1 (binary do-and-observe on real input); there is
no perceptual residue, so no tier-2 or tier-3 criteria. This fixture is expected
to certify `PASS` end-to-end - it is the positive control that disarms a
refuse-everything gate (canary criterion 2).

Drive: from `reference_implementation/`, run
`python3 porcelain.py < ../input.txt` and observe exit code, stdout, and stderr.
Paths in the actions are relative to `tests/canaries/known-good/`.

```yaml
class: cli
criteria:
  - id: KG1
    tier: 1
    action: "Run: python3 reference_implementation/porcelain.py < input.txt ; capture exit code."
    observation: "Exit code is 0."
  - id: KG2
    tier: 1
    action: "Run the same command and capture stdout; compare against expected_stdout.txt."
    observation: "stdout is byte-identical to expected_stdout.txt: the lines 'alpha', 'mid', 'zebra' in that order, one per line - archived repos and duplicates removed, sorted ascending, no chatter."
  - id: KG3
    tier: 1
    action: "Run: python3 reference_implementation/porcelain.py --bogus < input.txt ; capture exit code and stderr."
    observation: "Exit code is non-zero (2) and stderr contains a usage message; stdout is empty."
```
