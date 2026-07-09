# Canary fixtures for the acceptance-contract gates

These fixtures are the ground-truth inputs the acceptance-contract suite runs
against. The canary harness (`scripts/canaries/run_canaries.py`) exercises the
**real** gate implementations - the readiness check (PRD A2), the freeze gate
(B1/B3), the cold-exit verifier via the chokepoint (C4), and the complete gate
(C1) - against the committed builds here. The harness does not spawn a marathon
fleet; the fixtures include the built artifacts so the exit verifier drives a
real product (PRD E3).

This directory also defines the **contract-file format** that the freeze gate
(`scripts/contract/freeze.py`), the verifier (`scripts/contract/spawn_verifier.py`
+ the verifier prompt), and the harness all parse. Downstream should treat the
`contract.md` files here as the format's ground truth.

## Contract-file format

A `contract.md` is a human-readable markdown document whose machine-parseable
criteria live in **exactly one fenced `yaml` block**. Everything outside that
block is prose for humans; parsers read only the yaml block.

The yaml block has two top-level keys:

- `class` - one of `cli`, `interactive`, `report`, `refactor`. Sets the tier
  defaults (PRD A4) and the class-specific null artifact used by the freeze-time
  kill-test (PRD B1).
- `criteria` - a list of criterion objects, each with:
  - `id` - short stable identifier, unique within the contract (e.g. `JF2`).
  - `tier` - `1`, `2`, or `3` (PRD A4 observation ceiling):
    - tier-1 = binary do-and-observe a cold agent can fully verify (hard gate).
    - tier-2 = comparative against a frozen reference / judged-with-calibration
      (reports only in v1; blocking is unarmed - PRD C2).
    - tier-3 = perceptual residue a cold agent structurally cannot observe;
      escalated to the operator, never silently dropped.
  - `action` - what a cold agent **does** to exercise the criterion (the drive
    step). Concrete and executable, not a quality opinion.
  - `observation` - what the agent **observes** to decide pass/fail. Binary and
    absence-resistant: a broken or empty artifact must fail it.

Minimal example:

```yaml
class: cli
criteria:
  - id: KG1
    tier: 1
    action: "Run: python3 reference_implementation/porcelain.py < input.txt ; capture exit code."
    observation: "Exit code is 0."
```

### Structural rules downstream can rely on

- Exactly one `yaml` fenced block per `contract.md`; it is the parse target.
- `class` and `criteria` are both required; `criteria` is non-empty.
- Every criterion has all of `id`, `tier`, `action`, `observation`.
- `tier` is an integer in `{1, 2, 3}`; `class` is one of the four names above.
- Per PRD B3, an `interactive` contract must contain at least one tier-3
  criterion or `freeze.py` refuses.
- Paths in `action`/`observation` are relative to the fixture directory (the
  directory containing that `contract.md`) unless stated otherwise.

### Drive surface for interactive builds

The interactive builds (`jet-fighters/build/`, `known-good-interactive/build/`)
expose a headless drive surface so a cold agent can exercise tier-1 criteria
without a human:

- `window.__canary.getState()` returns a JSON-serializable state snapshot.
- Input is sent as standard DOM `keydown` events dispatched on `window`.

A correct build subscribes to input and advances a loop; the broken jet-fighters
build does neither (that is its planted defect).

## The four fixtures

| Fixture | Class | Role | Expected verdict |
|---|---|---|---|
| `jet-fighters/` | interactive | The real regression: 100% merged, unplayable. Renders but input never wired. | Never certifies - tier-1 launch/drive criteria FAIL at cold exit (detected, not just PARTIAL). Canary criterion 1. |
| `known-good/` | cli | Genuinely-working CLI (porcelain repo-list normalizer). | Certifies `PASS` end-to-end. Positive control that disarms refuse-everything gates. Canary criterion 2. |
| `known-good-interactive/` | interactive | Minimal working interactive toy (Lane Runner). | Tier-1 PASS, stalls at tier-3 escalation. Within-class discrimination vs jet-fighters. Canary criterion 2b. |
| `vacuous-contract/` | cli | Contract with a criterion satisfiable by absence. | Rejected at freeze by B1 (criterion passes against the null artifact). Canary criterion 3. |

Each fixture directory carries an `expected_result.md` stating, per class, what a
verifier should observe at each stage (freeze against the null artifact, then
cold exit against the build where one exists).

## Fixture contents

- `jet-fighters/`: `prd.md` (the verbatim jet-fighters v1 PRD), `contract.md`,
  `build/index.html` (broken build), `null_artifact/index.html` (launches, no
  behaviour), `expected_result.md`.
- `known-good/`: `spec.md`, `reference_implementation/porcelain.py`, `input.txt`
  + `expected_stdout.txt` (real input and its expected output), `contract.md`,
  `expected_result.md`.
- `known-good-interactive/`: `spec.md`, `build/index.html` (working toy),
  `contract.md`, `expected_result.md`.
- `vacuous-contract/`: `spec.md`, `contract.md` (vacuous), `expected_result.md`.
  No build - the assertion is entirely at the freeze gate.

## Fixture-identity spoofing is out of bounds

These are fixed, named fixtures, so a gate that recognizes fixture *identity*
rather than judging builds would pass every committed canary here. That is why
the harness also runs per-run **blind** pairs with no fixture identity available
(PRD E4, criteria 12 and 14): a behaviour-preserving transform on anonymized
copies of the known-good build with a fresh planted defect in one, and a
freshly-generated vacuous/sound contract pair. An implementation that hardcodes
recognition of these committed fixtures passes the named canaries but fails the
blind checks by construction. Treat these fixtures as ground truth for the
*format* and the *expected verdicts*, never as an allow-list to match by name.
