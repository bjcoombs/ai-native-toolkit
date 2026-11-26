# Expected result: known-good CLI canary

**Verdict the harness must reach: `PASS`.** This is the positive control
(canary criterion 2): a genuinely-working CLI must certify `PASS` end-to-end in
the same harness invocation that rejects jet-fighters and the vacuous contract.
Together with the known-good-interactive fixture it disarms a "refuse
everything" gate - a gate that fails jet-fighters only because it fails *every*
build is caught here.

## Freeze stage

The CLI-class null artifact is the empty repo of PRD B1: no working
entrypoint. The harness models it as an absent one, driving the contract
against `__does_not_exist__.py`, a script that does not exist
(`porcelain_kill_test_results()` in `scripts/canaries/run_canaries.py`).
Executed against it:

- KG1: FAIL. `python3` exits non-zero when the script is missing.
- KG2: FAIL. Nothing reaches stdout, so it is not `expected_stdout.txt`.
- KG3: FAIL. The non-zero exit comes with Python's missing-file error on
  stderr, not a usage message.

Every criterion fails against the null artifact, so B1 (red-contract-first,
applied per criterion in `scripts/contract/freeze.py`) passes and the contract
may freeze. An exit-code criterion carries no weight alone: against a no-op
entrypoint that exits 0, KG1 would pass, and one criterion that passes against
the null artifact is enough for B1 to refuse the contract. That is the vacuous
failure mode the vacuous-contract fixture (`tests/canaries/vacuous-contract/`)
shows.

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
