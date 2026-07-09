# Expected result: vacuous-contract canary

**Verdict the harness must reach: REJECTED AT FREEZE.** This fixture never gets
a build and never reaches cold-exit verification - the assertion is entirely at
the freeze gate (canary criterion 3). Its criterion passes against the class null
artifact, so B1 (red-contract-first) kicks it back and `freeze.py` refuses.

## Freeze stage (against the CLI null artifact)

The CLI-class null artifact is a no-op entrypoint that produces no output. B1
executes `contract.md` against it:

- VC1 ("No error-level lines appear in output.log") -> **PASS against the null
  artifact**. An empty or missing `output.log` has no error lines, so the
  criterion is trivially satisfied by absence.

A criterion that passes against the null artifact is vacuous. B1 rejects the
contract back to authoring; `freeze.py` refuses to freeze it and names the
offending criterion (VC1). No completion record is produced; the run cannot
proceed to decomposition.

## Contrast with the sound fixtures

Every criterion in the jet-fighters, known-good, and known-good-interactive
contracts **FAILS** against its null artifact (nothing to launch, no expected
output, no input effect), so those contracts are non-vacuous and freeze is
permitted. This fixture is the negative control: the one contract that must NOT
freeze. A freeze gate that hardcodes rejection of this specific committed fixture
(by identity) rather than executing the kill-test would pass the committed case
but fail the per-run blind vacuous contracts the harness generates (PRD E4 /
criterion 14) - so identity-matching is not a valid implementation.

## What a verifier should observe

The freeze gate runs the contract's criteria against the null artifact and
records, per criterion, whether it failed (good) or passed (vacuous). Any
criterion passing against the null artifact -> reject the whole contract, name
the criterion, refuse to freeze.
