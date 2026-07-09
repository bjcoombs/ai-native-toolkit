# Expected result: known-good CLI canary

**Verdict the harness must reach: `PASS`.** This is the positive control
(canary criterion 2): a genuinely-working CLI must certify `PASS` end-to-end in
the same harness invocation that rejects jet-fighters and the vacuous contract.
Together with the known-good-interactive fixture it disarms a "refuse
everything" gate - a gate that fails jet-fighters only because it fails *every*
build is caught here.

## Freeze stage

The CLI-class null artifact is a no-op entrypoint (a script that reads nothing
and prints nothing, exit 0). Executed against it:

- KG1: exit code would be 0 - but KG2 and KG3 both FAIL (no expected output, no
  usage error), so the contract is not vacuous. The kill-test passes; the
  contract may freeze. (KG1 alone passing against a no-op is why a contract needs
  output/behaviour criteria, not just an exit-code criterion - the vacuous
  fixture shows the failure mode where the *only* criterion is absence-satisfiable.)

## Cold-exit stage (against `reference_implementation/`)

A cold verifier runs the reference implementation on the committed real input:

| id | tier | expected | observed |
|----|------|----------|----------|
| KG1 | 1 | PASS | `python3 porcelain.py < input.txt` exits 0. |
| KG2 | 1 | PASS | stdout is byte-identical to `expected_stdout.txt` (`alpha`, `mid`, `zebra`). |
| KG3 | 1 | PASS | `--bogus` exits 2 with a usage message on stderr; stdout empty. |

All tier-1 criteria drive and pass; `couldnt_drive[]` is empty. The completion
record certifies **`PASS`** (per canary criterion 2 / verifier criterion 6:
every criterion drives and passes -> `PASS` with empty `couldnt_drive[]`).

## What a verifier should observe (per class)

CLI class: fully machine-verifiable. The verifier executes the tool on real
input and compares exit code + bytes - no human step, no perceptual residue.
This is the class where the machine tiers carry the full load, so `PASS` here is
a real, sufficient certification (unlike the interactive class, where contract
green is necessary but never sufficient).
