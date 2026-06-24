# Answer key for flawed-instruction-file

The companion to the per-lens skill fixture (`../flawed-sample-skill/`), for the
instruction-file path. The target is an always-loaded `CLAUDE.md`, so the
**Trigger/routing lens is dropped** by the artifact-type selector and only four
lenses are active (Fidelity, Adversarial, Compression, Usability). This fixture
plants one defect per active lens, with the **Fidelity accuracy sub-check** as
the headline: a correctly calibrated instruction-file panel catches all four and
fires on none of the clean lines.

The runner forging this fixture runs the **instruction-file variant** (read-only):
it is handed only `CLAUDE.md` plus a realistic repo task ("add a new component
and prepare it for commit") and states the actions it would take. The accuracy
defect surfaces because the runner, acting on the checklist, would update only
the surfaces the checklist names - leaving the enforced-but-unlisted surface
stale, which `check_counts.py` (the synthetic CI gate) then fails.

## Per-lens planted defects (detection)

| Lens | Planted defect | Location | Expected severity |
|------|----------------|----------|-------------------|
| Fidelity (accuracy sub-check) | **Count-surface trap.** The "Adding a component" checklist names a strict subset of the surfaces the repo validator enforces: it lists `README.md` and `catalog.json` but omits `docs/index.md`, which `check_counts.py` enforces. An agent trusting the checklist leaves `docs/index.md` stale and CI fails - a confident, wrong map of the repo. | "Adding a component" checklist | HIGH |
| Adversarial | Discretionary rationalization escape: "Run the full test suite ... unless the change is small and obviously safe, in which case you can skip it" lets the agent talk itself out of running the tests on exactly the changes that look safe but are not. | "Before committing", bullet 1 | MEDIUM |
| Compression | Bloated denormalized training knowledge: a paragraph explaining what JSON is (with provenance) and what a git branch is - concepts the model already holds, adding length with no instruction. | "What this repo is", paragraph 2 | MEDIUM |
| Usability | Assumes unestablished context: "Bump the version in the manifest" never establishes *which* manifest, where it lives, or the bump rule, so a fresh agent has no input to act on and gets stuck. | "Before committing", bullet 2 | MEDIUM |

## Clean-pass case (judgement)

| Case | What it is | Location | Expected outcome |
|------|------------|----------|------------------|
| Clean-pass | No defect: "Always wrap commit message bodies at 72 columns" is unambiguous, correct, and complete. | "Before committing", bullet 3 | **No finding.** A lens that flags this is over-firing. |

## The count-surface ground truth

`check_counts.py` declares `ENFORCED_COUNT_SURFACES = (README.md, catalog.json,
docs/index.md)` - the surfaces the CI count gate enforces. The CLAUDE.md
checklist names only the first two. The accuracy defect is therefore machine-
checkable: the checklist's count surfaces are a **proper subset** of the enforced
surfaces, and updating only the checklist surfaces leaves `docs/index.md` stale,
which the validator reports as a build break. This is what makes the re-forge of
the trap reproducible without hand-holding - the divergence is deterministic, not
a matter of judgement.
